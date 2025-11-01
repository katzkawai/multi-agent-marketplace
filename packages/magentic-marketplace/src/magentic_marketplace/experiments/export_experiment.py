"""Export a PostgreSQL experiment to SQLite database file.

PostgreSQL実験データをSQLiteデータベースファイルにエクスポートする

このモジュールは、PostgreSQLに保存された実験データを
ポータブルなSQLiteファイルに変換します。これにより、
PostgreSQLサーバーなしでデータを共有・分析できます。
"""

import sys  # システム終了とエラー出力用
from pathlib import Path  # ファイルパス操作用

# データベース接続関数のインポート
from magentic_marketplace.platform.database import connect_to_postgresql_database

# PostgreSQLからSQLiteへの変換関数のインポート
from magentic_marketplace.platform.database.converter import convert_postgres_to_sqlite


async def export_experiment(
    experiment_name: str,
    output_dir: str | None = None,
    output_filename: str | None = None,
    postgres_host: str = "localhost",
    postgres_port: int = 5432,
    postgres_user: str = "postgres",
    postgres_password: str = "postgres",
):
    """Export a PostgreSQL experiment database to SQLite.

    PostgreSQL実験データベースをSQLiteにエクスポートする非同期関数

    この関数は以下の処理を実行します：
    1. PostgreSQLデータベースに接続
    2. 指定された実験のスキーマからデータを読み取り
    3. SQLiteファイルにデータを書き込み

    なぜSQLiteにエクスポートするのか：
    - ファイルベースなので配布や共有が簡単
    - PostgreSQLサーバーが不要
    - 軽量な分析ツールで開ける
    - バックアップとして使える

    Args:
        experiment_name: 実験名（PostgreSQLのスキーマ名と一致する必要がある）
        output_dir: 出力ディレクトリのパス（Noneの場合はカレントディレクトリ）
        output_filename: 出力ファイル名（Noneの場合は<実験名>.dbが使われる）
        postgres_host: PostgreSQLサーバーのホスト名（デフォルト: localhost）
        postgres_port: PostgreSQLサーバーのポート番号（デフォルト: 5432）
        postgres_user: PostgreSQLのユーザー名（デフォルト: postgres）
        postgres_password: PostgreSQLのパスワード（デフォルト: postgres）

    Raises:
        FileExistsError: 出力ファイルが既に存在する場合
        Exception: データベース接続やエクスポートに失敗した場合

    """
    # ステップ1: 出力パスの決定
    # Determine output path
    # ファイル名が指定されていない場合、デフォルトで<実験名>.dbを使用
    if output_filename is None:
        output_filename = f"{experiment_name}.db"

    # 出力ディレクトリが指定されている場合はそれを使用、なければカレントディレクトリ
    if output_dir is not None:
        output_path = Path(output_dir) / output_filename
    else:
        output_path = Path(output_filename)

    # ステップ2: 出力ファイルの存在チェック
    # Check if output file already exists
    # ファイルが既に存在する場合はエラーを発生させる（データの上書きを防ぐため）
    if output_path.exists():
        raise FileExistsError(
            f"Output file already exists: {output_path}. "
            "Please remove it or choose a different output path."
        )

    # 処理開始のメッセージ
    print(f"Exporting experiment '{experiment_name}' to SQLite...")
    print(f"Output path: {output_path}")

    # ステップ3: PostgreSQLデータベースに接続
    # Connect to PostgreSQL database
    # tryブロックを使ってエラーをキャッチし、適切に処理する
    try:
        # async withを使ってデータベース接続を管理（自動的にクリーンアップされる）
        async with connect_to_postgresql_database(
            schema=experiment_name,  # 実験名に対応するスキーマを指定
            host=postgres_host,
            port=postgres_port,
            user=postgres_user,
            password=postgres_password,
            mode="existing",  # 既存のスキーマに接続（新規作成しない）
        ) as db_controller:
            print(
                f"Connected to PostgreSQL database (schema: {experiment_name}, host: {postgres_host})"
            )

            # ステップ4: SQLiteに変換
            # Convert to SQLite
            # convert_postgres_to_sqlite関数がすべてのテーブルとデータをコピーする
            result_path = await convert_postgres_to_sqlite(db_controller, output_path)
            print("\nExport completed successfully!")
            print(f"SQLite database saved to: {result_path}")

    except Exception as e:
        # エラーが発生した場合、標準エラー出力にメッセージを表示
        print(f"Error: Failed to export experiment: {e}", file=sys.stderr)
        raise  # エラーを再度発生させて呼び出し元に伝える
