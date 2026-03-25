#!/bin/bash
# =============================================================================
# tests/insert_test_data.sh - カーブフィット予測テスト用ダミーデータ投入
# =============================================================================
#
# 【使い方】
#   docker compose up -d mongo   ← MongoDBを起動
#   bash tests/insert_test_data.sh        ← データ投入
#   bash tests/insert_test_data.sh --delete  ← データ削除
#
# 【テストデータ】
#   テスト機器A: 加速劣化 + 改修1回（2023年）→ 指数カーブ
#   テスト機器B: 直線劣化 + 改修1回（2021年）→ 線形カーブ
#   テスト機器C: 改修なし、じわじわ悪化 → 線形カーブ
#   テスト機器D: 改修2回（2017年, 2022年）→ 指数カーブ
#   テスト機器E: 減速劣化（最初ガッと→落ち着く）→ 対数カーブ
#
# =============================================================================

set -e

CONTAINER="pdf-mongo"

if [ "$1" = "--delete" ]; then
  docker exec "$CONTAINER" mongosh pdf_system --quiet --eval '
    var result = db.pages.deleteMany({"_test_data": true});
    print("削除完了: " + result.deletedCount + "件");
  '
  exit 0
fi

docker exec "$CONTAINER" mongosh pdf_system --quiet --eval '
// 既存テストデータを削除（重複防止）
db.pages.deleteMany({"_test_data": true});

// --- パターンA: 改修1回 + 加速劣化 ---
var patternA = [
  {year: "2018-04-15", values: {"摩耗量・テストA・①": 0.05, "摩耗量・テストA・②": 0.04}},
  {year: "2019-04-15", values: {"摩耗量・テストA・①": 0.09, "摩耗量・テストA・②": 0.07}},
  {year: "2020-04-15", values: {"摩耗量・テストA・①": 0.16, "摩耗量・テストA・②": 0.13}},
  {year: "2021-04-15", values: {"摩耗量・テストA・①": 0.28, "摩耗量・テストA・②": 0.22}},
  {year: "2022-04-15", values: {"摩耗量・テストA・①": 0.45, "摩耗量・テストA・②": 0.38}},
  {year: "2023-04-15", values: {"摩耗量・テストA・①": 0.06, "摩耗量・テストA・②": 0.05}},
  {year: "2024-04-15", values: {"摩耗量・テストA・①": 0.12, "摩耗量・テストA・②": 0.10}},
  {year: "2025-04-15", values: {"摩耗量・テストA・①": 0.22, "摩耗量・テストA・②": 0.18}},
];
var docsA = patternA.map(function(row, i) {
  return {tenant:"default", page_number:i+1, data:{"機器":"テスト機器A","機器部品":"テスト部品A","点検項目":"摩耗量測定","点検年月日":row.year,"計測箇所":"テスト計測箇所A","測定値":row.values,"基準値":{"摩耗量":0.50}}, image_path:"", _test_data:true};
});

// --- パターンB: 改修1回 + 直線劣化 ---
var patternB = [
  {year: "2016-04-15", values: {"振動値・テストB・上": 0.10}},
  {year: "2017-04-15", values: {"振動値・テストB・上": 0.15}},
  {year: "2018-04-15", values: {"振動値・テストB・上": 0.20}},
  {year: "2019-04-15", values: {"振動値・テストB・上": 0.25}},
  {year: "2020-04-15", values: {"振動値・テストB・上": 0.30}},
  {year: "2021-04-15", values: {"振動値・テストB・上": 0.08}},
  {year: "2022-04-15", values: {"振動値・テストB・上": 0.13}},
  {year: "2023-04-15", values: {"振動値・テストB・上": 0.18}},
  {year: "2024-04-15", values: {"振動値・テストB・上": 0.23}},
  {year: "2025-04-15", values: {"振動値・テストB・上": 0.28}},
];
var docsB = patternB.map(function(row, i) {
  return {tenant:"default", page_number:i+1, data:{"機器":"テスト機器B","機器部品":"テスト部品B","点検項目":"振動値測定","点検年月日":row.year,"計測箇所":"テスト計測箇所B","測定値":row.values,"基準値":{"振動値":0.50}}, image_path:"", _test_data:true};
});

// --- パターンC: 改修なし ---
var patternC = [
  {year: "2018-04-15", values: {"摩耗量・テストC・①": 0.10}},
  {year: "2019-04-15", values: {"摩耗量・テストC・①": 0.14}},
  {year: "2020-04-15", values: {"摩耗量・テストC・①": 0.19}},
  {year: "2021-04-15", values: {"摩耗量・テストC・①": 0.22}},
  {year: "2022-04-15", values: {"摩耗量・テストC・①": 0.27}},
  {year: "2023-04-15", values: {"摩耗量・テストC・①": 0.30}},
  {year: "2024-04-15", values: {"摩耗量・テストC・①": 0.34}},
  {year: "2025-04-15", values: {"摩耗量・テストC・①": 0.38}},
];
var docsC = patternC.map(function(row, i) {
  return {tenant:"default", page_number:i+1, data:{"機器":"テスト機器C","機器部品":"テスト部品C","点検項目":"摩耗量測定","点検年月日":row.year,"計測箇所":"テスト計測箇所C","測定値":row.values,"基準値":{"摩耗量":0.50}}, image_path:"", _test_data:true};
});

// --- パターンD: 改修2回（2017年, 2022年） ---
var patternD = [
  {year: "2015-04-15", values: {"振動値・テストD・上": 0.08}},
  {year: "2016-04-15", values: {"振動値・テストD・上": 0.16}},
  {year: "2017-04-15", values: {"振動値・テストD・上": 0.03}},
  {year: "2018-04-15", values: {"振動値・テストD・上": 0.10}},
  {year: "2019-04-15", values: {"振動値・テストD・上": 0.19}},
  {year: "2020-04-15", values: {"振動値・テストD・上": 0.28}},
  {year: "2021-04-15", values: {"振動値・テストD・上": 0.36}},
  {year: "2022-04-15", values: {"振動値・テストD・上": 0.05}},
  {year: "2023-04-15", values: {"振動値・テストD・上": 0.13}},
  {year: "2024-04-15", values: {"振動値・テストD・上": 0.22}},
  {year: "2025-04-15", values: {"振動値・テストD・上": 0.30}},
];
var docsD = patternD.map(function(row, i) {
  return {tenant:"default", page_number:i+1, data:{"機器":"テスト機器D","機器部品":"テスト部品D","点検項目":"振動値測定","点検年月日":row.year,"計測箇所":"テスト計測箇所D","測定値":row.values,"基準値":{"振動値":0.50}}, image_path:"", _test_data:true};
});

// --- パターンE: 減速劣化（対数的） ---
var patternE = [
  {year: "2018-04-15", values: {"腐食量・テストE・①": 0.05}},
  {year: "2019-04-15", values: {"腐食量・テストE・①": 0.18}},
  {year: "2020-04-15", values: {"腐食量・テストE・①": 0.26}},
  {year: "2021-04-15", values: {"腐食量・テストE・①": 0.31}},
  {year: "2022-04-15", values: {"腐食量・テストE・①": 0.34}},
  {year: "2023-04-15", values: {"腐食量・テストE・①": 0.36}},
  {year: "2024-04-15", values: {"腐食量・テストE・①": 0.37}},
  {year: "2025-04-15", values: {"腐食量・テストE・①": 0.38}},
];
var docsE = patternE.map(function(row, i) {
  return {tenant:"default", page_number:i+1, data:{"機器":"テスト機器E","機器部品":"テスト部品E","点検項目":"腐食量測定","点検年月日":row.year,"計測箇所":"テスト計測箇所E","測定値":row.values,"基準値":{"腐食量":0.50}}, image_path:"", _test_data:true};
});

var all = docsA.concat(docsB).concat(docsC).concat(docsD).concat(docsE);
db.pages.insertMany(all);
var count = db.pages_default.countDocuments({"_test_data": true});
print("投入完了: " + all.length + "件（ビュー: " + count + "件）");
print("  テスト機器A: 加速劣化+改修1回 (" + docsA.length + "件)");
print("  テスト機器B: 直線劣化+改修1回 (" + docsB.length + "件)");
print("  テスト機器C: 改修なし (" + docsC.length + "件)");
print("  テスト機器D: 改修2回 (" + docsD.length + "件)");
print("  テスト機器E: 減速劣化 (" + docsE.length + "件)");
'
