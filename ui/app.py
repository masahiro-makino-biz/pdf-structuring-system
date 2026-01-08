# =============================================================================
# ui/app.py - Streamlit メイン画面
# =============================================================================
# 【このファイルの役割】
# ユーザーが操作するWebの画面を定義
# Admin画面（PDF管理）とUser画面（チャット）の2つのページを持つ
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
    ["🔧 Admin - PDF管理", "💬 User - チャット"],
    label_visibility="collapsed",
)


# =============================================================================
# Admin画面 - PDF管理
# =============================================================================
def admin_page():
    """管理者用ページ：PDFのアップロード、ファイル一覧"""
    st.title("🔧 Admin - PDF管理")

    # -------------------------------------------------------------------------
    # PDFアップロードセクション
    # -------------------------------------------------------------------------
    st.header("📤 PDFアップロード")

    uploaded_file = st.file_uploader(
        "PDFファイルを選択",
        type=["pdf"],
        help="アップロードするPDFファイルを選んでください",
    )

    col1, col2 = st.columns([2, 1])

    with col1:
        tenant_id = st.text_input(
            "テナントID",
            value="default",
            help="データを分けるための識別子。通常はそのまま「default」でOK",
        )

    with col2:
        upload_button = st.button(
            "アップロード実行",
            type="primary",
            disabled=(uploaded_file is None),
        )

    # -------------------------------------------------------------------------
    # アップロード処理
    # -------------------------------------------------------------------------
    if upload_button and uploaded_file:
        with st.spinner("アップロード中..."):
            try:
                # 【requests.post でファイルを送信】
                # files パラメータにファイルを指定
                # params パラメータにクエリパラメータを指定
                response = requests.post(
                    f"{API_URL}/admin/files",
                    files={"file": (uploaded_file.name, uploaded_file.getvalue(), "application/pdf")},
                    params={"tenant": tenant_id},
                    timeout=30,
                )

                if response.status_code == 200:
                    result = response.json()
                    st.success(f"✅ アップロード成功！")

                    # 【st.json】JSON形式でデータを表示
                    # デバッグやデータ確認に便利
                    st.json(result)
                else:
                    # 【エラーレスポンスの表示】
                    error_detail = response.json().get("detail", "不明なエラー")
                    st.error(f"❌ アップロード失敗: {error_detail}")

            except requests.exceptions.RequestException as e:
                # 【通信エラー】
                st.error(f"❌ API通信エラー: {e}")

    # -------------------------------------------------------------------------
    # アップロード済みファイル一覧
    # -------------------------------------------------------------------------
    st.header("📋 アップロード済みファイル一覧")

    # 【更新ボタン】
    # ファイル一覧を再取得するためのボタン
    if st.button("🔄 一覧を更新"):
        # 【st.rerun()】ページを再実行
        # Streamlitは画面更新のたびにスクリプト全体が再実行される
        # rerun()を呼ぶと明示的に再実行できる
        st.rerun()

    try:
        # 【ファイル一覧を取得】
        response = requests.get(
            f"{API_URL}/admin/files",
            params={"tenant": tenant_id},
            timeout=10,
        )

        if response.status_code == 200:
            files = response.json()

            if files:
                # 【st.dataframe】テーブル形式でデータを表示
                # Pandasのデータフレームやリストを渡せる
                st.dataframe(
                    [
                        {
                            "ファイルID": f["file_id"][:8] + "...",  # 長いので省略
                            "ファイル名": f["filename"],
                            "サイズ": f"{f['size'] / 1024:.1f} KB",
                            "アップロード日時": f["uploaded_at"],
                        }
                        for f in files
                    ],
                    use_container_width=True,
                )

                # -------------------------------------------------------------------------
                # ファイル詳細表示
                # -------------------------------------------------------------------------
                st.subheader("📄 ファイル詳細")

                # 【st.selectbox】ドロップダウン選択
                selected_file = st.selectbox(
                    "ファイルを選択",
                    options=files,
                    format_func=lambda f: f["filename"],  # 表示形式を指定
                )

                if selected_file:
                    col1, col2 = st.columns(2)
                    with col1:
                        st.write("**ファイルID:**", selected_file["file_id"])
                        st.write("**ファイル名:**", selected_file["filename"])
                    with col2:
                        st.write("**パス:**", selected_file["path"])
                        st.write("**サイズ:**", f"{selected_file['size'] / 1024:.1f} KB")

            else:
                st.info("📭 まだファイルがアップロードされていません")

        else:
            st.error(f"❌ ファイル一覧の取得に失敗しました (status: {response.status_code})")

    except requests.exceptions.RequestException as e:
        st.error(f"❌ API通信エラー: {e}")

    # -------------------------------------------------------------------------
    # 処理ジョブ一覧（フェーズ3で実装）
    # -------------------------------------------------------------------------
    st.header("⏳ 処理ジョブ一覧")
    st.info("🚧 フェーズ3で実装予定：PDFの画像化・構造化ジョブを表示します")


# =============================================================================
# User画面 - チャット
# =============================================================================
def user_page():
    """ユーザー用ページ：構造化データに対してチャットで質問"""
    st.title("💬 User - チャット")

    # セッションステートでチャット履歴を管理
    if "messages" not in st.session_state:
        st.session_state.messages = []

    # チャット履歴の表示
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    # チャット入力
    if prompt := st.chat_input("質問を入力してください"):
        st.session_state.messages.append({"role": "user", "content": prompt})

        with st.chat_message("user"):
            st.markdown(prompt)

        with st.chat_message("assistant"):
            with st.spinner("考え中..."):
                # フェーズ4で実装：APIにリクエストを送信
                response = f"🚧 フェーズ4で実装予定です。\n\n質問「{prompt}」を受け取りました。"
                st.markdown(response)

        st.session_state.messages.append({"role": "assistant", "content": response})


# =============================================================================
# ページのルーティング
# =============================================================================
if "Admin" in page:
    admin_page()
else:
    user_page()


# =============================================================================
# APIヘルスチェック（サイドバー下部）
# =============================================================================
st.sidebar.divider()
st.sidebar.caption("API接続状態")

try:
    response = requests.get(f"{API_URL}/health", timeout=2)
    if response.status_code == 200:
        data = response.json()
        mongo_status = data.get("mongodb", "unknown")
        if mongo_status == "connected":
            st.sidebar.success("✅ API接続OK")
            st.sidebar.caption("MongoDB: 接続済み")
        else:
            st.sidebar.warning("⚠️ API接続OK (MongoDB未接続)")
    else:
        st.sidebar.error(f"❌ API異常 (status: {response.status_code})")
except requests.exceptions.RequestException:
    st.sidebar.error("❌ API接続失敗")
