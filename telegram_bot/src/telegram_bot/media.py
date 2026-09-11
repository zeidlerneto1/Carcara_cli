"""Handle Telegram media (photos, videos, documents) sent by the user.

The agent (KimiSoul) accepts ``list[ContentPart]`` as user input, where media
is represented by ``ImageURLPart`` / ``VideoURLPart`` using a data-URI
(``data:<mime>;base64,...``). The model must declare the matching capability
(``image_in`` / ``video_in``) or KimiSoul raises ``LLMNotSupported``.

This module downloads the media bytes via the Bot API and builds the wire
ContentParts, attaching any accompanying caption as a ``TextPart``.
"""

from __future__ import annotations

import base64
import logging
from types import SimpleNamespace
from typing import TYPE_CHECKING

from kosong.message import ImageURLPart, TextPart, VideoURLPart

from telegram_bot.model_switch import _supports_capability

if TYPE_CHECKING:
    from telegram import Bot
    from telegram_bot.session_manager import UserSession

logger = logging.getLogger(__name__)

# Max bytes of a single media file we are willing to inline as a data-URI.
# Keeps the wire message and the LLM request body from getting absurdly large.
MAX_MEDIA_BYTES = 20 * 1024 * 1024  # 20 MB


class MediaError(Exception):
    """Raised when media cannot be processed (unsupported capability, too big, etc.)."""


def _to_data_uri(data: bytes, mime_type: str) -> str:
    return f"data:{mime_type};base64,{base64.b64encode(data).decode('ascii')}"


def _check_capability(user_session: UserSession, capability: str, media_kind: str) -> None:
    """Raise MediaError if the active model does not support the given capability."""
    if not _supports_capability(user_session, capability):
        raise MediaError(
            f"O modelo atual não suporta {media_kind}. "
            f"Troque de modelo com /model (use um com capability '{capability}')."
        )


async def process_photo(
    bot: Bot,
    photo_file,
    caption: str | None,
    user_session: UserSession,
) -> list[object]:
    """Download a Telegram photo and build the wire ContentParts.

    Args:
        bot: The Telegram bot.
        photo_file: A telegram.PhotoSize object (largest size).
        caption: Optional caption text.
        user_session: The per-chat UserSession (for capability checks).

    Returns:
        List of ContentPart (TextPart + ImageURLPart).
    """
    _check_capability(user_session, "image_in", "imagens")

    mime_type = photo_file.mime_type or "image/jpeg"
    new_file = await bot.get_file(photo_file.file_id)
    file_obj = await new_file.download_as_bytearray()
    if len(file_obj) > MAX_MEDIA_BYTES:
        raise MediaError(
            f"Imagem grande demais ({len(file_obj) // 1024 // 1024} MB, "
            f"máx. {MAX_MEDIA_BYTES // 1024 // 1024} MB)."
        )
    logger.info("Downloaded photo %s (%s, %d bytes)", photo_file.file_id, mime_type, len(file_obj))

    parts: list[object] = []
    if caption:
        parts.append(TextPart(text=caption))
    parts.append(ImageURLPart(image_url=ImageURLPart.ImageURL(url=_to_data_uri(bytes(file_obj), mime_type))))
    return parts


async def process_video(
    bot: Bot,
    video_file,
    caption: str | None,
    user_session: UserSession,
) -> list[object]:
    """Download a Telegram video and build the wire ContentParts."""
    _check_capability(user_session, "video_in", "vídeos")

    mime_type = video_file.mime_type or "video/mp4"
    new_file = await bot.get_file(video_file.file_id)
    file_obj = await new_file.download_as_bytearray()
    if len(file_obj) > MAX_MEDIA_BYTES:
        raise MediaError(
            f"Vídeo grande demais ({len(file_obj) // 1024 // 1024} MB, "
            f"máx. {MAX_MEDIA_BYTES // 1024 // 1024} MB)."
        )
    logger.info("Downloaded video %s (%s, %d bytes)", video_file.file_id, mime_type, len(file_obj))

    parts: list[object] = []
    if caption:
        parts.append(TextPart(text=caption))
    parts.append(VideoURLPart(video_url=VideoURLPart.VideoURL(url=_to_data_uri(bytes(file_obj), mime_type))))
    return parts


async def process_document(
    bot: Bot,
    doc_file,
    caption: str | None,
    user_session: UserSession,
) -> list[object]:
    """Download a Telegram document and route by mime type.

    Images (png/jpeg/webp/gif) -> image_in; videos (mp4/webm/mov) -> video_in;
    anything else -> MediaError (not supported by the agent as media).
    """
    mime_type = (doc_file.mime_type or "").lower()
    caption_text = getattr(doc_file, "file_name", None) or caption

    image_prefixes = ("image/",)
    video_prefixes = ("video/",)

    if mime_type.startswith(image_prefixes):
        # Re-use the photo path by wrapping the document's file_id + mime.
        photo_like = SimpleNamespace(file_id=doc_file.file_id, mime_type=mime_type)
        return await process_photo(bot, photo_like, caption_text, user_session)
    if mime_type.startswith(video_prefixes):
        video_like = SimpleNamespace(file_id=doc_file.file_id, mime_type=mime_type)
        return await process_video(bot, video_like, caption_text, user_session)

    raise MediaError(
        f"Tipo de arquivo não suportado como mídia: '{mime_type or 'desconhecido'}'. "
        "Envie imagens ou vídeos."
    )


__all__ = ["MediaError", "MAX_MEDIA_BYTES", "process_photo", "process_video", "process_document"]
