"""Unit tests for Telegram media handling (capability checks + part building)."""

from __future__ import annotations

import asyncio
import base64
from types import SimpleNamespace

from kosong.message import ImageURLPart, TextPart, VideoURLPart

from telegram_bot.media import MediaError, process_document, process_photo, process_video
from telegram_bot.model_switch import _supports_capability

_SAMPLE_PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 100


def _user_session(caps: set):
    """Fake UserSession whose active model has the given capabilities."""
    llm = SimpleNamespace(capabilities=caps, chat_provider=SimpleNamespace(thinking_effort=None))
    runtime = SimpleNamespace(llm=llm)
    cli = SimpleNamespace(_runtime=runtime)
    return SimpleNamespace(cli=cli)


def _make_file(file_id: str, mime: str, data: bytes):
    """Fake telegram media file: has .file_id/.mime_type and a downloadable File."""

    class _Media:
        pass

    m = _Media()
    m.file_id = file_id
    m.mime_type = mime

    async def _dl():
        return bytearray(data)

    m_file = SimpleNamespace(download_as_bytearray=_dl)
    return m, m_file


def _make_bot(data: bytes):
    """Fake Bot.get_file returning a File that downloads ``data``."""

    async def _get_file(file_id):
        m, m_file = _make_file(file_id, "", data)
        return m_file

    return SimpleNamespace(get_file=_get_file)


def test_supports_capability_flag():
    assert _supports_capability(_user_session({"image_in"}), "image_in") is True
    assert _supports_capability(_user_session(set()), "image_in") is False
    assert _supports_capability(_user_session({"video_in"}), "video_in") is True


def test_process_photo_rejects_when_no_capability():
    async def main():
        bot = _make_bot(_SAMPLE_PNG)
        photo, _ = _make_file("p1", "image/png", _SAMPLE_PNG)
        user = _user_session(set())  # no image_in
        try:
            await process_photo(bot, photo, None, user)
            assert False, "expected MediaError"
        except MediaError as e:
            assert "imagens" in str(e)

    asyncio.run(main())


def test_process_photo_builds_data_uri():
    async def main():
        bot = _make_bot(_SAMPLE_PNG)
        photo, _ = _make_file("p1", "image/png", _SAMPLE_PNG)
        user = _user_session({"image_in"})
        parts = await process_photo(bot, photo, "olha", user)
        assert isinstance(parts[0], TextPart)
        assert parts[0].text == "olha"
        assert isinstance(parts[1], ImageURLPart)
        url = parts[1].image_url.url
        assert url.startswith("data:image/png;base64,")
        b64 = url.split("base64,")[1]
        assert base64.b64decode(b64) == _SAMPLE_PNG

    asyncio.run(main())


def test_process_photo_without_caption_is_single_part():
    async def main():
        bot = _make_bot(_SAMPLE_PNG)
        photo, _ = _make_file("p1", "image/png", _SAMPLE_PNG)
        user = _user_session({"image_in"})
        parts = await process_photo(bot, photo, None, user)
        assert len(parts) == 1
        assert isinstance(parts[0], ImageURLPart)

    asyncio.run(main())


def test_process_video_rejects_when_no_capability():
    async def main():
        bot = _make_bot(b"\x00\x01\x02")
        video, _ = _make_file("v1", "video/mp4", b"\x00\x01\x02")
        user = _user_session({"image_in"})  # has image but not video
        try:
            await process_video(bot, video, None, user)
            assert False, "expected MediaError"
        except MediaError as e:
            assert "vídeos" in str(e)

    asyncio.run(main())


def test_process_video_builds_data_uri():
    async def main():
        bot = _make_bot(b"\x00\x01\x02")
        video, _ = _make_file("v1", "video/mp4", b"\x00\x01\x02")
        user = _user_session({"video_in"})
        parts = await process_video(bot, video, "clip", user)
        assert isinstance(parts[0], TextPart)
        assert isinstance(parts[1], VideoURLPart)
        assert parts[1].video_url.url.startswith("data:video/mp4;base64,")

    asyncio.run(main())


def test_process_document_routes_image_by_mime():
    async def main():
        bot = _make_bot(_SAMPLE_PNG)
        doc, _ = _make_file("d1", "image/jpeg", _SAMPLE_PNG)
        user = _user_session({"image_in"})
        parts = await process_document(bot, doc, None, user)
        assert isinstance(parts[-1], ImageURLPart)

    asyncio.run(main())


def test_process_document_rejects_non_media():
    async def main():
        bot = _make_bot(b"%PDF-1.4")
        doc, _ = _make_file("d1", "application/pdf", b"%PDF-1.4")
        user = _user_session({"image_in", "video_in"})
        try:
            await process_document(bot, doc, None, user)
            assert False, "expected MediaError"
        except MediaError as e:
            assert "não suportado" in str(e)

    asyncio.run(main())
