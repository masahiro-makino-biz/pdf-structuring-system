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
# 4. MongoDBの documents コレクションを更新（処理結果を追加）
#
# 【依存関係】
# - pdf2image : PDF→画像変換（内部でpopplerを使用）
# - openai    : GPT-4o Vision API呼び出し
# - MongoDB   : documents コレクション（ファイル情報と処理結果を統合）
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


# =============================================================================
# JSONスキーマ定義（Structured Outputs用）
# =============================================================================
JSON_SCHEMA = {
    "name": "mill_inspection_data",
    "strict": False,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["records"],
        "properties": {
            "records": {
                "type": "array",
                "description": "個々の点検記録を要素として持つ配列。1ページに複数の表がある場合は複数要素。",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "機器", "機器部品", "計測箇所", "点検項目",
                        "点検年月日", "測定者", "計測器具", "単位", "測定値", "基準値"
                    ],
                    "properties": {
                        "機器": {
                            "type": ["string", "null"],
                            "description": "対象となるミル機器の名称。例：2号機微粉炭機D"
                        },
                        "機器部品": {
                            "type": ["string", "null"],
                            "description": "図面上の名称・もしくは記録項目に記載のケースが多い。例：リンクサポート隙間計測"
                        },
                        "計測箇所": {
                            "type": ["string", "null"],
                            "description": "測定を行った具体的な場所"
                        },
                        "点検項目": {
                            "type": ["string", "null"],
                            "description": "記録対象となる項目。例：プレッシャーフレームリンクサポート計測記録"
                        },
                        "点検年月日": {
                            "type": ["string", "null"],
                            "description": "記録を実施した日付。ISO形式（YYYY-MM-DD）を推奨。"
                        },
                        "測定者": {
                            "type": ["string", "null"],
                            "description": "測定を担当した人物の氏名。"
                        },
                        "計測器具": {
                            "type": ["string", "null"],
                            "description": "使用した測定器具の名称や型式。例：直尺R300mm（S481）"
                        },
                        "単位": {
                            "type": ["string", "null"],
                            "description": "測定値の単位。例：mm, ℃"
                        },
                        "測定値": {
                            "type": "object",
                            "description": "測定結果を表す。キーは「・」区切りで階層パス形式（例：タイヤ①・a・上）",
                            "additionalProperties": {
                                "type": ["number", "string", "null"]
                            }
                        },
                        "基準値": {
                            "type": "object",
                            "description": "測定値と比較する基準値。キーは「・」区切りの階層パス形式。±などは文字列で表現。",
                            "additionalProperties": {
                                "type": ["number", "string", "null"]
                            }
                        }
                    }
                }
            }
        }
    }
}


def extract_page_data(image_path: str, page_number: int = 1) -> dict:
    """
    画像からGPT-4oで構造化データを抽出（Structured Outputs使用）

    【処理フロー】
    1. 画像を読み込み、大きすぎる場合はリサイズ
    2. Base64に変換
    3. GPT-4o Vision APIにJSONスキーマと画像を送信
    4. スキーマに従った構造化JSONを取得

    【なぜこの実装か】
    - Structured Outputsを使用し、JSONスキーマに厳密に従った出力を保証
    - GPT-4oのVision機能で画像から直接情報を抽出

    Args:
        image_path: 画像ファイルのパス
        page_number: ページ番号

    Returns:
        {"success": bool, "data": {...}}
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

    system_prompt = (
        "あなたは発電所の点検記録画像から、指定のJSONスキーマに厳密準拠したJSONを出力する専門家です。\n"
        "出力はJSONのみ（説明・コードフェンス禁止）。\n"
        "・トップはobject。\n"
        "・'records' は1ページから読み取れるレコード配列。1つの表につき1つのrecord。\n"
        "・各レコードはスキーマ上の全フィールドを必ず持つ（値が未知でもnullを許容）。\n"
        "・スキーマに存在しないキーは出さない。\n"
        "・表の行名（摩耗量/振動値/温度など）と列名（①/A/上など）を組み合わせて'測定値'と'基準値'のパス型キーとして格納。\n"
    )

    user_prompt = (
        "次の画像から指定スキーマに準拠したJSONを出力してください。\n"
        "注意:\n"
        "1) 1つの表につき1つのrecordを作成（行ごとではなく表ごと）。\n"
        "2) 表内の各行（摩耗量、振動値、温度など）は測定値のキーに含める。例：「摩耗量・タイヤ①」「振動値・A点」。\n"
        "3) '測定値'/'基準値'はオブジェクトで、キーは「・」区切りの階層パス形式。\n"
        "4) 未知/空欄/スラッシュ/×/- などはnullを格納。\n"
        "5) 表にある値は漏れなく全て取得。\n"
        "6) 記載のない情報を推測で入れない。記載のある情報のみをもとに出力。\n"
        "7) ハルシネーションは厳禁。\n"
        "8) JSONのみ。余計な文章は一切禁止。\n"
        "9) 日付はYYYY-MM-DD形式。\n"
    )

    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "system",
                    "content": system_prompt
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_prompt},
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
            max_tokens=4000,
            response_format={
                "type": "json_schema",
                "json_schema": JSON_SCHEMA
            }
        )

        result_text = response.choices[0].message.content
        structured_data = json.loads(result_text)

        return {
            "success": True,
            "page_number": page_number,
            "data": structured_data,
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

    # 1. ファイル情報を取得（pages コレクションの未処理ドキュメント）
    file_doc = await db.pages.find_one({"file_id": file_id, "tenant": tenant})
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
    processed_at = datetime.utcnow()

    # 3. 各ページをAI解析し、レコードごとに pages コレクションに保存
    records_processed = 0
    pages_with_errors = 0

    for i, image_path in enumerate(image_paths):
        page_num = i + 1
        logger.info(f"ページ処理中: {page_num}/{total_pages}")

        extraction_result = extract_page_data(image_path, page_num)

        if extraction_result["success"]:
            # レコードごとにドキュメントを作成
            records = extraction_result["data"].get("records", [])
            for record_idx, record_data in enumerate(records):
                record_doc = {
                    # メタデータ
                    "file_id": file_id,
                    "filename": filename,
                    "path": file_doc["path"],
                    "tenant": tenant,
                    "uploaded_at": file_doc["uploaded_at"],
                    "processed": True,
                    "processed_at": processed_at,
                    "page_number": page_num,
                    "table_index": record_idx + 1,
                    "table_title": record_data.get("点検項目"),
                    "image_path": image_path,
                    # データ（スキーマに従った構造化データ）
                    "data": record_data,
                }
                await db.pages.insert_one(record_doc)
                records_processed += 1
        else:
            # エラーの場合はページ単位で保存
            error_doc = {
                "file_id": file_id,
                "filename": filename,
                "path": file_doc["path"],
                "tenant": tenant,
                "uploaded_at": file_doc["uploaded_at"],
                "processed": True,
                "processed_at": processed_at,
                "page_number": page_num,
                "table_index": None,
                "table_title": None,
                "image_path": image_path,
                "error": extraction_result.get("error"),
            }
            await db.pages.insert_one(error_doc)
            pages_with_errors += 1

    # 4. 未処理ドキュメント（page_number: null）を削除
    await db.pages.delete_one({"file_id": file_id, "tenant": tenant, "page_number": None})

    return {
        "success": True,
        "file_id": file_id,
        "filename": filename,
        "total_pages": total_pages,
        "records_processed": records_processed,
        "pages_with_errors": pages_with_errors
    }
