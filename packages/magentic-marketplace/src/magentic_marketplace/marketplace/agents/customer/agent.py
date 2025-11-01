"""Main customer agent implementation."""
# カスタマーエージェントのメイン実装
# このファイルは、マーケットプレイスで自律的に買い物をする顧客エージェントの動作を実装しています。
#
# 主な機能:
# 1. ビジネスの検索 (Search): キーワードでビジネスを検索
# 2. メッセージ送受信: ビジネスに質問を送信し、提案を受け取る
# 3. 提案の評価: 受け取った提案を評価し、最適なものを選択
# 4. 支払いの実行: 選択した提案に対して支払いを実行
# 5. 取引の完了: 取引を完了してエージェントを終了

import asyncio
import traceback

from magentic_marketplace.platform.shared.models import (
    BaseAction,
)

from ...actions import (
    OrderProposal,
    Payment,
    ReceivedMessage,
    Search,
    SearchAlgorithm,
    SearchResponse,
    TextMessage,
)
from ...llm.config import BaseLLMConfig
from ...shared.models import Customer, CustomerAgentProfile
from ..base import BaseSimpleMarketplaceAgent
from ..proposal_storage import OrderProposalStorage
from .models import (
    CustomerAction,
    CustomerActionResult,
    CustomerSendMessageResults,
    CustomerSummary,
)
from .prompts import PromptsHandler


class CustomerAgent(BaseSimpleMarketplaceAgent[CustomerAgentProfile]):
    """Customer agent that autonomously shops in the marketplace."""

    # カスタマーエージェント: マーケットプレイスで自律的に買い物をするエージェント
    #
    # このクラスは、顧客の要求（request）と好み（preferences）に基づいて、
    # 以下のようなショッピングプロセスを自律的に実行します:
    #
    # 1. ビジネスを検索して、候補を見つける
    # 2. 気になるビジネスに質問メッセージを送る
    # 3. ビジネスから提案（OrderProposal）を受け取る
    # 4. LLMを使って提案を評価し、最適なものを選択
    # 5. 選択した提案に対して支払い（Payment）を実行
    # 6. 取引完了後、エージェントを終了
    #
    # 設計の特徴:
    # - LLM駆動: すべての意思決定はLLMが行う（どのビジネスに連絡するか、どの提案を受け入れるか等）
    # - 状態管理: 受け取った提案や完了した取引を内部状態として保持
    # - 非同期処理: asyncioを使用して、複数のアクションを効率的に実行

    def __init__(
        self,
        customer: Customer,
        base_url: str,
        llm_config: BaseLLMConfig | None = None,
        search_algorithm: str = "simple",
        search_bandwidth: int = 10,
        polling_interval: float = 2,
        max_steps: int | None = None,
    ):
        """Initialize the customer agent.
        カスタマーエージェントを初期化します。.

        Args:
            customer: Customer object with request and preferences
                     顧客オブジェクト（リクエストと好みの情報を含む）
            base_url: The marketplace server URL
                     マーケットプレイスサーバーのURL
            llm_config: LLM configuration for the agent
                       エージェントが使用するLLMの設定
            search_algorithm: Search algorithm to use (e.g., "simple", "filtered", "rnr")
                             使用する検索アルゴリズム（例: "simple", "filtered", "rnr"）
            search_bandwidth: The maximum number of search results to return.
                             検索結果の最大数
            polling_interval: Number of seconds to wait after receiving no messages.
                             新しいメッセージがない場合の待機時間（秒）
            max_steps: Maximum number of steps to take before stopping.
                      エージェントが実行する最大ステップ数（制限なしの場合はNone）

        """
        profile = CustomerAgentProfile.from_customer(customer)
        super().__init__(profile, base_url, llm_config)

        # カスタマーエージェントの状態を初期化
        # proposal_storage: 受け取った提案を保存するストレージ
        # - ビジネスから受け取ったOrderProposalを管理
        # - 提案のステータス（pending, accepted, rejected）を追跡
        self.proposal_storage = OrderProposalStorage()

        # completed_transactions: 完了した取引のIDリスト
        # - 支払いが成功した提案のIDを保存
        # - 取引履歴として使用
        self.completed_transactions: list[str] = []

        # conversation_step: 会話のステップカウンター
        # - エージェントが実行したアクションの回数を追跡
        # - max_stepsと比較して、無限ループを防止
        self.conversation_step: int = 0

        # _event_history: イベント履歴
        # - エージェントが実行したアクションとその結果を記録
        # - LLMのプロンプトに含めて、文脈を提供
        # - エラーメッセージも文字列として記録
        self._event_history: list[
            tuple[CustomerAction, CustomerActionResult] | str
        ] = []

        # 検索アルゴリズムと検索結果の最大数を設定
        self._search_algorithm = SearchAlgorithm(search_algorithm)
        self._search_bandwidth = search_bandwidth

        # ポーリング間隔と最大ステップ数を設定
        self._polling_interval = polling_interval
        self._max_steps = max_steps

    @property
    def customer(self) -> Customer:
        """Access customer data from profile with full type safety."""
        # 型安全性を持ってプロファイルから顧客データにアクセス
        return self.profile.customer

    async def execute_action(self, action: BaseAction):
        """Execute an action and record it in event history.
        アクションを実行し、イベント履歴に記録します。.

        Args:
            action: The action to execute
                   実行するアクション

        Returns:
            Result of the action execution
            アクション実行の結果

        """
        # 親クラスを通じてアクションを実行
        result = await super().execute_action(action)

        return result

    async def step(self):
        """One step of autonomous shopping agent logic.
        自律的な買い物エージェントの1ステップを実行します。.

        This method performs one iteration of the customer's shopping journey:
        このメソッドは、顧客のショッピングジャーニーの1回の反復を実行します:
        1. Check for new messages from businesses
           ビジネスからの新しいメッセージを確認
        2. Decide next action using LLM
           LLMを使用して次のアクションを決定
        3. Execute the action (search, prepare messages, or end transaction)
           アクションを実行（検索、メッセージ準備、または取引終了）
        4. Check if transaction completed (triggers shutdown)
           取引が完了したかチェック（シャットダウンのトリガー）

        このメソッドは、エージェントのメインループから繰り返し呼び出されます。
        各ステップで、エージェントは以下を行います:
        - LLMに現在の状態を伝え、次に何をすべきか尋ねる
        - LLMの決定に基づいてアクションを実行
        - 結果を記録し、次のステップの準備をする
        """
        # ステップカウンターをインクリメント
        self.conversation_step += 1

        # LLMを使用して次のアクションを決定
        # このメソッドは、現在の状態（受け取った提案、会話履歴等）を
        # プロンプトとしてLLMに渡し、次に何をすべきか決定させます
        action = await self._generate_customer_action()

        new_messages = False
        if action:
            # アクションを実行（メッセージング処理を内部で処理）
            # 戻り値は新しいメッセージがあったかどうかのフラグ
            new_messages = await self._execute_customer_action(action)

        # 注意: 以下のコードはコメントアウトされています
        # 取引完了時に自動的にシャットダウンする機能は無効化されています
        # これにより、エージェントは複数の取引を行うことができます
        # # 5a. 取引が完了したかチェック
        # if len(self.completed_transactions) > 0:
        #     self.logger.info("Completed a transaction, shutting down!")
        #     self.shutdown()
        #     return

        # 早期停止: 最大ステップ数を超えた場合
        # これにより、エージェントが無限ループに陥ることを防ぎます
        if self._max_steps is not None and self.conversation_step >= self._max_steps:
            await self.logger.warning("Max steps exceeded, shutting down early!")
            self.shutdown()
            return

        # 新しいメッセージがない場合、polling_interval秒待機
        # 新しいメッセージがある場合、すぐに次の決定に進む
        # これにより、メッセージがある場合は迅速に応答し、
        # ない場合はサーバーに過度な負荷をかけないようにします
        if not new_messages:
            # 次の決定の前に待機
            await asyncio.sleep(self._polling_interval)
        else:
            # 新しいメッセージを受け取った場合、すぐに次の決定に進む
            await asyncio.sleep(0)

    async def on_started(self):
        """Handle when the customer agent starts."""
        # カスタマーエージェントが起動したときの処理
        self.logger.info("Starting autonomous shopping agent")

    async def _process_new_messages(self, messages: list[ReceivedMessage]):
        """Process new messages from businesses.
        ビジネスから受け取った新しいメッセージを処理します。.

        このメソッドは、受信したメッセージを処理し、
        OrderProposal（提案）をproposal_storageに保存します。

        提案の保存メカニズム:
        - OrderProposalは、ビジネスが顧客に送る商品やサービスの提案です
        - 各提案には、商品リスト、価格、提案IDが含まれます
        - proposal_storageに保存することで、後でLLMが評価・比較できます
        - TextMessageは保存されず、イベント履歴にのみ記録されます

        Args:
            messages: 受信したメッセージのリスト

        """
        for message in messages:
            # 注意: ReceivedMessagesは、FetchMessagesのActionExecutionResultに記録されます
            # ここでは、OrderProposalのみをストレージに保存します
            if isinstance(message.message, OrderProposal):
                # 提案をストレージに追加
                # - message.message: OrderProposalオブジェクト（提案の内容）
                # - message.from_agent_id: 提案を送信したビジネスのID
                # - self.id: この顧客エージェントのID
                self.proposal_storage.add_proposal(
                    message.message,
                    message.from_agent_id,
                    self.id,
                )
                self.logger.debug(
                    f"Received and stored order proposal {message.message.id} from {message.from_agent_id}",
                    data=message,
                )

    def _get_prompts_handler(self) -> PromptsHandler:
        """Get a fresh PromptsHandler with current state.
        現在の状態を含む新しいPromptsHandlerを取得します。.

        PromptsHandlerは、LLMに渡すプロンプトを生成するクラスです。
        現在の状態（顧客情報、提案、取引履歴、イベント履歴）を
        プロンプトに含めることで、LLMが適切な意思決定を行えるようにします。

        Returns:
            現在の状態を含むPromptsHandlerインスタンス

        """
        return PromptsHandler(
            customer=self.customer,
            proposal_storage=self.proposal_storage,
            completed_transactions=self.completed_transactions,
            event_history=self._event_history,
            logger=self.logger,
        )

    async def _generate_customer_action(self) -> CustomerAction | None:
        """Use LLM to decide the next action to take.
        LLMを使用して次に実行するアクションを決定します。.

        このメソッドは、カスタマーエージェントの意思決定の中核です。
        LLMに現在の状態を伝え、次に何をすべきか決定させます。

        LLMアクション生成の流れ:
        1. PromptsHandlerを使ってプロンプトを構築
           - システムプロンプト: エージェントの役割と目標を説明
           - 状態コンテキスト: 現在の状態（提案、履歴等）を提供
           - ステッププロンプト: 次のアクションを決定するよう指示
        2. LLMに構造化された応答（CustomerAction）を生成させる
        3. LLMの決定（action_type, reason等）をログに記録
        4. エラーが発生した場合、イベント履歴に記録して次回のリカバリーを支援

        LLMが選択できるアクションタイプ:
        - search_businesses: ビジネスを検索
        - check_messages: 新しいメッセージを確認
        - send_messages: メッセージを送信（テキストまたは支払い）
        - end_transaction: 取引を終了

        Returns:
            次に実行するCustomerActionオブジェクト、
            またはエラーが発生した場合はNone

        """
        # PromptsHandlerを使ってプロンプトを構築
        prompts = self._get_prompts_handler()
        system_prompt = prompts.format_system_prompt().strip()
        state_context, step_counter = prompts.format_state_context()
        state_context = state_context.strip()
        step_prompt = prompts.format_step_prompt(step_counter).strip()

        # 完全なプロンプトを作成
        # システムプロンプト + 状態コンテキスト + ステッププロンプト
        full_prompt = f"{system_prompt}\n\n\n\n{state_context}\n\n{step_prompt}"

        # LLMを使用して次のアクションを決定
        try:
            # generate_struct: LLMに構造化された応答を生成させるメソッド
            # - prompt: LLMへの指示
            # - response_format: 期待される応答の型（ここではCustomerAction）
            action, _ = await self.generate_struct(
                prompt=full_prompt,
                response_format=CustomerAction,
            )

            # LLMの決定をログに記録
            # action_type: 選択されたアクションの種類
            # reason: LLMがそのアクションを選択した理由
            self.logger.info(
                f"[Step {self.conversation_step}/{self._max_steps or 'inf'}] Action: {action.action_type}. Reason: {action.reason}"
            )

            return action

        except Exception:
            # LLMの決定が失敗した場合のエラーハンドリング
            self.logger.exception(
                f"[Step {self.conversation_step}/{self._max_steps or 'inf'}] LLM decision failed"
            )
            # イベント履歴にエラーを記録
            # これにより、次回のLLM呼び出し時に、LLMがエラーを認識し、
            # リカバリー戦略を選択できる可能性があります
            self._event_history.append(f"LLM decision failed: {traceback.format_exc()}")
            return None

    async def _execute_customer_action(self, action: CustomerAction):
        """Execute the action decided by the LLM.
        LLMが決定したアクションを実行します。.

        このメソッドは、LLMが選択したアクションタイプに基づいて、
        適切な処理を実行します。各アクションタイプには異なる実行ロジックがあります。

        Args:
            action: 実行するCustomerActionオブジェクト

        Returns:
            新しいメッセージがあった場合はTrue、なければFalse
            （check_messagesの場合のみ）

        """
        # アクションタイプ1: ビジネスを検索
        # 顧客のリクエストやキーワードに基づいてビジネスを検索します
        if action.action_type == "search_businesses":
            # Searchアクションを構築
            # - query: 検索クエリ（LLMが指定、またはデフォルトで顧客のリクエスト）
            # - search_algorithm: 検索アルゴリズム（simple, filtered, rnr等）
            # - limit: 返す検索結果の最大数
            # - page: ページ番号（ページネーション用）
            search_action = Search(
                query=action.search_query or self.customer.request,
                search_algorithm=self._search_algorithm,
                limit=self._search_bandwidth,
                page=action.search_page,
            )
            # マーケットプレイスサーバーに検索リクエストを送信
            search_result = await self.execute_action(search_action)

            # 検索結果の処理
            if not search_result.is_error:
                # 成功: 検索結果をパースしてログに記録
                search_response = SearchResponse.model_validate(search_result.content)
                business_names = [ba.business.name for ba in search_response.businesses]
                business_names_str = ",".join(business_names)

                self.logger.info(
                    f'Search: "{search_action.query}", {search_action.search_algorithm}, resulting in {len(search_response.businesses)} business(es) found out of {search_response.total_possible_results} total business(es). Showing page {action.search_page} of {search_response.total_pages}.'
                )
                self.logger.info(f"Search Result: {business_names_str}")
                # イベント履歴に記録（LLMが次回の決定時に参照）
                self._event_history.append((action, search_response))
            else:
                # エラー: エラー結果をイベント履歴に記録
                self._event_history.append((action, search_result))

        # アクションタイプ2: 新しいメッセージを確認
        # ビジネスから送られてきたメッセージ（提案やテキスト）を取得します
        elif action.action_type == "check_messages":
            # マーケットプレイスサーバーから新しいメッセージを取得
            fetch_response = await self.fetch_messages()
            # イベント履歴に記録
            self._event_history.append((action, fetch_response))
            messages = fetch_response.messages
            # 受信したメッセージを処理（OrderProposalをストレージに保存）
            await self._process_new_messages(messages)
            # 新しいメッセージがあった場合はTrue、なければFalseを返す
            # これにより、メッセージがある場合はすぐに次のステップに進めます
            return len(messages) > 0

        # アクションタイプ3: メッセージを送信
        # テキストメッセージまたは支払いメッセージをビジネスに送信します
        #
        # メッセージ送信の設計上の重要なポイント:
        # - テキストメッセージと支払いメッセージは別々に管理されます
        # - 各メッセージは独立してマーケットプレイスに送信され、独立したエラーを持つことができます
        # - CustomerSendMessageResultsは、各メッセージの送信結果を保持します
        # - この構造により、LLMが次回の決定時に、どのメッセージが成功/失敗したか把握できます
        elif action.action_type == "send_messages":
            # メッセージが指定されていない場合はエラー
            if action.messages is None:
                raise ValueError(
                    "messages cannot be empty when action_type is send_messages"
                )

            # CustomerActionは2つのリストを作成します:
            # 1. text_messages: テキストメッセージのリスト
            # 2. pay_messages: 支払いメッセージのリスト
            #
            # 各メッセージは独立してマーケットプレイスに送信され、独立したエラーを持つことができます
            # このクラスは、各メッセージの送信結果をCustomerActionと同じ形式で保持するために使用されます
            # これにより、後でプロンプトにフォーマットしやすくなります
            send_message_results = CustomerSendMessageResults()

            # テキストメッセージの送信ループ
            # 各テキストメッセージを個別に送信し、結果を記録します
            for text_message in action.messages.text_messages:
                business_id = text_message.to_business_id
                message = TextMessage(content=text_message.content)
                try:
                    # マーケットプレイスサーバーにメッセージを送信
                    result = await self.send_message(business_id, message)
                    if result.is_error:
                        # エラー: ログに記録し、結果リストに追加
                        self.logger.error(
                            f"Failed to send message to {business_id}: {result.content}"
                        )
                        send_message_results.text_message_results.append(
                            (False, str(result.content))
                        )
                    else:
                        # 成功: 結果リストに追加
                        send_message_results.text_message_results.append(
                            (True, "Success!")
                        )
                except Exception:
                    # 例外: ログに記録し、エラー結果を追加
                    self.logger.exception(f"Failed to send message to {business_id}")
                    send_message_results.text_message_results.append(
                        (
                            False,
                            f"Failed to send message to {business_id}. {traceback.format_exc()}",
                        )
                    )

            # 支払いメッセージの送信ループ
            # 各支払いメッセージを個別に送信し、結果を記録します
            #
            # 支払いフローの説明:
            # 1. proposal_message_idを使って、proposal_storageから提案を取得
            # 2. 提案が存在する場合、Paymentオブジェクトを作成
            # 3. マーケットプレイスサーバーに支払いを送信
            # 4. 成功した場合、提案のステータスを"accepted"に更新
            # 5. completed_transactionsリストに提案IDを追加
            for pay_message in action.messages.pay_messages:
                business_id = pay_message.to_business_id
                proposal_to_accept = pay_message.proposal_message_id
                # ストレージから提案を取得
                stored_proposal = self.proposal_storage.get_proposal(proposal_to_accept)

                if stored_proposal:
                    # Paymentオブジェクトを作成
                    # - proposal_message_id: 受け入れる提案のID
                    # - payment_method: 支払い方法（デフォルトは"credit_card"）
                    # - payment_message: 支払いに添えるメッセージ
                    payment = Payment(
                        proposal_message_id=proposal_to_accept,
                        payment_method=pay_message.payment_method or "credit_card",
                        payment_message=pay_message.payment_message
                        or f"Accepting your proposal for {len(stored_proposal.proposal.items)} items",
                    )

                    self.logger.info(
                        f"Sending ${stored_proposal.proposal.total_price} payment to {stored_proposal.business_id} for proposal id {proposal_to_accept}",
                    )

                    try:
                        # マーケットプレイスサーバーに支払いを送信
                        result = await self.send_message(
                            stored_proposal.business_id, payment
                        )
                        if not result.is_error:
                            # 提案を"accepted"としてマーク
                            # これにより、この提案が既に受け入れられたことを追跡できます
                            success = self.proposal_storage.update_proposal_status(
                                proposal_to_accept, "accepted"
                            )
                            if success:
                                # 成功: 結果リストに追加し、completed_transactionsに記録
                                send_message_results.pay_message_results.append(
                                    (True, "Payment accepted!")
                                )
                                # 取引完了リストに追加
                                # このリストは、エージェントが完了した取引を追跡するために使用されます
                                self.completed_transactions.append(proposal_to_accept)
                            else:
                                # 提案ステータスの更新に失敗
                                send_message_results.pay_message_results.append(
                                    (False, "Failed to update order proposal status.")
                                )
                        else:
                            # 支払いの送信に失敗
                            self.logger.error(
                                f"Failed to send payment: {result.content}"
                            )
                            send_message_results.pay_message_results.append(
                                (False, f"Failed to send payment: {result.content}")
                            )
                    except Exception:
                        # 例外: ログに記録し、エラー結果を追加
                        self.logger.exception(
                            f"Failed to send payment for proposal {stored_proposal.proposal_id}"
                        )
                        send_message_results.pay_message_results.append(
                            (
                                False,
                                f"Failed to send payment for proposal {stored_proposal.proposal_id}: {traceback.format_exc()}",
                            )
                        )

                else:
                    # 提案が見つからない場合
                    # これは、LLMが存在しない提案IDを指定した場合に発生します
                    self.logger.warning(
                        f"Error: proposal_to_accept '{proposal_to_accept}' does not match any known proposals."
                    )
                    send_message_results.pay_message_results.append(
                        (
                            False,
                            f"Error: proposal_to_accept '{proposal_to_accept}' does not match any known proposals.",
                        )
                    )

            # すべてのメッセージ送信結果をイベント履歴に記録
            # これにより、LLMは次回の決定時に、どのメッセージが成功/失敗したか把握できます
            self._event_history.append((action, send_message_results))

        # アクションタイプ4: 取引を終了
        # エージェントをシャットダウンし、実行を停止します
        #
        # 設計上の注意:
        # - LLMが明示的に"end_transaction"を選択した場合のみ、エージェントを終了します
        # - 取引が完了したからといって、自動的に終了するわけではありません
        # - これにより、エージェントは複数の取引を行うことができます
        elif action.action_type == "end_transaction":
            # エージェントをシャットダウン
            # shutdown()を呼び出すと、メインループが停止し、エージェントが終了します
            self.shutdown()

        # デフォルト: 新しいメッセージなし
        return False

    def get_transaction_summary(self) -> CustomerSummary:
        """Get a summary of completed transactions.
        完了した取引のサマリーを取得します。.

        このメソッドは、エージェントの実行終了後に呼び出され、
        エージェントがどれだけの提案を受け取り、どれだけの取引を完了したかを
        要約します。

        サマリーには以下の情報が含まれます:
        - 顧客ID、顧客名、リクエスト
        - 顧客プロファイル（好み、要求等）
        - 受け取った提案の数
        - 完了した取引の数
        - 完了した提案のIDリスト

        Returns:
            取引と提案のサマリー（CustomerSummaryオブジェクト）

        """
        return CustomerSummary(
            customer_id=self.customer.id,
            customer_name=self.customer.name,
            request=self.customer.request,
            profile=self.customer.model_dump(),
            proposals_received=self.proposal_storage.count_proposals(),
            transactions_completed=len(self.completed_transactions),
            completed_proposal_ids=self.completed_transactions,
        )
