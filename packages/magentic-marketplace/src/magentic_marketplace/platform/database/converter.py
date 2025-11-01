"""Database converter for migrating PostgreSQL data to SQLite.

PostgreSQLからSQLiteへのデータ移行用データベースコンバーター。

このモジュールは、本番環境のPostgreSQLデータベースから実験データをエクスポートし、
ポータブルなSQLiteファイルに変換する機能を提供します。

主な用途:
1. 実験結果の共有: SQLiteファイルは単一のファイルで、簡単に共有可能
2. オフライン分析: PostgreSQLサーバーがなくてもデータを分析できる
3. バックアップ: 実験データの永続的なスナップショットを作成
4. 配布: 研究論文のデータセットとして配布可能

変換プロセス:
1. PostgreSQLから全データを読み込み（agents、actions、logs）
2. row_index順にソート（データの順序を保証）
3. SQLiteに順番に挿入（rowidが自動割り当て）
4. 検証: 行数とインデックスの一致を確認

重要な保証:
- データの完全性: すべての行が正確にコピーされる
- 順序の保持: PostgreSQLのrow_indexとSQLiteのrowidが一致
- 検証済み: 変換後に自動検証を実行
"""

import logging
from pathlib import Path

from .models import ActionRow, AgentRow, LogRow
from .postgresql.postgresql import PostgreSQLDatabaseController
from .sqlite import connect_to_sqlite_database
from .sqlite.sqlite import SQLiteDatabaseController

logger = logging.getLogger(__name__)


class DatabaseConverter:
    """Convert PostgreSQL database to SQLite format.

    PostgreSQLデータベースをSQLite形式に変換するコンバーター。

    このクラスは、1つのPostgreSQL実験スキーマを単一のSQLiteファイルに変換します。
    データの完全性と順序を保証しながら、効率的に大量のデータを移行します。

    使用パターン:
        converter = DatabaseConverter(postgres_db, "experiment.db")
        db_path = await converter.convert()
        # experiment.db ファイルに実験データが保存される
    """

    def __init__(
        self,
        source_db: PostgreSQLDatabaseController,
        target_path: str | Path,
    ):
        """Initialize converter.

        コンバーターを初期化。

        Args:
            source_db: ソースとなるPostgreSQLデータベースコントローラー
            target_path: SQLiteデータベースファイルを作成するパス

        注意:
            target_path に既にファイルが存在する場合、上書きされます。

        """
        self.source_db = source_db
        # PostgreSQLデータベースのコントローラー（データの読み込み元）
        self.target_path = Path(target_path)
        # SQLiteファイルの保存先（Pathオブジェクトに変換）

    async def convert(self) -> Path:
        """Convert PostgreSQL database to SQLite.

        PostgreSQLデータベースをSQLiteに変換する主要メソッド。

        変換の流れ:
        1. 親ディレクトリの作成（存在しない場合）
        2. SQLiteデータベースファイルの作成
        3. テーブルごとにデータをコピー（agents → actions → logs）
        4. 変換結果の検証（行数、インデックスの一致確認）

        Returns:
            作成されたSQLiteデータベースファイルのパス

        Raises:
            ValueError: 検証が失敗した場合（行数不一致、インデックス不一致など）

        重要な順序:
            テーブルは agents → actions → logs の順でコピーされます。
            この順序は外部キー制約や論理的依存関係に基づいています。

        """
        logger.info(f"Starting conversion to {self.target_path}")

        # Ensure parent directory exists
        # 親ディレクトリが存在しない場合は作成（例: exports/ ディレクトリ）
        self.target_path.parent.mkdir(parents=True, exist_ok=True)

        # Create SQLite database
        # SQLiteデータベースに接続（ファイルが存在しない場合は自動作成）
        async with connect_to_sqlite_database(str(self.target_path)) as target_db:
            # Copy data in order
            # データを順番にコピー（agents → actions → logs）
            await self._copy_agents(target_db)
            await self._copy_actions(target_db)
            await self._copy_logs(target_db)

            # Verify the conversion
            # 変換が正しく行われたかを検証
            await self._verify_conversion(target_db)

        logger.info(f"Conversion completed successfully: {self.target_path}")
        return self.target_path

    async def _copy_agents(self, target_db: SQLiteDatabaseController) -> None:
        """Copy agents from PostgreSQL to SQLite in row_index order.

        PostgreSQLからSQLiteにエージェントをrow_index順でコピー。

        重要なポイント:
        - PostgreSQLのrow_index順にソートしてコピー
        - SQLiteは挿入順にrowidを自動割り当て
        - 結果としてPostgreSQLのrow_index == SQLiteのrowid となる

        この順序の保持は、after_indexクエリの互換性に必須です。
        """
        logger.info("Copying agents table...")

        # Get all agents ordered by row_index
        # PostgreSQLから全エージェントを取得（row_index順）
        agents = await self.source_db.agents.get_all()

        # Sort by index to ensure correct order (should already be sorted, but be explicit)
        # インデックス順に明示的にソート（念のため、通常は既にソート済み）
        agents.sort(key=lambda a: a.index if a.index is not None else 0)

        # Prepare rows without the index (SQLite will auto-assign rowid)
        # indexフィールドなしで新しい行を準備（SQLiteがrowidを自動割り当て）
        new_agents = [
            AgentRow(
                id=agent.id,
                created_at=agent.created_at,
                data=agent.data,
                agent_embedding=agent.agent_embedding,
            )
            for agent in agents
        ]

        # Bulk insert all agents using create_many
        # create_many を使用してすべてのエージェントを一括挿入（効率的）
        await target_db.agents.create_many(new_agents)

        logger.info(f"Copied {len(agents)} agents")

    async def _copy_actions(self, target_db: SQLiteDatabaseController) -> None:
        """Copy actions from PostgreSQL to SQLite in row_index order.

        PostgreSQLからSQLiteにアクションをrow_index順でコピー。
        エージェントと同じロジックで、順序を保持しながらコピーします。
        """
        logger.info("Copying actions table...")

        # Get all actions ordered by row_index
        # PostgreSQLから全アクションを取得
        actions = await self.source_db.actions.get_all()

        # Sort by index to ensure correct order
        # インデックス順にソート
        actions.sort(key=lambda a: a.index if a.index is not None else 0)

        # Prepare rows without the index (SQLite will auto-assign rowid)
        # indexなしで新しい行を準備
        new_actions = [
            ActionRow(
                id=action.id,
                created_at=action.created_at,
                data=action.data,
            )
            for action in actions
        ]

        # Bulk insert all actions using create_many
        # 一括挿入
        await target_db.actions.create_many(new_actions)

        logger.info(f"Copied {len(actions)} actions")

    async def _copy_logs(self, target_db: SQLiteDatabaseController) -> None:
        """Copy logs from PostgreSQL to SQLite in row_index order.

        PostgreSQLからSQLiteにログをrow_index順でコピー。
        エージェント、アクションと同じロジックです。
        """
        logger.info("Copying logs table...")

        # Get all logs ordered by row_index
        # PostgreSQLから全ログを取得
        logs = await self.source_db.logs.get_all()

        # Sort by index to ensure correct order
        # インデックス順にソート
        logs.sort(key=lambda log: log.index if log.index is not None else 0)

        # Prepare rows without the index (SQLite will auto-assign rowid)
        # indexなしで新しい行を準備
        new_logs = [
            LogRow(
                id=log.id,
                created_at=log.created_at,
                data=log.data,
            )
            for log in logs
        ]

        # Bulk insert all logs using create_many
        # 一括挿入
        await target_db.logs.create_many(new_logs)

        logger.info(f"Copied {len(logs)} logs")

    async def _verify_conversion(self, target_db: SQLiteDatabaseController) -> None:
        """Verify that the conversion was successful.

        変換が正常に完了したかを検証。

        検証内容:
        1. 行数の一致: ソースとターゲットで行数が同じか
        2. インデックスの一致: PostgreSQLのrow_index == SQLiteのrowid か
        3. IDの一致: 各行のIDが正しくコピーされているか

        Raises:
            ValueError: 検証が失敗した場合（不一致が見つかった場合）

        重要性:
            この検証により、データの完全性と正確性が保証されます。
            検証失敗時はデータベースファイルを使用すべきではありません。

        """
        logger.info("Verifying conversion...")

        # Verify agents
        # エージェントテーブルを検証
        await self._verify_table(
            "agents",
            await self.source_db.agents.get_all(),
            await target_db.agents.get_all(),
        )

        # Verify actions
        # アクションテーブルを検証
        await self._verify_table(
            "actions",
            await self.source_db.actions.get_all(),
            await target_db.actions.get_all(),
        )

        # Verify logs
        # ログテーブルを検証
        await self._verify_table(
            "logs",
            await self.source_db.logs.get_all(),
            await target_db.logs.get_all(),
        )

        logger.info("Verification completed successfully")

    async def _verify_table(
        self,
        table_name: str,
        source_rows: list[AgentRow] | list[ActionRow] | list[LogRow],
        target_rows: list[AgentRow] | list[ActionRow] | list[LogRow],
    ) -> None:
        """Verify a single table.

        単一テーブルの検証を実行。

        Args:
            table_name: 検証するテーブルの名前（ログ出力用）
            source_rows: PostgreSQLからの行（row_index付き）
            target_rows: SQLiteからの行（rowid as index）

        Raises:
            ValueError: 検証が失敗した場合

        検証ステップ:
        1. 行数が同じか確認
        2. 両方をインデックス順にソート
        3. 各行について、インデックスとIDが一致するか確認

        """
        # Check row counts
        # 行数の一致を確認
        if len(source_rows) != len(target_rows):
            raise ValueError(
                f"{table_name}: Row count mismatch - "
                f"PostgreSQL has {len(source_rows)}, SQLite has {len(target_rows)}"
            )

        # Sort both by index to compare
        # 両方をインデックス順にソート（比較のため）
        source_rows.sort(key=lambda r: r.index if r.index is not None else 0)
        target_rows.sort(key=lambda r: r.index if r.index is not None else 0)

        # Verify each row
        # 各行を検証
        for source_row, target_row in zip(source_rows, target_rows, strict=True):
            # Check that SQLite rowid matches PostgreSQL row_index
            # SQLiteのrowidがPostgreSQLのrow_indexと一致するか確認
            if source_row.index != target_row.index:
                raise ValueError(
                    f"{table_name}: Index mismatch for row with id={source_row.id} - "
                    f"PostgreSQL row_index={source_row.index}, SQLite rowid={target_row.index}"
                )

            # Check that IDs match
            # IDが一致するか確認
            if source_row.id != target_row.id:
                raise ValueError(
                    f"{table_name}: ID mismatch at index {source_row.index} - "
                    f"PostgreSQL id={source_row.id}, SQLite id={target_row.id}"
                )

        logger.info(
            f"{table_name}: Verified {len(source_rows)} rows - all indices match"
        )


async def convert_postgres_to_sqlite(
    source_db: PostgreSQLDatabaseController,
    target_path: str | Path,
) -> Path:
    """Convert a PostgreSQL database to SQLite.

    PostgreSQLデータベースをSQLiteに変換する便利関数。

    DatabaseConverterのインスタンスを作成し、変換を実行します。
    この関数は、より簡潔なAPIを提供するためのヘルパーです。

    Args:
        source_db: ソースとなるPostgreSQLデータベースコントローラー
        target_path: SQLiteデータベースファイルを作成するパス

    Returns:
        作成されたSQLiteデータベースファイルのパス

    Raises:
        ValueError: 検証が失敗した場合

    使用例:
        from platform.database.postgresql import PostgreSQLDatabaseController
        from platform.database.converter import convert_postgres_to_sqlite

        async with PostgreSQLDatabaseController(...) as pg_db:
            sqlite_path = await convert_postgres_to_sqlite(pg_db, "export.db")
            print(f"Exported to {sqlite_path}")

    """
    converter = DatabaseConverter(source_db, target_path)
    return await converter.convert()
