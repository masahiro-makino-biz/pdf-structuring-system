# DB設計書：pages コレクション

## 1. 概要

| 項目 | 内容 |
|------|------|
| DB種別 | MongoDB |
| コレクション名 | `pages` |
| 役割 | PDFファイルの管理と抽出データの保存 |

このコレクションは**2つの状態**を持つドキュメントを格納します：
- **未処理状態**：アップロード直後（`page_number: null`）
- **処理済み状態**：GPT-4oで抽出後（`page_number: 1, 2, ...`）

---

## 2. スキーマ定義

### 2-1. 未処理ドキュメント（アップロード時）

| フィールド | 型 | 必須 | 説明 |
|-----------|-----|:----:|------|
| file_id | string | ○ | ファイルの一意識別子（UUID） |
| filename | string | ○ | 元のファイル名 |
| path | string | ○ | サーバー上の保存パス |
| size | number | ○ | ファイルサイズ（バイト） |
| tenant | string | ○ | テナントID（デフォルト: "default"） |
| content_type | string | ○ | MIMEタイプ（例: "application/pdf"） |
| uploaded_at | datetime | ○ | アップロード日時 |
| processed | boolean | ○ | 処理済みフラグ（`false`） |
| page_number | null | ○ | **null = 未処理を示す** |

### 2-2. 処理済みドキュメント（抽出後）

| フィールド | 型 | 必須 | 説明 |
|-----------|-----|:----:|------|
| file_id | string | ○ | ファイルの一意識別子 |
| filename | string | ○ | 元のファイル名 |
| path | string | ○ | PDFの保存パス |
| tenant | string | ○ | テナントID |
| uploaded_at | datetime | ○ | アップロード日時 |
| processed | boolean | ○ | 処理済みフラグ（`true`） |
| processed_at | datetime | ○ | 処理完了日時 |
| page_number | number | ○ | ページ番号（1始まり） |
| table_index | number | ○ | ページ内の表番号（1始まり） |
| table_title | string | - | 表のタイトル（点検項目から取得） |
| image_path | string | ○ | 変換画像のパス |
| data | object | ○ | **GPT-4oで抽出した構造化データ** |
| error | string | - | エラー時のみ設定 |

### 2-3. data オブジェクトの構造

| フィールド | 型 | 説明 |
|-----------|-----|------|
| 機器 | string \| null | 対象機器名 |
| 機器部品 | string \| null | 部品名 |
| 計測箇所 | string \| null | 計測した場所 |
| 点検項目 | string \| null | 点検項目名 |
| 点検年月日 | string \| null | 日付（YYYY-MM-DD形式） |
| 測定者 | string \| null | 担当者名 |
| 計測器具 | string \| null | 使用した器具 |
| 単位 | string \| null | 測定単位（mm, ℃など） |
| 測定値 | object | キー: パス形式（例: "タイヤ①・a・上"） |
| 基準値 | object | キー: パス形式 |

---

## 3. データのライフサイクル

```
[アップロード]
     │
     ▼
┌─────────────────────────────┐
│ 未処理ドキュメント作成      │
│ page_number: null           │
│ processed: false            │
└─────────────────────────────┘
     │
     │  POST /admin/process/{file_id}
     ▼
┌─────────────────────────────┐
│ PDF → 画像変換              │
│ GPT-4oで各ページ処理        │
└─────────────────────────────┘
     │
     ▼
┌─────────────────────────────┐
│ 処理済みドキュメント作成    │
│ （表ごとに1ドキュメント）   │
│ page_number: 1, 2, ...      │
│ processed: true             │
└─────────────────────────────┘
     │
     ▼
┌─────────────────────────────┐
│ 未処理ドキュメント削除      │
│ （page_number: null を削除）│
└─────────────────────────────┘
```

---

## 4. 主要なクエリパターン

| 用途 | クエリ |
|------|--------|
| ファイル一覧取得 | `db.pages.aggregate([{$group: {_id: "$file_id", ...}}])` |
| 未処理ファイル検索 | `{file_id: X, page_number: null}` |
| 処理済みレコード取得 | `{file_id: X, page_number: {$ne: null}}` |
| ファイル削除 | `{file_id: X, tenant: Y}` |

---

## 5. 推奨インデックス

```javascript
// 複合インデックス（ファイル検索用）
db.pages.createIndex({ "file_id": 1, "tenant": 1 })

// ページ番号でのソート用
db.pages.createIndex({ "file_id": 1, "page_number": 1, "table_index": 1 })

// テナントごとのファイル一覧用
db.pages.createIndex({ "tenant": 1, "uploaded_at": -1 })
```

---

## 6. 設計のポイント

### なぜ1コレクションで管理するか
- `files`と`pages`を分けると、JOIN相当の処理が必要になる
- MongoDBはJOINが苦手なため、1コレクションで完結させる設計
- `page_number: null` で未処理/処理済みを区別

### なぜ表ごとに1ドキュメントか
- 1ページに複数の表がある場合がある
- 検索・フィルタリングが表単位で行いやすい
