"""Marketplace launcher for coordinating server, protocol, and agents.

マーケットプレイスランチャー - サーバー、プロトコル、エージェントを統合管理するモジュール

このモジュールは、マーケットプレイスプラットフォームの起動と管理を担当します。
主な役割:
  - MarketplaceServer（FastAPIサーバー）の起動と停止
  - データベースコントローラーの初期化とライフサイクル管理
  - エージェントの並行実行と調整
  - マーケットプレイスの状態管理とクエリ

アーキテクチャ概要:
  1. MarketplaceLauncher: サーバーとプロトコルを起動し、全体を統合管理
  2. AgentLauncher: 実行中のサーバーに対してエージェントを並行実行
  3. 非同期コンテキストマネージャーパターンでリソースの安全な管理を実現
"""

import asyncio
from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager, AsyncExitStack
from types import TracebackType
from typing import Any, TypeVar

from pydantic import BaseModel

from .agent.base import BaseAgent
from .client import MarketplaceClient
from .database.base import BaseDatabaseController
from .logger import MarketplaceLogger
from .protocol.base import BaseMarketplaceProtocol
from .server import MarketplaceServer
from .shared.models import ActionProtocol, AgentProfile, Log

# TypeVar for any agent profile that extends AgentProfile
# AgentProfileを継承する任意のエージェントプロファイル型の型変数
AnyProfile = TypeVar("AnyProfile", bound=AgentProfile)


class MarketplaceState(BaseModel):
    """Current state of the marketplace.

    マーケットプレイスの現在の状態を表すモデル。
    サーバーの健全性、登録済みエージェント、利用可能なアクション、最近のログを含みます。
    """

    server_health: dict[str, Any]  # サーバーのヘルスチェック結果
    agents: list[AgentProfile]  # 登録済みの全エージェントプロファイル
    action_protocols: list[ActionProtocol]  # 利用可能なアクションプロトコル
    recent_logs: list[Log]  # 最近のログエントリ


class MarketplaceLauncher:
    """Launches and manages the marketplace server and protocol.

    マーケットプレイスランチャー - サーバーとプロトコルの起動と管理を行うクラス

    このクラスは、マーケットプレイスプラットフォームの中核を担います。
    主な責務:
      1. MarketplaceServer（FastAPIサーバー）の起動とヘルスチェック
      2. データベースコントローラーのライフサイクル管理
      3. プロトコル（ビジネスロジック）の初期化
      4. 非同期コンテキストマネージャーとしてリソースの安全な管理

    使用方法:
        async with MarketplaceLauncher(protocol, db_factory) as launcher:
            # サーバーが起動し、ヘルスチェックが完了
            # エージェントを実行したり、状態をクエリしたりできる
            state = await launcher.query_marketplace_state()
        # 終了時に自動的にサーバーが停止し、リソースがクリーンアップされる
    """

    def __init__(
        self,
        protocol: BaseMarketplaceProtocol,
        database_factory: Callable[
            [], AbstractAsyncContextManager[BaseDatabaseController, bool | None]
        ],
        *,
        host: str = "127.0.0.1",
        port: int = 8000,
        title: str = "Marketplace API",
        description: str = "A marketplace for autonomous agents",
        server_log_level: str = "info",
        experiment_name: str | None = None,
    ):
        """Initialize the marketplace launcher.

        マーケットプレイスランチャーの初期化。

        Args:
            protocol: マーケットプレイスのビジネスロジックを定義するプロトコル
            database_factory: データベースコントローラーを生成するファクトリー関数
                            （非同期コンテキストマネージャーを返す）
            host: サーバーのホストアドレス（デフォルト: 127.0.0.1）
            port: サーバーのポート番号（デフォルト: 8000）
            title: API ドキュメントのタイトル
            description: API ドキュメントの説明
            server_log_level: FastAPI サーバーのログレベル
                            (debug, info, warning, error, critical)
            experiment_name: 実験の名前（省略可能）

        """
        # プロトコルとデータベースのファクトリーを保存
        self.protocol = protocol
        self.database_factory = database_factory

        # サーバー設定
        self.host = host
        self.port = port
        self.title = title
        self.description = description
        self.server_log_level = server_log_level
        self.experiment_name = experiment_name

        # サーバーインスタンスと制御用の変数（初期化後に設定）
        self.server: MarketplaceServer | None = None  # FastAPIサーバーインスタンス
        self.server_task: asyncio.Task[None] | None = None  # サーバー実行タスク
        self._stop_server_fn: Callable[[], None] | None = None  # サーバー停止関数
        self.server_url = f"http://{host}:{port}"  # サーバーのURL
        self._exit_stack: AsyncExitStack | None = None  # リソース管理用のスタック

    async def start_server(
        self,
        *,
        max_retries: int = 10,
        retry_delay: float = 0.1,
        max_delay: float = 5.0,
    ) -> None:
        """Start the marketplace server.

        マーケットプレイスサーバーを起動します。

        サーバー起動の流れ:
          1. MarketplaceServerインスタンスを作成（データベースとプロトコルを注入）
          2. バックグラウンドタスクとしてサーバーを起動
          3. ヘルスチェックでサーバーが正常に起動したことを確認
          4. リトライとエクスポネンシャルバックオフで起動の安定性を確保

        Args:
            max_retries: ヘルスチェックの最大試行回数（デフォルト: 10）
            retry_delay: リトライ間の初期待機時間（秒）（デフォルト: 0.1）
            max_delay: リトライ間の最大待機時間（秒）（デフォルト: 5.0）

        Raises:
            RuntimeError: サーバーが指定回数のリトライ後も正常に起動しなかった場合

        """
        # サーバーを作成して設定
        # データベースファクトリーとプロトコルを注入
        self.server = MarketplaceServer(
            database_factory=self.database_factory,
            protocol=self.protocol,
            title=self.title,
            description=self.description,
        )
        print("Creating MarketplaceServer...")

        # サーバーをバックグラウンドで起動
        # server_taskはサーバーの実行タスク、_stop_server_fnは停止用のコールバック
        self.server_task, self._stop_server_fn = self.server.create_server_task(
            host=self.host, port=self.port, log_level=self.server_log_level
        )

        # サーバーの起動を待機（ヘルスチェックとエクスポネンシャルバックオフ）
        last_exception = None
        current_delay = retry_delay
        for _ in range(max_retries):
            try:
                # ヘルスチェックエンドポイントに接続を試みる
                async with MarketplaceClient(self.server_url) as client:
                    await client.health_check()
                    print(
                        f"MarketplaceServer is running and healthy at {self.server_url}"
                    )
                    return  # 成功：サーバーが正常に起動
            except Exception as e:
                # 失敗：待機してリトライ
                last_exception = e
                await asyncio.sleep(current_delay)
                current_delay = min(
                    current_delay * 2, max_delay
                )  # エクスポネンシャルバックオフ

        # 接続失敗：最大試行回数に達した
        raise RuntimeError(
            f"Server failed to become healthy after {max_retries} attempts"
        ) from last_exception

    async def stop_server(self) -> None:
        """Stop the marketplace server.

        マーケットプレイスサーバーを停止します。

        停止の流れ:
          1. 停止関数を呼び出してサーバーにシャットダウンシグナルを送信
          2. サーバータスクの完了を待機
          3. CancelledErrorは正常な停止なので無視

        """
        if self._stop_server_fn:
            print("Stopping server...")
            self._stop_server_fn()  # サーバーにシャットダウンシグナルを送信

        if self.server_task:
            try:
                await self.server_task  # サーバータスクの完了を待機
            except asyncio.CancelledError:
                pass  # CancelledErrorは正常な停止処理の一部

        print("Server stopped")

    async def create_logger(self, name: str = __name__) -> MarketplaceLogger:
        """Create a logger connected to the marketplace.

        マーケットプレイスに接続されたロガーを作成します。

        このロガーは、Pythonの標準ロギングとデータベースへのログ記録の両方をサポートします。
        AsyncExitStackを使用してクライアントのライフサイクルを管理します。

        Args:
            name: ロガーの名前（デフォルト: 現在のモジュール名）

        Returns:
            MarketplaceLogger: データベースに接続されたロガーインスタンス

        Raises:
            RuntimeError: 非同期コンテキストマネージャー外で呼び出された場合

        """
        if self._exit_stack is None:
            raise RuntimeError(
                "MarketplaceLauncher must be used as an async context manager"
            )

        # クライアントを作成し、AsyncExitStackで管理
        # これによりランチャー終了時に自動的にクリーンアップされる
        client = await self._exit_stack.enter_async_context(
            MarketplaceClient(self.server_url)
        )
        logger = MarketplaceLogger(name, client)
        return logger

    async def query_marketplace_state(self) -> MarketplaceState:
        """Query the current state of the marketplace.

        マーケットプレイスの現在の状態をクエリします。

        この関数は、サーバーの健全性、登録済みエージェント、利用可能なアクション、
        最近のログなど、マーケットプレイスの完全な状態を取得します。

        Returns:
            MarketplaceState: 現在のマーケットプレイス情報を含む状態オブジェクト

        """
        async with MarketplaceClient(self.server_url) as client:
            # サーバーの健全性を取得
            health = await client.health_check()

            # 登録済みの全エージェントを取得（ページネーション対応）
            agents: list[AgentProfile] = []
            offset = 0
            limit = 100
            has_more = True

            while has_more:
                agents_response = await client.agents.list(offset=offset, limit=limit)
                agents.extend(agents_response.items)
                has_more = agents_response.has_more or False
                offset += limit

            # 利用可能なアクションプロトコルを取得
            protocols = await client.actions.get_protocol()

            # 最近のログを取得
            logs_response = await client.logs.list(limit=10)

            return MarketplaceState(
                server_health=health,
                agents=agents,
                action_protocols=protocols.actions,
                recent_logs=logs_response.items,
            )

    async def __aenter__(self):
        """Async context manager entry.

        非同期コンテキストマネージャーのエントリーポイント。

        このメソッドは、`async with MarketplaceLauncher(...) as launcher:` の
        withブロックに入る際に自動的に呼び出されます。

        実行内容:
          1. AsyncExitStackを初期化してリソース管理を開始
          2. サーバーを起動してヘルスチェックを実行
          3. 自身のインスタンスを返して、withブロック内で使用可能にする

        """
        self._exit_stack = AsyncExitStack()
        await self._exit_stack.__aenter__()
        await self.start_server()  # サーバー起動とヘルスチェック
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Async context manager exit.

        非同期コンテキストマネージャーの終了処理。

        このメソッドは、`async with` ブロックを抜ける際に自動的に呼び出されます。
        例外が発生した場合でも必ず実行されます。

        実行内容:
          1. サーバーを停止してクリーンアップ
          2. AsyncExitStackを終了して全ての管理リソースをクリーンアップ
          3. 例外が発生してもリソースのクリーンアップは保証される（finally）

        Args:
            exc_type: 発生した例外の型（例外がない場合はNone）
            exc_val: 発生した例外のインスタンス（例外がない場合はNone）
            exc_tb: 例外のトレースバック（例外がない場合はNone）

        """
        try:
            await self.stop_server()  # サーバーを停止
        finally:
            # 例外が発生してもリソースをクリーンアップ
            if self._exit_stack is not None:
                await self._exit_stack.__aexit__(exc_type, exc_val, exc_tb)
                self._exit_stack = None


class AgentLauncher:
    """Launches and manages agents against a running marketplace server.

    エージェントランチャー - 実行中のマーケットプレイスサーバーに対してエージェントを管理

    このクラスは、既に起動しているマーケットプレイスサーバーに接続し、
    複数のエージェントを並行実行する役割を担います。

    主な用途:
      - MarketplaceLauncherとは別プロセスでエージェントを実行する場合
      - 既存のサーバーに新しいエージェントを追加する場合
      - 依存関係のあるエージェント（プライマリとディペンデント）の調整

    使用方法:
        async with AgentLauncher("http://localhost:8000") as agent_launcher:
            # エージェントを並行実行
            await agent_launcher.run_agents(agent1, agent2, agent3)
    """

    def __init__(self, base_url: str):
        """Initialize the agent launcher.

        エージェントランチャーの初期化。

        Args:
            base_url: 実行中のマーケットプレイスサーバーのURL
                     （例: "http://localhost:8000"）

        """
        self.base_url = base_url
        self._exit_stack: AsyncExitStack | None = None  # リソース管理用のスタック

    async def run_agents(self, *agents: BaseAgent[Any]) -> None:
        """Run a list of agents concurrently.

        複数のエージェントを並行実行します。

        全てのエージェントを非同期タスクとして起動し、全ての完了を待機します。
        エージェント間に依存関係がない場合に使用します。

        Args:
            agents: 実行するエージェントのリスト（可変長引数）

        """
        if not agents:
            return

        print(f"\nRunning {len(agents)} agents...")

        # 全エージェントを並行タスクとして起動
        agent_tasks = [asyncio.create_task(agent.run()) for agent in agents]

        # 全エージェントの完了を待機
        await asyncio.gather(*agent_tasks)

        print("All agents completed")

    async def run_agents_with_dependencies(
        self,
        primary_agents: Sequence[BaseAgent[Any]],
        dependent_agents: Sequence[BaseAgent[Any]],
    ) -> None:
        """Run agents where dependent agents shutdown when primary agents complete.

        依存関係のあるエージェントを実行（プライマリエージェント完了時にディペンデント停止）

        このメソッドは、プライマリエージェント（例：顧客）とディペンデントエージェント
        （例：ビジネス）を並行実行しますが、プライマリエージェントが全て完了した時点で、
        ディペンデントエージェントに対してグレースフルシャットダウンのシグナルを送信します。

        使用例:
          - 顧客エージェントが全て購入を完了した後、ビジネスエージェントを停止
          - 実験のライフサイクルを顧客エージェントが主導する場合

        Args:
            primary_agents: 実験のライフサイクルを主導するエージェント（例：顧客）
            dependent_agents: プライマリエージェント完了時に停止すべきエージェント（例：ビジネス）

        """
        if not primary_agents and not dependent_agents:
            return

        print(
            f"\nRunning {len(primary_agents)} primary agents and {len(dependent_agents)} dependent agents..."
        )

        # 全エージェントを並行タスクとして起動
        primary_tasks = [asyncio.create_task(agent.run()) for agent in primary_agents]
        dependent_tasks = [
            asyncio.create_task(agent.run()) for agent in dependent_agents
        ]

        try:
            # プライマリエージェント（例：顧客）の完了を待機
            print(f"Waiting for {len(primary_agents)} primary agents to complete...")
            await asyncio.gather(*primary_tasks)
            print("All primary agents completed")

            # ディペンデントエージェント（例：ビジネス）にグレースフルシャットダウンをシグナル
            print(f"Signaling {len(dependent_agents)} dependent agents to shutdown...")
            for agent in dependent_agents:
                agent.shutdown()

            # エージェントがシャットダウンシグナルを処理する時間を与える
            await asyncio.sleep(0.1)

            # ディペンデントエージェントのグレースフルシャットダウンを待機
            # （エージェントのon_will_stopフックでのロガークリーンアップを含む）
            await asyncio.gather(*dependent_tasks)
            print("All dependent agents shut down gracefully")

            # 全クリーンアップが完了することを保証するための最終待機
            await asyncio.sleep(0.2)

        except Exception as e:
            # エラー発生時は全エージェントにシャットダウンをシグナル
            print(f"Error during execution: {e}")
            for agent in list(primary_agents) + list(dependent_agents):
                agent.shutdown()

            # エージェントがシャットダウンシグナルを処理する時間を与える
            await asyncio.sleep(0.1)

            # 全エージェントのシャットダウンを待機、クリーンアップ中の例外は抑制
            await asyncio.gather(
                *primary_tasks, *dependent_tasks, return_exceptions=True
            )

            # 残りのクリーンアップのための最終待機
            await asyncio.sleep(0.2)
            raise  # 元の例外を再送出

    async def create_logger(self, name: str = __name__) -> MarketplaceLogger:
        """Create a logger connected to the marketplace.

        マーケットプレイスに接続されたロガーを作成します。

        Args:
            name: ロガーの名前（デフォルト: 現在のモジュール名）

        Returns:
            MarketplaceLogger: データベースに接続されたロガーインスタンス

        Raises:
            RuntimeError: 非同期コンテキストマネージャー外で呼び出された場合

        """
        if self._exit_stack is None:
            raise RuntimeError("AgentLauncher must be used as an async context manager")

        # クライアントを作成し、AsyncExitStackで管理
        client = await self._exit_stack.enter_async_context(
            MarketplaceClient(self.base_url)
        )
        logger = MarketplaceLogger(name, client)
        return logger

    async def query_marketplace_state(self) -> MarketplaceState:
        """Query the current state of the marketplace.

        マーケットプレイスの現在の状態をクエリします。

        Returns:
            MarketplaceState: 現在のマーケットプレイス情報を含む状態オブジェクト

        """
        async with MarketplaceClient(self.base_url) as client:
            # サーバーの健全性を取得
            health = await client.health_check()

            # 登録済みの全エージェントを取得（ページネーション対応）
            agents: list[AgentProfile] = []
            offset = 0
            limit = 100
            has_more = True

            while has_more:
                agents_response = await client.agents.list(offset=offset, limit=limit)
                agents.extend(agents_response.items)
                has_more = agents_response.has_more or False
                offset += limit

            # 利用可能なアクションプロトコルを取得
            protocols = await client.actions.get_protocol()

            # 最近のログを取得
            logs_response = await client.logs.list(limit=10)

            return MarketplaceState(
                server_health=health,
                agents=agents,
                action_protocols=protocols.actions,
                recent_logs=logs_response.items,
            )

    async def __aenter__(self):
        """Async context manager entry.

        非同期コンテキストマネージャーのエントリーポイント。

        """
        self._exit_stack = AsyncExitStack()
        await self._exit_stack.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Async context manager exit.

        非同期コンテキストマネージャーの終了処理。

        """
        if self._exit_stack is not None:
            await self._exit_stack.__aexit__(exc_type, exc_val, exc_tb)
            self._exit_stack = None
