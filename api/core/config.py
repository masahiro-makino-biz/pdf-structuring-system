# =============================================================================
# api/core/config.py - 設定一元管理
# =============================================================================
#
# 【なぜこのファイルを作ったか】
# - 各ファイルでos.getenv()を呼ぶのではなく、一箇所で管理
# - 起動時に全ての環境変数をバリデーション
# - 型安全で、IDEの補完が効く
#
# 【他の方法】
# - python-dotenv + dataclass: シンプルだがバリデーションが弱い
# - dynaconf: 高機能だが学習コスト高い
# - 今回はFastAPIとの相性が良いPydantic Settingsを選択
#
# 【使い方】
# from core.config import get_settings
# settings = get_settings()
# print(settings.mongo_url)
#
# =============================================================================

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    アプリケーション設定

    環境変数から自動的に値を読み込む。
    環境変数名は大文字小文字を区別しない（MONGO_URL, mongo_url どちらもOK）
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # -------------------------------------------------------------------------
    # アプリケーション設定
    # -------------------------------------------------------------------------
    app_name: str = Field(
        default="PDF構造化API",
        description="アプリケーション名"
    )
    debug: bool = Field(
        default=False,
        description="デバッグモード（Trueでログレベルが DEBUG になる）"
    )

    # -------------------------------------------------------------------------
    # MongoDB設定
    # -------------------------------------------------------------------------
    mongo_url: str = Field(
        default="mongodb://mongo:27017",
        description="MongoDB接続URL"
    )
    mongo_database: str = Field(
        default="pdf_system",
        description="データベース名"
    )

    # -------------------------------------------------------------------------
    # MCP設定
    # -------------------------------------------------------------------------
    mcp_url: str = Field(
        default="http://mcp:8001",
        description="MCPサーバーURL"
    )
    mcp_timeout: float = Field(
        default=30.0,
        description="MCPリクエストタイムアウト（秒）"
    )

    # -------------------------------------------------------------------------
    # AI設定
    # -------------------------------------------------------------------------
    ai_provider: Literal["openai", "azure", "bedrock"] = Field(
        default="openai",
        description="AIプロバイダー（openai, azure, bedrock）"
    )
    openai_api_key: str = Field(
        default="",
        description="OpenAI APIキー"
    )
    openai_model: str = Field(
        default="gpt-4o",
        description="使用するOpenAIモデル"
    )
    openai_chat_model_id: str = Field(
        default="gpt-4o",
        description="チャット用モデルID（Agent Framework用）"
    )
    openai_timeout: float = Field(
        default=60.0,
        description="OpenAI APIタイムアウト（秒）"
    )
    openai_max_retries: int = Field(
        default=3,
        description="OpenAI APIリトライ回数"
    )

    # Azure OpenAI設定
    azure_openai_endpoint: str = Field(
        default="",
        description="Azure OpenAI エンドポイント"
    )
    azure_openai_deployment: str = Field(
        default="",
        description="Azure OpenAI デプロイメント名"
    )

    # -------------------------------------------------------------------------
    # AWS Bedrock設定
    # -------------------------------------------------------------------------
    # 【なぜBedrock設定を分離したか】
    # - AWS認証情報はOpenAI/Azureとは別の体系
    # - リージョンやモデルIDもAWS固有のフォーマット
    aws_region: str = Field(
        default="us-east-1",
        description="AWSリージョン（Claudeが利用可能なリージョン）"
    )
    aws_access_key_id: str = Field(
        default="",
        description="AWSアクセスキーID"
    )
    aws_secret_access_key: str = Field(
        default="",
        description="AWSシークレットアクセスキー"
    )
    bedrock_model_id: str = Field(
        default="anthropic.claude-3-5-sonnet-20241022-v2:0",
        description="BedrockモデルID（通常のClaude IDとは異なる形式）"
    )

    # -------------------------------------------------------------------------
    # データディレクトリ
    # -------------------------------------------------------------------------
    data_dir: str = Field(
        default="/data",
        description="ファイル保存先ディレクトリ"
    )


@lru_cache
def get_settings() -> Settings:
    """
    設定のシングルトンを取得

    【@lru_cacheとは】
    関数の結果をキャッシュする。同じ引数で2回目以降は計算せず結果を返す。
    設定は起動時に1回読めばよいので、キャッシュで効率化。

    【使い方】
    settings = get_settings()
    print(settings.mongo_url)  # "mongodb://mongo:27017"
    """
    return Settings()
