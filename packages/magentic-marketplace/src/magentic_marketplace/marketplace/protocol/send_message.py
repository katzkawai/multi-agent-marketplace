"""SendMessage action implementation for the simple marketplace.

シンプルなマーケットプレイス用のSendMessageアクション実装。

このモジュールは、エージェント間のメッセージ送信機能を実装します。
メッセージは以下の3つのタイプがあります:
1. TextMessage: 自由形式のテキストメッセージ
2. OrderProposal: ビジネスから顧客への構造化された注文提案
3. Payment: 顧客からビジネスへの支払い（提案の受諾）

メッセージ送信には厳密な検証が必要です:
- 送信先エージェントの存在確認
- Paymentメッセージの場合、参照する提案の存在と有効性の確認
- すべての検証に成功した後、メッセージはデータベースに記録される
"""

import logging

from magentic_marketplace.platform.database.base import (
    BaseDatabaseController,
    RangeQueryParams,
)
from magentic_marketplace.platform.shared.models import (
    ActionExecutionResult,
)

from ..actions import OrderProposal, Payment, SendMessage
from ..database import queries

logger = logging.getLogger(__name__)


async def execute_send_message(
    send_message: SendMessage,
    database: BaseDatabaseController,
) -> ActionExecutionResult:
    """Execute a send message action.

    This function implements the message sending functionality that was previously
    handled by the /assistant/send and /service/send routes in platform.py.

    Args:
        send_message: The parsed send message action containing message data
        database: Database controller for accessing data

    Returns:
        ActionExecutionResult indicating success or failure

    """
    """メッセージ送信アクションを実行します。

    この関数は、以前platform.pyの/assistant/sendと/service/sendルートで
    処理されていたメッセージ送信機能を実装しています。

    メッセージ送信フロー:
    1. 送信先エージェントの存在を確認
    2. メッセージ内容を検証（特にPaymentメッセージの場合は提案IDを確認）
    3. 検証成功時、メッセージをデータベースのactionsテーブルに記録
    4. 成功またはエラーの結果を返す

    Args:
        send_message: メッセージデータを含む解析済みのsend messageアクション
        database: データにアクセスするためのデータベースコントローラ

    Returns:
        ActionExecutionResult: 成功またはエラーを示す実行結果
    """
    # Validate the target agent exists
    # 送信先エージェントの存在を検証
    # これにより、存在しないエージェントへのメッセージ送信を防ぐ
    target_agent = await database.agents.get_by_id(send_message.to_agent_id)
    if target_agent is None:
        # エラー結果を返す: エージェントが見つからない
        return ActionExecutionResult(
            content={"error": f"to_agent_id {send_message.to_agent_id} not found"},
            is_error=True,
        )

    # Validate message content
    # メッセージ内容を検証（特にPaymentメッセージの場合は提案の存在を確認）
    validation_error = await _validate_message_content(send_message, database)
    if validation_error:
        # 検証エラーがある場合、エラー結果を返す
        return ActionExecutionResult(
            content=validation_error,
            is_error=True,
        )

    # Create the action result for the successful send
    # メッセージ送信成功時のアクション結果を作成
    # このデータはデータベースのactionsテーブルに記録され、
    # 受信者がFetchMessagesアクションで取得できる
    action_result = ActionExecutionResult(
        content=send_message.model_dump(mode="json"),
        is_error=False,
        metadata={"status": "sent"},  # メタデータでステータスを記録
    )

    return action_result


async def _validate_message_content(
    send_message: SendMessage,
    database: BaseDatabaseController,
) -> dict[str, str] | None:
    """Validate message content based on message type.

    Args:
        send_message: The message to validate
        database: Database controller

    Returns:
        Error dict with error_type and message, or None if valid

    """
    """メッセージタイプに基づいてメッセージ内容を検証します。

    この関数は、メッセージ内容の整合性を確保するために呼び出されます。
    現在は主にPaymentメッセージの検証を行います。

    検証ロジック:
    - TextMessage: 特別な検証は不要（自由形式テキスト）
    - OrderProposal: 特別な検証は不要（構造はPydanticで保証済み）
    - Payment: 参照する提案IDが存在し、有効期限が切れていないことを確認

    Args:
        send_message: 検証するメッセージ
        database: データベースコントローラ

    Returns:
        検証エラーの辞書（error_typeとmessageを含む）、または有効な場合はNone
    """
    # For payment messages, validate proposal_id exists
    # Paymentメッセージの場合、proposal_idが存在することを検証
    if isinstance(send_message.message, Payment):
        proposal_id = send_message.message.proposal_message_id

        # Find the order proposal we're trying to pay for
        # 支払おうとしている注文提案を検索
        # クエリは3つの条件を組み合わせて構築:
        # 1. 送信者が送信先エージェント（ビジネス）であること
        # 2. メッセージタイプがOrderProposalであること
        # 3. 提案IDが一致すること
        query = (
            queries.actions.send_message.from_agent(send_message.to_agent_id)
            & queries.actions.send_message.order_proposals()
            & queries.actions.send_message.order_proposal_id(proposal_id)
        )
        action_rows = await database.actions.find(query, RangeQueryParams())
        order_proposals: list[OrderProposal] = []
        # データベースから取得した各アクション行を処理
        for row in action_rows:
            # アクションデータをSendMessageオブジェクトに変換
            action = SendMessage.model_validate(row.data.request.parameters)
            if isinstance(action.message, OrderProposal):
                # 現在、有効期限チェックは無効化されている
                # （LLMが生成する有効期限が不正確なため）
                logger.warning("Ignoring OrderProposal expiry time!")
                order_proposals.append(action.message)
                # TODO: Get LLMs to generate a decent expiry time, then bring this back:
                # TODO: LLMが適切な有効期限を生成できるようになったら、このコードを復活させる:
                # if (
                #     action.message.expiry_time
                #     and action.message.expiry_time
                #     < datetime.now(action.message.expiry_time.tzinfo)
                # ):
                #     logger.warning("Skipping expired order proposal")
                # else:
                #     order_proposals.append(action.message)
            else:
                # クエリがOrderProposal以外のアクションを返した場合（通常は発生しない）
                logger.warning(
                    f"OrderProposal query returned non OrderProposal action: {action.message.model_dump_json(indent=2)}"
                )

        if order_proposals:
            # 有効な提案が見つかった場合
            logger.info(
                f"Found {len(order_proposals)} matching unexpired proposals for payment",
                {
                    "order_proposals": [
                        p.model_dump(mode="json") for p in order_proposals
                    ]
                },
            )
            # There is at least one unexpired proposal for that id
            # そのIDに対して少なくとも1つの有効期限が切れていない提案が存在
            # 検証成功: Noneを返してメッセージ送信を許可
            return None
        else:
            # 有効な提案が見つからなかった場合
            logger.warning(
                f"No unexpired order proposals found with id {proposal_id}",
            )
            # エラーメッセージを返してメッセージ送信を拒否
            # この検証により、存在しない提案や期限切れの提案への支払いを防ぐ
            return {
                "error_type": "invalid_proposal",
                "message": f"No unexpired order proposals found with id {proposal_id}",
            }

    # TextMessageやOrderProposalの場合、特別な検証は不要
    # Noneを返して検証成功を示す
    return None
