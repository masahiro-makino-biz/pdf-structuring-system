# =============================================================================
# ui/Dockerfile - Streamlit UIのコンテナ設定
# =============================================================================
# 【構造はapi/Dockerfileとほぼ同じ】
# 違いは最後の起動コマンド（uvicornではなくstreamlit）
# =============================================================================

FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Streamlitのデフォルトポート
EXPOSE 8501

# ---------------------------------------------------------------------------
# Streamlit起動コマンド
# ---------------------------------------------------------------------------
# 【オプションの意味】
#   - run app.py → app.pyを実行
#   - --server.address 0.0.0.0 → 外部からのアクセスを許可
#   - --server.port 8501 → 8501番ポートで起動
#   - --server.headless true → ブラウザを自動で開かない（Docker内なので不要）
CMD ["streamlit", "run", "app.py", "--server.address", "0.0.0.0", "--server.port", "8501", "--server.headless", "true"]
