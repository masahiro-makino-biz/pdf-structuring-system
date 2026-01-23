# =============================================================================
# api/services/chat_service.py - チャットサービス（Agent Framework版）
# =============================================================================
# Microsoft Agent Frameworkを使用してチャット機能を提供
# OpenAI/Azure OpenAIを切り替え可能
# =============================================================================

import json
from typing import Annotated
import httpx
from pydantic import Field
from agent_framework import ai_function
from .agent_config import get_chat_client

# =============================================================================
# 設定とロギング
# =============================================================================
from core.config import get_settings
from core.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()
MCP_URL = settings.mcp_url

# ---------------------------------------------------------------------------
# テナント情報と検索結果の保持
# ---------------------------------------------------------------------------
# 【なぜグローバル変数か】
# @ai_function で定義したツール関数は、Agent Frameworkから呼び出されるため、
# 引数を自由に追加できない。そのため、テナント情報は外部から設定する。
#
# 【改善の余地】
# - ContextVar を使ってスレッドセーフにする方法もある
# - 今回はシングルリクエストの処理なので、シンプルにグローバル変数を使用
_current_tenant = "default"
_last_search_results = None  # 検索結果を保持（UIで画像表示に使用）


# =============================================================================
# MCP検索呼び出し
# =============================================================================
async def call_mcp_search(query: str, tenant: str) -> dict:
    """
    MCP検索APIを呼び出す

    【なぜMCPを経由するか】
    - MCPはAIエージェント用のツール提供サーバー
    - 将来的にClaude等のAIから直接呼び出せるようになる
    - 検索ロジックを一箇所に集約できる

    Args:
        query: 検索キーワード
        tenant: テナントID

    Returns:
        検索結果を含む辞書
    """
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                f"{MCP_URL}/api/search",
                json={"query": query, "tenant": tenant, "limit": 5},
                timeout=30.0
            )
            return response.json()
        except Exception as e:
            return {"success": False, "error": str(e), "results": []}


# =============================================================================
# ツール定義（Agent Framework方式）
# =============================================================================
# 【@ai_functionデコレータとは】
# 関数をAIが呼び出せる「ツール」として登録するデコレータ
# 従来のTOOLS辞書での定義が不要になり、コードがシンプルになる
#
# 【メリット】
# - 関数の型ヒントから自動的にスキーマを生成
# - docstringがツールの説明として使われる
# - Pydantic Fieldで詳細な引数の説明を追加できる

@ai_function
async def search_documents(
    query: Annotated[str, Field(description="検索キーワード（スペース区切りで複数指定可能）")]
) -> str:
    """
    PDFから抽出した構造化データを検索する。
    ユーザーが特定のトピックや情報について質問した時に使用する。
    """
    global _last_search_results
    # グローバル変数からテナント情報を取得
    result = await call_mcp_search(query, _current_tenant)
    # 検索結果を保持（UIで画像表示に使用）
    _last_search_results = result
    # Agent Frameworkは文字列を期待するのでJSONに変換
    return json.dumps(result, ensure_ascii=False)


# =============================================================================
# エージェント作成
# =============================================================================
def create_agent():
    """
    DocumentAssistantエージェントを作成

    【エージェントとは】
    - AIモデル + システムプロンプト + ツール をパッケージ化したもの
    - 「このAIはこういう役割で、こういうツールが使える」を定義

    【なぜエージェントパターンか】
    - 設定を一箇所にまとめられる
    - 再利用しやすい
    - テストしやすい

    Returns:
        設定済みのエージェント
    """
    # プロバイダーに応じたクライアントを取得
    client = get_chat_client()

    # エージェントを作成
    return client.as_agent(
        name="DocumentAssistant",
        instructions="""あなたはPDFドキュメントに関する質問に答えるアシスタントです。

ユーザーがドキュメントの内容について質問した場合は、search_documents関数を使って関連情報を検索してください。
検索結果に基づいて、わかりやすく回答してください。

検索結果がない場合や、一般的な質問の場合は、そのまま回答してください。""",
        tools=[search_documents]
    )


# =============================================================================
# チャット処理
# =============================================================================
async def process_chat(message: str, tenant: str = "default") -> dict:
    """
    ユーザーメッセージを処理して回答を生成

    【処理フロー（Agent Framework版）】
    1. テナント情報をグローバル変数に設定
    2. エージェントを作成
    3. agent.run()でメッセージを処理（ツール呼び出しも自動処理）
    4. 結果を返す（検索結果も含む）

    【従来との違い】
    - 従来: 2回のAPI呼び出し（ツール判断 → 結果取得）を自分で制御
    - Agent Framework: agent.run()が全部やってくれる

    Args:
        message: ユーザーのメッセージ
        tenant: テナントID

    Returns:
        回答と検索情報を含む辞書
    """
    global _current_tenant, _last_search_results
    _current_tenant = tenant
    _last_search_results = None  # 検索結果をリセット

    try:
        # エージェントを作成
        agent = create_agent()

        # メッセージを処理（ツール呼び出しも自動で行われる）
        result = await agent.run(message)

        return {
            "success": True,
            "response": result.text,
            "search_performed": _last_search_results is not None,
            "search_results": _last_search_results  # 検索結果を返す（UIで画像表示に使用）
        }

    except ValueError as e:
        # APIキー未設定などの設定エラー
        return {
            "success": False,
            "response": f"設定エラー: {str(e)}",
            "search_performed": False
        }
    except NotImplementedError as e:
        # Azure未設定エラー
        return {
            "success": False,
            "response": f"未実装: {str(e)}",
            "search_performed": False
        }
    except Exception as e:
        return {
            "success": False,
            "response": f"エラーが発生しました: {str(e)}",
            "search_performed": False
        }


# =============================================================================
# 【解説】Agent Frameworkへの移行で何が変わったか
# =============================================================================
#
# 【変更前（OpenAI直接呼び出し）】
# ```python
# TOOLS = [{"type": "function", "function": {...}}]  # 辞書でツール定義
# response = client.chat.completions.create(...)     # 1回目のAPI呼び出し
# if assistant_message.tool_calls:                    # ツール呼び出しの判定
#     search_results = await call_mcp_search(...)     # 検索実行
#     messages.append(...)                            # メッセージ追加
#     final_response = client.chat.completions.create(...)  # 2回目のAPI呼び出し
# ```
#
# 【変更後（Agent Framework）】
# ```python
# @ai_function                                        # デコレータでツール定義
# async def search_documents(...):
#     ...
#
# agent = client.as_agent(tools=[search_documents])  # エージェント作成
# result = await agent.run(message)                   # 全部自動で処理
# ```
#
# 【メリット】
# 1. コード量削減: ツール定義が約1/3に
# 2. 可読性向上: 何をしているか分かりやすい
# 3. プロバイダー切り替え: OpenAI/Azure を設定だけで切り替え可能
# 4. 拡張しやすい: ツール追加がデコレータ付与だけでOK
#
# 【デメリット】
# 1. 内部動作の把握が難しい（抽象化されている）
# 2. 検索有無などの詳細追跡が難しくなる
# 3. フレームワークのバージョンアップでAPIが変わる可能性
#
# 【注意点】
# - agent-frameworkはプレリリース版なのでAPIが変わる可能性がある
# - 本番環境では安定版を待つことを推奨
# - 問題が起きたら元の実装に戻せるよう、git履歴を残しておく
