"""Main MarketplaceClient with modular resource access.

メインMarketplaceClient - モジュラーリソースアクセスを提供するクライアント

このモジュールは、マーケットプレイスサーバーとHTTP通信を行うクライアントを提供します。
エージェントがマーケットプレイスと対話するための主要なインターフェースです。

主な機能:
  - REST APIエンドポイントへのHTTPリクエスト送信
  - エージェント、アクション、ログの各リソースへのモジュラーアクセス
  - BaseClientインスタンスのキャッシュによる効率的な接続管理
  - 非同期コンテキストマネージャーとしてのリソース管理

アーキテクチャ:
  1. MarketplaceClient: ユーザー向けの高レベルAPIを提供
  2. BaseClient: 低レベルHTTP通信を処理（キャッシュされる）
  3. Resource classes: 各エンドポイントグループの操作を提供
     - AgentsResource: エージェント登録、取得、リスト化
     - ActionsResource: アクション実行、プロトコル取得
     - LogsResource: ログ作成、クエリ

使用方法:
    async with MarketplaceClient("http://localhost:8000") as client:
        # エージェントを登録
        agent = await client.agents.register(agent_profile)
        # アクションを実行
        result = await client.actions.execute(action)
        # ログを作成
        await client.logs.create("info", {"message": "test"})
"""

from typing import Any

from .base import BaseClient, RetryConfig
from .resources import ActionsResource, AgentsResource, LogsResource

# Cache for reusing BaseClient instances across MarketplaceClients
# BaseClientインスタンスのキャッシュ（MarketplaceClient間で再利用）
#
# 同じbase_urlとパラメータを持つ複数のMarketplaceClientインスタンスは、
# 同じBaseClientインスタンスを共有し、接続プールを効率的に利用します。
_base_client_cache: dict[str, BaseClient] = {}


def _get_or_create_base_client(
    base_url: str,
    timeout: float | None = 60.0,
    retry_config: RetryConfig | None = None,
) -> BaseClient:
    """Get cached BaseClient or create new one.

    キャッシュされたBaseClientを取得、または新規作成します。

    この関数は、同じパラメータを持つ複数のMarketplaceClientインスタンスが
    同じBaseClientインスタンスを共有できるようにします。
    これにより、HTTPコネクションプールが効率的に利用されます。

    Args:
        base_url: クライアントのベースURL（例: "http://localhost:8000"）
        timeout: リクエストタイムアウト（秒）（デフォルト: 60.0）
        retry_config: リトライ設定（オプション）

    Returns:
        BaseClient: キャッシュされた、または新規作成されたBaseClientインスタンス

    """
    # パラメータからキャッシュキーを作成
    # retry_configのidentity（メモリアドレス）を使用して一意性を保証
    cache_key = (
        f"{base_url}:{timeout}:{id(retry_config) if retry_config else 'default'}"
    )

    # キャッシュに存在しない場合は新規作成
    if cache_key not in _base_client_cache:
        _base_client_cache[cache_key] = BaseClient(base_url, timeout, retry_config)

    return _base_client_cache[cache_key]


class MarketplaceClient:
    """Main client for the Magentic Marketplace API with modular resource access.

    モジュラーリソースアクセスを持つMagentic Marketplace API用のメインクライアント

    このクライアントは、エージェントがマーケットプレイスサーバーと通信するための
    高レベルAPIを提供します。リソースベースのアーキテクチャにより、
    各エンドポイントグループ（エージェント、アクション、ログ）への
    構造化されたアクセスが可能です。

    クライアント-サーバー通信の流れ:
      1. エージェントがMarketplaceClientを使用してアクションを実行
      2. クライアントがHTTPリクエストをサーバーに送信
      3. サーバーがリクエストを検証し、プロトコルに委譲
      4. プロトコルがデータベースを操作してアクションを記録
      5. 結果がサーバーを通じてクライアントに返される

    使用例:
        async with MarketplaceClient("http://localhost:8000") as client:
            # エージェントを取得
            agent = await client.agents.get("agent_id")
            # アクションを実行
            result = await client.actions.execute(action)
            # ログを作成
            log = await client.logs.create("info", {"message": "test"})
            # ロガーと統合
            from ..logger import MarketplaceLogger
            logger = MarketplaceLogger(__name__, client)
            logger.info("Pythonロギングとデータベースの両方にログ記録")
    """

    def __init__(
        self,
        base_url: str,
        timeout: float | None = 60.0,
        retry_config: RetryConfig | None = None,
    ):
        """Initialize marketplace client with resource modules and cached base client.

        リソースモジュールとキャッシュされたベースクライアントでマーケットプレイスクライアントを初期化。

        Args:
            base_url: マーケットプレイスサーバーのベースURL
                     （例: "http://localhost:8000"）
            timeout: リクエストタイムアウト（秒）（デフォルト: 60.0）
            retry_config: リトライ設定（オプション、失敗時の再試行を制御）

        """
        # キャッシュされたBaseClientインスタンスを使用
        # 同じパラメータを持つ他のMarketplaceClientインスタンスと共有される
        self._base_client = _get_or_create_base_client(base_url, timeout, retry_config)
        self._auth_token: str | None = None

        # リソースモジュールをベースクライアントで初期化
        # 各リソースは特定のエンドポイントグループへのアクセスを提供
        self.agents = AgentsResource(self._base_client)  # /agents/* エンドポイント
        self.actions = ActionsResource(self._base_client)  # /actions/* エンドポイント
        self.logs = LogsResource(self._base_client)  # /logs/* エンドポイント

    async def __aenter__(self):
        """Async context manager entry.

        非同期コンテキストマネージャーのエントリーポイント。
        接続を確立し、クライアントを使用可能な状態にします。

        """
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        """Async context manager exit.

        非同期コンテキストマネージャーの終了処理。
        接続を閉じてリソースをクリーンアップします。

        """
        await self.close()

    async def connect(self):
        """Connect the underlying base client (increments reference count).

        基底ベースクライアントを接続します（参照カウントを増加）。

        BaseClientは参照カウント方式で管理されるため、
        複数のMarketplaceClientインスタンスが同じBaseClientを共有できます。

        """
        await self._base_client.connect()

    async def close(self):
        """Close the underlying base client (decrements reference count).

        基底ベースクライアントを閉じます（参照カウントを減少）。

        参照カウントがゼロになった場合のみ、実際の接続が閉じられます。

        """
        await self._base_client.close()

    def set_token(self, token: str) -> None:
        """Set the authentication token for requests on all resources.

        全リソースのリクエストに認証トークンを設定します。

        このメソッドは、クライアントが認証を必要とするエンドポイントにアクセスする前に
        呼び出される必要があります。トークンは全てのリソース（agents, actions, logs）に
        伝播されます。

        Args:
            token: 使用する認証トークン

        """
        self._auth_token = token
        self.agents.set_token(token)  # エージェントリソースにトークンを設定
        self.actions.set_token(token)  # アクションリソースにトークンを設定
        self.logs.set_token(token)  # ログリソースにトークンを設定

    @property
    def auth_token(self) -> str | None:
        """Get the current authentication token from agents resource.

        エージェントリソースから現在の認証トークンを取得します。

        Returns:
            str | None: 認証トークン、または未設定の場合はNone

        """
        return self.agents.auth_token

    async def health_check(self) -> dict[str, Any]:
        """Check server health.

        サーバーのヘルスチェックを実行します。

        このメソッドは、サーバーが正常に動作しているか、リクエストに応答できるかを
        確認するために使用されます。MarketplaceLauncherの起動時にも使用されます。

        Returns:
            dict: ヘルスチェックレスポンス（サーバーの状態情報）

        """
        return await self._base_client.request("GET", "/health")
