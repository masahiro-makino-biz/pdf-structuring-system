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
from services.pipeline import run_pipeline

logger = get_logger(__name__)
settings = get_settings()
DATA_DIR = Path(settings.data_dir)
FEWSHOT_DIR = DATA_DIR / "fewshot"


def get_openai_client():
    """
    LiteLLM経由のOpenAIクライアントを取得

    【なぜLiteLLM経由か】
    - チャットと同じ設定でPDF処理も行える
    - OpenAI/Azure/Bedrockなど、プロバイダー切り替えが一箇所で可能
    - litellm/config.yaml で設定したモデルを使用
    """
    logger.info(f"LiteLLMクライアント作成: url={settings.litellm_url}")
    return OpenAI(
        api_key=settings.litellm_api_key,
        base_url=f"{settings.litellm_url}/v1"
    )


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


# few-shot例題のキャッシュ（ページごとに毎回ファイルを読まないようにする）
_fewshot_cache = {"loaded": False, "data": None}


def load_fewshot_example():
    """
    /data/fewshot/ から例題（画像+正解JSON）を1件読み込む

    【なぜこの関数が必要か】
    - few-shot学習: GPT-4oに「正しい構造化の見本」を見せることで精度を上げる
    - 画像＋正解JSONのペアをプロンプト内の会話例として挿入する

    【ディレクトリ構造】
    data/fewshot/
      example.png     ← 例題の点検記録画像（.jpg も可）
      example.json    ← その画像に対する正解JSON

    【キャッシュ】
    - 一度読み込んだ結果をメモリに保持し、2回目以降はディスクI/Oを省略
    - PDFが複数ページある場合、ページごとにこの関数が呼ばれるため効果的

    Returns:
        {"image_base64": str, "json_text": str} または None（例題がない場合）
    """
    if _fewshot_cache["loaded"]:
        return _fewshot_cache["data"]

    def _cache_and_return(data):
        _fewshot_cache["loaded"] = True
        _fewshot_cache["data"] = data
        return data

    if not FEWSHOT_DIR.exists():
        return _cache_and_return(None)

    # 画像ファイルを探す（.png または .jpg/.jpeg）
    image_path = None
    for ext in ["*.png", "*.jpg", "*.jpeg"]:
        found = list(FEWSHOT_DIR.glob(ext))
        if found:
            image_path = found[0]
            break

    # JSONファイルを探す
    json_files = list(FEWSHOT_DIR.glob("*.json"))
    json_path = json_files[0] if json_files else None

    # 両方揃っていなければfew-shotなし
    if not image_path or not json_path:
        return _cache_and_return(None)

    try:
        # 画像をBase64に変換（extract_page_dataと同じリサイズ処理）
        image = Image.open(image_path)
        max_size = 2048
        if max(image.size) > max_size:
            image.thumbnail((max_size, max_size), Image.LANCZOS)
        image_base64 = image_to_base64(image)

        # 正解JSONを読み込み
        json_text = json_path.read_text(encoding="utf-8")
        # JSONとして有効か検証
        json.loads(json_text)

        logger.info(f"Few-shot例題を読み込み: image={image_path.name}, json={json_path.name}")
        return _cache_and_return({"image_base64": image_base64, "json_text": json_text})

    except Exception as e:
        logger.warning(f"Few-shot例題の読み込みに失敗（無視して続行）: {e}")
        return _cache_and_return(None)


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
                "description": (
                    "個々の点検記録を要素として持つ配列。"
                    "1つの表 = 1つのレコード。行ごとに分割しない。"
                    "1ページに複数の表がある場合のみ複数要素にする。"
                ),
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "点検タイトル", "機器", "機器部品", "測定物理量",
                        "点検年月日", "測定者", "計測器具", "単位", "測定値", "基準値"
                    ],
                    "properties": {
                        "点検タイトル": {
                            "type": ["string", "null"],
                            "description": (
                                "PDF全体のタイトル（表紙やヘッダに書かれた文書名）。"
                                "例：微粉炭機定期点検記録、プレッシャーフレームリンクサポート計測記録。"
                                "同一PDF内の全レコードで同じ値になる。"
                            )
                        },
                        "機器": {
                            "type": ["string", "null"],
                            "description": (
                                "対象機器の名称。PDFの表記をそのまま使う。"
                                "例：2号機微粉炭機D。"
                                "表のタイトルや周辺テキストに機器名が含まれていればそこから抽出する。"
                            )
                        },
                        "機器部品": {
                            "type": ["string", "null"],
                            "description": (
                                "機器内の部品名。PDFの表記をそのまま使う。"
                                "表のタイトルや周辺テキストに部位の階層がある場合は"
                                "『・』区切りでブレイクダウンする。"
                                "例：インペラ、インペラ・外周部、軸受・ドライブ側。"
                                "階層数は任意。"
                            )
                        },
                        "測定物理量": {
                            "type": ["string", "null"],
                            "description": (
                                "何を測定したかを表す物理量の名称。"
                                "例：摩耗量、振動値、温度、肉厚、当たり幅。"
                                "表のタイトルや列/行ヘッダーから抽出する。"
                                "タイトルに『ローラタイヤ摩耗量測定』のように位置+物理量が混在している場合は"
                                "物理量部分（摩耗量）だけを抽出し、位置部分（ローラタイヤ）は機器部品側に入れる。"
                                "明記がない場合はAIが表の内容から推測してよい。"
                            )
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
                            "description": "測定値の単位。例：mm, ℃, mm/s"
                        },
                        "測定値": {
                            "type": "object",
                            "description": (
                                "表内の行列から取得した個々の測定値。"
                                "キーは表の行ラベル・列ラベルをそのまま使う。"
                                "複数階層のラベル（行の親子、列の親子、上下/左右 等）は"
                                "『・』区切りでパス化する。例：『タイヤ①』『A点・上』『1段・左』。"
                                "物理量は測定物理量フィールドに入っているため、ここのキーには含めない。"
                            ),
                            "additionalProperties": {
                                "type": ["number", "string", "null"]
                            }
                        },
                        "基準値": {
                            "type": "object",
                            "description": (
                                "測定値と比較する基準値。キーは測定値と同じ行列ラベル形式、"
                                "または単一基準値ならその物理量名（例：摩耗量）でよい。"
                                "±や≦を含む基準は文字列で表現する。"
                            ),
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

    client = get_openai_client()

    system_prompt = (
        "あなたは発電所等の点検記録画像から構造化JSONを出力する専門家です。\n"
        "出力はJSONのみ（説明・コードフェンス禁止）。\n"
        "\n"
        "【出力形式（厳守）】\n"
        '必ず以下の形式で出力すること。recordsキーは必須。表がなくても空配列 [] を返すこと。\n'
        '{"records": [{"点検タイトル": "...", "機器": "...", "機器部品": "...", '
        '"測定物理量": "...", "点検年月日": "...", "測定者": null, "計測器具": null, '
        '"単位": "...", "測定値": {"キー": 値}, "基準値": {"キー": "値"}}]}\n'
        "\n"
        "・各レコードは上記の全フィールドを必ず持つ（値が未知でもnullを許容）。\n"
        "\n"
        "【抽出ルールの全体像】\n"
        "PDFは階層的に情報を持っている。以下の対応で抽出すること：\n"
        "  PDF全体のタイトル          → 点検タイトル（全レコード共通）\n"
        "  機器名（PDF上部や表周辺）  → 機器\n"
        "  部品名・位置（表の周辺）   → 機器部品（『・』区切りで詳細化可）\n"
        "  物理量（タイトルやヘッダー）→ 測定物理量\n"
        "  表の行列ラベル＋数値       → 測定値のキーと値\n"
        "  基準値                     → 基準値\n"
    )

    user_prompt = (
        "次の画像から指定スキーマに準拠したJSONを出力してください。\n"
        "\n"
        "【最重要ルール】\n"
        "★ 1つの表 = 1つのレコード。表の行ごとにレコードを分割してはいけない。\n"
        "★ 表内の全データ（全行×全列）を1つの測定値オブジェクトにまとめること。\n"
        "★ 1ページに複数の表がある場合のみ、表ごとにレコードを分ける。\n"
        "\n"
        "【抽出ルール】\n"
        "1) 表のタイトルや周辺テキストは分解する：\n"
        "   - 機器名に該当する部分 → 『機器』\n"
        "   - 部品や位置に該当する部分 → 『機器部品』（階層があれば『・』区切り）\n"
        "   - 物理量（摩耗量・振動値・温度 等）に該当する部分 → 『測定物理量』\n"
        "   例：タイトルが『2号機微粉炭機D ローラタイヤ摩耗量測定』なら\n"
        "       機器=『2号機微粉炭機D』、機器部品=『ローラタイヤ』、測定物理量=『摩耗量』\n"
        "\n"
        "2) 測定値キーの作り方：\n"
        "   - 列ヘッダーが入れ子の場合: 外側→内側の順で「・」区切り、最後に行ラベルを付ける\n"
        "     例: 列[タイヤ①→A] 行[①] → キー「タイヤ①・A・①」\n"
        "   - 表内に複数の物理量（摩耗量・振動値・温度）があっても全て1つの測定値にまとめる\n"
        "\n"
        "   列に「基準値」「判定」「備考」「単位」があればそれは測定値ではない：\n"
        "   - 基準値列の値は「基準値」フィールドに入れる\n"
        "   - 判定・備考・単位は無視する（測定値に含めない）\n"
        "\n"
        "3) 測定物理量：\n"
        "   - 表に1種類の物理量しかなければその名称（例：『摩耗量』）\n"
        "   - 複数の物理量が混在していれば代表的なもの、またはnull\n"
        "\n"
        "4) 基準値は 基準値 に格納する。キーは物理量名や測定値と同じラベルを使う。\n"
        "\n"
        "5) 未知/空欄/スラッシュ/×/- などは null を格納。\n"
        "\n"
        "6) 表にある数値は漏れなく全て取得する。\n"
        "\n"
        "7) 記載のない情報を推測で入れない。ただし 測定物理量 は PDFに明記がない場合、\n"
        "   表の内容から推測してよい。\n"
        "\n"
        "8) ハルシネーションは厳禁。\n"
        "9) JSONのみ。余計な文章は一切禁止。\n"
        "10) 日付はYYYY-MM-DD形式。\n"
    )

    try:
        # メッセージ組み立て: system → [few-shot例題] → 本番画像
        messages = [{"role": "system", "content": system_prompt}]

        # few-shot例題があれば挿入（user: 例題画像 → assistant: 正解JSON）
        fewshot = load_fewshot_example()
        if fewshot:
            messages.append({
                "role": "user",
                "content": [
                    {"type": "text", "text": user_prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{fewshot['image_base64']}",
                            "detail": "auto"
                        },
                    },
                ],
            })
            messages.append({
                "role": "assistant",
                "content": fewshot["json_text"],
            })

        # 本番: 実際に構造化したい画像
        messages.append({
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
        })

        # response_format は使用しない（Claude/Bedrockで空JSONが返る問題のため）
        # JSON出力はシステムプロンプトで強制する
        response = client.chat.completions.create(
            model=settings.litellm_model,
            messages=messages,
            max_tokens=4000,
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
    4. 結果を pages コレクションに保存
    5. 未処理ドキュメント（page_number: null）を削除

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
                # 正規化パイプライン適用（表記ゆれの統一）
                normalized_data = await run_pipeline(record_data, db)

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
                    "table_title": normalized_data.get("点検タイトル"),
                    "image_path": image_path,
                    # データ（正規化済みの構造化データ）
                    "data": normalized_data,
                    # 元データ（正規化前。問題発生時のロールバック用）
                    "data_raw": record_data,
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

    # 5. 測定値キー突合スキャン（自動実行）
    # 新しいデータが追加されたことでキー不一致が発生している可能性があるため、
    # 自動でスキャンを実行する。結果は key_mappings に pending として保存され、
    # 人間がUIで確認・承認するまでデータは変更されない。
    if records_processed > 0:
        try:
            from services.reconciliation import run_reconciliation_scan
            scan_result = await run_reconciliation_scan(db, tenant)
            logger.info(
                f"自動突合スキャン完了: {scan_result.get('groups_found', 0)}グループ, "
                f"{scan_result.get('mappings_created', 0)}件の候補検出"
            )
        except Exception as e:
            logger.warning(f"自動突合スキャンでエラー（処理は継続）: {e}")

    return {
        "success": True,
        "file_id": file_id,
        "filename": filename,
        "total_pages": total_pages,
        "records_processed": records_processed,
        "pages_with_errors": pages_with_errors
    }
