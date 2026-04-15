// =============================================================================
// MongoDB 初期化スクリプト
// =============================================================================
// docker-entrypoint-initdb.d/ に配置され、MongoDBの初回起動時に自動実行される。
//
// 【注意】
// - このスクリプトはDBデータが空の初回起動時のみ実行される
// - 既にデータがある場合は実行されない（mongo_data ボリュームを削除すれば再実行）
// =============================================================================

db = db.getSiblingDB("pdf_system");

// テナント分離用ビュー
// pages_default: tenant="default" の有効レコードだけを公開するビュー
// MongoDB MCP Server がこのビューを通じてデータにアクセスすることで、
// 他テナントのデータが見えないようにする
db.createView("pages_default", "pages", [
  {
    $match: {
      tenant: "default",
      page_number: { $ne: null },
      error: { $exists: false }
    }
  }
]);

print("Created view: pages_default");

// 正規化辞書コレクション用インデックス
// field（対象フィールド名）+ canonical（正規名）の組み合わせで一意制約
// AI自動マッチングが表記ゆれを検出した際、辞書に自動登録する
db.normalization_dict.createIndex(
  { "field": 1, "canonical": 1 },
  { unique: true }
);

print("Created index: normalization_dict (field + canonical)");
