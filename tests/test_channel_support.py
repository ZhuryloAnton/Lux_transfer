"""Channels: commands arrive as channel_post and only inline buttons can show."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import taxibot.handlers as handlers
from taxibot.views import UPDATE_INLINE, OutboundMessage


class _Msg:
    def __init__(self, chat_id: int = -100123) -> None:
        self.chat_id = chat_id
        self.text = "/report"
        self.replies: list[tuple[str, object]] = []

    async def reply_text(self, text, parse_mode=None, reply_markup=None):
        self.replies.append((text, reply_markup))
        return self

    async def delete(self):
        return True


def _channel_update(msg: _Msg):
    # A channel post: update.message is None, the post is in effective_message.
    return SimpleNamespace(
        message=None,
        effective_message=msg,
        effective_chat=SimpleNamespace(type="channel", id=msg.chat_id),
    )


class _Bot:
    def __init__(self) -> None:
        self.sent: list[tuple[object, str, object]] = []

    async def send_message(self, chat_id, text, parse_mode=None, reply_markup=None):
        self.sent.append((chat_id, text, reply_markup))


def _fake_view(monkeypatch, name: str) -> None:
    async def build(pipeline):
        return [OutboundMessage("flights"), OutboundMessage("tgv")]

    monkeypatch.setattr(handlers, name, build)


def test_command_in_channel_replies_with_inline_update(monkeypatch) -> None:
    _fake_view(monkeypatch, "build_soon_messages")
    msg = _Msg()
    context = SimpleNamespace(bot_data={"pipeline": object()})

    asyncio.run(handlers.cmd_report(_channel_update(msg), context))

    texts = [t for t, _ in msg.replies]
    assert texts[-2:] == ["flights", "tgv"]
    # Reply keyboards don't exist in channels — the last message carries inline Update.
    assert msg.replies[-1][1] is UPDATE_INLINE


def test_private_chat_keeps_reply_keyboard() -> None:
    update = SimpleNamespace(effective_chat=SimpleNamespace(type="private"))
    assert handlers._keyboard(update) is handlers.KEYBOARD


def test_inline_update_posts_fresh_view_to_channel_with_cooldown(monkeypatch) -> None:
    _fake_view(monkeypatch, "build_fresh_soon_messages")
    answers: list[str | None] = []

    async def answer(text=None):
        answers.append(text)

    query = SimpleNamespace(message=SimpleNamespace(chat_id=-100123), answer=answer)
    update = SimpleNamespace(callback_query=query)
    bot = _Bot()
    context = SimpleNamespace(bot=bot, bot_data={"pipeline": object()})

    asyncio.run(handlers.handle_update_callback(update, context))
    asyncio.run(handlers.handle_update_callback(update, context))  # second press, too soon

    assert [(c, t) for c, t, _ in bot.sent] == [(-100123, "flights"), (-100123, "tgv")]
    assert bot.sent[-1][2] is UPDATE_INLINE
    assert answers[-1] == "Already updated just now."
