"""Data models for the business agent.

ビジネスエージェントのデータモデル
--------------------------------
このモジュールは、ビジネスエージェントのLLM出力を構造化するための
Pydanticモデルを定義します。

カスタマーエージェントのモデルとの違い:
1. よりシンプルな構造: アクションは "text" か "order_proposal" の2種類のみ
2. search や check_messages は不要: ビジネスは顧客を検索せず、問い合わせを待つだけ
3. order_proposal の作成能力: 具体的な注文提案を構造化して送信できる

主要なモデル:
- BusinessAction: LLMが選択するアクション（textまたはorder_proposal）
- ServiceTextMessageRequest: テキスト応答用のメッセージ
- ServiceOrderProposalMessageRequest: 注文提案用のメッセージ

プロンプトエンジニアリングとの関係:
- prompts.py で生成されたプロンプトにより、LLMは BusinessAction を出力
- action_type で "text" と "order_proposal" を使い分ける
- OrderProposal には items, total_price, special_instructions などが含まれる
"""

from typing import Literal

from pydantic import BaseModel, Field

from ...actions import OrderProposal, TextMessage


class ServiceTextMessageRequest(TextMessage):
    """Request for sending a text service message.

    サービステキストメッセージリクエスト
    ----------------------------------
    ビジネスが顧客に送るテキストメッセージ（一般的な回答や質問への返信）。

    フィールド:
    - to_customer_id: 送信先の顧客ID（LLMがプロンプトから取得）
    - content: メッセージの内容（親クラスTextMessageから継承）
    - type: メッセージタイプ "text"（親クラスから継承）

    使用例: 「はい、デリバリー可能です！」「タコスは$3.50です」

    カスタマーエージェントのAssistantTextMessageRequestとの違い:
    - to_customer_id vs to_business_id: 送信先が逆
    - 内容: ビジネスからの回答 vs 顧客からの質問
    """

    to_customer_id: str = Field(
        description="The id of the customer this message should be sent to."
    )


class ServiceOrderProposalMessageRequest(OrderProposal):
    """Request for sending an order proposal message.

    サービス注文提案メッセージリクエスト
    ----------------------------------
    ビジネスが顧客に送る具体的な注文提案。

    フィールド:
    - to_customer_id: 送信先の顧客ID（LLMがプロンプトから取得）
    - items: 注文アイテムのリスト（OrderItemのリスト、親クラスから継承）
    - total_price: 合計金額（親クラスから継承）
    - special_instructions: 特別な指示や注意事項（親クラスから継承）
    - estimated_delivery: 配達予定時刻（親クラスから継承）
    - type: メッセージタイプ "order_proposal"（親クラスから継承）

    OrderItemの構造:
    - id: メニューアイテムID（"Item-1", "Item-2"など）
    - item_name: アイテム名（"Taco", "Burrito"など）
    - quantity: 数量
    - unit_price: 単価（メニューの価格と一致する必要がある）

    重要なバリデーション（analytics時にチェック）:
    1. アイテム名はメニューに存在する必要がある
    2. 価格はメニューの価格と一致する必要がある
    3. total_price = Σ(quantity × unit_price) の計算が正しい必要がある

    これらのバリデーションに失敗すると「無効な提案」としてカウントされます。
    """

    to_customer_id: str = Field(
        description="The id of the customer this message should be sent to."
    )


class BusinessAction(BaseModel):
    """Actions the service agent can take.

    ビジネスアクション（LLMの出力スキーマ）
    ------------------------------------
    ビジネスエージェントが顧客に応答するためのアクション。

    Structured Output の実装:
    1. action_type: "text" または "order_proposal" の2択（Literal型で制限）
    2. text_message: action_type="text"の場合に使用
    3. order_proposal_message: action_type="order_proposal"の場合に使用

    LLMへの指示の仕組み:
    - プロンプトで「いつorder_proposalを使うべきか」を明示的に指示
    - Field descriptionがLLMに各フィールドの意味を説明
    - Optional (| None) により、片方のみが必須となる

    出力例1（テキスト応答）:
    {
        "action_type": "text",
        "text_message": {
            "to_customer_id": "customer-1",
            "type": "text",
            "content": "はい、デリバリー可能です！"
        }
    }

    出力例2（注文提案）:
    {
        "action_type": "order_proposal",
        "order_proposal_message": {
            "to_customer_id": "customer-1",
            "type": "order_proposal",
            "items": [
                {"id": "Item-1", "item_name": "Taco", "quantity": 3, "unit_price": 3.50}
            ],
            "total_price": 10.50,
            "special_instructions": "Extra salsa included"
        }
    }

    カスタマーエージェントのCustomerActionとの違い:
    - よりシンプル: アクションタイプが2種類のみ
    - 提案作成能力: order_proposalでビジネスが主導的に提案を作成
    - 検索不要: searchやcheck_messagesは不要（リアクティブな対応のみ）
    """

    action_type: Literal["text", "order_proposal"] = Field(
        description="Type of action to take"
    )

    text_message: ServiceTextMessageRequest | None = None
    order_proposal_message: ServiceOrderProposalMessageRequest | None = None


class BusinessSummary(BaseModel):
    """Summary of business operations.

    ビジネスサマリー
    --------------
    実験終了後のビジネスの活動サマリーを表すモデル。
    分析とレポート生成に使用されます。

    このモデルはLLMの出力ではなく、システムが実験結果を集計して生成します。
    ビジネスの業績（提案数、受注数、売上など）を追跡するために使用されます。
    """

    business_id: str = Field(description="Business ID")
    business_name: str = Field(description="Business name")
    description: str = Field(description="Business description")
    rating: float = Field(description="Business rating")
    menu_items: int = Field(description="Number of menu items available")
    amenities: int = Field(description="Number of amenities offered")
    pending_proposals: int = Field(description="Number of pending proposals")
    confirmed_orders: int = Field(description="Number of confirmed orders")
    delivery_available: bool = Field(description="Whether delivery is available")
