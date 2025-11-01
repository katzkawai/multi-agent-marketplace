"""Data models for the customer agent.

カスタマーエージェントのデータモデル
----------------------------------
このモジュールは、LLMからの出力を構造化するためのPydanticモデルを定義します。
プロンプトエンジニアリングにおける「Structured Output」戦略の実装です。

なぜ構造化出力が重要か:
1. LLMの出力を予測可能にする: 自由形式のテキストではなく、決まった形式で出力
2. バリデーション: Pydanticが自動的に型チェックと値の検証を行う
3. パース不要: JSON文字列を手動でパースする必要がない
4. エラー処理: 無効な出力を早期に検出できる

主要なモデル:
- CustomerAction: LLMが次に取るアクションを表現（search/send_messages/check_messages/end）
- Messages: 送信するメッセージの集合（text_messages と pay_messages）
- AssistantTextMessageRequest: テキストメッセージ（質問や関心表明用）
- AssistantPayMessageRequest: 支払いメッセージ（提案の受諾用）

LLMは CustomerAction を JSON形式で出力し、それがこのモデルにパースされます。
"""

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from magentic_marketplace.platform.shared.models import ActionExecutionResult

from ...actions.actions import FetchMessagesResponse, SearchResponse
from ...actions.messaging import Payment, TextMessage


@dataclass
class CustomerSendMessageResults:
    """Internal dataclass for storing the results of sending messages in CustomerAction format.

    メッセージ送信結果の内部データクラス
    ----------------------------------
    send_messagesアクションの実行結果を格納します。

    各メッセージの結果を (成功/失敗のbool, エラーメッセージのstr) のタプルで保持:
    - text_message_results: テキストメッセージの送信結果リスト
    - pay_message_results: 支払いメッセージの送信結果リスト

    この結果は format_event_history でフォーマットされ、
    次回のプロンプトに含まれてLLMにフィードバックされます。
    """

    text_message_results: list[tuple[bool, str]] = field(default_factory=list)
    pay_message_results: list[tuple[bool, str]] = field(default_factory=list)


class AssistantTextMessageRequest(TextMessage):
    """Request for sending a text message.

    テキストメッセージリクエスト
    --------------------------
    顧客エージェントがビジネスに質問や関心を伝えるためのメッセージ。

    フィールド:
    - to_business_id: 送信先のビジネスID（LLMが指定）
    - content: メッセージの内容（親クラスTextMessageから継承）
    - type: メッセージタイプ "text"（親クラスから継承）

    使用例: 「デリバリーはできますか？」「タコス3個いくらですか？」
    """

    to_business_id: str = Field(
        description="The id of the business this message should be sent to."
    )


class AssistantPayMessageRequest(Payment):
    """Request for sending a payment message to accept an order proposal.

    支払いメッセージリクエスト
    ------------------------
    顧客エージェントがビジネスの提案を受諾するための支払いメッセージ。

    フィールド:
    - to_business_id: 送信先のビジネスID（LLMが指定）
    - proposal_id: 受諾する提案のID（受信したorder_proposalのmessage_id）
    - amount: 支払い額（親クラスPaymentから継承）
    - type: メッセージタイプ "pay"（親クラスから継承）

    重要: proposal_idは受信したorder_proposalメッセージのmessage_idと一致する必要があります。
    これにより、どの提案を受諾するかを明確に特定できます。
    """

    to_business_id: str = Field(
        description="The id of the business this message should be sent to."
    )


class Messages(BaseModel):
    """Messages to be sent to services.

    Use text messages for general inquiries and pay messages to accept order proposals.

    メッセージコンテナ
    ----------------
    send_messagesアクションで送信するメッセージの集合を表します。

    フィールド:
    - text_messages: テキストメッセージのリスト（複数のビジネスに同時に質問可能）
    - pay_messages: 支払いメッセージのリスト（複数の提案を同時に受諾可能、通常は1つ）

    Pydanticのリスト型により、LLMは1つまたは複数のメッセージを柔軟に指定できます。
    空のリストも許可されていますが、validator で少なくとも1つは必要とチェックします。
    """

    text_messages: list[AssistantTextMessageRequest]
    pay_messages: list[AssistantPayMessageRequest]


class CustomerAction(BaseModel):
    """Actions the Assistant can take.

    Use:
        - search_businesses to search for businesses.
        - send_messages to send messages to some businesses.
        - check_messages to check for new responses from businesses.
        - end_transaction if you have paid for an order or received confirmation.

    Do not end if you haven't completed a purchase transaction.

    カスタマーアクション（LLMの出力スキーマ）
    --------------------------------------
    これが最も重要なモデルです。LLMはこのスキーマに従ってJSON出力を生成します。

    Structured Output の実装方法:
    1. Literal型でアクションタイプを制限: "search_businesses" | "send_messages" | "check_messages" | "end_transaction"
    2. Field(description=...) でLLMに各フィールドの意味を説明
    3. Optional (| None) フィールドで、アクションに応じて必要なフィールドを変える
    4. model_validator でクロスフィールドバリデーション

    LLMへの指示の仕組み:
    - このクラスのdocstringとField descriptionがプロンプトに含まれる
    - LLMは "action_type": "search_businesses" のように出力
    - Pydanticが自動的にバリデーションを行い、無効な出力をエラーとして返す

    例:
    {
        "action_type": "search_businesses",
        "reason": "顧客がメキシコ料理を探しているため",
        "search_query": "Mexican restaurants with delivery",
        "search_page": 1
    }
    """

    action_type: Literal[
        "search_businesses", "send_messages", "check_messages", "end_transaction"
    ] = Field(description="Type of action to take")
    reason: str = Field(description="Reason for taking this action")

    # Search-specific fields
    # 検索専用フィールド（action_type="search_businesses"の場合のみ必須）
    search_query: str | None = Field(
        default=None,
        description="Search query for businesses.",
    )
    search_page: int = Field(
        default=1,
        description="Page number to retrieve for the search results (default: 1)",
    )

    # Message-specific field
    # メッセージ専用フィールド（action_type="send_messages"の場合のみ必須）
    messages: Messages | None = Field(
        default=None,
        description="Messages container with text and pay message lists",
    )

    @model_validator(mode="after")
    def validate_model(self):
        """Validate the BaseModel structure.

        クロスフィールドバリデーション
        ----------------------------
        アクションタイプに応じて、必要なフィールドが存在するかチェックします。

        - search_businesses: search_queryが必須
        - send_messages: messagesが必須

        これにより、LLMが不完全なアクションを生成した場合にエラーを返し、
        再試行を促すことができます。
        """
        if self.action_type == "search_businesses":
            if not self.search_query:
                raise ValueError(
                    "search_query is required when action_type is search_businesses"
                )
        elif self.action_type == "send_messages":
            if not self.messages:
                raise ValueError(
                    "messages must have at least one element when action_type is send_messages"
                )

        return self


class CustomerSummary(BaseModel):
    """Summary of customer transactions and activity.

    カスタマーサマリー
    ----------------
    実験終了後の顧客の活動サマリーを表すモデル。
    分析とレポート生成に使用されます。

    このモデルはLLMの出力ではなく、システムが実験結果を集計して生成します。
    """

    customer_id: str = Field(description="Customer ID")
    customer_name: str = Field(description="Customer name")
    request: str = Field(description="Original customer request")
    profile: dict[str, Any] = Field(description="Full customer profile data")
    proposals_received: int = Field(description="Number of proposals received")
    transactions_completed: int = Field(description="Number of completed transactions")
    completed_proposal_ids: list[str] = Field(description="IDs of completed proposals")


# Type alias for action execution results
# CustomerActionの実行結果として返される可能性のある型のUnion
CustomerActionResult = (
    ActionExecutionResult  # エラー結果（アクション実行失敗時）
    | SearchResponse  # search_businessesの成功結果
    | CustomerSendMessageResults  # send_messagesの結果
    | FetchMessagesResponse  # check_messagesの成功結果
)
