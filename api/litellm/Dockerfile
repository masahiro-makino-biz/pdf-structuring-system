# =============================================================================
# litellm/Dockerfile - LiteLLMプロキシサーバー
# =============================================================================
#
# 【このファイルの役割】
# LiteLLMプロキシサーバーのDockerイメージを定義。
# OpenAI互換APIを提供し、様々なLLMプロバイダーへのリクエストを中継する。
#
# 【なぜ公式イメージを使わないか】
# - 公式イメージもあるが、設定ファイルのマウントが簡単
# - 軽量なベースイメージを使用
#
# =============================================================================

FROM python:3.11-slim

WORKDIR /app

# LiteLLMとプロキシ機能をインストール
# 【なぜ[proxy]オプションか】
# - LiteLLMはSDKとプロキシサーバーの2つの使い方がある
# - [proxy]でプロキシサーバー機能が有効になる
RUN pip install --no-cache-dir 'litellm[proxy]'

# 設定ファイルをコピー
COPY config.yaml /app/config.yaml

# ヘルスチェック用
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:4000/health || exit 1

# LiteLLMプロキシを起動
# --config: 設定ファイルのパス
# --port: 待ち受けポート
# --host: 全てのインターフェースでリッスン
CMD ["litellm", "--config", "/app/config.yaml", "--port", "4000", "--host", "0.0.0.0"]
