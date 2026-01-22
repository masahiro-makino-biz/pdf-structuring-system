# =============================================================================
# api/services/agent_config.py - AIプロバイダー設定
# =============================================================================
# OpenAI/Azure OpenAIを切り替えるための設定ファイル
# 環境変数AI_PROVIDERで切り替え可能
# =============================================================================

import os
from agent_framework.openai import OpenAIChatClient
# from agent_framework.azure import AzureOpenAIChatClient  # Azure使用時にコメント解除
# from azure.identity import DefaultAzureCredential  # Azure使用時にコメント解除

# =============================================================================
# 設定値
# =============================================================================
# 【AI_PROVIDER】使用するAIプロバイダーを指定
#   - "openai": OpenAI API（デフォルト）
#   - "azure": Azure OpenAI Service
PROVIDER = os.getenv("AI_PROVIDER", "openai")

# 【各プロバイダーの違い】
# OpenAI:
#   - APIキーのみで利用可能
#   - 最新モデルにすぐアクセス可能
#   - 従量課金
#
# Azure OpenAI:
#   - Azureサブスクリプションが必要
#   - 企業向けセキュリティ・コンプライアンス
#   - リージョン選択可能
#   - SLAあり


def get_chat_client():
    """
    AIプロバイダーに応じたチャットクライアントを返す

    【なぜこの関数を作るか】
    - chat_service.pyからプロバイダーの詳細を隠蔽する
    - 切り替え時にこのファイルだけ変更すればOK
    - 将来的に他のプロバイダー（Anthropic等）も追加しやすい

    Returns:
        OpenAIChatClient または AzureOpenAIChatClient

    Raises:
        NotImplementedError: Azure設定が未完了の場合
        ValueError: APIキーが未設定の場合
    """
    if PROVIDER == "azure":
        # ---------------------------------------------------------------------------
        # Azure OpenAI設定（使用時にコメント解除）
        # ---------------------------------------------------------------------------
        # 【必要な環境変数】
        #   - AZURE_OPENAI_ENDPOINT: リソースのエンドポイントURL
        #   - AZURE_OPENAI_DEPLOYMENT: デプロイメント名
        #
        # 【認証方法】
        #   - DefaultAzureCredential: Azure CLIログイン、マネージドID等を自動検出
        #   - APIキーも使用可能（AzureKeyCredentialを使用）
        # ---------------------------------------------------------------------------
        # endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
        # deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT")
        #
        # if not endpoint or not deployment:
        #     raise ValueError(
        #         "Azure OpenAIを使用するには AZURE_OPENAI_ENDPOINT と "
        #         "AZURE_OPENAI_DEPLOYMENT を設定してください"
        #     )
        #
        # return AzureOpenAIChatClient(
        #     endpoint=endpoint,
        #     credential=DefaultAzureCredential(),
        #     deployment=deployment
        # )
        raise NotImplementedError(
            "Azure OpenAIは現在未設定です。"
            "agent_config.py のコメントを解除して設定してください。"
        )

    else:
        # ---------------------------------------------------------------------------
        # OpenAI設定（デフォルト）
        # ---------------------------------------------------------------------------
        api_key = os.getenv("OPENAI_API_KEY")
        # 使用するモデルID（環境変数で上書き可能）
        model_id = os.getenv("OPENAI_CHAT_MODEL_ID", "gpt-5.2")

        if not api_key:
            raise ValueError(
                "OpenAIを使用するには OPENAI_API_KEY を設定してください"
            )

        return OpenAIChatClient(api_key=api_key, model_id=model_id)


# =============================================================================
# 【解説】このファイルの設計意図
# =============================================================================
#
# 【なぜこの構成にしたのか】
# 1. 単一責任の原則: プロバイダー設定だけを担当するファイル
# 2. 依存性注入パターン: chat_serviceは具体的なクライアントを知らなくていい
# 3. 設定の一元管理: プロバイダー切り替えがこのファイルだけで完結
#
# 【他の方法】
# 方法1: chat_service.py内に直接書く
#   - メリット: ファイルが少なくなる
#   - デメリット: 切り替え時にメイン処理のファイルを触る必要がある
#
# 方法2: 環境変数だけで切り替え（条件分岐をchat_serviceに書く）
#   - メリット: 設定ファイルが不要
#   - デメリット: chat_serviceが複雑になる
#
# 方法3: ファクトリーパターン + 設定クラス（今回の方法）
#   - メリット: 拡張しやすい、テストしやすい
#   - デメリット: ファイルが1つ増える
#
# 【注意点】
# - Azure使用時はazure-identityのインストールが必要
# - DefaultAzureCredentialを使うには、事前にAzure CLIでログインするか、
#   マネージドIDを設定する必要がある
# - APIキー認証も可能（本番ではマネージドIDを推奨）
