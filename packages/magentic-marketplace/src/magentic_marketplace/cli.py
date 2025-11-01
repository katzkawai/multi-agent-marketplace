"""Command-line interface for magentic-marketplace.

Magentic Marketplaceコマンドラインインターフェース
================================================

このモジュールは、Magentic Marketplaceの主要なCLIエントリーポイントを提供します。
エージェント市場シミュレーションの実行、分析、可視化などの操作を
コマンドラインから実行できます。

利用可能なコマンド:
    run             - マーケットプレイス実験を実行
    analyze         - 実験結果を分析して統計を生成
    extract-traces  - LLMトレースをマークダウンファイルに抽出
    audit           - 実験の整合性を検証（提案配信など）
    export          - PostgreSQL実験をSQLiteファイルにエクスポート
    list            - PostgreSQLに保存されている全実験を一覧表示
    ui              - インタラクティブな可視化UIを起動

基本的な使用方法:
    # 実験を実行
    magentic-marketplace run data/mexican_3_9 --experiment-name test_exp

    # 結果を分析
    magentic-marketplace analyze test_exp

    # UI で可視化
    magentic-marketplace ui test_exp

    # SQLite にエクスポート
    magentic-marketplace export test_exp -o ./exports

環境設定:
    .envファイルまたは環境変数で以下を設定:
    - LLM_PROVIDER: "openai", "anthropic", "gemini" のいずれか
    - LLM_MODEL: 使用するモデル名
    - OPENAI_API_KEY / ANTHROPIC_API_KEY / GEMINI_API_KEY: APIキー
    - POSTGRES_HOST, POSTGRES_PORT, POSTGRES_PASSWORD: PostgreSQL接続情報

詳細なヘルプ:
    magentic-marketplace <command> --help
"""

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from magentic_marketplace.experiments.export_experiment import export_experiment
from magentic_marketplace.experiments.extract_agent_llm_traces import (
    run_extract_traces,
)
from magentic_marketplace.experiments.list_experiments import list_experiments
from magentic_marketplace.experiments.run_analytics import run_analytics
from magentic_marketplace.experiments.run_audit import run_audit
from magentic_marketplace.experiments.run_experiment import run_marketplace_experiment
from magentic_marketplace.experiments.utils import setup_logging
from magentic_marketplace.ui import run_ui_server

# デフォルト設定
DEFAULT_POSTGRES_PORT = 5432  # PostgreSQLのデフォルトポート
DEFAULT_UI_PORT = 5000  # 可視化UIのデフォルトポート


def run_experiment_command(args):
    """Handle the experiment subcommand.

    実験実行コマンドのハンドラー
    ===========================

    マーケットプレイス実験を実行します。以下の手順で動作します：
    1. YAMLファイルからビジネスと顧客エージェントをロード
    2. PostgreSQLデータベーススキーマを作成（またはオーバーライド）
    3. FastAPIベースのマーケットプレイスサーバーを起動
    4. 全エージェントを登録
    5. シミュレーションを実行
    6. オプションでSQLiteにエクスポート

    データディレクトリ構造:
        data_dir/
        ├── businesses/
        │   ├── business_001.yaml
        │   ├── business_002.yaml
        │   └── ...
        └── customers/
            ├── customer_001.yaml
            ├── customer_002.yaml
            └── ...

    各YAMLファイルの形式:
        Business: id, name, description, rating, menu_features, amenity_features
        Customer: id, name, request, menu_features, amenity_features

    Args:
        args: argparseからの引数オブジェクト（コマンドライン引数を含む）

    Note:
        実験結果はPostgreSQLに保存され、後で分析や可視化が可能です。
        --exportフラグを使用すると、実験完了後に自動的にSQLiteファイルにエクスポートされます。

    """
    # ロギングのセットアップ
    setup_logging()
    logger = logging.getLogger(__name__)

    # ログレベルが指定されている場合は設定
    if hasattr(args, "log_level"):
        numeric_level = getattr(logging, args.log_level.upper())
        logging.getLogger().setLevel(numeric_level)

    # パスをPathオブジェクトに変換
    data_dir = Path(args.data_dir)

    # データディレクトリ構造の検証
    if not data_dir.exists():
        logger.error(f"Data directory does not exist: {data_dir}")
        sys.exit(1)

    businesses_dir = data_dir / "businesses"
    customers_dir = data_dir / "customers"

    if not businesses_dir.exists():
        logger.error(f"Businesses directory does not exist: {businesses_dir}")
        sys.exit(1)

    if not customers_dir.exists():
        logger.error(f"Customers directory does not exist: {customers_dir}")
        sys.exit(1)

    # .envファイルの読み込み試行
    # LLM APIキーやPostgreSQL接続情報などの環境変数を取得
    env_file = getattr(args, "env_file", ".env")
    did_load_env = load_dotenv(env_file)
    if did_load_env:
        logger.info(f"Loaded environment variables from env file at path: {env_file}")
    else:
        logger.warning(
            f"No environment variables loaded from env file at path: {env_file}"
        )

    # 実験の概要をログ出力
    logger.info(
        "Marketplace Experiment Runner\n"
        "This experiment will:\n"
        f"1. Load businesses from: {businesses_dir}\n"
        f"2. Load customers from: {customers_dir}\n"
        f"3. Create a Postgres database schema: {args.experiment_name}\n"
        "4. Start a marketplace server with simple marketplace protocol\n"
        "5. Register all business and customer agents\n"
        "6. Run the marketplace simulation\n"
    )

    # 実験を実行（非同期関数をasyncio.runで実行）
    asyncio.run(
        run_marketplace_experiment(
            data_dir=data_dir,
            experiment_name=args.experiment_name,
            search_algorithm=args.search_algorithm,
            search_bandwidth=args.search_bandwidth,
            customer_max_steps=args.customer_max_steps,
            postgres_host=args.postgres_host,
            postgres_port=args.postgres_port,
            postgres_password=args.postgres_password,
            db_pool_min_size=args.db_pool_min_size,
            db_pool_max_size=args.db_pool_max_size,
            server_host=args.server_host,
            server_port=args.server_port,
            override=args.override_db,
            export_sqlite=args.export,
            export_dir=args.export_dir,
            export_filename=args.export_filename,
        )
    )


def run_analysis_command(args):
    """Handle the analytics subcommand.

    分析コマンドのハンドラー
    =======================

    実験結果を分析して、市場効率性の統計情報を生成します。

    分析内容:
    - 顧客効用の計算（マッチスコア - 支払い総額）
    - ビジネス効用の計算（収益総額）
    - 市場厚生の計算（全顧客効用の合計）
    - 無効な提案の追跡（誤ったメニュー項目、価格エラーなど）
    - ファジーマッチング（Levenshtein距離を使用したタイポの許容）

    出力:
    - analytics_results_<name>.json ファイル（--no-save-json フラグで無効化可能）
    - コンソールへの統計情報の表示

    Args:
        args: argparseからの引数オブジェクト

    """
    save_to_json = not args.no_save_json
    asyncio.run(
        run_analytics(
            args.database_name,
            args.db_type,
            save_to_json=save_to_json,
            print_results=True,
            fuzzy_match_distance=args.fuzzy_match_distance,
        )
    )


def run_extract_traces_command(args):
    """Handle the extract-traces subcommand.

    LLMトレース抽出コマンドのハンドラー
    ==================================

    実験中の全LLM呼び出しをマークダウンファイルに抽出します。
    各エージェントのプロンプト、応答、トークン使用量などの詳細を含みます。

    出力:
    - エージェントごとのマークダウンファイル
    - プロンプト、応答、トークン使用量、レイテンシなどの詳細

    Args:
        args: argparseからの引数オブジェクト

    """
    asyncio.run(run_extract_traces(args.database_name, args.db_type))


def run_audit_command(args):
    """Handle the audit subcommand.

    監査コマンドのハンドラー
    =======================

    実験の整合性を検証します。主に以下をチェック:
    - 顧客が全てのビジネス提案を受信したか
    - メッセージ配信の完全性
    - プロトコル違反の有無

    出力:
    - audit_results_<name>.json ファイル（--no-save-json フラグで無効化可能）
    - コンソールへの検証結果の表示

    Args:
        args: argparseからの引数オブジェクト

    """
    save_to_json = not args.no_save_json
    asyncio.run(run_audit(args.database_name, args.db_type, save_to_json=save_to_json))


def list_experiments_command(args):
    """Handle the list-experiments subcommand.

    実験一覧コマンドのハンドラー
    ===========================

    PostgreSQLデータベースに保存されている全ての実験を一覧表示します。

    表示情報:
    - 実験名（スキーマ名）
    - 最終更新日時
    - エージェント数
    - アクション数

    Args:
        args: argparseからの引数オブジェクト

    """
    asyncio.run(
        list_experiments(
            host=args.postgres_host,
            port=args.postgres_port,
            database=args.postgres_database,
            user=args.postgres_user,
            password=args.postgres_password,
            limit=args.limit,
        )
    )


def run_export_command(args):
    """Handle the export subcommand.

    エクスポートコマンドのハンドラー
    ===============================

    PostgreSQL実験をSQLiteファイルにエクスポートします。
    これにより、ポータブルな分析やオフライン可視化が可能になります。

    エクスポート内容:
    - agents テーブル
    - actions テーブル
    - logs テーブル

    出力:
    - <experiment_name>.db SQLiteファイル（デフォルト）
    - カスタムディレクトリと ファイル名をオプションで指定可能

    Args:
        args: argparseからの引数オブジェクト

    """
    asyncio.run(
        export_experiment(
            experiment_name=args.experiment_name,
            output_dir=args.output_dir,
            output_filename=args.output_filename,
            postgres_host=args.postgres_host,
            postgres_port=args.postgres_port,
            postgres_user=args.postgres_user,
            postgres_password=args.postgres_password,
        )
    )


def run_ui_command(args):
    """Handle the UI subcommand to launch the visualizer.

    UI起動コマンドのハンドラー
    =========================

    インタラクティブなWeb可視化UIを起動します。

    機能:
    - エージェント間のインタラクションの可視化
    - メッセージフローの追跡
    - 統計情報のグラフ表示
    - LLMトレースの閲覧

    アクセス:
    - デフォルト: http://localhost:5000
    - カスタムホスト/ポートをオプションで指定可能

    Args:
        args: argparseからの引数オブジェクト

    """
    run_ui_server(
        database_name=args.database_name,
        db_type=args.db_type,
        postgres_host=args.postgres_host,
        postgres_port=args.postgres_port,
        postgres_password=args.postgres_password,
        ui_port=args.ui_port,
        ui_host=args.ui_host,
    )


def main():
    """Run main CLI.

    メインCLIエントリーポイント
    =========================

    このメソッドは、コマンドライン引数をパースし、適切なサブコマンドハンドラーを
    実行します。全てのCLI操作のエントリーポイントとして機能します。

    サブコマンド構造:
    - run: 実験実行
    - analyze: 結果分析
    - extract-traces: LLMトレース抽出
    - audit: 整合性検証
    - export: PostgreSQL → SQLite エクスポート
    - list: 実験一覧
    - ui: 可視化UI起動

    各サブコマンドは独自の引数セットを持ち、対応するハンドラー関数を実行します。

    実行フロー:
    1. argparseでコマンドライン引数をパース
    2. サブコマンドに基づいてハンドラー関数を選択
    3. ハンドラー関数を実行
    4. エラーハンドリング（KeyboardInterrupt、一般的な例外）

    Note:
        このメソッドは pyproject.toml の [project.scripts] セクションで
        'magentic-marketplace' コマンドとして登録されています。

    """
    parser = argparse.ArgumentParser(
        prog="magentic-marketplace",
        description="Magentic Marketplace - Python SDK for building and running agentic marketplace simulations",
    )

    # サブコマンドの追加
    # 各サブコマンドは独自のパーサーと引数セットを持つ
    subparsers = parser.add_subparsers(
        dest="command", help="Available commands", required=True
    )

    # =================================================================
    # RUN サブコマンド - 実験実行
    # =================================================================
    # マーケットプレイス実験を実行するためのサブコマンド
    # YAMLファイルからエージェントを読み込み、シミュレーションを実行
    experiment_parser = subparsers.add_parser(
        "run", help="Run a marketplace experiment using YAML configuration files"
    )
    experiment_parser.set_defaults(func=run_experiment_command)

    # 必須引数：データディレクトリ
    # businesses/ と customers/ サブディレクトリを含む必要がある
    experiment_parser.add_argument(
        "data_dir",
        type=str,
        help="Path to the data directory containing businesses/ and customers/ subdirectories",
    )

    experiment_parser.add_argument(
        "--search-algorithm",
        type=str,
        default="lexical",
        help="Search algorithm for customer agents (default: lexical)",
    )

    experiment_parser.add_argument(
        "--search-bandwidth",
        type=int,
        default=10,
        help="Search bandwidth for customer agents (default: 10)",
    )

    experiment_parser.add_argument(
        "--customer-max-steps",
        type=int,
        default=100,
        help="Maximum number of steps a customer agent can take before stopping.",
    )

    experiment_parser.add_argument(
        "--experiment-name",
        default=None,
        help="Provide a name for this experiment. Will be used as the 'schema' name in postgres",
    )

    experiment_parser.add_argument(
        "--env-file",
        default=".env",
        help=".env file with environment variables to load.",
    )

    experiment_parser.add_argument(
        "--postgres-host",
        default=os.environ.get("POSTGRES_HOST", "localhost"),
        help="PostgreSQL host (default: POSTGRES_HOST env var or localhost)",
    )

    experiment_parser.add_argument(
        "--postgres-port",
        type=int,
        default=int(os.environ.get("POSTGRES_PORT", DEFAULT_POSTGRES_PORT)),
        help=f"PostgreSQL port (default: POSTGRES_PORT env var or {DEFAULT_POSTGRES_PORT})",
    )

    experiment_parser.add_argument(
        "--postgres-password",
        default=os.environ.get("POSTGRES_PASSWORD", "postgres"),
        help="PostgreSQL password (default: POSTGRES_PASSWORD env var or postgres)",
    )

    experiment_parser.add_argument(
        "--db-pool-min-size",
        type=int,
        default=2,
        help="Minimum connections in PostgreSQL pool (default: 2)",
    )

    experiment_parser.add_argument(
        "--db-pool-max-size",
        type=int,
        default=10,
        help="Maximum connections in PostgreSQL pool (default: 10)",
    )

    experiment_parser.add_argument(
        "--server-host",
        default="127.0.0.1",
        help="FastAPI server host (default: 127.0.0.1)",
    )

    experiment_parser.add_argument(
        "--server-port",
        type=int,
        default=0,
        help="FastAPI server port (default: auto-assign)",
    )

    experiment_parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Set the logging level (default: INFO)",
    )

    experiment_parser.add_argument(
        "--override-db",
        action="store_true",
        help="Override the existing database schema if it exists.",
    )

    experiment_parser.add_argument(
        "--export",
        action="store_true",
        help="Export the experiment to SQLite after completion.",
    )

    experiment_parser.add_argument(
        "--export-dir",
        default=None,
        help="Output directory for SQLite export (default: current directory). Only used with --export.",
    )

    experiment_parser.add_argument(
        "--export-filename",
        default=None,
        help="Output filename for SQLite export (default: <experiment_name>.db). Only used with --export.",
    )

    # =================================================================
    # ANALYZE サブコマンド - 結果分析
    # =================================================================
    # 実験結果を分析して市場効率性の統計を生成
    # 顧客効用、ビジネス効用、市場厚生などを計算
    analytics_parser = subparsers.add_parser(
        "analyze", help="Analyze marketplace simulation data"
    )
    analytics_parser.set_defaults(func=run_analysis_command)

    analytics_parser.add_argument(
        "database_name", help="Postgres schema name or path to the SQLite database file"
    )

    analytics_parser.add_argument(
        "--db-type",
        choices=["sqlite", "postgres"],
        default="postgres",
        help="Type of database to use (default: postgres)",
    )

    analytics_parser.add_argument(
        "--no-save-json",
        action="store_true",
        help="Disable saving analytics to JSON file",
    )

    analytics_parser.add_argument(
        "--fuzzy-match-distance",
        type=int,
        default=0,
        help="Maximum Levenshtein distance for fuzzy item name matching (default: 0)",
    )

    # =================================================================
    # EXTRACT-TRACES サブコマンド - LLMトレース抽出
    # =================================================================
    # 実験中の全LLM呼び出しをマークダウンファイルに抽出
    # デバッグやプロンプト最適化に有用
    extract_traces_parser = subparsers.add_parser(
        "extract-traces",
        help="Extract LLM traces from marketplace simulation and save to markdown files",
    )
    extract_traces_parser.set_defaults(func=run_extract_traces_command)

    extract_traces_parser.add_argument(
        "database_name", help="Postgres schema name or path to the SQLite database file"
    )

    extract_traces_parser.add_argument(
        "--db-type",
        choices=["sqlite", "postgres"],
        default="postgres",
        help="Type of database to use (default: postgres)",
    )

    # =================================================================
    # AUDIT サブコマンド - 整合性検証
    # =================================================================
    # 実験の整合性を検証（全提案が配信されたかなど）
    # プロトコル違反や配信エラーを検出
    audit_parser = subparsers.add_parser(
        "audit",
        help="Audit marketplace simulation to verify customers received all proposals",
    )
    audit_parser.set_defaults(func=run_audit_command)

    audit_parser.add_argument(
        "database_name", help="Postgres schema name or path to the SQLite database file"
    )

    audit_parser.add_argument(
        "--db-type",
        choices=["sqlite", "postgres"],
        default="postgres",
        help="Type of database to use (default: postgres)",
    )

    audit_parser.add_argument(
        "--no-save-json",
        action="store_true",
        help="Disable saving audit results to JSON file",
    )

    # =================================================================
    # EXPORT サブコマンド - PostgreSQL → SQLite エクスポート
    # =================================================================
    # PostgreSQL実験をポータブルなSQLiteファイルにエクスポート
    # オフライン分析や共有に便利
    export_parser = subparsers.add_parser(
        "export",
        help="Export a PostgreSQL experiment to SQLite database file",
    )
    export_parser.set_defaults(func=run_export_command)

    export_parser.add_argument(
        "experiment_name",
        help="Name of the experiment (PostgreSQL schema name)",
    )

    export_parser.add_argument(
        "-o",
        "--output-dir",
        help="Output directory for the SQLite database file (default: current directory)",
        default=None,
    )

    export_parser.add_argument(
        "-f",
        "--output-filename",
        help="Output filename for the SQLite database (default: <experiment_name>.db)",
        default=None,
    )

    export_parser.add_argument(
        "--postgres-host",
        default=os.environ.get("POSTGRES_HOST", "localhost"),
        help="PostgreSQL host (default: POSTGRES_HOST env var or localhost)",
    )

    export_parser.add_argument(
        "--postgres-port",
        type=int,
        default=int(os.environ.get("POSTGRES_PORT", "5432")),
        help="PostgreSQL port (default: POSTGRES_PORT env var or 5432)",
    )

    export_parser.add_argument(
        "--postgres-user",
        default=os.environ.get("POSTGRES_USER", "postgres"),
        help="PostgreSQL user (default: POSTGRES_USER env var or postgres)",
    )

    export_parser.add_argument(
        "--postgres-password",
        default=os.environ.get("POSTGRES_PASSWORD", "postgres"),
        help="PostgreSQL password (default: POSTGRES_PASSWORD env var or postgres)",
    )

    # =================================================================
    # LIST サブコマンド - 実験一覧
    # =================================================================
    # PostgreSQLに保存されている全実験を一覧表示
    # 実験の管理と選択に便利
    list_experiments_parser = subparsers.add_parser(
        "list",
        help="List all marketplace experiments stored in PostgreSQL",
    )
    list_experiments_parser.set_defaults(func=list_experiments_command)

    list_experiments_parser.add_argument(
        "--postgres-host",
        default=os.environ.get("POSTGRES_HOST", "localhost"),
        help="PostgreSQL host (default: POSTGRES_HOST env var or localhost)",
    )

    list_experiments_parser.add_argument(
        "--postgres-port",
        type=int,
        default=int(os.environ.get("POSTGRES_PORT", "5432")),
        help="PostgreSQL port (default: POSTGRES_PORT env var or 5432)",
    )

    list_experiments_parser.add_argument(
        "--postgres-database",
        default=os.environ.get("POSTGRES_DB", "marketplace"),
        help="PostgreSQL database name (default: POSTGRES_DB env var or marketplace)",
    )

    list_experiments_parser.add_argument(
        "--postgres-user",
        default=os.environ.get("POSTGRES_USER", "postgres"),
        help="PostgreSQL user (default: POSTGRES_USER env var or postgres)",
    )

    list_experiments_parser.add_argument(
        "--postgres-password",
        default=os.environ.get("POSTGRES_PASSWORD", "postgres"),
        help="PostgreSQL password (default: POSTGRES_PASSWORD env var or postgres)",
    )

    list_experiments_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of experiments to display",
    )

    # =================================================================
    # UI サブコマンド - 可視化UI起動
    # =================================================================
    # インタラクティブなWeb UIを起動して実験結果を可視化
    # エージェントのインタラクション、統計、LLMトレースなどを閲覧
    ui_parser = subparsers.add_parser(
        "ui", help="Launch interactive visualizer for marketplace data"
    )
    ui_parser.set_defaults(func=run_ui_command)

    ui_parser.add_argument(
        "database_name",
        help="Postgres schema name or path to the SQLite database file",
    )

    ui_parser.add_argument(
        "--db-type",
        choices=["sqlite", "postgres"],
        default="postgres",
        help="Type of database to use (default: postgres)",
    )

    ui_parser.add_argument(
        "--postgres-host",
        default="localhost",
        help="PostgreSQL host (default: localhost)",
    )

    ui_parser.add_argument(
        "--postgres-port",
        type=int,
        default=DEFAULT_POSTGRES_PORT,
        help=f"PostgreSQL port (default: {DEFAULT_POSTGRES_PORT})",
    )

    ui_parser.add_argument(
        "--postgres-password",
        default="postgres",
        help="PostgreSQL password (default: postgres)",
    )

    ui_parser.add_argument(
        "--ui-host",
        default="localhost",
        help="UI server host (default: localhost)",
    )

    ui_parser.add_argument(
        "--ui-port",
        type=int,
        default=DEFAULT_UI_PORT,
        help=f"Port for ui server(default: {DEFAULT_UI_PORT})",
    )

    # =================================================================
    # 引数のパースと実行
    # =================================================================
    # コマンドライン引数をパースし、対応するハンドラー関数を実行
    args = parser.parse_args()

    try:
        # 各サブコマンドに設定された func を呼び出し
        # set_defaults(func=...) で設定されたハンドラー関数を実行
        args.func(args)
    except KeyboardInterrupt:
        # ユーザーによる中断（Ctrl+C）
        print("\nExecution interrupted by user.")
        sys.exit(1)
    except Exception as e:
        # その他のエラー
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    # スクリプトとして直接実行された場合のエントリーポイント
    # 通常は 'magentic-marketplace' コマンドから呼び出される
    main()
