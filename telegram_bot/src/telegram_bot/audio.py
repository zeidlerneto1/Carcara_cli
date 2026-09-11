"""Voice message transcription (speech-to-text) using VOSK offline.

The user sends a Telegram voice message (.ogg/Opus). We download it, convert
it to 16 kHz mono WAV with ffmpeg, then transcribe locally with the VOSK
PT-BR model. No internet or API key required at transcription time.
"""

from __future__ import annotations

import json
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from vosk import KaldiRecognizer, Model

if TYPE_CHECKING:
    from telegram import Bot

logger = logging.getLogger(__name__)

# Default model path relative to the project root.
_MODEL_DIR = Path(__file__).resolve().parent.parent.parent / "models" / "vosk-model-small-pt-0.3"

# Sample rate VOSK was trained on.
_SAMPLE_RATE = 16000


def _model_path() -> Path:
    """Return the VOSK model directory, honoring an env override."""
    import os

    override = os.getenv("CARCARA_VOSK_MODEL")
    if override:
        p = Path(override).expanduser()
        if p.is_dir():
            return p
    return _MODEL_DIR


class VoiceTranscriber:
    """Transcribes Telegram voice messages to text via VOSK (offline).

    The VOSK Model is loaded once and reused (it is thread-affine, so each
    transcribe call creates a fresh KaldiRecognizer on the shared model).

    Temp files are written to a unique /tmp/tg_voice_* directory per call and
    removed in the ``finally`` block. Additionally, stale directories are
    pruned on each call to avoid litter from crashed runs.
    """

    #: Number of stale /tmp/tg_voice_* dirs allowed to accumulate before pruning.
    _MAX_STALE_DIRS = 4

    def __init__(self, model_path: Path | None = None) -> None:
        self._model_path = model_path or _model_path()
        self._model: Model | None = None

    def _ensure_model(self) -> Model:
        if self._model is None:
            logger.info("Loading VOSK model from %s", self._model_path)
            self._model = Model(str(self._model_path))
            logger.info("VOSK model loaded.")
        return self._model

    def _prune_stale_dirs(self) -> None:
        """Remove old /tmp/tg_voice_* directories.

        Keeps at most ``_MAX_STALE_DIRS`` most-recent dirs; removes the rest.
        Only touches directories that match our prefix and are not currently
        in use (i.e. not the one we just created for this transcribe call).
        """
        tmp_root = Path(tempfile.gettempdir())
        try:
            dirs = sorted(
                (p for p in tmp_root.glob("tg_voice_*") if p.is_dir()),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            return

        if len(dirs) <= self._MAX_STALE_DIRS:
            return

        to_remove = dirs[self._MAX_STALE_DIRS :]
        for d in to_remove:
            try:
                for child in d.iterdir():
                    if child.is_file():
                        child.unlink()
                d.rmdir()
                logger.info("Pruned stale temp dir: %s", d)
            except OSError as e:
                logger.warning("Could not prune %s: %s", d, e)

    async def transcribe(self, bot: Bot, voice_file) -> str:
        """Download a voice file and return its transcription.

        Args:
            bot: The Telegram bot (used to fetch the file bytes).
            voice_file: A telegram.Voice object (has .file_id, .mime_type, .duration).

        Returns:
            The transcribed text (may be empty if no speech was detected).
        """
        model = self._ensure_model()

        # Prune any stale temp dirs from previous (possibly crashed) runs.
        self._prune_stale_dirs()

        # Download the voice file to a temp file.
        tmp_dir = tempfile.mkdtemp(prefix="tg_voice_")
        ogg_path = Path(tmp_dir) / "input.ogg"
        wav_path = Path(tmp_dir) / "input.wav"

        try:
            new_file = await bot.get_file(voice_file.file_id)
            await new_file.download_to_drive(ogg_path)
            logger.info(
                "Downloaded voice file: %s (%s, %ss)",
                voice_file.file_id,
                voice_file.mime_type,
                voice_file.duration,
            )

            # Convert to 16 kHz mono WAV.
            self._convert_to_wav(ogg_path, wav_path)
            logger.info("Converted to WAV 16kHz mono: %s", wav_path)

            # Transcribe.
            text = self._transcribe_wav(model, wav_path)
            logger.info("Transcription result: %r", text)
            return text.strip()
        finally:
            # Clean up temp files and the directory itself.
            self._remove_dir(tmp_dir)

    @staticmethod
    def _remove_dir(d: str | Path) -> None:
        """Remove a temp directory and all its contents, ignoring errors."""
        p = Path(d)
        try:
            if not p.exists():
                return
            for child in p.iterdir():
                if child.is_file() or child.is_symlink():
                    child.unlink()
            p.rmdir()
        except OSError as e:
            logger.warning("Could not remove temp dir %s: %s", p, e)

    @staticmethod
    def _convert_to_wav(src: Path, dst: Path) -> None:
        """Convert an audio file to 16 kHz mono WAV using ffmpeg."""
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(src),
            "-ar",
            str(_SAMPLE_RATE),
            "-ac",
            "1",
            "-f",
            "wav",
            str(dst),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg conversion failed: {result.stderr}")

    def _transcribe_wav(self, model: Model, wav_path: Path) -> str:
        """Run VOSK on a 16 kHz mono WAV file and return the text."""
        rec = KaldiRecognizer(model, _SAMPLE_RATE)
        with open(wav_path, "rb") as f:
            data = f.read()

        # Feed in chunks; VOSK processes 4096-byte frames.
        chunk_size = 4096
        for i in range(0, len(data), chunk_size):
            rec.AcceptWaveform(data[i : i + chunk_size])

        result = json.loads(rec.FinalResult())
        return result.get("text", "")


__all__ = ["VoiceTranscriber"]
