"""Send built views to a chat by id — shared by the hourly push and the Update button.

Both deliver without a triggering message to reply to (the push has none; the
inline Update button in a channel has only a callback query), so they send to
``chat_id`` directly instead of using ``reply_text``.
"""

from __future__ import annotations

import logging

from telegram import Bot, InlineKeyboardMarkup

from taxibot.core.text import split_message
from taxibot.views import OutboundMessage

logger = logging.getLogger(__name__)


async def send_messages(
    bot: Bot,
    chat_id: int | str,
    messages: list[OutboundMessage],
    last_markup: InlineKeyboardMarkup | None = None,
) -> int:
    """Send every message; ``last_markup`` goes under the very last chunk.

    One failed send must not abort the rest. Returns how many chunks were sent.
    """
    sent = 0
    for i, msg in enumerate(messages):
        chunks = split_message(msg.text)
        for j, chunk in enumerate(chunks):
            is_last = i == len(messages) - 1 and j == len(chunks) - 1
            markup = msg.reply_markup or (last_markup if is_last else None)
            try:
                await bot.send_message(
                    chat_id=chat_id, text=chunk, parse_mode="HTML", reply_markup=markup
                )
                sent += 1
            except Exception:
                logger.exception("Send failed for one chunk (chat_id=%s).", chat_id)
    return sent
