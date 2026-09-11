"""Handle ApprovalRequest WireMessages via Telegram inline buttons."""

from __future__ import annotations

import asyncio
import contextlib
import html
import logging
from typing import TYPE_CHECKING

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from kimi_cli.wire.types import ApprovalRequest

if TYPE_CHECKING:
    from telegram import Bot

    from kimi_cli.approval_runtime import ApprovalRuntime

logger = logging.getLogger(__name__)

# callback_data prefix for approval callbacks
PREFIX = "apr"

# mapping from button action to ApprovalRequest.Kind
_ACTION_TO_KIND = {
    "approve": "approve",
    "session": "approve_for_session",
    "reject": "reject",
}


def _callback(request_id: str, action: str) -> str:
    return f"{PREFIX}:{request_id}:{action}"


def parse_callback(callback_data: str) -> tuple[str, str] | None:
    """Parse an approval callback_data into (request_id, action) or None."""
    if not callback_data.startswith(PREFIX + ":"):
        return None
    try:
        _, request_id, action = callback_data.split(":", 2)
        return request_id, action
    except ValueError:
        logger.warning("Failed to parse callback data: %s", callback_data)
        return None


class ApprovalTracker:
    """Tracks pending approval requests so callbacks can resolve them."""

    def __init__(self, timeout_s: float = 300.0, approval_runtime: "ApprovalRuntime | None" = None) -> None:
        # request_id -> ApprovalRequest
        self._pending: dict[str, ApprovalRequest] = {}
        # request_id -> timeout task
        self._timeout_tasks: dict[str, asyncio.Task] = {}
        # request_id -> event to signal resolution
        self._resolve_events: dict[str, asyncio.Event] = {}
        # request_id -> (chat_id, message_id) so timeout can update the UI
        self._msg_refs: dict[str, tuple[int, int]] = {}
        # request_id -> bot reference for timeout updates
        self._bots: dict[str, Bot] = {}
        # How long to wait for a user response before auto-rejecting.
        self.timeout_s = timeout_s
        # Reference to the approval runtime so we can resolve requests properly.
        self._approval_runtime = approval_runtime

    async def _auto_reject(self, request: ApprovalRequest) -> None:
        print("[DEBUG-APPROVAL] _auto_reject START (sleeping %.1fs)" % self.timeout_s)
        await asyncio.sleep(self.timeout_s)
        print("[DEBUG-APPROVAL] _auto_reject WAKE UP — request.resolved:", request.resolved)
        if not request.resolved:
            logger.warning("Approval %s timed out, auto-rejecting", request.id)
            print("[DEBUG-APPROVAL] getting approval_runtime for this request...")
            approval_runtime = getattr(self, "_approval_runtimes", {}).get(request.id)
            print("[DEBUG-APPROVAL] approval_runtime:", approval_runtime)
            
            # Resolve via the approval runtime (this is what the agent is waiting for).
            if approval_runtime is not None:
                print("[DEBUG-APPROVAL] calling approval_runtime.resolve('reject', ...)")
                approval_runtime.resolve(request.id, "reject", "Timed out waiting for user response.")
                print("[DEBUG-APPROVAL] approval_runtime.resolve() done")
            else:
                # Fallback: resolve the wire request directly (for testing).
                print("[DEBUG-APPROVAL] approval_runtime is None, calling request.resolve(...)")
                request.resolve("reject", "Timed out waiting for user response.")
                print("[DEBUG-APPROVAL] request.resolve() done")
            
            event = self._resolve_events.get(request.id)
            if event:
                event.set()
                print("[DEBUG-APPROVAL] event.set() done")
            # Update the Telegram message so the user sees it expired.
            print("[DEBUG-APPROVAL] calling _update_message_on_timeout...")
            await self._update_message_on_timeout(request.id)
            print("[DEBUG-APPROVAL] _update_message_on_timeout done")
        else:
            print("[DEBUG-APPROVAL] already resolved, skipping")
        self._timeout_tasks.pop(request.id, None)
        print("[DEBUG-APPROVAL] _auto_reject EXIT")

    async def _update_message_on_timeout(self, request_id: str) -> None:
        """Edit the approval message to show it expired (remove buttons)."""
        ref = self._msg_refs.pop(request_id, None)
        bot = self._bots.pop(request_id, None)
        if ref is None or bot is None:
            return
        chat_id, message_id = ref
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text="⏳ <b>Aprovação expirada.</b>\n\nNenhum clique em tempo hábil. Tente novamente.",
                parse_mode="HTML",
                reply_markup=None,
            )
        except Exception as e:
            logger.warning("Failed to update approval message on timeout: %s", e)

    async def handle(
        self,
        bot: Bot,
        chat_id: int,
        request: ApprovalRequest,
        approval_runtime=None,
    ) -> None:
        """Send an approval request to the user. Resolution happens via callback.

        This method sends the inline buttons and returns immediately; the
        request is resolved asynchronously when the user clicks a button (see
        :meth:`resolve_callback`).

        Args:
            approval_runtime: The ApprovalRuntime instance from the KimiCLI.
                              Required for proper resolution.
        """
        print("\n" + "-" * 50)
        print("[DEBUG-APPROVAL] handle() ENTER")
        print("[DEBUG-APPROVAL] bot:", bot)
        print("[DEBUG-APPROVAL] chat_id:", chat_id)
        print("[DEBUG-APPROVAL] request.id:", request.id)
        print("[DEBUG-APPROVAL] request.action:", request.action)
        print("[DEBUG-APPROVAL] request.description:", request.description)
        print("[DEBUG-APPROVAL] approval_runtime:", approval_runtime)
        logger.info("Sending approval request to chat %s: %s", chat_id, request.id)

        # Store the approval runtime for this request so resolve_callback can use it.
        self._approval_runtimes: dict[str, object] = getattr(self, "_approval_runtimes", {})
        self._approval_runtimes[request.id] = approval_runtime
        
        print("[DEBUG-APPROVAL] storing in _pending...")
        self._pending[request.id] = request
        print("[DEBUG-APPROVAL] _pending now:", list(self._pending.keys()))
        self._resolve_events[request.id] = asyncio.Event()

        if self.timeout_s > 0:
            print("[DEBUG-APPROVAL] creating auto_reject task (timeout=%.1fs)" % self.timeout_s)
            self._timeout_tasks[request.id] = asyncio.create_task(self._auto_reject(request))
            print("[DEBUG-APPROVAL] _timeout_tasks now:", list(self._timeout_tasks.keys()))

        description = request.description or request.action or "Ação"
        text = (
            f"🔒 <b>Aprovação necessária</b>\n\n{html.escape(description)}\n\n"
            f"<i>{html.escape(request.action)}</i>"
        )
        print("[DEBUG-APPROVAL] text to send:", repr(text))

        # Create callback data
        approve_callback = _callback(request.id, "approve")
        session_callback = _callback(request.id, "session")
        reject_callback = _callback(request.id, "reject")
        print("[DEBUG-APPROVAL] callbacks:", approve_callback, session_callback, reject_callback)

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("✅ Aprovar", callback_data=approve_callback),
                    InlineKeyboardButton("✅ Sessão", callback_data=session_callback),
                ],
                [
                    InlineKeyboardButton("❌ Rejeitar", callback_data=reject_callback)
                ],
            ]
        )
        print("[DEBUG-APPROVAL] calling bot.send_message with keyboard...")
        msg = await bot.send_message(chat_id, text, reply_markup=keyboard, parse_mode="HTML")
        print("[DEBUG-APPROVAL] bot.send_message returned msg.message_id:", msg.message_id)
        self._msg_refs[request.id] = (chat_id, msg.message_id)
        self._bots[request.id] = bot
        logger.info("Approval sent to chat %s: %s (message_id=%s)", chat_id, request.id, msg.message_id)
        print("[DEBUG-APPROVAL] handle() EXIT")
        print("-" * 50 + "\n")

    def resolve_callback(self, request_id: str, action: str, feedback: str = "") -> bool:
        """Resolve a pending approval request via callback."""
        print("[DEBUG-APPROVAL] resolve_callback ENTER")
        print("[DEBUG-APPROVAL] request_id:", request_id)
        print("[DEBUG-APPROVAL] action:", action)
        logger.debug("Trying to resolve: request_id=%s, action=%s", request_id, action)

        print("[DEBUG-APPROVAL] _pending keys:", list(self._pending.keys()))
        request = self._pending.get(request_id)
        print("[DEBUG-APPROVAL] request found:", request is not None)
        if request is None:
            print("[DEBUG-APPROVAL] request is None, RETURNING False")
            logger.warning("Request not found: %s", request_id)
            return False

        print("[DEBUG-APPROVAL] request.resolved:", request.resolved)
        if request.resolved:
            print("[DEBUG-APPROVAL] request already resolved, RETURNING False")
            logger.warning("Request already resolved: %s", request_id)
            return False

        kind = _ACTION_TO_KIND.get(action)
        print("[DEBUG-APPROVAL] kind:", kind)
        if kind is None:
            print("[DEBUG-APPROVAL] unknown action, RETURNING False")
            logger.warning("Unknown action: %s", action)
            return False

        logger.info("Resolving request %s with action %s (kind: %s)", request_id, action, kind)
        
        # Get the approval runtime for this specific request.
        approval_runtime = getattr(self, "_approval_runtimes", {}).get(request_id)
        print("[DEBUG-APPROVAL] approval_runtime for this request:", approval_runtime)
        
        # Resolve via the approval runtime (this is what the agent is waiting for).
        if approval_runtime is not None:
            print("[DEBUG-APPROVAL] calling approval_runtime.resolve(...)")
            success = approval_runtime.resolve(request_id, kind, feedback)
            print("[DEBUG-APPROVAL] approval_runtime.resolve returned:", success)
            if not success:
                print("[DEBUG-APPROVAL] approval_runtime.resolve failed, RETURNING False")
                return False
        else:
            # Fallback: resolve the wire request directly (for testing).
            print("[DEBUG-APPROVAL] approval_runtime is None, calling request.resolve(...)")
            request.resolve(kind, feedback)
            print("[DEBUG-APPROVAL] request.resolve() done")
        
        print("[DEBUG-APPROVAL] popping from _pending...")
        self._pending.pop(request_id, None)
        print("[DEBUG-APPROVAL] _pending now:", list(self._pending.keys()))

        # Signal that it was resolved.
        event = self._resolve_events.pop(request_id, None)
        print("[DEBUG-APPROVAL] resolve_event:", event)
        if event:
            event.set()
            print("[DEBUG-APPROVAL] event.set() done")

        timeout_task = self._timeout_tasks.pop(request_id, None)
        print("[DEBUG-APPROVAL] timeout_task:", timeout_task)
        if timeout_task is not None:
            timeout_task.cancel()
            print("[DEBUG-APPROVAL] timeout_task.cancel() done")

        # Clean up message refs and approval runtime ref (no longer needed).
        self._msg_refs.pop(request_id, None)
        self._bots.pop(request_id, None)
        getattr(self, "_approval_runtimes", {}).pop(request_id, None)
        print("[DEBUG-APPROVAL] resolve_callback EXIT (True)")

        return True


__all__ = ["ApprovalTracker", "parse_callback", "PREFIX"]
