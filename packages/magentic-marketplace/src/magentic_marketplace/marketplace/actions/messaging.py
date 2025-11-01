"""Messaging actions for the simple marketplace.

シンプルなマーケットプレイス用のメッセージングアクション。

このモジュールは、マーケットプレイスにおけるエージェント間のメッセージ交換のための
構造化されたメッセージタイプを定義します。メッセージは型安全性を保証し、
ビジネスエージェントと顧客エージェント間の明確なコミュニケーションプロトコルを
提供します。
"""

from typing import Annotated, Literal

from pydantic import BaseModel, Field
from pydantic.type_adapter import TypeAdapter


class OrderItem(BaseModel):
    """An item in an order with quantity and pricing."""

    """注文における商品アイテムとその数量・価格情報。

    注文提案（OrderProposal）に含まれる個別の商品を表します。
    各アイテムは一意のID、商品名、数量、単価を持ちます。
    """

    id: str = Field(description="Menu item ID from the business")
    # メニューアイテムの一意識別子（ビジネスエージェントが定義）
    item_name: str = Field(description="Name of the item")
    # 商品の表示名（例: "タコス", "ブリトー"）
    quantity: int = Field(description="Quantity ordered", ge=1)
    # 注文数量（1以上の整数）
    unit_price: float = Field(description="Price per unit", ge=0)
    # 1つあたりの価格（0以上の浮動小数点数）


class TextMessage(BaseModel):
    """A text message."""

    """テキストメッセージ。

    エージェント間の自由形式のテキスト通信に使用されます。
    顧客からの質問、ビジネスからの回答、その他一般的なコミュニケーションに利用。
    """

    type: Literal["text"] = "text"
    # メッセージタイプの識別子（常に "text"）
    content: str = Field(description="Text content of the message")
    # メッセージの本文テキスト


class OrderProposal(BaseModel):
    """Order proposal details sent by service agents to customers."""

    """ビジネスエージェントが顧客に送信する注文提案の詳細情報。

    このメッセージタイプは、マーケットプレイスにおける中核的な取引メカニズムです。
    ビジネスは顧客のリクエストに応じて、具体的な商品リスト、価格、配達情報を含む
    構造化された提案を生成します。

    重要な構造的要素:
    - items: 注文する各商品の詳細（OrderItemのリスト）
    - total_price: 全商品の合計金額（各アイテムの quantity × unit_price の総和）
    - id: この提案の一意識別子（支払い時に参照される）

    構造化メッセージが重要な理由:
    1. データ検証: 価格や数量の妥当性を自動チェック可能
    2. 分析可能性: 実験後に提案内容を構造的に分析できる
    3. エラー検出: 不正な形式や矛盾するデータを早期発見
    4. 一貫性: すべてのビジネスが同じフォーマットで提案を送信
    """

    type: Literal["order_proposal"] = "order_proposal"
    # メッセージタイプの識別子（常に "order_proposal"）
    id: str = Field(description="The unique id of this proposal", min_length=1)
    # この提案の一意識別子（顧客が支払い時に参照するID）
    items: list[OrderItem] = Field(
        min_length=1,
        description="Required; the list of OrderItem objects with item_name, quantity, and unit_price",
    )
    # 注文商品のリスト（必須、最低1つの商品が必要）
    # 各OrderItemには item_name（商品名）、quantity（数量）、unit_price（単価）が含まれる
    total_price: float = Field(description="Required; total price for the entire order")
    # 注文全体の合計金額（必須）
    # 分析時に各アイテムの（quantity × unit_price）の総和と比較され、計算誤りを検出
    special_instructions: str | None = Field(
        default=None, description="Optional; any special requests or notes"
    )
    # 特別な指示やメモ（任意）。例: "辛さ控えめ"、"アレルギー対応"
    estimated_delivery: str | None = Field(
        default=None, description="Optional; estimated delivery time"
    )
    # 配達予定時刻（任意）。例: "30分"、"19:00頃"
    expiry_time: str | None = Field(
        default=None, description="Optional; when this proposal expires"
    )
    # 提案の有効期限（任意）。この時刻を過ぎると提案は無効となる


class Payment(BaseModel):
    """A payment message to accept an order proposal."""

    """注文提案を受け入れるための支払いメッセージ。

    顧客エージェントがビジネスエージェントからの注文提案（OrderProposal）を
    承諾する際に送信する構造化メッセージです。

    重要なフロー:
    1. ビジネスが OrderProposal を送信（一意のIDを含む）
    2. 顧客がメッセージを取得して内容を評価
    3. 顧客が承諾する場合、その提案IDを参照してPaymentを送信
    4. マーケットプレイスが提案の存在と有効性を検証
    5. 検証成功で取引が完了し、効用分析に使用される
    """

    type: Literal["payment"] = "payment"
    # メッセージタイプの識別子（常に "payment"）
    proposal_message_id: str = Field(
        description="ID of the message containing the order proposal to accept"
    )
    # 受け入れる注文提案のメッセージID（OrderProposal.idに対応）
    # このIDを使って、マーケットプレイスは対応する提案を検索・検証する
    payment_method: str | None = Field(
        default=None,
        description="Payment method to use (e.g., 'credit_card', 'cash', 'digital_wallet')",
    )
    # 支払い方法（任意）。例: "credit_card"、"cash"、"digital_wallet"
    delivery_address: str | None = Field(
        default=None, description="Delivery address if different from customer profile"
    )
    # 配送先住所（任意）。顧客プロフィールと異なる場合に指定
    payment_message: str | None = Field(
        default=None, description="Additional message to include with the payment"
    )
    # 支払いに添える追加メッセージ（任意）。例: "ありがとうございます"


# Message is a union type of the message types
# メッセージは3つのメッセージタイプのユニオン型
# "type"フィールドを使って適切なメッセージクラスに自動的にデシリアライズされる
Message = Annotated[TextMessage | OrderProposal | Payment, Field(discriminator="type")]

# Type adapter for Message for serialization/deserialization
# MessageのシリアライズとデシリアライズのためのTypeAdapter
# JSONとPythonオブジェクト間の変換を処理し、型安全性を保証する
MessageAdapter: TypeAdapter[Message] = TypeAdapter(Message)
