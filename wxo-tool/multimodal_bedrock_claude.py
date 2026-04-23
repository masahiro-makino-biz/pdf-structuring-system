"""
watsonx Orchestrate Python Tool
AWS Bedrock (Claude) マルチモーダルリクエスト（画像 + PDF 対応）

【事前準備：コネクション設定】
以下のコマンドを実行してAWS認証情報を登録してください：

# 1. コネクションを追加
orchestrate connections add -a bedrock_claude_credentials

# 2. コネクションを設定（Key-Value方式、チーム共有）
orchestrate connections configure -a bedrock_claude_credentials --env draft -k key_value -t team

# 3. AWS認証情報を設定（実際のトークンに置き換えてください）
orchestrate connections set-credentials -a bedrock_claude_credentials --env draft -e "aws_bearer_token=YOUR_AWS_TOKEN_HERE"

# 4. （オプション）コネクションが正しく設定されたか確認
orchestrate connections list
orchestrate connections get -a bedrock_claude_credentials

【ツールのインポート】
# requirements.txt と一緒にインポート
orchestrate tools import -k python -f multimodal_bedrock_claude.py -r requirements.txt -a bedrock_claude_credentials

【使用方法】
Watson Orchestrate のチャット画面でこのツールを呼び出すと、
AWS Bedrock の Claude モデルにテキスト + 画像/PDF を送信してマルチモーダルな回答を得られます。
"""

import os
import re
import time
from typing import List, Tuple

import boto3
import requests

from ibm_watsonx_orchestrate.agent_builder.connections import ConnectionType
from ibm_watsonx_orchestrate.agent_builder.tools import tool
from ibm_watsonx_orchestrate.run import connections

# コネクション名（上記コマンドで設定した名前と一致させる）
APP_ID = "bedrock_claude_credentials"

# AWS Bedrock 設定（固定値）
_AWS_REGION = "ap-northeast-1"
_MODEL_ID = "jp.anthropic.claude-sonnet-4-6"  # Claude Sonnet 4.6 (日本リージョン)

# 対応する画像フォーマット（Bedrock Converse API の image.format 値）
_IMAGE_FORMATS = {
    "image/jpeg": "jpeg",
    "image/jpg": "jpeg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
}


def _build_client():
    """
    コネクションから AWS Bedrock のベアラートークンを取得し、bedrock-runtime クライアントを生成する。

    AWS_BEARER_TOKEN_BEDROCK 環境変数をセットすると boto3 が自動認識する。
    """
    creds = connections.key_value(APP_ID)
    aws_token = creds["aws_bearer_token"]
    os.environ["AWS_BEARER_TOKEN_BEDROCK"] = aws_token
    return boto3.client("bedrock-runtime", region_name=_AWS_REGION)


def _fetch_and_classify(url: str, timeout: int = 60) -> Tuple[str, bytes]:
    """
    URL からファイルをダウンロードし、Content-Type を判定する。

    優先順位:
    1. HTTP レスポンスの Content-Type ヘッダ
    2. マジックバイト（先頭バイト列）
    3. URL の拡張子

    Returns:
        (content_type, bytes): 例 ("application/pdf", b"%PDF-...")
    """
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    data = r.content

    # 1. Content-Type ヘッダ
    ct = r.headers.get("Content-Type", "").split(";")[0].strip().lower()

    # 2. マジックバイト判定（ヘッダが汎用的すぎる場合）
    if ct in ("", "application/octet-stream", "binary/octet-stream"):
        if data[:4] == b"%PDF":
            ct = "application/pdf"
        elif data[:3] == b"\xFF\xD8\xFF":
            ct = "image/jpeg"
        elif data[:8] == b"\x89PNG\r\n\x1a\n":
            ct = "image/png"
        elif data[:6] in (b"GIF87a", b"GIF89a"):
            ct = "image/gif"
        elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            ct = "image/webp"

    # 3. URL拡張子フォールバック
    if ct not in _IMAGE_FORMATS and ct != "application/pdf":
        path = url.split("?")[0].lower()
        if path.endswith(".pdf"):
            ct = "application/pdf"
        elif path.endswith((".jpg", ".jpeg")):
            ct = "image/jpeg"
        elif path.endswith(".png"):
            ct = "image/png"
        elif path.endswith(".gif"):
            ct = "image/gif"
        elif path.endswith(".webp"):
            ct = "image/webp"

    return ct, data


def _build_content_block(url: str) -> dict:
    """
    URL を元に Bedrock Converse API の content block を構築する。

    Returns:
        dict: {"image": {...}} または {"document": {...}}
    """
    content_type, data = _fetch_and_classify(url)

    if content_type in _IMAGE_FORMATS:
        return {
            "image": {
                "format": _IMAGE_FORMATS[content_type],
                "source": {"bytes": data},
            }
        }

    if content_type == "application/pdf":
        # document.name は英数字・空白・ハイフン・丸括弧のみ許可（Bedrock仕様）
        return {
            "document": {
                "format": "pdf",
                "name": "uploaded-document",
                "source": {"bytes": data},
            }
        }

    raise ValueError(
        f"サポートされていないファイル形式: {content_type}. "
        f"対応: PDF, JPEG, PNG, GIF, WEBP"
    )


def _sanitize_error_message(msg: str) -> str:
    """エラーメッセージからベアラートークンをマスク。"""
    msg = re.sub(r"(Authorization[:\s]+)?Bearer\s+[A-Za-z0-9\-_\.=/+]+", "Bearer ***", msg)
    msg = re.sub(r"bedrock-api-key-[A-Za-z0-9\-_\.=/+]+", "bedrock-api-key-***", msg)
    return msg


def _invoke_with_retry(client, messages: List[dict], system_prompt: str, max_retries: int = 3) -> str:
    """Bedrock Converse API をリトライ付きで呼び出す。"""
    for attempt in range(1, max_retries + 1):
        try:
            response = client.converse(
                modelId=_MODEL_ID,
                messages=messages,
                system=[{"text": system_prompt}],
                inferenceConfig={
                    "temperature": 0,
                    "maxTokens": 16000,
                },
            )
            output_msg = response["output"]["message"]
            text_parts = [b["text"] for b in output_msg.get("content", []) if "text" in b]
            return "\n".join(text_parts)
        except Exception as exc:
            exc_str = _sanitize_error_message(str(exc))

            if "content_filter" in exc_str.lower() or "content policy" in exc_str.lower():
                return (
                    "リクエストが AWS Bedrock のコンテンツポリシーによりブロックされました。"
                    "入力内容を確認してください。"
                )

            print(f"[retry {attempt}/{max_retries}] {type(exc).__name__}: {exc_str[:500]}")
            # ValidationException などは再試行しても同じなので即時終了
            if type(exc).__name__ in ("ValidationException", "AccessDeniedException", "ResourceNotFoundException"):
                return f"Bedrockエラー: {type(exc).__name__}: {exc_str[:500]}"
            if attempt == max_retries:
                return f"エラーが発生しました: {type(exc).__name__}: {exc_str[:300]}"

            time.sleep(2 ** attempt)

    return "予期しないエラーが発生しました。"


@tool(
    expected_credentials=[
        {"app_id": APP_ID, "type": ConnectionType.KEY_VALUE}
    ]
)
def multimodal_request(
    user_text: str,
    file_urls: List[str] = None,
    image_urls: List[str] = None,
    system_prompt: str = "You are a helpful assistant.",
) -> str:
    """
    AWS Bedrock (Claude) にテキストと画像/PDF を送信してマルチモーダルな回答を返す。

    Args:
        user_text (str): ユーザーが送る本文テキスト。
        file_urls (List[str]): 送信するファイルの URL リスト。対応形式: PDF, JPEG, PNG, GIF, WEBP。HTTPS公開URL or 署名付きURLを指定。
        image_urls (List[str]): 旧名 (file_urls と同じ意味。後方互換のため残す)。
        system_prompt (str): システムプロンプト。デフォルトは汎用アシスタント。

    Returns:
        str: Claude モデルの応答テキスト。
    """
    # file_urls または image_urls のどちらでも受け付ける
    urls = file_urls or image_urls or []

    start = time.time()
    try:
        client = _build_client()

        # Converse API の content block を構築
        content_blocks: List[dict] = [{"text": user_text}]
        for url in urls:
            content_blocks.append(_build_content_block(url))

        messages = [{"role": "user", "content": content_blocks}]

        response = _invoke_with_retry(client, messages, system_prompt)
    except ValueError as exc:
        response = f"入力エラー: {exc}"
        print(f"[multimodal_request][ValueError] {exc}")
    except requests.exceptions.RequestException as exc:
        response = f"ファイルダウンロード失敗: {type(exc).__name__}: {str(exc)[:200]}"
        print(f"[multimodal_request][download] {exc}")
    except Exception as exc:  # noqa: BLE001
        sanitized = _sanitize_error_message(str(exc))
        response = f"想定外エラー: {type(exc).__name__}: {sanitized[:200]}"
        print(f"[multimodal_request][error] {type(exc).__name__}: {sanitized}")

    elapsed = time.time() - start
    print(f"[multimodal_request] 実行時間: {elapsed:.2f}秒")
    print(f"[multimodal_request] 送信ファイル数: {len(urls)}")
    print(f"[multimodal_request] テキスト長: {len(user_text)} 文字")

    return response


# ツールのメタデータ（Watson Orchestrate で表示される情報）
__tool_name__ = "AWS Bedrock Claude マルチモーダルリクエスト"
__tool_description__ = "AWS Bedrock の Claude モデルにテキストと画像/PDF を送信して、マルチモーダルな回答を取得します。"

# Made with Bob
