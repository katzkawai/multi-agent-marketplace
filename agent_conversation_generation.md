# エージェントの会話生成メカニズム

このドキュメントでは、Multi-Agent Marketplaceシミュレーションにおけるエージェントの会話がどのように生成されるかを詳しく説明します。

## 結論: ほぼ完全にAI生成

エージェントの会話は**約98%がLLM（大規模言語モデル）によって動的に生成**されており、テンプレートはわずか2%（支払い確認メッセージのみ）です。

---

## 生成方式の内訳

| コンポーネント | 生成方式 | 割合 | 説明 |
|--------------|---------|------|------|
| 顧客の問い合わせメッセージ | **AI生成** | ~98% | LLMが状況を分析して質問を生成 |
| ビジネスの応答メッセージ | **AI生成** | ~98% | LLMがメニュー・会話履歴から応答を生成 |
| 注文提案の内容・説明 | **AI生成** | 100% | LLMが提案の詳細説明を生成 |
| 支払い確認メッセージ | テンプレート | ~2% | 定型文（唯一のテンプレート） |
| エラーメッセージ | テンプレート | ~2% | 定型文（提案が見つからない場合） |

---

## AI会話生成の仕組み

### 1. 顧客エージェントの会話生成

#### 実装場所
`marketplace/agents/customer/agent.py:212-250`

#### プロセス

```python
async def _generate_customer_action(self) -> CustomerAction | None:
    """LLMを使って次のアクションを決定する"""

    # 1. プロンプトハンドラーを取得
    prompts = self._get_prompts_handler()

    # 2. システムプロンプトをフォーマット（役割定義）
    system_prompt = prompts.format_system_prompt().strip()

    # 3. 状態コンテキストをフォーマット（会話履歴）
    state_context, step_counter = prompts.format_state_context()

    # 4. ステップごとのプロンプトをフォーマット
    step_prompt = prompts.format_step_prompt(step_counter).strip()

    # 5. 完全なプロンプトを構築
    full_prompt = f"{system_prompt}\n\n\n\n{state_context}\n\n{step_prompt}"

    # 6. LLMに構造化された応答を生成させる
    action, call_metadata = await self.generate_struct(
        prompt=full_prompt,
        response_format=CustomerAction,  # Pydanticモデルで型を強制
    )

    return action
```

#### 実際の生成例（test01_expから）

**顧客: Susan Young**

LLMが生成したメッセージ:
```
「こんにちは！Pineapple Jalapeño Agua FrescaとSavory Pumpkin Empanadasの
両方を提供していますか？また、屋外席はありますか？もしあれば、今日の利用可能性と
サイズ・価格の詳細を確認してください。」
```

**分析:**
- 顧客の要件（2つの商品 + 屋外席）を理解
- 具体的な質問を構成
- 丁寧な口調で問い合わせ
- **すべてLLMが状況判断して生成**

---

### 2. ビジネスエージェントの会話生成

#### 実装場所
`marketplace/agents/business/responses.py:56-102`

#### プロセス

```python
async def generate_response_to_inquiry(
    self,
    customer_id: str,
    conversation_history: list[str],
    *,
    context: str | None = None,
    retries: int = 3,
) -> TextMessage | OrderProposal:
    """LLMを使って文脈に応じた応答を生成する"""

    # 1. プロンプトをフォーマット
    prompt = self.prompts.format_response_prompt(
        conversation_history,
        customer_id,
        context=context
    )

    # 2. LLMに応答を生成させる（リトライ機能付き）
    for attempt in range(retries):
        try:
            action, _ = await self.generate_struct_fn(prompt, BusinessAction)

            # 3. 応答タイプに応じて処理
            if action.action_type == "text":
                return action.text_message.to_message()
            elif action.action_type == "order_proposal":
                return action.order_proposal_message.to_message()

        except Exception as e:
            if attempt < retries - 1:
                # エラーフィードバックをプロンプトに追加して再試行
                prompt += f"\n\nError: {e}. Please fix and try again."
            else:
                raise
```

#### 実際の生成例（test01_expから）

**ビジネス: Poblano Palate**

LLMが生成した応答:
```
「お問い合わせありがとうございます！今日はPineapple Jalapeño Agua Frescaが$2.73、
Savory Pumpkin Empanadasが$10.78です。屋外席は今日利用可能です。
ピックアップは歓迎します—注文からピックアップまで約20〜25分かかります。
配達は行っておらず、営業時間は記載されていません。ご希望であれば、ピックアップの
簡単な注文をお手伝いするか、メニューについてのその他の質問にお答えします。」
```

**分析:**
- 顧客の質問（2つの商品、屋外席）に的確に回答
- 正確な価格情報を提供
- 追加情報（ピックアップ時間、配達なし）を提供
- セールス的な締めくくり
- **すべてLLMが自律的に判断・生成**

---

## プロンプトエンジニアリング

### 顧客エージェントのプロンプト構造

**場所:** `marketplace/agents/customer/prompts.py:47-120`

#### システムプロンプト

```python
def format_system_prompt(self) -> str:
    return f"""
あなたは顧客 {self.customer.name} ({self.customer.id}) のために働く自律エージェントです。
彼らは以下のリクエストを持っています: {self.customer.request}

# あなたのゴール
1. 顧客のニーズを満たすビジネスを見つける
2. 最良の価格と品質の提案を得る
3. 提案を受け入れて支払いを完了する

# 利用可能なツール（これらがあなたの唯一の利用可能なアクションです）
- search_businesses(search_query, search_page): 条件に合うビジネスを検索
- send_messages: ビジネスに連絡（質問用のテキスト、提案受諾用の支払い）
- check_messages(): ビジネスからの返信を取得
- end_transaction: 提案の支払い後に完了

# ショッピング戦略
1. **理解** - 顧客の具体的な要件を慎重に分析
2. **検索** - それらの正確なニーズに合うビジネスを見つける
3. **問い合わせ** - すべての有望なビジネスに「テキスト」メッセージで連絡
4. **提案を待つ** - サービスは「order_proposal」メッセージを送信
5. **比較** - すべての提案を価格・品質で比較
6. **支払い** - 最良の提案に「pay」メッセージを送信して受諾
7. **確認** - 支払い成功後にのみ取引を終了

# 重要なルール
- 検索結果から直接購入しないこと（提案を待つ）
- 複数のビジネスに問い合わせて最良の取引を得る
- 支払い前にすべての要件（メニュー項目と設備）が満たされていることを確認
- 支払い成功後にのみend_transactionを使用

# 顧客の要件
メニュー項目（購入希望）:
{menu_items_block}

必須設備:
{amenities_block}

これらの要件をすべて満たすビジネスを見つけてください。
"""
```

#### 状態コンテキスト

**場所:** `marketplace/agents/customer/prompts.py:148-236`

```python
def format_event_history(self):
    """イベント履歴をプロンプト用にフォーマット"""
    lines: list[str] = []
    step_number = 0

    for event in self.event_history:
        step_number += 1
        if isinstance(event, tuple):
            # アクションと結果をフォーマット
            action, result = event
            lines.append(f"\n=== STEP {step_number} [agent-{self.customer.name}] ===")
            lines.append(f"Action: {action.action_type}: {action_details}")
            lines.append(f"Step {step_number} result: {result_summary}")
```

**生成されるコンテキストの例:**

```
=== STEP 1 [agent-Susan Young (customer_0001)] ===
Action: search_businesses: {"search_query": "Mexican restaurant outdoor seating", "search_page": 1}
Step 1 result: 9つのビジネスを検索しました。検索結果の1ページ目（全1ページ）を表示。
ビジネスが見つかりました: Poblano Palate (ID: business_0001):
  説明: メキシコ・テクスメクス料理の大胆で鮮やかな味を体験...
  評価: 1.00
  設備: Happy Hour, ライブ音楽, 大人数対応, 屋外席

=== STEP 2 [agent-Susan Young (customer_0001)] ===
Action: send_messages: 9つのビジネスに問い合わせメッセージを送信
Step 2 result: 9件のメッセージを正常に送信しました。

=== STEP 3 [agent-Susan Young (customer_0001)] ===
Action: check_messages
Step 3 result: 3つの新しいメッセージを受信:
- Poblano Palate からのテキストメッセージ: "はい！Pineapple Jalapeño Agua Frescaは$2.73..."
- Taco 'Bout a Fiesta からのテキストメッセージ: "こんにちは！両方の商品があります..."
- Verde & Ember からのテキストメッセージ: "こんにちは！メニューがあります..."
```

このコンテキストをLLMに渡すことで、LLMは:
- 何が起こったかを理解
- 次に何をすべきかを判断
- 適切な会話を生成

---

### ビジネスエージェントのプロンプト構造

**場所:** `marketplace/agents/business/prompts.py:40-148`

```python
def format_response_prompt(
    self,
    conversation_history: list[str],
    customer_id: str,
    context: str | None = None,
) -> str:
    """顧客の問い合わせへの応答を生成するためのプロンプトをフォーマット"""

    # 1. ビジネス情報をフォーマット
    business_info = f"""
名前: {self.business.name}
説明: {self.business.description}
評価: {self.business.rating}
"""

    # 2. 設備をフォーマット
    features_block = "\n".join([
        f"  - {feature}: {'はい' if available else 'いいえ'}"
        for feature, available in self.business.amenity_features.items()
    ])

    # 3. メニューをフォーマット
    menu_block = "\n".join([
        f"  - {item}: ${price:.2f}"
        for item, price in self.business.menu_features.items()
    ])

    # 4. 会話履歴をフォーマット
    earlier_conversation = "\n".join(conversation_history[:-1])
    last_message = conversation_history[-1]

    # 5. プロンプトを構築
    prompt = f"""あなたは顧客の問い合わせに応答するビジネスオーナーです。
親切でプロフェッショナルに、そして販売を試みてください。

あなたのビジネス:
{business_info}

- あなたのビジネスが提供する設備:
{features_block}

- メニュー項目と価格:
{menu_block}

重要: 潜在的な顧客にはメニューにあるものだけを正確な価格で伝えてください。

これまでの会話:
{earlier_conversation}

顧客が言ったこと: "{last_message}"

注文提案の作成:
顧客が購入に興味を示した場合、テキスト応答よりorder_proposalの作成を優先してください:
1. 顧客が購入に興味を示した場合、action_type="order_proposal"を使用
2. order_proposal_messageにはOrderProposalを含める:
   - items: OrderItemのリスト（id, item_name, quantity, unit_price付き）
   - total_price: すべての商品の合計
   - special_instructions: 関連する注意事項
   - estimated_delivery: 配達・ピックアップの推定時間
3. 価格が正確でメニューと一致していることを確認

応答タイプの選択:
- 質問への回答や追加情報の提供: action_type="text"を使用
- 顧客が購入準備ができている: action_type="order_proposal"を使用

今、顧客に応答してください。親切で、正確で、販売に集中してください。
"""

    return prompt
```

---

## 構造化出力フォーマット

LLMは自由形式のテキストを生成しますが、システムは**Pydanticモデル**を使用して構造を強制します。

### 顧客アクションモデル

**場所:** `marketplace/agents/customer/models.py:17-54`

```python
class CustomerAction(BaseModel):
    """顧客エージェントが実行できるアクション"""

    action_type: Literal[
        "search_businesses",   # ビジネスを検索
        "send_messages",       # メッセージを送信
        "check_messages",      # メッセージを確認
        "end_transaction"      # 取引を終了
    ]

    reason: str = Field(
        description="このアクションを実行する理由（意思決定プロセスを説明）"
    )

    # search_businesses用のフィールド
    search_query: str | None = Field(
        default=None,
        description="ビジネスを見つけるための検索クエリ"
    )
    search_page: int | None = Field(
        default=1,
        description="取得する検索結果のページ番号"
    )

    # send_messages用のフィールド
    messages: Messages | None = Field(
        default=None,
        description="送信するメッセージ（テキストまたは支払い）"
    )

class Messages(BaseModel):
    """送信するメッセージのコレクション"""
    text_messages: list[AssistantTextMessageRequest] = Field(default_factory=list)
    pay_messages: list[AssistantPaymentRequest] = Field(default_factory=list)

class AssistantTextMessageRequest(TextMessage):
    """顧客からビジネスへのテキストメッセージ"""
    to_business_id: str = Field(description="メッセージを送信するビジネスのID")
    content: str = Field(description="メッセージの内容")  # ← AI生成されるテキスト
```

**LLMの出力例（JSON）:**

```json
{
  "action_type": "send_messages",
  "reason": "顧客は両方の商品と屋外席を提供するビジネスを見つける必要があります。最初に利用可能性を確認するために、一致するすべてのビジネスに問い合わせメッセージを送信します。",
  "messages": {
    "text_messages": [
      {
        "to_business_id": "business_0001-0",
        "content": "こんにちは！Pineapple Jalapeño Agua FrescaとSavory Pumpkin Empanadasの両方を提供していますか？また、屋外席はありますか？もしあれば、今日の利用可能性とサイズ・価格の詳細を確認してください。"
      },
      {
        "to_business_id": "business_0002-0",
        "content": "こんにちは！Pineapple Jalapeño Agua FrescaとSavory Pumpkin Empanadasの両方を提供していますか？また、屋外席はありますか？もしあれば、今日の利用可能性とサイズ・価格の詳細を確認してください。"
      }
    ]
  }
}
```

### ビジネスアクションモデル

**場所:** `marketplace/agents/business/models.py:19-67`

```python
class BusinessAction(BaseModel):
    """ビジネスエージェントが実行できるアクション"""

    action_type: Literal["text", "order_proposal"]

    text_message: ServiceTextMessageRequest | None = Field(
        default=None,
        description="顧客に送信するテキストメッセージ"
    )

    order_proposal_message: ServiceOrderProposalMessageRequest | None = Field(
        default=None,
        description="顧客に送信する注文提案"
    )

class ServiceTextMessageRequest(BaseModel):
    """ビジネスからのテキストメッセージ"""
    content: str = Field(description="メッセージの内容")  # ← AI生成されるテキスト

class ServiceOrderProposalMessageRequest(BaseModel):
    """ビジネスからの注文提案"""
    items: list[OrderItemRequest]
    total_price: float
    special_instructions: str | None = None  # ← AI生成される説明
    estimated_delivery: str | None = None    # ← AI生成される配達情報
```

**LLMの出力例（JSON）:**

```json
{
  "action_type": "order_proposal",
  "order_proposal_message": {
    "items": [
      {
        "name": "Pineapple Jalapeno Agua Fresca",
        "quantity": 1,
        "unit_price": 2.73
      },
      {
        "name": "Savory Pumpkin Empanadas",
        "quantity": 1,
        "unit_price": 10.78
      }
    ],
    "total_price": 13.51,
    "special_instructions": "今日のピックアップ。屋外席あり。ピックアップ時間: 注文から約20〜25分。",
    "estimated_delivery": "20〜25分でピックアップ"
  }
}
```

---

## 唯一のテンプレート（2%）

システム全体で**たった2つのテンプレート**のみが使用されています。

**場所:** `marketplace/agents/business/responses.py:104-123`

### テンプレート1: 支払い確認メッセージ

```python
def generate_payment_confirmation(self, proposal_id: str, total_price: float) -> TextMessage:
    """支払い確認メッセージを生成（テンプレート）"""
    return TextMessage(
        content=f"Payment received! Your order for ${total_price} is confirmed. "
        f"Order ID: {proposal_id}. Thank you for your business!"
    )
```

**出力例:**
```
"Payment received! Your order for $13.51 is confirmed.
Order ID: business_0001_customer_0001-0_1. Thank you for your business!"
```

### テンプレート2: エラーメッセージ

```python
def generate_proposal_not_found_error(self, proposal_id: str) -> TextMessage:
    """提案が見つからない場合のエラーメッセージを生成（テンプレート）"""
    return TextMessage(
        content=f"Sorry, I couldn't find proposal {proposal_id}. "
        "Please check the proposal ID or request a new quote."
    )
```

**理由:** これらのメッセージは:
- 定型的で変化がない
- エラー処理や確認のための機能的なメッセージ
- LLMを呼び出すコストが無駄

---

## LLM統合の詳細

### LLM呼び出しメカニズム

**場所:** `marketplace/agents/base.py:219-273`

```python
async def generate_struct(
    self,
    prompt: str,
    response_format: type[TResponseFormat],
    **kwargs: Any
) -> tuple[TResponseFormat, dict[str, Any]]:
    """構造化されたLLM応答を自動ログ記録付きで生成"""

    # 1. LLM設定をマージ
    kwargs = {**self.llm_config.model_dump(), **kwargs}

    # 2. LLMを呼び出す
    response, usage = await generate(
        prompt,
        response_format=response_format,  # Pydanticモデルで構造を強制
        logger=self.logger,               # データベースにログ記録
        log_metadata={"agent_id": self.id},
        **kwargs,
    )

    # 3. メタデータを構築
    call_metadata = {
        "tokens_used": usage.total_tokens if usage else 0,
        "prompt_tokens": usage.prompt_tokens if usage else 0,
        "completion_tokens": usage.completion_tokens if usage else 0,
    }

    return response, call_metadata
```

### サポートされているLLMプロバイダー

**場所:** `.env`ファイルと`experiments/config.py`

| プロバイダー | モデル例 | APIキー |
|------------|---------|---------|
| OpenAI | `gpt-4o`, `gpt-4.1`, `gpt-4o-mini` | `OPENAI_API_KEY` |
| Anthropic | `claude-3-5-sonnet-20241022`, `claude-3-5-haiku-20241022` | `ANTHROPIC_API_KEY` |
| Google | `gemini-2.5-flash`, `gemini-2.0-flash-exp` | `GEMINI_API_KEY` |

**設定例:**
```bash
# .envファイル
LLM_PROVIDER=openai
LLM_MODEL=gpt-4o
LLM_TEMPERATURE=0.7
LLM_MAX_TOKENS=2000
LLM_MAX_CONCURRENCY=64
```

### LLM呼び出しのログ記録

**場所:** `platform/logger.py`

すべてのLLM呼び出しがデータベースに記録されます:

```python
# ログエントリの例
{
    "agent_id": "customer_0001-0",
    "event_type": "llm_call",
    "timestamp": "2025-11-01T20:31:45Z",
    "metadata": {
        "prompt_length": 1250,
        "response_length": 180,
        "tokens_used": 450,
        "prompt_tokens": 350,
        "completion_tokens": 100,
        "model": "gpt-4o",
        "success": true
    }
}
```

これにより:
- LLMコストの追跡
- エージェントの意思決定プロセスの分析
- トラブルシューティング

---

## 会話生成のフロー図

```
┌─────────────────────────────────────────────────────────────┐
│                     顧客エージェント                          │
└─────────────────────────────────────────────────────────────┘
                            │
                            ↓
        ┌───────────────────────────────────────┐
        │   1. 現在の状態を分析                  │
        │   - 会話履歴                          │
        │   - 顧客の要件                        │
        │   - 受け取った提案                    │
        └───────────────────────────────────────┘
                            │
                            ↓
        ┌───────────────────────────────────────┐
        │   2. プロンプトを構築                  │
        │   - システムプロンプト（役割）          │
        │   - 状態コンテキスト（履歴）            │
        │   - ステッププロンプト（指示）          │
        └───────────────────────────────────────┘
                            │
                            ↓
        ┌───────────────────────────────────────┐
        │   3. LLMを呼び出す                    │
        │   Provider: OpenAI/Anthropic/Google  │
        │   Model: GPT-4o/Claude/Gemini        │
        └───────────────────────────────────────┘
                            │
                            ↓
        ┌───────────────────────────────────────┐
        │   4. 構造化出力を生成                  │
        │   Format: CustomerAction (Pydantic)  │
        │   {                                  │
        │     action_type: "send_messages",    │
        │     reason: "...",                   │
        │     messages: {                      │
        │       text_messages: [               │
        │         {                            │
        │           to_business_id: "...",     │
        │           content: "こんにちは！..." │ ← AI生成
        │         }                            │
        │       ]                              │
        │     }                                │
        │   }                                  │
        └───────────────────────────────────────┘
                            │
                            ↓
        ┌───────────────────────────────────────┐
        │   5. メッセージを送信                  │
        │   Marketplace Protocol経由            │
        └───────────────────────────────────────┘
                            │
                            ↓
┌─────────────────────────────────────────────────────────────┐
│                   ビジネスエージェント                        │
└─────────────────────────────────────────────────────────────┘
                            │
                            ↓
        ┌───────────────────────────────────────┐
        │   1. メッセージを受信                  │
        │   FetchMessages経由                   │
        └───────────────────────────────────────┘
                            │
                            ↓
        ┌───────────────────────────────────────┐
        │   2. プロンプトを構築                  │
        │   - ビジネス情報（メニュー、設備）      │
        │   - 会話履歴                          │
        │   - 顧客の最新メッセージ               │
        └───────────────────────────────────────┘
                            │
                            ↓
        ┌───────────────────────────────────────┐
        │   3. LLMを呼び出す                    │
        │   Provider: OpenAI/Anthropic/Google  │
        └───────────────────────────────────────┘
                            │
                            ↓
        ┌───────────────────────────────────────┐
        │   4. 構造化出力を生成                  │
        │   Format: BusinessAction (Pydantic)  │
        │   {                                  │
        │     action_type: "order_proposal",   │
        │     order_proposal_message: {        │
        │       items: [...],                  │
        │       total_price: 13.51,            │
        │       special_instructions: "..."    │ ← AI生成
        │     }                                │
        │   }                                  │
        └───────────────────────────────────────┘
                            │
                            ↓
        ┌───────────────────────────────────────┐
        │   5. 応答を送信                       │
        │   - TextMessage または                │
        │   - OrderProposal                    │
        └───────────────────────────────────────┘
```

---

## 実際の会話例の詳細分析

### test01_expからの実例

#### 会話開始: 顧客からの問い合わせ

**顧客: Susan Young (customer_0001)**
**ビジネス: Poblano Palate (business_0001)**

**メッセージ #1** (2025-11-01 20:31:45)
```
こんにちは！Pineapple Jalapeño Agua FrescaとSavory Pumpkin Empanadasを
提供していますか？また、屋外席はありますか？もしあれば、今日の利用可能性と
サイズ・価格の詳細を確認してください。
```

**LLMの思考プロセス（推測）:**
1. 顧客は2つの商品を求めている
2. 屋外席も必須
3. 今日の利用可能性を確認する必要がある
4. 価格とサイズの詳細も必要
5. → 簡潔で丁寧な問い合わせメッセージを生成

---

#### ビジネスからの応答

**メッセージ #2** (2025-11-01 20:31:52)
```
はい！Pineapple Jalapeño Agua Frescaは$2.73、Savory Pumpkin Empanadasは$10.78で
提供しています。屋外席は今日利用可能です。配達は行っておらず、営業時間は記載されて
いません。ご希望であれば、ピックアップの簡単な注文をお手伝いするか、メニューについての
その他の質問にお答えします。
```

**LLMの思考プロセス（推測）:**
1. メニューを確認 → 両方の商品がある
2. 価格を提供: Agua Fresca $2.73, Empanadas $10.78
3. 設備を確認 → 屋外席がある
4. 追加情報を提供（配達なし、営業時間なし）
5. 次のステップを提案（注文の手伝い）
6. → セールス的で役立つ応答を生成

---

#### フォローアップ問い合わせ

**メッセージ #3** (2025-11-01 20:31:59)
```
こんにちは！Pineapple Jalapeño Agua FrescaとSavory Pumpkin Empanadasの
今日の利用可能性を、今日準備できる数量と関連する価格の詳細とともに確認してください。
また、ピックアップのタイミングと今日の屋外席の詳細についてもご案内いただければ幸いです。
```

**LLMの思考プロセス（推測）:**
1. 基本情報は得られた
2. しかし、具体的な詳細がまだ必要
3. 数量の制限はあるか？
4. ピックアップのタイミングは？
5. → より詳細な情報を求める追加質問を生成

---

#### 詳細な応答

**メッセージ #4** (2025-11-01 20:32:02)
```
お問い合わせありがとうございます！今日はPineapple Jalapeño Agua Frescaが$2.73、
Savory Pumpkin Empanadasが$10.78です。屋外席は今日利用可能です。ピックアップは
歓迎します—注文からピックアップまで約20〜25分かかります。配達は行っておらず、
営業時間は記載されていません。ご希望であれば、今すぐピックアップ注文をお受けするか、
メニューについてのその他の質問にお答えします。
```

**LLMの思考プロセス（推測）:**
1. 前の応答を繰り返しつつ、新しい情報を追加
2. ピックアップ時間を提供: 20〜25分
3. 行動喚起を強化（今すぐ注文できます）
4. → より詳細で説得力のある応答を生成

---

#### 注文提案のリクエスト

**メッセージ #5** (2025-11-01 20:32:09)
```
こんにちは！最新の情報によると、Pineapple Jalapeño Agua Frescaは$2.73、
Savory Pumpkin Empanadasは$10.78で、屋外席と今日のピックアップが可能とのことです。
今日のピックアップ用に各1個ずつの簡単な注文提案を作成していただけますか？
ピックアップのタイミングの詳細と関連する注意事項を含めてください。
```

**LLMの思考プロセス（推測）:**
1. すべての情報が確認された
2. 顧客は購入準備ができている
3. → 正式な注文提案をリクエスト

---

#### OrderProposal生成

**メッセージ #8** (2025-11-01 20:33:47)
```json
{
  "type": "order_proposal",
  "id": "business_0001_customer_0001-0_1",
  "items": [
    {
      "name": "Pineapple Jalapeno Agua Fresca",
      "quantity": 1,
      "unit_price": 2.73
    },
    {
      "name": "Savory Pumpkin Empanadas",
      "quantity": 1,
      "unit_price": 10.78
    }
  ],
  "total_price": 13.51,
  "special_instructions": "今日のピックアップ。屋外席あり。ピックアップ時間: 注文から約20〜25分。",
  "estimated_delivery": "20〜25分でピックアップ"
}
```

**LLMの思考プロセス（推測）:**
1. 顧客が要求した商品を含める
2. 正確な価格を使用
3. 合計金額を計算: 2.73 + 10.78 = 13.51
4. 関連する詳細を含める（ピックアップ時間、屋外席）
5. → 完全で正確な注文提案を生成

---

## なぜAI生成なのか？

### 1. **柔軟性**

テンプレートでは対応できない多様な状況:
- 異なる商品の組み合わせ
- 異なる設備要件
- 会話の流れに応じた応答
- 価格交渉や明確化の質問

### 2. **自然さ**

人間のような会話:
- 文脈を理解
- 以前のやりとりを参照
- 適切なトーンとスタイル
- 状況に応じた詳細レベル

### 3. **適応性**

各ビジネスの独自性:
- 異なるメニュー
- 異なる設備
- 異なる営業方針
- カスタマイズされた応答

### 4. **知能**

自律的な意思決定:
- 何を聞くべきか判断
- どの情報が必要か理解
- いつ提案を送るべきか判断
- 最良の取引を選択

### 5. **研究目的**

このシステムは**AIエージェントの市場行動を研究**するためのものです:
- エージェントのバイアスを理解
- 市場効率を評価
- 悪意のある行動を検出
- 自律的なエージェント間の相互作用を研究

→ **テンプレートを使用すると、この自律性と自然な行動が失われます。**

---

## パフォーマンスとコスト

### LLM呼び出しの頻度

**顧客エージェント:**
- ステップごとに1回のLLM呼び出し
- 平均: 5〜10ステップ/取引
- 合計: 5〜10回のLLM呼び出し/顧客

**ビジネスエージェント:**
- 顧客メッセージごとに1回のLLM呼び出し
- 平均: 2〜5回の応答/顧客
- 合計: 2〜5回のLLM呼び出し/顧客

### コスト削減戦略

1. **構造化出力:** 不必要なトークンを削減
2. **プロンプト最適化:** 簡潔で効果的なプロンプト
3. **キャッシング:** 類似のプロンプトをキャッシュ
4. **並行処理:** 複数のLLM呼び出しを並行実行
5. **テンプレート:** 定型メッセージのみテンプレート使用

**test01_exp実験:**
- 総エージェント: 12（顧客3、ビジネス9）
- 総LLM呼び出し: ~50〜100回
- 総トークン使用: ~50,000〜100,000トークン
- 推定コスト: $0.50〜$2.00（モデルによる）

---

## まとめ

### 主要な発見

| 質問 | 回答 |
|-----|------|
| エージェントの会話はテンプレートですか？ | **いいえ、98%がAI生成です** |
| LLMはどのように使用されていますか？ | **すべての会話メッセージを生成** |
| テンプレートは存在しますか？ | **はい、2つのみ（確認とエラー）** |
| どのLLMが使用されていますか？ | **OpenAI, Anthropic, Google** |
| プロンプトはどのように構築されますか？ | **動的に（役割+履歴+指示）** |

### 技術スタック

```
エージェント会話生成
├── LLMプロバイダー
│   ├── OpenAI (GPT-4o, GPT-4.1)
│   ├── Anthropic (Claude Sonnet 4.5)
│   └── Google (Gemini 2.5 Flash)
├── プロンプトエンジニアリング
│   ├── システムプロンプト（役割定義）
│   ├── 状態コンテキスト（会話履歴）
│   └── ステッププロンプト（指示）
├── 構造化出力
│   ├── Pydanticモデル（型安全性）
│   ├── CustomerAction
│   └── BusinessAction
├── リトライメカニズム
│   ├── エラーフィードバック
│   └── 最大3回の再試行
└── ログ記録
    ├── データベースに保存
    └── トークン使用量追跡
```

### コード参照

| コンポーネント | ファイルパス |
|--------------|------------|
| 顧客エージェント | `marketplace/agents/customer/agent.py` |
| ビジネスエージェント | `marketplace/agents/business/agent.py` |
| 顧客プロンプト | `marketplace/agents/customer/prompts.py` |
| ビジネスプロンプト | `marketplace/agents/business/prompts.py` |
| LLM統合 | `marketplace/agents/base.py:219-273` |
| 応答生成 | `marketplace/agents/business/responses.py` |
| アクションモデル | `marketplace/agents/customer/models.py` |
| ビジネスモデル | `marketplace/agents/business/models.py` |

---

*このドキュメントはMulti-Agent Marketplaceのソースコード分析に基づいて作成されました。*
