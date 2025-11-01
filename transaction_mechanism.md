# 取引成立メカニズム

このドキュメントでは、Multi-Agent Marketplaceシミュレーションにおける取引成立の仕組みを詳細に説明します。

## 概要

取引は**非同期メッセージング**によって完全に実現されており、顧客エージェントとビジネスエージェント間でメッセージを交換することで成立します。マーケットプレイスプロトコルがすべてのステップを検証し、データベースに記録します。

---

## 取引フロー

### 1. 提案フェーズ（Proposal Phase）

#### ビジネスが注文提案を送信

**場所:** `marketplace/actions/messaging.py`

ビジネスエージェントは`OrderProposal`メッセージを顧客に送信します。

```python
class OrderProposal(BaseModel):
    type: Literal["order_proposal"] = "order_proposal"
    id: str                           # ユニークな提案ID
    items: list[OrderItem]            # 商品リスト（数量と単価）
    total_price: float                # 合計金額
    special_instructions: str | None  # 特別指示
    estimated_delivery: str | None    # 配達予定時刻
    expiry_time: str | None          # 有効期限
```

**OrderItem構造:**
```python
class OrderItem(BaseModel):
    name: str           # 商品名
    quantity: int       # 数量
    unit_price: float   # 単価
```

#### 提案送信の実装

**場所:** `marketplace/agents/business/agent.py:158-179`

ビジネスエージェントは以下のプロセスで提案を作成・送信します:

1. 顧客のリクエストをLLMで解析
2. メニューから適切な商品を選択
3. `OrderProposal`オブジェクトを生成
4. `send_message()`メソッドで顧客に送信
5. ローカルストレージに提案を保存（ステータス: "pending"）

---

### 2. 受信・保存フェーズ（Reception & Storage Phase）

#### 顧客が提案を受信

**場所:** `marketplace/agents/customer/agent.py:136-149`

顧客エージェントがメッセージをフェッチすると:

1. `FetchMessages`アクションを実行
2. 受信したメッセージを解析
3. `OrderProposal`タイプのメッセージを検出
4. `self.proposal_storage`に保存

**ProposalStorage構造:**
```python
{
    "proposal_id": {
        "proposal": OrderProposal,      # 提案オブジェクト
        "business_id": str,              # ビジネスID
        "customer_id": str,              # 顧客ID
        "created_at": datetime,          # 受信時刻
        "status": "pending"              # ステータス
    }
}
```

---

### 3. 評価・決定フェーズ（Evaluation & Decision Phase）

#### 顧客が提案を評価

**場所:** `marketplace/agents/customer/agent.py:272-335`

顧客エージェントは以下のプロセスで提案を評価します:

1. **LLMによる評価**
   - すべての保留中の提案を確認
   - 顧客の要件（メニュー、設備、価格）と照合
   - 最適な提案を選択

2. **条件チェック**
   - 要求したすべての商品が含まれているか
   - 必須設備が満たされているか
   - 価格が支払意欲の範囲内か

3. **決定**
   - 受諾: `Payment`メッセージを作成
   - 拒否: 他の提案を検討または新しい問い合わせ

---

### 4. 支払いフェーズ（Payment Phase）

#### 顧客が支払いメッセージを送信

**場所:** `marketplace/actions/messaging.py`

```python
class Payment(BaseModel):
    type: Literal["payment"] = "payment"
    proposal_message_id: str          # 参照する提案ID
    payment_method: str | None        # 支払い方法（例: "credit_card"）
    payment_message: str | None       # 支払いメッセージ
```

**送信プロセス:** `marketplace/agents/customer/agent.py:278-292`

```python
# Paymentメッセージ作成
payment = Payment(
    proposal_message_id=proposal_to_accept,
    payment_method="credit_card",
    payment_message="Accepting your proposal..."
)

# ビジネスに送信
result = await self.send_message(business_id, payment)
```

---

### 5. 検証フェーズ（Validation Phase）

#### マーケットプレイスプロトコルが検証

**場所:** `marketplace/protocol/send_message.py:76-127`

支払いメッセージが送信されると、プロトコルは以下を検証します:

1. **提案の存在確認**
   ```python
   # データベースから提案を検索
   proposal = await db.query_actions(
       action_type="SendMessage",
       filters={"proposal_id": payment.proposal_message_id}
   )
   ```

2. **送信者の検証**
   - 提案が正しいビジネスからのものか確認
   - `from_agent_id`が支払い先の`business_id`と一致するか

3. **有効期限の確認**
   ```python
   if proposal.expiry_time:
       if datetime.now() > proposal.expiry_time:
           return Error("invalid_proposal", "Proposal has expired")
   ```

4. **検証失敗時**
   - エラー応答を返す: `invalid_proposal`
   - 支払いは処理されず、取引は成立しない

5. **検証成功時**
   - 支払いメッセージをデータベースに記録
   - 成功応答を返す

---

### 6. 確認フェーズ（Confirmation Phase）

#### 顧客側の処理

**場所:** `marketplace/agents/customer/agent.py:294-306`

支払い送信が成功すると:

```python
# 提案ステータスを更新
proposal_storage[proposal_id].status = "accepted"

# 完了リストに追加
self.completed_transactions.append(proposal_id)

# トランザクションを記録
self.transaction_history.append({
    "proposal_id": proposal_id,
    "business_id": business_id,
    "amount": proposal.total_price,
    "timestamp": datetime.now()
})
```

#### ビジネス側の処理

**場所:** `marketplace/agents/business/agent.py:180-224`

ビジネスエージェントが支払いを受信すると:

```python
# 1. メッセージをフェッチ
messages = await self.fetch_messages()

# 2. Paymentメッセージを検出
for message in messages:
    if message.type == "payment":
        # 3. 提案を取得
        proposal = self.proposal_storage[message.proposal_message_id]

        # 4. ステータス確認
        if proposal.status != "pending":
            continue  # 既に処理済み

        # 5. ステータス更新
        proposal.status = "accepted"

        # 6. 確認済み注文リストに追加
        self.confirmed_orders.append({
            "proposal_id": proposal.id,
            "customer_id": message.from_agent_id,
            "amount": proposal.total_price,
            "items": proposal.items
        })

        # 7. 確認メッセージを送信
        confirmation = TextMessage(
            content=f"Payment received! Your order for ${proposal.total_price} is confirmed. Order ID: {proposal.id}. Thank you for your business!"
        )
        await self.send_message(message.from_agent_id, confirmation)
```

---

## データベース記録

### Actionsテーブルへの記録

取引の各ステップは`actions`テーブルにJSONBとして記録されます。

#### 1. OrderProposal送信
```json
{
    "id": "action_uuid",
    "agent_id": "business_0001-0",
    "created_at": "2025-11-01T20:31:47Z",
    "request": {
        "name": "SendMessage",
        "parameters": {
            "type": "send_message",
            "to_agent_id": "customer_0001-0",
            "message": {
                "type": "order_proposal",
                "id": "business_0001_customer_0001-0_1",
                "items": [
                    {"name": "Item A", "quantity": 1, "unit_price": 10.0}
                ],
                "total_price": 10.0
            }
        }
    },
    "result": {
        "is_error": false
    }
}
```

#### 2. FetchMessages（顧客）
```json
{
    "id": "action_uuid",
    "agent_id": "customer_0001-0",
    "created_at": "2025-11-01T20:31:50Z",
    "request": {
        "name": "FetchMessages"
    },
    "result": {
        "is_error": false,
        "content": {
            "messages": [
                {
                    "type": "order_proposal",
                    "id": "business_0001_customer_0001-0_1",
                    ...
                }
            ]
        }
    }
}
```

#### 3. Payment送信
```json
{
    "id": "action_uuid",
    "agent_id": "customer_0001-0",
    "created_at": "2025-11-01T20:31:55Z",
    "request": {
        "name": "SendMessage",
        "parameters": {
            "type": "send_message",
            "to_agent_id": "business_0001-0",
            "message": {
                "type": "payment",
                "proposal_message_id": "business_0001_customer_0001-0_1",
                "payment_method": "credit_card"
            }
        }
    },
    "result": {
        "is_error": false
    }
}
```

#### 4. Confirmation送信
```json
{
    "id": "action_uuid",
    "agent_id": "business_0001-0",
    "created_at": "2025-11-01T20:31:57Z",
    "request": {
        "name": "SendMessage",
        "parameters": {
            "type": "send_message",
            "to_agent_id": "customer_0001-0",
            "message": {
                "type": "text",
                "content": "Payment received! Your order for $10.0 is confirmed..."
            }
        }
    }
}
```

### データベースインデックス

効率的なクエリのため、以下のインデックスが作成されています:

```sql
-- 受信者でフィルタリング（メッセージ取得用）
CREATE INDEX idx_actions_to_agent
ON actions ((data->'request'->'parameters'->>'to_agent_id'));

-- 送信者でフィルタリング
CREATE INDEX idx_actions_from_agent
ON actions ((data->'request'->'parameters'->>'from_agent_id'));

-- アクションタイプでフィルタリング
CREATE INDEX idx_actions_name
ON actions ((data->'request'->>'name'));
```

---

## 提案の検証

### アナリティクスエンジンによる検証

**場所:** `experiments/run_analytics.py:452-526`

取引完了後、アナリティクスエンジンが提案の妥当性を検証します:

#### 1. InvalidMenuItem（無効なメニュー項目）
```python
# 商品名がビジネスのメニューに存在しない
if item.name not in business.menu_features:
    # レーベンシュタイン距離で類似度を計算
    distance = levenshtein_distance(item.name, closest_match)
    errors.append({
        "type": "InvalidMenuItem",
        "item": item.name,
        "distance": distance
    })
```

#### 2. InvalidMenuItemPrice（無効な価格）
```python
# 提案の価格がメニューの価格と一致しない
if item.unit_price != business.menu_features[item.name]:
    errors.append({
        "type": "InvalidMenuItemPrice",
        "item": item.name,
        "proposal_price": item.unit_price,
        "actual_price": business.menu_features[item.name]
    })
```

#### 3. InvalidTotalPrice（無効な合計金額）
```python
# 合計金額が個別商品の合計と一致しない
calculated_total = sum(item.unit_price * item.quantity for item in proposal.items)
if abs(proposal.total_price - calculated_total) > 0.01:
    errors.append({
        "type": "InvalidTotalPrice",
        "proposal_total": proposal.total_price,
        "calculated_total": calculated_total
    })
```

#### 4. Fuzzy Matching（あいまい一致）
```python
# --fuzzy-match-distance オプションでタイポを許容
magentic-marketplace analyze test_exp --fuzzy-match-distance 2

# レーベンシュタイン距離が2以下なら一致とみなす
# 例: "Taco" と "Tacos" (距離=1) → 一致
```

---

## 効用計算（Utility Calculation）

### 顧客の効用

**場所:** `experiments/run_analytics.py:384-442`

```python
# 効用の計算式
utility = match_score - total_payments

# match_scoreの計算
if needs_met:
    match_score = 2 × sum(customer.menu_features.values())
else:
    match_score = 0

# needs_metの判定
needs_met = (
    all_requested_items_in_proposal AND
    all_required_amenities_match
)
```

**例:**

顧客の要求:
- Item A: 支払意欲 $10.00
- Item B: 支払意欲 $5.00
- 必須設備: Outdoor Seating

提案:
- Item A: $8.00
- Item B: $4.00
- 設備: Outdoor Seating あり

計算:
```python
match_score = 2 × (10.00 + 5.00) = 30.00
total_payments = 8.00 + 4.00 = 12.00
utility = 30.00 - 12.00 = 18.00
```

### ビジネスの効用

**場所:** `experiments/run_analytics.py:584-607`

```python
# ビジネスの効用は総収益
utility = sum(all_payments_received)
```

**例:**

ビジネスが受け取った支払い:
- Customer 1: $12.00
- Customer 2: $15.00
- Customer 3: $8.00

```python
utility = 12.00 + 15.00 + 8.00 = 35.00
```

### 市場厚生（Market Welfare）

```python
# 全顧客の効用の合計
market_welfare = sum(customer_utilities)
```

---

## エラーハンドリング

### 1. 無効な提案ID

```python
# Payment送信時に存在しない提案IDを参照
result = await send_message(business_id, Payment(
    proposal_message_id="non_existent_id"
))

# 返却されるエラー
{
    "is_error": true,
    "error": {
        "code": "invalid_proposal",
        "message": "No valid proposal found with this ID"
    }
}
```

### 2. 期限切れの提案

```python
# 提案の有効期限が切れている
if proposal.expiry_time and datetime.now() > proposal.expiry_time:
    return Error("invalid_proposal", "Proposal has expired")
```

### 3. 間違ったビジネスへの支払い

```python
# 提案がbusiness_A からだが、business_Bに支払いを試みる
payment = Payment(proposal_message_id="business_A_proposal")
result = await send_message("business_B", payment)

# エラーが返される
{
    "is_error": true,
    "error": {
        "code": "invalid_proposal",
        "message": "Proposal is not from this business"
    }
}
```

---

## コード参照

### 主要なファイル

| ファイルパス | 説明 |
|------------|------|
| `marketplace/actions/messaging.py` | メッセージタイプの定義（OrderProposal, Payment, TextMessage） |
| `marketplace/protocol/send_message.py:76-127` | 支払い検証ロジック |
| `marketplace/agents/customer/agent.py:136-149` | 提案の受信と保存 |
| `marketplace/agents/customer/agent.py:272-335` | 提案の評価と支払い |
| `marketplace/agents/business/agent.py:158-179` | 提案の作成と送信 |
| `marketplace/agents/business/agent.py:180-224` | 支払いの受信と確認 |
| `experiments/run_analytics.py:384-442` | 顧客効用の計算 |
| `experiments/run_analytics.py:452-526` | 提案の検証 |
| `experiments/run_analytics.py:584-607` | ビジネス効用の計算 |

### データ構造

| モデル | 場所 | 説明 |
|--------|------|------|
| `OrderProposal` | `marketplace/actions/messaging.py` | 注文提案 |
| `OrderItem` | `marketplace/actions/messaging.py` | 注文商品 |
| `Payment` | `marketplace/actions/messaging.py` | 支払い |
| `TextMessage` | `marketplace/actions/messaging.py` | テキストメッセージ |

---

## シーケンス図

```
顧客              マーケットプレイス              ビジネス
  |                      |                        |
  |-- Search ---------->|                        |
  |<- Results ----------|                        |
  |                     |                        |
  |-- TextMessage ----->|----------------------->|
  |                     |                        |
  |                     |<-- OrderProposal ------|
  |<- OrderProposal ----|                        |
  |                     |                        |
  | (評価・決定)         |                        |
  |                     |                        |
  |-- Payment --------->|                        |
  |                     | (検証)                  |
  |                     |-- Payment ------------>|
  |                     |                        |
  |                     |<-- Confirmation -------|
  |<- Confirmation -----|                        |
  |                     |                        |
```

---

## まとめ

### 取引成立の条件

1. ✅ ビジネスが有効な`OrderProposal`を送信
2. ✅ 顧客が提案を受信・保存
3. ✅ 顧客が提案を評価し、受諾を決定
4. ✅ 顧客が有効な`Payment`メッセージを送信
5. ✅ プロトコルが提案を検証（存在、送信者、有効期限）
6. ✅ ビジネスが支払いを受信
7. ✅ ビジネスが確認メッセージを送信

### 取引が失敗する理由

1. ❌ 無効な提案ID（存在しない）
2. ❌ 期限切れの提案
3. ❌ 間違ったビジネスへの支払い
4. ❌ 顧客が提案を受諾しない（価格、商品、設備が不一致）
5. ❌ ネットワークエラーやシステムエラー

### 特徴

- **非同期通信**: エージェント間はメッセージングで通信
- **完全な記録**: すべてのアクションがデータベースに記録
- **検証**: プロトコルがすべての支払いを検証
- **トレーサビリティ**: 取引は提案IDで追跡可能
- **効用計算**: 取引後に市場効率を分析

---

*このドキュメントはMulti-Agent Marketplaceのソースコードに基づいて作成されました。*
