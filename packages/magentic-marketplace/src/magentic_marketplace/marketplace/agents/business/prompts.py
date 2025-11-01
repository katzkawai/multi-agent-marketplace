"""Prompt generation for the business agent.

ビジネスエージェント用のプロンプト生成モジュール
--------------------------------------------
このモジュールは、ビジネスエージェントがLLMと対話するための
プロンプトを動的に生成します。

カスタマーエージェントとの違い:
1. より単純な対話パターン: 顧客からのメッセージに応答するだけ
2. リアクティブ: 自分から検索や探索はせず、問い合わせを待つ
3. 提案作成能力: OrderProposal（注文提案）を作成できる

主な役割:
- 顧客の問い合わせに対する応答プロンプトの生成
- ビジネス情報（メニュー、価格、設備）の整形と提示
- テキスト応答とOrderProposal作成の使い分けをLLMに指示

プロンプトエンジニアリングの戦略:
- Role-playing: 「あなたはビジネスオーナーです」と明確な役割設定
- Context provision: メニュー、価格、設備情報を構造化して提供
- Decision guidance: いつorder_proposalを使うべきかを明示的に指示
- Constraint enforcement: メニューにあるものだけを正しい価格で提案するよう強調
"""

from magentic_marketplace.platform.logger import MarketplaceLogger

from ...shared.models import Business


class PromptsHandler:
    """Handles prompt generation for the business agent.

    ビジネスエージェントのプロンプト生成ハンドラー
    ------------------------------------------
    顧客の問い合わせに対する応答プロンプトを生成します。

    カスタマーエージェントとの違い:
    - 状態管理が不要: 顧客からのメッセージに反応するだけ
    - プロンプトはメッセージごとに独立: 会話履歴はあるが、アクション履歴は不要
    - よりシンプルな構造: format_response_prompt メソッドのみ
    """

    def __init__(
        self,
        business: Business,
        logger: MarketplaceLogger,
    ):
        """Initialize the prompts handler.

        Args:
            business: Business data
            logger: Logger instance

        必要な情報:
        - business: メニュー（menu_features）と設備（amenity_features）を含む
        - logger: ロギング用（エラーや警告の記録に使用）

        """
        self.business = business
        self.logger = logger

    def format_response_prompt(
        self,
        conversation_history: list[str],
        customer_id: str,
        context: str | None = None,
    ) -> str:
        """Format the prompt for generating responses to customer inquiries.

        顧客の問い合わせに対する応答プロンプトの生成
        ---------------------------------------
        これがビジネスエージェントの唯一のプロンプト生成メソッドです。
        顧客からのメッセージごとに呼ばれます。

        Args:
            conversation_history: 会話履歴（顧客とビジネス間のメッセージリスト）
            customer_id: 顧客のID（応答先を特定するため）
            context: 追加のコンテキスト（エラーメッセージや特別な指示）

        Returns:
            Formatted prompt for LLM

        プロンプトの構成要素:
        1. ビジネス情報: 名前、評価、説明、営業時間、デリバリー可否
        2. メニュー情報: アイテム名と価格のリスト（Item-1, Item-2形式でID付き）
        3. 設備情報: デリバリー、Wi-Fi、駐車場などの有無
        4. 会話履歴: 過去のやり取り
        5. 最新メッセージ: 顧客の最新の問い合わせ
        6. コンテキスト: アクションの選択指示（textかorder_proposalか）
        7. 出力スキーマ: BusinessActionの構造説明

        プロンプトエンジニアリングの工夫:
        - Structured data: メニューと設備を見やすく整形
        - Emphasis: 「正しい価格で」「メニューにあるものだけ」を強調
        - Decision tree: いつorder_proposalを使うべきかを明確に指示
        - Error prevention: 存在しないメニューや間違った価格を避けるよう警告

        """
        # Derive delivery availability from amenity features
        # amenity_featuresからデリバリー可否を抽出
        delivery_available = (
            "Yes" if self.business.amenity_features.get("delivery", False) else "No"
        )

        # Format amenity features for the prompt
        # 設備情報をプロンプト用に整形（Yes/No形式）
        features_block = (
            "\n".join(
                f"  - {k}: {'Yes' if v else 'No'}"
                for k, v in sorted(self.business.amenity_features.items())
            )
            if self.business.amenity_features
            else "  - (none)"
        )

        # Format menu items for the prompt
        # メニューアイテムをItem-N形式で整形（LLMが参照しやすいようにIDを付与）
        menu_lines: list[str] = []
        for item_name, price in self.business.menu_features.items():
            item_id = len(menu_lines) + 1
            menu_lines.append(f"  - Item-{item_id}: {item_name} - ${price:.2f}")

        if not menu_lines:
            menu_lines.append("  - (none listed)")

        # Sorted to match (incorrect, i.e. [1, 10, 11, 2]) sorting from v1
        # ソート（v1との互換性のため、辞書順ソート）
        menu_block = "\n".join(sorted(menu_lines))

        # Build business info with comprehensive structure
        # ビジネス情報ブロックの構築
        business_info_parts = [f"- Name: {self.business.name}"]
        business_info_parts.append(f"- Rating: {self.business.rating:.1f}/1.0")
        business_info_parts.append(f"- Description: {self.business.description}")
        business_info_parts.append("- Hours: Unknown")
        business_info_parts.append(f"- Delivery available: {delivery_available}")

        business_info = "\n".join(business_info_parts)

        # 会話履歴の整形: 最新メッセージとそれ以前を分離
        last_message = conversation_history[-1] if conversation_history else ""
        earlier_conversation_history = (
            "\n".join(conversation_history[:-1])
            if len(conversation_history) > 1
            else ""
        )
        # デフォルトのコンテキスト指示
        if context is None:
            context = "Customer is making an inquiry. Use text action to respond, or create an order_proposal if they want to purchase something specific."

        # Get current date and time
        # 実際のプロンプト文字列の構築
        # 以下のプロンプトは、LLMにビジネスオーナーの役割を与え、
        # 適切な応答（textまたはorder_proposal）を生成させます
        prompt = f"""You are a business owner responding to a customer inquiry. Be helpful, professional, and try to make a sale.

Your business:
{business_info}
- Amenities provided by your business:
{features_block}
- Menu items and prices:
{menu_block}
ONLY tell potential customers what you have on the menu with CORRECT PRICES.

Conversation so far:
{earlier_conversation_history}

Customer just said: "{last_message}"

Context: {context}

Generate a BusinessAction with:
- action_type: "text" for general inquiries/questions, "order_proposal" for creating structured proposals
- text_message: ServiceTextMessageRequest (if action_type is "text")
- order_proposal_message: ServiceOrderProposalMessageRequest (if action_type is "order_proposal")

For all message types, use:
- to_customer_id: {customer_id}
- type: Must match the action_type
- content: Appropriate response content (string for text, OrderProposal for order_proposal)


CREATING ORDER PROPOSALS:
When customers show interest in purchasing (asking about prices, availability, wanting to order),
PREFER creating order_proposal over text responses:

1. Use action_type="order_proposal" when:
   - Customer expresses interest in purchasing specific items
   - You can create a concrete proposal with items, quantities, and prices
   - Customer is asking "how much for..." or "I want to order..."
   - You want to move the conversation toward a purchase

2. The order_proposal_message should contain OrderProposal with:
   - items: list of OrderItem with id (use the menu item ID like "Item-1"), item_name, quantity, unit_price from your menu
   - total_price: sum of all items
   - special_instructions: any relevant notes
   - estimated_delivery: time estimate if applicable

DECISION PRIORITY:
1. If customer wants to purchase specific items: use action_type="order_proposal"
2. For general inquiries: use action_type="text"

REMEMBER: Order proposals let you actively shape the transaction instead of just responding to customer orders!"""

        return prompt
