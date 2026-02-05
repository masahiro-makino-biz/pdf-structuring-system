# =============================================================================
# api/services/agent_config.py - AIクライアント設定
# =============================================================================
#
# 【ファイル概要】
# LiteLLM経由でAIクライアントを生成するファクトリー。
# OpenAI/Azure/Bedrock/Gemini を同じコードで呼び出せる。
#
# 【なぜLiteLLMを使うか】
# - 全てのLLMプロバイダーがOpenAI互換APIで使える
# - Agent Framework が Bedrock 非対応でも、LiteLLM経由なら使える
# - プロバイダー切り替えは litellm/config.yaml で行う（コード変更不要）
#
# 【処理フロー】
# 1. chat_service.py が get_chat_client() を呼び出す
# 2. LiteLLMプロキシを向いた OpenAIChatClient を返す
# 3. LiteLLMが実際のプロバイダー（OpenAI/Bedrock等）にリクエストを中継
#
# 【変更履歴】
# - LiteLLM導入前: OpenAI/Azure/Bedrock で3つの分岐が必要だった
# - LiteLLM導入後: 1つのクライアントで全プロバイダー対応
#
# =============================================================================

from agent_framework.openai import OpenAIChatClient

from core.config import get_settings
from core.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()


def get_chat_client() -> OpenAIChatClient:
    """
    LiteLLM経由のチャットクライアントを返す

    【処理フロー】
    1. LiteLLMプロキシのURLを取得
    2. 使用するモデル名を取得（litellm/config.yamlで定義）
    3. OpenAIChatClientをLiteLLMに向けて作成

    【なぜOpenAIChatClientを使うか】
    - LiteLLMはOpenAI互換APIを提供する
    - Agent FrameworkのOpenAIChatClientがそのまま使える
    - base_urlをLiteLLMに向けるだけ

    【注意点】
    - api_keyは任意の値でOK（LiteLLMプロキシ側で認証する場合を除く）
    - model_idはlitellm/config.yamlで定義したmodel_nameを指定

    Returns:
        OpenAIChatClient: LiteLLMプロキシ経由のクライアント
    """
    litellm_url = settings.litellm_url
    model_id = settings.litellm_model

    logger.info(f"LiteLLMクライアント作成: url={litellm_url}, model={model_id}")

    return OpenAIChatClient(
        api_key="sk-litellm",  # LiteLLMでは任意の値でOK
        model_id=model_id,
        base_url=litellm_url,  # LiteLLMプロキシに向ける
    )
