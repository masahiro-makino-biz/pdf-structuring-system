# AWS Bedrock Claude マルチモーダルツール セットアップガイド

## 📋 概要
このツールは Watson Orchestrate から AWS Bedrock の Claude モデルを使用して、テキストと画像を含むマルチモーダルなリクエストを送信できます。

## 🔧 セットアップ手順

### ステップ1: コネクションの作成

```bash
# 1. コネクションを追加
orchestrate connections add -a bedrock_claude_credentials

# 2. コネクションを設定（Key-Value方式、チーム共有）
orchestrate connections configure -a bedrock_claude_credentials --env draft -k key_value -t team

# 3. AWS認証情報を設定
# ※ YOUR_AWS_TOKEN_HERE を実際のAWSトークンに置き換えてください
orchestrate connections set-credentials -a bedrock_claude_credentials --env draft -e "aws_bearer_token=YOUR_AWS_TOKEN_HERE"
```

### ステップ2: コネクションの確認

```bash
# コネクション一覧を表示
orchestrate connections list

# 特定のコネクションの詳細を確認
orchestrate connections get -a bedrock_claude_credentials
```

### ステップ3: ツールのインポート

```bash
# Pythonツールとして requirements.txt と一緒にインポート
orchestrate tools import -k python -f multimodal_bedrock_claude.py -r requirements.txt -a bedrock_claude_credentials
```

## 📝 設定内容の説明

### コネクション設定
- **コネクション名**: `bedrock_claude_credentials`
- **認証方式**: Key-Value
- **スコープ**: Team（チーム全体で共有）
- **環境**: draft（開発環境）

### AWS Bedrock 設定（コード内の固定値）
- **リージョン**: `ap-northeast-1`（東京リージョン）
- **モデルID**: `jp.anthropic.claude-sonnet-4-6`（Claude Sonnet 4.6 日本版）

### 必要な認証情報
コネクションに設定するキー：
- `aws_bearer_token`: AWS Bedrock へのアクセストークン

## 🚀 使用方法

Watson Orchestrate のチャット画面で以下のようにツールを呼び出します：

```
この画像に何が写っていますか？
画像URL: https://example.com/image.jpg
```

または、ツールを直接指定：

```
multimodal_request(
    user_text="この画像を分析してください",
    image_urls=["https://example.com/image1.jpg", "https://example.com/image2.jpg"],
    system_prompt="あなたは画像分析の専門家です。"
)
```

## 📦 ファイル構成

```
.
├── multimodal_bedrock_claude.py  # メインのツールコード
├── requirements.txt               # Python依存パッケージ
├── SETUP_GUIDE.md                # このファイル
└── README.md                     # 使用方法の詳細
```

## ⚠️ 注意事項

1. **AWS認証情報の管理**
   - トークンは安全に管理してください
   - 定期的にローテーションすることを推奨します

2. **画像URL**
   - 公開アクセス可能なHTTPS URLを使用してください
   - プライベートな画像は事前に公開URLに変換する必要があります

3. **コスト**
   - AWS Bedrock の使用には料金が発生します
   - 使用量を定期的に確認してください

4. **リトライ機能**
   - ネットワークエラー時は最大3回自動リトライします
   - コンテンツポリシー違反の場合はリトライしません

## 🔍 トラブルシューティング

### コネクションエラー
```bash
# コネクションが正しく設定されているか確認
orchestrate connections get -a bedrock_claude_credentials
```

### 認証エラー
- AWS トークンが正しいか確認
- トークンの有効期限を確認
- IAM権限でBedrock へのアクセスが許可されているか確認

### インポートエラー
```bash
# ツールが正しくインポートされているか確認
orchestrate tools list
```

## 📚 参考リンク

- [Watson Orchestrate Developer Portal](https://developer.watson-orchestrate.ibm.com/)
- [AWS Bedrock Documentation](https://docs.aws.amazon.com/bedrock/)
- [LangChain AWS Documentation](https://python.langchain.com/docs/integrations/platforms/aws)