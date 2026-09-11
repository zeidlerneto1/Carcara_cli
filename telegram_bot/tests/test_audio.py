"""Unit tests for the audio transcription module (temp dir pruning, cleanup)."""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

from telegram_bot.audio import VoiceTranscriber


def _make_stale_dir(tmp: Path, name: str, with_audio: bool = True, age_s: float = 0.0) -> Path:
    """Create a fake stale tg_voice_* dir under tmp, optionally with an audio file."""
    d = tmp / name
    d.mkdir()
    if with_audio:
        (d / "input.ogg").write_bytes(b"fake-ogg")
        (d / "input.wav").write_bytes(b"fake-wav")
    # Backdate mtime to simulate age.
    ts = time.time() - age_s
    for p in [d, *d.iterdir()]:
        import os

        os.utime(p, (ts, ts))
    return d


def test_prune_removes_dirs_beyond_max():
    """When more than _MAX_STALE_DIRS exist, the oldest ones are removed."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        # Monkeypatch tempfile.gettempdir to return our tmp so the glob hits it.
        import telegram_bot.audio as audio_mod

        orig = audio_mod.tempfile.gettempdir
        audio_mod.tempfile.gettempdir = lambda: str(tmp)
        try:
            # Create 6 stale dirs (max is 4) -> should remove the 2 oldest.
            for i in range(6):
                _make_stale_dir(tmp, f"tg_voice_dir{i:02d}", age_s=float(6 - i))
            # Newest 2 have age 0, oldest 4 have higher age.
            vt = VoiceTranscriber()
            vt._prune_stale_dirs()
            remaining = sorted(p.name for p in tmp.glob("tg_voice_*") if p.is_dir())
            # Should keep at most _MAX_STALE_DIRS.
            assert len(remaining) <= vt._MAX_STALE_DIRS, remaining
            # The oldest (highest age) should be gone.
            assert "tg_voice_dir00" not in remaining
            assert "tg_voice_dir01" not in remaining
        finally:
            audio_mod.tempfile.gettempdir = orig


def test_prune_noop_when_under_max():
    """When at or under _MAX_STALE_DIRS, nothing is removed."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        import telegram_bot.audio as audio_mod

        orig = audio_mod.tempfile.gettempdir
        audio_mod.tempfile.gettempdir = lambda: str(tmp)
        try:
            for i in range(3):  # under max of 4
                _make_stale_dir(tmp, f"tg_voice_keep{i:02d}")
            vt = VoiceTranscriber()
            vt._prune_stale_dirs()
            remaining = sorted(p.name for p in tmp.glob("tg_voice_*") if p.is_dir())
            assert len(remaining) == 3, remaining
        finally:
            audio_mod.tempfile.gettempdir = orig


def test_remove_dir_cleans_contents():
    """_remove_dir removes the dir and its files, and tolerates missing dirs."""
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp) / "tg_voice_test"
        d.mkdir()
        (d / "a.ogg").write_bytes(b"x")
        (d / "b.wav").write_bytes(b"y")
        vt = VoiceTranscriber()
        vt._remove_dir(d)
        assert not d.exists()
        # Removing a non-existent dir should not raise.
        vt._remove_dir(d)  # no-op
        vt._remove_dir(Path(tmp) / "does-not-exist")  # no-op
