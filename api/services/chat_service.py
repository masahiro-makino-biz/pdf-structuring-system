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
- database: "pdf_system" ← findやaggregateの呼び出し時に必ず指定すること
- collection: "pages_default" ← 必ずこのビューを使うこと（pagesは使わない）

【検索のルール】
- 文字列フィールドの検索には必ず $regex を使うこと（完全一致は絶対に使わない）
- $options: "i" で大文字小文字を区別しない
- 最初の検索では $or で複数フィールドを横断検索すること（どのフィールドにあるかわからないため）
- 検索結果が0件の場合、キーワードを短くしたり別の表現で再検索すること
  例: 「ローラタイヤ」→ 0件 → 「タイヤ」で再検索
  例: 「ミル機器」→ 0件 → 「ミル」で再検索、それでも0件なら「粉砕」で再検索

【findの呼び出し例（$orで横断検索）】
find(database="pdf_system", collection="pages_default", filter={"$or": [{"data.機器": {"$regex": "タイヤ", "$options": "i"}}, {"data.機器部品": {"$regex": "タイヤ", "$options": "i"}}, {"data.点検項目": {"$regex": "タイヤ", "$options": "i"}}, {"data.計測箇所": {"$regex": "タイヤ", "$options": "i"}}]})

【グラフ用findの呼び出し例（projectionで必要フィールドだけ取得）】
find(database="pdf_system", collection="pages_default", filter={"$or": [{"data.機器": {"$regex": "ポンプ", "$options": "i"}}, {"data.機器部品": {"$regex": "ポンプ", "$options": "i"}}, {"data.点検項目": {"$regex": "ポンプ", "$options": "i"}}, {"data.計測箇所": {"$regex": "ポンプ", "$options": "i"}}]}, projection={"_id": 0, "data": 1, "image_path": 1})

【スキーマ】
各ドキュメントのdataフィールドに以下が含まれます:
- data.機器: 機器名（例: "高圧ポンプユニットA-01"）
- data.機器部品: 機器部品名（例: "インペラシャフト"）
- data.点検項目: 点検項目（例: "摩耗量"）
- data.点検年月日: 点検日（例: "2024-11-14"）
- data.計測箇所: 計測箇所（例: "インペラ外周部"）
- data.測定値: 測定値のオブジェクト（例: {"摩耗量・A": 0.18}）
- data.基準値: 基準値のオブジェクト（例: {"摩耗量": 0.5}）
- page_number: ページ番号
- filename: ファイル名
- image_path: PDFページ画像のパス

【使い分け】
- 通常の検索 → find を使う（database引数を必ず指定）
- 集計・統計（平均値、最大値、年度別集計等）→ aggregate を使う（database引数を必ず指定）
- データの構造がわからない場合 → まず collection-schema で確認する

【重要】検索結果を回答する際は、参照元のPDFページ画像パス（image_path）をそのまま含めてください。
UIが自動的に画像として表示するので、「参照:」などのラベルは不要です。パスだけを含めてください。

## グラフ生成（visualize_data）
「グラフで見せて」「可視化して」などと言われたら、以下の2ステップで実行してください。

【手順】
1. まずfindで該当データを検索する（projection={"_id": 0, "data": 1, "image_path": 1}で必要フィールドだけ取得）
2. 検索結果のJSON文字列をvisualize_dataのdataパラメータに渡す
3. ユーザーの指示に応じてオプションパラメータを指定する

【オプションパラメータ（ユーザーが明示的に指定した場合のみ使用。指定がなければ絶対に省略すること）】
- chart_type: グラフ種類。"strip"(デフォルト), "scatter", "bar", "line"
  ※ユーザーが「棒グラフで」「折れ線で」等と指定した場合のみ渡す。指定がなければこのパラメータ自体を省略すること
- color: 全データ点を統一色にする（例: "red", "blue", "#FF6600"）
- year_from: 指定年度以降のデータだけ表示（例: 2024）
- year_to: 指定年度以前のデータだけ表示（例: 2025）
- min_value: 指定値以上のデータだけ表示（例: 0.5）
- max_value: 指定値以下のデータだけ表示（例: 1.0）
- show_reference: 基準値線の表示/非表示（デフォルト: true）
- x_axis: X軸のカラム。"year"(年度) or "key"(点検項目)。未指定なら自動判定（複数年→年度、単年→点検項目）

【オプション使用例】
- 「棒グラフで見せて」→ chart_type="bar"
- 「2024年度以降だけ」→ year_from=2024
- 「0.5以上の値だけ」→ min_value=0.5
- 「X軸を年度にして」→ x_axis="year"
- 「点検項目ごとに並べて」→ x_axis="key"
- 「赤色でプロット」→ color="red"
- 「折れ線グラフで、基準値なしで」→ chart_type="line", show_reference=false
- 「グラフで見せて」（種類指定なし）→ chart_type省略（dataのみ渡す）

【重要】
- findの結果をそのままJSON文字列としてdataに渡すこと
- 必ずprojectionで不要フィールドを除外すること（データが大きすぎると処理に失敗する）
- visualize_dataは1回だけ呼び出してください
- 戻り値のcharts内の各chart_pathと、reference_imagesのパスを回答に含めてください
- パスだけを記載してください。「グラフ:」「参照画像:」「参照:」などのラベルは絶対に付けないでください
- 回答文の後にパスだけを改行で並べてください

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

        # 2つのMCPサーバーに接続してエージェントを実行
        # ① MongoDB MCP: find/aggregateでデータ検索
        # ② 自作MCP: visualize_dataでグラフ生成
        async with MCPServerStreamableHttp(
            name="MongoDB Analytics",
            params={"url": f"{MONGODB_MCP_URL}/mcp", "timeout": 30},
            cache_tools_list=True,
        ) as mongo_mcp, MCPServerStreamableHttp(
            name="Visualization Tools",
            params={"url": f"{MCP_URL}/mcp", "timeout": 60},
            cache_tools_list=True,
        ) as viz_mcp:
            agent = Agent(
                name="DocumentAssistant",
                instructions=SYSTEM_PROMPT,
                mcp_servers=[mongo_mcp, viz_mcp],
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
