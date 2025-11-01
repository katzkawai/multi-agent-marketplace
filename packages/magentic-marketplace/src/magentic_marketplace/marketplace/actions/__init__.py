"""Simple marketplace actions.

シンプルなマーケットプレイスのアクション定義。

このモジュールは、マーケットプレイスにおけるエージェントが実行できるすべてのアクション（操作）と
メッセージタイプを定義し、公開APIとしてエクスポートします。

主要なコンポーネント:
1. アクション (actions.py): エージェントがマーケットプレイスに対して実行できる操作
   - Search: ビジネスを検索
   - SendMessage: メッセージを送信
   - FetchMessages: メッセージを取得

2. メッセージ (messaging.py): エージェント間で交換されるメッセージの型
   - TextMessage: 自由形式のテキストメッセージ
   - OrderProposal: 構造化された注文提案
   - Payment: 注文提案を受け入れる支払いメッセージ

モジュール構成の理由:
- actions.py: マーケットプレイスプロトコルが処理するアクションを定義
- messaging.py: メッセージの内容を定義（アクションのペイロードとして使用）
- __init__.py: すべての公開型をエクスポートし、クリーンなAPIを提供
"""

from .actions import (
    Action,
    ActionAdapter,
    FetchMessages,
    FetchMessagesResponse,
    ReceivedMessage,
    Search,
    SearchAlgorithm,
    SearchResponse,
    SendMessage,
)
from .messaging import (
    Message,
    MessageAdapter,
    OrderItem,
    OrderProposal,
    Payment,
    TextMessage,
)

__all__ = [
    "Action",
    "ActionAdapter",
    "FetchMessages",
    "FetchMessagesResponse",
    "Message",
    "MessageAdapter",
    "OrderItem",
    "OrderProposal",
    "Payment",
    "ReceivedMessage",
    "Search",
    "SearchAlgorithm",
    "SearchResponse",
    "SendMessage",
    "TextMessage",
]
