# WxO ツール登録手順

## 前提

- ADKは既にインストール済み（`~/.local/venvs/wxo`）
- 毎回新しいターミナルを開いたら最初に activate が必要

---

## 🚨 現在やるべきこと（優先順位順）

### アクション1: 露出したAPIキーの削除＆再作成

先ほどチャットに貼り付けたAPIキー（実体文字列）はログに残るため、ローテーション必須。

1. https://cloud.ibm.com/iam/apikeys を開く
2. 露出したキーの右端「…」メニュー → **Delete**
3. **Create +** で新規作成
4. 作成直後の画面で **Copy** か **Download** ボタンを押す（再表示不可）
5. 新しいAPIキー文字列（`AbcDE...` のような長い文字列）をクリップボードへ

### アクション2: 新キーを curl で検証

```bash
curl -X POST "https://iam.cloud.ibm.com/identity/token" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=urn:ibm:params:oauth:grant-type:apikey" \
  --data-urlencode "apikey=新しいAPIキーをここに"
```

→ `access_token` が返ればOK

### アクション3: venv activate

```bash
source ~/.local/venvs/wxo/bin/activate
```

### アクション4: env を登録し直す

```bash
orchestrate env remove -n dev
```

```bash
orchestrate env add -n dev -u https://api.jp-tok.watson-orchestrate.cloud.ibm.com/instances/98497123-b353-459c-844f-801907c545b1 -t ibm_iam -a
```

→ `Please enter WXO API key:` で **新しいAPIキー** を入力

### アクション5: 動作確認

```bash
orchestrate connections list
```

→ エラーなく一覧が出ればステップ2へ進む

---

## 【公式ドキュメント準拠フロー】

公式: https://developer.watson-orchestrate.ibm.com/getting_started/installing.md

### API キー取得の正しい手順（SaaS版 IBM Cloud）

1. ブラウザで watsonx Orchestrate インスタンスにログイン
2. 右上の**ユーザーアイコン → Settings**
3. **API details** タブを開く
4. そこに表示されている **Service instance URL** をコピー
5. **Generate API key** ボタンをクリック
   → IBM Cloud IAM ページにリダイレクト
6. IAMページで **Create** → 新規APIキー生成
7. 生成されたキーをコピー（画面を閉じると再表示不可）

**重要**: IAMキー管理ページ（https://cloud.ibm.com/iam/apikeys）から直接作ったキーは、WxOに紐付かないことがある。必ずWxO画面の「Generate API key」ボタン経由で作る。

---

## ステップ0: venv をactivate

```bash
source ~/.local/venvs/wxo/bin/activate
```

---

## ステップ0.5: APIキーの有効性を事前検証（推奨）

env add する前に、取得したAPIキーが本当に有効か確認：

```bash
curl -X POST "https://iam.cloud.ibm.com/identity/token" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=urn:ibm:params:oauth:grant-type:apikey" \
  --data-urlencode "apikey=YOUR_API_KEY_HERE"
```

| 結果 | 意味 | 次のアクション |
|---|---|---|
| `{"access_token": "..."}` | キー有効 | ステップ1へ進む |
| `{"errorMessage":"Provided API key could not be found"}` | キー無効 or 別アカウント | WxO画面からキー再生成 |
| `{"errorMessage":"..."}` 他 | 別エラー | エラー文確認 |

---

## ステップ1: 環境(env)を ibm_iam で登録

IBM Cloud (jp-tok) の場合は `--type ibm_iam`

### 1-1. 既存の dev を削除

```bash
orchestrate env remove -n dev
```

### 1-2. ibm_iam タイプで追加＆アクティベート（1行でコピペ！）

```bash
orchestrate env add -n dev -u https://api.jp-tok.watson-orchestrate.cloud.ibm.com/instances/98497123-b353-459c-844f-801907c545b1 -t ibm_iam -a
```

→ `Please enter WXO API key:` と聞かれたら取得したAPIキーを入力

### 1-3. 動作確認

```bash
orchestrate connections list
```

→ 一覧（空でも可）が出ればOK。

---

## ステップ2: AWS Bedrock 用のコネクション作成

### 2-1. コネクション追加（箱を作る）

```bash
orchestrate connections add -a bedrock_claude_credentials
```

### 2-2. Key-Value方式・チーム共有で設定

```bash
orchestrate connections configure -a bedrock_claude_credentials --env draft -k key_value -t team
```

### 2-3. AWS Bearer Token を登録

```bash
# YOUR_AWS_TOKEN_HERE を実際のAWSトークンに置換！
orchestrate connections set-credentials -a bedrock_claude_credentials --env draft -e "aws_bearer_token=YOUR_AWS_TOKEN_HERE"
```

### 2-4. 確認

```bash
orchestrate connections get -a bedrock_claude_credentials
```

---

## ステップ3: ツールをインポート

### 3-0. ローカル検証用に依存パッケージを事前インストール（重要）

ADKはインポート時にPythonファイルをローカルで`import`して検証する。`-r requirements.txt` はWxOサーバー側の指定なので、**ローカルvenvにも同じ依存が必要**:

```bash
pip install langchain-aws langchain-core boto3
```

### 3-1. ツールインポート

```bash
cd /Users/masahiro/Business/test-cusor/wxo-tool

orchestrate tools import -k python -f multimodal_bedrock_claude.py -r requirements.txt -a bedrock_claude_credentials
```

### 確認

```bash
orchestrate tools list
```

→ `multimodal_request` がリストに出ればOK

---

## ステップ実行の順番まとめ

```
[0] venv activate
 ↓
[0.5] curl でAPIキー検証（推奨）
 ↓
[1] env設定（ibm_iam）→ APIキー入力
 ↓
[2] connection作成 → AWS Bearer Token登録
 ↓
[3] tools import → 完了
```

---

## 認証タイプ早見表

| 認証タイプ | 対応環境 | URL例 |
|---|---|---|
| `ibm_iam` | IBM Cloud SaaS | `*.cloud.ibm.com` |
| `mcsp_v2` | AWS (新) | `*.watson-orchestrate.ibm.com` |
| `mcsp_v1` | AWS (旧) | 同上 |
| `mcsp` | AWS (自動fallback) | 同上 |
| `cpd` | オンプレミス | Cloud Pak for Data環境 |

**重要**: 今回のインスタンス `api.jp-tok.watson-orchestrate.cloud.ibm.com` は **IBM Cloud SaaS** なので `ibm_iam` 一択。

**トークン有効期限**: リモート環境の認証トークンは **2時間** で失効する。その場合は `orchestrate env activate dev` を再実行。

---

## トラブルシューティング

### `command not found: orchestrate`

venv をactivateし忘れ：

```bash
source ~/.local/venvs/wxo/bin/activate
```

### `Provided API key could not be found`

1. **ステップ0.5 の curl で検証** — キー自体が有効か確認
2. キー無効なら **WxO画面の Generate API key** で作り直し
3. 複数IBM Cloudアカウントがある場合、WxOがあるアカウントか確認
   - https://cloud.ibm.com/resources で WxOインスタンスが見えるアカウントかチェック

### インスタンスURLが合っているか

現在のURL:
`https://api.jp-tok.watson-orchestrate.cloud.ibm.com/instances/98497123-b353-459c-844f-801907c545b1`

WxO画面の **Settings → API details → Service instance URL** と一致することを確認。

### コネクションエラー

- `--env draft` で作ったか確認
- チーム共有権限があるか確認

---

# 【補助】WxO ADK MCPサーバーのClaude Codeへの登録

ADKコマンドをClaude Codeから直接呼べるようにする。**認証問題の解決にはならない**が、
ADK認証が通ればClaude Codeが直接ADK操作を代行できる。

## 前提

- `uv` / `uvx` インストール済み
- ADK認証が事前に通っていること

## ステップ1: MCPサーバー設定をClaude Codeに追加

プロジェクトルートの `.mcp.json` を使用:

```json
{
  "mcpServers": {
    "wxo-mcp": {
      "command": "uvx",
      "args": [
        "--with",
        "ibm-watsonx-orchestrate==1.13.0",
        "ibm-watsonx-orchestrate-mcp-server"
      ],
      "env": {
        "WXO_MCP_WORKING_DIRECTORY": "/Users/masahiro/Business/test-cusor/wxo-tool"
      }
    }
  }
}
```

## ステップ2: Claude Code再起動 → `/mcp` で確認

```bash
cd /Users/masahiro/Business/test-cusor
claude
```

起動後、`/mcp` で `wxo-mcp` が表示されれば接続OK。

## 注意点

- **ADK認証は事前必須**: `orchestrate env activate dev` が成功してないと、MCP経由でもツール一覧が取得できない
- **ADK 2.8 と 1.13 の差分**: 既存venvの2.8と、MCPサーバーが使う1.13は別環境で並存する（干渉しない）
- **プロジェクトスコープ**: `.mcp.json` をコミットするとチームで共有可能。個人利用なら `.gitignore` 推奨（本プロジェクトではignore済み）
