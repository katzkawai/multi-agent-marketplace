#!/usr/bin/env python3
"""Audit marketplace simulation to verify customers received all proposals sent to them.
マーケットプレイスシミュレーションを監査し、顧客が送信された全ての提案を受け取ったことを検証します。.

このモジュールの目的：
- 実験の整合性を検証する（データの正確性を確認）
- 提案（OrderProposal）の配信状況をチェックする
- メッセージ配信の正しさを確認する
- 取引（Payment）の検証を行う
- 顧客の効用（utility）が最適かどうかを分析する

監査の重要性：
マーケットプレイスでのエージェント実験において、ビジネスエージェントが送った提案が
顧客エージェントに正しく届いているかを確認することは、実験結果の信頼性を保つために不可欠です。
提案が届いていない場合、顧客は最適な選択ができず、実験データが無効になる可能性があります。
"""

import json
import sys
from collections import defaultdict
from pathlib import Path

from magentic_marketplace.marketplace.actions import ActionAdapter, SendMessage
from magentic_marketplace.marketplace.actions.actions import (
    FetchMessages,
    FetchMessagesResponse,
)
from magentic_marketplace.marketplace.actions.messaging import (
    Message,
    OrderProposal,
    Payment,
)
from magentic_marketplace.marketplace.database.queries.logs import llm_call
from magentic_marketplace.marketplace.llm.base import LLMCallLog
from magentic_marketplace.marketplace.shared.models import (
    BusinessAgentProfile,
    CustomerAgentProfile,
    MarketplaceAgentProfileAdapter,
)
from magentic_marketplace.platform.database import (
    connect_to_postgresql_database,
)
from magentic_marketplace.platform.database.base import (
    BaseDatabaseController,
    RangeQueryParams,
)
from magentic_marketplace.platform.database.models import ActionRow
from magentic_marketplace.platform.database.sqlite.sqlite import (
    SQLiteDatabaseController,
)
from magentic_marketplace.platform.shared.models import (
    ActionExecutionRequest,
    ActionExecutionResult,
)

# Terminal colors for output formatting
# ターミナル出力の色設定（視覚的にわかりやすくするため）
RED_COLOR = "\033[91m" if sys.stdout.isatty() else ""  # エラー・問題がある項目
YELLOW_COLOR = "\033[93m" if sys.stdout.isatty() else ""  # 警告・注意が必要な項目
GREEN_COLOR = "\033[92m" if sys.stdout.isatty() else ""  # 成功・正常な項目
CYAN_COLOR = "\033[96m" if sys.stdout.isatty() else ""  # ヘッダー・セクションタイトル
RESET_COLOR = "\033[0m" if sys.stdout.isatty() else ""  # 色のリセット


class MarketplaceAudit:
    """Audit engine to verify customers received all proposals in their LLM context.
    顧客が全ての提案をLLMコンテキストで受け取ったかを検証する監査エンジン。.

    この監査エンジンの役割：
    1. データベースから全てのアクションとメッセージを読み込む
    2. 提案（OrderProposal）が顧客のLLMログに含まれているか確認する
    3. 顧客の効用（utility）が最適かどうかを計算する
    4. 配信されなかった提案について詳細なレポートを生成する
    """

    def __init__(self, db_controller: BaseDatabaseController):
        """Initialize audit with database controller.
        データベースコントローラーで監査を初期化します。.

        Args:
            db_controller: データベース接続を管理するコントローラー

        """
        self.db = db_controller

        # Agent profiles
        # エージェントプロファイル（顧客とビジネスの基本情報）
        self.customer_agents: dict[
            str, CustomerAgentProfile
        ] = {}  # 顧客エージェントのID → プロファイル
        self.business_agents: dict[
            str, BusinessAgentProfile
        ] = {}  # ビジネスエージェントのID → プロファイル

        # Order and payment tracking
        # 注文と支払いの追跡用データ構造
        self.order_proposals: list[OrderProposal] = []  # 全ての提案のリスト
        self.payments: list[Payment] = []  # 全ての支払いのリスト

        # Map proposal_id -> (business_agent_id, customer_agent_id, timestamp)
        # 提案IDから送信元・送信先・タイムスタンプへのマッピング
        # これにより、各提案が誰から誰に送られたかを追跡できる
        self.proposal_metadata: dict[str, tuple[str, str, str]] = {}

        # Map customer_agent_id -> list of proposals they received
        # 顧客ID → その顧客が受け取った提案のリスト
        # これにより、各顧客がどの提案を受け取ったかを簡単に確認できる
        self.customer_proposals: dict[str, list[OrderProposal]] = defaultdict(list)

        # Track payments by customer
        # 顧客ごとの支払い追跡（顧客ID → 支払いリスト）
        self.customer_payments: dict[str, list[Payment]] = defaultdict(list)

        # Track all messages for context with timestamps
        # 全てのメッセージをタイムスタンプ付きで追跡
        self.customer_messages: dict[str, list[tuple[str, Message, str]]] = defaultdict(
            list
        )  # customer_id -> [(to_agent_id, message, timestamp)] 顧客が送ったメッセージ
        self.business_messages: dict[str, list[tuple[str, Message, str]]] = defaultdict(
            list
        )  # business_id -> [(to_agent_id, message, timestamp)] ビジネスが送ったメッセージ

        # Track FetchMessages actions per customer (only non-zero results)
        # 顧客ごとのFetchMessagesアクション追跡（結果が0件でないもののみ）
        # これにより、顧客がいつメッセージを取得したかを確認できる
        self.customer_fetch_actions: dict[str, list[dict]] = defaultdict(
            list
        )  # customer_id -> [fetch_action_data]

        # Track all customer actions and business messages to customers with indices
        # 顧客の全アクションとビジネスから顧客へのメッセージをインデックス付きで追跡
        # インデックスにより、時系列での順序を正確に把握できる
        self.customer_actions: dict[str, list[tuple[int | None, dict]]] = defaultdict(
            list
        )  # customer_id -> [(index, action_data)] 顧客が行った全アクション
        self.business_messages_to_customers: dict[
            str, list[tuple[int | None, dict]]
        ] = defaultdict(
            list
        )  # customer_id -> [(index, message_data)] 顧客が受け取ったビジネスからのメッセージ

    async def load_data(self):
        """Load and parse actions data and agent profiles from database.
        データベースからアクションデータとエージェントプロファイルを読み込んで解析します。.

        処理の流れ：
        1. エージェントプロファイル（顧客とビジネスの情報）を読み込む
        2. 全てのアクション（Search、SendMessage、FetchMessagesなど）を読み込む
        3. 各アクションを処理してメッセージや提案を抽出する
        """
        # Load agent profiles
        # エージェントプロファイルの読み込み
        agents = await self.db.agents.get_all()  # データベースから全エージェントを取得
        for agent_row in agents:
            agent_data = agent_row.data
            # エージェントデータを適切な型（CustomerまたはBusiness）に変換
            agent = MarketplaceAgentProfileAdapter.validate_python(
                agent_data.model_dump()
            )

            # 顧客エージェントとビジネスエージェントを分けて保存
            if isinstance(agent, CustomerAgentProfile):
                self.customer_agents[agent.id] = agent  # 顧客エージェントとして保存
            elif isinstance(agent, BusinessAgentProfile):
                self.business_agents[agent.id] = agent  # ビジネスエージェントとして保存

        # Load actions
        # アクション（エージェントが実行した操作）の読み込み
        actions = await self.db.actions.get_all()  # データベースから全アクションを取得

        # 各アクションを処理して、提案やメッセージを抽出
        for action_row in actions:
            await self._process_action_row(action_row)

    async def _process_action_row(self, action_row: ActionRow):
        """Process a single action row to extract proposals and payments.
        単一のアクション行を処理して、提案と支払いを抽出します。.

        Args:
            action_row: データベースから取得したアクション行

        処理内容：
        - アクションリクエストと結果を抽出
        - 顧客のアクションを記録（タイムライン作成用）
        - SendMessageアクション → 提案や支払いメッセージを抽出
        - FetchMessagesアクション → 顧客がメッセージを取得した記録を保存

        """
        # アクションの基本情報を抽出
        action_request: ActionExecutionRequest = action_row.data.request
        action_result: ActionExecutionResult = action_row.data.result
        agent_id = action_row.data.agent_id  # このアクションを実行したエージェントのID
        timestamp = action_row.created_at.isoformat()  # アクション実行時刻
        index = action_row.index  # type: ignore[attr-defined]  # アクションの順序インデックス

        # アクションパラメータを適切な型に変換
        action = ActionAdapter.validate_python(action_request.parameters)

        # Track all customer actions
        # 顧客の全アクションを記録（後でタイムラインを作成するため）
        if (
            "customer" in agent_id.lower()
        ):  # エージェントIDに"customer"が含まれているか確認
            action_data = {
                "index": index,
                "timestamp": timestamp,
                "agent_id": agent_id,
                "action_type": action_request.name,  # アクションの種類（Search, SendMessage, etc.）
                "action": action.model_dump(mode="json"),  # アクションの詳細
                "result": {
                    "is_error": action_result.is_error,  # エラーが発生したか
                    "content": action_result.content
                    if not action_result.is_error
                    else str(action_result.content),  # 結果の内容
                },
            }
            # 顧客のアクションリストに追加
            self.customer_actions[agent_id].append((index, action_data))

        # Process SendMessage actions
        # SendMessageアクション（メッセージ送信）の処理
        if isinstance(action, SendMessage):
            await self._process_send_message(
                action, action_result, agent_id, timestamp, index
            )
        # FetchMessagesアクション（メッセージ取得）の処理
        elif isinstance(action, FetchMessages):
            await self._process_fetch_messages(
                action, action_result, agent_id, timestamp
            )

    async def _process_send_message(
        self,
        action: SendMessage,
        result: ActionExecutionResult,
        agent_id: str,
        timestamp: str,
        index: int | None,
    ):
        """Process SendMessage actions and parse message content.
        SendMessageアクションを処理してメッセージ内容を解析します。.

        Args:
            action: SendMessageアクション
            result: アクション実行結果
            agent_id: 送信元エージェントID
            timestamp: タイムスタンプ
            index: アクションインデックス

        処理内容：
        - エラーチェック（エラーの場合は処理をスキップ）
        - メッセージの種類を判定（OrderProposal、Payment、など）
        - 提案メッセージの場合：提案リストに追加し、メタデータを保存
        - 支払いメッセージの場合：支払いリストに追加
        - 全てのメッセージをタイムスタンプ付きで追跡

        """
        # エラーが発生している場合は処理をスキップ
        if result.is_error:
            return

        try:
            message = action.message  # メッセージ本体を取得

            # Track all messages by sender type with timestamps
            # 送信元のタイプ（顧客 or ビジネス）に応じてメッセージを分類
            if "customer" in agent_id.lower():
                # 顧客が送信したメッセージを記録
                self.customer_messages[action.from_agent_id].append(
                    (action.to_agent_id, message, timestamp)
                )
            elif "business" in agent_id.lower():
                # ビジネスが送信したメッセージを記録
                self.business_messages[action.from_agent_id].append(
                    (action.to_agent_id, message, timestamp)
                )

                # Track business messages to customers with index
                # ビジネスから顧客へのメッセージをインデックス付きで記録
                # これにより、顧客がいつメッセージを受け取る「べきだった」かがわかる
                if "customer" in action.to_agent_id.lower():
                    message_data = {
                        "index": index,  # メッセージの順序番号
                        "timestamp": timestamp,
                        "from_agent_id": action.from_agent_id,
                        "to_agent_id": action.to_agent_id,
                        "message": message.model_dump(mode="json"),
                    }
                    self.business_messages_to_customers[action.to_agent_id].append(
                        (index, message_data)
                    )

            # Process OrderProposal messages
            # OrderProposal（注文提案）メッセージの処理
            if isinstance(message, OrderProposal):
                self.order_proposals.append(message)  # 提案リストに追加

                # Store metadata: proposal_id -> (business_id, customer_id, timestamp)
                # 提案のメタデータを保存（誰から誰に、いつ送られたか）
                self.proposal_metadata[message.id] = (
                    action.from_agent_id,  # business（送信元ビジネスID）
                    action.to_agent_id,  # customer（送信先顧客ID）
                    timestamp,  # 送信時刻
                )

                # Track proposals received by each customer
                # 各顧客が受け取った提案を追跡
                # これにより、後で「この顧客はどの提案を受け取ったか」を確認できる
                self.customer_proposals[action.to_agent_id].append(message)

            # Paymentメッセージの処理
            elif isinstance(message, Payment):
                self.payments.append(message)  # 支払いリストに追加
                # Link to customer if this is a payment from customer
                # 顧客からの支払いの場合、顧客の支払いリストに追加
                if "customer" in agent_id.lower():
                    self.customer_payments[action.from_agent_id].append(message)

        except Exception as e:
            # メッセージの解析に失敗した場合、警告を表示
            print(f"Warning: Failed to parse message: {e}")

    async def _process_fetch_messages(
        self,
        action: FetchMessages,
        result: ActionExecutionResult,
        agent_id: str,
        timestamp: str,
    ):
        """Process FetchMessages actions and track non-zero results.
        FetchMessagesアクションを処理し、メッセージ取得記録を追跡します。.

        Args:
            action: FetchMessagesアクション
            result: アクション実行結果
            agent_id: アクション実行エージェントID
            timestamp: タイムスタンプ

        重要なポイント：
        - 顧客のFetchMessagesアクションのみを追跡（ビジネスは対象外）
        - メッセージが0件の場合は記録しない（non-zero resultsのみ）
        - どのメッセージをいつ取得したかを詳細に記録
        - 後で「提案が顧客のLLMログに含まれているか」を検証する際に使用

        """
        # エラーが発生している場合は処理をスキップ
        if result.is_error:
            return

        try:
            # Only track for customers
            # 顧客のFetchMessagesのみを追跡（ビジネスは対象外）
            if "customer" not in agent_id.lower():
                return

            # Parse the result as FetchMessagesResponse
            # 結果をFetchMessagesResponse型として解析
            if result.content:
                fetch_response = FetchMessagesResponse.model_validate(result.content)

                # Only track if there are messages
                # メッセージが1件以上ある場合のみ記録（0件の場合は記録しない）
                if fetch_response.messages:
                    # Serialize the fetch action data
                    # フェッチアクションのデータをシリアライズして保存
                    fetch_data = {
                        "timestamp": timestamp,  # いつメッセージを取得したか
                        "from_agent_id_filter": action.from_agent_id,  # フィルター条件（特定の送信元）
                        "limit": action.limit,  # 取得上限数
                        "offset": action.offset,  # オフセット
                        "after": action.after.isoformat()
                        if action.after
                        else None,  # この時刻以降のメッセージ
                        "after_index": getattr(
                            action, "after_index", None
                        ),  # このインデックス以降のメッセージ
                        "num_messages_fetched": len(
                            fetch_response.messages
                        ),  # 取得したメッセージ数
                        "messages": [
                            {
                                "from_agent_id": msg.from_agent_id,  # 送信元
                                "to_agent_id": msg.to_agent_id,  # 送信先
                                "created_at": msg.created_at.isoformat(),  # 作成時刻
                                "message": msg.message.model_dump(
                                    mode="json"
                                ),  # メッセージ本体
                                "index": getattr(
                                    msg, "index", None
                                ),  # メッセージインデックス
                            }
                            for msg in fetch_response.messages
                        ],
                    }
                    # 顧客のフェッチアクションリストに追加
                    self.customer_fetch_actions[agent_id].append(fetch_data)

        except Exception as e:
            # 解析に失敗した場合、警告を表示
            print(f"Warning: Failed to parse FetchMessages result: {e}")

    def get_customer_messages_to_business(
        self, customer_id: str, business_id: str
    ) -> list[tuple[Message, str]]:
        """Get all messages a customer sent to a specific business with timestamps.

        Args:
            customer_id: The customer agent ID
            business_id: The business agent ID

        Returns:
            List of (message, timestamp) tuples the customer sent to the business

        """
        messages = []
        for to_agent_id, message, timestamp in self.customer_messages.get(
            customer_id, []
        ):
            if to_agent_id == business_id:
                messages.append((message, timestamp))
        return messages

    def get_payment_for_proposal(self, proposal_id: str) -> Payment | None:
        """Get the payment message for a specific proposal.

        Args:
            proposal_id: The proposal ID

        Returns:
            Payment message if found, None otherwise

        """
        for payment in self.payments:
            if payment.proposal_message_id == proposal_id:
                return payment
        return None

    async def get_last_llm_log_for_customer(
        self, customer_id: str
    ) -> tuple[LLMCallLog, str] | None:
        """Get the last LLM log for a specific customer with timestamp.

        Args:
            customer_id: The customer agent ID

        Returns:
            Tuple of (LLMCallLog, timestamp) for the most recent log, or None if not found

        """
        # Query for all LLM logs for this customer
        query = llm_call.all()
        params = RangeQueryParams()
        logs = await self.db.logs.find(query, params)

        if not logs:
            return None

        # Filter logs by customer_id and find the most recent
        customer_logs = []
        for log_row in logs:
            log = log_row.data
            agent_id = (log.metadata or {}).get("agent_id", None)

            if agent_id == customer_id:
                try:
                    llm_call_log = LLMCallLog.model_validate(log.data)
                    timestamp = log_row.created_at.isoformat()
                    customer_logs.append((log_row.index, llm_call_log, timestamp))  # type: ignore[attr-defined]
                except Exception as e:
                    print(f"Warning: Could not parse LLM call log: {e}")
                    continue

        if not customer_logs:
            return None

        # Sort by index and return the most recent (log, timestamp)
        customer_logs.sort(key=lambda x: x[0])
        return (customer_logs[-1][1], customer_logs[-1][2])

    def calculate_menu_matches(self, customer_agent_id: str) -> list[tuple[str, float]]:
        """Calculate which businesses can fulfill customer's menu requirements.
        顧客のメニュー要求を満たすことができるビジネスを計算します。.

        Args:
            customer_agent_id: 顧客エージェントID

        Returns:
            (business_agent_id, total_price)のタプルのリスト（価格順にソート）

        処理の流れ：
        1. 顧客が要求しているメニューアイテムを取得
        2. 各ビジネスのメニューをチェック
        3. 全てのアイテムを提供できるビジネスを抽出
        4. 合計価格を計算し、価格の安い順にソート

        重要：
        この関数はアメニティ（設備）のチェックは行わない。
        アメニティチェックは check_amenity_match() で別途行う。

        """
        # 顧客が存在しない場合は空リストを返す
        if customer_agent_id not in self.customer_agents:
            return []

        customer_agent = self.customer_agents[customer_agent_id]
        customer = customer_agent.customer
        requested_items = (
            customer.menu_features
        )  # 顧客が要求しているメニューアイテム（例: {"Taco": 10.0, "Burrito": 15.0}）
        matches: list[tuple[str, float]] = []  # マッチするビジネスと価格のリスト

        # 全てのビジネスをチェック
        for business_agent_id, business_agent in self.business_agents.items():
            business = business_agent.business

            total_price = 0.0  # このビジネスでの合計価格
            can_fulfill = True  # このビジネスが全てのアイテムを提供できるか

            # 顧客が要求している各アイテムをチェック
            for item_name in requested_items:
                if item_name in business.menu_features:
                    # アイテムがメニューにある場合、価格を加算
                    total_price += business.menu_features[item_name]
                else:
                    # アイテムがメニューにない場合、このビジネスは対象外
                    can_fulfill = False
                    break

            # 全てのアイテムを提供できる場合、リストに追加
            if can_fulfill:
                matches.append((business_agent_id, round(total_price, 2)))

        # 価格の安い順にソート（最適な選択肢を見つけやすくするため）
        matches.sort(key=lambda x: x[1])
        return matches

    def check_amenity_match(
        self, customer_agent_id: str, business_agent_id: str
    ) -> bool:
        """Check if business provides all required amenities for customer.
        ビジネスが顧客が要求する全てのアメニティ（設備）を提供しているかチェックします。.

        Args:
            customer_agent_id: 顧客エージェントID
            business_agent_id: ビジネスエージェントID

        Returns:
            True: ビジネスが全ての必須アメニティを提供している
            False: 一部のアメニティが不足している、またはエージェントが存在しない

        アメニティの例：
        - WiFi（無線LAN）
        - Outdoor seating（屋外席）
        - Parking（駐車場）
        - Wheelchair accessible（車椅子対応）

        重要：
        顧客の効用（utility）を満たすには、メニューアイテムだけでなく
        アメニティも一致している必要がある。

        """
        # エージェントが存在しない場合はFalseを返す
        if (
            customer_agent_id not in self.customer_agents
            or business_agent_id not in self.business_agents
        ):
            return False

        customer = self.customer_agents[customer_agent_id].customer
        business = self.business_agents[business_agent_id].business

        # 顧客が必要とするアメニティのセット（例: {"WiFi", "Parking"}）
        required_amenities = set(customer.amenity_features)
        # ビジネスが提供しているアメニティのセット（値がTrueのもののみ）
        available_amenities = {
            amenity
            for amenity, available in business.amenity_features.items()
            if available  # Trueの場合のみ含める
        }

        # required_amenitiesが全てavailable_amenitiesに含まれているかチェック
        # 例: required={"WiFi", "Parking"}, available={"WiFi", "Parking", "Outdoor"}
        #     → True（必要なものは全て揃っている）
        # 例: required={"WiFi", "Parking"}, available={"WiFi"}
        #     → False（Parkingが不足）
        return required_amenities.issubset(available_amenities)

    def calculate_customer_utility(
        self, customer_agent_id: str
    ) -> tuple[float, bool, float | None]:
        """Calculate customer utility and whether they achieved optimal utility.
        顧客の効用（utility）と、最適な効用を達成したかどうかを計算します。.

        Args:
            customer_agent_id: 顧客のID

        Returns:
            (utility, needs_met, optimal_utility)のタプル
            - utility: 実際の効用値
            - needs_met: 顧客のニーズが満たされたか（True/False）
            - optimal_utility: 最適な効用値（マッチするビジネスがない場合はNone）

        効用（utility）の計算式：
        utility = match_score - total_payments

        match_score（ニーズが満たされた場合のみカウント）：
        - match_score = 2 × Σ(customer.menu_features.values())
        - 例: Taco(10) + Burrito(15) → match_score = 2 × (10 + 15) = 50

        ニーズが満たされる条件：
        1. 提案に含まれるメニューアイテムが、顧客が要求したものと完全一致
        2. ビジネスが顧客の要求する全てのアメニティを提供している

        重要：
        - match_scoreは、ニーズが満たされた場合に「一度だけ」カウントされる
        - 複数の支払いがあっても、match_scoreは重複カウントしない

        """
        # 顧客が存在しない場合、効用は0
        if customer_agent_id not in self.customer_agents:
            return 0.0, False, None

        customer = self.customer_agents[customer_agent_id].customer
        payments = self.customer_payments.get(
            customer_agent_id, []
        )  # この顧客が行った全ての支払い
        proposals_received = self.customer_proposals.get(
            customer_agent_id, []
        )  # この顧客が受け取った全ての提案

        # Calculate optimal utility (best case scenario)
        # 最適効用の計算（理想的なケース）
        menu_matches = self.calculate_menu_matches(
            customer_agent_id
        )  # メニューが一致するビジネスを取得
        optimal_utility = None
        if menu_matches:
            # Find the optimal match (cheapest with amenities)
            # 最適なマッチを見つける（アメニティも満たす最安値のビジネス）
            for business_agent_id, price in menu_matches:  # 価格順にソートされている
                if self.check_amenity_match(customer_agent_id, business_agent_id):
                    # アメニティも一致する場合、最適効用を計算
                    match_score = 2 * sum(customer.menu_features.values())
                    optimal_utility = round(match_score - price, 2)
                    break  # 最初に見つかったもの（最安値）で決定

        # Calculate actual utility
        # 実際の効用を計算
        total_payments = 0.0  # 合計支払額
        needs_met = False  # ニーズが満たされたか

        # 各支払いをチェック
        for payment in payments:
            # Find the corresponding proposal
            # 対応する提案を見つける
            proposal = next(
                (p for p in proposals_received if p.id == payment.proposal_message_id),
                None,
            )
            if proposal:
                # Check if proposal matches customer's desired items
                # 提案が顧客の要求するアイテムと一致するかチェック
                proposal_items = {
                    item.item_name for item in proposal.items
                }  # 提案に含まれるアイテム
                requested_items = set(
                    customer.menu_features.keys()
                )  # 顧客が要求するアイテム
                price_paid = proposal.total_price  # 支払った金額
                total_payments += price_paid  # 合計支払額に加算

                # Find which business sent this proposal to check amenities
                # この提案を送ったビジネスを特定（アメニティチェックのため）
                business_agent_id = self._find_business_for_proposal(proposal.id)

                # Check if this payment meets the customer's needs
                # この支払いが顧客のニーズを満たすかチェック
                if proposal_items == requested_items:
                    # Items match - now check amenities
                    # アイテムが一致 → 次にアメニティをチェック
                    if business_agent_id and self.check_amenity_match(
                        customer_agent_id, business_agent_id
                    ):
                        # Items AND amenities match - needs are met!
                        # アイテムとアメニティの両方が一致 → ニーズが満たされた！
                        needs_met = True

        # Calculate utility: match_score counted only ONCE if needs were met
        # 効用の計算：match_scoreはニーズが満たされた場合に「一度だけ」カウント
        match_score = 0.0
        if needs_met:
            # ニーズが満たされた場合のみmatch_scoreを計算
            match_score = 2 * sum(customer.menu_features.values())

        # 効用 = match_score - 合計支払額
        utility = round(match_score - total_payments, 2)
        return utility, needs_met, optimal_utility

    def _find_business_for_proposal(self, proposal_id: str) -> str | None:
        """Find which business sent a specific proposal."""
        # First check in proposal_metadata which is more direct
        if proposal_id in self.proposal_metadata:
            business_id, _, _ = self.proposal_metadata[proposal_id]
            return business_id

        # Fallback to searching through messages
        for business_agent_id, messages in self.business_messages.items():
            for _, msg, _ in messages:
                if isinstance(msg, OrderProposal) and msg.id == proposal_id:
                    return business_agent_id
        return None

    def check_proposal_in_log(self, proposal_id: str, llm_log: LLMCallLog) -> bool:
        """Check if a proposal ID appears in the LLM log.
        提案IDがLLMログに含まれているかをチェックします。.

        Args:
            proposal_id: 検索する提案ID
            llm_log: 検索対象のLLMコールログ

        Returns:
            True: 提案IDがログに見つかった
            False: 提案IDがログに見つからなかった

        重要な概念：
        顧客エージェントがLLMを使って意思決定を行う際、FetchMessagesで取得した
        メッセージ（提案を含む）がLLMのプロンプトに含まれているはずです。
        このメソッドは、提案が実際にLLMに渡されたかを検証します。

        提案が見つからない場合の原因：
        1. FetchMessagesが実行されなかった
        2. FetchMessagesのタイミングが早すぎて、提案がまだ届いていなかった
        3. LLMのコンテキストウィンドウ制限により、提案が省略された
        4. バグによりメッセージが正しく取得されなかった

        """
        # Search in prompt
        # プロンプト内を検索（顧客に送られた入力）
        if isinstance(llm_log.prompt, str):
            # プロンプトが文字列の場合
            if proposal_id in llm_log.prompt:
                return True
        else:
            # For message sequences, search in all content
            # メッセージシーケンスの場合、全てのコンテンツを検索
            for message in llm_log.prompt:
                content = str(message.get("content", ""))
                if proposal_id in content:
                    return True  # 提案IDが見つかった

        # Search in response
        # レスポンス内を検索（LLMが生成した出力）
        if isinstance(llm_log.response, str):
            # レスポンスが文字列の場合
            if proposal_id in llm_log.response:
                return True
        else:
            # For structured response, convert to JSON string and search
            # 構造化されたレスポンスの場合、JSON文字列に変換して検索
            response_str = json.dumps(llm_log.response)
            if proposal_id in response_str:
                return True

        # プロンプトにもレスポンスにも見つからなかった
        return False

    async def audit_proposals(self, db_name: str = "unknown") -> dict:
        """Audit all proposals to verify they appear in customer LLM logs.
        全ての提案を監査し、顧客のLLMログに含まれているかを検証します。.

        Args:
            db_name: データベース名（レポート作成時に使用）

        Returns:
            監査結果を含む辞書

        監査プロセス：
        1. 各提案について、送信先の顧客を特定
        2. その顧客の最後のLLMログを取得
        3. 提案IDがLLMログに含まれているかチェック
        4. 含まれていない場合、詳細情報（タイムライン、フェッチ記録など）を収集
        5. 顧客の効用が最適かどうかを分析

        監査結果の構造：
        - total_proposals: 総提案数
        - proposals_found: LLMログに見つかった提案数
        - proposals_missing: LLMログに見つからなかった提案数
        - missing_details: 見つからなかった提案の詳細情報
        - customers_with_suboptimal_utility: 最適効用を達成できなかった顧客のリスト

        """
        # 監査結果を格納する辞書
        results = {
            "total_proposals": len(self.order_proposals),  # 総提案数
            "proposals_found": 0,  # LLMログに見つかった提案数
            "proposals_missing": 0,  # LLMログに見つからなかった提案数
            "customers_without_logs": set(),  # LLMログがない顧客のセット
            "missing_details": [],  # 見つからなかった提案の詳細リスト
            "customer_stats": defaultdict(
                lambda: {"received": 0, "found": 0, "missing": 0}
            ),  # 顧客ごとの統計（受信数、発見数、欠落数）
            "unique_customers": set(),  # 提案を受け取った顧客のユニークセット
            "unique_businesses": set(),  # 提案を送ったビジネスのユニークセット
            "missing_reasons": defaultdict(int),  # 欠落理由の集計
            "customers_with_suboptimal_utility": [],  # 最適効用未達成の顧客リスト
            "customers_who_made_purchases": 0,  # 購入を行った顧客数
            "customers_with_needs_met": 0,  # ニーズが満たされた顧客数
        }

        # 監査開始のヘッダーを表示
        print(f"{CYAN_COLOR}{'=' * 60}")
        print("MARKETPLACE PROPOSAL AUDIT")
        print(f"{'=' * 60}{RESET_COLOR}\n")

        print(f"Total proposals to audit: {results['total_proposals']}\n")

        # Check each proposal
        # 各提案をチェック（メインの監査ループ）
        for proposal in self.order_proposals:
            proposal_id = proposal.id  # 提案の一意なID

            # Get metadata about this proposal
            # この提案のメタデータ（送信元、送信先、タイムスタンプ）を取得
            if proposal_id not in self.proposal_metadata:
                # メタデータが見つからない場合は警告を表示してスキップ
                print(
                    f"{YELLOW_COLOR}Warning: No metadata found for proposal {proposal_id}{RESET_COLOR}"
                )
                continue

            business_id, customer_id, proposal_timestamp = self.proposal_metadata[
                proposal_id
            ]

            # Track unique customers and businesses
            # ユニークな顧客とビジネスを追跡
            results["unique_customers"].add(customer_id)
            results["unique_businesses"].add(business_id)
            results["customer_stats"][customer_id]["received"] += (
                1  # この顧客が受け取った提案数をカウント
            )

            # Get the last LLM log for this customer
            # この顧客の最後のLLMログを取得
            # 最後のログには、顧客が意思決定を行った際のコンテキストが含まれている
            llm_log_result = await self.get_last_llm_log_for_customer(customer_id)

            # LLMログが存在しない場合（エラーケース）
            if llm_log_result is None:
                print(
                    f"{YELLOW_COLOR}Customer {customer_id} has no LLM logs{RESET_COLOR}"
                )
                results["customers_without_logs"].add(customer_id)
                results["proposals_missing"] += 1
                results["customer_stats"][customer_id]["missing"] += 1
                results["missing_reasons"]["No LLM logs found"] += 1
                results["missing_details"].append(
                    {
                        "proposal_id": proposal_id,
                        "business_id": business_id,
                        "customer_id": customer_id,
                        "reason": "No LLM logs found",
                    }
                )
                continue  # 次の提案へ

            # Unpack LLM log and timestamp
            # LLMログとタイムスタンプを展開
            llm_log, llm_timestamp = llm_log_result

            # Check if proposal appears in the log
            # 提案がログに含まれているかチェック（最も重要な検証）
            if self.check_proposal_in_log(proposal_id, llm_log):
                # 提案が見つかった場合（正常ケース）
                results["proposals_found"] += 1
                results["customer_stats"][customer_id]["found"] += 1
                print(
                    f"{GREEN_COLOR}Found:{RESET_COLOR} Proposal {proposal_id} in {customer_id}'s last LLM log"
                )
            else:
                # 提案が見つからなかった場合（問題ケース）
                # 詳細な診断情報を収集する
                results["proposals_missing"] += 1
                results["customer_stats"][customer_id]["missing"] += 1
                results["missing_reasons"]["Proposal ID not found in last LLM log"] += 1

                # Serialize the LLM prompt for storage
                # LLMプロンプトをシリアライズ（JSON保存用）
                llm_prompt = None
                if isinstance(llm_log.prompt, str):
                    # プロンプトが文字列の場合、そのまま保存
                    llm_prompt = llm_log.prompt
                else:
                    # For message sequences, keep as list
                    # メッセージシーケンスの場合、リストとして保存
                    llm_prompt = llm_log.prompt

                # Serialize the LLM response for storage
                # LLMレスポンスをシリアライズ（JSON保存用）
                llm_response = None
                if isinstance(llm_log.response, str):
                    # Try to parse as JSON if it's a JSON string
                    # JSON文字列の場合、パースを試みる
                    try:
                        llm_response = json.loads(llm_log.response)
                    except json.JSONDecodeError:
                        # パースに失敗した場合、文字列のまま保存
                        llm_response = llm_log.response
                else:
                    # For BaseModel or dict responses, keep as dict
                    # BaseModelまたは辞書型のレスポンスの場合、そのまま保存
                    llm_response = llm_log.response

                # Get LLM model info
                # LLMモデル情報を取得（診断のために記録）
                llm_model = llm_log.model if llm_log.model else "unknown"
                llm_provider = llm_log.provider if llm_log.provider else "unknown"

                # Get the customer messages to this business
                # 顧客がこのビジネスに送ったメッセージを取得
                # これにより、顧客とビジネスの会話履歴がわかる
                customer_msgs_with_timestamps = self.get_customer_messages_to_business(
                    customer_id, business_id
                )

                # Serialize customer messages with timestamps (use mode='json' to handle datetime)
                # 顧客メッセージをタイムスタンプ付きでシリアライズ
                customer_messages_serialized = [
                    {"message": msg.model_dump(mode="json"), "timestamp": ts}
                    for msg, ts in customer_msgs_with_timestamps
                ]

                # Get the payment message for this proposal
                # この提案に対する支払いメッセージを取得
                # 支払いがあれば、顧客はこの提案を受け入れたことになる
                payment_msg = self.get_payment_for_proposal(proposal_id)
                payment_serialized = (
                    payment_msg.model_dump(mode="json") if payment_msg else None
                )

                # Get all FetchMessages actions for this customer
                # この顧客の全てのFetchMessagesアクションを取得
                # これにより、顧客がいつ、どのメッセージを取得したかがわかる
                fetch_actions = self.customer_fetch_actions.get(customer_id, [])

                # Build combined timeline of customer actions and business messages
                # 顧客のアクションとビジネスからのメッセージを統合したタイムラインを構築
                # これにより、時系列でイベントの流れを把握できる
                timeline_items = []

                # Add customer actions
                # 顧客のアクションを追加
                for idx, action_data in self.customer_actions.get(customer_id, []):
                    timeline_items.append(
                        {
                            "type": "customer_action",  # イベントタイプ：顧客のアクション
                            "index": idx,  # 順序インデックス
                            "data": action_data,  # アクションの詳細データ
                        }
                    )

                # Add business messages to this customer
                # ビジネスから顧客へのメッセージを追加
                for idx, message_data in self.business_messages_to_customers.get(
                    customer_id, []
                ):
                    timeline_items.append(
                        {
                            "type": "business_message",  # イベントタイプ：ビジネスからのメッセージ
                            "index": idx,  # 順序インデックス
                            "data": message_data,  # メッセージの詳細データ
                        }
                    )

                # Sort by index
                # インデックスでソートして時系列順に並べる
                timeline_items.sort(key=lambda x: x["index"])

                results["missing_details"].append(
                    {
                        "proposal_id": proposal_id,
                        "business_id": business_id,
                        "customer_id": customer_id,
                        "reason": "Proposal ID not found in last LLM log",
                        "llm_model": llm_model,
                        "llm_provider": llm_provider,
                        "llm_prompt": llm_prompt,
                        "llm_response": llm_response,
                        "llm_timestamp": llm_timestamp,
                        "proposal": proposal.model_dump(mode="json"),
                        "proposal_timestamp": proposal_timestamp,
                        "customer_messages_to_business": customer_messages_serialized,
                        "payment": payment_serialized,
                        "fetch_messages_actions": fetch_actions,
                        "customer_timeline": timeline_items,
                    }
                )
                print(
                    f"{RED_COLOR}Missing:{RESET_COLOR} Proposal {proposal_id} NOT in {customer_id}'s last LLM log (from {business_id})"
                )

        # Calculate utility statistics for all customers
        # 全ての顧客の効用統計を計算
        # これにより、顧客が最適な選択をしたかどうかを分析できる
        for customer_id in self.customer_agents.keys():
            payments = self.customer_payments.get(
                customer_id, []
            )  # この顧客の支払い記録

            # 購入を行った顧客をカウント
            if payments:
                results["customers_who_made_purchases"] += 1

            # 顧客の効用を計算
            # utility: 実際の効用値
            # needs_met: ニーズが満たされたか
            # optimal_utility: 最適な効用値（理論上の最良値）
            utility, needs_met, optimal_utility = self.calculate_customer_utility(
                customer_id
            )

            # ニーズが満たされた顧客をカウント
            if needs_met:
                results["customers_with_needs_met"] += 1

            # Check if customer achieved suboptimal utility
            # 顧客が最適効用未達成かどうかをチェック
            # 最適効用が存在し、かつ購入を行った顧客について分析
            if optimal_utility is not None and payments:
                if utility < optimal_utility:  # 実際の効用が最適効用より低い場合
                    # 最適効用未達成の顧客の詳細情報を収集
                    customer_name = self.customer_agents[customer_id].customer.name

                    # Construct customer trace path
                    # 顧客のLLMトレースファイルのパスを構築
                    # このファイルには、顧客のLLM呼び出し履歴が保存されている
                    customer_trace_path = (
                        f"{db_name}-agent-llm-traces/customers/{customer_id}-0.md"
                    )

                    # Find which business(es) the customer transacted with
                    # 顧客がどのビジネスと取引したかを特定
                    proposals_received = self.customer_proposals.get(customer_id, [])
                    businesses_transacted = []  # 取引したビジネスのリスト
                    for payment in payments:
                        # 支払いに対応する提案を見つける
                        proposal = next(
                            (
                                p
                                for p in proposals_received
                                if p.id == payment.proposal_message_id
                            ),
                            None,
                        )
                        if proposal:
                            # 提案を送ったビジネスを特定
                            business_id = self._find_business_for_proposal(proposal.id)
                            if business_id:
                                business_name = (
                                    self.business_agents[business_id].business.name
                                    if business_id in self.business_agents
                                    else "Unknown"
                                )
                                # Construct business-customer trace path
                                # ビジネスと顧客の会話トレースファイルのパスを構築
                                business_trace_path = f"{db_name}-agent-llm-traces/businesses/{business_id}-{customer_id}-0.md"

                                businesses_transacted.append(
                                    {
                                        "business_id": business_id,
                                        "business_name": business_name,
                                        "price_paid": proposal.total_price,  # 実際に支払った金額
                                        "trace_path": business_trace_path,
                                    }
                                )

                    # Count proposals in final LLM log
                    # 最終的なLLMログに含まれている提案の数をカウント
                    # これにより、顧客が全ての提案を見た上で判断したかがわかる
                    proposals_in_final_log = 0
                    llm_log_result = await self.get_last_llm_log_for_customer(
                        customer_id
                    )
                    if llm_log_result is not None:
                        llm_log, _ = llm_log_result
                        # Check each proposal received to see if it's in the log
                        # 受け取った各提案がログに含まれているかチェック
                        for proposal in proposals_received:
                            if self.check_proposal_in_log(proposal.id, llm_log):
                                proposals_in_final_log += (
                                    1  # ログに含まれている提案数をカウント
                                )

                    # 最適効用未達成の顧客の情報をリストに追加
                    results["customers_with_suboptimal_utility"].append(
                        {
                            "customer_id": customer_id,
                            "customer_name": customer_name,
                            "actual_utility": utility,  # 実際の効用
                            "optimal_utility": optimal_utility,  # 最適効用
                            "utility_gap": round(
                                optimal_utility - utility, 2
                            ),  # 効用ギャップ
                            "needs_met": needs_met,  # ニーズが満たされたか
                            "businesses_transacted": businesses_transacted,  # 取引したビジネスのリスト
                            "proposals_received_total": len(
                                proposals_received
                            ),  # 受け取った提案の総数
                            "proposals_in_final_llm_log": proposals_in_final_log,  # 最終LLMログに含まれていた提案数
                            "trace_path": customer_trace_path,  # LLMトレースファイルのパス
                        }
                    )

        # Sort suboptimal utility customers by utility gap (largest gap first)
        # 最適効用未達成の顧客を効用ギャップの大きい順にソート
        # これにより、最も問題が大きい顧客から順に確認できる
        results["customers_with_suboptimal_utility"].sort(
            key=lambda x: x["utility_gap"], reverse=True
        )

        return results  # 監査結果を返す

    async def generate_report(
        self, save_to_json: bool = True, db_name: str = "unknown"
    ):
        """Generate comprehensive audit report.
        包括的な監査レポートを生成します。.

        Args:
            save_to_json: JSONファイルに結果を保存するか
            db_name: データベース名

        処理の流れ：
        1. データベースからデータを読み込む（load_data）
        2. 提案の監査を実行（audit_proposals）
        3. 監査結果のサマリーをコンソールに表示
        4. オプションでJSONファイルに詳細結果を保存

        このレポートの用途：
        - 実験の整合性確認（提案が正しく配信されたか）
        - 問題のデバッグ（配信されなかった提案の原因調査）
        - 顧客の意思決定分析（最適な選択をしたか）

        """
        # データベースから全データを読み込む
        await self.load_data()

        print(
            f"Loaded {len(self.order_proposals)} proposals and {len(self.payments)} payments\n"
        )

        # Run the audit
        # 監査を実行
        results = await self.audit_proposals(db_name=db_name)

        # Print summary
        print(f"\n{CYAN_COLOR}{'=' * 60}")
        print("AUDIT SUMMARY")
        print(f"{'=' * 60}{RESET_COLOR}\n")

        # Overall statistics
        print(f"{CYAN_COLOR}OVERALL STATISTICS:{RESET_COLOR}")
        print(f"Total proposals sent: {results['total_proposals']}")
        print(
            f"{GREEN_COLOR}Proposals found in customer logs: {results['proposals_found']}{RESET_COLOR}"
        )
        print(
            f"{RED_COLOR}Proposals missing from customer logs: {results['proposals_missing']}{RESET_COLOR}"
        )

        if results["total_proposals"] > 0:
            success_rate = (
                results["proposals_found"] / results["total_proposals"]
            ) * 100
            print(f"Success rate: {success_rate:.1f}%")

        # Customer and business statistics
        print(f"\n{CYAN_COLOR}CUSTOMER & BUSINESS STATISTICS:{RESET_COLOR}")
        print(
            f"Unique customers who received proposals: {len(results['unique_customers'])}"
        )
        print(
            f"Unique businesses who sent proposals: {len(results['unique_businesses'])}"
        )

        if results["unique_customers"]:
            avg_proposals_per_customer = results["total_proposals"] / len(
                results["unique_customers"]
            )
            print(f"Average proposals per customer: {avg_proposals_per_customer:.1f}")

        # FetchMessages statistics
        print(f"\n{CYAN_COLOR}FETCHMESSAGES STATISTICS:{RESET_COLOR}")
        total_fetch_actions = sum(
            len(fetches) for fetches in self.customer_fetch_actions.values()
        )
        customers_with_fetches = len(self.customer_fetch_actions)
        print(
            f"Total FetchMessages actions with non-zero results: {total_fetch_actions}"
        )
        print(f"Customers who fetched messages: {customers_with_fetches}")
        if customers_with_fetches > 0:
            avg_fetches_per_customer = total_fetch_actions / customers_with_fetches
            print(
                f"Average fetches per active customer: {avg_fetches_per_customer:.1f}"
            )

        # Customer delivery status
        customers_with_all = sum(
            1
            for stats in results["customer_stats"].values()
            if stats["missing"] == 0 and stats["received"] > 0
        )
        customers_with_partial = sum(
            1
            for stats in results["customer_stats"].values()
            if 0 < stats["missing"] < stats["received"]
        )
        customers_with_none = sum(
            1
            for stats in results["customer_stats"].values()
            if stats["found"] == 0 and stats["received"] > 0
        )

        print(f"\n{CYAN_COLOR}CUSTOMER DELIVERY STATUS:{RESET_COLOR}")
        print(
            f"{GREEN_COLOR}Customers who received all proposals in LLM logs: {customers_with_all}{RESET_COLOR}"
        )
        print(
            f"{YELLOW_COLOR}Customers who received some proposals in LLM logs: {customers_with_partial}{RESET_COLOR}"
        )
        print(
            f"{RED_COLOR}Customers who received no proposals in LLM logs: {customers_with_none}{RESET_COLOR}"
        )

        # Missing reasons breakdown
        if results["missing_reasons"]:
            print(f"\n{CYAN_COLOR}MISSING PROPOSAL REASONS:{RESET_COLOR}")
            for reason, count in sorted(
                results["missing_reasons"].items(), key=lambda x: x[1], reverse=True
            ):
                print(f"  {reason}: {count}")

        print(
            f"\n{YELLOW_COLOR}Unique customers without LLM logs: {len(results['customers_without_logs'])}{RESET_COLOR}"
        )

        # Utility analysis summary
        print(f"\n{CYAN_COLOR}UTILITY ANALYSIS:{RESET_COLOR}")
        print(
            f"Customers who made purchases: {results['customers_who_made_purchases']}/{len(self.customer_agents)}"
        )
        print(
            f"Customers with needs met: {results['customers_with_needs_met']}/{results['customers_who_made_purchases'] if results['customers_who_made_purchases'] > 0 else len(self.customer_agents)}"
        )

        if results["customers_with_suboptimal_utility"]:
            print(
                f"\n{YELLOW_COLOR}Customers with less than optimal utility: {len(results['customers_with_suboptimal_utility'])}{RESET_COLOR}"
            )
            for customer_data in results["customers_with_suboptimal_utility"]:
                print(
                    f"  - {customer_data['customer_name']} (ID: {customer_data['customer_id']})"
                )
                print(
                    f"    Actual utility: {customer_data['actual_utility']:.2f}, "
                    f"Optimal utility: {customer_data['optimal_utility']:.2f}, "
                    f"Gap: {customer_data['utility_gap']:.2f}"
                )
                print(f"    Needs met: {customer_data['needs_met']}")
                print(
                    f"    Proposals in final LLM log: {customer_data.get('proposals_in_final_llm_log', 0)}/{customer_data.get('proposals_received_total', 0)}"
                )
                if customer_data.get("trace_path"):
                    print(f"    Customer trace: {customer_data['trace_path']}")
                if customer_data.get("businesses_transacted"):
                    print("    Transacted with:")
                    for biz in customer_data["businesses_transacted"]:
                        print(
                            f"      - {biz['business_name']} (ID: {biz['business_id']}) - "
                            f"Paid: ${biz['price_paid']:.2f}"
                        )
                        if biz.get("trace_path"):
                            print(f"        Business trace: {biz['trace_path']}")
        else:
            print(
                f"\n{GREEN_COLOR}All customers who made purchases achieved optimal utility!{RESET_COLOR}"
            )

        # Print details of missing proposals
        if results["missing_details"]:
            print(f"\n{RED_COLOR}MISSING PROPOSAL DETAILS:{RESET_COLOR}")
            for detail in results["missing_details"]:
                print(f"  Proposal: {detail['proposal_id']}")
                print(f"    Business: {detail['business_id']}")
                print(f"    Customer: {detail['customer_id']}")
                print(f"    Reason: {detail['reason']}")

                # Print customer messages to business
                if detail.get("customer_messages_to_business"):
                    print(
                        f"    Customer Messages to Business: {len(detail['customer_messages_to_business'])}"
                    )
                    for i, msg_data in enumerate(
                        detail["customer_messages_to_business"], 1
                    ):
                        msg = msg_data.get("message", {})
                        timestamp = msg_data.get("timestamp", "unknown")
                        msg_type = msg.get("type", "unknown")
                        print(
                            f"      Message {i} (type: {msg_type}, timestamp: {timestamp}):"
                        )
                        msg_str = json.dumps(msg, indent=8)
                        if len(msg_str) > 300:
                            print(f"        {msg_str[:300]}...")
                        else:
                            print(f"        {msg_str}")

                # Print proposal details
                if detail.get("proposal"):
                    proposal_timestamp = detail.get("proposal_timestamp", "unknown")
                    print(f"    Proposal Details (timestamp: {proposal_timestamp}):")
                    proposal_str = json.dumps(detail["proposal"], indent=6)
                    if len(proposal_str) > 500:
                        print(f"      {proposal_str[:500]}...")
                    else:
                        print(f"      {proposal_str}")

                # Print payment details
                if detail.get("payment"):
                    print("    Payment Message:")
                    payment_str = json.dumps(detail["payment"], indent=6)
                    if len(payment_str) > 300:
                        print(f"      {payment_str[:300]}...")
                    else:
                        print(f"      {payment_str}")
                else:
                    print(
                        "    Payment Message: None (customer did not pay for this proposal)"
                    )

                # Print FetchMessages actions
                if detail.get("fetch_messages_actions"):
                    fetch_actions = detail["fetch_messages_actions"]
                    print(
                        f"    FetchMessages Actions: {len(fetch_actions)} calls with non-zero results"
                    )
                    for i, fetch in enumerate(fetch_actions, 1):
                        num_msgs = fetch.get("num_messages_fetched", 0)
                        timestamp = fetch.get("timestamp", "unknown")
                        from_filter = fetch.get("from_agent_id_filter", "None")
                        print(f"      Fetch {i} (timestamp: {timestamp}):")
                        print(
                            f"        Fetched {num_msgs} messages (from_agent_id_filter: {from_filter})"
                        )
                        # Show proposal IDs in fetched messages
                        proposal_ids_in_fetch = []
                        for msg_data in fetch.get("messages", []):
                            msg = msg_data.get("message", {})
                            if msg.get("type") == "order_proposal":
                                proposal_ids_in_fetch.append(msg.get("id", "unknown"))
                        if proposal_ids_in_fetch:
                            print(
                                f"        Proposal IDs in fetch: {', '.join(proposal_ids_in_fetch)}"
                            )

                # Print customer timeline summary
                if detail.get("customer_timeline"):
                    timeline = detail["customer_timeline"]
                    print(
                        f"    Customer Timeline: {len(timeline)} events (actions + messages received)"
                    )
                    print("      (Full timeline available in JSON output)")
                    # Show first few and last few for context
                    num_to_show = min(3, len(timeline))
                    if num_to_show > 0:
                        print(f"      First {num_to_show} events:")
                        for item in timeline[:num_to_show]:
                            event_type = item.get("type")
                            event_data = item.get("data", {})
                            idx = item.get("index")
                            ts = event_data.get("timestamp", "unknown")
                            if event_type == "customer_action":
                                action_type = event_data.get("action_type", "unknown")
                                print(
                                    f"        [{idx}] {ts}: Customer action: {action_type}"
                                )
                            else:
                                from_agent = event_data.get("from_agent_id", "unknown")
                                msg_type = event_data.get("message", {}).get(
                                    "type", "unknown"
                                )
                                print(
                                    f"        [{idx}] {ts}: Received {msg_type} from {from_agent}"
                                )
                    if len(timeline) > num_to_show * 2:
                        print(
                            f"      ... ({len(timeline) - num_to_show * 2} more events)"
                        )
                        print(f"      Last {num_to_show} events:")
                        for item in timeline[-num_to_show:]:
                            event_type = item.get("type")
                            event_data = item.get("data", {})
                            idx = item.get("index")
                            ts = event_data.get("timestamp", "unknown")
                            if event_type == "customer_action":
                                action_type = event_data.get("action_type", "unknown")
                                print(
                                    f"        [{idx}] {ts}: Customer action: {action_type}"
                                )
                            else:
                                from_agent = event_data.get("from_agent_id", "unknown")
                                msg_type = event_data.get("message", {}).get(
                                    "type", "unknown"
                                )
                                print(
                                    f"        [{idx}] {ts}: Received {msg_type} from {from_agent}"
                                )

                # Print LLM prompt if available
                if detail.get("llm_prompt"):
                    llm_timestamp = detail.get("llm_timestamp", "unknown")
                    llm_model = detail.get("llm_model", "unknown")
                    llm_provider = detail.get("llm_provider", "unknown")
                    print(
                        f"    LLM Prompt (model: {llm_model}, provider: {llm_provider}, timestamp: {llm_timestamp}, truncated to 1000 chars):"
                    )

                    if isinstance(detail["llm_prompt"], str):
                        prompt_text = detail["llm_prompt"]
                    else:
                        # For message sequences, format nicely
                        prompt_text = json.dumps(detail["llm_prompt"], indent=6)

                    if len(prompt_text) > 1000:
                        print(f"      {prompt_text[:1000]}...")
                    else:
                        print(f"      {prompt_text}")

                # Print LLM response if available
                if detail.get("llm_response"):
                    print("    LLM Response (truncated to 500 chars):")
                    response_text = (
                        json.dumps(detail["llm_response"], indent=6)
                        if isinstance(detail["llm_response"], dict)
                        else str(detail["llm_response"])
                    )
                    if len(response_text) > 500:
                        print(f"      {response_text[:500]}...")
                    else:
                        print(f"      {response_text}")
                print()

        # Save to JSON if requested
        if save_to_json:
            output_path = f"audit_results_{db_name}.json"

            # Calculate FetchMessages statistics
            total_fetch_actions = sum(
                len(fetches) for fetches in self.customer_fetch_actions.values()
            )
            customers_with_fetches = len(self.customer_fetch_actions)
            avg_fetches_per_customer = (
                total_fetch_actions / customers_with_fetches
                if customers_with_fetches > 0
                else 0
            )

            # Convert sets to lists for JSON serialization
            json_results = {
                **results,
                "unique_customers": sorted(results["unique_customers"]),
                "unique_businesses": sorted(results["unique_businesses"]),
                "customers_without_logs": sorted(results["customers_without_logs"]),
                "customer_stats": dict(results["customer_stats"]),
                "missing_reasons": dict(results["missing_reasons"]),
                "customers_with_suboptimal_utility": results[
                    "customers_with_suboptimal_utility"
                ],
                "customers_with_suboptimal_utility_count": len(
                    results["customers_with_suboptimal_utility"]
                ),
                "customers_who_made_purchases": results["customers_who_made_purchases"],
                "customers_with_needs_met": results["customers_with_needs_met"],
                "fetch_messages_stats": {
                    "total_fetch_actions": total_fetch_actions,
                    "customers_with_fetches": customers_with_fetches,
                    "avg_fetches_per_customer": avg_fetches_per_customer,
                },
            }
            with open(output_path, "w") as f:
                json.dump(json_results, f, indent=2)
            print(f"Audit results saved to: {output_path}")


async def run_audit(db_path_or_schema: str, db_type: str, save_to_json: bool = True):
    """Run proposal audit on the database.
    データベース上で提案監査を実行します。.

    Args:
        db_path_or_schema (str): SQLiteデータベースファイルのパス、またはPostgreSQLスキーマ名
        db_type (str): データベースのタイプ（"sqlite" または "postgres"）
        save_to_json (bool): 結果をJSONファイルに保存するか

    使用例：
        # SQLiteデータベースの監査
        await run_audit("./exports/my_experiment.db", "sqlite", save_to_json=True)

        # PostgreSQLデータベースの監査
        await run_audit("my_experiment", "postgres", save_to_json=True)

    出力：
        - コンソールに監査サマリーを表示
        - audit_results_{db_name}.json に詳細結果を保存（save_to_json=Trueの場合）

    監査内容：
        - 全ての提案が顧客のLLMログに含まれているか検証
        - 顧客の効用が最適かどうか分析
        - 配信されなかった提案の詳細診断情報を収集

    """
    # SQLiteデータベースの場合
    if db_type == "sqlite":
        # ファイルの存在確認
        if not Path(db_path_or_schema).exists():
            raise FileNotFoundError(
                f"SQLite database file {db_path_or_schema} not found"
            )

        # データベース名を取得（拡張子なし）
        db_name = Path(db_path_or_schema).stem

        # SQLiteデータベースコントローラーを作成
        db_controller = SQLiteDatabaseController(db_path_or_schema)
        await db_controller.initialize()  # データベースを初期化

        # 監査エンジンを作成してレポートを生成
        audit = MarketplaceAudit(db_controller)
        await audit.generate_report(save_to_json=save_to_json, db_name=db_name)

    # PostgreSQLデータベースの場合
    elif db_type == "postgres":
        # PostgreSQLデータベースに接続（コンテキストマネージャーで自動クリーンアップ）
        async with connect_to_postgresql_database(
            schema=db_path_or_schema,  # スキーマ名
            host="localhost",  # ホスト名
            port=5432,  # ポート番号
            password="postgres",  # パスワード
            mode="existing",  # 既存のスキーマを使用
        ) as db_controller:
            # 監査エンジンを作成してレポートを生成
            audit = MarketplaceAudit(db_controller)
            await audit.generate_report(
                save_to_json=save_to_json, db_name=db_path_or_schema
            )
    else:
        # サポートされていないデータベースタイプの場合、エラーを発生
        raise ValueError(
            f"Unsupported database type: {db_type}. Must be 'sqlite' or 'postgres'."
        )
