#!/usr/bin/env python3
"""Analyze marketplace simulation data to compute utility metrics using typed models.
マーケットプレイスシミュレーションデータを分析し、型付きモデルを使用して効用メトリクスを計算します。.

===================================================================================
このファイルの目的 (Purpose of this file)
===================================================================================

このファイルは、市場シミュレーションの結果を分析するための中核的なロジックを含んでいます。
顧客効用（カスタマー・ユーティリティ）、企業効用（ビジネス・ユーティリティ）、
市場全体の福祉（マーケット・ウェルフェア）を計算し、提案の妥当性を検証します。

===================================================================================
経済学の基礎概念 (Economic Concepts)
===================================================================================

1. 効用（Utility）
   - エージェントが取引から得る満足度や利益を数値化したもの
   - 顧客効用: utility = match_score - total_payments
   - 企業効用: 受け取った支払いの合計額（売上）

2. 福祉（Welfare）
   - 市場全体の効用の合計 = Σ(全顧客の効用)
   - 高いほど市場が効率的に機能していることを示す
   - 注: このプロジェクトでは企業効用は福祉に含めない（顧客中心の分析）

3. 市場効率性（Market Efficiency）
   - 最適な資源配分がどれだけ達成されたかを示す指標
   - 理想: 全顧客が最安値で要求を満たす企業と取引
   - 現実: エージェントの探索能力や交渉戦略により理想から外れる

4. マッチスコア（Match Score）
   - 顧客のニーズが完全に満たされた場合の価値
   - match_score = 2 × Σ(各アイテムへの支払意思額)
   - 「2倍」の理由: 完全一致の価値を強調（研究設計による）

===================================================================================
分析フロー (Analytics Flow)
===================================================================================

1. データ読み込み (load_data)
   - データベースから全エージェント情報を読み込む
   - 顧客エージェントと企業エージェントを分類
   - LLM呼び出しログを読み込む

2. アクション解析 (analyze_actions)
   - 全てのマーケットプレイスアクションを処理
   - メッセージ、提案、支払いを整理
   - 検索活動を追跡

3. 提案検証 (check_proposal_errors)
   - メニューアイテムの妥当性チェック
   - 価格の正確性チェック
   - 計算ミスの検出

4. 効用計算 (calculate_customer_utility, _calculate_business_utilities)
   - 各顧客の効用を計算
   - 各企業の収益を計算
   - ニーズ充足状況を判定

5. 結果集約 (collect_analytics_results)
   - 全ての計算結果を統合
   - サマリー統計を生成
   - 市場全体の福祉を算出

6. レポート生成 (generate_report, _print_report)
   - 詳細なレポートをコンソールに出力
   - JSON形式でファイルに保存

===================================================================================
ファジーマッチング (Fuzzy Matching)
===================================================================================

LLMエージェントはタイプミスを起こすことがあります（例: "burrito" → "buritos"）。
完全一致のみでは、些細な誤字で取引が「失敗」と判定されてしまいます。

ファジーマッチングの仕組み:
- レーベンシュタイン距離（編集距離）を使用
- fuzzy_match_distance パラメータで許容する最大距離を指定
- 例: distance=2 なら、2文字以下の違いを「一致」とみなす
- 貪欲法で最適なマッチングを選択

これにより、LLMの実質的な性能を正確に評価できます。

===================================================================================
主要クラスとメソッド (Key Classes and Methods)
===================================================================================

MarketplaceAnalytics: 分析エンジンの本体
  - calculate_customer_utility(): 顧客効用を計算
  - check_proposal_errors(): 提案の妥当性を検証
  - filter_valid_proposal_items(): ファジーマッチングを適用
  - collect_analytics_results(): 結果を集約
  - generate_report(): レポートを生成

run_analytics(): エントリーポイント関数
  - SQLite/PostgreSQLデータベースに接続
  - MarketplaceAnalyticsを初期化して実行
"""

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

from magentic_marketplace.experiments.models import (
    AnalyticsResults,
    BusinessSummary,
    CustomerSummary,
    TransactionSummary,
)
from magentic_marketplace.marketplace.actions import (
    ActionAdapter,
    Search,
    SearchResponse,
    SendMessage,
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
from magentic_marketplace.platform.database.base import BaseDatabaseController
from magentic_marketplace.platform.database.models import ActionRow, LogRow
from magentic_marketplace.platform.database.sqlite.sqlite import (
    SQLiteDatabaseController,
)
from magentic_marketplace.platform.shared.models import (
    ActionExecutionRequest,
    ActionExecutionResult,
)

from .models.analytics import (
    InvalidBusiness,
    InvalidCustomer,
    InvalidMenuItem,
    InvalidMenuItemPrice,
    InvalidTotalPrice,
    OrderProposalError,
)

# ターミナル出力のフォーマット用カラーコード
# Terminal colors for output formatting
# sys.stdout.isatty()でターミナル出力かどうかを判定し、
# ファイルリダイレクト時はカラーコードを無効化（エスケープシーケンスの混入を防ぐ）
RED_COLOR = "\033[91m" if sys.stdout.isatty() else ""
YELLOW_COLOR = "\033[93m" if sys.stdout.isatty() else ""
GREEN_COLOR = "\033[92m" if sys.stdout.isatty() else ""
CYAN_COLOR = "\033[96m" if sys.stdout.isatty() else ""
BLUE_COLOR = "\033[94m" if sys.stdout.isatty() else ""
MAGENTA_COLOR = "\033[95m" if sys.stdout.isatty() else ""
RESET_COLOR = "\033[0m" if sys.stdout.isatty() else ""


def levenshtein_distance(s1: str, s2: str) -> int:
    """Calculate Levenshtein distance between two strings.
    2つの文字列間のレーベンシュタイン距離を計算します。.

    レーベンシュタイン距離（編集距離）とは:
    - ある文字列を別の文字列に変換するために必要な最小の編集操作回数
    - 編集操作: 挿入（insertion）、削除（deletion）、置換（substitution）
    - 例: "burrito" -> "buritos" は距離2（'r'を削除、'o'を's'に置換）

    このファイルでの使用目的:
    - メニューアイテム名のタイプミスを許容するため（ファジーマッチング）
    - LLMが生成した提案と実際のメニュー項目の類似度を測定するため

    Args:
        s1: 1つ目の文字列
        s2: 2つ目の文字列

    Returns:
        編集距離（整数）。0なら完全一致、大きいほど異なる

    """
    # 効率化のため、常にs1が長い文字列になるようスワップ
    if len(s1) < len(s2):
        return levenshtein_distance(s2, s1)
    # 一方が空文字列なら、もう一方の長さが距離
    if len(s2) == 0:
        return len(s1)

    # 動的計画法による実装
    # previous_rowは1つ前の行の結果を保持
    previous_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            # 3つの操作のうち最小コストを選択
            insertions = previous_row[j + 1] + 1  # 挿入
            deletions = current_row[j] + 1  # 削除
            substitutions = previous_row[j] + (c1 != c2)  # 置換（同じ文字ならコスト0）
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row
    return previous_row[-1]


class MarketplaceAnalytics:
    """Advanced analytics engine for marketplace simulation data using typed models.
    マーケットプレイスシミュレーションデータ用の高度な分析エンジン（型付きモデル使用）。.

    このクラスの役割:
    - データベースから実験データを読み込み、整理する
    - 顧客・企業の効用を計算する
    - 提案の妥当性を検証する
    - 市場全体の福祉指標を算出する
    - 詳細なレポートを生成する
    """

    def __init__(
        self, db_controller: BaseDatabaseController, fuzzy_match_distance: int = 0
    ):
        """Initialize analytics with database controller.
        データベースコントローラを使って分析エンジンを初期化します。.

        Args:
            db_controller: データベース操作を行うコントローラ
            fuzzy_match_distance: ファジーマッチングの最大距離
                                  0 = 完全一致のみ、1以上 = タイプミスを許容

        """
        self.db = db_controller
        self.fuzzy_match_distance = fuzzy_match_distance

        # エージェントプロファイルの保存
        # Agent profiles storage
        self.customer_agents: dict[str, CustomerAgentProfile] = {}
        self.business_agents: dict[str, BusinessAgentProfile] = {}

        # アクションとメッセージの統計追跡
        # Typed action and message tracking
        self.action_stats: Counter[str] = Counter()  # アクション種別ごとのカウント
        self.message_stats: Counter[str] = Counter()  # メッセージ種別ごとのカウント
        self.customer_messages: dict[str, list[Message]] = defaultdict(
            list
        )  # 顧客が送信したメッセージ
        self.business_messages: dict[str, list[Message]] = defaultdict(
            list
        )  # 企業が送信したメッセージ

        # 注文と支払いの追跡
        # Order and payment tracking
        self.order_proposals: list[OrderProposal] = []  # 全ての注文提案
        self.payments: list[Payment] = []  # 全ての支払い
        self.customer_orders: dict[str, list[OrderProposal]] = defaultdict(
            list
        )  # 顧客が受け取った提案
        self.customer_payments: dict[str, list[Payment]] = defaultdict(
            list
        )  # 顧客が行った支払い
        self.purchased_proposal_ids: set[str] = set()  # 購入された提案のID集合

        # 検索活動の追跡
        # Search tracking
        self.customer_searches: dict[str, list[tuple[Search, SearchResponse]]] = (
            defaultdict(list)
        )

        # LLM呼び出しログの追跡（エージェント別）
        # Track all llm logs keyed by agent
        self.agent_llm_logs: dict[str, list[tuple[LogRow, LLMCallLog]]] = defaultdict(
            list
        )

        # 失敗したLLM呼び出しの追跡
        # Track all failed LLM calls
        self.failed_llm_logs: list[tuple[LogRow, LLMCallLog, str]] = []

        # 使用されたLLMプロバイダーとモデルの追跡
        # Track LLM providers and models
        self.llm_providers: set[str] = set()
        self.llm_models: set[str] = set()

        # 無効な提案とそのエラー詳細の追跡
        # Track invalid purchased proposals with error details
        self.invalid_proposals: dict[str, list[OrderProposalError]] = defaultdict(list)

        # 購入された提案のファジーマッチング情報の追跡
        # Track fuzzy matching for purchased proposals
        # proposal_id -> (distance, proposed item, matched item on menu)
        # 提案ID -> [(距離, 提案されたアイテム, メニュー上の実際のアイテム), ...]
        self.purchased_proposal_fuzzy_matches: dict[
            str, list[tuple[int, str, str]]
        ] = {}

    async def load_data(self):
        """Load and parse agents data from database.
        データベースからエージェントデータを読み込んで解析します。.

        処理内容:
        1. 全てのエージェント情報をデータベースから取得
        2. 型付きモデルに変換（CustomerAgentProfile または BusinessAgentProfile）
        3. エージェントタイプに応じて適切な辞書に格納
        4. LLM呼び出しログを読み込む
        """
        # データベースから全エージェントを取得
        agents = await self.db.agents.get_all()

        # 各エージェントを処理して分類
        for agent_row in agents:
            agent_data = agent_row.data
            # 型付きモデルに変換（Pydanticのバリデーション機能を使用）
            agent = MarketplaceAgentProfileAdapter.validate_python(
                agent_data.model_dump()
            )

            # 顧客エージェントか企業エージェントかで分類
            if isinstance(agent, CustomerAgentProfile):
                self.customer_agents[agent.id] = agent
            elif isinstance(agent, BusinessAgentProfile):  # pyright: ignore[reportUnnecessaryIsInstance] # Makes code more readable
                self.business_agents[agent.id] = agent
            else:
                # 未知のエージェントタイプはエラー
                raise TypeError(f"Unrecognized agent type: {agent}")

        # LLM呼び出しログを読み込む（エージェントの判断プロセスを分析するため）
        await self.load_llm_logs()

    async def load_llm_logs(self):
        """Load all LLM call logs from database and cache them organized by agent.
        データベースから全てのLLM呼び出しログを読み込み、エージェント別にキャッシュします。.

        LLMログの用途:
        - エージェントの意思決定プロセスを追跡
        - LLMの成功率や失敗パターンを分析
        - 使用されたモデルやプロバイダーを記録
        - パフォーマンス分析（トークン数、レイテンシなど）
        """
        query = llm_call.all()
        logs = await self.db.logs.find(query)

        for log_row in logs:
            log = log_row.data
            try:
                # ログをLLMCallLog型に変換
                llm_call_log = LLMCallLog.model_validate(log.data)
                agent_id = (log.metadata or {}).get("agent_id", "unknown")

                # エージェント別にログを整理
                self.agent_llm_logs[agent_id].append((log_row, llm_call_log))

                # 失敗したLLM呼び出しを別途追跡（クイックアクセス用）
                # Also track failures separately for quick access
                if not llm_call_log.success:
                    self.failed_llm_logs.append((log_row, llm_call_log, agent_id))

                # 使用されたモデルとプロバイダーを記録
                # Track models and providers
                if llm_call_log.provider:
                    self.llm_providers.add(llm_call_log.provider)
                if llm_call_log.model:
                    self.llm_models.add(llm_call_log.model)
            except Exception as e:
                print(f"Warning: Could not parse LLM call log: {e}")

    async def analyze_actions(self):
        """Analyze all actions using typed models.
        型付きモデルを使用して全てのアクションを解析します。.

        この処理で整理されるデータ:
        - 送信されたメッセージ（顧客・企業別）
        - 注文提案（OrderProposal）
        - 支払い（Payment）
        - 検索活動（Search）
        - アクションとメッセージの統計

        これらのデータは後続の効用計算や検証で使用されます。
        """
        # データベースから全てのアクションを取得
        actions = await self.db.actions.get_all()

        # 各アクションを処理
        for action_row in actions:
            await self._process_action_row(action_row)

    async def _process_action_row(self, action_row: ActionRow):
        """Process a single action row with proper typing."""
        action_request: ActionExecutionRequest = action_row.data.request
        action_result: ActionExecutionResult = action_row.data.result
        agent_id = action_row.data.agent_id

        # Count action types
        action_name = action_request.name
        self.action_stats[action_name] += 1

        # Parse agent type
        agent_type = self._get_agent_type(agent_id)

        action = ActionAdapter.validate_python(action_request.parameters)

        # Process based on action type
        if isinstance(action, SendMessage):
            await self._process_send_message(action, action_result, agent_type)

        if isinstance(action, Search):
            if not action_result.is_error:
                search_response = SearchResponse.model_validate(action_result.content)
                self.customer_searches[agent_id].append((action, search_response))

        # Note: FetchMessages and Search are only counted, not processed for message content

    def _get_agent_type(self, agent_id: str) -> str:
        """Determine if agent is customer or business."""
        if agent_id in self.customer_agents:
            return "customer"
        elif agent_id in self.business_agents:
            return "business"
        return "unknown"

    async def _process_send_message(
        self,
        action: SendMessage,
        result: ActionExecutionResult,
        agent_type: str,
    ):
        """Process SendMessage actions and parse message content.
        SendMessageアクションを処理し、メッセージ内容を解析します。.

        処理内容:
        1. メッセージタイプをカウント（統計用）
        2. エージェントタイプ別にメッセージを保存
        3. 特殊なメッセージタイプを処理:
           - OrderProposal: 妥当性を検証し、顧客に紐付け
           - Payment: 支払いを記録し、顧客に紐付け

        Args:
            action: SendMessageアクション
            result: アクション実行結果
            agent_type: エージェントのタイプ（"customer" または "business"）

        """
        # エラーが発生している場合はスキップ
        if result.is_error:
            return

        try:
            message = action.message
            # メッセージタイプをカウント（統計用）
            # Count message types
            self.message_stats[message.type] += 1

            # エージェントタイプ別にメッセージを保存
            # Store messages by agent type
            if agent_type == "customer":
                self.customer_messages[action.from_agent_id].append(message)
            elif agent_type == "business":
                self.business_messages[action.from_agent_id].append(message)

            # 特定のメッセージタイプに対する特別な処理
            # Process specific message types
            if isinstance(message, OrderProposal):
                # 全ての注文提案を記録
                self.order_proposals.append(message)
                # 提案の妥当性をチェック（価格、メニューアイテムなど）
                errors = self.check_proposal_errors(
                    message, action.from_agent_id, action.to_agent_id
                )
                if errors:
                    # エラーがあれば記録（後でレポートに含める）
                    self.invalid_proposals[message.id] = errors

                # 企業からの提案なら、顧客に紐付ける
                # Link to customer if this came from a business
                if agent_type == "business":
                    if action.to_agent_id in self.customer_agents:
                        self.customer_orders[action.to_agent_id].append(message)
                    else:
                        print("WARNING: order proposal to non-existing customer")

            elif isinstance(message, Payment):
                # 全ての支払いを記録
                self.payments.append(message)
                # 購入された提案IDを記録（無効な提案が購入されたか追跡するため）
                self.purchased_proposal_ids.add(message.proposal_message_id)
                # 顧客からの支払いなら、顧客に紐付ける
                # Link to customer if this is a payment from customer
                if agent_type == "customer":
                    self.customer_payments[action.from_agent_id].append(message)

        except Exception as e:
            print(f"Warning: Failed to parse message: {e}")

    def calculate_menu_matches(self, customer_agent_id: str) -> list[tuple[str, float]]:
        """Calculate which businesses can fulfill customer's menu requirements.
        顧客のメニュー要件を満たせる企業を計算します。.

        この関数は市場効率性分析の基礎となります:
        - どの企業が顧客のニーズを満たせるか特定
        - 各企業での合計価格を計算
        - 価格順にソートして最適な選択肢を明確化

        Args:
            customer_agent_id: 顧客エージェントのID

        Returns:
            [(business_agent_id, total_price), ...] のリスト。価格の昇順でソート済み

        """
        if customer_agent_id not in self.customer_agents:
            return []

        customer_agent = self.customer_agents[customer_agent_id]
        customer = customer_agent.customer
        requested_items = customer.menu_features  # 顧客が欲しいメニューアイテムの辞書
        matches: list[tuple[str, float]] = []

        # 全ての企業について、顧客の要求を満たせるかチェック
        for business_agent_id, business_agent in self.business_agents.items():
            business = business_agent.business

            total_price = 0.0
            can_fulfill = True

            # 顧客が要求する各アイテムが企業のメニューにあるか確認
            for item_name in requested_items:
                if item_name in business.menu_features:
                    total_price += business.menu_features[item_name]  # 価格を累積
                else:
                    can_fulfill = False  # 1つでも欠けていたら満たせない
                    break

            # 全てのアイテムを提供できる企業のみをマッチとして記録
            if can_fulfill:
                matches.append((business_agent_id, round(total_price, 2)))

        # 価格の安い順にソート（最初の要素が最適価格）
        matches.sort(key=lambda x: x[1])
        return matches

    def check_amenity_match(
        self, customer_agent_id: str, business_agent_id: str
    ) -> bool:
        """Check if business provides all required amenities for customer.
        企業が顧客の必要とするアメニティを全て提供しているかチェックします。.

        アメニティ（amenity）とは:
        - メニュー項目以外の設備・サービス（Wi-Fi、駐車場、テラス席など）
        - 顧客によっては必須条件となる場合がある
        - メニューマッチと組み合わせて「完全なニーズ充足」を判定

        Args:
            customer_agent_id: 顧客エージェントのID
            business_agent_id: 企業エージェントのID

        Returns:
            True = 全ての必要アメニティが利用可能、False = 不足あり

        """
        if (
            customer_agent_id not in self.customer_agents
            or business_agent_id not in self.business_agents
        ):
            return False

        customer = self.customer_agents[customer_agent_id].customer
        business = self.business_agents[business_agent_id].business

        # 顧客が必要とするアメニティのセット
        required_amenities = set(customer.amenity_features)
        # 企業が提供しているアメニティのセット（Trueのもののみ）
        available_amenities = {
            amenity
            for amenity, available in business.amenity_features.items()
            if available
        }

        # 必要なアメニティが全て利用可能なアメニティの部分集合かチェック
        return required_amenities.issubset(available_amenities)

    def get_optimal_business_for_customer(self, customer_agent_id: str):
        """Get the business that has the optimal (menu-match + amenity match for lowest total price) for the customer, irrespective of any real proposals or not.
        顧客にとって最適な企業を取得します（実際の提案とは無関係に、理論的な最適解を求める）。.

        この関数の用途:
        - 市場効率性の評価（実際の取引と理論上の最適解を比較）
        - 顧客が「最善の選択」をしたかどうかの分析
        - エージェントの探索能力や意思決定能力の評価

        最適企業の定義:
        1. 顧客の全てのメニュー要求を満たせる
        2. 顧客の全てのアメニティ要求を満たせる
        3. 上記を満たす企業の中で最も安い

        Args:
            customer_agent_id: 顧客のID

        Returns:
            最適な企業のID、または該当なしの場合はNone

        """
        # メニュー要求を満たせる企業のリスト（価格昇順）
        # (business_id, total_price), sorted ascending by total_price
        menu_matches = self.calculate_menu_matches(customer_agent_id)

        # 価格が安い順に処理し、最初にアメニティも満たす企業が最適解
        for business_agent_id, _ in menu_matches:
            if self.check_amenity_match(customer_agent_id, business_agent_id):
                # 価格順にソート済みなので、最初のマッチが最安値
                # Return the first because they are sorted by total_price
                # i.e. the first match is the cheapest
                return business_agent_id

        # メニューとアメニティの両方を満たす企業がない
        return None

    def filter_valid_proposal_items(
        self, business_agent_id: str, proposal: OrderProposal
    ):
        """Return only the proposed items that actually match real menu items (up to self.fuzzy_match_distance).
        提案されたアイテムのうち、実際のメニューアイテムと一致するもののみを返します（ファジーマッチング適用）。.

        このメソッドの役割:
        1. 完全一致するアイテムを見つける
        2. fuzzy_match_distance > 0 の場合、タイプミスを許容した一致も探す
        3. 貪欲法（greedy）で最適なマッチングを選択

        なぜ必要か:
        - LLMは時々アイテム名を間違えて生成する（"burrito" → "buritos"など）
        - 完全一致のみでは、些細なタイプミスで「ニーズ未充足」と判定されてしまう
        - ファジーマッチングで、実質的には正しい取引を「成功」として認識できる

        Args:
            business_agent_id: 企業エージェントのID
            proposal: 検証する注文提案

        Returns:
            (matched_items, fuzzy_matches) のタプル
            - matched_items: 一致したメニューアイテムのセット（実際のメニュー名）
            - fuzzy_matches: [(距離, 提案名, メニュー名), ...] のリスト

        """
        business_agent = self.business_agents[business_agent_id]
        menu_items = set(business_agent.business.menu_features.keys())
        proposal_items = {item.item_name for item in proposal.items}

        # まず完全一致するアイテムを見つける
        # Start with exact matches
        matched_items: set[str] = proposal_items.intersection(menu_items)

        fuzzy_matches: list[tuple[int, str, str]] = []
        if self.fuzzy_match_distance > 0:
            # 完全一致したアイテムを除外（残りに対してファジーマッチングを適用）
            # Remove exact matches
            menu_items.difference_update(matched_items)
            proposal_items.difference_update(matched_items)

            # 残りの全ペアの距離を計算
            # Calculate all pairwise distances of remaining items
            fuzzy_distances: list[tuple[int, str, str]] = []
            for menu_item in menu_items:
                for proposal_item in proposal_items:
                    distance = levenshtein_distance(
                        menu_item.lower(), proposal_item.lower()
                    )
                    # 距離が閾値以下なら候補として記録
                    if distance <= self.fuzzy_match_distance:
                        fuzzy_distances.append(
                            (
                                distance,
                                menu_item,
                                proposal_item,
                            )
                        )

            # 貪欲法でマッチングを選択（距離が小さい順に処理）
            # Greedily pick matches
            for distance, menu_item, proposal_item in sorted(fuzzy_distances):
                # これらがまだ使用可能か確認（既に別のペアにマッチしていないか）
                # Make sure these are still available
                if menu_item in menu_items and proposal_item in proposal_items:
                    # 実際のメニューアイテム名をマッチリストに追加
                    # （顧客の要求と完全一致するかのチェックに使用）
                    # Add the menu_item to the match list (for exact match with customer requests)
                    matched_items.add(menu_item)
                    # ファジーマッチをレポート用に追跡
                    # Track fuzzy matches for reporting
                    fuzzy_matches.append((distance, proposal_item, menu_item))
                    # 二重カウントを防ぐため削除
                    # Remove so we don't double count
                    menu_items.remove(menu_item)
                    proposal_items.remove(proposal_item)

        return matched_items, fuzzy_matches

    def calculate_customer_utility(self, customer_agent_id: str) -> tuple[float, bool]:
        """Calculate customer utility where match_score is only counted once if ANY payment meets the customer's needs.
        顧客効用を計算します。ニーズが満たされた場合のみ match_score が1回だけカウントされます。.

        顧客効用の計算式:
            utility = match_score - total_payments

        詳細:
        - match_score = 2 × Σ(顧客の各アイテムへの支払意思額)
          - ニーズが満たされた場合のみカウント（1回のみ）
          - ニーズ充足条件: (1) 全ての要求アイテムが提案に含まれる AND (2) 全てのアメニティが一致
        - total_payments = 顧客が実際に支払った金額の合計
        - 効用がプラス = 得をした（支払った金額より価値が高い）
        - 効用がマイナス = 損をした（支払った金額より価値が低い）

        経済学的意味:
        - 効用は顧客の「満足度」を金額換算したもの
        - match_scoreは「完璧な取引ができた場合の価値」を表す
        - 実際の支払額を引くことで「純粋な利益」を算出

        Args:
            customer_agent_id: 顧客のID

        Returns:
            (utility, needs_met) のタプル
            - utility: 効用値（小数第2位まで丸め）
            - needs_met: ニーズが満たされたかどうか（True/False）

        """
        if customer_agent_id not in self.customer_agents:
            return 0.0, False

        customer = self.customer_agents[customer_agent_id].customer
        payments = self.customer_payments.get(customer_agent_id, [])
        proposals_received = self.customer_orders.get(customer_agent_id, [])

        total_payments = 0.0
        needs_met = False

        # 全ての支払いについて処理
        for payment in payments:
            # 対応する提案を見つける
            # Find the corresponding proposal
            proposal = next(
                (p for p in proposals_received if p.id == payment.proposal_message_id),
                None,
            )
            if proposal:
                # 提案されたアイテムのうち、実際に企業のメニューにあるものを取得
                # Get proposal items that are actually part of the businesses menu (up to fuzzy distance)
                business_agent_id = self._find_business_for_proposal(proposal.id)
                if business_agent_id:
                    proposal_items, proposal_item_fuzzy_matches = (
                        self.filter_valid_proposal_items(business_agent_id, proposal)
                    )

                    requested_items = set(customer.menu_features.keys())
                    price_paid = proposal.total_price
                    total_payments += price_paid  # 支払総額を累積

                    # 要求された全アイテムが提案に含まれているかチェック
                    if requested_items.issubset(proposal_items):
                        # ファジーマッチがあった場合は記録
                        # Record fuzzy matches
                        if proposal_item_fuzzy_matches:
                            self.purchased_proposal_fuzzy_matches[proposal.id] = (
                                proposal_item_fuzzy_matches
                            )

                        # アイテムが一致した場合、次はアメニティをチェック
                        # Items match (exactly or fuzzily) - now check amenities
                        if self.check_amenity_match(
                            customer_agent_id, business_agent_id
                        ):
                            # アイテム AND アメニティが一致 → ニーズ充足！
                            # Items AND amenities match - needs are met!
                            needs_met = True

        # 効用を計算: ニーズが満たされた場合のみ match_score をカウント（1回のみ）
        # Calculate utility: match_score counted only ONCE if needs were met
        match_score = 0.0
        if needs_met:
            # match_score = 2倍の支払意思額の合計
            # これは「完全なニーズ充足」の価値を表す
            match_score = 2 * sum(customer.menu_features.values())

        # 効用 = 得られた価値 - 支払った金額
        utility = match_score - total_payments
        return round(utility, 2), needs_met

    def _find_business_for_proposal(self, proposal_id: str) -> str | None:
        """Find which business sent a specific proposal."""
        for business_agent_id, messages in self.business_messages.items():
            for msg in messages:
                if isinstance(msg, OrderProposal) and msg.id == proposal_id:
                    return business_agent_id
        return None

    def check_proposal_errors(
        self, proposal: OrderProposal, business_agent_id: str, customer_agent_id: str
    ) -> list[OrderProposalError]:
        """Check if proposal items and prices are valid against business menu.
        提案されたアイテムと価格が企業のメニューに対して妥当かチェックします。.

        検証内容:
        1. InvalidBusiness: 存在しない企業からの提案
        2. InvalidCustomer: 存在しない顧客への提案
        3. InvalidMenuItem: メニューに存在しないアイテムの提案
           - レーベンシュタイン距離で最も近いメニューアイテムも記録
        4. InvalidMenuItemPrice: 正しいアイテムだが価格が間違っている
           - 0.01ドル以上の差があれば無効と判定
        5. InvalidTotalPrice: 個別アイテムの合計と提案の合計金額が一致しない
           - 計算ミスや意図的な価格操作を検出

        なぜこの検証が重要か:
        - LLMエージェントは計算ミスやハルシネーションを起こす可能性がある
        - 悪意のあるエージェントが不正な価格を提示する可能性を監視
        - 市場の公正性と効率性を評価するための基礎データ

        Args:
            proposal: 検証する注文提案
            business_agent_id: 提案を送信した企業のID
            customer_agent_id: 提案を受け取った顧客のID

        Returns:
            検出されたエラーのリスト（エラーがなければ空リスト）

        """
        errors: list[OrderProposalError] = []
        business_agent = self.business_agents.get(business_agent_id, None)
        customer_agent = self.customer_agents.get(customer_agent_id, None)

        # エラー1: 存在しない企業
        if not business_agent:
            errors.append(
                InvalidBusiness(
                    proposal_id=proposal.id,
                    business_agent_id=business_agent_id,
                    customer_agent_id=customer_agent_id,
                )
            )

        # エラー2: 存在しない顧客
        if not customer_agent:
            errors.append(
                InvalidCustomer(
                    proposal_id=proposal.id,
                    business_agent_id=business_agent_id,
                    customer_agent_id=customer_agent_id,
                )
            )

        # 企業が存在する場合、メニューアイテムと価格を詳細検証
        if business_agent:
            business_menu = business_agent.business.menu_features
            proposed_total = 0

            # 提案された各アイテムをチェック
            for item in proposal.items:
                proposed_total += item.unit_price * item.quantity

                # エラー3: メニューに存在しないアイテム
                if item.item_name not in business_menu:
                    # 最も近いメニューアイテムを見つけて距離を記録
                    # Find closest menu item to calculate distance and track the pair
                    item_distances = [
                        (
                            levenshtein_distance(
                                item.item_name.lower(), menu_item.lower()
                            ),
                            menu_item,
                        )
                        for menu_item in business_menu.keys()
                    ]
                    closest_distance, closest_menu_item = sorted(item_distances)[0]
                    errors.append(
                        InvalidMenuItem(
                            proposal_id=proposal.id,
                            business_agent_id=business_agent_id,
                            customer_agent_id=customer_agent_id,
                            proposed_menu_item=item.item_name,
                            closest_menu_item=closest_menu_item,
                            closest_menu_item_distance=closest_distance,
                        )
                    )

                # エラー4: アイテムは存在するが価格が間違っている
                # 0.01ドル（1セント）以上の差があれば価格エラーと判定
                elif abs(item.unit_price - business_menu[item.item_name]) >= 0.01:
                    errors.append(
                        InvalidMenuItemPrice(
                            proposal_id=proposal.id,
                            business_agent_id=business_agent_id,
                            customer_agent_id=customer_agent_id,
                            menu_item=item.item_name,
                            proposed_price=item.unit_price,
                            actual_price=business_menu[item.item_name],
                        )
                    )

            # エラー5: 合計金額の計算ミス
            # 0.01ドル以上の差があれば計算エラーと判定
            if abs(proposal.total_price - proposed_total) >= 0.01:
                errors.append(
                    InvalidTotalPrice(
                        proposal_id=proposal.id,
                        business_agent_id=business_agent_id,
                        customer_agent_id=customer_agent_id,
                        proposed_total_price=proposal.total_price,
                        calculated_total_price=proposed_total,
                    )
                )

        return errors

    def calculate_conversation_utility(
        self, customer_agent_id: str, business_agent_id: str
    ) -> float:
        """Calculate utility for a specific customer-business conversation.

        This calculates utility based on payments made by the customer to this specific
        business. Unlike total customer utility, the match score is counted for each
        payment in this conversation that meets the customer's needs.

        Args:
            customer_agent_id: ID of the customer
            business_agent_id: ID of the business

        Returns:
            Utility for this specific conversation (can be positive or negative)

        """
        if customer_agent_id not in self.customer_agents:
            return 0.0

        customer = self.customer_agents[customer_agent_id].customer
        all_payments = self.customer_payments.get(customer_agent_id, [])
        all_proposals = self.customer_orders.get(customer_agent_id, [])

        # Filter payments that went to this specific business
        total_payments_to_business = 0.0
        match_score = 0.0

        for payment in all_payments:
            # Find the corresponding proposal
            proposal = next(
                (p for p in all_proposals if p.id == payment.proposal_message_id),
                None,
            )
            if proposal:
                # Check if this proposal is from the target business
                proposal_business_id = self._find_business_for_proposal(proposal.id)
                if proposal_business_id == business_agent_id:
                    # This payment is to the target business
                    total_payments_to_business += proposal.total_price

                    # Check if this payment meets customer's needs
                    proposal_items = {item.item_name for item in proposal.items}
                    requested_items = set(customer.menu_features.keys())

                    if proposal_items == requested_items:
                        # Items match - now check amenities
                        if self.check_amenity_match(
                            customer_agent_id, business_agent_id
                        ):
                            # Items AND amenities match - add match score
                            match_score = 2 * sum(customer.menu_features.values())

        utility = match_score - total_payments_to_business
        return round(utility, 2)

    def _calculate_business_utilities(self) -> dict[str, float]:
        """Calculate utility (revenue) for each business based on payments received.
        各企業の効用（収益）を、受け取った支払いに基づいて計算します。.

        企業効用の定義:
        - 企業効用 = 受け取った支払いの合計額（売上・収益）
        - 顧客効用とは異なり、単純に「受け取った金額」のみをカウント
        - コストは考慮しない（このシミュレーションでは企業のコストはゼロと仮定）

        経済学的意味:
        - 企業の「成功度」を測る指標
        - 高い効用 = 多くの顧客から支払いを受けた
        - 市場全体の福祉は「顧客効用の合計」のみで測定される
          （企業効用は市場福祉には含まれない - これは顧客中心の分析のため）

        Returns:
            {business_agent_id: total_revenue, ...} の辞書

        """
        business_utilities: defaultdict[str, float] = defaultdict(float)

        # 全ての支払いを処理し、どの企業が受け取ったか特定
        # Go through all payments and find which businesses received them
        for customer_agent_id, payments in self.customer_payments.items():
            for payment in payments:
                # 対応する提案を見つけて企業情報を取得
                # Find the corresponding proposal to get business info
                proposals_received = self.customer_orders.get(customer_agent_id, [])
                proposal = next(
                    (
                        p
                        for p in proposals_received
                        if p.id == payment.proposal_message_id
                    ),
                    None,
                )
                if proposal:
                    # ヘルパーメソッドを使って企業を特定
                    # Use the helper method to find the business
                    business_agent_id = self._find_business_for_proposal(proposal.id)
                    if business_agent_id:
                        # 企業の収益に加算
                        business_utilities[business_agent_id] += proposal.total_price

        return dict(business_utilities)

    def collect_analytics_results(self) -> AnalyticsResults:
        """Collect all analytics results into a structured format.
        全ての分析結果を構造化されたフォーマットに集約します。.

        このメソッドは分析エンジンの最終ステップです:
        - 各種効用計算の結果を統合
        - 取引サマリー（平均値、完了率など）を算出
        - 顧客・企業ごとのサマリーを生成
        - 市場全体の福祉指標を計算

        Returns:
            AnalyticsResults: 全ての分析結果を含む型付きモデル

        """
        # 企業効用を計算
        business_utilities = self._calculate_business_utilities()

        # 取引サマリーの計算
        # Calculate transaction summary

        # 平均提案価格（全ての提案の平均）
        avg_proposal_value = None
        if self.order_proposals:
            avg_proposal_value = sum(p.total_price for p in self.order_proposals) / len(
                self.order_proposals
            )

        # 平均支払額（実際に購入された注文の平均）
        avg_paid_order_value = None
        if self.payments:
            paid_order_values: list[float] = []
            for customer_id, payments in self.customer_payments.items():
                for payment in payments:
                    proposals_received = self.customer_orders.get(customer_id, [])
                    proposal = next(
                        (
                            p
                            for p in proposals_received
                            if p.id == payment.proposal_message_id
                        ),
                        None,
                    )
                    if proposal:
                        paid_order_values.append(proposal.total_price)

            if paid_order_values:
                avg_paid_order_value = sum(paid_order_values) / len(paid_order_values)

        # 取引サマリーオブジェクトを作成
        transaction_summary = TransactionSummary(
            order_proposals_created=len(self.order_proposals),  # 作成された提案の総数
            payments_made=len(self.payments),  # 実行された支払いの総数
            average_paid_order_value=avg_paid_order_value,  # 購入された注文の平均価格
            average_proposal_value=avg_proposal_value,  # 全提案の平均価格
            invalid_proposals_purchased=len(
                self.purchased_proposal_ids.intersection(self.invalid_proposals.keys())
            ),  # 購入された無効な提案の数（エラーがあるのに購入された）
            total_invalid_proposals=len(self.invalid_proposals),  # 無効な提案の総数
        )

        # 顧客サマリーを収集
        # Collect customer summaries
        customer_summaries: list[CustomerSummary] = []
        total_utility = 0.0  # 市場全体の福祉（全顧客の効用の合計）
        customers_who_purchased = 0  # 購入した顧客の数
        customers_with_needs_met = 0  # ニーズが満たされた顧客の数

        # 各顧客について処理
        for customer_agent_id in sorted(self.customer_agents.keys()):
            customer = self.customer_agents[customer_agent_id].customer
            messages_sent = len(self.customer_messages.get(customer_agent_id, []))
            orders_received = len(self.customer_orders.get(customer_agent_id, []))
            payments_made = len(self.customer_payments.get(customer_agent_id, []))
            searches_made = len(self.customer_searches.get(customer_agent_id, []))
            utility, needs_met = self.calculate_customer_utility(customer_agent_id)

            # 顧客サマリーを作成
            customer_summaries.append(
                CustomerSummary(
                    customer_id=customer_agent_id,
                    customer_name=customer.name,
                    messages_sent=messages_sent,
                    searches_made=searches_made,
                    proposals_received=orders_received,
                    payments_made=payments_made,
                    utility=utility,
                    needs_met=needs_met,
                )
            )

            # 市場全体の指標を更新
            total_utility += utility  # 市場福祉に加算
            if payments_made > 0:
                customers_who_purchased += 1  # アクティブな顧客としてカウント
            if needs_met:
                customers_with_needs_met += 1  # ニーズ充足顧客としてカウント

        # 企業サマリーを収集
        # Collect business summaries
        business_summaries: list[BusinessSummary] = []
        for business_agent_id in sorted(self.business_agents.keys()):
            business = self.business_agents[business_agent_id].business
            messages_sent = len(self.business_messages.get(business_agent_id, []))
            proposals_sent = sum(
                1
                for msg in self.business_messages.get(business_agent_id, [])
                if isinstance(msg, OrderProposal)
            )
            utility = business_utilities.get(business_agent_id, 0.0)

            business_summaries.append(
                BusinessSummary(
                    business_id=business_agent_id,
                    business_name=business.name,
                    messages_sent=messages_sent,
                    proposals_sent=proposals_sent,
                    utility=utility,
                )
            )

        # 最終サマリーメトリクスを計算
        # Calculate final summary metrics

        # アクティブな顧客1人あたりの平均効用
        # （購入しなかった顧客は除外して平均を計算）
        avg_utility_per_active_customer = None
        if customers_who_purchased > 0:
            avg_utility_per_active_customer = total_utility / customers_who_purchased

        # 購入完了率（全顧客のうち何%が購入したか）
        # この指標は市場の活発さを示す
        completion_rate = (
            (customers_who_purchased / len(self.customer_agents)) * 100
            if self.customer_agents
            else 0
        )

        # 全ての結果を統合したAnalyticsResultsオブジェクトを返す
        return AnalyticsResults(
            total_customers=len(self.customer_agents),
            total_businesses=len(self.business_agents),
            total_actions_executed=sum(self.action_stats.values()),
            total_messages_sent=sum(self.message_stats.values()),
            action_breakdown=dict(self.action_stats),
            message_type_breakdown=dict(self.message_stats),
            transaction_summary=transaction_summary,
            customer_summaries=customer_summaries,
            business_summaries=business_summaries,
            customers_who_made_purchases=customers_who_purchased,
            customers_with_needs_met=customers_with_needs_met,
            total_marketplace_customer_utility=total_utility,  # 市場福祉（最重要指標）
            average_utility_per_active_customer=avg_utility_per_active_customer,
            purchase_completion_rate=completion_rate,
            llm_providers=list(self.llm_providers),
            llm_models=list(self.llm_models),
            total_llm_calls=sum(map(len, self.agent_llm_logs.values())),
            failed_llm_calls=len(self.failed_llm_logs),
        )

    async def generate_report(
        self,
        db_name: str = "unknown",
        save_to_json: bool = True,
        print_results: bool = True,
    ) -> AnalyticsResults:
        """Generate comprehensive analytics report.
        包括的な分析レポートを生成します。.

        このメソッドは分析プロセス全体を統括します:
        1. データベースからエージェントデータを読み込む
        2. 全てのアクションを解析する
        3. 結果を集約する
        4. JSONファイルに保存する（オプション）
        5. コンソールに出力する（オプション）

        Args:
            db_name: データベース名（出力ファイル名に使用）
            save_to_json: JSON形式で結果を保存するか
            print_results: コンソールに結果を出力するか

        Returns:
            AnalyticsResults: 全ての分析結果を含むオブジェクト

        """
        # データベースからエージェント情報とLLMログを読み込む
        await self.load_data()
        # 全てのアクションを解析してメッセージ・取引を整理
        await self.analyze_actions()

        # 分析結果を一度だけ収集（効率化のため）
        # Collect analytics results once
        analytics_results = self.collect_analytics_results()

        # JSON形式で保存（要求された場合）
        # Save to JSON if requested
        if save_to_json:
            output_path = f"analytics_results_{db_name}.json"
            with open(output_path, "w") as f:
                json.dump(analytics_results.model_dump(), f, indent=2)
            print(f"Analytics results saved to: {output_path}")

        # 収集した結果を使ってレポートを出力
        # Print report using the collected results
        if print_results:
            self._print_report(analytics_results)

        return analytics_results

    def _print_report(self, results: AnalyticsResults):
        """Print the analytics report using collected results."""
        print(f"{CYAN_COLOR}{'=' * 60}")
        print("MARKETPLACE SIMULATION ANALYTICS REPORT")
        print(f"{'=' * 60}{RESET_COLOR}\n")

        # Basic statistics
        print(f"{BLUE_COLOR}SIMULATION OVERVIEW:{RESET_COLOR}")
        print(
            f"Found {results.total_customers} customers and {results.total_businesses} businesses"
        )
        print(f"Total actions executed: {results.total_actions_executed}")
        print(f"Total messages sent: {results.total_messages_sent}")
        print()

        # Action breakdown
        print(f"{YELLOW_COLOR}ACTION BREAKDOWN:{RESET_COLOR}")
        # Sort by count descending
        sorted_actions = sorted(
            results.action_breakdown.items(), key=lambda x: x[1], reverse=True
        )
        for action_type, count in sorted_actions:
            print(f"  {action_type}: {count}")
        print()

        # Message breakdown
        print(f"{YELLOW_COLOR}MESSAGE TYPE BREAKDOWN:{RESET_COLOR}")
        # Sort by count descending
        sorted_messages = sorted(
            results.message_type_breakdown.items(), key=lambda x: x[1], reverse=True
        )
        for message_type, count in sorted_messages:
            print(f"  {message_type}: {count}")
        print()

        # Customer summary
        print(f"{CYAN_COLOR}CUSTOMER SUMMARY:{RESET_COLOR}")
        print("=" * 40)

        for customer in results.customer_summaries:
            print(
                f"{customer.customer_name}:\t{customer.messages_sent} messages, "
                f"{customer.proposals_received} proposals, {customer.payments_made} payments,\t"
                f"utility: {customer.utility:.2f}"
            )
        print()

        # Business summary
        print(f"{CYAN_COLOR}BUSINESS SUMMARY:{RESET_COLOR}")
        print("=" * 40)
        for business in results.business_summaries:
            print(
                f"{business.business_name}:\t{business.messages_sent} messages, "
                f"{business.proposals_sent} proposals sent,\tutility: {business.utility:.2f}"
            )
        print()

        # Detailed customer analysis
        print(f"{CYAN_COLOR}DETAILED CUSTOMER ANALYSIS:{RESET_COLOR}")
        print("=" * 40)

        for customer in results.customer_summaries:
            customer_agent_id = customer.customer_id
            customer_data = self.customer_agents[customer_agent_id].customer

            # Customer header
            print(
                f"\n{YELLOW_COLOR}{customer_data.name} (ID: {customer_data.id}){RESET_COLOR}"
            )
            print(
                f"Request: {customer_data.request[:100]}{'...' if len(customer_data.request) > 100 else ''}"
            )
            print(f"Desired items: {list(customer_data.menu_features.keys())}")
            print(f"Required amenities: {customer_data.amenity_features}")

            # Business matches
            menu_matches = self.calculate_menu_matches(customer_agent_id)
            if menu_matches:
                print(
                    f"\n{len(menu_matches)} businesses can fulfill menu requirements:"
                )
                optimal_price = menu_matches[0][1]

                for i, (business_agent_id, price) in enumerate(
                    menu_matches[:3]
                ):  # Show top 3
                    business = (
                        self.business_agents[business_agent_id].business
                        if business_agent_id in self.business_agents
                        else None
                    )
                    business_name = business.name if business else "Unknown"
                    amenity_match = self.check_amenity_match(
                        customer_agent_id, business_agent_id
                    )

                    status = ""
                    if amenity_match and price == optimal_price:
                        status = " (OPTIMAL)"
                    elif amenity_match:
                        status = " (GOOD FIT)"

                    amenity_status = "Yes" if amenity_match else "No"
                    print(
                        f"  {i + 1}. {business_name} - ${price} - Amenities: {amenity_status}{status}"
                    )

            # Customer activity (from collected results)
            print(
                f"\nActivity: {customer.messages_sent} messages sent, "
                f"{customer.proposals_received} proposals received, {customer.payments_made} payments made."
            )

            # Search activity
            print("\nSearch Activity:")
            searches = self.customer_searches.get(customer_agent_id, [])
            if searches:
                unique_queries = {search.query for search, _ in searches}
                print("  Queries: ")
                for search, response in searches:
                    print(f"   - Query: '{search.query}'")
                    print(f"     Page: {search.page}")
                    print(f".    Algorithm: {search.search_algorithm}")
                    print(
                        f"     Businesses: {','.join([b.business.name for b in response.businesses])}"
                    )

                print(f"  Total searches made: {len(searches)}")
                print(f"  Unique queries tried: {len(unique_queries)}")

            # Payment and order details with welfare analysis
            payments = self.customer_payments.get(customer_agent_id, [])
            proposals_received = self.customer_orders.get(customer_agent_id, [])

            # Get optimal price for comparison
            menu_matches = self.calculate_menu_matches(customer_agent_id)
            optimal_price = menu_matches[0][1] if menu_matches else None

            if payments:
                print(f"\n{GREEN_COLOR}{len(payments)} payment(s) made:{RESET_COLOR}")
                for payment in payments:
                    # Find the corresponding proposal
                    proposal = next(
                        (
                            p
                            for p in proposals_received
                            if p.id == payment.proposal_message_id
                        ),
                        None,
                    )
                    if proposal:
                        # Find which business sent this proposal
                        business_agent_id = self._find_business_for_proposal(
                            proposal.id
                        )
                        business_name = "Unknown"
                        if (
                            business_agent_id
                            and business_agent_id in self.business_agents
                        ):
                            business_name = self.business_agents[
                                business_agent_id
                            ].business.name

                        price_paid = proposal.total_price
                        print(
                            f"  - Paid ${price_paid:.2f} to {business_name}, ", end=""
                        )

                        # Check item matching
                        proposal_items = {item.item_name for item in proposal.items}
                        requested_items = set(customer_data.menu_features.keys())

                        if proposal_items != requested_items:
                            print("which does NOT match the requested menu items.")
                            print(
                                f"    (Ordered items: {', '.join(sorted(proposal_items))})"
                            )
                        elif business_agent_id and self.check_amenity_match(
                            customer_agent_id, business_agent_id
                        ):
                            print("which matches all requested amenities, ", end="")
                            if optimal_price is not None:
                                if price_paid < optimal_price:
                                    print(
                                        f"and is BETTER than the optimal posted price by ${round(optimal_price - price_paid, 2)}."
                                    )
                                elif price_paid == optimal_price:
                                    print(
                                        f"and is the optimal price of ${optimal_price:.2f}."
                                    )
                                else:
                                    print(
                                        f"but is NOT the optimal price of ${optimal_price:.2f}."
                                    )
                            else:
                                print("")
                        else:
                            print("which does NOT match all requested amenities.")

                        # Show order details
                        print("    Order items:")
                        for item in proposal.items:
                            print(
                                f"      - {item.item_name}: ${item.unit_price:.2f} x {item.quantity}"
                            )
                    else:
                        print("  - Payment (no matching proposal found)")

            # Utility calculations
            print(
                f"\nCustomer utility: {customer.utility:.2f} (needs met: {customer.needs_met})"
            )

        # Search aggregate
        total_searches = sum([len(s) for s in self.customer_searches.values()])
        searches_per_customer = total_searches / len(self.customer_searches.keys())

        total_queries = []
        total_pages = []
        for search_queries in self.customer_searches.values():
            # Map queries to pages
            queries_to_pages: dict[str, list] = defaultdict(list)
            for search, _ in search_queries:
                queries_to_pages[search.query].append(search.page)

            total_queries.append(len(queries_to_pages.keys()))
            total_pages.append(sum([len(s) for s in queries_to_pages.values()]))

        pages_per_query = sum(total_pages) / sum(total_queries)

        # Transaction summary
        print(f"\n{GREEN_COLOR}TRANSACTION SUMMARY:{RESET_COLOR}")
        print("=" * 40)
        ts = results.transaction_summary
        print(f"Order proposals created: {ts.order_proposals_created}")
        print(f"Payments made: {ts.payments_made}")
        print(f"Average proposal value: ${ts.average_proposal_value:.2f}")
        print(f"Average paid order value: ${ts.average_paid_order_value:.2f}")
        print(f"Total invalid proposals: {ts.total_invalid_proposals}")
        print(f"Invalid proposals purchased: {ts.invalid_proposals_purchased}")

        # Aggregate error types across all invalid proposals
        errors_by_type: dict[str, list[OrderProposalError]] = defaultdict(list)
        for errors in self.invalid_proposals.values():
            for error in errors:
                errors_by_type[error.type].append(error)

        print()

        print("Error types:")
        if errors_by_type:
            # Iterate over error types, most common first
            for error_type, errors in sorted(
                errors_by_type.items(), key=lambda item: len(item[1]), reverse=True
            ):
                print(f"  - {error_type}: {len(errors)}")
                # Build a header to explain the following rows
                header = ""
                indent = " " * 6
                if error_type == "invalid_menu_item_price":
                    header = "Item | Proposed | Actual"
                elif error_type == "invalid_total_price":
                    header = "Proposed | Calculated | Delta"
                elif error_type == "invalid_business":
                    header = "Business"
                elif error_type == "invalid_customer":
                    header = "Customer"

                if header:
                    divider = "-" * len(header)
                    print(indent + header)
                    print(indent + divider)

                # Iterate over each error, with "largest" by sort_key first (e.g. largest levenshtein distance)
                for error in sorted(errors, key=lambda e: e.sort_key, reverse=True):
                    if error.type == "invalid_menu_item":
                        # json.dumps to make the character differences easier to see
                        print(f"{indent}Distance: {error.closest_menu_item_distance}")
                        print(
                            f"{indent}  Proposed: {json.dumps(error.proposed_menu_item)}"
                        )
                        print(
                            f"{indent}  Matched:  {json.dumps(error.closest_menu_item)}"
                        )
                        print()
                    elif error.type == "invalid_menu_item_price":
                        print(
                            f"      {error.menu_item} | ${error.proposed_price:.2f} | ${error.actual_price:.2f}"
                        )
                    elif error.type == "invalid_total_price":
                        print(
                            f"      ${error.proposed_total_price:.2f} | ${error.calculated_total_price:.2f} | ${abs(error.calculated_total_price - error.proposed_total_price):.2f}"
                        )
                    elif error.type == "invalid_business":
                        print(f"      {error.business_agent_id}")
                    elif error.type == "invalid_customer":
                        print(f"      {error.customer_agent_id}")
        else:
            print("  - No errors")

        # Fuzzy matched proposals summary
        print()
        print(
            f"{len(self.purchased_proposal_fuzzy_matches)} purchased proposals contained invalid menu items that fuzzy-matched an actual menu item with distance <= {self.fuzzy_match_distance}"
        )
        for proposal_id, matches in list(self.purchased_proposal_fuzzy_matches.items()):
            print(f"  Proposal: {proposal_id}")
            indent = " " * 6
            for distance, proposed_item, menu_item in matches:
                print(f"{indent}Distance: {distance}")
                print(f"{indent}  Proposed: {json.dumps(proposed_item)}")
                print(f"{indent}  Matched:  {json.dumps(menu_item)}")
                print()

        # LLM Call summary
        print(f"\n{BLUE_COLOR}LLM CALL SUMMARY:{RESET_COLOR}")
        print("=" * 40)
        print(f"LLM providers: {results.llm_providers}")
        print(f"LLM models: {results.llm_models}")
        print(f"Total LLM calls: {results.total_llm_calls}")
        print(f"Failed LLM calls: {results.failed_llm_calls}")

        # Final summary
        print(f"\n{MAGENTA_COLOR}SEARCH SUMMARY:{RESET_COLOR}")
        print("=" * 40)
        print(f"Searches per customer: {searches_per_customer:.2f}")
        print(f"Pages per query: {pages_per_query:.2f}")
        print(f"Total searches: {total_searches}")

        # Final summary
        print(f"\n{CYAN_COLOR}FINAL SUMMARY:{RESET_COLOR}")
        print("=" * 40)
        print(
            f"Customers who made purchases: {results.customers_who_made_purchases}/{results.total_customers}"
        )
        print(
            f"Customers with needs met: {results.customers_with_needs_met}/{results.total_customers}"
        )

        print(f"\nPurchase completion rate: {results.purchase_completion_rate:.1f}%")

        print(
            f"Total marketplace customer utility: {results.total_marketplace_customer_utility:.2f}"
        )

        if results.average_utility_per_active_customer is not None:
            print(
                f"Average utility per active customer: {results.average_utility_per_active_customer:.2f}"
            )


async def run_analytics(
    db_path_or_schema: str,
    db_type: str,
    save_to_json: bool = True,
    print_results: bool = True,
    fuzzy_match_distance: int = 0,
) -> AnalyticsResults:
    """Run comprehensive analytics on the database.
    データベースに対して包括的な分析を実行します。.

    このエントリーポイント関数は:
    - データベースタイプに応じて適切なコントローラを初期化
    - MarketplaceAnalyticsインスタンスを作成
    - レポート生成を実行
    - 結果を返す

    ファジーマッチング（fuzzy_match_distance）について:
    - 0: 完全一致のみを受け入れる（デフォルト）
    - 1以上: レーベンシュタイン距離がこの値以下なら「一致」とみなす
    - 例: fuzzy_match_distance=2 なら "burrito" と "buritos" を一致と判定
    - LLMのタイプミスを許容しつつ、市場効率を正確に測定するために使用

    Args:
        db_path_or_schema: SQLiteデータベースファイルのパス、またはPostgreSQLスキーマ名
        db_type: データベースのタイプ（"sqlite" または "postgres"）
        save_to_json: 結果をJSONファイルに保存するか
        print_results: 結果をコンソールに出力するか
        fuzzy_match_distance: 要求アイテムと提案アイテムを「一致」とみなす最大距離

    Returns:
        AnalyticsResults: 全ての分析結果を含むオブジェクト

    Raises:
        FileNotFoundError: SQLiteファイルが存在しない場合
        ValueError: サポートされていないデータベースタイプの場合

    """
    if db_type == "sqlite":
        # SQLiteデータベースファイルの存在を確認
        if not Path(db_path_or_schema).exists():
            raise FileNotFoundError(
                f"SQLite database file {db_path_or_schema} not found"
            )

        db_name = Path(db_path_or_schema).stem

        # SQLiteコントローラを初期化
        db_controller = SQLiteDatabaseController(db_path_or_schema)
        await db_controller.initialize()

        # 分析エンジンを作成してレポート生成
        analytics = MarketplaceAnalytics(
            db_controller, fuzzy_match_distance=fuzzy_match_distance
        )
        results = await analytics.generate_report(
            db_name=db_name, save_to_json=save_to_json, print_results=print_results
        )
        return results

    elif db_type == "postgres":
        # PostgreSQLデータベースに接続（コンテキストマネージャで自動クリーンアップ）
        async with connect_to_postgresql_database(
            schema=db_path_or_schema,
            host="localhost",
            port=5432,
            password="postgres",
            mode="existing",
        ) as db_controller:
            # 分析エンジンを作成してレポート生成
            analytics = MarketplaceAnalytics(
                db_controller, fuzzy_match_distance=fuzzy_match_distance
            )
            results = await analytics.generate_report(
                db_name=db_path_or_schema,
                save_to_json=save_to_json,
                print_results=print_results,
            )
            return results
    else:
        raise ValueError(
            f"Unsupported database type: {db_type}. Must be 'sqlite' or 'postgres'."
        )
