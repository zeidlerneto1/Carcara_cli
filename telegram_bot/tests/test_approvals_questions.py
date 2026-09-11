"""Unit tests for approval and question tracking."""

from __future__ import annotations

import asyncio

from telegram_bot.approvals import ApprovalTracker, parse_callback
from telegram_bot.questions import QuestionTracker
from telegram_bot.questions import parse_callback as qparse_callback

from kimi_cli.wire.types import (
    ApprovalRequest,
    QuestionItem,
    QuestionOption,
    QuestionRequest,
)


def test_approval_callback_parsing():
    assert parse_callback("apr:req123:approve") == ("req123", "approve")
    assert parse_callback("apr:req123:session") == ("req123", "session")
    assert parse_callback("apr:req123:reject") == ("req123", "reject")
    assert parse_callback("other:xx") is None


def test_question_callback_parsing():
    assert qparse_callback("qst:req456:0:2:s") == ("req456", 0, 2, "s")
    assert qparse_callback("qst:req456:1:0:c") == ("req456", 1, 0, "c")
    assert qparse_callback("nope") is None
    assert qparse_callback("qst:req456:0:s") is None


def test_approval_resolution():
    async def main():
        tracker = ApprovalTracker()
        req = ApprovalRequest(
            id="req123",
            tool_call_id="tc1",
            sender="x",
            action="Shell",
            description="Run command",
        )
        tracker._pending["req123"] = req
        assert tracker.resolve_callback("req123", "approve") is True
        assert req.resolved
        assert await req.wait() == "approve"

    asyncio.run(main())


def test_question_single_select_resolves():
    async def main():
        tracker = QuestionTracker()
        qreq = QuestionRequest(
            id="req456",
            tool_call_id="tc2",
            questions=[
                QuestionItem(
                    question="Linguagem?",
                    options=[
                        QuestionOption(label="Python"),
                        QuestionOption(label="Rust"),
                    ],
                )
            ],
        )
        from telegram_bot.questions import _QuestionState

        state = _QuestionState(request_id=qreq.id, request=qreq, items=qreq.questions)
        tracker._pending[qreq.id] = state

        # Simulate selecting option 0 (Python).
        await tracker.on_select(bot=None, chat_id=1, request_id=qreq.id, item_idx=0, opt_idx=0)
        assert qreq.resolved
        answers = await qreq.wait()
        assert answers == {"Linguagem?": "Python"}

    asyncio.run(main())


def test_question_multi_select_resolves_on_confirm():
    async def main():
        tracker = QuestionTracker()
        qreq = QuestionRequest(
            id="req789",
            tool_call_id="tc3",
            questions=[
                QuestionItem(
                    question="Escolha frameworks?",
                    options=[
                        QuestionOption(label="A"),
                        QuestionOption(label="B"),
                        QuestionOption(label="C"),
                    ],
                    multi_select=True,
                )
            ],
        )
        from telegram_bot.questions import _QuestionState

        state = _QuestionState(request_id=qreq.id, request=qreq, items=qreq.questions)
        tracker._pending[qreq.id] = state

        # Toggle A and C, then confirm.
        await tracker.on_select(bot=None, chat_id=1, request_id=qreq.id, item_idx=0, opt_idx=0)
        await tracker.on_select(bot=None, chat_id=1, request_id=qreq.id, item_idx=0, opt_idx=2)
        assert not qreq.resolved  # not yet, need confirm
        await tracker.on_confirm(bot=None, chat_id=1, request_id=qreq.id, item_idx=0)
        assert qreq.resolved
        answers = await qreq.wait()
        assert answers == {"Escolha frameworks?": "A, C"}

    asyncio.run(main())
