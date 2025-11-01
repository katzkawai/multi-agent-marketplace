<div align="center">
<img src="docs/public/magentic-marketplace.svg" style="width: 80%" alt="Magentic Marketplace Logo">

_エージェント型マーケットプレイスのシミュレーション環境_

</div>

---

<div align="center">
   <video src="https://github.com/user-attachments/assets/5b897387-d96c-4e7a-9bd2-b6c53eaeabb9" style="max-height: 450px;">
   </video>
</div>

Magentic Marketplaceは、エージェント型市場のシミュレーションを実行するためのPython SDKです。
ビジネスエージェントと顧客エージェントを設定して取引を行い、市場の厚生を評価するシミュレーションを実行できます。

[**Magentic Marketplaceの詳細については、ドキュメントウェブサイトをご覧ください。**](https://microsoft.github.io/multi-agent-marketplace/)

## クイックスタート

1. 環境を設定する

   ```bash
   # リポジトリをクローン
   git clone https://github.com/microsoft/multi-agent-marketplace.git
   cd multi-agent-marketplace

   # `uv`で依存関係をインストール。https://docs.astral.sh/uv/ からインストール
   uv sync --all-extras
   source .venv/bin/activate

   # .envで環境変数を設定。お気に入りのエディタで編集
   cp sample.env .env

   # データベースサーバーを起動
   docker compose up -d
   ```

2. シミュレーションを実行して出力を分析する

   ```bash
   # 実験を実行（実験名はオプション）
   magentic-marketplace run data/mexican_3_9 --experiment-name test_exp

   # 結果を分析
   magentic-marketplace analyze test_exp
   ```

   Pythonスクリプトから実験を実行することもできます。[experiments/example.py](experiments/example.py)を参照してください。

   その他のCLIオプションは`magentic-marketplace --help`で確認できます。

## 日本語ドキュメント

このリポジトリには、日本語話者向けの包括的なドキュメントが用意されています：

### 📚 プロジェクト概要
- **[README_ja.md](README_ja.md)** - このファイル（クイックスタートガイド）
- **[TRANSPARENCY_ja.md](TRANSPARENCY_ja.md)** - プロジェクトの透明性情報、データセット詳細、制限事項、ベストプラクティス

### 🔧 技術ドキュメント
- **[transaction_mechanism.md](transaction_mechanism.md)** - 取引成立メカニズムの詳細説明
  - 提案フェーズから確認フェーズまでの6ステップ
  - データベース記録、検証ロジック、効用計算
  - エラーハンドリングとコード参照

- **[agent_conversation_generation.md](agent_conversation_generation.md)** - エージェントの会話生成メカニズム
  - LLMによるAI生成会話（98%）vs テンプレート（2%）
  - プロンプトエンジニアリング戦略
  - 構造化出力の仕組み
  - 実際の会話例とフロー図

### 📊 実験結果とサンプル
- **[test01_exp.md](test01_exp.md)** - test01_exp実験の結果サマリー
  - 市場効率性と取引パフォーマンス
  - 顧客別・事業者別の詳細結果
  - 重要な知見と課題

- **[test01_exp_chat.md](test01_exp_chat.md)** - test01_exp実験のエージェント間会話ログ
  - 顧客とビジネスのプロフィール
  - メッセージの詳細なタイムライン
  - 取引成功・失敗の分析

### 💻 ソースコード
すべてのPythonソースコード（30ファイル、約4,900行のコメント）に初心者向けの詳細な日本語コメントが追加されています：

**Phase 1 - コアファイル:**
- エージェント基底クラス (`agents/base.py`)
- 顧客エージェント (`agents/customer/agent.py`)
- ビジネスエージェント (`agents/business/agent.py`)
- メッセージング (`actions/messaging.py`)
- プロトコル (`protocol/`)

**Phase 2 - 実験関連:**
- 実験実行 (`experiments/run_experiment.py`)
- アナリティクス (`experiments/run_analytics.py`)
- 監査 (`experiments/run_audit.py`)
- エクスポート (`experiments/export_experiment.py`)

**Phase 3 - その他:**
- プロンプトとモデル (`agents/*/prompts.py`, `agents/*/models.py`)
- プラットフォーム層 (`platform/`)
- LLMクライアント (`marketplace/llm/clients/`)
- CLI (`cli.py`)

コメントは以下を説明しています：
- 各コンポーネントの目的（what）
- 実装の仕組み（how）
- 設計の理由（why）
- 初心者向けの概念説明（education）

## さらに詳しく

より詳細な情報については、以下をご覧ください：
- **公式ドキュメント:** https://microsoft.github.io/multi-agent-marketplace/
- **英語版README:** [README.md](README.md)
- **透明性情報（英語）:** [TRANSPARENCY.md](TRANSPARENCY.md)

---

## 更新履歴

### 2025-11-02: 日本語ドキュメント・コメントの大幅追加

このリポジトリに包括的な日本語ドキュメントとソースコードコメントを追加しました。

#### 📄 新規作成ドキュメント（7ファイル）

1. **README_ja.md** (このファイル)
   - 日本語版クイックスタートガイド
   - 全日本語ドキュメントへのナビゲーション

2. **TRANSPARENCY_ja.md**
   - プロジェクト概要の日本語翻訳
   - データセット詳細（メキシコ料理レストラン、業者）
   - 想定用途、制限事項、ベストプラクティス
   - 責任あるAIリソースへのリンク

3. **transaction_mechanism.md**
   - 取引成立メカニズムの完全解説（616行）
   - 6つのフェーズ詳細：提案→受信→評価→支払い→検証→確認
   - データベース記録のJSON例
   - 効用計算式と具体例
   - エラーハンドリングパターン
   - シーケンス図とコード参照

4. **agent_conversation_generation.md**
   - エージェント会話生成の仕組み（946行）
   - LLMによるAI生成（98%）vs テンプレート（2%）の詳細分析
   - プロンプトエンジニアリング戦略の完全解説
   - 顧客・ビジネスエージェントの実装詳細
   - 構造化出力（Pydantic）の仕組み
   - 実際のコード例とフロー図
   - LLM統合（OpenAI, Anthropic, Google）

5. **test01_exp.md**
   - test01_exp実験の詳細な結果サマリー
   - 市場効率性指標（市場全体の顧客効用: +42.21）
   - 顧客別の詳細結果（3名の成功・失敗分析）
   - 事業者別の収益と成約率
   - 重要な知見（強み・課題・市場構造）
   - アクション内訳とメッセージタイプ統計

6. **test01_exp_chat.md**
   - test01_exp実験の全会話ログ（565行）
   - 12エージェントのプロフィール詳細
   - 73件のメッセージの完全なタイムライン
   - 6つの会話スレッドの詳細分析
   - 取引成功・失敗の理由
   - ビジネスマッチング分析と価格比較

7. **このREADME_ja.md**
   - 日本語ドキュメントの完全なインデックス
   - カテゴリ別ドキュメント整理
   - 更新履歴（このセクション）

#### 💻 ソースコードへの日本語コメント追加（30ファイル、4,923行）

**Phase 2 完了: 実験関連（5ファイル）**
- `experiments/run_experiment.py` - 実験実行の7ステップワークフロー
- `experiments/run_analytics.py` - 効用計算・市場厚生分析（49KB→75KB）
- `experiments/run_audit.py` - 整合性検証・監査ロジック（50KB）
- `experiments/export_experiment.py` - PostgreSQL→SQLite変換
- `experiments/list_experiments.py` - 実験一覧とSQLクエリ解説

**Phase 1 完了: コアファイル（7ファイル）**
- `marketplace/agents/base.py` - エージェント基底クラス・LLM統合メカニズム
- `marketplace/agents/customer/agent.py` - 顧客ショッピングフロー・支払い処理
- `marketplace/agents/business/agent.py` - ビジネス応答・提案生成ロジック
- `marketplace/actions/messaging.py` - メッセージ型定義（OrderProposal, Payment, TextMessage）
- `marketplace/protocol/protocol.py` - マーケットプレイスプロトコル・アクション実行
- `marketplace/protocol/send_message.py` - メッセージ検証・支払い検証ロジック

**Phase 3 完了: その他（18ファイル）**

*プロンプト＆モデル（4ファイル）:*
- `marketplace/agents/customer/prompts.py` - 顧客エージェントのプロンプト戦略
- `marketplace/agents/customer/models.py` - 顧客アクションの構造化モデル
- `marketplace/agents/business/prompts.py` - ビジネスエージェントのプロンプト戦略
- `marketplace/agents/business/models.py` - ビジネスアクションの構造化モデル

*プラットフォーム層（6ファイル）:*
- `platform/launcher.py` - サーバー起動・エージェント調整
- `platform/server/server.py` - FastAPI REST APIアーキテクチャ
- `platform/client/client.py` - HTTPクライアント・リソース管理
- `platform/database/base.py` - データベース抽象化レイヤー
- `platform/database/converter.py` - PostgreSQL→SQLite変換ロジック
- `platform/logger.py` - デュアルロギングシステム（Python + Database）

*LLMクライアント（3ファイル）:*
- `marketplace/llm/clients/openai.py` - OpenAI API統合（reasoning modelサポート）
- `marketplace/llm/clients/anthropic.py` - Anthropic Claude統合（thinking mode）
- `marketplace/llm/clients/gemini.py` - Google Gemini統合（native JSON schema）

*その他（5ファイル）:*
- `cli.py` - 全CLIコマンド（run, analyze, export, list, ui）
- `experiments/utils/color_formatter.py` - カラーログシステム
- `experiments/utils/yaml_loader.py` - YAML設定ローダー
- `marketplace/actions/actions.py` - アクション定義（Search, SendMessage, FetchMessages）
- `platform/database/models.py` - データベースモデル定義

#### 📝 コメントの特徴

すべてのコメントは初心者でも理解できるように以下の方針で記述：

1. **バイリンガルアプローチ**
   - 元の英語docstringを保持
   - 日本語訳を追加
   - 技術用語は英語のまま（理解しやすさのため）

2. **4つの説明レベル**
   - **What（何を）**: 各コンポーネントの目的
   - **How（どのように）**: 実装の仕組み
   - **Why（なぜ）**: 設計の理由・意図
   - **Education（教育）**: 初心者向けの概念説明

3. **実践的な内容**
   - 具体的な使用例
   - よくあるパターン
   - トラブルシューティングのヒント
   - コード参照（ファイルパス:行番号）

4. **包括性**
   - クラス・メソッド・関数のdocstring
   - 複雑なロジックのインラインコメント
   - アルゴリズム解説
   - データ構造の説明

#### 🎯 成果物サマリー

| カテゴリ | ファイル数 | 行数 | 内容 |
|---------|-----------|------|------|
| 新規ドキュメント | 7 | 3,393行 | 完全な日本語技術ドキュメント |
| ソースコードコメント | 30 | 4,923行追加 | 初心者向け詳細解説 |
| **合計** | **37** | **8,316行** | **包括的日本語化** |

#### 🔗 主要なコミット

- `bb7ad7e` - README_ja.md, TRANSPARENCY_ja.mdの追加
- `003bab2` - agent_conversation_generation.mdの追加（946行）
- `8a40516` - transaction_mechanism.mdの追加（616行）
- `35e6ade` - test01_exp_chat.mdの追加（565行）
- `e24ed85` - 全ソースコードへの日本語コメント追加（30ファイル、4,923行）
- `17eb0f4` - README_ja.mdへのドキュメントリンク追加

#### 🙏 貢献

この日本語化作業は、日本語話者がMulti-Agent Marketplaceのコードベースを理解し、学習し、貢献できるようにすることを目的としています。フィードバックや改善提案は歓迎します。

---

## ライセンス

[MITライセンス](./LICENSE)

## 連絡先

研究は[Microsoft Research](https://www.microsoft.com/en-us/research/)のメンバーによって実施されました。
フィードバックとコラボレーション歓迎: magenticmarket@microsoft.com
