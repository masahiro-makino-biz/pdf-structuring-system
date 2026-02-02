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
# 3. プロバイダーに応じてエージェントを実行
#    - OpenAI: agent-framework の @ai_function を使用
#    - Bedrock: Anthropic SDK で直接ツール呼び出し
# 4. 結果を履歴に追加して返す
#
# 【依存関係】
# - agent_config.py : AIクライアント設定
# - mcp/server.py   : 検索API（HTTP経由で呼び出し）
#
# 【Bedrock対応について】
# - agent-framework は Bedrock 非対応のため、Anthropic SDK を直接使用
# - ツール定義とツール呼び出しループを手動実装
#
# =============================================================================

import json
from typing import Annotated
import httpx
from pydantic import Field
from agent_framework import ai_function
from .agent_config import get_chat_client, PROVIDER

from core.config import get_settings
from core.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()
MCP_URL = settings.mcp_url

_current_tenant = "default"
_last_search_results = None

# =============================================================================
# Bedrock用ツール定義
# =============================================================================
# 【なぜ別途定義が必要か】
# - agent-framework の @ai_function はBedrock非対応
# - Anthropic SDK は独自のツール定義形式を使用
# - OpenAI形式とは異なるため、個別に定義が必要

SYSTEM_PROMPT = """あなたはミル機器の点検記録PDFに関する質問に答えるアシスタントです。

ユーザーが点検記録について質問した場合は、search_documents関数を使って検索してください。
検索には以下の4つの条件が使えます（1つ以上指定が必要）:
- equipment: 機器名（例: "2号機微粉炭機D"）
- equipment_part: 機器部品名（例: "リンクサポート"）
- inspection_item: 点検項目（例: "隙間計測"）
- inspection_date: 点検年月日（例: "2024-01-15", "2024"）

ユーザーの質問から適切な条件を抽出して検索してください。
検索結果には測定値と基準値が含まれているので、それを参照して回答してください。

会話の文脈を考慮して回答してください。例えば「去年のは？」と聞かれたら、
直前の会話で話題になっていた機器について検索してください。

検索結果がない場合や、一般的な質問の場合は、そのまま回答してください。"""

BEDROCK_TOOLS = [
    {
        "name": "search_documents",
        "description": "ミル機器の点検記録を検索する。検索条件は1つ以上指定が必要。",
        "input_schema": {
            "type": "object",
            "properties": {
                "equipment": {
                    "type": "string",
                    "description": "機器名（例: '2号機微粉炭機D'）"
                },
                "equipment_part": {
                    "type": "string",
                    "description": "機器部品名（例: 'リンクサポート'）"
                },
                "inspection_item": {
                    "type": "string",
                    "description": "点検項目（例: '隙間計測'）"
                },
                "inspection_date": {
                    "type": "string",
                    "description": "点検年月日（例: '2024-01-15', '2024'）"
                }
            }
        }
    }
]

# =============================================================================
# チャット履歴管理
# =============================================================================
_chat_histories: dict[str, list[dict]] = {}
MAX_HISTORY_LENGTH = 20


def get_history(session_id: str) -> list[dict]:
    """
    セッションの履歴を取得

    【なぜこの実装か】
    - セッションIDごとに履歴を分離し、複数ユーザーの同時利用に対応

    Args:
        session_id: セッションID

    Returns:
        メッセージ履歴のリスト [{"role": "user", "content": "..."}, ...]
    """
    return _chat_histories.get(session_id, [])


def add_to_history(session_id: str, role: str, content: str):
    """
    履歴にメッセージを追加

    【処理フロー】
    1. セッションIDがなければ新規作成
    2. メッセージを追加
    3. 最大件数を超えたら古いものを削除

    【なぜこの実装か】
    - MAX_HISTORY_LENGTH で履歴を制限し、トークン消費を抑える
    - 古いメッセージから削除することで、直近の文脈を優先

    Args:
        session_id: セッションID
        role: "user" または "assistant"
        content: メッセージ内容
    """
    if session_id not in _chat_histories:
        _chat_histories[session_id] = []
    _chat_histories[session_id].append({"role": role, "content": content})
    if len(_chat_histories[session_id]) > MAX_HISTORY_LENGTH:
        _chat_histories[session_id] = _chat_histories[session_id][-MAX_HISTORY_LENGTH:]


def clear_history(session_id: str):
    """
    履歴をクリア

    Args:
        session_id: セッションID
    """
    if session_id in _chat_histories:
        del _chat_histories[session_id]
        logger.info(f"履歴クリア: session_id={session_id}")


# =============================================================================
# MCP検索
# =============================================================================
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
    result = await call_mcp_search(
        tenant=_current_tenant,
        equipment=equipment,
        equipment_part=equipment_part,
        inspection_item=inspection_item,
        inspection_date=inspection_date
    )
    _last_search_results = result
    return json.dumps(result, ensure_ascii=False)


# =============================================================================
# Bedrock用ツール実行
# =============================================================================
async def execute_bedrock_tool(tool_name: str, tool_input: dict) -> str:
    """
    Bedrockからのツール呼び出しを実行する

    【処理フロー】
    1. ツール名に応じて対応する関数を呼び出す
    2. 結果をJSON文字列で返す

    【なぜこの実装か】
    - agent-framework の @ai_function が使えないため手動でディスパッチ
    - 将来ツールが増えた場合も、ここに追加するだけでOK

    Args:
        tool_name: ツール名（"search_documents" など）
        tool_input: ツールへの入力パラメータ

    Returns:
        ツール実行結果のJSON文字列
    """
    global _last_search_results

    if tool_name == "search_documents":
        result = await call_mcp_search(
            tenant=_current_tenant,
            equipment=tool_input.get("equipment", ""),
            equipment_part=tool_input.get("equipment_part", ""),
            inspection_item=tool_input.get("inspection_item", ""),
            inspection_date=tool_input.get("inspection_date", "")
        )
        _last_search_results = result
        return json.dumps(result, ensure_ascii=False)

    return json.dumps({"error": f"Unknown tool: {tool_name}"})


# =============================================================================
# Bedrock用チャット処理
# =============================================================================
async def process_chat_bedrock(message: str, tenant: str, session_id: str) -> dict:
    """
    Bedrock (Claude) を使ってチャットを処理する

    【処理フロー】
    1. 履歴を取得してメッセージリストを構築
    2. Claude に質問を送信
    3. ツール呼び出しがあれば実行し、結果を再送信
    4. 最終回答を取得して履歴に保存

    【なぜこの実装か】
    - agent-framework が Bedrock 非対応のため、Anthropic SDK を直接使用
    - ツール呼び出しループを手動で実装（stop_reason == "tool_use" の間繰り返す）

    【注意点】
    - Anthropic のメッセージ形式は OpenAI とは異なる
    - tool_result は user ロールで送信する必要がある

    Args:
        message: ユーザーのメッセージ
        tenant: テナントID
        session_id: セッションID

    Returns:
        {"success": bool, "response": str, "search_results": dict}
    """
    global _current_tenant, _last_search_results
    _current_tenant = tenant
    _last_search_results = None

    try:
        client = get_chat_client()

        # 履歴からメッセージリストを構築
        history = get_history(session_id)
        messages = []

        # 履歴を Anthropic 形式に変換
        for msg in history:
            messages.append({
                "role": msg["role"],
                "content": msg["content"]
            })

        # 今回のメッセージを追加
        messages.append({"role": "user", "content": message})

        # Claude に質問
        response = client.messages.create(
            model=settings.bedrock_model_id,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=messages,
            tools=BEDROCK_TOOLS
        )

        # ツール呼び出しループ
        # 【なぜループが必要か】
        # - Claude は1回の応答で複数のツールを呼び出すことがある
        # - ツール結果を見てさらにツールを呼ぶ場合もある
        # - stop_reason が "end_turn" になるまで繰り返す
        while response.stop_reason == "tool_use":
            tool_results = []

            # レスポンスから tool_use ブロックを抽出
            for block in response.content:
                if block.type == "tool_use":
                    logger.info(f"ツール呼び出し: {block.name}, input={block.input}")
                    result = await execute_bedrock_tool(block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result
                    })

            # アシスタントの応答（ツール呼び出し）を追加
            messages.append({"role": "assistant", "content": response.content})
            # ツール結果を user ロールで追加
            messages.append({"role": "user", "content": tool_results})

            # 再度 Claude に質問
            response = client.messages.create(
                model=settings.bedrock_model_id,
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                messages=messages,
                tools=BEDROCK_TOOLS
            )

        # 最終回答を取得
        answer = ""
        for block in response.content:
            if hasattr(block, "text"):
                answer += block.text

        # 履歴に追加
        add_to_history(session_id, "user", message)
        add_to_history(session_id, "assistant", answer)

        logger.info(f"Bedrockチャット処理完了: session_id={session_id}")

        return {
            "success": True,
            "response": answer,
            "search_performed": _last_search_results is not None,
            "search_results": _last_search_results
        }

    except Exception as e:
        logger.error(f"Bedrockチャットエラー: {e}")
        return {
            "success": False,
            "response": f"エラーが発生しました: {str(e)}",
            "search_performed": False
        }


# =============================================================================
# エージェント作成（OpenAI用）
# =============================================================================
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
    client = get_chat_client()

    return client.as_agent(
        name="DocumentAssistant",
        instructions=SYSTEM_PROMPT,
        tools=[search_documents]
    )


# =============================================================================
# チャット処理
# =============================================================================
async def process_chat(message: str, tenant: str = "default", session_id: str = "default") -> dict:
    """
    ユーザーメッセージを処理して回答を生成

    【処理フロー】
    1. AI_PROVIDER をチェック
    2. Bedrock なら process_chat_bedrock() を呼び出す
    3. それ以外なら agent-framework を使用

    【なぜこの実装か】
    - Bedrock と OpenAI で処理フローが異なるため分岐
    - Bedrock: Anthropic SDK を直接使用
    - OpenAI: agent-framework の @ai_function を使用

    Args:
        message: ユーザーのメッセージ
        tenant: テナントID
        session_id: セッションID

    Returns:
        {"success": bool, "response": str, "search_results": dict}
    """
    # Bedrock の場合は専用処理
    if PROVIDER == "bedrock":
        return await process_chat_bedrock(message, tenant, session_id)

    # OpenAI/Azure の場合は agent-framework を使用
    global _current_tenant, _last_search_results
    _current_tenant = tenant
    _last_search_results = None

    try:
        agent = create_agent()

        # 履歴を取得してコンテキストを構築
        history = get_history(session_id)
        if history:
            # 履歴がある場合、過去の会話を含めたプロンプトを作成
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

        # メッセージを処理
        result = await agent.run(full_message)

        # 履歴に追加
        add_to_history(session_id, "user", message)
        add_to_history(session_id, "assistant", result.text)

        logger.info(f"チャット処理完了: session_id={session_id}, 履歴件数={len(get_history(session_id))}")

        return {
            "success": True,
            "response": result.text,
            "search_performed": _last_search_results is not None,
            "search_results": _last_search_results
        }

    except ValueError as e:
        return {
            "success": False,
            "response": f"設定エラー: {str(e)}",
            "search_performed": False
        }
    except NotImplementedError as e:
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
