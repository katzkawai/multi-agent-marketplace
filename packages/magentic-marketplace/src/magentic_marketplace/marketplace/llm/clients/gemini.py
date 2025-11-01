"""Gemini model client implementation.

Geminiクライアントの実装
=======================

このモジュールは、Google Gemini API（Gemini 1.5、Gemini 2.0シリーズなど）への
統合を提供します。OpenAI互換のインターフェースを維持しながら、Gemini固有の機能
（thinking mode、ネイティブJSON出力）をサポートします。

主な機能:
- Google Gemini APIへの非同期接続
- OpenAI形式のメッセージをGemini形式に自動変換
- 構造化出力生成（response_schemaを使用）
- Thinking mode（推論モード）のサポート
- 自動リトライ機能（最大3回）
- トークン使用量のトラッキング
- クライアントインスタンスのキャッシング

重要な違い:
- Systemメッセージはsystem_instructionパラメータに配置
- Assistant roleは "model" roleに変換される
- 構造化出力はresponse_schemaとresponse_mime_typeで制御
- Thinking modeではthinking_budgetで推論トークン数を指定

使用例:
    from magentic_marketplace.marketplace.llm.clients.gemini import GeminiClient

    # 環境変数から設定を読み込んで初期化
    client = GeminiClient()

    # テキスト生成
    text, usage = await client.generate(
        model="gemini-2.0-flash-exp",
        messages=[{"role": "user", "content": "こんにちは"}],
        temperature=0.7
    )

    # 構造化出力生成
    from pydantic import BaseModel
    class Response(BaseModel):
        answer: str

    response, usage = await client.generate(
        model="gemini-2.0-flash-exp",
        messages=[{"role": "user", "content": "質問"}],
        response_format=Response
    )
"""

import json
import threading
from collections.abc import Sequence
from hashlib import sha256
from typing import Any, Literal, overload

import google.genai as genai
import google.genai.types
import pydantic

from ..base import (
    AllowedChatCompletionMessageParams,
    ProviderClient,
    TResponseModel,
    Usage,
)
from ..config import BaseLLMConfig, EnvField


class GeminiConfig(BaseLLMConfig):
    """Configuration for Gemini provider.

    Geminiプロバイダーの設定クラス
    =============================

    Google Gemini APIに接続するための設定を管理します。
    環境変数から自動的に値を読み込みます。

    環境変数:
        LLM_PROVIDER: プロバイダー名（デフォルト: "gemini"）
        GEMINI_API_KEY: Google AI Studio APIキー（必須）

    Attributes:
        provider: プロバイダー識別子（"gemini"に固定）
        api_key: Gemini APIキー

    """

    provider: Literal["gemini"] = EnvField("LLM_PROVIDER", default="gemini")  # pyright: ignore[reportIncompatibleVariableOverride]
    api_key: str = EnvField("GEMINI_API_KEY", exclude=True)


class GeminiClient(ProviderClient[GeminiConfig]):
    """Gemini model client that accepts OpenAI SDK arguments."""

    _client_cache: dict[str, "GeminiClient"] = {}
    _cache_lock = threading.Lock()

    def __init__(self, config: GeminiConfig | None = None):
        """Initialize Gemini client.

        Args:
            config: Gemini configuration. If None, creates from environment.

        """
        if config is None:
            config = GeminiConfig()
        else:
            config = GeminiConfig.model_validate(config)

        super().__init__(config)

        self.config = config
        if not self.config.api_key:
            raise ValueError(
                "Gemini API key not found. Set GEMINI_API_KEY environment variable "
                "or pass api_key in config."
            )
        self.client = genai.Client(api_key=self.config.api_key)

    @staticmethod
    def _get_cache_key(config: GeminiConfig) -> str:
        """Generate cache key for a config."""
        config_json = config.model_dump_json(include={"api_key", "provider"})
        return sha256(config_json.encode()).hexdigest()

    @staticmethod
    def from_cache(config: GeminiConfig) -> "GeminiClient":
        """Get or create client from cache."""
        cache_key = GeminiClient._get_cache_key(config)
        with GeminiClient._cache_lock:
            if cache_key not in GeminiClient._client_cache:
                GeminiClient._client_cache[cache_key] = GeminiClient(config)
            return GeminiClient._client_cache[cache_key]

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
        """Generate completion using Gemini API."""
        # Handle structured output
        if response_format is not None:
            return await self._generate_struct(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                reasoning_effort=reasoning_effort,
                response_format=response_format,
                **kwargs,
            )
        else:
            return await self._generate_text(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                reasoning_effort=reasoning_effort,
                **kwargs,
            )

    async def _generate_text(
        self,
        model: str,
        messages: Sequence[AllowedChatCompletionMessageParams],
        temperature: float | None = None,
        max_tokens: int | None = None,
        reasoning_effort: str | int | None = None,
        **kwargs: Any,
    ) -> tuple[str, Usage]:
        """Generate text completion using Gemini API."""
        # Convert messages to Gemini format
        contents, system_prompt = self._convert_messages(messages)

        # Build config
        config = google.genai.types.GenerateContentConfig()

        if temperature is not None:
            config.temperature = temperature

        if max_tokens is not None:
            config.max_output_tokens = max_tokens

        # Handle reasoning effort -> thinking config
        if reasoning_effort is not None:
            if reasoning_effort == "minimal":
                reasoning_effort = 0
            elif isinstance(reasoning_effort, str):
                reasoning_effort = 0  # Fallback for unsupported string values

            if isinstance(reasoning_effort, int) and reasoning_effort >= -1:  # type: ignore[misc]
                config.thinking_config = google.genai.types.ThinkingConfig(
                    thinking_budget=reasoning_effort
                )

        # Add system instruction if we have system messages
        if system_prompt:
            config.system_instruction = system_prompt

        args: dict[str, Any] = {
            "model": model,
            "contents": contents,
            "config": config,
        }

        # Add any additional kwargs
        args.update(kwargs)

        try:
            response = await self.client.aio.models.generate_content(**args)

            token_count = 0
            if response.usage_metadata and response.usage_metadata.total_token_count:
                token_count = response.usage_metadata.total_token_count

            usage = Usage(
                token_count=token_count,
                provider="gemini",
                model=model,
            )

            return response.text or "", usage

        except Exception as e:
            raise RuntimeError(f"Gemini API call failed: {str(e)}") from e

    async def _generate_struct(
        self,
        model: str,
        messages: Sequence[AllowedChatCompletionMessageParams],
        response_format: type[TResponseModel],
        temperature: float | None = None,
        max_tokens: int | None = None,
        reasoning_effort: str | int | None = None,
        **kwargs: Any,
    ) -> tuple[TResponseModel, Usage]:
        """Generate structured output using Gemini API with retry logic."""
        # Convert messages to Gemini format
        contents, system_prompt = self._convert_messages(messages)

        # Track exception messages for final error
        exceptions: list[str] = []

        # Build config
        config = google.genai.types.GenerateContentConfig()

        if temperature is not None:
            config.temperature = temperature

        if max_tokens is not None:
            config.max_output_tokens = max_tokens

        # Handle reasoning effort -> thinking config
        if reasoning_effort is not None:
            if reasoning_effort == "minimal":
                reasoning_effort = 0
            elif isinstance(reasoning_effort, str):
                reasoning_effort = 0  # Fallback for unsupported string values

            if isinstance(reasoning_effort, int) and reasoning_effort >= -1:  # type: ignore[misc]
                config.thinking_config = google.genai.types.ThinkingConfig(
                    thinking_budget=reasoning_effort
                )

        # Configure for structured output
        config.response_schema = response_format.model_json_schema()
        config.response_mime_type = "application/json"

        # Add system instruction if we have system messages
        if system_prompt:
            config.system_instruction = system_prompt

        args: dict[str, Any] = {
            "model": model,
            "contents": contents,
            "config": config,
        }

        # Make 3 attempts while recovering from errors
        for attempt in range(3):
            try:
                # Add any additional kwargs
                args.update(kwargs)

                response = await self.client.aio.models.generate_content(**args)

                token_count = 0
                if (
                    response.usage_metadata
                    and response.usage_metadata.total_token_count
                ):
                    token_count = response.usage_metadata.total_token_count

                usage = Usage(
                    token_count=token_count,
                    provider="gemini",
                    model=model,
                )

                # Parse structured response
                if response.parsed:
                    return response_format.model_validate(response.parsed), usage
                elif response.text:
                    try:
                        return response_format.model_validate_json(response.text), usage
                    except (json.JSONDecodeError, pydantic.ValidationError) as e:
                        import traceback

                        tb_str = traceback.format_exc()
                        error_message = (
                            f"Error parsing response: {e}\nTraceback:\n{tb_str}"
                        )
                        exceptions.append(error_message)

                        # Add error context to conversation for retry
                        if attempt < 2:
                            contents.append(
                                google.genai.types.Content(
                                    role="user",
                                    parts=[google.genai.types.Part(text=error_message)],
                                )
                            )
                        continue
                else:
                    error_message = f"No response content available from Gemini on attempt {attempt + 1}"
                    exceptions.append(error_message)

                    # Add context for retry
                    if attempt < 2:
                        contents.append(
                            google.genai.types.Content(
                                role="user",
                                parts=[
                                    google.genai.types.Part(
                                        text="No response received. Please provide a JSON response that matches the required schema."
                                    )
                                ],
                            )
                        )
                    continue

            except Exception as e:
                error_message = (
                    f"Gemini API call failed on attempt {attempt + 1}: {str(e)}"
                )
                exceptions.append(error_message)

                # Add error context to conversation for retry
                if attempt < 2:
                    contents.append(
                        google.genai.types.Content(
                            role="user",
                            parts=[
                                google.genai.types.Part(
                                    text=f"Error: {str(e)}. Please try again with a valid JSON response."
                                )
                            ],
                        )
                    )
                continue

        raise Exception(
            "Failed to _generate_struct: Exceeded maximum retries. Inner exceptions: "
            + " -> ".join(exceptions)
        )

    def _convert_messages(
        self, messages: Sequence[AllowedChatCompletionMessageParams]
    ) -> tuple[list[google.genai.types.Content], str | None]:
        """Convert OpenAI messages to Gemini Content format.

        OpenAI形式のメッセージをGemini形式に変換
        ========================================

        OpenAI APIとGemini APIの主な違いを吸収します：
        1. Systemメッセージは会話履歴から分離され、system_promptとして返される
        2. "assistant"ロールは"model"ロールに変換される
        3. メッセージはContent/Partオブジェクトに変換される
        4. マルチパートコンテンツをテキストのみ抽出して結合

        Args:
            messages: OpenAI形式のメッセージシーケンス

        Returns:
            tuple[list[Content], str | None]:
                - contents: Gemini形式のContentオブジェクトリスト（systemメッセージを除く）
                - system_prompt: 連結されたsystemメッセージ、または存在しない場合はNone

        Note:
            Gemini APIでは、systemメッセージは会話履歴の一部ではなく、
            別個の system_instruction パラメータとして API に渡されます。

        """
        gemini_contents: list[google.genai.types.Content] = []
        system_messages: list[str] = []

        for message in messages:
            role = message.get("role", "user")
            content = message.get("content", "")

            if not content:
                continue

            # 異なるコンテンツタイプの処理
            if isinstance(content, str):
                text_content = content
            elif isinstance(content, list):
                # マルチパートコンテンツの処理
                text_parts: list[str] = []
                for part in content:
                    if part["type"] == "text":
                        text_parts.append(part.get("text", ""))
                text_content = "\n".join(text_parts)
            else:
                text_content = str(content)

            if not text_content:
                continue

            # ロールの変換: OpenAI "assistant" -> Gemini "model", "user" はそのまま
            # Systemメッセージは別に収集
            if role == "system":
                system_messages.append(text_content)
            elif role == "assistant":
                # AssistantロールはGeminiの"model"ロールにマッピング
                gemini_contents.append(
                    google.genai.types.Content(
                        role="model", parts=[google.genai.types.Part(text=text_content)]
                    )
                )
            elif role == "user":
                gemini_contents.append(
                    google.genai.types.Content(
                        role="user", parts=[google.genai.types.Part(text=text_content)]
                    )
                )

        # Systemメッセージを連結、または存在しない場合はNoneを返す
        system_prompt = "\n\n".join(system_messages) if system_messages else None

        return gemini_contents, system_prompt
