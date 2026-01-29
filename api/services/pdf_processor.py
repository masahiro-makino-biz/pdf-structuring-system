# =============================================================================
# api/services/pdf_processor.py - PDF処理サービス
# =============================================================================
#
# 【ファイル概要】
# PDFファイルを画像に変換し、GPT-4o Vision APIで構造化データを抽出する。
# 抽出結果はMongoDBに保存される。
#
# 【処理フロー】
# 1. main.py の /admin/process/{id} から process_pdf() が呼ばれる
# 2. pdf_to_images() でPDFを各ページPNG画像に変換
# 3. extract_page_data() で各画像をGPT-4oに送信、構造化JSONを取得
# 4. MongoDBの structured_data コレクションに保存
#
# 【依存関係】
# - pdf2image : PDF→画像変換（内部でpopplerを使用）
# - openai    : GPT-4o Vision API呼び出し
# - MongoDB   : 構造化データ保存
#
# =============================================================================

import base64
import json
from io import BytesIO
from pathlib import Path
from datetime import datetime

from pdf2image import convert_from_path
from PIL import Image
from openai import OpenAI

from core.config import get_settings
from core.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()
OPENAI_API_KEY = settings.openai_api_key
DATA_DIR = Path(settings.data_dir)


def image_to_base64(image: Image.Image, format: str = "PNG") -> str:
    """
    PIL ImageをBase64文字列に変換

    【なぜこの実装か】
    - GPT-4o Vision APIは画像をBase64形式で受け取る
    - BytesIOを使ってメモリ上で変換することでファイルI/Oを避ける

    Args:
        image: PIL Imageオブジェクト
        format: 画像フォーマット

    Returns:
        Base64エンコードされた文字列
    """
    buffer = BytesIO()
    image.save(buffer, format=format)
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


def pdf_to_images(pdf_path: str, tenant: str, file_id: str, dpi: int = 150) -> dict:
    """
    PDFを画像に変換

    【処理フロー】
    1. pdf2image.convert_from_path() でPDFを画像リストに変換
    2. 各ページを /data/{tenant}/images/{file_id}/page_001.png 形式で保存
    3. 保存したパスのリストを返す

    【なぜこの実装か】
    - GPT-4o Vision APIは画像を入力とするため、PDFを画像に変換する必要がある
    - dpi=150 は品質とファイルサイズのバランス

    Args:
        pdf_path: PDFファイルのパス
        tenant: テナントID
        file_id: ファイルID
        dpi: 解像度

    Returns:
        {"success": bool, "image_paths": [...], "total_pages": int}
    """
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
    """
    画像からGPT-4oで構造化データを抽出

    【処理フロー】
    1. 画像を読み込み、大きすぎる場合はリサイズ
    2. Base64に変換
    3. GPT-4o Vision APIにプロンプトと画像を送信
    4. 返ってきたJSONをパースして返す

    【なぜこの実装か】
    - GPT-4oのVision機能を使い、画像から直接情報を抽出
    - response_format={"type": "json_object"} でJSON形式を強制
    - max_size=2048 はAPIの推奨サイズ

    Args:
        image_path: 画像ファイルのパス
        page_number: ページ番号

    Returns:
        {"success": bool, "data": {...}, "tokens_used": int}
    """
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
この画像はミル機器の点検記録PDFの1ページです。以下のJSON形式で情報を抽出してください。

{
  "records": [
    {
      "機器": "対象機器の名称（例: 2号機微粉炭機D）",
      "機器部品": "図面上の名称や記録項目に記載の部品名（例: リンクサポート）",
      "計測箇所": "測定を行った具体的な場所",
      "点検項目": "記録対象となる項目名（例: プレッシャーフレームリンクサポート計測記録）",
      "点検年月日": "YYYY-MM-DD形式の日付",
      "測定者": "測定担当者の氏名",
      "計測器具": "使用した測定器具（例: 直尺R300mm（S481））",
      "単位": "測定値の単位（例: mm, ℃）",
      "測定値": {
        "階層パス形式のキー": 数値または文字列,
        "例: タイヤ①・a・上": 5.2,
        "例: A・No1・調整後": 3.0
      },
      "基準値": {
        "階層パス形式のキー": 数値または文字列,
        "例: 上限": 6.0,
        "例: 下限": 4.0
      }
    }
  ]
}

注意事項:
- 測定値と基準値のキーは「・」区切りで階層を表現する（スラッシュではなく中点）
- 1ページに複数の記録がある場合はrecords配列に複数要素を入れる
- 見つからない項目はnullを設定
- JSONのみを返してください。説明文は不要です。
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
    """
    PDFを一括処理: 画像変換→AI解析→MongoDB保存

    【処理フロー】
    1. MongoDBからファイル情報を取得
    2. pdf_to_images() でPDFを画像に変換
    3. 各ページに対して extract_page_data() でAI構造化
    4. 結果を structured_data コレクションに保存
    5. files コレクションの processed フラグを更新

    【なぜこの実装か】
    - 非同期関数にすることで、DB操作中に他のリクエストをブロックしない
    - 各ページを順番に処理することで、API rate limitに対応

    Args:
        db: MongoDBデータベースオブジェクト
        file_id: 処理するファイルのID
        tenant: テナントID

    Returns:
        {"success": bool, "total_pages": int, "pages_processed": int}
    """

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
