# =============================================================================
# api/core/logging.py - ロギング設定
# =============================================================================
#
# 【なぜこのファイルを作ったか】
# - print()ではなくloggingモジュールを使用
# - タイムスタンプ、ログレベル、ファイル名が自動で付く
# - 本番では監視ツール（CloudWatch、Datadog等）と連携可能
#
# 【他の方法】
# - loguru: よりシンプルなAPI。ただし標準ライブラリでない
# - structlog: 高機能な構造化ログ。JSONで出力したい場合に便利
# - 今回は標準loggingで十分と判断
#
# 【使い方】
# from core.logging import get_logger
# logger = get_logger(__name__)
# logger.info("処理開始")
# logger.error("エラー発生", exc_info=True)
#
# =============================================================================

import logging
import sys
from typing import Optional

from .config import get_settings


def setup_logging(level: Optional[str] = None) -> logging.Logger:
    """
    アプリケーションロガーを設定

    【この関数を最初に1回だけ呼ぶ】
    main.pyの起動時に呼び出すことで、全体のログ設定が有効になる

    Args:
        level: ログレベル（DEBUG, INFO, WARNING, ERROR, CRITICAL）
               指定しない場合は設定から取得

    Returns:
        設定済みのロガー

    【ログレベルとは】
    - DEBUG: 開発時の詳細情報（変数の値など）
    - INFO: 正常な動作の記録（処理開始・完了など）
    - WARNING: 問題ではないが注意が必要（非推奨機能の使用など）
    - ERROR: エラーが発生したが継続可能
    - CRITICAL: 致命的なエラー（システム停止レベル）
    """
    settings = get_settings()

    # ログレベルを決定
    # 引数で指定 > 設定のdebugフラグ
    if level:
        log_level = level
    elif settings.debug:
        log_level = "DEBUG"
    else:
        log_level = "INFO"

    # ルートロガーを取得
    logger = logging.getLogger("pdf_api")
    logger.setLevel(getattr(logging, log_level))

    # 既存のハンドラをクリア（重複防止）
    # ※ 複数回setup_logging()を呼んでもハンドラが増えない
    logger.handlers.clear()

    # コンソールハンドラを追加
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(getattr(logging, log_level))

    # フォーマッタを設定
    # 出力例: 2024-01-23 10:30:45 | INFO     | pdf_api.main | MongoDB接続成功
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    # 他のライブラリのログレベルを調整（うるさいので）
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)

    return logger


def get_logger(name: str = "pdf_api") -> logging.Logger:
    """
    名前付きロガーを取得

    【なぜ名前を付けるか】
    - ログにどのファイル/モジュールから出力されたかが表示される
    - 特定のモジュールだけログレベルを変えることも可能

    Args:
        name: ロガー名（通常は __name__ を渡す）

    Returns:
        ロガーインスタンス

    【使い方】
    # ファイルの先頭で
    from core.logging import get_logger
    logger = get_logger(__name__)

    # 使用時
    logger.info("処理開始")
    logger.debug(f"変数の値: {value}")
    logger.error("エラー発生", exc_info=True)  # exc_info=Trueでスタックトレースも出力
    """
    # pdf_api をプレフィックスとして付ける
    # これにより setup_logging() の設定が継承される
    if name.startswith("pdf_api"):
        return logging.getLogger(name)
    return logging.getLogger(f"pdf_api.{name}")
