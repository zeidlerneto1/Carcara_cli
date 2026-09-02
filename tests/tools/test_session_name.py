"""Tests for the human-readable session name generator."""

from __future__ import annotations

import re

from kimi_cli.tools.session_name import (
    extract_topic_words,
    generate_session_name,
    random_session_name,
    with_collision_suffix,
)

_VALID_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class TestExtractTopicWords:
    def test_drops_stopwords(self):
        words = extract_topic_words("analisa ultima as sessoes e me diz o final")
        assert "analisa" not in words  # stopword
        assert "as" not in words  # stopword
        assert "e" not in words  # stopword
        assert words  # still has meaningful words

    def test_limits_to_max_words(self):
        words = extract_topic_words("a b c d e f g h i j")
        assert len(words) <= 2

    def test_ignores_short_tokens(self):
        words = extract_topic_words("oi hi ok e a um")
        assert words == []  # all short or stopwords

    def test_handles_accents(self):
        words = extract_topic_words("conexão com o servidor")
        assert "conexao" in words  # accented chars normalized


class TestGenerateSessionName:
    def test_returns_slug(self):
        name = generate_session_name("analisa ultima as sessoes", "abcd1234")
        assert _VALID_SLUG_RE.match(name)
        assert name == name.lower()

    def test_correlates_with_topic(self):
        name = generate_session_name("configurar o firewall do servidor", "a")
        assert "servidor" in name or "firewall" in name

    def test_stable_for_same_input(self):
        a = generate_session_name("analisa ultima as sessoes", "abcd")
        b = generate_session_name("analisa ultima as sessoes", "abcd")
        assert a == b

    def test_same_topic_same_theme_word(self):
        a = generate_session_name("otimizar o carregamento da cli", "x1")
        b = generate_session_name("otimizar o carregamento da cli", "x2")
        # Theme word is derived from a hash of the topic, so identical topics
        # map to the same theme word (suffix may differ only by session).
        assert a.rsplit("-", 1)[-1] == b.rsplit("-", 1)[-1]

    def test_short_or_empty_text(self):
        name = generate_session_name("", "abc")
        assert _VALID_SLUG_RE.match(name)
        name2 = generate_session_name("!!!", "abc")
        assert _VALID_SLUG_RE.match(name2)

    def test_name_length_bounded(self):
        long_text = "palavra " * 100
        name = generate_session_name(long_text, "abc")
        assert len(name) <= 40

    def test_emoji_removed(self):
        name = generate_session_name("olá você! 👋 como vai?", "x")
        assert "👋" not in name
        assert _VALID_SLUG_RE.match(name)


class TestWithCollisionSuffix:
    def test_appends_short_session_prefix(self):
        result = with_collision_suffix("analisa-sessoes-captain", "38867713")
        assert result.endswith("3886")
        assert _VALID_SLUG_RE.match(result)

    def test_keeps_within_length(self):
        result = with_collision_suffix("x" * 100, "abcd")
        assert len(result) <= 44


class TestRandomSessionName:
    def test_returns_valid_slug(self):
        name = random_session_name()
        assert _VALID_SLUG_RE.match(name)
        assert name

    def test_returns_different_names(self):
        names = {random_session_name() for _ in range(20)}
        assert len(names) > 1
