"""文字正規化與相似度。

比對前要把兩邊拉到同一個基準，否則彎引號、破折號、全半形、
富文本標籤都會變成假 bug。

三層 key：
  display_key : 人看的乾淨文字（保留標點）
  match_key   : 比對用（小寫、去標點）
  loose_key   : 折疊 OCR 易混字元後的比對用 key，當成備援分數
"""

from __future__ import annotations

import re
import unicodedata

from rapidfuzz import fuzz

# 富文本 / 標記：<color=#FF0000>、<b>、</color>、[color=...]
_RICH_TAG_RE = re.compile(r"<[^<>]{1,40}>|\[/?(?:color|b|i|u|size)[^\]]{0,40}\]", re.IGNORECASE)

# 佔位符：{playerName}、%s、%d、%1$s、{0}
_PLACEHOLDER_RE = re.compile(r"\{[^{}]{0,40}\}|%(?:\d+\$)?[sd]")

_CJK_RE = re.compile(
    r"[㐀-䶿一-鿿豈-﫿぀-ゟ゠-ヿ]"
)

# 統一成 ASCII 對應字元。刻意不用整串 NFKC，避免動到不該動的字。
_CHAR_MAP = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"',
    "′": "'", "″": '"',
    "‐": "-", "‑": "-", "‒": "-", "–": "-",
    "—": "-", "―": "-", "−": "-",
    " ": " ", " ": " ", " ": " ", "　": " ",
    "​": "", "‌": "", "‍": "", "﻿": "",
    "…": "...",
}
_TRANS = str.maketrans(_CHAR_MAP)

# OCR 在英文上的固定誤判組，只用在 loose_key
_CONFUSABLE_MAP = {
    "l": "1", "i": "1", "|": "1", "!": "1",
    "o": "0",
    "s": "5",
    "b": "6",
    "g": "9",
    "q": "9",
}
_CONFUSABLE_TRANS = str.maketrans(_CONFUSABLE_MAP)


def has_cjk(text: str) -> bool:
    """是否含中日文字元。目標語為英文時，命中即代表未翻譯/未套用。"""
    return bool(_CJK_RE.search(text or ""))


def cjk_ratio(text: str) -> float:
    """CJK 字元佔比，用來區分『整句沒翻』與『夾雜一兩個字』。"""
    if not text:
        return 0.0
    hits = len(_CJK_RE.findall(text))
    return hits / len(text)


def strip_tags(text: str) -> str:
    return _RICH_TAG_RE.sub("", text or "")


def split_placeholders(text: str) -> list[str]:
    """把含佔位符的字串切成固定片段，空片段會被濾掉。"""
    parts = _PLACEHOLDER_RE.split(text or "")
    return [p for p in (s.strip() for s in parts) if p]


def has_placeholder(text: str) -> bool:
    return bool(_PLACEHOLDER_RE.search(text or ""))


def display_key(text: str) -> str:
    """人看的乾淨版本：去標記、統一標點、收斂空白。"""
    s = strip_tags(text or "")
    s = unicodedata.normalize("NFC", s)
    s = s.translate(_TRANS)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def match_key(text: str) -> str:
    """比對用 key：小寫、移除標點與空白。

    OCR 最常錯的就是標點，全部拿掉可以讓相似度只反映真正的文字差異。
    """
    s = display_key(text).lower()
    s = _PLACEHOLDER_RE.sub(" ", s)
    s = re.sub(r"[^0-9a-z㐀-鿿぀-ヿ]+", "", s)
    return s


def loose_key(text: str) -> str:
    """再把 OCR 易混字元折疊掉，當作備援分數。"""
    s = match_key(text)
    s = s.replace("rn", "m").replace("cl", "d").replace("vv", "w")
    return s.translate(_CONFUSABLE_TRANS)


def similarity(expected: str, actual: str) -> float:
    """回傳 0.0 ~ 1.0。

    取「嚴格 key」與「折疊 key」的較高分，避免 OCR 噪音壓低真正相符的句子。
    """
    a, b = match_key(expected), match_key(actual)
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    score = fuzz.ratio(a, b) / 100.0
    if score < 0.995:
        la, lb = loose_key(expected), loose_key(actual)
        score = max(score, fuzz.ratio(la, lb) / 100.0)
    return score


def placeholder_match(expected: str, actual: str, threshold: float = 0.9) -> bool:
    """譯文含佔位符時，只要求所有固定片段都出現在畫面文字中。

    例：expected = "Hello {playerName}, welcome back."
        actual   = "Hello Nev, welcome back."
    """
    segments = split_placeholders(expected)
    if not segments:
        return False
    hay = match_key(actual)
    if not hay:
        return False
    for seg in segments:
        needle = match_key(seg)
        if not needle:
            continue
        if needle in hay:
            continue
        if fuzz.partial_ratio(needle, hay) / 100.0 < threshold:
            return False
    return True


def prefix_score(expected: str, actual: str) -> float:
    """actual 有多像 expected 的開頭。超框判定的核心訊號。

    截掉 expected 的前 len(actual) 個字元再比，OCR 有噪音也撐得住。
    """
    exp_k, act_k = match_key(expected), match_key(actual)
    if not act_k or not exp_k:
        return 0.0
    if len(act_k) >= len(exp_k):
        return fuzz.ratio(exp_k, act_k) / 100.0
    head = exp_k[: len(act_k)]
    score = fuzz.ratio(head, act_k) / 100.0
    if score < 0.995:
        # 折疊版備援
        exp_l, act_l = loose_key(expected), loose_key(actual)
        if act_l and len(act_l) < len(exp_l):
            score = max(score, fuzz.ratio(exp_l[: len(act_l)], act_l) / 100.0)
    return score


def length_ratio(expected: str, actual: str) -> float:
    """畫面文字長度 / 譯文長度。"""
    exp_k, act_k = match_key(expected), match_key(actual)
    if not exp_k:
        return 1.0
    return len(act_k) / len(exp_k)
