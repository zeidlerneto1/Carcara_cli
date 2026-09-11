"""The Telegram bot application: handlers and wiring.

Sets up the python-telegram-bot Application, registers message/command/callback
handlers, and ties together the session manager, approval tracker, question
tracker and turn runner.
"""

from __future__ import annotations

import contextlib
import logging
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from telegram import Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from telegram_bot import approvals, media, model_switch, questions
from telegram_bot.audio import VoiceTranscriber
from telegram_bot.runner import TurnRunner
from telegram_bot.session_manager import SessionManager

if TYPE_CHECKING:
    from telegram_bot.config import BotConfig

logger = logging.getLogger(__name__)

HELP_TEXT = (
    "🤖 <b>Carcará</b> — agente via Telegram\n\n"
    "Envie uma mensagem (texto, 🎤 voz, 🖼️ imagem ou 🎬 vídeo) e eu processarei com o agente Carcará.\n\n"
    "<b>Comandos:</b>\n"
    "/start — iniciar / mostrar ajuda\n"
    "/help — esta ajuda\n"
    "/new — nova sessão (limpa contexto atual)\n"
    "/model — trocar o modelo em uso (lista os disponíveis)\n"
    "/think — ligar/desligar o modo de pensamento (se suportado)\n"
    "/status — mostrar modelo, pensamento e aprovação atuais\n"
    "/yolo — alternar aprovação automática\n"
    "/cancel — cancelar turno em andamento\n"
    "/clear — limpar contexto (delegado ao agente)\n\n"
    "<b>🎤 Voz:</b> envie um áudio e eu transcrevo (VOSK offline) e respondo.\n"
    "<b>🖼️ Imagem / 🎬 Vídeo:</b> envie que eu analiso — desde que o modelo atual\n"
    "tenha suporte (capabilities 'image_in' / 'video_in'). Use /model para trocar.\n\n"
    "Comandos do agente (ex: /compact, /plan, /skill:...) são passados "
    "diretamente ao agente."
)


async def _build_runner(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
) -> tuple["TurnRunner", bool] | None:
    """Fetch-or-create the user session and build a TurnRunner.

    Returns ``(runner, yolo)`` or ``None`` if the chat is busy.
    """
    config: BotConfig = context.bot_data["config"]
    manager: SessionManager = context.bot_data["session_manager"]
    yolo_users: dict[int, bool] = context.bot_data.setdefault("yolo_users", {})
    if chat_id not in yolo_users:
        yolo_users[chat_id] = config.default_approval == "yolo"
    yolo = yolo_users[chat_id]
    user = await manager.get_or_create(chat_id, yolo=yolo)
    if user.busy:
        return None
    approvals_tracker: approvals.ApprovalTracker = context.bot_data["approvals"]
    questions_tracker: questions.QuestionTracker = context.bot_data["questions"]
    runner = TurnRunner(
        context.bot,
        chat_id,
        user,
        config,
        approvals_tracker,
        questions_tracker,
    )
    return runner, yolo


async def _run_input(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_input: str | list,
    busy_note: str = "⏳ Já estou processando uma mensagem. Aguarde...",
) -> None:
    """Build a runner and run ``user_input`` (str or list[ContentPart])."""
    chat_id = update.effective_chat.id
    built = await _build_runner(update, context, chat_id)
    if built is None:
        await update.message.reply_text(busy_note)
        return
    runner, _yolo = built
    context.bot_data["active_runners"][chat_id] = runner
    try:
        await runner.run(user_input)
    finally:
        context.bot_data["active_runners"].pop(chat_id, None)


async def _handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    text = (update.message.text or "").strip()
    if not text:
        return

    # If a question is awaiting free text, route the reply there.
    questions_tracker: questions.QuestionTracker = context.bot_data["questions"]
    if questions_tracker.is_awaiting_free_text_for_chat(chat_id):
        request_id = questions_tracker.free_text_request_for_chat(chat_id)
        if request_id:
            await questions_tracker.on_free_text(context.bot, chat_id, request_id, text)
            return

    await _run_input(update, context, text)


async def _handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle a photo (with optional caption): send it to the agent as media."""
    chat_id = update.effective_chat.id
    message = update.message
    if not message.photo:
        return
    photo = message.photo[-1]  # largest size
    caption = message.caption
    status_msg = await message.reply_text("🖼️ Recebi a imagem, processando...")
    try:
        parts = await media.process_photo(context.bot, photo, caption,
                                          await _get_user(context, chat_id))
    except media.MediaError as e:
        await status_msg.edit_text(f"⚠️ {e}")
        return
    except Exception as e:
        logger.exception("Failed to process photo for chat %s", chat_id)
        await status_msg.edit_text(f"❌ Erro ao processar a imagem: {e}")
        return
    # Delete the status, then run the turn with the media parts.
    with contextlib.suppress(Exception):
        await status_msg.delete()
    await _run_input(update, context, parts)


async def _handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle a video (with optional caption): send it to the agent as media."""
    chat_id = update.effective_chat.id
    message = update.message
    video = message.video
    if video is None:
        return
    caption = message.caption
    status_msg = await message.reply_text("🎬 Recebi o vídeo, processando...")
    try:
        parts = await media.process_video(context.bot, video, caption,
                                          await _get_user(context, chat_id))
    except media.MediaError as e:
        await status_msg.edit_text(f"⚠️ {e}")
        return
    except Exception as e:
        logger.exception("Failed to process video for chat %s", chat_id)
        await status_msg.edit_text(f"❌ Erro ao processar o vídeo: {e}")
        return
    with contextlib.suppress(Exception):
        await status_msg.delete()
    await _run_input(update, context, parts)


async def _handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle a document (image/video by mime): send to the agent as media."""
    chat_id = update.effective_chat.id
    message = update.message
    doc = message.document
    if doc is None:
        return
    caption = message.caption
    status_msg = await message.reply_text("📄 Recebi o arquivo, processando...")
    try:
        parts = await media.process_document(context.bot, doc, caption,
                                             await _get_user(context, chat_id))
    except media.MediaError as e:
        await status_msg.edit_text(f"⚠️ {e}")
        return
    except Exception as e:
        logger.exception("Failed to process document for chat %s", chat_id)
        await status_msg.edit_text(f"❌ Erro ao processar o arquivo: {e}")
        return
    with contextlib.suppress(Exception):
        await status_msg.delete()
    await _run_input(update, context, parts)


async def _get_user(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """Fetch-or-create the UserSession for a chat (used for capability checks)."""
    config: BotConfig = context.bot_data["config"]
    manager: SessionManager = context.bot_data["session_manager"]
    yolo_users: dict[int, bool] = context.bot_data.setdefault("yolo_users", {})
    if chat_id not in yolo_users:
        yolo_users[chat_id] = config.default_approval == "yolo"
    return await manager.get_or_create(chat_id, yolo=yolo_users[chat_id])


async def _handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle a voice message: transcribe it and route to the agent as text."""
    chat_id = update.effective_chat.id
    voice = update.message.voice
    transcriber: VoiceTranscriber = context.bot_data["transcriber"]

    # Show a typing indicator while transcribing + processing.
    await update.message.chat.send_action("typing")
    status_msg = await update.message.reply_text("🎧 Ouvindo sua voz...")

    try:
        text = await transcriber.transcribe(context.bot, voice)
    except Exception as e:
        logger.exception("Voice transcription failed for chat %s", chat_id)
        await status_msg.edit_text(f"❌ Falha na transcrição: {e}")
        return

    if not text:
        await status_msg.edit_text("🤷 Não entendi o que você disse. Tente novamente ou digite.")
        return

    await status_msg.edit_text(f"🗣️ Ouvi: \"{text}\"")

    # Route the transcribed text through the same path as typed text.
    # Build a synthetic text update so _handle_text can reuse it.
    config: BotConfig = context.bot_data["config"]
    manager: SessionManager = context.bot_data["session_manager"]
    approvals_tracker: approvals.ApprovalTracker = context.bot_data["approvals"]
    questions_tracker: questions.QuestionTracker = context.bot_data["questions"]

    yolo_users: dict[int, bool] = context.bot_data.setdefault("yolo_users", {})
    if chat_id not in yolo_users:
        yolo_users[chat_id] = config.default_approval == "yolo"
    yolo: bool = yolo_users[chat_id]
    user = await manager.get_or_create(chat_id, yolo=yolo)

    if user.busy:
        await status_msg.edit_text("⏳ Já estou processando outra mensagem. Aguarde...")
        return

    runner = TurnRunner(
        context.bot,
        chat_id,
        user,
        config,
        approvals_tracker,
        questions_tracker,
    )
    context.bot_data["active_runners"][chat_id] = runner
    try:
        await runner.run(text)
    finally:
        context.bot_data["active_runners"].pop(chat_id, None)


async def _cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(HELP_TEXT, parse_mode="HTML")


async def _cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(HELP_TEXT, parse_mode="HTML")


async def _cmd_new(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    manager: SessionManager = context.bot_data["session_manager"]
    chat_id = update.effective_chat.id
    await manager.new_session(chat_id)
    await update.message.reply_text("🧹 Nova sessão iniciada. Contexto anterior descartado.")


async def _cmd_yolo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    yolo_users: dict[int, bool] = context.bot_data["yolo_users"]
    current = yolo_users.get(chat_id, False)
    yolo_users[chat_id] = not current
    # Recreate the user session with the new approval mode.
    manager: SessionManager = context.bot_data["session_manager"]
    await manager.get_or_create(chat_id, yolo=not current)
    state = "🟢 Aprovação automática ATIVADA" if not current else "🔴 Aprovação manual"
    await update.message.reply_text(state)


async def _cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    runner = context.bot_data.get("active_runners", {}).get(chat_id)
    if runner is not None:
        runner.cancel_event.set()
        await update.message.reply_text("⏹️ Cancelando...")
    else:
        await update.message.reply_text("Nada em execução.")


async def _cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Delegate to the agent by passing the raw /clear command through.
    await _handle_text(update, context)


async def _cmd_model(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/model — show the available models as an inline keyboard.

    Clicking a button switches the active LLM in place (context preserved).
    """
    chat_id = update.effective_chat.id
    config: BotConfig = context.bot_data["config"]
    manager: SessionManager = context.bot_data["session_manager"]

    yolo_users: dict[int, bool] = context.bot_data.setdefault("yolo_users", {})
    yolo = yolo_users.get(chat_id, config.default_approval == "yolo")
    user = await manager.get_or_create(chat_id, yolo=yolo)

    current_alias = model_switch.current_model_alias(user)
    keyboard = model_switch.build_model_keyboard(config.get_carcara_config(), current_alias)

    await update.message.reply_text(
        "🧩 <b>Escolha o modelo</b> (o contexto é preservado):", parse_mode="HTML"
    )
    await context.bot.send_message(chat_id, "", reply_markup=keyboard)


async def _cmd_think(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/think — toggle thinking on/off if the current model supports it."""
    chat_id = update.effective_chat.id
    manager: SessionManager = context.bot_data["session_manager"]
    yolo_users: dict[int, bool] = context.bot_data.setdefault("yolo_users", {})
    yolo = yolo_users.get(chat_id, False)
    user = await manager.get_or_create(chat_id, yolo=yolo)

    runtime = getattr(user.cli, "_runtime", None)
    if runtime is None:
        await update.message.reply_text("❌ Sessão não inicializada.")
        return

    if not model_switch._supports_thinking(user):
        await update.message.reply_text(
            "🚫 O modelo atual não suporta modo de pensamento. "
            "Troque de modelo com /model."
        )
        return

    now_on = model_switch.current_thinking_on(user)
    next_state = not now_on
    # Force the thinking state on the current model.
    alias = model_switch.current_model_alias(user) or runtime.config.default_model
    display, thinking_now = await model_switch.switch_model(user, alias, thinking=next_state)
    emoji = "🧠" if thinking_now else "💤"
    await update.message.reply_text(
        f"{emoji} Pensamento {'LIGADO' if thinking_now else 'DESLIGADO'} no {display}."
    )


async def _cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/status — show current model, thinking, and approval state."""
    chat_id = update.effective_chat.id
    config: BotConfig = context.bot_data["config"]
    manager: SessionManager = context.bot_data["session_manager"]
    yolo_users: dict[int, bool] = context.bot_data.setdefault("yolo_users", {})
    yolo = yolo_users.get(chat_id, config.default_approval == "yolo")
    user = await manager.get_or_create(chat_id, yolo=yolo)

    text = model_switch.build_status_text(config.get_carcara_config(), user, yolo=yolo)
    await update.message.reply_text(text, parse_mode="HTML")


async def _handle_model_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    alias: str,
) -> None:
    """Switch the active model when a /model keyboard button is clicked."""
    chat_id = update.effective_chat.id
    query = update.callback_query
    config: BotConfig = context.bot_data["config"]
    manager: SessionManager = context.bot_data["session_manager"]
    yolo_users: dict[int, bool] = context.bot_data.setdefault("yolo_users", {})
    yolo = yolo_users.get(chat_id, config.default_approval == "yolo")
    user = await manager.get_or_create(chat_id, yolo=yolo)

    try:
        display, thinking_now = await model_switch.switch_model(user, alias)
    except ValueError as e:
        with contextlib.suppress(Exception):
            await query.edit_message_text(f"❌ {e}")
        return

    # Rebuild the keyboard so the check mark moves to the new model.
    keyboard = model_switch.build_model_keyboard(config.get_carcara_config(), alias)
    status = "🧠" if thinking_now else "💤"
    with contextlib.suppress(Exception):
        await query.edit_message_text(
            f"{status} Modelo ativo: <b>{display}</b>",
            parse_mode="HTML",
            reply_markup=keyboard,
        )


async def _cmd_restart(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Privileged /restart: reinicia o bot imediatamente, ignorando a fila.

    Somente usuários listados em ``CARCARA_ADMIN_IDS`` podem executar. O comando é
    tratado diretamente (CommandHandler), sem passar pela fila de processamento.
    """
    config: BotConfig = context.bot_data["config"]
    user = update.effective_user
    user_id = user.id if user is not None else None

    if user_id is None or user_id not in config.admin_ids:
        logger.warning("Restart blocked for unauthorized user id=%s", user_id)
        with contextlib.suppress(Exception):
            await update.message.reply_text("🚫 Você não tem permissão para reiniciar o bot.")
        return

    # Cancel any in-flight turn so the restart is not stuck behind it.
    runner = context.bot_data.get("active_runners", {}).get(update.effective_chat.id)
    if runner is not None:
        runner.cancel_event.set()

    await update.message.reply_text("🔄 Reiniciando o bot...")
    logger.info("Restart requested by admin %s", user_id)
    _trigger_restart()


def _trigger_restart() -> None:
    """Relança o bot em um processo desanexado (via restart.sh).

    O script restart.sh mata o processo atual e sobe um novo em background.
    """
    # Localiza restart.sh na raiz do telegram_bot (src/telegram_bot/bot.py -> telegram_bot/).
    script = Path(__file__).resolve().parents[2] / "restart.sh"
    if not script.exists():
        logger.error("restart.sh not found at %s; cannot restart", script)
        return
    try:
        subprocess.Popen(
            ["bash", str(script)],
            start_new_session=True,  # desanexa para sobreviver ao kill do bot.
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        logger.error("Failed to launch restart script: %s", exc)


async def _on_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    print("\n" + "=" * 60)
    print("[DEBUG] _on_callback_query ENTER")
    print("[DEBUG] update:", update)
    query = update.callback_query
    print("[DEBUG] query:", query)
    print("[DEBUG] query.data:", query.data if query else None)
    if query is None:
        print("[DEBUG] query is None, RETURNING")
        return

    chat_id = update.effective_chat.id
    data = query.data or ""
    print("[DEBUG] chat_id:", chat_id)
    print("[DEBUG] data:", data)
    logger.debug("Callback received - chat: %s, data: %s", chat_id, data)

    try:
        # Answer the callback to avoid timeout.
        print("[DEBUG] calling query.answer()...")
        with contextlib.suppress(Exception):
            await query.answer()
        print("[DEBUG] query.answer() done")
    except Exception as e:
        logger.debug("Failed to answer callback: %s", e)

    try:
        # Approval callback
        print("[DEBUG] parsing approval callback...")
        parsed = approvals.parse_callback(data)
        print("[DEBUG] parsed:", parsed)
        if parsed is not None:
            request_id, action = parsed
            print("[DEBUG] request_id:", request_id)
            print("[DEBUG] action:", action)
            tracker: approvals.ApprovalTracker = context.bot_data.get("approvals")
            print("[DEBUG] tracker:", tracker)
            if tracker is None:
                print("[DEBUG] tracker is None, RETURNING")
                logger.error("ApprovalTracker not found")
                with contextlib.suppress(Exception):
                    await query.edit_message_text("❌ Erro interno: tracker não encontrado")
                return

            action_labels = {
                "approve": "✅ Aprovado",
                "session": "✅ Aprovado para esta sessão",
                "reject": "❌ Rejeitado",
            }

            print("[DEBUG] calling tracker.resolve_callback(...)")
            resolved = tracker.resolve_callback(request_id, action)
            print("[DEBUG] resolve_callback returned:", resolved)
            if resolved:
                logger.info("Approval resolved: %s -> %s", request_id, action)
                print("[DEBUG] editing message with label:", action_labels.get(action, "✅ Respondido"))
                with contextlib.suppress(Exception):
                    await query.edit_message_text(
                        action_labels.get(action, "✅ Respondido"),
                        reply_markup=None,
                    )
                print("[DEBUG] edit_message_text done")
            else:
                # Late callback: the request was already resolved by timeout.
                # Still update the UI so the user sees what happened.
                logger.warning("Late approval callback for already-resolved request: %s", request_id)
                print("[DEBUG] LATE CALLBACK — request already resolved by timeout")
                with contextlib.suppress(Exception):
                    await query.edit_message_text(
                        "⏳ Esta aprovação expirou antes do clique.",
                        reply_markup=None,
                    )
                print("[DEBUG] edit_message_text done (late)")
            print("[DEBUG] approval callback handled, RETURNING")
            return

        # Model-switch callback (from /model keyboard)
        parsed_model = model_switch.parse_callback(data)
        if parsed_model is not None:
            logger.info("Model switch callback: %s (chat %s)", parsed_model, chat_id)
            await _handle_model_callback(update, context, parsed_model)
            return

        # Question callback
        print("[DEBUG] parsing question callback...")
        parsed_q = questions.parse_callback(data)
        print("[DEBUG] parsed_q:", parsed_q)
        if parsed_q is not None:
            request_id, item_idx, opt_idx, action = parsed_q
            print("[DEBUG] question request_id:", request_id)
            print("[DEBUG] question item_idx:", item_idx)
            print("[DEBUG] question opt_idx:", opt_idx)
            print("[DEBUG] question action:", action)
            tracker: questions.QuestionTracker = context.bot_data.get("questions")
            print("[DEBUG] question tracker:", tracker)
            if tracker is None:
                print("[DEBUG] question tracker is None, RETURNING")
                logger.error("QuestionTracker not found")
                with contextlib.suppress(Exception):
                    await query.edit_message_text("❌ Erro interno: tracker não encontrado")
                return

            if action == "s":
                print("[DEBUG] calling tracker.on_select(...)")
                await tracker.on_select(context.bot, chat_id, request_id, item_idx, opt_idx)
                print("[DEBUG] on_select done")
            elif action == "c":
                print("[DEBUG] calling tracker.on_confirm(...)")
                await tracker.on_confirm(context.bot, chat_id, request_id, item_idx)
                print("[DEBUG] on_confirm done")
            elif action == "o":
                print("[DEBUG] calling tracker.on_other(...)")
                await tracker.on_other(context.bot, chat_id, request_id, item_idx)
                print("[DEBUG] on_other done")
            print("[DEBUG] question callback handled, RETURNING")
            return

        logger.warning("Unknown callback: %s", data)
        print("[DEBUG] Unknown callback, editing message")
        with contextlib.suppress(Exception):
            await query.edit_message_text("⚠️ Comando não reconhecido.")
        print("[DEBUG] edit_message_text done (unknown)")

    except Exception as e:
        print("[DEBUG] EXCEPTION in _on_callback_query:", e, flush=True)
        logger.exception("Error processing callback: %s", e)
        with contextlib.suppress(Exception):
            await query.edit_message_text("❌ Erro ao processar sua solicitação.")
        print("[DEBUG] exception handled, RETURNING")
    print("[DEBUG] _on_callback_query EXIT")
    print("=" * 60 + "\n")


async def _on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Bot error: %s", context.error)
    if update is not None and isinstance(update, Update) and update.effective_chat is not None:
        with contextlib.suppress(Exception):
            await update.effective_chat.send_message("❌ Ocorreu um erro interno. Tente novamente.")


def build_application(config: BotConfig) -> Application:
    """Build and return the telegram-bot Application."""
    manager = SessionManager(config)
    approvals_tracker = approvals.ApprovalTracker(timeout_s=config.approval_timeout)
    questions_tracker = questions.QuestionTracker(timeout_s=config.approval_timeout)

    app = (
        ApplicationBuilder()
        .token(config.token)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )

    app.bot_data["config"] = config
    app.bot_data["session_manager"] = manager
    app.bot_data["approvals"] = approvals_tracker
    app.bot_data["questions"] = questions_tracker
    app.bot_data["transcriber"] = VoiceTranscriber()
    app.bot_data["yolo_users"] = {}
    app.bot_data["active_runners"] = {}

    app.add_handler(CommandHandler("start", _cmd_start))
    app.add_handler(CommandHandler("help", _cmd_help))
    app.add_handler(CommandHandler("new", _cmd_new))
    app.add_handler(CommandHandler("model", _cmd_model))
    app.add_handler(CommandHandler("think", _cmd_think))
    app.add_handler(CommandHandler("status", _cmd_status))
    app.add_handler(CommandHandler("yolo", _cmd_yolo))
    app.add_handler(CommandHandler("cancel", _cmd_cancel))
    app.add_handler(CommandHandler("clear", _cmd_clear))
    app.add_handler(CommandHandler("restart", _cmd_restart))
    app.add_handler(CallbackQueryHandler(_on_callback_query))
    app.add_handler(MessageHandler(filters.VOICE, _handle_voice))
    app.add_handler(MessageHandler(filters.PHOTO, _handle_photo))
    app.add_handler(MessageHandler(filters.VIDEO, _handle_video))
    app.add_handler(MessageHandler(filters.Document.ALL, _handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _handle_text))
    app.add_error_handler(_on_error)

    return app


async def _post_init(app: Application) -> None:
    logger.info("Carcará Telegram bot started.")
    await app.bot.set_my_commands(
        [
            ("start", "Iniciar / mostrar ajuda"),
            ("new", "Nova sessão"),
            ("model", "Trocar o modelo"),
            ("think", "Ligar/desligar pensamento"),
            ("status", "Status: modelo, pensamento, aprovação"),
            ("yolo", "Alternar aprovação automática"),
            ("cancel", "Cancelar turno"),
            ("restart", "Reiniciar o bot (admin)"),
            ("help", "Ajuda"),
        ]
    )


async def _post_shutdown(app: Application) -> None:
    manager: SessionManager | None = app.bot_data.get("session_manager")
    if manager is not None:
        await manager.shutdown()
    logger.info("Carcará Telegram bot stopped.")


__all__ = ["build_application", "HELP_TEXT"]