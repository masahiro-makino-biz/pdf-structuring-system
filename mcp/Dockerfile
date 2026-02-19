# =============================================================================
# mcp/Dockerfile - MCPツールサーバーのコンテナ設定
# =============================================================================
# 【このファイルの役割】
# MCPサーバーコンテナをビルドするための設定
# PDF処理に必要なシステムライブラリもインストール
# =============================================================================

FROM python:3.11-slim

WORKDIR /app

# =============================================================================
# システムライブラリのインストール
# =============================================================================
# 【poppler-utils とは】
# PDFを画像に変換するためのコマンドラインツール群
# pdf2imageライブラリが内部でpopplerを使用する
#
# 【なぜ apt-get が必要か】
# Pythonライブラリ（pip）だけでは動かないソフトウェアがある
# popplerはC/C++で書かれたシステムレベルのツールなので、
# OSのパッケージマネージャー（apt-get）でインストールが必要
#
# 【--no-install-recommends】
# 推奨パッケージをスキップして最小限のインストール
# コンテナサイズを小さく保つため
#
# 【rm -rf /var/lib/apt/lists/*】
# aptのキャッシュを削除してコンテナサイズを削減
# 【fonts-ipafont-gothic】日本語フォント
# Plotlyでグラフに日本語を表示するために必要
# 【fontconfig】fc-cacheコマンドを提供（フォントキャッシュ更新用）
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    poppler-utils \
    fonts-ipafont-gothic \
    fontconfig \
    && fc-cache -fv \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8001

# FastMCPサーバーを起動
# mcp.run() がSSEトランスポートでHTTPサーバーを起動
CMD ["python", "server.py"]
