"""Colored logging formatter for experiment scripts.

実験スクリプト用のカラーロギングフォーマッター。

このモジュールは、コンソールに出力されるログメッセージを見やすくするための
カラーフォーマッターを提供します。ログレベルに応じて異なる色を使用し、
タイムスタンプとロガー名も色分けして表示します。

使用目的:
- 実験の実行中に大量のログが出力される際の可読性向上
- ログレベル（INFO、WARNING、ERRORなど）を視覚的に区別
- デバッグ時の重要な情報の見落とし防止

色の使い分け:
- DEBUG: シアン（詳細な診断情報）
- INFO: 緑（通常の情報メッセージ）
- WARNING: 黄色（警告、注意が必要）
- ERROR: 赤（エラー、問題が発生）
- CRITICAL: マゼンタ（致命的なエラー）
"""

import logging
from datetime import datetime


class ColoredFormatter(logging.Formatter):
    """Colored formatter for logging.

    ログメッセージに色を付けるフォーマッター。

    Python の標準 logging.Formatter を拡張し、ANSI エスケープコードを使用して
    ターミナル出力に色を付けます。
    """

    COLORS = {
        "DEBUG": "\033[36m",  # Cyan - シアン（デバッグ情報用）
        "INFO": "\033[32m",  # Green - 緑（通常の情報用）
        "WARNING": "\033[33m",  # Yellow - 黄色（警告用）
        "ERROR": "\033[31m",  # Red - 赤（エラー用）
        "CRITICAL": "\033[35m",  # Magenta - マゼンタ（致命的エラー用）
    }
    RESET = "\033[0m"  # リセットコード（色をデフォルトに戻す）

    def format(self, record: logging.LogRecord):
        """Format a log record.

        ログレコードをフォーマットしてカラー出力用の文字列に変換。

        Args:
            record: Python の logging.LogRecord オブジェクト

        Returns:
            フォーマットされた色付きログメッセージ

        フォーマット例:
            INFO [experiment.runner] (14:30:25) Starting experiment
            ^^^^   ^^^^^^^^^^^^^^^^^  ^^^^^^^^^^  ^^^^^^^^^^^^^^^^^^
            緑色   青色ロガー名        グレー時刻   メッセージ本文

        """
        color = self.COLORS.get(record.levelname, "")
        # ログレベルに対応する色コードを取得
        timestamp = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
        # タイムスタンプを時:分:秒 形式にフォーマット
        log_color = f"{color}{record.levelname}{self.RESET}"
        # ログレベルに色を付ける（例: 緑色の "INFO"）
        name_color = f"\033[34m{record.name}{self.RESET}"  # Blue - ロガー名を青色で表示
        time_color = (
            f"\033[90m({timestamp}){self.RESET}"  # Gray - タイムスタンプをグレーで表示
        )
        return f"{log_color} [{name_color}] {time_color} {record.getMessage()}"
        # フォーマット: "レベル [ロガー名] (時刻) メッセージ"


def setup_logging():
    """Set up colorful logging configuration and suppress noisy loggers.

    カラフルなロギング設定を行い、ノイズの多いロガーを抑制。

    この関数は、実験スクリプトの開始時に一度呼び出され、グローバルなロギング設定を行います。
    主な処理:
    1. 基本的なロギング設定（レベル、フォーマット）
    2. カラーフォーマッターの適用
    3. サードパーティライブラリのログレベル調整

    ノイズ抑制の理由:
    - azure、httpx、httpcore、urllib3 などのライブラリは大量のDEBUG/INFOログを出力
    - 実験の重要なログが埋もれてしまうため、これらをERRORレベルに制限
    - 実験固有のログメッセージに集中できるようにする
    """
    logging.basicConfig(
        level=logging.INFO,
        # ルートロガーのレベルをINFOに設定（DEBUG未満は表示しない）
        format="%(levelname)s [%(name)s] (%(asctime)s) %(message)s",
        # フォールバックフォーマット（ColoredFormatterが使われる場合は上書きされる）
        datefmt="%H:%M:%S",
        # 時刻フォーマット: 時:分:秒
        handlers=[],
        # ハンドラーは空で初期化（後でカスタムハンドラーを追加）
    )

    console_handler = logging.StreamHandler()
    # コンソール（標準エラー出力）へのハンドラーを作成
    console_handler.setFormatter(ColoredFormatter())
    # カスタムのカラーフォーマッターを設定

    root_logger = logging.getLogger()
    # ルートロガーを取得
    root_logger.handlers.clear()
    # 既存のハンドラーをすべてクリア（重複を避ける）
    root_logger.addHandler(console_handler)
    # カラーフォーマッターを使用するコンソールハンドラーを追加

    # 以下のライブラリは詳細すぎるログを出力するため、ERRORレベルに制限
    logging.getLogger("azure").setLevel(logging.ERROR)
    # Azure SDKのログを抑制
    logging.getLogger("httpx").setLevel(logging.ERROR)
    # HTTPXライブラリのログを抑制（LLM APIリクエストで使用）
    logging.getLogger("httpcore").setLevel(logging.ERROR)
    # HTTPコアライブラリのログを抑制
    logging.getLogger("urllib3").setLevel(logging.ERROR)
    # urllib3のログを抑制
