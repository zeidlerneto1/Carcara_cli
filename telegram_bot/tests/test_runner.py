"""Unit tests for the TurnRunner streaming logic (using a fake bot)."""

from __future__ import annotations

import asyncio

from kosong.message import TextPart
from telegram_bot.approvals import ApprovalTracker
from telegram_bot.config import BotConfig
from telegram_bot.questions import QuestionTracker
from telegram_bot.runner import TurnRunner

from kimi_cli.wire.types import (
    ApprovalRequest,
    StepBegin,
    TurnEnd,
)


class FakeMessage:
    def __init__(self, message_id: int):
        self.message_id = message_id
        self.text = ""

    async def edit_text(self, text: str, **kwargs):
        self.text = text
        return self

    async def delete(self):
        pass


class FakeBot:
    def __init__(self):
        self.messages: list[FakeMessage] = []
        self._counter = 0

    async def send_message(self, chat_id: int, text: str, **kwargs):
        msg = FakeMessage(self._counter)
        self._counter += 1
        msg.text = text
        self.messages.append(msg)
        return msg

    async def delete_message(self, chat_id: int, message_id: int):
        pass

    async def edit_message_text(self, chat_id: int, message_id: int, text: str, **kwargs):
        for m in self.messages:
            if m.message_id == message_id:
                m.text = text
                return m
        return None


class FakeUserSession:
    def __init__(self, stream, approval_runtime=None):
        self._stream = stream
        self.approval_runtime = approval_runtime

    async def run(self, user_input, cancel_event):
        async for msg in self._stream:
            yield msg


def _make_config():
    return BotConfig(
        token="x",
        base_dir=None,
        carcara_config_file=None,
        show_thinking=False,
        stream_refresh_interval=0.0,
        stream_max_chars=3500,
    )


def test_runner_streams_text():
    async def main():
        bot = FakeBot()
        config = _make_config()
        approvals = ApprovalTracker()
        questions = QuestionTracker()

        async def stream():
            yield StepBegin(n=1)
            yield TextPart(text="Hello ")
            yield TextPart(text="world")
            yield TurnEnd()

        user = FakeUserSession(stream())
        runner = TurnRunner(
            bot,
            chat_id=1,
            user_session=user,
            config=config,
            approvals=approvals,
            questions=questions,
        )
        await runner.run("hi")

        # There should be a "processing" status message and a final stream message.
        texts = [m.text for m in bot.messages]
        assert any("processando" in t for t in texts)
        assert any("Hello world" in t for t in texts)

    asyncio.run(main())


def test_runner_escapes_html_in_streamed_text():
    """Agent text containing HTML-special chars must be escaped before sending with parse_mode=HTML."""

    async def main():
        bot = FakeBot()
        config = _make_config()
        approvals = ApprovalTracker()
        questions = QuestionTracker()

        async def stream():
            yield StepBegin(n=1)
            yield TextPart(text="Use --flag and <b>bold</b> & more")
            yield TurnEnd()

        user = FakeUserSession(stream())
        runner = TurnRunner(
            bot,
            chat_id=1,
            user_session=user,
            config=config,
            approvals=approvals,
            questions=questions,
        )
        await runner.run("hi")

        texts = [m.text for m in bot.messages]
        stream_text = next(t for t in texts if "flag" in t)
        # The raw text must be escaped for HTML parse mode.
        assert "--flag" in stream_text
        assert "&lt;b&gt;bold&lt;/b&gt;" in stream_text
        assert "&amp;" in stream_text

    asyncio.run(main())


def test_runner_sends_approval_button():
    async def main():
        bot = FakeBot()
        config = _make_config()
        approvals = ApprovalTracker()
        questions = QuestionTracker()

        req = ApprovalRequest(
            id="req1",
            tool_call_id="tc1",
            sender="x",
            action="Shell",
            description="Run command",
        )

        async def stream():
            yield StepBegin(n=1)
            yield req
            yield TurnEnd()

        user = FakeUserSession(stream())
        runner = TurnRunner(
            bot,
            chat_id=1,
            user_session=user,
            config=config,
            approvals=approvals,
            questions=questions,
        )
        await runner.run("go")

        # The approval request should be registered as pending.
        assert req.id in approvals._pending
        texts = [m.text for m in bot.messages]
        assert any("Aprovação" in t for t in texts)

    asyncio.run(main())


def test_runner_splits_long_text():
    """Text longer than stream_max_chars must be split into multiple messages."""

    async def main():
        bot = FakeBot()
        config = _make_config()  # stream_max_chars=3500
        approvals = ApprovalTracker()
        questions = QuestionTracker()

        long_text = "A" * 5000

        async def stream():
            yield StepBegin(n=1)
            yield TextPart(text=long_text)
            yield TurnEnd()

        user = FakeUserSession(stream())
        runner = TurnRunner(
            bot,
            chat_id=1,
            user_session=user,
            config=config,
            approvals=approvals,
            questions=questions,
        )
        await runner.run("hi")

        # The "processando" message + split text chunks.
        texts = [m.text for m in bot.messages]
        non_status = [t for t in texts if "processando" not in t]
        # 5000 chars with 3500 max should produce at least 2 chunks.
        assert len(non_status) >= 2
        # Every chunk must be <= stream_max_chars.
        for t in non_status:
            assert len(t) <= config.stream_max_chars

    asyncio.run(main())
