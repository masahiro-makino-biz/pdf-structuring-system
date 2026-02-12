# =============================================================================
# api/services/chat_service.py - チャットサービス
# =============================================================================
#
# 【ファイル概要】
# ユーザーのチャットメッセージを処理し、AIエージェントを使って回答を生成する。
# セッション単位で会話履歴を保持し、文脈を踏まえた回答が可能。
#
# 【処理フロー】
# 1. main.py の /chat から process_chat() が呼ばれる
# 2. get_history() でセッションの履歴を取得
# 3. MCPServerStreamableHttp + Agent + Runner でエージェントを実行
# 4. 結果を履歴に追加して返す
#
# 【LiteLLM対応について】
# - LiteLLM経由で全プロバイダー統一（OpenAI/Azure/Bedrock/Gemini）
# - set_default_openai_client() でLiteLLMプロキシを設定するだけ
# - プロバイダー切り替えは litellm/config.yaml で行う
#
# 【なぜOpenAI Agents SDKか】
# - Microsoft Agent Frameworkの MCPStreamableHTTPTool にバグがあった
#   （tools/list_changed通知でデッドロックする問題）
# - OpenAI Agents SDKはこの問題が起きない設計
# - MCPServerStreamableHttp 1つでMCP接続が完結しシンプル
#
# =============================================================================

from openai import AsyncOpenAI
from agents import Agent, Runner, set_default_openai_client
from agents.mcp import MCPServerStreamableHttp

from core.config import get_settings
from core.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()
MCP_URL = settings.mcp_url
MONGODB_MCP_URL = settings.mongodb_mcp_url

# =============================================================================
# LiteLLMクライアント設定
# =============================================================================
# 【なぜここで設定するか】
# OpenAI Agents SDKは「デフォルトクライアント」をモジュール全体で共有する設計。
# set_default_openai_client()で1回設定すれば、全てのAgentが自動的にこのクライアントを使う。
#
# 【api_keyについて】
# LiteLLMプロキシでは任意の値でOK（プロキシ側で実際のAPIキー認証を行う）
#
# 【/v1 について】
# AsyncOpenAIは base_url に /v1 が必要。LiteLLMは http://litellm:4000/v1 で提供。
# /v1 を省略すると404エラーになるので注意。
_openai_client = AsyncOpenAI(
    base_url=f"{settings.litellm_url}/v1",
    api_key="sk-litellm",
)
set_default_openai_client(_openai_client)

# =============================================================================
# システムプロンプト
# =============================================================================
SYSTEM_PROMPT = """あなたはミル機器の点検記録PDFに関する質問に答えるアシスタントです。

## データベース検索
ユーザーが点検記録について質問した場合は、MongoDBのfindやaggregateツールを使って検索してください。

【使用するデータベースとコレクション】
- データベース: pdf_system
- コレクション: pages_default（必ずこのビューを使うこと。pagesコレクションは使わないこと）

【スキーマ】
各ドキュメントのdataフィールドに以下が含まれます:
- data.機器: 機器名（例: "2号機微粉炭機D"）
- data.機器部品: 機器部品名（例: "インペラシャフト"）
- data.点検項目: 点検項目（例: "隙間計測"）
- data.点検年月日: 点検日（例: "2024-01-15"）
- data.計測箇所: 計測箇所（例: "インペラ外周部"）
- data.測定値: 測定値の配列（各要素はキーと値のペア）
- data.基準値: 基準値の配列
- page_number: ページ番号
- filename: ファイル名
- image_path: PDFページ画像のパス

【使い分け】
- 通常の検索 → find を使う
- 集計・統計（平均値、最大値、年度別集計等）→ aggregate を使う
- データの構造がわからない場合 → まず collection-schema で確認する

【重要】検索結果を回答する際は、参照元のPDFページ画像パス（image_path）をそのまま含めてください。
UIが自動的に画像として表示するので、「参照:」などのラベルは不要です。パスだけを含めてください。

## 共通
会話の文脈を考慮して回答してください。例えば「去年のは？」と聞かれたら、
直前の会話で話題になっていた機器について検索してください。

検索結果がない場合や、一般的な質問の場合は、そのまま回答してください。"""

# =============================================================================
# チャット履歴管理
# =============================================================================
_chat_histories: dict[str, list[dict]] = {}
MAX_HISTORY_LENGTH = 20


def get_history(session_id: str) -> list[dict]:
    """セッションの履歴を取得"""
    return _chat_histories.get(session_id, [])


def add_to_history(session_id: str, role: str, content: str):
    """履歴にメッセージを追加"""
    if session_id not in _chat_histories:
        _chat_histories[session_id] = []
    _chat_histories[session_id].append({"role": role, "content": content})
    if len(_chat_histories[session_id]) > MAX_HISTORY_LENGTH:
        _chat_histories[session_id] = _chat_histories[session_id][-MAX_HISTORY_LENGTH:]


def clear_history(session_id: str):
    """履歴をクリア"""
    if session_id in _chat_histories:
        del _chat_histories[session_id]
        logger.info(f"履歴クリア: session_id={session_id}")


# =============================================================================
# チャット処理
# =============================================================================
async def process_chat(message: str, tenant: str = "default", session_id: str = "default") -> dict:
    """
    ユーザーメッセージを処理して回答を生成

    【処理フロー】
    1. 履歴を取得してコンテキストを構築
    2. MCPServerStreamableHttp で MongoDB MCPサーバーに接続
    3. Agent を作成してMCPサーバーを渡す
    4. Runner.run() でメッセージを処理
    5. 結果を履歴に追加して返す

    【なぜこの実装か】
    - LiteLLM経由で全プロバイダー統一（OpenAI/Azure/Bedrock/Gemini）
    - OpenAI Agents SDK の MCPServerStreamableHttp で安定したMCP接続
    - Agent + Runner が自動的にツール呼び出しを処理

    Args:
        message: ユーザーのメッセージ
        tenant: テナントID（マルチテナント対応用）
        session_id: セッションID（会話履歴の識別用）

    Returns:
        dict: {
            "success": bool,
            "response": str,  # AIの回答
            "search_performed": bool,  # 検索が実行されたか
            "search_results": dict | None  # 検索結果
        }
    """
    try:
        # 履歴を取得してコンテキストを構築
        history = get_history(session_id)
        if history:
            context_parts = ["【過去の会話】"]
            for msg in history:
                role_label = "ユーザー" if msg["role"] == "user" else "アシスタント"
                context_parts.append(f"{role_label}: {msg['content']}")
            context_parts.append("")
            context_parts.append("【今回の質問】")
            context_parts.append(message)
            full_message = "\n".join(context_parts)
        else:
            full_message = message

        # MongoDB公式MCPサーバーに接続してエージェントを実行
        # 【MCPServerStreamableHttpの利点】
        # - MCP接続管理を内部で行う（旧Agent Frameworkのデッドロック問題なし）
        # - cache_tools_list=True でツール一覧をキャッシュし、毎回取得しない
        async with MCPServerStreamableHttp(
            name="MongoDB Analytics",
            params={"url": f"{MONGODB_MCP_URL}/mcp", "timeout": 30},
            cache_tools_list=True,
        ) as mcp_server:
            agent = Agent(
                name="DocumentAssistant",
                instructions=SYSTEM_PROMPT,
                mcp_servers=[mcp_server],
                model=settings.litellm_model,
            )
            result = await Runner.run(agent, full_message)

        add_to_history(session_id, "user", message)
        add_to_history(session_id, "assistant", result.final_output)

        logger.info(f"チャット処理完了: session_id={session_id}")

        return {
            "success": True,
            "response": result.final_output,
            "search_performed": False,  # MCPツール経由なので直接検知は難しい
            "search_results": None
        }

    except Exception as e:
        logger.error(f"チャットエラー: {e}")
        return {
            "success": False,
            "response": f"エラーが発生しました: {str(e)}",
            "search_performed": False
        }
