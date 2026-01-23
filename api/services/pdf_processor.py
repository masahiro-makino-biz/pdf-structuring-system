# =============================================================================
# api/services/pdf_processor.py - PDF処理サービス
# =============================================================================
# PDF→画像変換、GPT-4o解析、MongoDB保存を行う内部サービス
# =============================================================================

import base64
import json
from io import BytesIO
from pathlib import Path
from datetime import datetime

from pdf2image import convert_from_path
from PIL import Image
from openai import OpenAI

# =============================================================================
# 設定とロギング
# =============================================================================
from core.config import get_settings
from core.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()
OPENAI_API_KEY = settings.openai_api_key
DATA_DIR = Path(settings.data_dir)


# =============================================================================
# ユーティリティ関数
# =============================================================================

def image_to_base64(image: Image.Image, format: str = "PNG") -> str:
    """PIL ImageをBase64文字列に変換"""
    buffer = BytesIO()
    image.save(buffer, format=format)
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


# =============================================================================
# PDF処理関数
# =============================================================================

def pdf_to_images(pdf_path: str, tenant: str, file_id: str, dpi: int = 150) -> dict:
    """PDFを画像に変換"""
    pdf_file = Path(pdf_path)
    if not pdf_file.exists():
        return {"success": False, "error": f"PDFファイルが見つかりません: {pdf_path}"}

    output_dir = DATA_DIR / tenant / "images" / file_id
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        images = convert_from_path(pdf_path, dpi=dpi)
    except Exception as e:
        return {"success": False, "error": f"PDF変換エラー: {str(e)}"}

    saved_paths = []
    for i, image in enumerate(images):
        page_num = i + 1
        output_path = output_dir / f"page_{page_num:03d}.png"
        image.save(output_path, "PNG")
        saved_paths.append(str(output_path))

    return {
        "success": True,
        "file_id": file_id,
        "total_pages": len(images),
        "image_paths": saved_paths,
    }


def extract_page_data(image_path: str, page_number: int = 1) -> dict:
    """画像からGPT-4oで構造化データを抽出"""
    if not OPENAI_API_KEY:
        return {"success": False, "error": "OPENAI_API_KEYが設定されていません"}

    image_file = Path(image_path)
    if not image_file.exists():
        return {"success": False, "error": f"画像が見つかりません: {image_path}"}

    try:
        image = Image.open(image_path)
        max_size = 2048
        if max(image.size) > max_size:
            image.thumbnail((max_size, max_size), Image.LANCZOS)
        base64_image = image_to_base64(image)
    except Exception as e:
        return {"success": False, "error": f"画像読み込みエラー: {str(e)}"}

    client = OpenAI(api_key=OPENAI_API_KEY)

    prompt = """
この画像はPDFの1ページです。以下の情報を抽出してJSON形式で返してください。

抽出する情報:
- title: ページのタイトルや見出し（なければnull）
- summary: ページ内容の要約（2-3文で）
- key_points: 重要なポイントのリスト（箇条書きの内容など）
- has_table: 表が含まれているか（true/false）
- has_figure: 図やグラフが含まれているか（true/false）

JSONのみを返してください。説明文は不要です。
"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{base64_image}",
                                "detail": "auto"
                            },
                        },
                    ],
                }
            ],
            max_tokens=1000,
            response_format={"type": "json_object"}
        )

        result_text = response.choices[0].message.content
        structured_data = json.loads(result_text)

        return {
            "success": True,
            "page_number": page_number,
            "data": structured_data,
            "tokens_used": response.usage.total_tokens if response.usage else None
        }

    except json.JSONDecodeError as e:
        return {"success": False, "error": f"JSON解析エラー: {str(e)}"}
    except Exception as e:
        return {"success": False, "error": f"OpenAI APIエラー: {str(e)}"}


async def process_pdf(db, file_id: str, tenant: str = "default") -> dict:
    """PDFを一括処理: 画像変換→AI解析→MongoDB保存"""

    # 1. ファイル情報を取得
    file_doc = await db.files.find_one({"file_id": file_id, "tenant": tenant})
    if not file_doc:
        return {"success": False, "error": f"ファイルが見つかりません: {file_id}"}

    pdf_path = file_doc["path"]
    filename = file_doc["filename"]

    # 2. PDF→画像変換
    conversion_result = pdf_to_images(pdf_path, tenant, file_id)
    if not conversion_result["success"]:
        return conversion_result

    image_paths = conversion_result["image_paths"]
    total_pages = conversion_result["total_pages"]

    # 3. 各ページをAI解析
    pages_data = []
    for i, image_path in enumerate(image_paths):
        page_num = i + 1
        logger.info(f"ページ処理中: {page_num}/{total_pages}")

        extraction_result = extract_page_data(image_path, page_num)

        if extraction_result["success"]:
            pages_data.append({
                "page_number": page_num,
                "image_path": image_path,
                "data": extraction_result["data"],
                "tokens_used": extraction_result.get("tokens_used")
            })
        else:
            pages_data.append({
                "page_number": page_num,
                "image_path": image_path,
                "error": extraction_result.get("error")
            })

    # 4. 結果をMongoDBに保存
    structured_doc = {
        "file_id": file_id,
        "filename": filename,
        "tenant": tenant,
        "total_pages": total_pages,
        "pages": pages_data,
        "processed_at": datetime.utcnow(),
        "status": "completed"
    }

    await db.structured_data.update_one(
        {"file_id": file_id},
        {"$set": structured_doc},
        upsert=True
    )

    # 5. ファイル情報も更新
    await db.files.update_one(
        {"file_id": file_id},
        {"$set": {"processed": True, "processed_at": datetime.utcnow()}}
    )

    return {
        "success": True,
        "file_id": file_id,
        "filename": filename,
        "total_pages": total_pages,
        "pages_processed": len([p for p in pages_data if "data" in p]),
        "pages_with_errors": len([p for p in pages_data if "error" in p])
    }
