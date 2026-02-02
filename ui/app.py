# =============================================================================
# ui/app.py - Streamlit メイン画面
# =============================================================================

import streamlit as st
import requests
import os
import uuid

# =============================================================================
# 設定
# =============================================================================
API_URL = os.getenv("API_URL", "http://localhost:8000")

# =============================================================================
# ページ設定
# =============================================================================
st.set_page_config(
    page_title="PDF構造化システム",
    layout="wide",
)

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
    st.header("構造化データ一覧")

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
                                        f"P{r.get('page_number', '?')}-{r.get('table_index', '?')}: {(r.get('data', {}).get('点検項目') or '記録')[:15]}"
                                        for r in records
                                    ]
                                    tabs = st.tabs(tab_labels)
                                    for tab, record in zip(tabs, records):
                                        with tab:
                                            record_data = record.get("data", {})
                                            st.caption(f"機器: {record_data.get('機器', 'N/A')} / 点検項目: {record_data.get('点検項目', 'N/A')}")
                                            image_path = record.get("image_path")
                                            if image_path:
                                                st.image(image_path)
                                            if "error" in record:
                                                st.error(record["error"])

                                # JSON表示オプション
                                if st.checkbox(f"JSON表示", key=f"json_{f['file_id']}"):
                                    st.json(data)

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


# =============================================================================
# User画面 - チャット
# =============================================================================


def show_reference_pages(ref_pages: list, key_prefix: str = ""):
    """
    参照元ページを折りたたみ式で表示するヘルパー関数

    【なぜヘルパー関数にしたか】
    - 新規回答時と履歴表示時で同じ表示ロジックを使うため
    - コードの重複を避けて保守しやすくする

    Args:
        ref_pages: 参照ページ情報のリスト
        key_prefix: Streamlitのkey重複を避けるためのプレフィックス
    """
    if not ref_pages:
        return

    # expanderで折りたたみ式にする（タップで展開）
    with st.expander(f"📄 参照元ページ（{len(ref_pages)}件）", expanded=False):
        # タブで切り替え表示
        tab_labels = [f"{p['filename']} P{p['page_number']}" for p in ref_pages]
        tabs = st.tabs(tab_labels)
        for tab, page_info in zip(tabs, ref_pages):
            with tab:
                st.image(page_info["image_path"], caption=page_info.get("title", ""))


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
            st.markdown(message["content"])
            # アシスタントの回答に参照ページがあれば表示
            if message["role"] == "assistant" and message.get("ref_pages"):
                show_reference_pages(message["ref_pages"], key_prefix=f"history_{i}_")

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
                        timeout=60,
                    )

                    if response.status_code == 200:
                        result = response.json()
                        answer = result.get("response", "回答を取得できませんでした")
                        ref_pages = []  # 参照ページを保存用に初期化

                        # 検索が行われたかどうかを表示
                        if result.get("search_performed"):
                            st.caption("ドキュメント検索を実行しました")

                        st.markdown(answer)

                        # 検索結果に画像がある場合、収集して表示
                        if result.get("search_results") and result["search_results"].get("results"):
                            for doc in result["search_results"]["results"]:
                                for record in doc.get("matched_records", []):
                                    if record.get("image_path"):
                                        ref_pages.append({
                                            "filename": doc["filename"],
                                            "page_number": record["page_number"],
                                            "table_index": record.get("table_index"),
                                            "image_path": record["image_path"],
                                            "title": record.get("inspection_item", "")
                                        })

                            # 折りたたみ式で画像表示
                            show_reference_pages(ref_pages, key_prefix="new_")
                    else:
                        answer = f"エラーが発生しました (status: {response.status_code})"
                        ref_pages = []
                        st.error(answer)

                except requests.exceptions.Timeout:
                    answer = "タイムアウトしました。もう一度お試しください。"
                    ref_pages = []
                    st.error(answer)
                except requests.exceptions.RequestException as e:
                    answer = f"通信エラー: {e}"
                    ref_pages = []
                    st.error(answer)

        # 回答と参照ページを履歴に保存
        st.session_state.messages.append({
            "role": "assistant",
            "content": answer,
            "ref_pages": ref_pages  # 参照ページも保存（次の会話でも表示される）
        })


# =============================================================================
# ページのルーティング
# =============================================================================
if "Admin" in page:
    admin_page()
else:
    user_page()
