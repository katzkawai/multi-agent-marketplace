#!/usr/bin/env python3
"""List marketplace experiments stored in PostgreSQL.

PostgreSQLに保存されたマーケットプレイス実験を一覧表示する

このモジュールは、PostgreSQLデータベースに保存されたすべての実験を
検索し、実験名、実行時刻、データ量などの情報を表示します。
"""

import sys  # システム終了とエラー出力用
from datetime import UTC, datetime  # タイムゾーン処理とタイムスタンプ用

import asyncpg  # PostgreSQL非同期接続ライブラリ


def format_datetime_local(dt: datetime) -> str:
    """Format datetime in local timezone with timezone abbreviation.

    日時をローカルタイムゾーンでフォーマットする関数

    UTC時刻をローカルタイムゾーンに変換し、読みやすい形式で表示します。
    例: "2025-10-09 14:23:45 JST"

    Args:
        dt: フォーマットする日時（タイムゾーン情報を持つべき）

    Returns:
        フォーマットされた文字列（例: "2025-10-09 14:23:45 PDT"）

    """
    # タイムゾーン情報がない場合はUTCとして扱う
    # Convert to local timezone if needed
    if dt.tzinfo is None:
        # Assume UTC if naive
        # タイムゾーン情報がない「naive datetime」の場合、UTCと仮定
        dt = dt.replace(tzinfo=UTC)

    # ローカルタイムゾーンに変換（引数なしでastimezone()を呼ぶとシステムのタイムゾーンを使用）
    # Get local timezone by using astimezone() without arguments
    local_dt = dt.astimezone()

    # タイムゾーンの略称を含めてフォーマット
    # Format with timezone abbreviation
    # %Y=年、%m=月、%d=日、%H=時、%M=分、%S=秒、%Z=タイムゾーン略称
    return local_dt.strftime("%Y-%m-%d %H:%M:%S %Z")


async def list_experiments(
    host: str = "localhost",
    port: int = 5432,
    database: str = "marketplace",
    user: str = "postgres",
    password: str | None = None,
    limit: int | None = None,
):
    """List all marketplace experiments from PostgreSQL schemas.

    PostgreSQLスキーマからすべてのマーケットプレイス実験を一覧表示する非同期関数

    この関数は以下の処理を実行します：
    1. PostgreSQLデータベースに接続
    2. すべてのユーザー定義スキーマを検索（システムスキーマを除外）
    3. 各スキーマの詳細情報を取得（テーブル数、データ件数、アクティビティ時刻など）
    4. 実験を最新順にソートして表示

    各実験について以下の情報を表示します：
    - 実験名（スキーマ名）
    - 最初のエージェント登録時刻
    - 最後のアクティビティ時刻
    - データ件数（エージェント、アクション、ログ）
    - 使用されたLLMプロバイダー

    Args:
        host: PostgreSQLサーバーのホスト名（デフォルト: "localhost"）
        port: PostgreSQLサーバーのポート番号（デフォルト: 5432）
        database: データベース名（デフォルト: "marketplace"）
        user: データベースユーザー名（デフォルト: "postgres"）
        password: データベースパスワード（デフォルト: None）
        limit: 表示する実験の最大数（Noneの場合はすべて表示）

    """
    # エラーハンドリング用のtryブロック
    try:
        # ステップ1: PostgreSQLデータベースに接続
        # Connect to PostgreSQL
        conn = await asyncpg.connect(
            host=host,
            port=port,
            database=database,
            user=user,
            password=password,
        )

        # 接続のクリーンアップを保証するためのtryブロック
        try:
            # ステップ2: すべてのユーザー定義スキーマを検索
            # Query for all schemas excluding system schemas
            # システムスキーマ（pg_catalog、information_schemaなど）を除外して
            # ユーザーが作成した実験スキーマだけを取得する
            query = """
            SELECT
                s.schema_name,
                COUNT(DISTINCT t.table_name) as table_count
            FROM information_schema.schemata s
            LEFT JOIN information_schema.tables t
                ON s.schema_name = t.table_schema
            WHERE s.schema_name NOT IN ('pg_catalog', 'information_schema', 'public', 'pg_toast')
                AND s.schema_name NOT LIKE 'pg_temp%'
                AND s.schema_name NOT LIKE 'pg_toast%'
            GROUP BY s.schema_name
            ORDER BY s.schema_name
            """

            # クエリを実行してスキーマ一覧を取得
            rows = await conn.fetch(query)

            # ステップ3: 各スキーマの詳細情報を取得
            # For each schema, get the last activity timestamp
            schema_info = []  # スキーマ情報を格納するリスト
            for row in rows:
                schema_name = row["schema_name"]
                table_count = row["table_count"] or 0

                # 各スキーマから最初と最後のアクティビティ時刻を取得
                # Try to get the first and last activity from the schema
                first_activity = None  # 最初のエージェント登録時刻
                last_activity = None  # 最後のアクティビティ時刻
                llm_providers = set()  # 使用されたLLMプロバイダーのセット
                try:
                    # 必要なテーブルが存在するかチェック
                    # Check if the tables exist first
                    # なぜチェックするのか：スキーマが存在してもテーブルがない場合があるため
                    has_agents = await conn.fetchval(
                        """
                        SELECT EXISTS (
                            SELECT 1 FROM information_schema.tables
                            WHERE table_schema = $1 AND table_name = 'agents'
                        )
                        """,
                        schema_name,
                    )
                    has_actions = await conn.fetchval(
                        """
                        SELECT EXISTS (
                            SELECT 1 FROM information_schema.tables
                            WHERE table_schema = $1 AND table_name = 'actions'
                        )
                        """,
                        schema_name,
                    )
                    has_logs = await conn.fetchval(
                        """
                        SELECT EXISTS (
                            SELECT 1 FROM information_schema.tables
                            WHERE table_schema = $1 AND table_name = 'logs'
                        )
                        """,
                        schema_name,
                    )

                    # 3つの必須テーブル（agents, actions, logs）がすべて揃っていないスキーマはスキップ
                    # Skip schemas that don't have all three required tables
                    if not (has_agents and has_actions and has_logs):
                        continue

                    # 最初のエージェント登録時刻を取得（実験の開始時刻）
                    # Get first agent registration timestamp (earliest agent created_at)
                    if has_agents:
                        first_activity = await conn.fetchval(
                            f"SELECT MIN(created_at) FROM {schema_name}.agents"
                        )

                    # すべてのテーブルから最後のアクティビティ時刻を取得
                    # Get last activity across all tables
                    # 複数のテーブルの最大created_atを比較して、最も新しい時刻を見つける
                    queries = []
                    if has_agents:
                        queries.append(
                            f"SELECT MAX(created_at) as max_created_at FROM {schema_name}.agents"
                        )
                    if has_actions:
                        queries.append(
                            f"SELECT MAX(created_at) as max_created_at FROM {schema_name}.actions"
                        )
                    if has_logs:
                        queries.append(
                            f"SELECT MAX(created_at) as max_created_at FROM {schema_name}.logs"
                        )

                    # UNION ALLで複数のクエリを結合し、その中の最大値を取得
                    if queries:
                        union_query = " UNION ALL ".join(queries)
                        last_activity = await conn.fetchval(
                            f"SELECT MAX(max_created_at) FROM ({union_query}) AS dates"
                        )

                    # ログテーブルから使用されたLLMプロバイダーを取得
                    # Get unique LLM providers from logs
                    if has_logs:
                        try:
                            # JSONBデータから'$.data.provider'パスを検索
                            # jsonb_path_query_firstでJSONパスクエリを実行
                            provider_rows = await conn.fetch(
                                f"""
                                SELECT DISTINCT jsonb_path_query_first(data, '$.data.provider') #>> '{{}}' as provider
                                FROM {schema_name}.logs
                                WHERE jsonb_path_query_first(data, '$.data.provider') IS NOT NULL
                                """
                            )
                            # セット内包表記で重複を排除してプロバイダーリストを作成
                            llm_providers = {
                                row["provider"]
                                for row in provider_rows
                                if row["provider"]
                            }
                        except Exception:
                            # プロバイダー情報が取得できなくてもエラーにしない
                            # If we can't get providers, just skip it
                            pass
                except Exception:
                    # アクティビティ情報が取得できなくてもエラーにしない
                    # If we can't get activity timestamps, just skip it
                    pass

                # スキーマ情報をリストに追加
                schema_info.append(
                    {
                        "schema_name": schema_name,
                        "table_count": table_count,
                        "first_activity": first_activity,
                        "last_activity": last_activity,
                        "llm_providers": llm_providers,
                    }
                )

            # ステップ4: 実験を最新順にソート
            # Sort by first agent registration (most recent first)
            # Use timezone-aware datetime for comparison
            # 最初のエージェント登録時刻でソート（新しい順）
            # first_activityがNoneの場合は最も古い時刻として扱う
            schema_info.sort(
                key=lambda x: x["first_activity"] or datetime.min.replace(tzinfo=UTC),
                reverse=True,  # 降順（新しいものが先）
            )

            rows = schema_info

            # 実験が見つからない場合
            if not rows:
                print("No experiments found in PostgreSQL database.")
                print(f"\nDatabase: {database}")
                print(f"Host: {host}:{port}")
                return

            # 表示件数の制限を適用
            # Apply limit if specified
            total_experiments = len(rows)
            if limit is not None and limit > 0:
                rows = rows[:limit]  # 最初のN件だけを取得

            # ステップ5: ヘッダーを表示
            # Print header
            print(f"\n{'=' * 80}")
            print(f"MARKETPLACE EXPERIMENTS (Database: {database})")
            print(f"{'=' * 80}\n")

            # 表示件数の情報を出力
            if limit is not None and total_experiments > limit:
                print(
                    f"Showing {len(rows)} of {total_experiments} experiment(s) (most recent first):\n"
                )
            else:
                print(f"Found {len(rows)} experiment(s) (most recent first):\n")

            # ステップ6: 各実験の詳細情報を表示
            # Print each experiment
            for idx, schema_dict in enumerate(rows, 1):  # 1から始まる番号付け
                schema_name = schema_dict["schema_name"]
                table_count = schema_dict["table_count"] or 0
                first_activity = schema_dict["first_activity"]
                last_activity = schema_dict["last_activity"]
                llm_providers = schema_dict.get("llm_providers", set())

                # 実験番号と名前を表示
                print(f"{idx}. {schema_name}")

                # 最初のエージェント登録時刻を表示
                if first_activity:
                    # Format the datetime
                    # datetimeオブジェクトの場合、ローカルタイムゾーンでフォーマット
                    if isinstance(first_activity, datetime):
                        formatted_time = format_datetime_local(first_activity)
                        print(f"   First agent registered: {formatted_time}")
                else:
                    print("   First agent registered: N/A (no data)")

                # 最後のアクティビティ時刻を表示
                if last_activity:
                    # Format the datetime
                    if isinstance(last_activity, datetime):
                        formatted_time = format_datetime_local(last_activity)
                        print(f"   Last activity: {formatted_time}")
                else:
                    print("   Last activity: N/A (no data)")

                # 各テーブルのレコード数を取得
                # Get row counts for each table in this schema
                try:
                    # agentsテーブルの件数を取得
                    agents_count = await conn.fetchval(
                        f"SELECT COUNT(*) FROM {schema_name}.agents"
                    )
                    # actionsテーブルの件数を取得
                    actions_count = await conn.fetchval(
                        f"SELECT COUNT(*) FROM {schema_name}.actions"
                    )
                    # logsテーブルの件数を取得
                    logs_count = await conn.fetchval(
                        f"SELECT COUNT(*) FROM {schema_name}.logs"
                    )

                    # データ量を1行で表示
                    print(
                        f"   Data: {agents_count} agents, {actions_count} actions, {logs_count} logs"
                    )
                except Exception:
                    # テーブルが存在しない場合や権限がない場合
                    # Table might not exist
                    print("   Data: Unable to query")

                # 使用されたLLMプロバイダーを表示
                # Display LLM providers
                if llm_providers:
                    providers_str = ", ".join(
                        sorted(llm_providers)
                    )  # アルファベット順にソート
                    print(f"   LLM Providers: {providers_str}")

                print()  # 実験間に空行を挿入

        finally:
            # データベース接続を必ずクローズ（エラーが発生しても実行される）
            await conn.close()

    # データベース関連のエラーをキャッチして適切に処理
    except asyncpg.InvalidCatalogNameError:
        # データベースが存在しない場合のエラー
        print(f"Error: Database '{database}' does not exist", file=sys.stderr)
        sys.exit(1)  # 終了コード1でプログラムを終了
    except asyncpg.InvalidPasswordError:
        # パスワードが間違っている場合のエラー
        print("Error: Invalid password", file=sys.stderr)
        sys.exit(1)  # 終了コード1でプログラムを終了
