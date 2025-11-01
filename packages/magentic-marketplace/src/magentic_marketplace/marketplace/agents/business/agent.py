"""Main business agent implementation."""

"""
ビジネスエージェントのメイン実装

このモジュールは、マーケットプレイスで顧客からの問い合わせに応答し、
注文提案を作成し、支払いを処理するビジネスエージェントを実装します。

【ビジネスエージェントの動作フロー】

1. 初期化（__init__）:
   - ビジネス情報（メニュー、設備、価格など）を設定
   - 会話履歴管理システムを初期化
   - 提案ストレージを初期化（送信した提案を追跡）
   - ResponseHandlerを設定（LLMによる応答生成を担当）

2. メインループ（step）:
   - マーケットプレイスから新しいメッセージをポーリング（定期的に確認）
   - 受信したメッセージを顧客ごとにグループ化
   - 各顧客からのメッセージを並列処理
   - メッセージがない場合は polling_interval 秒待機

3. メッセージ処理（_handle_new_customer_messages）:
   a. 支払いメッセージの処理:
      - 支払いを検証し、提案の状態を更新
      - 確認メッセージを生成

   b. テキストメッセージ（問い合わせ）の処理:
      - 会話履歴をLLMに渡す
      - LLMが顧客のニーズを理解し、適切な応答を生成
      - 応答はTextMessage（通常のメッセージ）またはOrderProposal（注文提案）

   c. すべてのメッセージを会話履歴に記録
   d. 生成した応答を顧客に送信

4. 注文提案の生成（ResponseHandler経由）:
   - 顧客のリクエストを分析
   - 自身のメニューと価格に基づいて提案を作成
   - OrderProposalオブジェクトとして構造化（商品、数量、価格、合計など）
   - 提案をストレージに保存（後で支払い検証に使用）

5. 支払い処理（_handle_payment）:
   - 支払いに含まれる提案IDを検証
   - 提案が存在し、状態が「pending（保留中）」であることを確認
   - 支払いを受け入れ、状態を「accepted（承認済み）」に更新
   - 確定した注文として記録（ビジネスの収益）
   - 確認メッセージを顧客に送信

【重要な設計ポイント】

- 会話履歴の管理: 各顧客との全てのやり取りを記録し、文脈を理解した応答を可能にする
- LLMの活用: 自然言語での問い合わせを理解し、適切な提案を生成
- 非同期処理: 複数の顧客からのメッセージを効率的に並列処理
- 状態管理: 提案の状態（pending/accepted/rejected）を追跡し、二重支払いを防止
- エラーハンドリング: メッセージ送信の失敗やデータの不整合を適切に処理

【データの流れ】

顧客 → マーケットプレイス → BusinessAgent.fetch_messages()
                                      ↓
                            _handle_new_customer_messages()
                                      ↓
                              +-------+-------+
                              |               |
                         Payment         TextMessage
                              |               |
                      _handle_payment()  ResponseHandler
                              |          (LLM呼び出し)
                              |               |
                              +-------+-------+
                                      ↓
                               send_message()
                                      ↓
                            マーケットプレイス → 顧客
"""

import asyncio
from collections import defaultdict
from typing import Literal

from ...actions import (
    Message,
    OrderProposal,
    Payment,
    ReceivedMessage,
    TextMessage,
)
from ...llm.config import BaseLLMConfig
from ...shared.models import Business, BusinessAgentProfile
from ..base import BaseSimpleMarketplaceAgent
from ..proposal_storage import OrderProposalStorage
from .models import BusinessSummary
from .responses import ResponseHandler


class BusinessAgent(BaseSimpleMarketplaceAgent[BusinessAgentProfile]):
    """Business agent that responds to customer inquiries and creates proposals."""

    """
    ビジネスエージェントクラス

    顧客からの問い合わせに応答し、注文提案を作成するエージェントです。
    主な機能:
    - 顧客からのメッセージの受信と応答
    - LLMを使用した提案の生成
    - 支払いの処理と注文の確認
    - 各顧客との会話履歴の管理
    """

    def __init__(
        self,
        business: Business,
        base_url: str,
        llm_config: BaseLLMConfig | None = None,
        polling_interval: float = 2,
    ):
        """Initialize the business agent.

        Args:
            business: Business object with menu and capabilities
            base_url: The marketplace server URL
            llm_config: LLM configuration for the agent
            polling_interval: Number of seconds to wait after fetching 0 messages.

        """
        """
        ビジネスエージェントを初期化します。

        Args:
            business: メニューと機能を持つビジネスオブジェクト
            base_url: マーケットプレイスサーバーのURL
            llm_config: エージェントのLLM設定
            polling_interval: メッセージが0件の場合に待機する秒数
        """
        # ビジネス情報からプロファイルを作成
        profile = BusinessAgentProfile.from_business(business)
        super().__init__(profile, base_url, llm_config)

        # Initialize state from BaseBusinessAgent
        # ビジネスエージェントの状態を初期化

        # 各顧客との会話履歴を保存する辞書
        # キー: 顧客ID、値: メッセージのリスト
        self.customer_histories: dict[str, list[str]] = defaultdict(list)

        # 送信した提案を管理するストレージ
        # 提案の状態（pending/accepted/rejected）を追跡
        self.proposal_storage = OrderProposalStorage()

        # 確定した注文のIDリスト
        self.confirmed_orders: list[str] = []

        # メッセージをポーリングする間隔（秒）
        self._polling_interval = polling_interval

        # Initialize handler resources
        # レスポンスハンドラーを初期化
        # このハンドラーがLLMを使用して顧客への応答を生成します
        self._responses = ResponseHandler(
            business=self.business,
            agent_id=self.id,
            proposal_storage=self.proposal_storage,
            logger=self.logger,
            generate_struct_fn=self.generate_struct,  # LLMによる構造化データ生成関数
        )

    @property
    def business(self) -> Business:
        """Access business data from profile with full type safety."""
        """
        ビジネスデータへのアクセス

        型安全性を保ちながら、プロファイルからビジネスデータにアクセスします。
        """
        return self.profile.business

    async def _handle_new_customer_messages(
        self, customer_id: str, new_messages: list[ReceivedMessage]
    ):
        """顧客からの新しいメッセージを処理します。.

        処理フロー:
        1. 支払いメッセージを優先的に処理（提案の状態を更新）
        2. テキストメッセージに対してLLMで応答を生成
        3. すべてのメッセージを会話履歴に追加
        4. 生成した応答を顧客に送信

        Args:
            customer_id: 顧客のID
            new_messages: 受信した新しいメッセージのリスト

        """
        # 送信するメッセージのリスト（顧客ID、メッセージ）のタプル
        messages_to_send: list[tuple[str, Message]] = []

        # First, handle all payments to update proposal statuses
        # まず、すべての支払いを処理して提案の状態を更新します
        last_text_message: TextMessage | None = None

        # 受信したメッセージを順に処理
        for received_message in new_messages:
            # 支払いメッセージの場合
            if isinstance(received_message.message, Payment):
                # 支払いを処理し、確認メッセージを生成
                response = await self._handle_payment(
                    customer_id, received_message.message
                )
                messages_to_send.append((customer_id, response))

            # テキストメッセージの場合（顧客からの問い合わせ）
            elif isinstance(received_message.message, TextMessage):
                # 最後のテキストメッセージを記録（複数ある場合は最新のものに応答）
                last_text_message = received_message.message

            # すべてのメッセージを顧客との会話履歴に追加
            self.add_to_history(
                customer_id,
                received_message.message,
                "customer",
            )

        # Generate a text response if there are any text messages
        # テキストメッセージがある場合、LLMを使用して応答を生成
        if last_text_message is not None:
            # ResponseHandlerがLLMを呼び出して、会話履歴に基づいた応答を生成
            # 応答はTextMessageまたはOrderProposal（注文提案）になる可能性がある
            response_message = await self._responses.generate_response_to_inquiry(
                customer_id, self.customer_histories[customer_id]
            )
            messages_to_send.append((customer_id, response_message))

        # Send all generated responses
        # 生成したすべての応答を送信
        for customer_id, message in messages_to_send:
            # If this is a proposal, store it in proposal storage
            # 注文提案の場合、提案ストレージに保存
            # これにより、後で支払い時に提案を検証できる
            if isinstance(message, OrderProposal):
                self.proposal_storage.add_proposal(message, self.id, customer_id)

            # FUTURE -- add retries on message fail.
            # 将来的には、メッセージ送信失敗時の再試行を追加予定
            try:
                # マーケットプレイスサーバーにメッセージを送信
                result = await self.send_message(customer_id, message)
                if result.is_error:
                    # エラーの場合、ログに記録し履歴に追加
                    error_msg = f"Error: Failed to send message to {customer_id}: {result.content}"
                    self.logger.error(error_msg)
                    self.add_to_history(customer_id, error_msg, "business")

                else:
                    # 成功した場合、送信したメッセージを履歴に追加
                    self.add_to_history(customer_id, message, "business")
            except Exception as e:
                # 例外が発生した場合、詳細をログに記録
                error_msg = f"Error: Failed to send message to {customer_id}: {e}"
                self.logger.exception(error_msg)
                self.add_to_history(customer_id, error_msg, "business")

    def add_to_history(
        self,
        customer_id: str,
        message: Message | str,
        customer_or_agent: Literal["customer", "business"],
    ):
        """Add a message to the customer's history.

        Args:
            customer_id: ID of the customer
            message: The message to add
            customer_or_agent: Whether the message is from the customer or the agent

        """
        """
        メッセージを顧客の会話履歴に追加します。

        会話履歴はLLMに渡され、文脈を理解した応答を生成するために使用されます。
        各メッセージは「Customer:」または「You:」のプレフィックス付きで保存されます。

        Args:
            customer_id: 顧客のID
            message: 追加するメッセージ（Message型または文字列）
            customer_or_agent: メッセージの送信者（"customer" または "business"）
        """
        # メッセージのプレフィックスを設定
        # 顧客からのメッセージは "Customer:"、ビジネスからのメッセージは "You:"
        prefix = "Customer" if customer_or_agent == "customer" else "You"
        formatted_message = None

        # メッセージの型に応じてフォーマット
        if isinstance(message, str):
            # 文字列の場合はそのまま使用
            formatted_message = f"{prefix}: {message}"
        elif isinstance(message, TextMessage):
            # テキストメッセージの場合、content属性を使用
            formatted_message = f"{prefix}: {message.content}"
        elif isinstance(message, Payment):
            # 支払いメッセージの場合、辞書形式に変換
            formatted_message = f"{prefix}: {message.model_dump(exclude_none=True)}"
        elif isinstance(message, OrderProposal):
            # 注文提案の場合、辞書形式に変換
            # 提案内容（商品、価格、合計など）がすべて含まれる
            formatted_message = f"{prefix}: {message.model_dump(exclude_none=True)}"
        else:
            # 未知のメッセージ型の場合、警告をログに記録して無視
            self.logger.warning(
                "Ignoring message in Business add_to_history: ", message
            )

        # フォーマットされたメッセージを会話履歴に追加
        if formatted_message is not None:
            self.customer_histories[customer_id].append(formatted_message)

    async def step(self):
        """One step of business agent logic - check for and handle customer messages."""
        """
        ビジネスエージェントの1ステップを実行します。

        メインループから繰り返し呼び出され、以下の処理を行います:
        1. マーケットプレイスから新しいメッセージを取得
        2. 顧客ごとにメッセージをグループ化
        3. 各顧客からのメッセージを並列処理
        4. メッセージがない場合は待機

        これがビジネスエージェントの中核的な実行ループです。
        """
        # Check for new messages
        # マーケットプレイスサーバーから新しいメッセージを取得
        messages = await self.fetch_messages()

        # Group new messages by customer
        # 新しいメッセージを顧客ごとにグループ化
        # これにより、同じ顧客からの複数のメッセージを一緒に処理できる
        new_messages_by_customer: dict[str, list[ReceivedMessage]] = defaultdict(list)

        for received_message in messages.messages:
            # from_agent_idは送信者（顧客）のIDを示す
            new_messages_by_customer[received_message.from_agent_id].append(
                received_message
            )

        # メッセージがある場合、各顧客のメッセージを並列処理
        if new_messages_by_customer:
            # asyncio.gatherを使用して、複数の顧客からのメッセージを同時に処理
            # これにより、複数の顧客への対応を効率的に行える
            await asyncio.gather(
                *[
                    self._handle_new_customer_messages(customer_id, new_messages)
                    for customer_id, new_messages in new_messages_by_customer.items()
                ]
            )

        # メッセージがない場合は、次のチェックまで待機
        if len(new_messages_by_customer) == 0:
            # Wait before next check
            # polling_interval秒待機してからメッセージを再チェック
            await asyncio.sleep(self._polling_interval)
        else:
            # メッセージがあった場合は即座に次のステップへ
            # 待機時間を0にすることで、連続してメッセージを処理できる
            await asyncio.sleep(0)

    async def on_started(self):
        """Handle when the business agent starts."""
        """
        ビジネスエージェント起動時の処理

        エージェントがマーケットプレイスに参加し、顧客からのメッセージを
        受け付ける準備ができたことをログに記録します。
        """
        self.logger.info("Ready for customers")

    async def _handle_payment(self, customer_id: str, payment: Payment) -> TextMessage:
        """Handle a payment from a customer.

        Args:
            customer_id: ID of the customer
            payment: The payment message

        Returns:
            Message to send back to customer

        """
        """
        顧客からの支払いを処理します。

        支払い処理のフロー:
        1. 支払いに対応する提案をストレージから取得
        2. 提案が存在し、状態が「pending（保留中）」であることを確認
        3. 支払いを受け入れ、提案の状態を「accepted（承認済み）」に更新
        4. 確認メッセージを生成して返す
        5. エラーの場合はエラーメッセージを返す

        Args:
            customer_id: 顧客のID
            payment: 支払いメッセージ（提案IDと金額を含む）

        Returns:
            顧客に送り返すテキストメッセージ（確認またはエラー）
        """
        # 支払いメッセージから提案IDを取得
        proposal_id = payment.proposal_message_id
        self.logger.info(
            f"Processing payment for proposal {proposal_id} from customer {customer_id}"
        )

        # 提案ストレージから該当する提案を取得
        stored_proposal = self.proposal_storage.get_proposal(proposal_id)

        # 提案が存在し、状態が「pending（保留中）」の場合のみ受け入れ
        if stored_proposal and stored_proposal.status == "pending":
            # Accept the payment
            # 支払いを受け入れる

            # 提案の状態を「accepted（承認済み）」に更新
            self.proposal_storage.update_proposal_status(proposal_id, "accepted")

            # 確定した注文リストに追加（ビジネスの収益として記録）
            self.confirmed_orders.append(proposal_id)

            # Generate confirmation using ResponseHandler
            # ResponseHandlerを使用して確認メッセージを生成
            # このメッセージは顧客に送信され、注文が確定したことを通知する
            confirmation = self._responses.generate_payment_confirmation(
                proposal_id, stored_proposal.proposal.total_price
            )
            self.logger.info(
                f"Confirmed payment for proposal {proposal_id} from customer {customer_id}"
            )
            return confirmation
        else:
            # 提案が見つからない、または状態が「pending」でない場合
            if stored_proposal:
                # 提案は存在するが、状態が不正（すでに処理済みなど）
                self.logger.error(
                    f"Failed to process payment for proposal {proposal_id} from customer {customer_id}. Proposal status is not pending: {stored_proposal.status}."
                )
            else:
                # 提案IDに一致する提案が見つからない
                self.logger.error(
                    f"Failed to process payment for proposal {proposal_id} from customer {customer_id}. No proposals match that id."
                )

            # Generate error message using ResponseHandler
            # エラーメッセージを生成して顧客に返す
            error_message = self._responses.generate_proposal_not_found_error(
                proposal_id
            )
            return error_message

    def get_business_summary(self) -> BusinessSummary:
        """Get a summary of business operations.

        Returns:
            Summary of business state and transactions

        """
        """
        ビジネスの運営状況のサマリーを取得します。

        実験の分析やデバッグに使用される統計情報を提供します。
        ビジネスの基本情報、提案の状態、確定した注文数などが含まれます。

        Returns:
            BusinessSummary: ビジネスの状態と取引のサマリー
        """
        return BusinessSummary(
            business_id=self.business.id,  # ビジネスID
            business_name=self.business.name,  # ビジネス名
            description=self.business.description,  # ビジネスの説明
            rating=self.business.rating,  # ビジネスの評価（星の数など）
            menu_items=len(self.business.menu_features),  # 提供しているメニュー項目の数
            amenities=len(
                self.business.amenity_features
            ),  # 提供している設備・サービスの数
            pending_proposals=self.proposal_storage.count_pending_proposals(),  # 保留中の提案数
            confirmed_orders=len(
                self.confirmed_orders
            ),  # 確定した注文数（収益を生んだ取引）
            delivery_available=self.business.amenity_features.get(
                "delivery", False
            ),  # 配達可能かどうか
        )

    # async def on_will_stop(self):
    #     """Handle agent pre-shutdown.

    #     Override this method to implement custom pre-shutdown logic.
    #     """
    #     self.logger.info("Business agent shutting down...")

    #     for customer_id in self.customer_histories.keys():
    #         conversation_history = "\n".join(self.customer_histories[customer_id])
    #         self.logger.info(
    #             f"\nFinal conversation history with customer {customer_id}:\n{conversation_history}"
    #         )
