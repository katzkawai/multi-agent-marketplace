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
