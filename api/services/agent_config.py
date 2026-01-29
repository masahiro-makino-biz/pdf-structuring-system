# =============================================================================
# api/services/agent_config.py - AIプロバイダー設定
# =============================================================================
#
# 【ファイル概要】
# AIクライアント（OpenAI/Azure OpenAI）を生成するファクトリー。
# 環境変数 AI_PROVIDER でプロバイダーを切り替え可能。
#
# 【処理フロー】
# 1. chat_service.py が get_chat_client() を呼び出す
# 2. AI_PROVIDER の値に応じて OpenAI or Azure のクライアントを返す
#
# 【なぜこのファイルを分離したか】
# - プロバイダー切り替え時にこのファイルだけ変更すればOK
# - chat_service.py はプロバイダーの詳細を知らなくていい
#
# =============================================================================

from agent_framework.openai import OpenAIChatClient

from core.config import get_settings
from core.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()

PROVIDER = settings.ai_provider


def get_chat_client():
    """
    AIプロバイダーに応じたチャットクライアントを返す

    【処理フロー】
    1. AI_PROVIDER 環境変数をチェック
    2. "azure" なら AzureOpenAIChatClient を返す（現在未実装）
    3. それ以外なら OpenAIChatClient を返す

    【なぜこの実装か】
    - ファクトリーパターンで、呼び出し側は具体的なクライアントを知らなくていい
    - 将来的に Anthropic 等のプロバイダーも追加しやすい

    Returns:
        OpenAIChatClient または AzureOpenAIChatClient

    Raises:
        NotImplementedError: Azure設定が未完了の場合
        ValueError: APIキーが未設定の場合
    """
    if PROVIDER == "azure":
        raise NotImplementedError("Azure OpenAIは現在未設定です")

    api_key = settings.openai_api_key
    model_id = settings.openai_chat_model_id

    if not api_key:
        raise ValueError("OPENAI_API_KEY を設定してください")

    logger.info(f"OpenAIクライアント作成: model={model_id}")
    return OpenAIChatClient(api_key=api_key, model_id=model_id)
