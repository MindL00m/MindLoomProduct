"""Validation for the canonical :class:`Conversation` format.

Hard failures (empty participants/messages, unknown sender, missing timestamp)
raise :class:`ValueError`. Recoverable issues (out-of-order messages, blank
messages) are fixed in place and logged rather than rejecting the conversation.
"""

from __future__ import annotations

import logging

from models import Conversation

logger = logging.getLogger(__name__)


def validate_conversation(conversation: Conversation) -> None:
    """Validate and lightly repair a conversation in place.

    Raises:
        ValueError: If the conversation has no participants, no messages, a
            message whose sender is not a known participant, or a message
            missing a timestamp.

    Mutations (silent fixes applied directly to ``conversation``):
        * Messages with empty/whitespace-only text are discarded.
        * Out-of-order messages are sorted by timestamp ascending.

    Returns:
        None on success.
    """

    if not conversation.participants:
        raise ValueError("Conversation has no participants.")

    if not conversation.messages:
        raise ValueError("Conversation has no messages.")

    participant_ids = {participant.id for participant in conversation.participants}

    # Silently discard blank messages, while enforcing timestamp presence.
    kept = []
    discarded = 0
    for message in conversation.messages:
        if message.timestamp is None:
            raise ValueError(f"Message '{message.id}' is missing a timestamp.")
        if not message.text.strip():
            discarded += 1
            continue
        kept.append(message)

    if discarded:
        logger.warning(
            "Discarded %d empty message(s) from conversation '%s'",
            discarded,
            conversation.conversation_id,
        )

    # Validate sender references against the participant list.
    for message in kept:
        if message.sender not in participant_ids:
            raise ValueError(
                f"Message '{message.id}' has sender '{message.sender}' "
                "that does not match any participant id."
            )

    # Sort if out of order, logging a warning rather than rejecting.
    is_ordered = all(
        kept[i].timestamp <= kept[i + 1].timestamp for i in range(len(kept) - 1)
    )
    if not is_ordered:
        logger.warning(
            "Messages in conversation '%s' were out of order; sorting by timestamp.",
            conversation.conversation_id,
        )
        kept.sort(key=lambda message: message.timestamp)

    conversation.messages = kept
