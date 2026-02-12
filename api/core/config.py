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
        description="自作MCPサーバーURL（検索・可視化ツール）"
    )
    mongodb_mcp_url: str = Field(
        default="http://mongodb-mcp:3100",
        description="MongoDB公式MCPサーバーURL（find/aggregate等）"
    )
    mcp_timeout: float = Field(
        default=30.0,
        description="MCPリクエストタイムアウト（秒）"
    )

    # -------------------------------------------------------------------------
    # LiteLLM設定（全プロバイダー統一）
    # -------------------------------------------------------------------------
    # 【なぜLiteLLMを使うか】
    # - OpenAI/Azure/Bedrock/Gemini を同じコードで呼び出せる
    # - Agent Framework が Bedrock 非対応でも、LiteLLM経由なら使える
    # - プロバイダー切り替えは litellm/config.yaml で行う
    litellm_url: str = Field(
        default="http://litellm:4000",
        description="LiteLLMプロキシURL"
    )
    litellm_model: str = Field(
        default="gpt-4o",
        description="使用するモデル名（litellm/config.yamlで定義したmodel_name）"
    )

    # -------------------------------------------------------------------------
    # OpenAI設定（PDF解析用）
    # -------------------------------------------------------------------------
    # 【なぜ残すか】
    # - PDF解析（画像→構造化データ）は直接OpenAI呼び出しが必要
    # - LiteLLMはチャット用、これは解析用
    openai_api_key: str = Field(
        default="",
        description="OpenAI APIキー（PDF解析用）"
    )
    openai_model: str = Field(
        default="gpt-4o",
        description="PDF解析に使用するOpenAIモデル"
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
