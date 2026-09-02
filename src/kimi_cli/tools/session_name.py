"""Generates human-readable, memorable session names correlated with the topic.

Session IDs are opaque UUIDs (e.g. ``38867713eabe091f906f4fe1255312d7``) which
are the technical directory/continuity key. To make sessions easier to recognise
we derive a short, stable, human-readable *friendly name* from the first user
message, e.g. ``analisa-sessoes-captain``.

The friendly name is *stable*: for the same ``(first_user_text, session_id)``
pair it always returns the same result, so it can be persisted once and trusted
to not change across restarts.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import unicodedata

# Words that carry little topical meaning and are dropped from the topic slug.
_STOPWORDS = {
    # English
    "the",
    "a",
    "an",
    "and",
    "or",
    "of",
    "to",
    "in",
    "for",
    "on",
    "with",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "being",
    "it",
    "this",
    "that",
    "these",
    "those",
    "my",
    "your",
    "our",
    "their",
    "his",
    "her",
    "its",
    "do",
    "does",
    "did",
    "can",
    "could",
    "will",
    "would",
    "should",
    "shall",
    "me",
    "you",
    "us",
    "them",
    "i",
    "we",
    "he",
    "she",
    "they",
    "at",
    "by",
    "from",
    "about",
    "into",
    "over",
    "under",
    "as",
    "but",
    "if",
    "then",
    "so",
    "please",
    "help",
    "need",
    "want",
    "make",
    "get",
    "have",
    "has",
    # Portuguese
    "o",
    "os",
    "um",
    "uma",
    "uns",
    "umas",
    "de",
    "da",
    "dos",
    "das",
    "em",
    "no",
    "na",
    "nos",
    "nas",
    "para",
    "por",
    "com",
    "sem",
    "que",
    "quem",
    "qual",
    "quais",
    "como",
    "onde",
    "quando",
    "meu",
    "minha",
    "meus",
    "minhas",
    "seu",
    "sua",
    "seus",
    "suas",
    "eu",
    "voce",
    "você",
    "ele",
    "ela",
    "eles",
    "elas",
    "nós",
    "te",
    "lhe",
    "vos",
    "se",
    "é",
    "e",
    "ser",
    "estar",
    "ter",
    "haver",
    "fazer",
    "querer",
    "precisar",
    "porfavor",
    "por_favor",
    "favor",
    "pode",
    "poderia",
    "vou",
    "vamos",
    "vai",
    "sobre",
    "analisa",
    "analise",
    "verifica",
    "verifique",
    "mostra",
    "mostre",
    "diz",
    "diga",
    "cria",
    "crie",
    "faça",
    "faca",
    "faz",
    "faze",
    "ajuda",
}

# A small topical thesaurus: a stable hash of the topic picks one of these, so
# that the same topic reliably maps to the same flavour word while different
# topics spread across distinct words.
_THEME_WORDS: list[str] = [
    "captain",
    "engineer",
    "pilot",
    "scholar",
    "architect",
    "builder",
    "detective",
    "explorer",
    "gardener",
    "hunter",
    "inventor",
    "judge",
    "keeper",
    "librarian",
    "mechanic",
    "navigator",
    "oracle",
    "painter",
    "ranger",
    "scout",
    "tailor",
    "voyager",
    "warden",
    "weaver",
    "smith",
    "mason",
    "scribe",
    "sentry",
    "runner",
    "forger",
    "seeker",
    "mender",
]

# Regex for slugifying: keep latin letters, digits and single hyphens.
_SLUG_RE = re.compile(r"[^a-z0-9]+")
_HYPHEN_RE = re.compile(r"-{2,}")

# Max words kept from the topic portion of the name.
_MAX_TOPIC_WORDS = 2
# Max total length of the final friendly name (excluding collision suffix).
_MAX_NAME_LENGTH = 40


def _slugify(text: str) -> str:
    """Normalise text to a lowercase hyphenated slug (no accents/spaces)."""
    # Decompose accents (é -> e + combining) then drop combining marks.
    normalized = unicodedata.normalize("NFKD", text)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    ascii_text = ascii_text.lower()
    slug = _SLUG_RE.sub("-", ascii_text)
    slug = _HYPHEN_RE.sub("-", slug).strip("-")
    return slug


def extract_topic_words(text: str, *, max_words: int = _MAX_TOPIC_WORDS) -> list[str]:
    """Extract the most topic-bearing words from a user message.

    Splits the text into words, removes stopwords and very short tokens,
    then returns up to *max_words* of the remaining ones in order.
    """
    slug = _slugify(text)
    words = [w for w in slug.split("-") if len(w) > 2 and w not in _STOPWORDS]
    return words[:max_words]


def _theme_word(topic: str) -> str:
    """Pick a stable flavour word for a topic using a hash of the topic."""
    digest = hashlib.sha1(topic.encode("utf-8")).digest()  # noqa: S324 - not security
    index = int.from_bytes(digest[:4], "big") % len(_THEME_WORDS)
    return _THEME_WORDS[index]


def _base_name(topic: str) -> str:
    """Build ``<topic>-<theme-word>`` from a topic slug."""
    theme = _theme_word(topic)
    if not topic:
        return theme
    return f"{topic}-{theme}"


def generate_session_name(first_user_text: str, session_id: str) -> str:
    """Generate a stable, human-readable session name from the first user text.

    The result is deterministic for a given ``(first_user_text, session_id)``
    pair. A short collision suffix (``-<session_id[:4]>``) is appended when the
    base name exceeds ``_MAX_NAME_LENGTH`` or when the caller signals a
    collision (see ``with_collision_suffix``).
    """
    topic = "-".join(extract_topic_words(first_user_text))
    name = _base_name(topic)
    if not name:
        name = _base_name(_slugify(first_user_text))
    # Keep the name short and memorable.
    name = name[:_MAX_NAME_LENGTH].rstrip("-")
    return name


def with_collision_suffix(name: str, session_id: str) -> str:
    """Append a short, stable disambiguator for name collisions."""
    suffix = session_id[:4]
    # Reserve room for the suffix so the final name stays within limits.
    budget = _MAX_NAME_LENGTH - len(suffix) - 1  # 1 for the hyphen
    base = name[:budget].rstrip("-") if budget > 0 else name[:1]
    return f"{base}-{suffix}"


def random_session_name() -> str:
    """Generate a purely random (non-topic) friendly name as a fallback."""
    word = secrets.choice(_THEME_WORDS)
    token = secrets.token_hex(3)
    return f"{word}-{token}"
