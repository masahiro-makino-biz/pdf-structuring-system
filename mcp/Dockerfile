# =============================================================================
# mcp/Dockerfile - MCPツールサーバーのコンテナ設定
# =============================================================================
# 【フェーズ1ではスケルトンのみ】
# 後のフェーズでPDF処理、画像加工、OpenAI連携を実装
# =============================================================================

FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8001

# FastMCPサーバーを起動
# mcp.run() がSSEトランスポートでHTTPサーバーを起動
CMD ["python", "server.py"]
