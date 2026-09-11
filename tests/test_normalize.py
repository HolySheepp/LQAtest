"""正規化與相似度的測試。範例句子取自實際專案文本。"""

from lqa.compare import normalize as nz


class TestDisplayKey:
    def test_curly_quotes_become_straight(self):
        raw = "There are still a few formulas I haven’t memorized properly..."
        assert "haven't" in nz.display_key(raw)

    def test_dashes_are_unified(self):
        assert nz.display_key("But—") == "But-"
        assert nz.display_key("The Owls’ boss—I mean") == "The Owls' boss-I mean"

    def test_ellipsis_char_expands(self):
        assert nz.display_key("Nev…") == "Nev..."

    def test_rich_text_tags_removed(self):
        assert nz.display_key("<color=#FF0000>Fine.</color>") == "Fine."

    def test_whitespace_collapsed(self):
        assert nz.display_key("  Take   it\neasy.  ") == "Take it easy."


class TestCjk:
    def test_detects_chinese(self):
        assert nz.has_cjk("涅維站在門口")
        assert not nz.has_cjk("Nev stood by the doorway.")

    def test_ratio_ignores_ascii_punctuation(self):
        assert nz.cjk_ratio("Take it easy.") == 0.0
        assert nz.cjk_ratio("放輕鬆") > 0.9


class TestSimilarity:
    def test_identical_after_normalization(self):
        a = "It doesn’t matter what score you end up with..."
        b = "It doesn't matter what score you end up with…"
        assert nz.similarity(a, b) == 1.0

    def test_punctuation_only_difference_is_ignored(self):
        a = "Take it easy. Do your best, that's all that matters."
        b = "Take it easy Do your best thats all that matters"
        assert nz.similarity(a, b) > 0.98

    def test_ocr_confusable_chars_tolerated(self):
        # OCR 把 l 讀成 I、O 讀成 0 是固定誤判組
        a = "I will not disappoint you."
        b = "l wiII not disapp0int you."
        assert nz.similarity(a, b) > 0.9

    def test_different_sentences_score_low(self):
        a = "Nev fell silent. After a moment, he snapped his notebook shut."
        b = "Through the classroom windows, they watched the students."
        assert nz.similarity(a, b) < 0.6


class TestPrefixAndLength:
    def test_truncated_text_has_high_prefix_score(self):
        full = "The Owls' boss—I mean, your brother also spent a lot of time helping me."
        cut = "The Owls' boss—I mean, your brother also spent a lot of"
        assert nz.prefix_score(full, cut) > 0.95
        assert nz.length_ratio(full, cut) < 0.9

    def test_unrelated_text_has_low_prefix_score(self):
        full = "Take it easy. Do your best, that's all that matters."
        other = "No matter how it turns out"
        assert nz.prefix_score(full, other) < 0.7


class TestPlaceholders:
    def test_placeholder_segments_match(self):
        expected = "Hello {playerName}, welcome back."
        assert nz.placeholder_match(expected, "Hello Nev, welcome back.")

    def test_placeholder_rejects_wrong_static_text(self):
        expected = "Hello {playerName}, welcome back."
        assert not nz.placeholder_match(expected, "Goodbye Nev, see you later.")

    def test_detects_percent_style(self):
        assert nz.has_placeholder("You gained %d coins.")
        assert not nz.has_placeholder("You gained coins.")
