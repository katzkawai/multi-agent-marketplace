"""Marketplace logger for dual Python/database logging.

Pythonロギングとデータベースロギングのデュアルロガー。

このモジュールは、マーケットプレイス用のカスタムロガーを提供します。
標準のPythonロギングとデータベースへの永続化の両方を同時に行います。

デュアルロギングの理由:
1. 開発時の可視性: コンソールでリアルタイムにログを確認
2. 永続化: データベースに保存し、後で分析可能
3. 実験再現性: すべてのログがデータベースに記録されるため、実験を再現・分析できる
4. 監視と分析: LLM呼び出しの成功率、レイテンシなどを分析

主要な機能:
- Pythonのloggingモジュールとの統合（標準のログレベルを使用）
- 非同期データベースロギング（fire-and-forget方式で性能影響を最小化）
- 構造化ログ: dataフィールドに辞書やPydanticモデルを含められる
- エラーハンドリング: データベースロギング失敗時もPythonロギングは継続
"""

import asyncio
import logging
import traceback
from typing import Any

from pydantic import BaseModel

from ..platform.shared.models import Log, LogLevel
from .client import MarketplaceClient


class MarketplaceLogger:
    """Logger wrapper that logs to both Python logging and the database.

    Pythonロギングとデータベースの両方にログを記録するラッパー。

    このクラスは、標準のPython loggingインターフェースと同じメソッド
    （debug、info、warning、error）を提供しながら、同時にデータベースにも
    ログを保存します。

    設計の特徴:
    - 非同期: データベース書き込みは非同期タスクとして実行（ブロッキングなし）
    - Fire-and-forget: ログ記録が完了するのを待たない（性能重視）
    - タスク追跡: flush()で全タスクの完了を待つことも可能
    - エラー分離: データベースログ失敗がPythonログに影響しない

    使用例:
        logger = MarketplaceLogger("my_agent", client)
        logger.info("Agent started")
        logger.error("Failed to process request", data={"request_id": "123"})
        await logger.flush()  # すべてのログがDBに書き込まれるまで待つ
    """

    def __init__(self, name: str, client: MarketplaceClient):
        """Initialize marketplace logger with name and client.

        マーケットプレイスロガーを名前とクライアントで初期化。

        Args:
            name: ロガーの名前（通常はモジュール名やエージェント名）
            client: MarketplaceClient インスタンス（データベースへの書き込み用）

        """
        self.name = name
        # ロガーの名前（ログメッセージに表示される）
        # Set up basic logging config if none exists
        # 基本的なロギング設定がまだない場合は設定
        if not logging.getLogger().handlers:
            logging.basicConfig(
                level=logging.INFO, format="%(levelname)s [%(name)s] %(message)s"
            )
        self.python_logger = logging.getLogger(name)
        # 標準のPythonロガーを取得
        self._client = client
        # MarketplaceClientインスタンス（データベースアクセス用）
        self._tasks: list[asyncio.Task] = []
        # 実行中の非同期ログタスクのリスト（flush用）

    def _log(
        self,
        level: LogLevel,
        message: str | None = None,
        *,
        data: dict[str, Any] | BaseModel | None = None,
        metadata: dict[str, Any] | None = None,
    ):
        """Log to both Python logger and database.

        Pythonロガーとデータベースの両方にログを記録する内部メソッド。

        Args:
            level: ログレベル（"debug", "info", "warning", "error"）
            message: ログメッセージ（人間が読むテキスト、任意）
            data: 構造化データ（辞書またはPydanticモデル、任意）
            metadata: 追加のメタデータ（辞書形式、任意）

        Returns:
            データベースログタスク（await可能）

        Raises:
            ValueError: messageとdataの両方がNoneの場合

        処理フロー:
        1. Pythonロガーに即座にログ出力（同期）
        2. データベースログタスクを作成（非同期、fire-and-forget）
        3. タスクをリストに追加（flush用）

        """
        if message is None and data is None:
            # 最低1つは指定する必要がある
            raise ValueError("Must provide at least one of message or data.")

        # Log to Python logging
        # Pythonのloggingモジュールにログ出力
        python_level = getattr(
            logging,
            level.upper(),
        )
        # ログレベルの文字列から対応する定数を取得（例: "info" -> logging.INFO）
        self.python_logger.log(
            python_level,
            message,
        )

        log = Log(
            level=level, name=self.name, message=message, data=data, metadata=metadata
        )
        # Logオブジェクトを作成（データベースに保存する構造化ログ）

        # Log to database. Fire and forget to avoid blocking but return task in case caller wants to wait.
        # データベースにログ記録（Fire-and-forget: 完了を待たずに続行）
        # ただし、呼び出し側が待ちたい場合のためにタスクを返す
        task = asyncio.create_task(self._log_to_db(log))
        # 非同期タスクを作成
        self._tasks.append(task)
        # タスクリストに追加（flush時に使用）
        task.add_done_callback(self._remove_task)
        # タスク完了時にリストから削除するコールバックを設定
        return task

    async def _log_to_db(self, log: Log):
        """Async helper to log to database.

        データベースにログを記録する非同期ヘルパー。

        Args:
            log: 記録するLogオブジェクト

        エラーハンドリング:
            データベースロギングが失敗しても、Pythonロガーにエラーを記録するのみで、
            例外は発生させません（アプリケーションの実行を妨げない）。

        """
        try:
            await self._client.logs.create(log)
            # MarketplaceClient経由でログをデータベースに保存
        except Exception:
            # If database logging fails, log the error to Python logger only
            # データベースロギングが失敗した場合、Pythonロガーにのみエラーを記録
            # 例外は再発生させない（アプリケーションの継続を優先）
            self.python_logger.error(
                f"Failed to log to database: {traceback.format_exc()}"
            )

    def debug(
        self,
        message: str | None = None,
        *,
        data: dict[str, Any] | BaseModel | None = None,
        metadata: dict[str, Any] | None = None,
    ):
        """Log a debug message.

        デバッグメッセージをログ記録。詳細な診断情報に使用。
        """
        return self._log("debug", message, data=data, metadata=metadata)

    def info(
        self,
        message: str | None = None,
        *,
        data: dict[str, Any] | BaseModel | None = None,
        metadata: dict[str, Any] | None = None,
    ):
        """Log an info message.

        情報メッセージをログ記録。通常の動作状況の報告に使用。
        """
        return self._log("info", message, data=data, metadata=metadata)

    def warning(
        self,
        message: str | None = None,
        *,
        data: dict[str, Any] | BaseModel | None = None,
        metadata: dict[str, Any] | None = None,
    ):
        """Log a warning message.

        警告メッセージをログ記録。注意が必要だが致命的ではない状況に使用。
        """
        return self._log("warning", message, data=data, metadata=metadata)

    def error(
        self,
        message: str | None = None,
        *,
        data: dict[str, Any] | BaseModel | None = None,
        metadata: dict[str, Any] | None = None,
    ):
        """Log an error message.

        エラーメッセージをログ記録。問題が発生した場合に使用。
        """
        return self._log("error", message, data=data, metadata=metadata)

    def exception(
        self,
        message: str | None = None,
        *,
        data: dict[str, Any] | BaseModel | None = None,
        metadata: dict[str, Any] | None = None,
    ):
        """Log an error message with exception traceback.

        例外のトレースバック付きでエラーメッセージをログ記録。

        例外ハンドラー内で呼び出すと、現在の例外のスタックトレースを
        自動的にメッセージに追加します。

        使用例:
            try:
                risky_operation()
            except Exception:
                logger.exception("Operation failed")
        """
        message = ((message or "") + "\n" + traceback.format_exc(2)).strip()
        # 現在の例外のトレースバックを取得してメッセージに追加
        return self.error(message, data=data, metadata=metadata)

    def _remove_task(self, task: asyncio.Task):
        """Remove a task from the task list.

        タスクリストからタスクを削除する内部メソッド。

        タスク完了時にコールバックとして呼ばれます。
        """
        try:
            self._tasks.remove(task)
        except ValueError:
            # Debug because this can get noisy and is expected when flush is called
            # DEBUGレベルでログ（flush時に発生する可能性があり、正常な動作）
            self.python_logger.debug("Failed to remove task: task is not in list.")

    async def flush(self):
        """Wait for any pending tasks to complete.

        保留中のすべてのログタスクが完了するまで待機。

        この関数は、エージェント終了時やクリティカルなポイントで呼び出し、
        すべてのログがデータベースに書き込まれたことを保証します。

        Returns:
            すべてのタスクの結果（例外が発生した場合も含む）

        使用例:
            logger.info("Processing complete")
            await logger.flush()  # すべてのログがDBに書き込まれるまで待つ
            # ここでプログラムを安全に終了できる

        """
        tasks = list(self._tasks)
        # 現在のタスクリストのコピーを取得
        self._tasks.clear()
        # タスクリストをクリア（新しいログは新しいリストに追加される）
        return await asyncio.gather(*tasks, return_exceptions=True)
        # すべてのタスクを並列実行し、完了を待つ
        # return_exceptions=True: 例外が発生してもすべてのタスクを待つ
