# =============================================================================
# api/services/chat_service.py - チャットサービス
# =============================================================================
#
# 【ファイル概要】
# ユーザーのチャットメッセージを処理し、AIエージェントを使って回答を生成する。
# Microsoft Agent Framework を使用し、OpenAI/Azure OpenAI を切り替え可能。
#
# 【処理フロー】
# 1. main.py の /chat から process_chat() が呼ばれる
# 2. create_agent() でAIエージェントを作成
# 3. agent.run(message) でメッセージを処理
# 4. AIが必要と判断したら @ai_function の search_documents() を呼び出す
# 5. mcp/server.py の検索APIを呼び出し、結果を取得
# 6. AIが検索結果を元に回答を生成
#
# 【依存関係】
# - agent_config.py : AIクライアント設定
# - mcp/server.py   : 検索API（HTTP経由で呼び出し）
#
# =============================================================================

import json
from typing import Annotated
import httpx
from pydantic import Field
from agent_framework import ai_function
from .agent_config import get_chat_client

from core.config import get_settings
from core.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()
MCP_URL = settings.mcp_url

_current_tenant = "default"
_last_search_results = None
async def call_mcp_search(
    tenant: str,
    equipment: str = "",
    equipment_part: str = "",
    inspection_item: str = "",
    inspection_date: str = ""
) -> dict:
    """
    MCP検索APIを呼び出す

    【処理フロー】
    1. 検索条件をJSON形式で構築
    2. httpx で mcp/server.py の /api/search にPOSTリクエスト
    3. 検索結果をdictで返す

    【なぜこの実装か】
    - MCPサーバーに検索ロジックを集約することで、将来Claude等から直接呼び出し可能
    - HTTP経由にすることで、サービス間の疎結合を維持

    Args:
        tenant: テナントID
        equipment: 機器名
        equipment_part: 機器部品名
        inspection_item: 点検項目
        inspection_date: 点検年月日

    Returns:
        検索結果を含む辞書 {"success": bool, "results": [...]}
    """
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                f"{MCP_URL}/api/search",
                json={
                    "equipment": equipment,
                    "equipment_part": equipment_part,
                    "inspection_item": inspection_item,
                    "inspection_date": inspection_date,
                    "tenant": tenant,
                    "limit": 5
                },
                timeout=30.0
            )
            return response.json()
        except Exception as e:
            return {"success": False, "error": str(e), "results": []}


@ai_function
async def search_documents(
    equipment: Annotated[str, Field(description="機器名（例: '2号機微粉炭機D'）", default="")] = "",
    equipment_part: Annotated[str, Field(description="機器部品名（例: 'リンクサポート'）", default="")] = "",
    inspection_item: Annotated[str, Field(description="点検項目（例: '隙間計測'）", default="")] = "",
    inspection_date: Annotated[str, Field(description="点検年月日（例: '2024-01-15', '2024'）", default="")] = ""
) -> str:
    """
    ミル機器の点検記録を検索する（AIが呼び出すツール）

    【処理フロー】
    1. AIがユーザーの質問から検索条件を抽出してこの関数を呼ぶ
    2. call_mcp_search() でMCPサーバーに検索リクエスト
    3. 結果をJSONで返し、AIが回答を生成

    【なぜこの実装か】
    - @ai_function デコレータにより、AIが自動的にこの関数を呼び出せる
    - 引数の description はAIへの説明（どんな値を入れるべきか）

    Args:
        equipment: 機器名
        equipment_part: 機器部品名
        inspection_item: 点検項目
        inspection_date: 点検年月日

    Returns:
        検索結果のJSON文字列
    """
    global _last_search_results
    # グローバル変数からテナント情報を取得
    result = await call_mcp_search(
        tenant=_current_tenant,
        equipment=equipment,
        equipment_part=equipment_part,
        inspection_item=inspection_item,
        inspection_date=inspection_date
    )
    # 検索結果を保持（UIで画像表示に使用）
    _last_search_results = result
    # Agent Frameworkは文字列を期待するのでJSONに変換
    return json.dumps(result, ensure_ascii=False)


def create_agent():
    """
    DocumentAssistantエージェントを作成

    【処理フロー】
    1. get_chat_client() でAIクライアントを取得
    2. client.as_agent() でエージェントを作成
    3. instructions（システムプロンプト）とtools（使えるツール）を設定

    【なぜこの実装か】
    - エージェントパターンにより、AIモデル+プロンプト+ツールを一箇所で管理
    - agent.run() だけで、ツール呼び出しも含めた処理が自動で行われる

    Returns:
        設定済みのエージェント
    """
    # プロバイダーに応じたクライアントを取得
    client = get_chat_client()

    # エージェントを作成
    return client.as_agent(
        name="DocumentAssistant",
        instructions="""あなたはミル機器の点検記録PDFに関する質問に答えるアシスタントです。

ユーザーが点検記録について質問した場合は、search_documents関数を使って検索してください。
検索には以下の4つの条件が使えます（1つ以上指定が必要）:
- equipment: 機器名（例: "2号機微粉炭機D"）
- equipment_part: 機器部品名（例: "リンクサポート"）
- inspection_item: 点検項目（例: "隙間計測"）
- inspection_date: 点検年月日（例: "2024-01-15", "2024"）

ユーザーの質問から適切な条件を抽出して検索してください。
検索結果には測定値と基準値が含まれているので、それを参照して回答してください。

検索結果がない場合や、一般的な質問の場合は、そのまま回答してください。""",
        tools=[search_documents]
    )


async def process_chat(message: str, tenant: str = "default") -> dict:
    """
    ユーザーメッセージを処理して回答を生成

    【処理フロー】
    1. テナント情報をグローバル変数に設定
    2. create_agent() でエージェントを作成
    3. agent.run(message) でメッセージを処理（ツール呼び出しも自動）
    4. 結果を返す（検索結果も含む）

    【なぜこの実装か】
    - Agent Framework の agent.run() がツール呼び出しを自動処理
    - 従来の「2回のAPI呼び出しを自分で制御」が不要になりシンプル

    Args:
        message: ユーザーのメッセージ
        tenant: テナントID

    Returns:
        {"success": bool, "response": str, "search_results": dict}
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


