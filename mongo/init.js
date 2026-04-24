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
// 他テナントのデータが見えないようにする。
// data.点検タイトル はノイズになりやすいためビュー側で除外し、AIから見えなくする。
db.createView("pages_default", "pages", [
  {
    $match: {
      tenant: "default",
      page_number: { $ne: null },
      error: { $exists: false }
    }
  },
  {
    $project: {
      "data.点検タイトル": 0
    }
  }
]);

print("Created view: pages_default (excludes data.点検タイトル)");

// 正規化辞書コレクション用インデックス
// field（対象フィールド名）+ canonical（正規名）の組み合わせで一意制約
// AI自動マッチングが表記ゆれを検出した際、辞書に自動登録する
db.normalization_dict.createIndex(
  { "field": 1, "canonical": 1 },
  { unique: true }
);

print("Created index: normalization_dict (field + canonical)");

// 測定値キー突合コレクション用インデックス
// 同一グループ内の同一 variant_key に対して重複マッピングを防止
db.key_mappings.createIndex(
  { "group.機器": 1, "group.機器部品": 1, "group.測定物理量": 1, "variant_key": 1 },
  { unique: true }
);

// ステータスでのフィルタ用（pending/approved/rejected）
db.key_mappings.createIndex({ "status": 1 });

print("Created indexes: key_mappings (group + variant_key, status)");
