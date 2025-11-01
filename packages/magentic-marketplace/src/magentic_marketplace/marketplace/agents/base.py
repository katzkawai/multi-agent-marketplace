"""Base agent functionality for the simple marketplace."""

"""
マーケットプレイスのエージェントベースクラス

このモジュールは、マーケットプレイスで活動する全てのエージェント（顧客エージェント、
ビジネスエージェント）の基底クラスを定義します。

主な機能:
- LLM（大規模言語モデル）との統合
- 構造化されたLLM出力の生成
- メッセージの送受信
- エージェント活動の自動ログ記録

エージェントとは:
このシステムにおけるエージェントは、マーケットプレイスで自律的に行動する
AI駆動のアクターです。顧客エージェントはサービスを探し、ビジネスエージェントは
提案を行います。全てのエージェントはLLMを使用して意思決定を行います。
"""

from datetime import UTC, datetime
from typing import Any, TypeVar

from pydantic import BaseModel

# プラットフォーム層からベースエージェントクラスをインポート
# BaseAgent: 全エージェントの基礎となる抽象クラス
# TProfile: エージェントプロファイルの型変数
from magentic_marketplace.platform.agent.base import BaseAgent, TProfile

# マーケットプレイスアクション（メッセージ送受信）をインポート
from ..actions import (
    FetchMessages,  # メッセージ取得アクション
    FetchMessagesResponse,  # メッセージ取得レスポンス
    Message,  # メッセージデータモデル
    SendMessage,  # メッセージ送信アクション
)

# LLM統合機能をインポート
from ..llm import generate  # LLMテキスト生成関数
from ..llm.config import BaseLLMConfig  # LLM設定クラス

# TResponseFormat: 構造化LLM出力用の型変数（Pydanticモデル限定）
# この型変数により、generate_struct()メソッドが型安全な構造化出力を返せます
TResponseFormat = TypeVar("TResponseFormat", bound=BaseModel)


class LLMCallMetadata(BaseModel):
    """Metadata returned from LLM calls for logging purposes."""

    """
    LLM呼び出しのメタデータ

    LLMとのやり取りをログ記録するための情報を保持します。
    このデータは実験後の分析で使用されます（コスト計算、パフォーマンス分析など）。
    """

    duration_ms: float  # LLM呼び出しの所要時間（ミリ秒）
    token_count: int  # 使用されたトークン数（課金の基準）
    provider: str  # LLMプロバイダー名（"openai", "anthropic", "google"など）
    model: str  # 使用されたモデル名（"gpt-4", "claude-3-5-sonnet"など）


class AgentLogData(BaseModel):
    """Structured log data for agent activities."""

    """
    エージェント活動のログデータ

    エージェントの活動を構造化してログに記録するためのモデルです。
    データベースに保存され、後で実験の分析に使用されます。
    """

    agent_class: str  # エージェントのクラス名（"CustomerAgent", "BusinessAgent"など）
    agent_id: str  # エージェントの一意な識別子
    additional_data: dict[str, Any] = {}  # その他の追加情報（柔軟に拡張可能）


class BaseSimpleMarketplaceAgent(BaseAgent[TProfile]):
    """Base class for simple marketplace agents with common functionality."""

    """
    マーケットプレイスエージェントの基底クラス

    このクラスは全てのマーケットプレイスエージェント（顧客エージェント、ビジネスエージェント）
    の共通機能を提供します。

    主な責務:
    1. MarketplaceClientとの統合（マーケットプレイスサーバーとの通信）
    2. LLM（大規模言語モデル）との統合
    3. メッセージの送受信管理
    4. 構造化されたLLM出力の生成
    5. エージェント活動のログ記録

    サブクラス（CustomerAgent、BusinessAgentなど）は、run()メソッドを
    実装することで、エージェント固有のロジックを定義します。

    型パラメータ:
        TProfile: エージェントのプロファイル型（CustomerProfile、BusinessProfileなど）
    """

    def __init__(
        self, profile: TProfile, base_url: str, llm_config: BaseLLMConfig | None = None
    ):
        """Initialize the simple marketplace agent."""
        """
        マーケットプレイスエージェントの初期化

        Args:
            profile: エージェントのプロファイル情報（ID、名前、機能など）
            base_url: マーケットプレイスサーバーのURL（HTTP通信用）
            llm_config: LLM設定（プロバイダー、モデル、温度など）。
                       Noneの場合、デフォルト設定が使用されます。
        """
        # 親クラスを初期化（MarketplaceClientを設定）
        super().__init__(profile, base_url)

        # メッセージ追跡用の状態変数
        self.last_fetch_index: int | None = (
            None  # 最後に取得したメッセージのインデックス
        )
        self.llm_config = llm_config or BaseLLMConfig()  # LLM設定（デフォルト使用可能）
        self._seen_message_indexes: set[int] = (
            set()
        )  # 既に処理したメッセージのインデックス集合（重複防止）

    async def send_message(self, to_agent_id: str, message: Message):
        """Send a message to another agent.

        Args:
            to_agent_id: ID of the agent to send the message to
            message: The message to send

        Returns:
            Result of the action execution

        """
        """
        他のエージェントにメッセージを送信

        マーケットプレイスでは、エージェント間のコミュニケーションは全てメッセージを
        通じて行われます。例えば、顧客エージェントがビジネスエージェントに問い合わせを
        送ったり、ビジネスエージェントが提案を返したりします。

        このメソッドは非同期（async）です。Pythonのasync/awaitパターンを使用して、
        複数のエージェントが同時に動作できるようにしています。

        Args:
            to_agent_id: 送信先エージェントのID（例: "business_001"）
            message: 送信するメッセージ（Messageオブジェクト、typeとcontentを含む）

        Returns:
            アクション実行結果（成功/失敗、レスポンスデータなど）
        """
        # SendMessageアクションを作成（マーケットプレイスプロトコルに従う）
        action = SendMessage(
            from_agent_id=self.id,  # 送信元エージェントID（自分）
            to_agent_id=to_agent_id,  # 送信先エージェントID
            created_at=datetime.now(UTC),  # メッセージの作成時刻（UTC）
            message=message,  # メッセージ本体
        )

        # メッセージ送信をログに記録（デバッグ・分析用）
        self.logger.info(
            f"Sending {message.type} message to {to_agent_id}",
            data=action,
        )

        # アクションをマーケットプレイスサーバーに送信して実行
        # execute_action()は親クラス（BaseAgent）で定義されており、
        # HTTP経由でMarketplaceServerと通信します
        result = await self.execute_action(action)

        return result

    async def fetch_messages(
        self,
    ) -> FetchMessagesResponse:
        """Fetch messages received by this agent.

        Args:
            from_agent_id: Filter by sender agent ID
            limit: Maximum number of messages to retrieve
            offset: Number of messages to skip for pagination
            after_index: Only return messages with index greater than this

        Returns:
            Response containing the fetched messages

        """
        """
        このエージェント宛のメッセージを取得

        マーケットプレイスサーバーからこのエージェント宛のメッセージを取得します。
        重複したメッセージを防ぐため、既に処理したメッセージはフィルタリングされます。

        このメソッドは、エージェントのrunループ内で定期的に呼び出され、
        新しいメッセージをチェックします。

        Returns:
            メッセージ取得レスポンス（新しいメッセージのリストを含む）
        """
        # FetchMessagesアクションを作成
        action = FetchMessages()

        # アクションを実行してメッセージを取得
        result = await self.execute_action(action)

        # アクションが失敗した場合の処理
        if result.is_error:
            # エージェントのループを壊さないように、空のレスポンスを返す
            # エラーは警告としてログに記録
            self.logger.warning(f"Failed to fetch messages: {result.content}")
            return FetchMessagesResponse(messages=[], has_more=False)

        # 成功した場合、結果をFetchMessagesResponseモデルに変換
        response = FetchMessagesResponse.model_validate(result.content)

        # 重複メッセージフィルタリング
        # 既に処理したメッセージを除外して、新しいメッセージのみを抽出
        new_messages = []
        for message in response.messages:
            # このメッセージのインデックスが未処理の場合のみ追加
            if message.index not in self._seen_message_indexes:
                new_messages.append(message)
                # 処理済みとしてマーク
                self._seen_message_indexes.add(message.index)

                # 最新のメッセージインデックスを更新
                if (
                    self.last_fetch_index is None
                    or message.index > self.last_fetch_index
                ):
                    self.last_fetch_index = message.index

        # 新しいメッセージのみを含むレスポンスを返す
        return response.model_copy(update={"messages": new_messages})

    async def generate(self, prompt: str, **kwargs: Any) -> tuple[str, Any]:
        """Generate LLM response with automatic logging.

        Args:
            prompt: The prompt to send to the LLM
            **kwargs: Additional arguments passed to generate

        Returns:
            Tuple of (response_text, call_metadata)

        """
        """
        LLMを使用してテキストを生成（自動ログ記録付き）

        このメソッドは、LLM（大規模言語モデル）にプロンプトを送信し、
        テキスト応答を取得します。エージェントの意思決定に使用されます。

        LLM統合の仕組み:
        1. プロンプト（指示文）をLLMに送信
        2. LLMが自然言語で応答を生成
        3. 応答とメタデータ（トークン数、モデル名など）を返す
        4. 全ての呼び出しは自動的にデータベースにログ記録される

        Args:
            prompt: LLMに送信するプロンプト（指示文）
            **kwargs: 追加の引数（temperature、max_tokensなど）

        Returns:
            タプル: (LLMの応答テキスト, 呼び出しメタデータ)
        """
        # LLM設定をマージ（llm_configとkwargsを結合、kwargsが優先）
        kwargs = {**self.llm_config.model_dump(), **kwargs}

        # LLMを呼び出してテキストを生成
        # logger: 自動的にログをデータベースに記録
        # log_metadata: ログに含める追加情報（agent_id）
        response, usage = await generate(
            prompt, logger=self.logger, log_metadata={"agent_id": self.id}, **kwargs
        )

        # メタデータオブジェクトを作成（互換性のため）
        call_metadata = LLMCallMetadata(
            duration_ms=0,  # 所要時間はgenerate関数内部でログ記録される
            token_count=usage.token_count,  # 使用トークン数
            provider=usage.provider,  # プロバイダー名（openai、anthropicなど）
            model=usage.model,  # モデル名（gpt-4、claude-3-5-sonnetなど）
        )

        return response, call_metadata

    async def generate_struct(
        self, prompt: str, response_format: type[TResponseFormat], **kwargs: Any
    ):
        """Generate LLM structured response with automatic logging.

        Args:
            prompt: The prompt to send to the LLM
            response_format: The Pydantic model class for structured response
            **kwargs: Additional arguments passed to generate_struct

        Returns:
            Tuple of (structured_response, call_metadata)

        """
        """
        LLMを使用して構造化された出力を生成（自動ログ記録付き）

        【重要】このメソッドは、エージェントがLLMを使って意思決定を行う際の
        中核的な機能です。

        構造化生成とは:
        通常のLLMは自由形式のテキストを返しますが、構造化生成では、
        Pydanticモデルで定義したスキーマに従ったJSONオブジェクトを返します。
        これにより、LLMの出力をプログラムで確実に処理できます。

        例:
            # Pydanticモデルの定義
            class Decision(BaseModel):
                action: str  # "buy" or "skip"
                reason: str  # 理由

            # 構造化生成の使用
            decision, metadata = await self.generate_struct(
                "この提案を受け入れるべきか？",
                response_format=Decision
            )
            # decision.action と decision.reason に安全にアクセス可能

        この仕組みにより、エージェントは複雑な意思決定を行い、
        その結果を確実に処理できます。

        Args:
            prompt: LLMに送信するプロンプト（指示文）
            response_format: 出力スキーマを定義するPydanticモデルクラス
            **kwargs: 追加の引数（temperature、max_tokensなど）

        Returns:
            タプル: (構造化された応答オブジェクト, 呼び出しメタデータ)
        """
        # LLM設定をマージ（llm_configとkwargsを結合、kwargsが優先）
        kwargs = {
            **self.llm_config.model_dump(),
            **kwargs,
        }

        # LLMを呼び出して構造化された出力を生成
        # response_format: Pydanticモデルを渡すことで、LLMがそのスキーマに
        #                  従ったJSONを生成し、自動的にパースされます
        response, usage = await generate(
            prompt,
            response_format=response_format,  # 構造化出力のスキーマ
            logger=self.logger,  # 自動的にログをデータベースに記録
            log_metadata={"agent_id": self.id},  # ログに含める追加情報
            **kwargs,
        )

        # メタデータオブジェクトを作成（互換性のため）
        call_metadata = LLMCallMetadata(
            duration_ms=0,  # 所要時間はgenerate関数内部でログ記録される
            token_count=usage.token_count,  # 使用トークン数
            provider=usage.provider,  # プロバイダー名（openai、anthropicなど）
            model=usage.model,  # モデル名（gpt-4、claude-3-5-sonnetなど）
        )

        return response, call_metadata
