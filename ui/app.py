# =============================================================================
# ui/app.py - Streamlit メイン画面
# =============================================================================

import streamlit as st
import requests
import os
import uuid
import re

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
                                                try:
                                                    st.image(image_path)
                                                except Exception:
                                                    st.warning("画像が見つかりません")
                                            if "error" in record:
                                                st.error(record["error"])

                                # JSON表示オプション
                                if st.checkbox(f"JSON表示", key=f"json_{f['file_id']}"):
                                    st.json(data)

                                # 削除ボタン
                                st.divider()
                                is_dummy = f["filename"].startswith("【ダミー】")
                                delete_label = "このダミーデータを削除" if is_dummy else "このファイルを削除"
                                if st.button(
                                    delete_label,
                                    key=f"delete_{f['file_id']}",
                                    type="secondary"
                                ):
                                    try:
                                        if is_dummy:
                                            # ダミーデータは dummy_group_id（= file_id）で一括削除
                                            delete_response = requests.delete(
                                                f"{API_URL}/admin/dummy/{f['file_id']}",
                                                params={"tenant": tenant_id},
                                                timeout=30,
                                            )
                                        else:
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
    # ダミーデータ生成
    # -------------------------------------------------------------------------
    st.divider()
    st.header("ダミーデータ生成")
    st.caption("既存データをテンプレートに、経年劣化をシミュレーションしたダミーデータを生成します")

    try:
        # テンプレート選択用に処理済みファイル一覧を取得
        files_resp = requests.get(
            f"{API_URL}/admin/files",
            params={"tenant": tenant_id},
            timeout=30,
        )
        if files_resp.status_code == 200:
            all_files = files_resp.json()
            # 処理済みかつダミーでないファイルだけ選択肢にする
            template_files = [
                f for f in all_files
                if f.get("processed") and not f["filename"].startswith("【ダミー】")
            ]

            if template_files:
                # テンプレート選択
                template_options = {
                    f["filename"]: f["file_id"]
                    for f in template_files
                }
                selected_name = st.selectbox(
                    "テンプレート選択",
                    options=list(template_options.keys()),
                    help="ダミーデータの元になるファイルを選択",
                )
                source_file_id = template_options[selected_name]

                # 年度範囲
                col1, col2 = st.columns(2)
                with col1:
                    start_year = st.number_input("開始年", min_value=2000, max_value=2050, value=2015)
                with col2:
                    end_year = st.number_input("終了年", min_value=2000, max_value=2050, value=2025)

                # 修繕年の選択
                year_range = list(range(int(start_year), int(end_year) + 1))
                repair_years = st.multiselect(
                    "修繕年（修繕があった年を選択）",
                    options=year_range,
                    help="修繕年には測定値が改善（初期値方向に回復）します",
                )

                # 生成ボタン
                if st.button("ダミーデータを生成", type="primary"):
                    with st.spinner("ダミーデータ生成中..."):
                        try:
                            gen_resp = requests.post(
                                f"{API_URL}/admin/dummy/generate",
                                json={
                                    "source_file_id": source_file_id,
                                    "tenant": tenant_id,
                                    "start_year": int(start_year),
                                    "end_year": int(end_year),
                                    "repair_years": repair_years,
                                },
                                timeout=60,
                            )
                            if gen_resp.status_code == 200:
                                gen_result = gen_resp.json()
                                if gen_result.get("success"):
                                    st.success(
                                        f"生成完了: {gen_result['total_records']}レコード "
                                        f"（{gen_result['year_range']}）"
                                    )
                                    st.rerun()
                                else:
                                    st.error(f"生成失敗: {gen_result.get('error')}")
                            else:
                                detail = gen_resp.json().get("detail", "不明なエラー")
                                st.error(f"生成失敗: {detail}")
                        except requests.exceptions.RequestException as e:
                            st.error(f"通信エラー: {e}")
            else:
                st.info("テンプレートとなる処理済みファイルがありません。先にPDFをアップロード・処理してください。")

    except requests.exceptions.RequestException as e:
        st.error(f"API通信エラー: {e}")


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
