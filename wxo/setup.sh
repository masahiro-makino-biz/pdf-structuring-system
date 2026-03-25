#!/bin/bash
# =============================================================================
# wxo/setup.sh - WxO ADK環境セットアップ & ツール登録
# =============================================================================
#
# 【使い方】
#   bash wxo/setup.sh install    ← venv作成 & ADKインストール
#   bash wxo/setup.sh login      ← WxO環境にログイン（初回のみ）
#   bash wxo/setup.sh deploy     ← echo_toolをWxOに登録
#   bash wxo/setup.sh list       ← 登録済みツール一覧
#
# 【前提条件】
#   - Python 3.11以上: brew install python@3.11
#   - WxOのURL・APIキー（ログイン時にプロンプトで入力）
#
# =============================================================================

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"

# venvを有効化するヘルパー
activate_venv() {
    if [ ! -d "$VENV_DIR" ]; then
        echo "venvが見つかりません。先に 'bash wxo/setup.sh install' を実行してください"
        exit 1
    fi
    source "$VENV_DIR/bin/activate"
}

case "${1:-help}" in
    install)
        echo "venv作成 & ADKインストール..."
        python3.11 -m venv "$VENV_DIR"
        source "$VENV_DIR/bin/activate"
        pip install ibm-watsonx-orchestrate
        echo "インストール完了"
        echo ""
        echo "次のステップ: bash wxo/setup.sh login"
        ;;

    login)
        activate_venv
        echo "WxO環境にログイン..."
        echo ""
        echo "以下の情報が必要です："
        echo "  - WxO環境のURL"
        echo "  - APIキー（activate時にプロンプトで入力）"
        echo ""
        read -p "環境名（例: dev）: " ENV_NAME
        read -p "WxO URL: " WXO_URL
        echo ""
        # env add: 環境を登録（-a で同時にactivate）
        # activate時にAPIキーの入力プロンプトが表示される
        orchestrate env add -n "$ENV_NAME" -u "$WXO_URL" -a
        echo ""
        echo "ログイン完了（環境: $ENV_NAME）"
        echo ""
        echo "次のステップ: bash wxo/setup.sh deploy"
        ;;

    deploy)
        activate_venv
        echo "echo_tool をWxOに登録..."
        orchestrate tools import --kind python --file "$SCRIPT_DIR/echo_tool.py"
        echo "登録完了"
        echo ""
        echo "WxOチャットで「helloをエコーして」等と試してみてください"
        ;;

    list)
        activate_venv
        echo "登録済みツール一覧:"
        orchestrate tools list
        ;;

    *)
        echo "WxO ADK セットアップスクリプト"
        echo ""
        echo "使い方:"
        echo "  bash wxo/setup.sh install  - venv作成 & ADKインストール"
        echo "  bash wxo/setup.sh login    - WxO環境にログイン"
        echo "  bash wxo/setup.sh deploy   - echo_toolをWxOに登録"
        echo "  bash wxo/setup.sh list     - 登録済みツール一覧"
        ;;
esac
