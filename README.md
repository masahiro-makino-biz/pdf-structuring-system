# PDF構造化 & チャットシステム

PDFをアップロードして構造化データに変換し、チャットで検索・分析できるシステム。

## 必要なもの

- **Docker Desktop**（必須）
- **Git**（コードの取得に必要）

## 環境構築

### 1. Docker Desktopをインストール

1. https://www.docker.com/products/docker-desktop/ にアクセス
2. 「Download for Mac」をクリック（Windowsの場合はWindows版）
3. ダウンロードした `.dmg` を開いてインストール
4. アプリケーションからDockerを起動
5. メニューバーにクジラアイコンが表示されれば準備完了

### 2. リポジトリをクローン

```bash
git clone https://github.com/masahiro-makino-biz/pdf-structuring-system.git
cd pdf-structuring-system
```

### 3. 起動

```bash
docker-compose up --build -d
```

初回は数分かかります（イメージのダウンロードとビルド）。

### 4. 動作確認

以下のURLにアクセス：

| サービス | URL | 説明 |
|----------|-----|------|
| **UI（メイン画面）** | http://localhost:8501 | Admin/User画面 |
| **API ドキュメント** | http://localhost:8000/docs | FastAPI自動生成ドキュメント |
| **MCP サーバー** | http://localhost:8001 | ツールサーバー（内部利用） |

## よく使うコマンド

### 起動・停止

```bash
# 起動
docker-compose up -d

# 停止
docker-compose down

# 再ビルドして起動（コード変更後）
docker-compose up --build -d
```

### ログ確認

```bash
# 全サービスのログ
docker-compose logs

# 特定サービスのログ（リアルタイム）
docker-compose logs -f api
docker-compose logs -f ui
docker-compose logs -f mcp
```

### 状態確認

```bash
# コンテナの状態
docker-compose ps
```

## プロジェクト構成

```
pdf-structuring-system/
├── docker-compose.yml   # 全コンテナの設定
├── api/                 # FastAPI（バックエンドAPI）
│   ├── Dockerfile
│   ├── requirements.txt
│   └── main.py
├── ui/                  # Streamlit（フロントエンドUI）
│   ├── Dockerfile
│   ├── requirements.txt
│   └── app.py
├── mcp/                 # FastMCP（ツールサーバー）
│   ├── Dockerfile
│   ├── requirements.txt
│   └── server.py
└── data/                # ファイル保存領域（Git管理外）
```

## トラブルシューティング

### Docker Desktopが起動していない

```
Cannot connect to the Docker daemon
```

→ Docker Desktopアプリを起動してください

### ポートが使用中

```
Bind for 0.0.0.0:8501 failed: port is already allocated
```

→ 該当ポートを使っているアプリを終了するか、`docker-compose.yml`でポート番号を変更

### コンテナが再起動を繰り返す

```bash
# ログを確認
docker-compose logs mcp
```

→ エラー内容を確認して対処

## 開発フェーズ

- [x] **Phase 1**: Docker環境構築、UI基盤
- [ ] **Phase 2**: API実装（PDFアップロード、ファイル配信）
- [ ] **Phase 3**: MCPツール実装（PDF→画像→構造化）
- [ ] **Phase 4**: チャット機能（検索、グラフ、予測）
