"""MarketplaceServer - FastAPI Server for the Magentic Marketplace API.

マーケットプレイスサーバー - Magentic Marketplace API用のFastAPIサーバー

このモジュールは、FastAPIをサブクラス化したMarketplaceServerクラスを提供します。
非同期コンテキストマネージャーファクトリー関数を受け取り、依存性注入を実現します。

主な機能:
  - データベースコントローラーとプロトコルのライフサイクル管理
  - REST APIエンドポイントの提供（エージェント、アクション、ログ、ヘルスチェック）
  - 認証サービスとIDジェネレーションサービスの管理
  - Uvicornサーバーの同期・非同期起動サポート

アーキテクチャ:
  - FastAPIのlifespanイベントでリソースの初期化とクリーンアップを管理
  - app.stateにサービスインスタンスを保存し、ルートハンドラーで使用
  - ルートモジュール（routes/）で各エンドポイントを定義
"""

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request

from ..database.base import BaseDatabaseController
from ..protocol.base import BaseMarketplaceProtocol
from .auth import AuthService
from .idgen import DatabaseIdGenerationService


class MarketplaceServer(FastAPI):
    """FastAPI server for the Magentic Marketplace API with hybrid dependency injection.

    ハイブリッド依存性注入を持つMagentic Marketplace API用のFastAPIサーバー

    このサーバーは、DatabaseControllerのファクトリー関数（適切なリソース管理のため）と
    BaseMarketplaceProtocolインスタンスを受け取り、それらのライフサイクルを管理し、
    app.stateを通じてルートハンドラーで利用可能にします。

    ライフサイクル管理:
      1. 起動時（lifespan startup）:
         - データベースコントローラーを作成して接続
         - プロトコル固有のリソース（インデックスなど）を初期化
         - 認証サービスとIDジェネレーションサービスを作成
         - 全てをapp.stateに保存

      2. 実行中:
         - ルートハンドラーがapp.stateからサービスを取得
         - リクエストを処理

      3. 停止時（lifespan shutdown）:
         - データベース接続をクリーンアップ
         - その他のリソースを解放
    """

    def __init__(
        self,
        database_factory: Callable[
            [], AbstractAsyncContextManager[BaseDatabaseController]
        ],
        protocol: BaseMarketplaceProtocol,
        **kwargs: Any,
    ):
        """Initialize the MarketplaceServer.

        マーケットプレイスサーバーの初期化。

        Args:
            database_factory: DatabaseControllerの非同期コンテキストマネージャーを
                            返すファクトリー関数
            protocol: ビジネスロジックを定義するプロトコルインスタンス
            **kwargs: FastAPIコンストラクターに渡される追加引数
                     （title, description, version など）

        """
        # ファクトリー関数とプロトコルインスタンスを保存
        self._database_factory = database_factory
        self._behavior_protocol = protocol

        # lifespanマネージャーを作成
        # FastAPIの起動・停止時にリソースの初期化とクリーンアップを行う
        @asynccontextmanager
        async def lifespan(app: FastAPI) -> AsyncIterator[None]:
            # 起動処理: データベースコンテキストマネージャーを作成して開始
            database_cm = self._database_factory()
            database_controller = await database_cm.__aenter__()

            # プロトコル固有のリソースを初期化（例：データベースインデックス）
            await self._behavior_protocol.initialize(database_controller)

            # 認証サービスを作成
            auth_service = AuthService()

            # IDジェネレーションサービスを作成
            id_generation_service = DatabaseIdGenerationService()

            # インスタンスとコンテキストマネージャーをapp.stateに保存
            # これらはルートハンドラーで依存性注入を介してアクセス可能
            app.state.database_controller = database_controller
            app.state.behavior_protocol = self._behavior_protocol
            app.state.auth_service = auth_service
            app.state.id_generation_service = id_generation_service
            app.state._database_cm = database_cm

            try:
                yield  # サーバーが実行中の間はここで待機
            finally:
                # 停止処理: データベースコンテキストマネージャーを正しく終了
                try:
                    await app.state._database_cm.__aexit__(None, None, None)
                except Exception:
                    pass  # 実際の実装ではエラーをログに記録すべき

        # デフォルトのタイトルを設定（未指定の場合）
        if "title" not in kwargs:
            kwargs["title"] = "Magentic Marketplace API"

        # lifespanを設定
        kwargs["lifespan"] = lifespan

        # FastAPIを初期化
        super().__init__(**kwargs)

        # 全ルートモジュールを含める
        # 各ルートモジュールはRESTエンドポイントを定義
        from .routes import actions, agents, health, logs

        self.include_router(agents.router)  # エージェント関連のエンドポイント
        self.include_router(actions.router)  # アクション関連のエンドポイント
        self.include_router(logs.router)  # ログ関連のエンドポイント
        self.include_router(health.router)  # ヘルスチェックエンドポイント

    def serve(
        self,
        host: str = "127.0.0.1",
        port: int = 8000,
        log_level: str = "info",
        **kwargs: Any,
    ) -> None:
        """Start the server synchronously using uvicorn.

        Uvicornを使用してサーバーを同期的に起動します。

        このメソッドは、スクリプトやコマンドラインから直接サーバーを起動する場合に使用します。
        ブロッキング呼び出しで、サーバーが停止するまで戻りません。

        Args:
            host: バインドするホストアドレス（デフォルト: 127.0.0.1）
            port: バインドするポート番号（デフォルト: 8000）
            log_level: Uvicornのログレベル（デフォルト: "info"）
            **kwargs: uvicorn.run()に渡される追加引数

        Raises:
            ImportError: uvicornがインストールされていない場合

        """
        try:
            import uvicorn
        except ImportError as e:
            raise ImportError(
                "uvicorn is required for serve() method. Install with: pip install uvicorn"
            ) from e

        # サーバーを同期的に起動（ブロッキング）
        uvicorn.run(
            self, host=host, port=port, log_level=log_level, workers=1, **kwargs
        )

    async def serve_async(
        self,
        host: str = "127.0.0.1",
        port: int = 8000,
        log_level: str = "info",
        **kwargs: Any,
    ) -> None:
        """Start the server asynchronously using uvicorn.

        Uvicornを使用してサーバーを非同期的に起動します。

        このメソッドは、非同期環境内で他のタスクと並行してサーバーを実行する場合に使用します。
        非ブロッキング呼び出しで、awaitで完了を待機します。

        Args:
            host: バインドするホストアドレス（デフォルト: 127.0.0.1）
            port: バインドするポート番号（デフォルト: 8000）
            log_level: Uvicornのログレベル（デフォルト: "info"）
            **kwargs: uvicorn.Config()に渡される追加引数

        Raises:
            ImportError: uvicornがインストールされていない場合

        """
        try:
            import uvicorn
        except ImportError as e:
            raise ImportError(
                "uvicorn is required for serve_async() method. Install with: pip install uvicorn"
            ) from e

        # Uvicorn設定を作成
        config = uvicorn.Config(
            self,
            host=host,
            port=port,
            log_level=log_level,
            timeout_keep_alive=60,
            workers=1,
            **kwargs,
        )
        # サーバーを非同期的に起動（非ブロッキング）
        server = uvicorn.Server(config)
        await server.serve()

    def create_server_task(
        self,
        host: str = "127.0.0.1",
        port: int = 8000,
        log_level: str = "info",
        **kwargs: Any,
    ) -> tuple["asyncio.Task[None]", Callable[[], None]]:
        """Create a server task with a shutdown function for graceful termination.

        グレースフルシャットダウン機能付きでサーバータスクを作成します。

        このメソッドは、サーバーをバックグラウンドタスクとして起動し、
        後で停止するためのシャットダウン関数を返します。
        MarketplaceLauncherで使用され、サーバーの制御可能な実行を可能にします。

        使用例:
            server_task, shutdown_fn = server.create_server_task()
            # サーバーがバックグラウンドで実行中
            # ...他の処理...
            shutdown_fn()  # サーバーにシャットダウンをシグナル
            await server_task  # サーバーの停止を待機

        Args:
            host: バインドするホストアドレス（デフォルト: 127.0.0.1）
            port: バインドするポート番号（デフォルト: 8000）
            log_level: Uvicornのログレベル（デフォルト: "info"）
            **kwargs: uvicorn.Config()に渡される追加引数

        Returns:
            tuple[asyncio.Task[None], Callable[[], None]]:
                - server_task: サーバー実行タスク
                - shutdown_function: サーバーを停止するためのコールバック関数

        Raises:
            ImportError: uvicornがインストールされていない場合

        """
        try:
            import uvicorn
        except ImportError as e:
            raise ImportError(
                "uvicorn is required for create_server_with_shutdown() method. Install with: pip install uvicorn"
            ) from e

        # Uvicorn設定を作成
        config = uvicorn.Config(
            self,
            host=host,
            port=port,
            log_level=log_level,
            timeout_keep_alive=60,
            workers=1,
            **kwargs,
        )
        server = uvicorn.Server(config)

        # サーバーをバックグラウンドタスクとして起動
        server_task = asyncio.create_task(server.serve())

        # シャットダウン関数を定義
        def shutdown():
            server.should_exit = True  # サーバーに停止をシグナル

        return server_task, shutdown


# Reusable dependency functions that grab from app.state
# 再利用可能な依存性注入関数（app.stateから取得）
#
# これらの関数は、FastAPIの依存性注入システムで使用されます。
# ルートハンドラーで以下のように使用:
#   @router.get("/endpoint")
#   async def handler(db: BaseDatabaseController = Depends(get_database)):
#       # dbを使用してデータベース操作を実行


def get_database(request: Request) -> BaseDatabaseController:
    """Get the database controller from app state.

    app.stateからデータベースコントローラーを取得します。

    Args:
        request: FastAPIのRequestオブジェクト

    Returns:
        BaseDatabaseController: データベースコントローラーインスタンス

    """
    return request.app.state.database_controller


def get_protocol(request: Request) -> BaseMarketplaceProtocol:
    """Get the behavior protocol from app state.

    app.stateからビジネスロジックプロトコルを取得します。

    Args:
        request: FastAPIのRequestオブジェクト

    Returns:
        BaseMarketplaceProtocol: プロトコルインスタンス

    """
    return request.app.state.behavior_protocol


def get_auth_service(request: Request) -> AuthService:
    """Get the auth service from app state.

    app.stateから認証サービスを取得します。

    Args:
        request: FastAPIのRequestオブジェクト

    Returns:
        AuthService: 認証サービスインスタンス

    """
    return request.app.state.auth_service


def get_idgen_service(request: Request) -> DatabaseIdGenerationService:
    """Get the ID generation service from app state.

    app.stateからIDジェネレーションサービスを取得します。

    Args:
        request: FastAPIのRequestオブジェクト

    Returns:
        DatabaseIdGenerationService: IDジェネレーションサービスインスタンス

    """
    return request.app.state.id_generation_service
