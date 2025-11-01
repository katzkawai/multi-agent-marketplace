"""Base database classes and interfaces for the marketplace.

マーケットプレイス用の基底データベースクラスとインターフェース。

このモジュールは、データベース操作の抽象インターフェースを定義します。
具体的なデータベース実装（SQLite、PostgreSQL）はこれらのインターフェースを実装し、
アプリケーションコードはこの抽象層を通じてデータベースにアクセスします。

抽象化の利点:
1. データベース切り替えが容易（SQLite ⇔ PostgreSQL）
2. テストが簡単（モックデータベースを作成可能）
3. ビジネスロジックとデータベース実装の分離
4. 複数のデータベースバックエンドを同時にサポート

主要なクラス:
- TableController: テーブルごとのCRUD操作を定義する抽象基底クラス
- AgentTableController: Agentテーブル専用のコントローラー
- ActionTableController: Actionテーブル専用のコントローラー
- LogTableController: Logテーブル専用のコントローラー
- BaseDatabaseController: すべてのテーブルコントローラーを所有する統合コントローラー

設計パターン:
- Repository パターン: データアクセスロジックをカプセル化
- Abstract Base Class (ABC): サブクラスで実装すべきメソッドを明確化
- Generic Types: 型安全性を保ちながら再利用可能なコードを実現
"""

from abc import ABC, abstractmethod
from typing import Any, Generic, TypeVar

from .models import ActionRow, AgentRow, LogRow
from .queries import Query, RangeQueryParams

TableEntryType = TypeVar("TableEntryType")
# ジェネリック型変数: TableController で扱うエントリ（行）の型をパラメータ化


class DatabaseTooBusyError(Exception):
    """Raised when database is too busy to handle requests (connection pool exhausted, timeouts, etc).

    データベースが多忙で要求を処理できない場合に発生する例外。

    この例外は以下の状況で発生します:
    - コネクションプールが枯渇（すべての接続が使用中）
    - データベースクエリがタイムアウト
    - データベースサーバーが過負荷状態

    エラーハンドリングの推奨:
    - リトライ: 一時的な問題の可能性があるため、指数バックオフでリトライ
    - 負荷軽減: 同時実行数を減らす
    - 監視: 頻発する場合はデータベースのリソース増強を検討
    """

    def __init__(self, message: str = "Database is too busy to handle the request"):
        """Initialize the DatabaseTooBusyError with a message.

        Args:
            message: エラーメッセージ（デフォルトは標準メッセージ）

        """
        self.message = message
        super().__init__(self.message)


class TableController(ABC, Generic[TableEntryType]):  # noqa: UP046
    """Abstract base class for table-specific CRUD operations.

    テーブル固有のCRUD操作のための抽象基底クラス。

    このジェネリックインターフェースは、データベーステーブルを管理するコントローラーの
    契約を定義します。Create（作成）、Read（読取）、Update（更新）、Delete（削除）の
    非同期メソッドを提供します。

    ジェネリック型パラメータ:
    - TableEntryType: テーブルの行またはエントリを表す型（例: AgentRow、ActionRow）

    サブクラスの実装:
    具体的なテーブル/エンティティごとに、これらのメソッドを実装する必要があります。
    SQLite実装とPostgreSQL実装が存在し、同じインターフェースを共有します。

    非同期設計の理由:
    - I/O待機中に他のタスクを実行可能（並行性向上）
    - データベース接続を効率的に使用
    - 大量のエージェントが同時にアクションを実行する実験に対応
    """

    @abstractmethod
    async def create(self, item: TableEntryType) -> TableEntryType:
        """Create a new item in the Table.

        テーブルに新しいアイテムを作成。

        Args:
            item: 作成するアイテム（例: AgentRow、ActionRow）

        Returns:
            作成されたアイテム（データベースが付与したindex付き）

        """
        pass

    @abstractmethod
    async def create_many(
        self, items: list[TableEntryType], batch_size: int = 1000
    ) -> None:
        """Create multiple items efficiently in batches.

        複数のアイテムを効率的にバッチで作成。

        大量のデータを挿入する際、1件ずつ挿入するよりもバッチ挿入の方が
        はるかに高速です。この関数は指定されたバッチサイズでデータを分割し、
        効率的に挿入します。

        Args:
            items: 作成するアイテムのリスト
            batch_size: バッチあたりの挿入件数（デフォルト: 1000）

        使用例:
            大量のエージェントを一度にデータベースに登録する際に使用。

        """
        pass

    @abstractmethod
    async def get_by_id(self, item_id: str) -> TableEntryType | None:
        """Retrieve an item by its ID.

        IDでアイテムを取得。

        Args:
            item_id: 取得するアイテムのID

        Returns:
            見つかった場合はアイテム、見つからない場合はNone

        """
        pass

    @abstractmethod
    async def get_all(
        self, params: RangeQueryParams | None = None, batch_size: int = 1000
    ) -> list[TableEntryType]:
        """Retrieve all items with optional pagination, fetching in batches.

        すべてのアイテムを取得（オプションでページネーション）。

        大量のデータを扱う際、メモリ効率のためにバッチ単位で取得します。
        RangeQueryParams を使用して、特定の範囲（時刻、インデックス）のみを
        フィルタリングすることも可能です。

        Args:
            params: 範囲クエリパラメータ（フィルタリング用、任意）
            batch_size: バッチあたりの取得件数（デフォルト: 1000）

        Returns:
            マッチするすべてのアイテムのリスト

        """
        pass

    @abstractmethod
    async def find(
        self, query: Query, params: RangeQueryParams | None = None
    ) -> list[TableEntryType]:
        """Find items matching query with range parameters.

        クエリと範囲パラメータにマッチするアイテムを検索。

        Args:
            query: 検索クエリ（フィールドと値の条件）
            params: 範囲クエリパラメータ（さらなるフィルタリング用、任意）

        Returns:
            マッチするアイテムのリスト

        """
        pass

    @abstractmethod
    async def update(
        self, item_id: str, updates: dict[str, Any]
    ) -> TableEntryType | None:
        """Update an item by ID with the given field updates.

        IDでアイテムを更新。

        Args:
            item_id: 更新するアイテムのID
            updates: 更新するフィールドと値の辞書

        Returns:
            更新されたアイテム、見つからない場合はNone

        """
        pass

    @abstractmethod
    async def delete(self, item_id: str) -> bool:
        """Delete an item by ID. Returns True if deleted, False if not found.

        IDでアイテムを削除。

        Args:
            item_id: 削除するアイテムのID

        Returns:
            削除された場合はTrue、見つからない場合はFalse

        """
        pass

    @abstractmethod
    async def count(self) -> int:
        """Get the total count of items.

        アイテムの総数を取得。

        Returns:
            テーブル内のアイテムの総数

        """
        pass


class AgentTableController(
    TableController[AgentRow],
):
    """Abstract controller for Agent operations.

    Agentテーブル操作のための抽象コントローラー。

    TableController[AgentRow] を継承し、Agent固有の操作を追加定義します。
    """

    @abstractmethod
    async def find_agents_by_id_pattern(self, id_pattern: str) -> list[str]:
        """Find all agent IDs that contain the given ID pattern.

        指定されたIDパターンを含むすべてのエージェントIDを検索。

        Args:
            id_pattern: 検索するIDパターン（例: "Agent"、"Customer"）

        Returns:
            パターンを含むエージェントIDのリスト

        使用例:
            すべての顧客エージェントを検索: find_agents_by_id_pattern("Customer")

        """
        pass


class ActionTableController(
    TableController[ActionRow],
):
    """Abstract controller for Action operations.

    Actionテーブル操作のための抽象コントローラー。

    TableController[ActionRow] を継承し、基本的なCRUD操作を提供します。
    Action固有の追加操作は現在定義されていません。
    """


class LogTableController(
    TableController[LogRow],
):
    """Abstract controller for Log operations.

    Logテーブル操作のための抽象コントローラー。

    TableController[LogRow] を継承し、基本的なCRUD操作を提供します。
    Log固有の追加操作は現在定義されていません。
    """


class BaseDatabaseController(ABC):
    """Database controller that owns all entity controllers.

    すべてのエンティティコントローラーを所有するデータベースコントローラー。

    このクラスは、データベース全体へのアクセスポイントを提供します。
    個別のテーブルコントローラー（agents、actions、logs）を管理し、
    統一されたインターフェースでアクセスできるようにします。

    設計パターン:
    - Facade パターン: 複雑なサブシステム（複数のテーブル）への統一インターフェース
    - Dependency Injection: コントローラーを注入可能（テスト容易性）

    使用例:
        async with PostgreSQLDatabaseController(...) as db:
            # エージェントを作成
            await db.agents.create(agent_row)
            # アクションを記録
            await db.actions.create(action_row)
            # ログを保存
            await db.logs.create(log_row)
    """

    @property
    @abstractmethod
    def agents(self) -> AgentTableController:
        """Get the agent controller.

        Agentテーブルのコントローラーを取得。

        Returns:
            AgentTableController インスタンス

        """
        pass

    @property
    @abstractmethod
    def actions(self) -> ActionTableController:
        """Get the Action controller.

        Actionテーブルのコントローラーを取得。

        Returns:
            ActionTableController インスタンス

        """
        pass

    @property
    @abstractmethod
    def logs(self) -> LogTableController:
        """Get the log record controller.

        Logテーブルのコントローラーを取得。

        Returns:
            LogTableController インスタンス

        """
        pass

    @property
    @abstractmethod
    def row_index_column(self) -> str:
        """Get the name of the row index column for this database.

        このデータベースの行インデックスカラム名を取得。

        データベースによって行番号を表すカラム名が異なります:
        - SQLite: "rowid" (自動生成される内部カラム)
        - PostgreSQL: "row_index" (明示的に定義されたカラム)

        Returns:
            行インデックスカラムの名前

        """
        pass

    @abstractmethod
    async def execute(self, command: Any) -> Any:
        """Execute an arbitrary database command.

        任意のデータベースコマンドを実行。

        低レベルのデータベース操作が必要な場合に使用します。
        通常はテーブルコントローラーのメソッドを使用することを推奨。

        Args:
            command: 実行するデータベースコマンド（SQLクエリなど）

        Returns:
            コマンドの実行結果

        """
        pass
