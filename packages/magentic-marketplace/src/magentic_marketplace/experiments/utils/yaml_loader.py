"""YAML loading functions for experiment data.

実験データのYAML読み込み関数。

このモジュールは、実験で使用するビジネスと顧客のプロフィールをYAMLファイルから
読み込むユーティリティ関数を提供します。

YAML設定ファイルの役割:
- 実験に参加するエージェント（ビジネスと顧客）の特性を定義
- 人間が読み書きしやすい形式で実験パラメータを管理
- 同じコードで異なる実験シナリオを簡単に実行可能

典型的なディレクトリ構造:
    data/
    └── mexican_3_9/           # 実験データディレクトリ
        ├── businesses/        # ビジネスのYAMLファイル
        │   ├── business_1.yaml
        │   ├── business_2.yaml
        │   └── ...
        └── customers/         # 顧客のYAMLファイル
            ├── customer_1.yaml
            ├── customer_2.yaml
            └── ...

使用例:
    businesses = load_businesses_from_yaml(Path("data/mexican_3_9/businesses"))
    customers = load_customers_from_yaml(Path("data/mexican_3_9/customers"))
"""

from pathlib import Path

import yaml

from magentic_marketplace.marketplace.shared.models import Business, Customer


def load_businesses_from_yaml(businesses_dir: Path) -> list[Business]:
    """Load business profiles from YAML files in the given directory.

    指定ディレクトリ内のYAMLファイルからビジネスプロフィールを読み込む。

    この関数は、ディレクトリ内のすべての .yaml または .yml ファイルを読み込み、
    それぞれを Business モデルに変換します。

    Args:
        businesses_dir: ビジネスのYAMLファイルが格納されているディレクトリのパス

    Returns:
        Business オブジェクトのリスト（ファイル名のアルファベット順）

    Raises:
        FileNotFoundError: 指定されたディレクトリが存在しない場合
        ValueError: ディレクトリ内にYAMLファイルが見つからない場合
        ValidationError: YAMLの内容がBusinessモデルの要件を満たさない場合

    YAMLファイルの例:
        id: "business_1"
        name: "タコス・デル・ソル"
        description: "本格的なメキシコ料理のレストラン"
        rating: 4.5
        menu_features:
          tacos: 8.99
          burritos: 10.99
        amenity_features:
          outdoor_seating: true
          wifi: true

    """
    businesses: list[Business] = []

    if not businesses_dir.exists():
        # ディレクトリの存在チェック
        raise FileNotFoundError(f"Businesses directory not found: {businesses_dir}")

    yaml_files = list(businesses_dir.glob("*.yaml")) + list(
        businesses_dir.glob("*.yml")
    )
    # .yaml と .yml 両方の拡張子に対応

    if not yaml_files:
        # YAMLファイルが1つも見つからない場合はエラー
        raise ValueError(
            f"No YAML files found in businesses directory: {businesses_dir}"
        )

    for yaml_file in sorted(yaml_files):
        # ファイル名順にソートして処理（実験の再現性のため）
        with open(yaml_file, encoding="utf-8") as f:
            # UTF-8エンコーディングで開く（日本語などの多言語対応）
            data = yaml.safe_load(f)
            # YAMLを辞書形式にパース（safe_load は安全な読み込み）

        business = Business.model_validate(data)
        # Pydantic モデルでバリデーション（型チェック、必須フィールド確認）
        businesses.append(business)

    return businesses


def load_customers_from_yaml(customers_dir: Path) -> list[Customer]:
    """Load customer profiles from YAML files in the given directory.

    指定ディレクトリ内のYAMLファイルから顧客プロフィールを読み込む。

    この関数は、ディレクトリ内のすべての .yaml または .yml ファイルを読み込み、
    それぞれを Customer モデルに変換します。

    Args:
        customers_dir: 顧客のYAMLファイルが格納されているディレクトリのパス

    Returns:
        Customer オブジェクトのリスト（ファイル名のアルファベット順）

    Raises:
        FileNotFoundError: 指定されたディレクトリが存在しない場合
        ValueError: ディレクトリ内にYAMLファイルが見つからない場合
        ValidationError: YAMLの内容がCustomerモデルの要件を満たさない場合

    YAMLファイルの例:
        id: "customer_1"
        name: "山田太郎"
        request: "辛いタコスとケサディーヤが食べたい"
        menu_features:
          tacos: 12.00      # この商品に最大12ドル支払う意思がある
          quesadillas: 10.00
        amenity_features:   # 必須の設備
          - outdoor_seating
          - wifi

    顧客の特性:
    - menu_features: 各商品に対する支払い意思額（willingness-to-pay）
    - amenity_features: 必須設備のリスト（すべて満たす必要がある）
    - request: 自然言語での要求（検索クエリに使用）

    """
    customers: list[Customer] = []

    if not customers_dir.exists():
        # ディレクトリの存在チェック
        raise FileNotFoundError(f"Customers directory not found: {customers_dir}")

    yaml_files = list(customers_dir.glob("*.yaml")) + list(customers_dir.glob("*.yml"))
    # .yaml と .yml 両方の拡張子に対応

    if not yaml_files:
        # YAMLファイルが1つも見つからない場合はエラー
        raise ValueError(f"No YAML files found in customers directory: {customers_dir}")

    for yaml_file in sorted(yaml_files):
        # ファイル名順にソートして処理（実験の再現性のため）
        with open(yaml_file, encoding="utf-8") as f:
            # UTF-8エンコーディングで開く（日本語などの多言語対応）
            data = yaml.safe_load(f)
            # YAMLを辞書形式にパース（safe_load は安全な読み込み）

        customer = Customer.model_validate(data)
        # Pydantic モデルでバリデーション（型チェック、必須フィールド確認）
        customers.append(customer)

    return customers
