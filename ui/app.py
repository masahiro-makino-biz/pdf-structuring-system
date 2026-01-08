# =============================================================================
# ui/app.py - Streamlit メイン画面
# =============================================================================

import streamlit as st
import requests
import os

# =============================================================================
# 設定
# =============================================================================
API_URL = os.getenv("API_URL", "http://localhost:8000")

# =============================================================================
# ページ設定
# =============================================================================
st.set_page_config(
    page_title="PDF構造化システム",
    page_icon="📄",
    layout="wide",
)

# =============================================================================
# サイドバー - ページ選択
# =============================================================================
st.sidebar.title("📄 PDF構造化システム")
page = st.sidebar.radio(
    "ページ選択",
    ["💬 User - チャット", "🔧 Admin - PDF管理"],
    label_visibility="collapsed",
)


# =============================================================================
# Admin画面 - PDF管理
# =============================================================================
def admin_page():
    """管理者用ページ：PDFアップロード → 自動構造化 → 一覧表示"""
    st.title("🔧 PDF構造化システム")

    # -------------------------------------------------------------------------
    # PDFアップロード & テナントID
    # -------------------------------------------------------------------------
    col1, col2 = st.columns([3, 1])

    with col1:
        uploaded_file = st.file_uploader(
            "PDFファイルをアップロード",
            type=["pdf"],
            help="アップロード後、自動的にAI解析が実行されます",
        )

    with col2:
        tenant_id = st.text_input(
            "テナントID",
            value="default",
            help="データを分けるための識別子",
        )

    # -------------------------------------------------------------------------
    # アップロード → 自動処理
    # -------------------------------------------------------------------------
    if uploaded_file:
        # アップロード
        with st.status("処理中...", expanded=True) as status:
            st.write("📤 ファイルをアップロード中...")

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
                    st.write(f"✅ アップロード完了: {result['filename']}")

                    # 自動でAI処理を実行
                    st.write("🤖 AI解析中...（数分かかる場合があります）")

                    process_response = requests.post(
                        f"{API_URL}/admin/process/{file_id}",
                        params={"tenant": tenant_id},
                        timeout=300,
                    )

                    if process_response.status_code == 200:
                        process_result = process_response.json()
                        if process_result.get("success"):
                            st.write(f"✅ 解析完了: {process_result.get('pages_processed')}ページ処理")
                            status.update(label="処理完了！", state="complete")
                        else:
                            st.write(f"⚠️ 解析エラー: {process_result.get('error')}")
                            status.update(label="エラーあり", state="error")
                    else:
                        st.write("❌ AI処理に失敗しました")
                        status.update(label="処理失敗", state="error")

                else:
                    error_detail = response.json().get("detail", "不明なエラー")
                    st.write(f"❌ アップロード失敗: {error_detail}")
                    status.update(label="アップロード失敗", state="error")

            except requests.exceptions.Timeout:
                st.write("❌ タイムアウト")
                status.update(label="タイムアウト", state="error")
            except requests.exceptions.RequestException as e:
                st.write(f"❌ 通信エラー: {e}")
                status.update(label="通信エラー", state="error")

    # -------------------------------------------------------------------------
    # 構造化データ一覧
    # -------------------------------------------------------------------------
    st.divider()
    st.header("📊 構造化データ一覧")

    try:
        response = requests.get(
            f"{API_URL}/admin/files",
            params={"tenant": tenant_id},
            timeout=10,
        )

        if response.status_code == 200:
            files = response.json()
            processed_files = [f for f in files if f.get("processed", False)]

            if processed_files:
                for f in processed_files:
                    with st.expander(f"📄 {f['filename']}", expanded=False):
                        # 構造化データを取得して表示
                        try:
                            struct_response = requests.get(
                                f"{API_URL}/admin/structured/{f['file_id']}",
                                params={"tenant": tenant_id},
                                timeout=10,
                            )

                            if struct_response.status_code == 200:
                                data = struct_response.json()

                                st.caption(f"処理日時: {data.get('processed_at', 'N/A')}")

                                pages = data.get("pages", [])
                                for page in pages:
                                    page_num = page.get("page_number", "?")

                                    if "error" in page:
                                        st.error(f"ページ {page_num}: {page['error']}")
                                    else:
                                        page_data = page.get("data", {})

                                        st.markdown(f"**ページ {page_num}**")

                                        title = page_data.get("title")
                                        if title:
                                            st.write(f"タイトル: {title}")

                                        summary = page_data.get("summary")
                                        if summary:
                                            st.write(f"要約: {summary}")

                                        key_points = page_data.get("key_points", [])
                                        if key_points:
                                            st.write("重要ポイント:")
                                            for point in key_points:
                                                st.write(f"  • {point}")

                                        st.divider()

                                # JSON表示オプション
                                if st.checkbox(f"JSON表示", key=f"json_{f['file_id']}"):
                                    st.json(data)

                                # 削除ボタン
                                st.divider()
                                if st.button(
                                    "🗑️ このファイルを削除",
                                    key=f"delete_{f['file_id']}",
                                    type="secondary"
                                ):
                                    try:
                                        delete_response = requests.delete(
                                            f"{API_URL}/admin/files/{f['file_id']}",
                                            params={"tenant": tenant_id},
                                            timeout=10,
                                        )
                                        if delete_response.status_code == 200:
                                            st.success(f"✅ {f['filename']} を削除しました")
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
                st.info("📭 まだ構造化されたデータはありません。PDFをアップロードしてください。")

        else:
            st.error(f"❌ データ取得に失敗しました (status: {response.status_code})")

    except requests.exceptions.RequestException as e:
        st.error(f"❌ API通信エラー: {e}")


# =============================================================================
# User画面 - チャット
# =============================================================================
def user_page():
    """ユーザー用ページ：構造化データに対してチャットで質問"""
    st.title("💬 ドキュメントチャット")

    # テナントID設定
    tenant_id = st.sidebar.text_input(
        "テナントID",
        value="default",
        help="検索対象のテナント",
        key="chat_tenant"
    )

    st.caption("PDFから抽出した構造化データについて質問できます")

    if "messages" not in st.session_state:
        st.session_state.messages = []

    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    if prompt := st.chat_input("質問を入力してください（例：〇〇について教えて）"):
        st.session_state.messages.append({"role": "user", "content": prompt})

        with st.chat_message("user"):
            st.markdown(prompt)

        with st.chat_message("assistant"):
            with st.spinner("考え中..."):
                try:
                    response = requests.post(
                        f"{API_URL}/chat",
                        json={"message": prompt, "tenant": tenant_id},
                        timeout=60,
                    )

                    if response.status_code == 200:
                        result = response.json()
                        answer = result.get("response", "回答を取得できませんでした")

                        # 検索が行われたかどうかを表示
                        if result.get("search_performed"):
                            st.caption("🔍 ドキュメント検索を実行しました")

                        st.markdown(answer)
                    else:
                        answer = f"エラーが発生しました (status: {response.status_code})"
                        st.error(answer)

                except requests.exceptions.Timeout:
                    answer = "タイムアウトしました。もう一度お試しください。"
                    st.error(answer)
                except requests.exceptions.RequestException as e:
                    answer = f"通信エラー: {e}"
                    st.error(answer)

        st.session_state.messages.append({"role": "assistant", "content": answer})


# =============================================================================
# ページのルーティング
# =============================================================================
if "Admin" in page:
    admin_page()
else:
    user_page()
