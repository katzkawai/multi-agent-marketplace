"""Simple marketplace protocol implementation.

シンプルなマーケットプレイスのプロトコル実装。

このモジュールは、マーケットプレイスの中核となるプロトコルを定義します。
プロトコルは、エージェントが実行できるアクションのセット、各アクションの検証ロジック、
実行ロジック、およびデータベースの初期化を管理します。

プロトコルの役割:
1. 利用可能なアクションの定義（Search、SendMessage、FetchMessages）
2. アクション実行前の検証（パラメータの妥当性チェック）
3. アクション実行の委譲（各アクションの専用実行関数を呼び出す）
4. データベースインデックスの設定（クエリパフォーマンスの最適化）
"""

from magentic_marketplace.platform.database.base import BaseDatabaseController
from magentic_marketplace.platform.protocol.base import BaseMarketplaceProtocol
from magentic_marketplace.platform.shared.models import (
    ActionExecutionRequest,
    ActionExecutionResult,
    AgentProfile,
)

from ..actions import (
    ActionAdapter,
    FetchMessages,
    Search,
    SendMessage,
)
from .fetch_messages import execute_fetch_messages
from .search import execute_search
from .send_message import execute_send_message


class SimpleMarketplaceProtocol(BaseMarketplaceProtocol):
    """Marketplace protocol."""

    """マーケットプレイスプロトコル。

    エージェント間の相互作用を管理し、マーケットプレイスのルールを実施します。
    このプロトコルは、すべてのアクションリクエストを受け取り、検証し、実行します。
    """

    def __init__(self):
        """Initialize the marketplace protocol."""
        """マーケットプレイスプロトコルを初期化します。"""

    def get_actions(self):
        """Define available actions in the marketplace."""
        """マーケットプレイスで利用可能なアクションを定義します。

        Returns:
            エージェントが実行できるアクションクラスのリスト:
            - SendMessage: エージェント間でメッセージを送信
            - FetchMessages: 受信メッセージを取得
            - Search: ビジネスエージェントを検索
        """
        return [SendMessage, FetchMessages, Search]

    async def initialize(self, database: BaseDatabaseController) -> None:
        """Initialize SimpleMarketplace-specific database indexes.

        Creates functional indexes on frequently queried JSONB paths to improve
        query performance for SendMessage action parameters.

        Args:
            database: The database controller instance

        """
        """SimpleMarketplace固有のデータベースインデックスを初期化します。

        頻繁にクエリされるJSONBパスに関数インデックスを作成し、
        SendMessageアクションのパラメータのクエリパフォーマンスを向上させます。

        データベース初期化の重要性:
        1. パフォーマンス: インデックスによりメッセージ取得が高速化
        2. スケーラビリティ: 大規模実験でも効率的なクエリが可能
        3. 複合インデックス: created_atとrow_indexを含めてソート効率を最適化

        Args:
            database: データベースコントローラインスタンス
        """
        # Import here to avoid circular dependencies
        # 循環依存を避けるためにここでインポート
        from magentic_marketplace.platform.database.postgresql.postgresql import (
            PostgreSQLDatabaseController,
        )

        # Only create indexes for PostgreSQL databases
        # PostgreSQLデータベースの場合のみインデックスを作成
        # （SQLiteはJSONBインデックスをサポートしていないため）
        if isinstance(database, PostgreSQLDatabaseController):
            schema = database._schema

            # Create functional indexes for SendMessage action parameters
            # SendMessageアクションのパラメータ用の関数インデックスを作成
            # These indexes extract the JSON values and allow PostgreSQL to use
            # index scans instead of sequential scans for filtered queries
            # これらのインデックスはJSON値を抽出し、PostgreSQLがフィルタ付きクエリで
            # シーケンシャルスキャンの代わりにインデックススキャンを使用できるようにする
            # Composite indexes include created_at and row_index for efficient sorting
            # 複合インデックスはcreated_atとrow_indexを含み、効率的なソートを実現
            row_index_col = database.row_index_column
            index_sql = f"""
CREATE INDEX IF NOT EXISTS actions_to_agent_id_idx
  ON {schema}.actions (
    (jsonb_path_query_first(data, '$."request"."parameters"."to_agent_id"'::jsonpath) #>> '{{}}'),
    {row_index_col} DESC
  );
-- メッセージの受信者IDでフィルタリングするためのインデックス（FetchMessages用）

CREATE INDEX IF NOT EXISTS actions_from_agent_id_idx
  ON {schema}.actions (
    (jsonb_path_query_first(data, '$."request"."parameters"."from_agent_id"'::jsonpath) #>> '{{}}'),
    {row_index_col} DESC
  );
-- メッセージの送信者IDでフィルタリングするためのインデックス（分析用）

CREATE INDEX IF NOT EXISTS actions_request_name_idx
  ON {schema}.actions (
    (jsonb_path_query_first(data, '$."request"."name"'::jsonpath) #>> '{{}}'),
    {row_index_col} DESC
  );
-- アクション名（"SendMessage"、"FetchMessages"など）でフィルタリングするためのインデックス

CREATE INDEX IF NOT EXISTS actions_fetch_messages_idx
  ON {schema}.actions (
    (jsonb_path_query_first(data, '$."request"."name"'::jsonpath) #>> '{{}}'),
    (jsonb_path_query_first(data, '$."request"."parameters"."to_agent_id"'::jsonpath) #>> '{{}}'),
    {row_index_col} DESC
  );
-- FetchMessagesアクション専用の複合インデックス（アクション名と受信者IDの組み合わせ）
-- これにより「特定エージェント宛てのメッセージを取得」というクエリが最適化される
"""
            await database.execute(index_sql)

    async def execute_action(
        self,
        *,
        agent: AgentProfile,
        action: ActionExecutionRequest,
        database: BaseDatabaseController,
    ) -> ActionExecutionResult:
        """Execute an action."""
        """アクションを実行します。

        このメソッドは、マーケットプレイスのすべてのアクション実行のエントリポイントです。
        アクション実行フロー:
        1. ActionAdapterでリクエストパラメータを適切なアクションクラスに解析
        2. アクションタイプに基づいて対応する実行関数に委譲
        3. 実行関数が検証を行い、データベース操作を実行
        4. 成功またはエラーの結果を返す

        Args:
            agent: アクションを実行するエージェントのプロフィール
            action: 実行するアクションのリクエスト（名前とパラメータを含む）
            database: データベース操作用のコントローラ

        Returns:
            ActionExecutionResult: 実行結果（成功時はcontent、失敗時はerrorを含む）

        Raises:
            ValueError: 未知のアクションタイプの場合
        """
        # Parse the action parameters into a strongly-typed action object
        # アクションパラメータを強く型付けされたアクションオブジェクトに解析
        # ActionAdapterは "type" フィールドを使って適切なクラス（SendMessage、FetchMessages、Search）を選択
        parsed_action = ActionAdapter.validate_python(action.parameters)

        # Delegate to the appropriate executor based on action type
        # アクションタイプに基づいて適切な実行関数に委譲
        if isinstance(parsed_action, SendMessage):
            # メッセージ送信: 送信先エージェントの存在確認、メッセージ内容の検証を実行
            return await execute_send_message(parsed_action, database)

        elif isinstance(parsed_action, FetchMessages):
            # メッセージ取得: エージェント宛てのメッセージをデータベースから取得
            return await execute_fetch_messages(parsed_action, agent, database)

        elif isinstance(parsed_action, Search):
            # ビジネス検索: 顧客のクエリに基づいてビジネスエージェントを検索
            return await execute_search(
                search=parsed_action, agent=agent, database=database
            )
        else:
            # Unknown action type - this should not happen if ActionAdapter is working correctly
            # 未知のアクションタイプ - ActionAdapterが正しく動作していれば発生しない
            raise ValueError(f"Unknown action type: {parsed_action.type}")
