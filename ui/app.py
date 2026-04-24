# =============================================================================
# ui/app.py - Streamlit メイン画面
# =============================================================================

import streamlit as st
import requests
import os
import uuid
import re
import hmac

# =============================================================================
# 設定
# =============================================================================
API_URL = os.getenv("API_URL", "http://localhost:8000")
BASIC_USER = os.getenv("BASIC_AUTH_USER", "admin")
BASIC_PASS = os.getenv("BASIC_AUTH_PASS", "password")

# =============================================================================
# ページ設定（set_page_configは最初に1回だけ呼ぶ必要がある）
# =============================================================================
st.set_page_config(
    page_title="PDF構造化システム",
    layout="wide",
)


# =============================================================================
# Basic認証
# =============================================================================
def check_auth():
    """認証チェック。未認証ならログインフォームを表示してアプリを停止する"""
    if st.session_state.get("authenticated"):
        return

    st.title("ログイン")
    user = st.text_input("ユーザー名")
    password = st.text_input("パスワード", type="password")
    if st.button("ログイン"):
        if hmac.compare_digest(user, BASIC_USER) and hmac.compare_digest(password, BASIC_PASS):
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("ユーザー名またはパスワードが正しくありません")
    st.stop()


check_auth()

# =============================================================================
# サイドバー - ページ選択
# =============================================================================
st.sidebar.title("PDF構造化システム")
page = st.sidebar.radio(
    "ページ選択",
    ["User - チャット", "Admin - PDF管理"],
    label_visibility="collapsed",
)


# =============================================================================
# Admin画面 - PDF管理
# =============================================================================
def admin_page():
    """管理者用ページ：PDFアップロード → 自動構造化 → 一覧表示"""
    st.title("PDF構造化システム")

    # -------------------------------------------------------------------------
    # サイドバー - テナントID設定
    # -------------------------------------------------------------------------
    tenant_id = st.sidebar.text_input(
        "テナントID",
        value="default",
        help="データを分けるための識別子",
        key="admin_tenant"
    )

    # -------------------------------------------------------------------------
    # session_state 初期化（処理済みファイルを追跡）
    # -------------------------------------------------------------------------
    if "processed_files" not in st.session_state:
        st.session_state.processed_files = set()

    # -------------------------------------------------------------------------
    # PDFアップロード
    # -------------------------------------------------------------------------
    uploaded_file = st.file_uploader(
        "PDFファイルをアップロード",
        type=["pdf"],
        help="アップロード後、自動的にAI解析が実行されます",
    )

    # -------------------------------------------------------------------------
    # アップロード → ボタンで処理開始
    # -------------------------------------------------------------------------
    if uploaded_file:
        # ファイルを一意に識別するキー（ファイル名 + サイズ）
        file_key = f"{uploaded_file.name}_{uploaded_file.size}"

        # まだ処理していないファイルの場合
        if file_key not in st.session_state.processed_files:
            st.info(f"選択中: {uploaded_file.name} ({uploaded_file.size:,} bytes)")

            if st.button("アップロードして処理開始", type="primary"):
                with st.status("処理中...", expanded=True) as status:
                    st.write("ファイルをアップロード中...")

                    try:
                        response = requests.post(
                            f"{API_URL}/admin/files",
                            files={"file": (uploaded_file.name, uploaded_file.getvalue(), "application/pdf")},
                            params={"tenant": tenant_id},
                            timeout=30,
                        )

                        if response.status_code == 200:
                            result = response.json()
                            file_id = result["file_id"]
                            st.write(f"アップロード完了: {result['filename']}")

                            # 自動でAI処理を実行
                            st.write("AI解析中...（数分かかる場合があります）")

                            process_response = requests.post(
                                f"{API_URL}/admin/process/{file_id}",
                                params={"tenant": tenant_id},
                                timeout=300,
                            )

                            if process_response.status_code == 200:
                                process_result = process_response.json()
                                if process_result.get("success"):
                                    st.write(f"解析完了: {process_result.get('records_processed')}レコードを抽出")
                                    status.update(label="処理完了", state="complete")
                                    # 処理成功を記録
                                    st.session_state.processed_files.add(file_key)
                                else:
                                    st.write(f"解析エラー: {process_result.get('error')}")
                                    status.update(label="エラーあり", state="error")
                            else:
                                st.write("AI処理に失敗しました")
                                status.update(label="処理失敗", state="error")

                        else:
                            error_detail = response.json().get("detail", "不明なエラー")
                            st.write(f"アップロード失敗: {error_detail}")
                            status.update(label="アップロード失敗", state="error")

                    except requests.exceptions.Timeout:
                        st.write("タイムアウト")
                        status.update(label="タイムアウト", state="error")
                    except requests.exceptions.RequestException as e:
                        st.write(f"通信エラー: {e}")
                        status.update(label="通信エラー", state="error")

        # 既に処理済みのファイルの場合
        else:
            st.success(f"{uploaded_file.name} は処理済みです")
            if st.button("再処理する"):
                st.session_state.processed_files.discard(file_key)
                st.rerun()

    # -------------------------------------------------------------------------
    # 構造化データ一覧
    # -------------------------------------------------------------------------
    st.divider()
    tab_data, tab_dict, tab_reconcile = st.tabs(["構造化データ一覧", "正規化辞書管理", "測定値キー照合"])

    with tab_data:

        try:
            response = requests.get(
                f"{API_URL}/admin/files",
                params={"tenant": tenant_id},
                timeout=30,
            )

            if response.status_code == 200:
                files = response.json()
                processed_files = [f for f in files if f.get("processed", False)]

                if processed_files:
                    for f in processed_files:
                        with st.expander(f"{f['filename']}", expanded=False):
                            # 構造化データを取得して表示
                            try:
                                struct_response = requests.get(
                                    f"{API_URL}/admin/structured/{f['file_id']}",
                                    params={"tenant": tenant_id},
                                    timeout=30,
                                )

                                if struct_response.status_code == 200:
                                    data = struct_response.json()

                                    st.caption(f"処理日時: {data.get('processed_at', 'N/A')} / レコード数: {data.get('total_records', 0)}")

                                    records = data.get("records", [])
                                    if records:
                                        # タブでレコードごとに切り替え表示
                                        tab_labels = [
                                            f"P{r.get('page_number', '?')}-{r.get('table_index', '?')}: {(r.get('data', {}).get('測定物理量') or '記録')[:15]}"
                                            for r in records
                                        ]
                                        tabs = st.tabs(tab_labels)
                                        for tab, record in zip(tabs, records):
                                            with tab:
                                                record_data = record.get("data", {})
                                                record_id = record.get("_id", "")
                                                st.caption(f"機器: {record_data.get('機器', 'N/A')} / 部品: {record_data.get('機器部品', 'N/A')} / 物理量: {record_data.get('測定物理量', 'N/A')}")
                                                image_path = record.get("image_path")
                                                if image_path:
                                                    try:
                                                        st.image(image_path)
                                                    except Exception:
                                                        st.warning("画像が見つかりません")
                                                if "error" in record:
                                                    st.error(record["error"])

                                                # 構造化データ編集
                                                if record_id:
                                                    import json as _json
                                                    edit_key = f"edit_{record_id}"
                                                    if st.checkbox("編集", key=f"chk_{record_id}"):
                                                        edited_json = st.text_area(
                                                            "JSON編集",
                                                            value=_json.dumps(record_data, ensure_ascii=False, indent=2),
                                                            height=300,
                                                            key=edit_key,
                                                        )
                                                        if st.button("保存", key=f"save_{record_id}", type="primary"):
                                                            try:
                                                                parsed = _json.loads(edited_json)
                                                                save_resp = requests.put(
                                                                    f"{API_URL}/admin/structured/{record_id}",
                                                                    json={"data": parsed},
                                                                    timeout=30,
                                                                )
                                                                if save_resp.status_code == 200:
                                                                    st.success("保存しました")
                                                                    st.rerun()
                                                                else:
                                                                    st.error(f"保存失敗: {save_resp.status_code}")
                                                            except _json.JSONDecodeError:
                                                                st.error("JSONの形式が不正です")
                                                            except requests.exceptions.RequestException as e:
                                                                st.error(f"通信エラー: {e}")

                                    # 削除ボタン
                                    st.divider()
                                    if st.button(
                                        "このファイルを削除",
                                        key=f"delete_{f['file_id']}",
                                        type="secondary"
                                    ):
                                        try:
                                            delete_response = requests.delete(
                                                f"{API_URL}/admin/files/{f['file_id']}",
                                                params={"tenant": tenant_id},
                                                timeout=30,
                                            )
                                            if delete_response.status_code == 200:
                                                st.success(f"{f['filename']} を削除しました")
                                                st.rerun()
                                            else:
                                                st.error("削除に失敗しました")
                                        except requests.exceptions.RequestException as e:
                                            st.error(f"通信エラー: {e}")

                                else:
                                    st.error("データ取得に失敗しました")

                            except requests.exceptions.RequestException as e:
                                st.error(f"通信エラー: {e}")

                else:
                    st.info("まだ構造化されたデータはありません。PDFをアップロードしてください。")

            else:
                st.error(f"データ取得に失敗しました (status: {response.status_code})")

        except requests.exceptions.RequestException as e:
            st.error(f"API通信エラー: {e}")

        # -------------------------------------------------------------------------
        # 正規化辞書管理
        # -------------------------------------------------------------------------
        # 【このセクションの役割】
        # normalization_dict を管理画面から直接編集できるようにする。
        # 辞書は PDF 処理時の表記ゆれ統一（canonical + variants）に使われる。
        #
        # 【表示構成】
        # 1. フィールド選択（点検タイトル/機器/機器部品/測定物理量）
        # 2. そのフィールドの辞書一覧（expander展開で variant 編集）
        # 3. 新規canonical登録フォーム

    with tab_dict:
        st.caption("PDF構造化時に使われる表記ゆれ辞書（canonical + variants）を編集します")

        NORMALIZATION_FIELDS = ["点検タイトル", "機器", "機器部品", "測定物理量"]

        selected_field = st.selectbox(
            "フィールド選択",
            options=NORMALIZATION_FIELDS,
            help="編集する正規化フィールドを選択",
            key="norm_dict_field",
        )

        # ---- 一覧取得 ----
        try:
            list_resp = requests.get(
                f"{API_URL}/admin/normalization-dict",
                params={"field": selected_field},
                timeout=30,
            )
        except requests.exceptions.RequestException as e:
            st.error(f"辞書取得エラー: {e}")
            list_resp = None

        if list_resp and list_resp.status_code == 200:
            entries = list_resp.json().get("entries", [])

            st.caption(f"[{selected_field}] 登録数: {len(entries)}件")

            if not entries:
                st.info("このフィールドの辞書はまだ空です。下のフォームから追加してください。")

            for entry in entries:
                entry_id = entry["id"]
                canonical = entry["canonical"]
                variants = entry["variants"]
                variant_badge = f"（variant {len(variants)}件）" if variants else ""

                with st.expander(f"{canonical} {variant_badge}", expanded=False):
                    # -- canonicalリネーム --
                    col_rename, col_btn = st.columns([4, 1])
                    with col_rename:
                        new_canonical = st.text_input(
                            "canonical（正規名）",
                            value=canonical,
                            key=f"rename_{entry_id}",
                        )
                    with col_btn:
                        st.write("")  # ボタン位置調整用の空行
                        if st.button("リネーム", key=f"rename_btn_{entry_id}"):
                            if new_canonical.strip() and new_canonical != canonical:
                                try:
                                    rename_resp = requests.put(
                                        f"{API_URL}/admin/normalization-dict/{entry_id}",
                                        json={"canonical": new_canonical.strip()},
                                        timeout=30,
                                    )
                                    if rename_resp.status_code == 200:
                                        st.success(f"{canonical} → {new_canonical} にリネームしました")
                                        st.rerun()
                                    else:
                                        detail = rename_resp.json().get("detail", "不明なエラー")
                                        st.error(f"リネーム失敗: {detail}")
                                except requests.exceptions.RequestException as e:
                                    st.error(f"通信エラー: {e}")

                    # -- variants 表示と削除 --
                    st.write("**variants（表記ゆれ）**")
                    if variants:
                        for i, v in enumerate(variants):
                            col_v, col_del = st.columns([5, 1])
                            with col_v:
                                st.code(v, language=None)
                            with col_del:
                                if st.button("削除", key=f"del_v_{entry_id}_{i}"):
                                    new_variants = [vv for vv in variants if vv != v]
                                    try:
                                        upd_resp = requests.put(
                                            f"{API_URL}/admin/normalization-dict/{entry_id}",
                                            json={"variants": new_variants},
                                            timeout=30,
                                        )
                                        if upd_resp.status_code == 200:
                                            st.success(f"variant削除: {v}")
                                            st.rerun()
                                        else:
                                            st.error("variant削除に失敗しました")
                                    except requests.exceptions.RequestException as e:
                                        st.error(f"通信エラー: {e}")
                    else:
                        st.caption("（未登録）")

                    # -- variant 追加 --
                    col_new, col_add = st.columns([4, 1])
                    with col_new:
                        new_variant = st.text_input(
                            "追加するvariant",
                            key=f"new_v_{entry_id}",
                            placeholder="例: No.2微粉炭機D",
                        )
                    with col_add:
                        st.write("")
                        if st.button("variant追加", key=f"add_v_btn_{entry_id}"):
                            if new_variant.strip():
                                merged = variants + [new_variant.strip()]
                                # 重複除去
                                merged = list(dict.fromkeys(merged))
                                try:
                                    upd_resp = requests.put(
                                        f"{API_URL}/admin/normalization-dict/{entry_id}",
                                        json={"variants": merged},
                                        timeout=30,
                                    )
                                    if upd_resp.status_code == 200:
                                        st.success(f"variant追加: {new_variant.strip()}")
                                        st.rerun()
                                    else:
                                        st.error("variant追加に失敗しました")
                                except requests.exceptions.RequestException as e:
                                    st.error(f"通信エラー: {e}")

                    # -- エントリ削除 --
                    st.divider()
                    if st.button(
                        "このエントリを削除",
                        key=f"del_entry_{entry_id}",
                        type="secondary",
                    ):
                        try:
                            del_resp = requests.delete(
                                f"{API_URL}/admin/normalization-dict/{entry_id}",
                                timeout=30,
                            )
                            if del_resp.status_code == 200:
                                st.success(f"{canonical} を削除しました")
                                st.rerun()
                            else:
                                st.error("削除に失敗しました")
                        except requests.exceptions.RequestException as e:
                            st.error(f"通信エラー: {e}")

            # ---- 新規canonical登録 ----
            st.divider()
            st.subheader("新規canonical登録")
            with st.form(key=f"new_canonical_form_{selected_field}"):
                new_canonical_name = st.text_input(
                    "canonical（正規名）",
                    placeholder=f"例: 2号機微粉炭機D（{selected_field}）",
                )
                new_variants_text = st.text_input(
                    "variants（カンマ区切りで複数可、省略可）",
                    placeholder="例: No.2微粉炭機D, #2微粉炭機D",
                )
                submitted = st.form_submit_button("追加", type="primary")
                if submitted:
                    if not new_canonical_name.strip():
                        st.error("canonicalは必須です")
                    else:
                        variants_list = [
                            v.strip() for v in new_variants_text.split(",") if v.strip()
                        ]
                        try:
                            create_resp = requests.post(
                                f"{API_URL}/admin/normalization-dict",
                                json={
                                    "field": selected_field,
                                    "canonical": new_canonical_name.strip(),
                                    "variants": variants_list,
                                },
                                timeout=30,
                            )
                            if create_resp.status_code == 200:
                                st.success(f"登録完了: [{selected_field}] {new_canonical_name}")
                                st.rerun()
                            else:
                                try:
                                    detail = create_resp.json().get("detail", "不明なエラー")
                                except Exception:
                                    detail = f"サーバーエラー (status: {create_resp.status_code})"
                                st.error(f"登録失敗: {detail}")
                        except requests.exceptions.RequestException as e:
                            st.error(f"通信エラー: {e}")
        elif list_resp is not None:
            st.error(f"辞書取得失敗 (status: {list_resp.status_code})")

        # -------------------------------------------------------------------------
        # 測定値キー照合
        # -------------------------------------------------------------------------

    with tab_reconcile:
        st.caption("同じ機器・部品・物理量のレコード間で異なる測定値キーを検出し、人間がレビューします。AI判定は別途実行可能。")

        # 検出スキャン（高速） + AI判定（重い）を分離したボタン
        col_scan1, col_scan2 = st.columns(2)

        with col_scan1:
            if st.button("① 検出スキャン（高速）", type="primary", key="reconciliation_scan"):
                with st.spinner("検出中..."):
                    try:
                        scan_resp = requests.post(
                            f"{API_URL}/admin/reconciliation/scan",
                            params={"tenant": tenant_id, "run_ai": False},
                            timeout=300,
                        )
                        if scan_resp.status_code == 200:
                            scan_result = scan_resp.json()
                            st.success(
                                f"検出完了: {scan_result.get('groups_found', 0)}グループで"
                                f"{scan_result.get('mappings_created', 0)}件の候補を記録"
                                f"（AI未判定）"
                            )
                            st.rerun()
                        else:
                            st.error(f"スキャン失敗 (status: {scan_resp.status_code})")
                    except requests.exceptions.RequestException as e:
                        st.error(f"通信エラー: {e}")

        with col_scan2:
            if st.button("② AI判定実行（未判定のみ）", key="reconciliation_ai_judge"):
                with st.spinner("AI画像比較を実行中..."):
                    try:
                        ai_resp = requests.post(
                            f"{API_URL}/admin/reconciliation/ai_judge",
                            params={"tenant": tenant_id},
                            timeout=1800,
                        )
                        if ai_resp.status_code == 200:
                            ai_result = ai_resp.json()
                            st.success(
                                f"AI判定完了: {ai_result.get('judged_records', 0)}レコード / "
                                f"{ai_result.get('updated_mappings', 0)}件のマッピング更新"
                            )
                            st.rerun()
                        else:
                            st.error(f"AI判定失敗 (status: {ai_resp.status_code})")
                    except requests.exceptions.RequestException as e:
                        st.error(f"通信エラー: {e}")

        # ステータスフィルタ（適用済みはデフォルトで除外）
        status_filter = st.selectbox(
            "ステータス",
            options=["pending_approved", "pending", "approved", "rejected", "applied", "all"],
            format_func=lambda x: {
                "pending_approved": "未レビュー+承認済み（デフォルト）",
                "pending": "未レビューのみ",
                "approved": "承認済みのみ",
                "rejected": "却下",
                "applied": "適用済み（履歴）",
                "all": "全て",
            }[x],
            key="reconciliation_status",
        )

        # レポート取得
        try:
            report_resp = requests.get(
                f"{API_URL}/admin/reconciliation/report",
                params={"status": status_filter},
                timeout=30,
            )
        except requests.exceptions.RequestException as e:
            st.error(f"レポート取得エラー: {e}")
            report_resp = None

        if report_resp and report_resp.status_code == 200:
            report = report_resp.json()
            mappings = report.get("mappings", [])

            st.caption(f"照合候補: {len(mappings)}件")

            if not mappings:
                st.info("照合候補がありません。スキャンを実行するか、フィルタを変更してください。")

            # ページ（variant_page_id）ごとにグルーピング
            from collections import defaultdict
            page_groups = defaultdict(list)
            for m in mappings:
                page_key = m.get("variant_page_id", m["id"])
                page_groups[page_key].append(m)

            for page_key, page_mappings in page_groups.items():
                first = page_mappings[0]
                group = first.get("group", {})
                group_label = f"{group.get('機器', '?')} / {group.get('機器部品', '?')} / {group.get('測定物理量', '?')}"
                key_count = len(page_mappings)
                statuses = set(m["status"] for m in page_mappings)
                if statuses == {"approved"}:
                    page_emoji = "✅"
                elif statuses == {"rejected"}:
                    page_emoji = "❌"
                elif "pending" in statuses:
                    page_emoji = "⏳"
                else:
                    page_emoji = "🔀"

                with st.expander(
                    f"{page_emoji} {group_label}（{key_count}キー）",
                    expanded=False,
                ):
                    # 画像とJSON を横並び表示
                    col_img1, col_img2 = st.columns(2)
                    with col_img1:
                        st.write("**少数派（変更元）**")
                        if first.get("variant_image_path"):
                            try:
                                st.image(first["variant_image_path"], use_container_width=True)
                            except Exception:
                                st.warning("画像を表示できません")
                        if first.get("variant_measurements"):
                            st.json(first["variant_measurements"])
                    with col_img2:
                        st.write("**多数派（変更先）**")
                        if first.get("canonical_image_path"):
                            try:
                                st.image(first["canonical_image_path"], use_container_width=True)
                            except Exception:
                                st.warning("画像を表示できません")
                        if first.get("canonical_measurements"):
                            st.json(first["canonical_measurements"])

                    # キーマッピング一覧
                    st.divider()
                    st.write("**キー対応表**")
                    for m in page_mappings:
                        m_id = m["id"]
                        status_emoji = {"pending": "⏳", "approved": "✅", "rejected": "❌"}.get(m["status"], "")
                        confidence = m.get("ai_confidence", 0)

                        col_map, col_actions = st.columns([3, 3])
                        with col_map:
                            st.write(f"{status_emoji} 「{m['variant_key']}」→「{m.get('canonical_key', '?')}」")

                        with col_actions:
                            if m["status"] == "pending":
                                c1, c2, c3 = st.columns(3)
                                with c1:
                                    if st.button("承認", key=f"approve_{m_id}", type="primary"):
                                        try:
                                            requests.put(
                                                f"{API_URL}/admin/reconciliation/{m_id}",
                                                json={"action": "approve"},
                                                timeout=30,
                                            )
                                            st.rerun()
                                        except requests.exceptions.RequestException as e:
                                            st.error(f"エラー: {e}")
                                with c2:
                                    if st.button("却下", key=f"reject_{m_id}"):
                                        try:
                                            requests.put(
                                                f"{API_URL}/admin/reconciliation/{m_id}",
                                                json={"action": "reject"},
                                                timeout=30,
                                            )
                                            st.rerun()
                                        except requests.exceptions.RequestException as e:
                                            st.error(f"エラー: {e}")
                                with c3:
                                    new_key = st.text_input("修正", key=f"mod_{m_id}", label_visibility="collapsed")
                                    if st.button("修正承認", key=f"modify_{m_id}"):
                                        if new_key.strip():
                                            try:
                                                requests.put(
                                                    f"{API_URL}/admin/reconciliation/{m_id}",
                                                    json={"action": "modify", "modified_key": new_key.strip()},
                                                    timeout=30,
                                                )
                                                st.rerun()
                                            except requests.exceptions.RequestException as e:
                                                st.error(f"エラー: {e}")


            # 承認済みマッピング一括適用ボタン
            approved_count = sum(1 for m in mappings if m["status"] == "approved")
            if approved_count > 0:
                st.divider()
                if st.button(f"承認済み {approved_count} 件をDBに適用", type="primary", key="apply_mappings"):
                    with st.spinner("適用中..."):
                        try:
                            apply_resp = requests.post(
                                f"{API_URL}/admin/reconciliation/apply",
                                params={"tenant": tenant_id},
                                timeout=120,
                            )
                            if apply_resp.status_code == 200:
                                apply_result = apply_resp.json()
                                st.success(f"適用完了: {apply_result.get('records_updated', 0)}件のレコードを更新")
                                st.rerun()
                            else:
                                st.error(f"適用失敗 (status: {apply_resp.status_code})")
                        except requests.exceptions.RequestException as e:
                            st.error(f"通信エラー: {e}")

        elif report_resp is not None:
            st.error(f"レポート取得失敗 (status: {report_resp.status_code})")


# =============================================================================
# User画面 - チャット
# =============================================================================


def extract_chart_paths(text: str) -> tuple[str, list[str]]:
    """
    テキストからグラフHTMLのパスを抽出する

    【なぜ必要か】
    グラフは/data/chartsにHTMLファイルとして保存される。
    AIの回答からパスを抽出してインタラクティブグラフとして表示する。
    """
    # /data/charts/xxx.html 形式のパスを検索
    # AIがMarkdownリンク [text](url) で返す場合があるので ] ( [ も除外する
    pattern = r'/data/charts/[^\s\)\(\]\[\"\']+\.html'

    paths = re.findall(pattern, text)
    cleaned_text = text

    for path in paths:
        cleaned_text = cleaned_text.replace(path, '')

    return cleaned_text, paths


def extract_reference_paths(text: str) -> tuple[str, list[str]]:
    """
    テキストから参照PDF画像のパスを抽出する

    【なぜ必要か】
    検索結果に含まれるPDFページ画像を表示するため。
    AIの回答からパスを抽出して画像を表示する。

    Args:
        text: AIの回答テキスト

    Returns:
        (パスを除いたテキスト, 画像パスのリスト)
    """
    # /data/default/images/xxx/page_001.png 形式のパスを検索
    pattern = r'/data/[^\s\)\"\']+/images/[^\s\)\"\']+\.png'

    paths = re.findall(pattern, text)
    cleaned_text = text

    # パスを含む行を削除（参照: /data/... の行）
    for path in paths:
        cleaned_text = re.sub(rf'参照[：:]?\s*{re.escape(path)}', '', cleaned_text)
        cleaned_text = cleaned_text.replace(path, '')

    return cleaned_text, paths


def show_reference_images(paths: list[str]):
    """
    参照PDF画像を折りたたみ式・タブ切り替えで表示する

    Args:
        paths: 画像ファイルパスのリスト
    """
    if not paths:
        return

    with st.expander(f"📄 参照元ページ（{len(paths)}件）", expanded=False):
        # タブでページを切り替え表示
        tab_labels = [f"ページ {i+1}" for i in range(len(paths))]
        tabs = st.tabs(tab_labels)
        for tab, path in zip(tabs, paths):
            with tab:
                try:
                    if os.path.exists(path):
                        st.image(path)
                    else:
                        st.warning(f"画像が見つかりません")
                except Exception as e:
                    st.error(f"表示エラー: {e}")


def show_chart_images(paths: list[str]):
    """
    PlotlyグラフのHTMLファイルをStreamlit内に埋め込み表示する

    【なぜ st.components.v1.html() か】
    - PlotlyのHTMLをページ内に埋め込んで、ホバー・ズーム等が動く
    - st.image()はPNG/JPEG専用なのでHTMLは表示できない
    - MCP側はHTMLファイルを作るだけ → フロント非依存

    Args:
        paths: HTMLファイルパスのリスト
    """
    import streamlit.components.v1 as components

    for path in paths:
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    html_content = f.read()
                components.html(html_content, height=550, scrolling=False)
            else:
                st.warning(f"グラフファイルが見つかりません: {path}")
        except Exception as e:
            st.error(f"グラフの表示に失敗しました: {e}")


def user_page():
    """ユーザー用ページ：構造化データに対してチャットで質問"""
    st.title("ドキュメントチャット")

    # テナントID設定
    tenant_id = st.sidebar.text_input(
        "テナントID",
        value="default",
        help="検索対象のテナント",
        key="chat_tenant"
    )

    # セッションID（会話履歴を識別するためのID）
    if "session_id" not in st.session_state:
        st.session_state.session_id = str(uuid.uuid4())

    # 会話リセットボタン
    if st.sidebar.button("会話をリセット"):
        try:
            requests.post(
                f"{API_URL}/chat/clear",
                params={"session_id": st.session_state.session_id},
                timeout=30,
            )
        except requests.exceptions.RequestException:
            pass
        st.session_state.session_id = str(uuid.uuid4())
        st.session_state.messages = []
        st.rerun()

    st.caption("PDFから抽出した構造化データについて質問できます")

    if "messages" not in st.session_state:
        st.session_state.messages = []

    # 履歴表示（検索結果の画像も含む）
    for i, message in enumerate(st.session_state.messages):
        with st.chat_message(message["role"]):
            content = message["content"]
            # アシスタントの回答は画像を抽出して表示
            if message["role"] == "assistant":
                cleaned_content, chart_paths = extract_chart_paths(content)
                cleaned_content, ref_paths = extract_reference_paths(cleaned_content)
                st.markdown(cleaned_content)
                if chart_paths:
                    show_chart_images(chart_paths)
                if ref_paths:
                    show_reference_images(ref_paths)
            else:
                st.markdown(content)

    if prompt := st.chat_input("質問を入力してください（例：〇〇について教えて）"):
        st.session_state.messages.append({"role": "user", "content": prompt})

        with st.chat_message("user"):
            st.markdown(prompt)

        with st.chat_message("assistant"):
            with st.spinner("考え中..."):
                try:
                    response = requests.post(
                        f"{API_URL}/chat",
                        json={
                            "message": prompt,
                            "tenant": tenant_id,
                            "session_id": st.session_state.session_id
                        },
                        timeout=180,  # グラフ生成で複数ツール呼び出しがあるため長めに設定
                    )

                    if response.status_code == 200:
                        result = response.json()
                        answer = result.get("response", "回答を取得できませんでした")

                        cleaned_answer, chart_paths = extract_chart_paths(answer)
                        cleaned_answer, ref_paths = extract_reference_paths(cleaned_answer)
                        st.markdown(cleaned_answer)

                        if chart_paths:
                            show_chart_images(chart_paths)
                        if ref_paths:
                            show_reference_images(ref_paths)
                    else:
                        answer = f"エラーが発生しました (status: {response.status_code})"
                        st.error(answer)

                except requests.exceptions.Timeout:
                    answer = "タイムアウトしました。もう一度お試しください。"
                    st.error(answer)
                except requests.exceptions.RequestException as e:
                    answer = f"通信エラー: {e}"
                    st.error(answer)

        st.session_state.messages.append({
            "role": "assistant",
            "content": answer,
        })


# =============================================================================
# ページのルーティング
# =============================================================================
if "Admin" in page:
    admin_page()
else:
    user_page()
