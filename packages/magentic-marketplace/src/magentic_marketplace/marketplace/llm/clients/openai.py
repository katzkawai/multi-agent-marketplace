"""OpenAI model client implementation.

OpenAIクライアントの実装
========================

このモジュールは、OpenAI API（GPT-4、GPT-5、o1、o3、o4などの推論モデルを含む）への
統合を提供します。通常のテキスト生成と構造化出力の両方をサポートし、
自動リトライとエラーハンドリングを実装しています。

主な機能:
- OpenAI APIへの非同期接続
- 構造化出力生成（Pydanticモデルを使用）
- 推論モデル（o1、o3、o4、gpt-5シリーズ）の特別な取り扱い
- 自動リトライ機能（最大3回）
- トークン使用量のトラッキング
- クライアントインスタンスのキャッシング

使用例:
    from magentic_marketplace.marketplace.llm.clients.openai import OpenAIClient

    # 環境変数から設定を読み込んで初期化
    client = OpenAIClient()

    # テキスト生成
    text, usage = await client.generate(
        model="gpt-4",
        messages=[{"role": "user", "content": "こんにちは"}],
        temperature=0.7
    )

    # 構造化出力生成
    from pydantic import BaseModel
    class Response(BaseModel):
        answer: str

    response, usage = await client.generate(
        model="gpt-4",
        messages=[{"role": "user", "content": "質問"}],
        response_format=Response
    )
"""

import json
import threading
from collections.abc import Sequence
from hashlib import sha256
from typing import Any, Literal, cast, overload

import pydantic
from openai import AsyncOpenAI
from openai.types.chat import (
    ChatCompletionAssistantMessageParam,
    ChatCompletionMessageParam,
    ChatCompletionToolMessageParam,
    ChatCompletionToolParam,
)
from openai.types.chat.chat_completion import ChatCompletion
from openai.types.shared_params import FunctionDefinition

from ..base import (
    AllowedChatCompletionMessageParams,
    ProviderClient,
    TResponseModel,
    Usage,
)
from ..config import BaseLLMConfig, EnvField


class OpenAIConfig(BaseLLMConfig):
    """Configuration for OpenAI provider.

    OpenAIプロバイダーの設定クラス
    ============================

    OpenAI APIに接続するための設定を管理します。
    環境変数から自動的に値を読み込みます。

    環境変数:
        LLM_PROVIDER: プロバイダー名（デフォルト: "openai"）
        OPENAI_API_KEY: OpenAI APIキー（必須）
        OPENAI_BASE_URL: カスタムAPIエンドポイント（オプション）

    Attributes:
        provider: プロバイダー識別子（"openai"に固定）
        api_key: OpenAI APIキー
        base_url: カスタムベースURL（Azure OpenAIなどに使用）

    """

    provider: Literal["openai"] = EnvField("LLM_PROVIDER", default="openai")  # pyright: ignore[reportIncompatibleVariableOverride]
    api_key: str = EnvField("OPENAI_API_KEY", exclude=True)
    base_url: str | None = EnvField("OPENAI_BASE_URL", default=None)


class OpenAIClient(ProviderClient[OpenAIConfig]):
    """OpenAI model client that accepts OpenAI SDK arguments.

    OpenAI APIクライアントクラス
    ===========================

    OpenAI APIとの通信を管理するクライアントクラスです。
    非同期操作をサポートし、クライアントインスタンスをキャッシュして
    パフォーマンスを最適化します。

    設計の特徴:
    - シングルトンパターン: 同じ設定のクライアントは再利用される
    - スレッドセーフ: 複数のスレッドから安全にアクセス可能
    - 非同期API: asyncio を使用した非同期処理

    Attributes:
        _client_cache: 設定ごとにクライアントインスタンスをキャッシュする辞書
        _cache_lock: キャッシュアクセスを保護するスレッドロック
        config: OpenAI設定オブジェクト
        client: AsyncOpenAIクライアントインスタンス

    """

    _client_cache: dict[str, "OpenAIClient"] = {}
    _cache_lock = threading.Lock()

    def __init__(self, config: OpenAIConfig | None = None):
        """Initialize OpenAI client.

        OpenAIクライアントの初期化
        =========================

        環境変数または明示的な設定からOpenAIクライアントを初期化します。

        Args:
            config: OpenAI設定オブジェクト。Noneの場合は環境変数から作成

        Raises:
            ValueError: APIキーが設定されていない場合

        Note:
            OPENAI_API_KEYは必須の環境変数です。
            .envファイルに設定するか、環境変数として設定してください。

        """
        if config is None:
            config = OpenAIConfig()
        else:
            config = OpenAIConfig.model_validate(config)

        super().__init__(config)

        self.config = config
        if not self.config.api_key:
            raise ValueError(
                "OpenAI API key not found. Set OPENAI_API_KEY environment variable "
                "or pass api_key in config."
            )
        # AsyncOpenAIクライアントの作成（非同期操作用）
        self.client = AsyncOpenAI(
            api_key=self.config.api_key, base_url=self.config.base_url
        )

    @staticmethod
    def _get_cache_key(config: OpenAIConfig) -> str:
        """Generate cache key for a config.

        設定からキャッシュキーを生成
        ===========================

        設定オブジェクトからユニークなハッシュキーを生成します。
        同じAPIキーとプロバイダーを持つ設定は同じキーを返します。

        Args:
            config: OpenAI設定オブジェクト

        Returns:
            SHA256ハッシュ文字列（キャッシュキー）

        """
        config_json = config.model_dump_json(include={"api_key", "provider"})
        return sha256(config_json.encode()).hexdigest()

    @staticmethod
    def from_cache(config: OpenAIConfig) -> "OpenAIClient":
        """Get or create client from cache.

        キャッシュからクライアントを取得または作成
        ========================================

        指定された設定に対応するクライアントをキャッシュから取得します。
        存在しない場合は新しいクライアントを作成してキャッシュに追加します。

        この機能により、同じ設定を持つ複数のリクエストで
        クライアントインスタンスを再利用でき、リソースを節約できます。

        Args:
            config: OpenAI設定オブジェクト

        Returns:
            キャッシュされたまたは新しく作成されたOpenAIClientインスタンス

        Note:
            スレッドセーフ: 複数のスレッドから同時に呼び出しても安全です。

        """
        cache_key = OpenAIClient._get_cache_key(config)
        with OpenAIClient._cache_lock:
            if cache_key not in OpenAIClient._client_cache:
                OpenAIClient._client_cache[cache_key] = OpenAIClient(config)
            return OpenAIClient._client_cache[cache_key]

    @overload
    async def _generate(
        self,
        *,
        model: str,
        messages: Sequence[AllowedChatCompletionMessageParams],
        temperature: float | None = None,
        max_tokens: int | None = None,
        reasoning_effort: str | int | None = None,
        response_format: None = None,
        **kwargs: Any,
    ) -> tuple[str, Usage]: ...

    @overload
    async def _generate(
        self,
        *,
        model: str,
        messages: Sequence[AllowedChatCompletionMessageParams],
        temperature: float | None = None,
        max_tokens: int | None = None,
        reasoning_effort: str | int | None = None,
        response_format: type[TResponseModel],
        **kwargs: Any,
    ) -> tuple[TResponseModel, Usage]: ...

    async def _generate(
        self,
        *,
        model: str,
        messages: Sequence[AllowedChatCompletionMessageParams],
        temperature: float | None = None,
        max_tokens: int | None = None,
        reasoning_effort: str | int | None = None,
        response_format: type[TResponseModel] | None = None,
        **kwargs: Any,
    ) -> tuple[str, Usage] | tuple[TResponseModel, Usage]:
        """Generate completion using OpenAI API.

        OpenAI APIを使用してテキスト生成または構造化出力を実行
        ====================================================

        このメソッドは、OpenAI APIを呼び出して、通常のテキスト生成または
        Pydanticモデルに基づく構造化出力を生成します。

        推論モデル（o1、o3、o4、gpt-5シリーズ）と通常のモデル（gpt-4など）で
        異なるパラメータの取り扱いを行います。

        Args:
            model: 使用するモデル名（例: "gpt-4", "gpt-5-nano", "o1-preview"）
            messages: 会話履歴（OpenAI形式のメッセージリスト）
            temperature: 生成のランダム性（0.0-2.0）。推論モデルでは制限あり
            max_tokens: 生成する最大トークン数
            reasoning_effort: 推論努力レベル（"minimal", "low", "medium", "high"）
            response_format: 構造化出力用のPydanticモデルクラス
            **kwargs: OpenAI APIに渡す追加パラメータ

        Returns:
            tuple[str, Usage]: response_formatがNoneの場合、テキストと使用量
            tuple[TResponseModel, Usage]: response_format指定時、構造化データと使用量

        Raises:
            RuntimeError: 構造化出力のパースに3回失敗した場合

        Note:
            推論モデル（o1、o3、o4、gpt-5）の特別な取り扱い:
            - max_completion_tokensパラメータを使用
            - temperature < 1.0は無視される（一部モデルを除く）
            - reasoning_effortパラメータをサポート

        """
        # APIリクエストの引数を構築（推論モデルの特別な処理を含む）
        args: dict[str, Any] = {
            "model": model,
            "messages": messages,
        }

        # 推論モデル vs 通常モデルの判定
        # 推論モデル: gpt-5、o4、o3、o1シリーズ
        is_reasoning_model = any(
            reasoning_model in model for reasoning_model in ("gpt-5", "o4", "o3", "o1")
        )

        if is_reasoning_model:
            # 推論モデルは max_completion_tokens を使用
            # （通常の max_tokens ではなく）
            if max_tokens:
                args["max_completion_tokens"] = max_tokens

            if "gpt-5-chat" not in model:
                # ほとんどの推論モデルは temperature < 1.0 をサポートしない
                if temperature and temperature < 1.0:
                    temperature = None

            # サポートされているモデルに reasoning_effort を設定
            # o1モデルは reasoning_effort をサポートしないため除外
            if reasoning_effort is not None and "o1" not in model:
                if reasoning_effort == "minimal":
                    reasoning_effort = "low"  # oモデルは "minimal" をサポートしない
                args["reasoning_effort"] = reasoning_effort
        else:
            # 通常モデル（gpt-4など）
            if temperature is not None:
                args["temperature"] = temperature
            if max_tokens is not None:
                args["max_tokens"] = max_tokens

        # 追加のキーワード引数をマージ
        args.update(kwargs)

        # 構造化出力の処理
        if response_format is not None:
            # 構造化出力のパースを最大3回試行
            # エラーが発生した場合、エラー情報を会話履歴に追加して再試行
            exceptions: list[Exception] = []
            for _ in range(3):
                try:
                    # OpenAI の parse API を使用して構造化出力を生成
                    response = await self.client.chat.completions.parse(
                        response_format=response_format, **args
                    )
                    parsed = response.choices[0].message.parsed
                    if parsed is not None:
                        # パース成功：使用量情報と共に返す
                        usage = Usage(
                            token_count=response.usage.total_tokens
                            if response.usage
                            else 0,
                            provider="openai",
                            model=model,
                        )
                        return parsed, usage
                    elif response.choices[0].message.refusal:
                        # モデルが出力を拒否：再試行のために例外を発生
                        raise ValueError(response.choices[0].message.refusal)
                    else:
                        # 不明な失敗：再試行情報がないため中断
                        break

                except Exception as e:
                    exceptions.append(e)
                    # エラーメッセージを会話履歴に追加して、モデルが情報を得て再試行できるようにする
                    args["messages"].append({"role": "user", "content": str(e)})
            # ここに到達した場合、再試行回数を使い果たした
            exc_message = "Exceeded attempts to parse response_format."
            if exceptions:
                exc_message += "Inner exceptions: " + " ".join(map(str, exceptions))
            raise RuntimeError(exc_message)

        else:
            # 通常のテキスト生成
            response = cast(
                ChatCompletion, await self.client.chat.completions.create(**args)
            )
            usage = Usage(
                token_count=response.usage.total_tokens if response.usage else 0,
                provider="openai",
                model=model,
            )
            if response.choices and response.choices[0].message.content:
                return response.choices[0].message.content, usage
            return "", usage

    async def _generate_text(
        self,
        model: str,
        messages: Sequence[AllowedChatCompletionMessageParams],
        **kwargs: Any,
    ):
        response = await self.client.chat.completions.create(
            model=model, messages=messages, stream=False, **kwargs
        )
        usage = Usage(
            token_count=response.usage.total_tokens if response.usage else 0,
            provider="openai",
            model=model,
        )
        if response.choices and response.choices[0].message.content:
            return response.choices[0].message.content, usage
        return "", usage

    async def _generate_struct(
        self,
        model: str,
        messages: Sequence[ChatCompletionMessageParam],
        response_format: type[TResponseModel],
        **kwargs: Any,
    ):
        """Generate structured output using tool calling (legacy method).

        ツール呼び出しを使用した構造化出力生成（レガシーメソッド）
        ========================================================

        このメソッドは、OpenAIのツール呼び出し機能を使用して
        構造化出力を生成します。parse APIをサポートしない
        古いモデルや特定のユースケースで使用されます。

        動作の流れ:
        1. Pydanticモデルをツール定義に変換
        2. モデルに必須ツールとして提供
        3. モデルがツールを呼び出す形で構造化出力を生成
        4. JSON出力をPydanticモデルで検証
        5. エラー時は会話履歴にエラー情報を追加して再試行（最大3回）

        Args:
            model: 使用するモデル名
            messages: 会話履歴
            response_format: 出力スキーマを定義するPydanticモデルクラス
            **kwargs: OpenAI APIに渡す追加パラメータ

        Returns:
            TResponseModel: 検証された構造化データ

        Raises:
            Exception: 3回の試行後もパースに失敗した場合

        Note:
            このメソッドは、_generate メソッドから内部的に呼び出されます。
            通常、ユーザーが直接呼び出す必要はありません。

        """
        messages = list(messages)
        # 最終エラー用に例外メッセージを追跡
        exceptions: list[str] = []

        # Pydanticモデルをツール定義に変換
        # ツールの名前、説明、パラメータスキーマを設定
        tool = ChatCompletionToolParam(
            type="function",
            function=FunctionDefinition(
                name=f"Generate{response_format.__name__}",
                description=f"Generate a {response_format.__name__}.",
                parameters=response_format.model_json_schema(),
            ),
        )

        # エラーから回復しながら最大3回試行
        for _ in range(3):
            # ツール使用を必須としてAPIを呼び出し
            completion = await self.client.chat.completions.create(
                model=model,
                messages=messages,
                tools=[tool],
                tool_choice="required",  # モデルは必ずツールを呼び出す
                stream=False,
                **kwargs,
            )

            if not completion.choices:
                raise ValueError("Failed to _generate_struct: choices was empty")

            message = completion.choices[0].message
            tool_calls = message.tool_calls

            if not tool_calls:
                raise ValueError("Failed to _generate_struct: tool_calls was empty")

            tool_call = tool_calls[0]

            if tool_call.type != "function":
                raise ValueError(
                    "Failed to _generate_struct: tool_call was not function type"
                )

            # パース失敗時のエラー回復のため、生成されたツール呼び出しをメッセージ履歴に追加
            messages.append(
                ChatCompletionAssistantMessageParam(
                    role="assistant",
                    tool_calls=[
                        {
                            "id": tool_call.id,
                            "type": "function",
                            "function": {
                                "name": tool_call.function.name,
                                "arguments": tool_call.function.arguments,
                            },
                        }
                    ],
                )
            )

            try:
                # ツールの引数（JSON文字列）をPydanticモデルで検証
                response: TResponseModel = response_format.model_validate_json(
                    tool_call.function.arguments
                )
                return response
            except (json.decoder.JSONDecodeError, pydantic.ValidationError) as e:
                import traceback

                # エラーの詳細情報を収集
                tb_str = traceback.format_exc()
                error_message = f"Error parsing tool: {e}\nTraceback:\n{tb_str}"
                exceptions.append(error_message)
                # エラー情報をツール結果として会話履歴に追加
                # これにより、モデルは次回の試行でエラーを修正できる
                messages.append(
                    ChatCompletionToolMessageParam(
                        role="tool",
                        tool_call_id=tool_call.id,
                        content=error_message,
                    )
                )
        # 最大再試行回数を超えた場合
        raise Exception(
            "Failed to _generate_struct: Exceeded maximum retries. Inner exceptions: "
            + " -> ".join(exceptions)
        )
