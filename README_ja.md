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
