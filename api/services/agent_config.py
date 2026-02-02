# =============================================================================
# api/services/agent_config.py - AIプロバイダー設定
# =============================================================================
#
# 【ファイル概要】
# AIクライアント（OpenAI/Azure OpenAI/AWS Bedrock）を生成するファクトリー。
# 環境変数 AI_PROVIDER でプロバイダーを切り替え可能。
#
# 【処理フロー】
# 1. chat_service.py が get_chat_client() を呼び出す
# 2. AI_PROVIDER の値に応じて OpenAI or Azure or Bedrock のクライアントを返す
#
# 【なぜこのファイルを分離したか】
# - プロバイダー切り替え時にこのファイルだけ変更すればOK
# - chat_service.py はプロバイダーの詳細を知らなくていい
#
# 【Bedrock対応について】
# - agent-framework は Bedrock 非対応（2025年5月時点）
# - そのため Anthropic SDK を直接使用
# - get_bedrock_client() は AnthropicBedrock を返す
#
# =============================================================================

from agent_framework.openai import OpenAIChatClient
from anthropic import AnthropicBedrock

from core.config import get_settings
from core.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()

PROVIDER = settings.ai_provider


def get_bedrock_client() -> AnthropicBedrock:
    """
    AWS Bedrock用のAnthropicクライアントを返す

    【処理フロー】
    1. 設定からAWS認証情報を取得
    2. AnthropicBedrockクライアントを作成して返す

    【なぜこの実装か】
    - Anthropic SDKがBedrock接続をネイティブサポート
    - boto3を直接使うより簡潔に書ける

    【注意点】
    - AWS_ACCESS_KEY_ID と AWS_SECRET_ACCESS_KEY が必要
    - 本番環境ではIAM Roleの使用を推奨（環境変数を空にすると自動でIAM Role使用）

    Returns:
        AnthropicBedrock クライアント

    Raises:
        ValueError: AWS認証情報が不足している場合
    """
    # 認証情報がない場合はIAM Roleを使用（本番推奨）
    if settings.aws_access_key_id and settings.aws_secret_access_key:
        logger.info(f"Bedrockクライアント作成: region={settings.aws_region}, model={settings.bedrock_model_id}")
        return AnthropicBedrock(
            aws_region=settings.aws_region,
            aws_access_key=settings.aws_access_key_id,
            aws_secret_key=settings.aws_secret_access_key,
        )
    else:
        # IAM Role使用（EC2/ECS/Lambdaなど）
        logger.info(f"Bedrockクライアント作成（IAM Role使用）: region={settings.aws_region}")
        return AnthropicBedrock(
            aws_region=settings.aws_region,
        )


def get_chat_client():
    """
    AIプロバイダーに応じたチャットクライアントを返す

    【処理フロー】
    1. AI_PROVIDER 環境変数をチェック
    2. "bedrock" なら AnthropicBedrock を返す
    3. "azure" なら AzureOpenAIChatClient を返す（現在未実装）
    4. それ以外なら OpenAIChatClient を返す

    【なぜこの実装か】
    - ファクトリーパターンで、呼び出し側は具体的なクライアントを知らなくていい
    - Bedrock/Azure/OpenAI を環境変数で切り替え可能

    【注意点】
    - Bedrock の場合は AnthropicBedrock を返す（agent-framework とは異なるAPI）
    - chat_service.py 側でプロバイダーに応じた処理分岐が必要

    Returns:
        OpenAIChatClient, AzureOpenAIChatClient, または AnthropicBedrock

    Raises:
        NotImplementedError: Azure設定が未完了の場合
        ValueError: APIキーが未設定の場合
    """
    if PROVIDER == "bedrock":
        return get_bedrock_client()

    if PROVIDER == "azure":
        raise NotImplementedError("Azure OpenAIは現在未設定です")

    # OpenAI
    api_key = settings.openai_api_key
    model_id = settings.openai_chat_model_id

    if not api_key:
        raise ValueError("OPENAI_API_KEY を設定してください")

    logger.info(f"OpenAIクライアント作成: model={model_id}")
    return OpenAIChatClient(api_key=api_key, model_id=model_id)
