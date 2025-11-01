#!/usr/bin/env python3
"""Script to run marketplace experiments using YAML configuration files.

マーケットプレイス実験を実行するスクリプト
YAMLファイルから設定を読み込んで、ビジネスエージェントと顧客エージェントが
相互作用する市場シミュレーションを実行します。
"""

# 標準ライブラリのインポート
import socket  # ネットワーク通信用（ポート自動割り当てに使用）
from datetime import datetime  # タイムスタンプの生成に使用
from pathlib import Path  # ファイルパスの操作用

# 実験ユーティリティのインポート（YAMLからエージェント情報を読み込む）
from magentic_marketplace.experiments.utils import (
    load_businesses_from_yaml,  # ビジネスのYAMLファイルを読み込む関数
    load_customers_from_yaml,  # 顧客のYAMLファイルを読み込む関数
)

# エージェントクラスのインポート
from magentic_marketplace.marketplace.agents import BusinessAgent, CustomerAgent

# マーケットプレイスのプロトコル（ルール）のインポート
from magentic_marketplace.marketplace.protocol.protocol import SimpleMarketplaceProtocol

# データベース接続関数のインポート
from magentic_marketplace.platform.database import (
    connect_to_postgresql_database,
)

# PostgreSQLからSQLiteへの変換ツールのインポート
from magentic_marketplace.platform.database.converter import convert_postgres_to_sqlite

# エージェント実行管理用のランチャーのインポート
from magentic_marketplace.platform.launcher import AgentLauncher, MarketplaceLauncher


async def run_marketplace_experiment(
    data_dir: str | Path,
    experiment_name: str | None = None,
    search_algorithm: str = "simple",
    search_bandwidth: int = 10,
    customer_max_steps: int | None = None,
    postgres_host: str = "localhost",
    postgres_port: int = 5432,
    postgres_password: str = "postgres",
    db_pool_min_size: int = 2,
    db_pool_max_size: int = 10,
    server_host: str = "127.0.0.1",
    server_port: int = 0,
    override: bool = False,
    export_sqlite: bool = False,
    export_dir: str | None = None,
    export_filename: str | None = None,
):
    """Run a marketplace experiment using YAML configuration files.

    YAMLファイルからマーケットプレイス実験を実行する非同期関数

    この関数は以下の処理を順番に実行します：
    1. YAMLファイルからビジネスと顧客のデータを読み込む
    2. PostgreSQLデータベースに接続する
    3. マーケットプレイスサーバーを起動する
    4. ビジネスエージェントと顧客エージェントを作成する
    5. エージェント間の相互作用をシミュレートする
    6. （オプション）結果をSQLiteにエクスポートする

    Args:
        data_dir: 実験データディレクトリのパス（businesses/とcustomers/サブディレクトリを含む）
        experiment_name: 実験名（Noneの場合は自動生成される）
        search_algorithm: 顧客の検索アルゴリズム（"simple"など）
        search_bandwidth: 検索で返す最大ビジネス数（デフォルト: 10）
        customer_max_steps: 顧客の最大ステップ数（Noneの場合は制限なし）
        postgres_host: PostgreSQLサーバーのホスト名（デフォルト: "localhost"）
        postgres_port: PostgreSQLサーバーのポート番号（デフォルト: 5432）
        postgres_password: PostgreSQLのパスワード（デフォルト: "postgres"）
        db_pool_min_size: データベース接続プールの最小サイズ（デフォルト: 2）
        db_pool_max_size: データベース接続プールの最大サイズ（デフォルト: 10）
        server_host: マーケットプレイスサーバーのホスト（デフォルト: "127.0.0.1"）
        server_port: マーケットプレイスサーバーのポート（0の場合は自動割り当て）
        override: 既存の実験を上書きするかどうか（デフォルト: False）
        export_sqlite: 実験後にSQLiteファイルにエクスポートするかどうか（デフォルト: False）
        export_dir: エクスポート先のディレクトリ（Noneの場合はカレントディレクトリ）
        export_filename: エクスポートファイル名（Noneの場合は自動生成）

    """
    # ステップ1: YAMLファイルからビジネスと顧客のデータを読み込む
    # Load businesses and customers from YAML files
    data_dir = Path(data_dir)  # 文字列をPathオブジェクトに変換
    businesses_dir = data_dir / "businesses"  # ビジネスのディレクトリパス
    customers_dir = data_dir / "customers"  # 顧客のディレクトリパス

    print(f"Loading data from: {data_dir}")
    # YAMLファイルからビジネスプロファイルを読み込む
    # 各ビジネスは名前、説明、メニュー、価格、アメニティなどの情報を持つ
    businesses = load_businesses_from_yaml(businesses_dir)
    # YAMLファイルから顧客プロファイルを読み込む
    # 各顧客はリクエスト、支払い意思額、必要なアメニティなどの情報を持つ
    customers = load_customers_from_yaml(customers_dir)

    print(f"Loaded {len(customers)} customers and {len(businesses)} businesses")

    # 実験名が指定されていない場合は自動生成する
    # フォーマット: "marketplace_顧客数_ビジネス数_タイムスタンプ"
    if experiment_name is None:
        experiment_name = f"marketplace_{len(customers)}_{len(businesses)}_{int(datetime.now().timestamp() * 1000)}"

    # データベース接続を作成するファクトリー関数
    # なぜファクトリー関数を使うのか：
    # MarketplaceLauncherが必要に応じて複数の接続を作成できるようにするため
    def database_factory():
        return connect_to_postgresql_database(
            schema=experiment_name,  # 実験ごとに別のスキーマを使用（データの分離）
            host=postgres_host,
            port=postgres_port,
            password=postgres_password,
            min_size=db_pool_min_size,  # 接続プールの最小サイズ
            max_size=db_pool_max_size,  # 接続プールの最大サイズ
            mode="override"
            if override
            else "create_new",  # 上書きモードか新規作成モードか
        )

    # ステップ2: サーバーポートの自動割り当て
    # Auto-assign port if set to 0
    # ポート番号が0の場合、OSに空いているポートを自動で割り当ててもらう
    if server_port == 0:
        # ソケットを一時的に作成してポート番号を取得
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind((server_host, 0))  # ポート0を指定するとOSが自動割り当て
            server_port = s.getsockname()[1]  # 割り当てられたポート番号を取得
        print(f"Auto-assigned server port: {server_port}")

    # ステップ3: マーケットプレイスランチャーの作成
    # MarketplaceLauncherはサーバーの起動とエージェントの管理を行う
    marketplace_launcher = MarketplaceLauncher(
        protocol=SimpleMarketplaceProtocol(),  # マーケットプレイスのルールを定義
        database_factory=database_factory,  # データベース接続の作成方法
        host=server_host,  # サーバーのホスト名
        port=server_port,  # サーバーのポート番号
        server_log_level="warning",  # サーバーのログレベル
        experiment_name=experiment_name,  # 実験名
    )

    print(f"Using protocol: {marketplace_launcher.protocol.__class__.__name__}")

    # ステップ4: マーケットプレイスランチャーを非同期コンテキストマネージャーとして使用
    # Use marketplace launcher as async context manager
    # async withを使うことで、終了時に自動的にリソースがクリーンアップされる
    async with marketplace_launcher:
        # ロガーの作成（実験の進行状況や重要なイベントを記録）
        # Create logger
        logger = await marketplace_launcher.create_logger("marketplace_experiment")
        logger.info(
            f"Marketplace experiment started:\nbusinesses={len(businesses)}\ncustomers={len(customers)}\ndata_dir={data_dir}\nexperiment_name:{experiment_name}",
        )

        # ステップ5: 読み込んだプロファイルからエージェントを作成
        # Create agents from loaded profiles
        # ビジネスエージェント：顧客からの問い合わせに応答し、提案を送る
        business_agents = [
            BusinessAgent(business, marketplace_launcher.server_url)
            for business in businesses
        ]

        # 顧客エージェント：ビジネスを検索し、提案を評価し、購入を決定する
        customer_agents = [
            CustomerAgent(
                customer,
                marketplace_launcher.server_url,
                search_algorithm=search_algorithm,  # 検索アルゴリズム
                search_bandwidth=search_bandwidth,  # 検索結果の最大数
                max_steps=customer_max_steps,  # 最大ステップ数（行動回数の上限）
            )
            for customer in customers
        ]

        # ステップ6: エージェントランチャーを使ってエージェントを実行
        # Create agent launcher and run agents with dependency management
        # AgentLauncherは複数のエージェントを並行実行し、依存関係を管理する
        async with AgentLauncher(marketplace_launcher.server_url) as agent_launcher:
            try:
                # エージェントを依存関係を考慮して実行
                # primary_agents（顧客）が先に動き、dependent_agents（ビジネス）がそれに応答する
                # なぜこの順序か：顧客が検索やメッセージを送らないとビジネスは何もできないため
                await agent_launcher.run_agents_with_dependencies(
                    primary_agents=customer_agents, dependent_agents=business_agents
                )
            except KeyboardInterrupt:
                # ユーザーがCtrl+Cで中断した場合
                logger.warning("Simulation interrupted by user")

        # ステップ7: PostgreSQLデータベースをSQLiteに変換（オプション）
        # Convert PostgreSQL database to SQLite (if requested)
        # なぜSQLiteにエクスポートするのか：
        # - PostgreSQLサーバーなしで実験結果を共有できる
        # - ファイルベースなので持ち運びが簡単
        # - 分析ツールで開きやすい
        if export_sqlite:
            # 出力パスの決定
            # Determine output path
            if export_filename is None:
                export_filename = f"{experiment_name}.db"  # デフォルトのファイル名

            if export_dir is not None:
                sqlite_path = (
                    Path(export_dir) / export_filename
                )  # ディレクトリが指定された場合
            else:
                sqlite_path = Path(export_filename)  # カレントディレクトリ

            # 出力ファイルが既に存在するかチェック
            # Check if output file already exists
            if sqlite_path.exists():
                raise FileExistsError(
                    f"Output file already exists: {sqlite_path}. "
                    "Please remove it or choose a different output path using --export-filename or --export-dir."
                )

            logger.info(f"Converting database to SQLite: {sqlite_path}")
            # サーバーからデータベースコントローラーを取得してSQLiteに変換
            if marketplace_launcher.server:
                db = marketplace_launcher.server.state.database_controller
                await convert_postgres_to_sqlite(db, sqlite_path)
                logger.info(f"Database conversion complete: {sqlite_path}")

        # 実験完了のメッセージ
        # 次のステップとして分析コマンドを表示
        print(f"\nRun analytics with: magentic-marketplace analyze {experiment_name}")
