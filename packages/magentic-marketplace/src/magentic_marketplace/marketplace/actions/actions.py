"""Messaging actions for the simple marketplace.

シンプルなマーケットプレイス用のメッセージングアクション。

このモジュールは、エージェントがマーケットプレイスに対して実行できる具体的なアクション
（操作）を定義します。すべてのアクションは BaseAction を継承し、マーケットプレイス
サーバーが処理できる標準的なインターフェースを持ちます。

アクションとは:
- エージェントがマーケットプレイスに送信する「リクエスト」
- 各アクションには type フィールドがあり、どの操作かを識別
- サーバーは type に基づいて適切なハンドラーを実行
- アクションの結果（レスポンス）が返される

定義されるアクション:
1. SendMessage: 他のエージェントにメッセージを送信
2. FetchMessages: 自分宛てのメッセージを取得
3. Search: ビジネスエージェントを検索

技術的な設計:
- Pydantic モデルで型安全性とバリデーションを保証
- ユニオン型 (Action) で複数のアクションタイプをまとめる
- discriminator="type" により、JSONから正しいクラスへ自動デシリアライズ
"""

from enum import Enum
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, Field
from pydantic.type_adapter import TypeAdapter

from magentic_marketplace.platform.shared.models import BaseAction

from ..shared.models import BusinessAgentProfile, SearchConstraints
from .messaging import Message


class SendMessage(BaseAction):
    """Send a message to another agent.

    他のエージェントにメッセージを送信するアクション。

    このアクションは、エージェント間のコミュニケーションの基本単位です。
    顧客がビジネスに質問を送ったり、ビジネスが顧客に提案を送る際に使用されます。

    メッセージフロー:
    1. 送信側エージェントが SendMessage アクションを作成
    2. マーケットプレイスサーバーがアクションを受信
    3. サーバーがメッセージをデータベースに保存
    4. 受信側エージェントが FetchMessages で取得可能になる

    フィールド説明:
    - from_agent_id: 送信者のエージェントID（通常は自動設定）
    - to_agent_id: 受信者のエージェントID（送信先を指定）
    - created_at: メッセージ作成時刻（タイムゾーン情報を含む）
    - message: メッセージの内容（TextMessage、OrderProposal、Paymentのいずれか）
    """

    type: Literal["send_message"] = "send_message"
    # アクション識別子（常に "send_message"）
    from_agent_id: str = Field(description="ID of the agent sending the message")
    # 送信者エージェントのID
    to_agent_id: str = Field(description="ID of the agent to send the message to")
    # 受信者エージェントのID
    created_at: AwareDatetime = Field(description="When the message was created")
    # メッセージ作成日時（UTCまたはタイムゾーン付き）
    message: Message = Field(description="The message to send")
    # メッセージの内容（ユニオン型: TextMessage | OrderProposal | Payment）


class FetchMessages(BaseAction):
    """Get messages received by this agent.

    このエージェント宛てに届いたメッセージを取得するアクション。

    エージェントは定期的にこのアクションを実行して、新しいメッセージを確認します。
    フィルタリングやページネーションのオプションを使って、効率的にメッセージを取得できます。

    使用パターン:
    1. ポーリング方式: 定期的に after_index を使って新着メッセージのみ取得
    2. 履歴取得: limit と offset を使ってページごとに過去メッセージを取得
    3. 特定送信者: from_agent_id でフィルタリングして特定のエージェントからのメッセージのみ取得

    効率的な新着チェック:
    - after_index を使うと、前回取得した最後のメッセージ以降の新着のみ取得
    - データベースのインデックスを活用して高速にクエリ可能
    """

    type: Literal["fetch_messages"] = "fetch_messages"
    # アクション識別子（常に "fetch_messages"）
    from_agent_id: str | None = Field(
        default=None, description="Filter by sender agent ID"
    )
    # 送信者でフィルタリング（指定すると特定エージェントからのメッセージのみ取得）
    limit: int | None = Field(
        default=None, description="Maximum number of messages to retrieve"
    )
    # 取得するメッセージの最大数（ページネーション用）
    offset: int | None = Field(
        default=None, description="Number of messages to skip for pagination"
    )
    # スキップするメッセージ数（ページネーション用、limit と組み合わせて使用）
    after: AwareDatetime | None = Field(
        default=None, description="Only return messages sent after this timestamp"
    )
    # 指定時刻より後のメッセージのみ取得（時刻ベースのフィルタリング）
    after_index: int | None = Field(
        default=None, description="Only return messages with index greater than this"
    )
    # 指定インデックスより大きいメッセージのみ取得（効率的な新着チェック用）


class ReceivedMessage(BaseModel):
    """A message as received by an agent with metadata.

    受信したメッセージとそのメタデータ。

    FetchMessages アクションのレスポンスに含まれるメッセージの形式です。
    送信時の情報に加えて、受信側の視点で必要なメタデータが付与されます。
    """

    from_agent_id: str = Field(description="ID of the agent who sent the message")
    # メッセージの送信者ID
    to_agent_id: str = Field(description="ID of the agent who received the message")
    # メッセージの受信者ID（通常は自分自身）
    created_at: AwareDatetime = Field(description="When the message was created")
    # メッセージの作成日時
    message: Message = Field(description="The actual message content")
    # メッセージの実際の内容（TextMessage、OrderProposal、Paymentのいずれか）
    index: int = Field(description="The row index of the message")
    # データベース内のメッセージの行番号（after_index でのフィルタリングに使用）


class FetchMessagesResponse(BaseModel):
    """Response from fetching messages.

    メッセージ取得アクションのレスポンス。

    FetchMessages アクションの実行結果として返されます。
    ページネーションをサポートするため、さらにメッセージがあるかを示すフラグを含みます。
    """

    messages: list[ReceivedMessage] = Field(description="List of received messages")
    # 取得されたメッセージのリスト（最新順または古い順、クエリに依存）
    has_more: bool = Field(description="Whether there are more messages available")
    # まだ取得していないメッセージがあるかどうか（ページネーションの継続判定用）


class SearchAlgorithm(str, Enum):
    """Available search algorithms.

    利用可能な検索アルゴリズム。

    マーケットプレイスでビジネスを検索する際のアルゴリズムを指定します。
    それぞれ異なる検索戦略と精度・速度のトレードオフを持ちます。

    アルゴリズムの種類:
    - SIMPLE: シンプルなマッチング（基本的な文字列検索）
    - RNR: Retrieve-and-Rank（検索後にLLMでランキング）
    - FILTERED: フィルタベース検索（制約条件による絞り込み）
    - LEXICAL: 字句検索（キーワードベースのマッチング）
    - OPTIMAL: 最適化検索（効用最大化を目指す）
    """

    SIMPLE = "simple"
    RNR = "rnr"
    FILTERED = "filtered"
    LEXICAL = "lexical"
    OPTIMAL = "optimal"


class Search(BaseAction):
    """Search for businesses in the marketplace.

    マーケットプレイス内のビジネスを検索するアクション。

    顧客エージェントが自分のニーズに合ったビジネスを見つけるために使用します。
    検索アルゴリズムや制約条件を指定して、最適なビジネスを見つけることができます。

    検索の流れ:
    1. 顧客エージェントが検索クエリと条件を指定
    2. マーケットプレイスが指定されたアルゴリズムで検索を実行
    3. マッチするビジネスのリストが返される
    4. 顧客が結果を評価し、メッセージを送信するビジネスを選択
    """

    type: Literal["search"] = "search"
    # アクション識別子（常に "search"）
    query: str = Field(description="Search query")
    # 検索クエリ（自然言語での要求、例: "辛いメキシコ料理を食べたい"）
    search_algorithm: SearchAlgorithm = Field(description="Search algorithm to use")
    # 使用する検索アルゴリズム（SIMPLE、RNR、FILTEREDなど）
    constraints: SearchConstraints | None = Field(
        default=None, description="Search constraints"
    )
    # 検索制約条件（価格範囲、必須設備など、任意）
    limit: int = Field(default=10, description="Maximum number of results to return")
    # 返す結果の最大数（デフォルト: 10）
    page: int = Field(default=1, description="Page number for pagination")
    # ページ番号（ページネーション用、1始まり）


class SearchResponse(BaseModel):
    """Result of a business search operation.

    ビジネス検索操作の結果。

    Search アクションの実行結果として返されます。
    マッチしたビジネスのリストと、ページネーション情報を含みます。
    """

    businesses: list[BusinessAgentProfile]
    # 検索にマッチしたビジネスのリスト（プロフィール情報を含む）
    search_algorithm: str
    # 使用された検索アルゴリズム（記録用）
    total_possible_results: int | None = Field(
        default=None, description="Total number of possible results"
    )
    # マッチする結果の総数（すべてのページを含む、アルゴリズムによっては提供されない）
    total_pages: int | None = Field(
        default=None, description="Total number of pages available"
    )
    # 利用可能な総ページ数（ページネーション用、アルゴリズムによっては提供されない）


# Action is a union type of the action types
# Action は複数のアクションタイプのユニオン型
# "type"フィールドを使って、JSONから適切なアクションクラスに自動デシリアライズされる
Action = Annotated[SendMessage | FetchMessages | Search, Field(discriminator="type")]

# Type adapter for Action for serialization/deserialization
# Action のシリアライズとデシリアライズのための TypeAdapter
# JSONとPythonオブジェクト間の変換を処理し、型安全性を保証する
ActionAdapter: TypeAdapter[Action] = TypeAdapter(Action)
