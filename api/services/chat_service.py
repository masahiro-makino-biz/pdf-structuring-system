# =============================================================================
# api/services/chat_service.py - チャットサービス
# =============================================================================
# ユーザーの質問を受けて、必要に応じてMCPで検索し、回答を生成する
# =============================================================================

import os
import json
import httpx
from openai import OpenAI

# =============================================================================
# 設定
# =============================================================================
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
MCP_URL = os.getenv("MCP_URL", "http://mcp:8001")


# =============================================================================
# OpenAI Function Calling用のツール定義
# =============================================================================
# 【Function Callingとは】
# OpenAI APIに「こういうツールが使えるよ」と教えておくと、
# AIが必要に応じてツールを呼び出すように指示してくる仕組み

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_documents",
            "description": "PDFから抽出した構造化データを検索する。ユーザーが特定のトピックや情報について質問した時に使用する。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "検索キーワード（スペース区切りで複数指定可能）"
                    }
                },
                "required": ["query"]
            }
        }
    }
]


# =============================================================================
# MCP検索呼び出し
# =============================================================================
async def call_mcp_search(query: str, tenant: str = "default") -> dict:
    """
    MCP検索APIを呼び出す

    【なぜMCPを経由するか】
    - MCPはAIエージェント用のツール提供サーバー
    - 将来的にClaude等のAIから直接呼び出せるようになる
    - 今はHTTP経由で呼び出し
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
# チャット処理
# =============================================================================
async def process_chat(message: str, tenant: str = "default") -> dict:
    """
    ユーザーメッセージを処理して回答を生成

    【処理フロー】
    1. OpenAI GPT-4oにメッセージを送信（ツール定義付き）
    2. AIが検索を必要と判断したら、MCP検索を実行
    3. 検索結果をAIに渡して最終回答を生成

    Args:
        message: ユーザーのメッセージ
        tenant: テナントID

    Returns:
        回答と検索情報を含む辞書
    """
    if not OPENAI_API_KEY:
        return {
            "success": False,
            "response": "OpenAI APIキーが設定されていません。",
            "search_performed": False
        }

    client = OpenAI(api_key=OPENAI_API_KEY)

    # システムプロンプト
    system_prompt = """あなたはPDFドキュメントに関する質問に答えるアシスタントです。

ユーザーがドキュメントの内容について質問した場合は、search_documents関数を使って関連情報を検索してください。
検索結果に基づいて、わかりやすく回答してください。

検索結果がない場合や、一般的な質問の場合は、そのまま回答してください。"""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": message}
    ]

    try:
        # 1回目: AIに質問を送信（ツール呼び出しの判断）
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            tools=TOOLS,
            tool_choice="auto"  # AIが自動判断
        )

        assistant_message = response.choices[0].message
        search_performed = False
        search_results = None

        # ツール呼び出しがあるかチェック
        if assistant_message.tool_calls:
            for tool_call in assistant_message.tool_calls:
                if tool_call.function.name == "search_documents":
                    # 検索を実行
                    args = json.loads(tool_call.function.arguments)
                    search_results = await call_mcp_search(
                        query=args.get("query", message),
                        tenant=tenant
                    )
                    search_performed = True

                    # メッセージ履歴にツール呼び出しと結果を追加
                    messages.append(assistant_message)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": json.dumps(search_results, ensure_ascii=False)
                    })

            # 2回目: 検索結果を元に最終回答を生成
            final_response = client.chat.completions.create(
                model="gpt-4o",
                messages=messages
            )
            answer = final_response.choices[0].message.content

        else:
            # ツール呼び出しなし = 直接回答
            answer = assistant_message.content

        return {
            "success": True,
            "response": answer,
            "search_performed": search_performed,
            "search_results": search_results
        }

    except Exception as e:
        return {
            "success": False,
            "response": f"エラーが発生しました: {str(e)}",
            "search_performed": False
        }
