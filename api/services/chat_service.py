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
# 3. MCPStreamableHTTPTool + ChatAgent でエージェントを実行
# 4. 結果を履歴に追加して返す
#
# 【LiteLLM対応について】
# - 以前: OpenAI/Azure と Bedrock で処理を分岐（200行以上）
# - 現在: LiteLLM経由で統一（約100行）
# - プロバイダー切り替えは litellm/config.yaml で行う
#
# =============================================================================

from agent_framework import ChatAgent, MCPStreamableHTTPTool
from .agent_config import get_chat_client

from core.config import get_settings
from core.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()
MCP_URL = settings.mcp_url

# =============================================================================
# システムプロンプト
# =============================================================================
SYSTEM_PROMPT = """あなたはミル機器の点検記録PDFに関する質問に答えるアシスタントです。

## search_documents - 検索
ユーザーが点検記録について質問した場合は、search_documents関数を使って検索してください。
検索には以下の4つの条件が使えます（1つ以上指定が必要）:
- equipment: 機器名（例: "2号機微粉炭機D"）
- equipment_part: 機器部品名（例: "リンクサポート"）
- inspection_item: 点検項目（例: "隙間計測"）
- inspection_date: 点検年月日（例: "2024-01-15", "2024"）

ユーザーの質問から適切な条件を抽出して検索してください。
検索結果には測定値と基準値が含まれているので、それを参照して回答してください。

## visualize_data - グラフ生成
「グラフで見せて」「推移を見せて」などと言われたら、visualize_dataを使ってグラフを生成してください。

手順:
1. まず search_documents で検索
2. その結果をそのまま visualize_data の data パラメータに渡す

パラメータ:
- data: search_documentsの結果（JSON文字列）
- measurement_key: 表示する測定値のキー（例: "摩耗量"）
- chart_type: "line"(折れ線) or "bar"(棒)
- title: グラフタイトル（省略可）

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
    2. MCPStreamableHTTPTool で MCPサーバーに接続
    3. ChatAgent を作成してMCPツールを渡す
    4. agent.run() でメッセージを処理
    5. 結果を履歴に追加して返す

    【なぜこの実装か】
    - LiteLLM経由で全プロバイダー統一（OpenAI/Azure/Bedrock/Gemini）
    - Agent Framework の MCPStreamableHTTPTool で標準的なMCP接続
    - ChatAgent が自動的にツール呼び出しを処理

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

        # MCP経由でツールを取得してエージェントを実行
        async with MCPStreamableHTTPTool(
            name="PDF Search Tools",
            url=f"{MCP_URL}/mcp",
        ) as mcp_tools:
            async with ChatAgent(
                chat_client=get_chat_client(),
                name="DocumentAssistant",
                instructions=SYSTEM_PROMPT,
            ) as agent:
                result = await agent.run(full_message, tools=mcp_tools)

        add_to_history(session_id, "user", message)
        add_to_history(session_id, "assistant", result.text)

        logger.info(f"チャット処理完了: session_id={session_id}")

        return {
            "success": True,
            "response": result.text,
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
