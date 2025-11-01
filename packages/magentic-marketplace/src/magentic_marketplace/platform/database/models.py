"""Database models for marketplace entities.

マーケットプレイスエンティティのデータベースモデル。

このモジュールは、データベースに保存されるデータの構造を定義します。
すべてのモデルは Row ベースクラスを継承し、データベース固有のメタデータ
（id、created_at、index）とドメインデータを組み合わせた構造を持ちます。

設計理念:
- ドメインモデルとデータベースモデルの分離
- data フィールドにドメインオブジェクトを格納（柔軟性を保つ）
- index フィールドでデータベースの行番号を追跡（SQLiteのrowidやPostgreSQLのrow_index）

主要なモデル:
1. Row: すべてのデータベース行の基底クラス（ジェネリック型）
2. AgentRow: エージェント情報を保存する行
3. ActionRow: マーケットプレイスアクション（操作）を保存する行
4. LogRow: ログメッセージを保存する行

ジェネリック型の利点:
- 型安全性: コンパイル時に型エラーを検出
- 再利用性: 同じRow構造を異なるデータ型で使い回せる
- 明確性: data フィールドに何が入るかが明確
"""

from typing import Generic, TypeVar

from pydantic import AwareDatetime, BaseModel

from ..shared.models import (
    ActionExecutionRequest,
    ActionExecutionResult,
    AgentProfile,
    Log,
)

T = TypeVar("T")
# ジェネリック型変数: Row クラスで data フィールドの型をパラメータ化するために使用


class Row(BaseModel, Generic[T]):
    """Base database row model with generic data field.

    ジェネリックなデータフィールドを持つデータベース行の基底モデル。

    すべてのデータベーステーブルで共通する構造を定義します。
    ジェネリック型 T により、data フィールドに格納されるデータの型を
    サブクラスで指定できます。

    フィールド説明:
    - id: 行の一意識別子（UUID形式が一般的）
    - created_at: 行が作成された日時（タイムゾーン情報を含む）
    - data: 実際のドメインデータ（型 T、サブクラスで具体的な型を指定）
    - index: データベースの行番号（SQLiteのrowid、PostgreSQLのrow_index）
    """

    id: str
    # 行の一意識別子（エンティティのID）
    created_at: AwareDatetime
    # 作成日時（タイムゾーン情報付き datetime）
    data: T
    # ドメインデータ（ジェネリック型、サブクラスで具体化）
    index: int | None = None
    # データベースの行番号（挿入順序を追跡、after_indexクエリで使用）


class AgentRow(Row[AgentProfile]):
    """Database Agent model that wraps the Agent with DB fields.

    エージェント情報を保存するデータベースモデル。

    Row[AgentProfile] を継承することで、data フィールドが AgentProfile 型になります。
    エージェントの基本情報（名前、説明、特徴など）をデータベースに保存します。
    """

    agent_embedding: bytes | None = None
    # エージェントの埋め込みベクトル（検索用、バイナリ形式、任意）
    # 将来的にベクトル検索を実装する際に使用される可能性がある


class ActionRowData(BaseModel):
    """Data container for action request and result.

    アクションのリクエストと結果を格納するデータコンテナ。

    ActionRow の data フィールドに格納される構造化データです。
    エージェントが実行したアクション（操作）とその結果を記録します。

    構造化された記録の理由:
    - リクエスト: エージェントが何を要求したか（検索、メッセージ送信など）
    - 結果: マーケットプレイスが何を返したか（成功/失敗、返却データ）
    - 分析: 実験後にエージェントの行動パターンを分析可能
    """

    agent_id: str
    # アクションを実行したエージェントのID
    request: ActionExecutionRequest
    # アクション実行リクエスト（アクションの種類とパラメータ）
    result: ActionExecutionResult
    # アクション実行結果（成功/失敗、レスポンスデータ）


class ActionRow(Row[ActionRowData]):
    """Database Action model that wraps the Action with DB fields.

    アクション（操作）を保存するデータベースモデル。

    Row[ActionRowData] を継承し、data フィールドに ActionRowData を格納します。
    マーケットプレイスで実行されたすべてのアクションの履歴を記録します。

    重要性:
    - すべてのエージェントの行動を追跡（完全な監査証跡）
    - 実験の再現性を保証（すべてのアクションが記録される）
    - 分析の基盤（どのアクションがどの結果につながったか）
    """


class LogRow(Row[Log]):
    """Database model for log records that wraps the Log with DB fields.

    ログレコードを保存するデータベースモデル。

    Row[Log] を継承し、data フィールドに Log オブジェクトを格納します。
    システムログ、エージェントログ、エラーログなどをデータベースに記録します。

    用途:
    - デバッグ: 問題発生時の詳細な情報を提供
    - 監視: システムの健全性を追跡
    - 分析: LLM呼び出しの成功率、レイテンシなどを分析
    """
