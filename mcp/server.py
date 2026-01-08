# =============================================================================
# mcp/server.py - FastMCP ツールサーバー（スケルトン）
# =============================================================================
# 【このファイルの役割】
# PDF処理、画像加工、AI呼び出しなどのツールを提供するMCPサーバー
# フェーズ1では動作確認用の最小限のツールのみ
#
# 【MCPとは】
# Model Context Protocol の略
# AIモデル（Claude等）に「ツール」を提供するための標準規格
# 例：「PDFを画像に変換して」→ pdf_to_page_images ツールが呼ばれる
#
# 【参考】https://github.com/jlowin/fastmcp
# =============================================================================

from fastmcp import FastMCP

# =============================================================================
# MCPサーバーの作成
# =============================================================================
# 【FastMCP()とは】
# MCPサーバーのインスタンスを作成
# FastAPIと似た書き方でツールを定義できる
#
# 【引数の意味】
#   - "pdf-tools": サーバーの名前（クライアントが識別に使う）
mcp = FastMCP("pdf-tools")


# =============================================================================
# ツールの定義（スケルトン）
# =============================================================================


@mcp.tool()
def hello(name: str = "World") -> str:
    """
    動作確認用のシンプルなツール

    【デコレータ @mcp.tool() とは】
    この関数をMCPツールとして公開する
    AIが「helloツールを使って」と言うと、この関数が呼ばれる

    【引数の型ヒント name: str】
    MCPはこの型情報を使って、AIに「nameは文字列です」と伝える
    AIが適切な形式でパラメータを渡せるようになる

    【戻り値の型ヒント -> str】
    この関数が文字列を返すことを宣言

    Args:
        name: 挨拶する相手の名前

    Returns:
        挨拶メッセージ
    """
    return f"Hello, {name}! MCP server is working."


@mcp.tool()
def get_server_status() -> dict:
    """
    サーバーの状態を返す

    【戻り値が dict の場合】
    MCPが自動でJSONに変換して返す

    Returns:
        サーバーの状態情報
    """
    return {
        "status": "running",
        "phase": 1,
        "available_tools": ["hello", "get_server_status"],
        "coming_soon": [
            "pdf_to_page_images",
            "image_preprocess",
            "detect_regions",
            "crop_regions",
            "extract_structured_from_images",
        ],
    }


# =============================================================================
# 【フェーズ3で実装予定のツール】
# =============================================================================
# @mcp.tool()
# def pdf_to_page_images(pdf_path: str, tenant: str, job_id: str, dpi: int = 200):
#     """PDFを1ページずつ画像に変換"""
#     pass
#
# @mcp.tool()
# def image_preprocess(image_path: str, ops: list, out_path: str):
#     """画像の前処理（コントラスト調整、ノイズ除去等）"""
#     pass
#
# @mcp.tool()
# def detect_regions(image_path: str, hint: str):
#     """画像内の領域（表、テキスト、図等）を検出"""
#     pass
#
# @mcp.tool()
# def crop_regions(image_path: str, regions: list, out_dir: str):
#     """検出した領域を切り出して保存"""
#     pass
#
# @mcp.tool()
# def extract_structured_from_images(image_paths: list, schema: dict, prompt_opts: dict):
#     """画像からOpenAI APIを使って構造化データを抽出"""
#     pass
# =============================================================================


# =============================================================================
# サーバー起動
# =============================================================================
if __name__ == "__main__":
    # 【mcp.run()】MCPサーバーを起動
    #
    # 【transport="sse"】Server-Sent Eventsを使用（HTTPベースの通信）
    #   - SSE: ブラウザやHTTPクライアントからアクセス可能
    #   - Webサービスとして動かす場合に使う
    #
    # 【transport="stdio"】標準入出力を使用
    #   - Claude Desktop等のローカルアプリ向け
    #   - 今回はコンテナ間通信なのでSSEを使用
    #
    # 【port=8001】待ち受けポート番号
    #
    # 【host="0.0.0.0"】
    #   - 0.0.0.0 = 全てのネットワークインターフェースで待ち受け
    #   - Dockerコンテナ外からアクセスするために必要
    mcp.run(transport="sse", host="0.0.0.0", port=8001)
