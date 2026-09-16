"""把對齊結果分類成六大類問題。

分類優先序（同一句只落在一個主分類，發話者錯誤獨立追加）：
  1. UNTRANSLATED  畫面出現中文
  2. ORDER         有配到但位置不對
  3. PASS          相似度達標
  4. TRUNCATED     畫面文字是譯文的前綴且明顯較短（超框）
  5. MISMATCH      其餘配到但不像的
  另外：MISSING（文本有、畫面沒有）、EXTRA（畫面有、文本沒有）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from ..model import CapturedLine, Category, ExpectedLine, Issue
from . import normalize as nz
from .align import AlignConfig, AlignPair, align


@dataclass
class CompareConfig:
    pass_threshold: float = 0.97       # 達標視為一致
    mismatch_floor: float = 0.55       # 低於此值不視為同一句（交給缺口處理）
    truncate_prefix: float = 0.88      # 前綴相似度門檻
    truncate_len_ratio: float = 0.92   # 畫面長度 / 譯文長度 低於此值才算超框
    truncate_min_chars: int = 6        # 太短的畫面文字不判超框，避免 OCR 失敗誤報
    speaker_threshold: float = 0.85    # 發話者名相似度門檻
    # 綁定模式下，不一致的句子要像到這個程度才算「其實是文本裡的另一句」。
    # 抓高一點：寧可報成不一致，也不要隨便指認成別句誤導使用者
    order_threshold: float = 0.92
    untranslated_cjk_ratio: float = 0.05  # CJK 佔比達此值才算未翻譯
    check_speaker: bool = True
    align: AlignConfig = field(default_factory=AlignConfig)


@dataclass
class CompareResult:
    issues: list[Issue]
    expected: list[ExpectedLine]
    captured: list[CapturedLine]

    @property
    def problems(self) -> list[Issue]:
        return [i for i in self.issues if i.category is not Category.PASS]

    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for issue in self.issues:
            counts[issue.category.value] = counts.get(issue.category.value, 0) + 1
        return counts


def _is_truncated(expected_text: str, actual_text: str, cfg: CompareConfig) -> bool:
    """畫面文字是否為譯文被截斷後的前綴。

    注意：不能用「結尾是刪節號」當訊號 —— 專案譯文本身大量使用 "..." 結尾。
    唯一可靠的訊號是前綴關係加上長度明顯不足。
    """
    act_len = len(nz.match_key(actual_text))
    if act_len < cfg.truncate_min_chars:
        return False
    if nz.length_ratio(expected_text, actual_text) > cfg.truncate_len_ratio:
        return False
    return nz.prefix_score(expected_text, actual_text) >= cfg.truncate_prefix


def _speaker_issue(
    exp: ExpectedLine,
    cap: CapturedLine,
    cfg: CompareConfig,
    enabled: bool,
) -> Issue | None:
    """發話者比對。只有在文本有給發話者時才檢查。"""
    if not enabled or not cfg.check_speaker or not exp.speaker_zh:
        return None
    if not exp.speaker_en:
        return None  # 對照表沒這個名字，交給 unknown_speakers 提醒，不誤報
    # 畫面上的發話者後面會接內部流水號（例如 "Cyan(11201)"），要先剝掉
    actual = nz.strip_speaker_id(cap.speaker_text)
    if not actual:
        return Issue(
            category=Category.SPEAKER,
            expected=exp,
            captured=cap,
            similarity=0.0,
            detail=f"畫面未讀到發話者名（文本為 {exp.speaker_en} / {exp.speaker_zh}）",
            expected_order=exp.order + 1,
            actual_order=cap.seq + 1,
        )
    score = nz.similarity(exp.speaker_en, actual)
    if score >= cfg.speaker_threshold:
        return None
    return Issue(
        category=Category.SPEAKER,
        expected=exp,
        captured=cap,
        similarity=score,
        detail=f"發話者不符：文本 {exp.speaker_en}，畫面 {actual}",
        expected_order=exp.order + 1,
        actual_order=cap.seq + 1,
    )


def _find_elsewhere(
    text: str,
    expected: Sequence[ExpectedLine],
    skip: int,
    threshold: float,
) -> tuple[ExpectedLine | None, float]:
    """畫面上這句其實是文本裡的哪一條。

    綁定模式下截圖是釘死在某個條目上的，所以序列對齊那套「重排」判斷
    完全用不上 —— 不處理的話，遊戲順序和文本不同、或使用者按鍵時
    游標沒對上，全部都只會報成「不一致」，看不出到底發生什麼事。
    這裡回頭在整份文本裡找，找得到就講得出是哪一句。
    """
    best, best_score = None, 0.0
    for index, candidate in enumerate(expected):
        if index == skip or not candidate.target_en:
            continue
        score = nz.similarity(candidate.target_en, text)
        if score > best_score:
            best, best_score = candidate, score
    return (best, best_score) if best_score >= threshold else (None, 0.0)


def _variant_issues(
    matched: list[tuple[ExpectedLine, CapturedLine]],
    cfg: CompareConfig,
    enabled: bool,
) -> list[Issue]:
    """同一個發話者在遊戲中出現不只一種英文名。

    這是對照表查不出來的那一類：表裡沒有這個中文名時，發話者檢查會整個
    跳過（沒有正確答案就不該亂報錯）。但「同一個角色前後譯名不一致」
    本身就是問題，不需要正確答案也看得出來 —— 拿畫面自己和自己比就好。

    有正確答案的名字不在這裡處理，那些對不上的已經是發話者錯誤了，
    重複報只會讓同一件事出現兩次。
    """
    if not enabled or not cfg.check_speaker:
        return []

    # 中文名 -> {畫面上的英文名: [出現次數, 第一次出現在第幾句]}
    tally: dict[str, dict[str, list[int]]] = {}
    for order, (exp, cap) in enumerate(matched):
        if not exp.speaker_zh or exp.speaker_en:
            continue
        name = nz.strip_speaker_id(cap.speaker_text)
        if not name:
            continue
        entry = tally.setdefault(exp.speaker_zh, {})
        if name in entry:
            entry[name][0] += 1
        else:
            entry[name] = [1, order]

    issues: list[Issue] = []
    for exp, cap in matched:
        entry = tally.get(exp.speaker_zh)
        if not entry or len(entry) < 2:
            continue
        name = nz.strip_speaker_id(cap.speaker_text)
        # 次數最多的當主要譯名；同票時以先出現的為準，才不會每次跑結果不同
        main = min(entry, key=lambda n: (-entry[n][0], entry[n][1]))
        if not name or name == main:
            continue
        # OCR 把同一個名字讀出一兩個字差別是常事，那不是譯名不一致
        if nz.similarity(name, main) >= cfg.speaker_threshold:
            continue
        others = "、".join(
            f"{n}（{entry[n][0]} 次）"
            for n in sorted(entry, key=lambda n: (-entry[n][0], entry[n][1])))
        issues.append(Issue(
            category=Category.SPEAKER_VARIANT,
            expected=exp,
            captured=cap,
            detail=(f"「{exp.speaker_zh}」在遊戲中出現不只一種譯名：{others}。"
                    "對照表沒有這個名字，無法判斷哪個才對"),
            expected_order=exp.order + 1,
            actual_order=cap.seq + 1,
        ))
    return issues


def _guess_untranslated_source(
    cap: CapturedLine,
    expected: Sequence[ExpectedLine],
) -> tuple[ExpectedLine | None, float]:
    """畫面出現中文時，拿中文原文欄反查是哪一句，這樣才報得出 ID。"""
    best, best_score = None, 0.0
    for exp in expected:
        if not exp.source_zh:
            continue
        score = nz.similarity(exp.source_zh, cap.body_text)
        if score > best_score:
            best, best_score = exp, score
    return (best, best_score) if best_score >= 0.7 else (None, best_score)


def _bound_pairs(
    expected: Sequence[ExpectedLine], captured: Sequence[CapturedLine]
) -> list[AlignPair]:
    """截圖已經綁定條目時，直接逐條配對，不做對齊。

    介面版拍攝時游標就決定了每張截圖對應哪一條，位置是已知的。
    硬要再跑一次模糊對齊只會引入不必要的誤差。
    """
    by_index = {c.expected_index: c for c in captured if 0 <= c.expected_index}
    pairs: list[AlignPair] = []
    for index in range(len(expected)):
        cap = by_index.get(index)
        pairs.append(AlignPair(index, captured.index(cap) if cap else None))
    # 綁到範圍外的截圖（例如文本後來被改短）仍要報出來
    for position, cap in enumerate(captured):
        if cap.expected_index >= len(expected):
            pairs.append(AlignPair(None, position))
    return pairs


def compare(
    expected: Sequence[ExpectedLine],
    captured: Sequence[CapturedLine],
    cfg: CompareConfig | None = None,
) -> CompareResult:
    """把錄製結果對到翻譯文本，產出分類後的問題清單。"""
    cfg = cfg or CompareConfig()
    expected = list(expected)
    captured = list(captured)

    # 整場錄製都沒讀到任何發話者名，代表 profile 沒設姓名框（或姓名框設錯）。
    # 這種情況下逐句報「未讀到發話者」只會製造滿江紅，直接關掉這項檢查。
    speaker_enabled = any(c.speaker_text.strip() for c in captured)

    # 截圖已綁定條目時直接逐條配對；自由拍攝模式才需要序列對齊
    bound = bool(captured) and all(c.expected_index >= 0 for c in captured)
    if bound:
        pairs, reordered = _bound_pairs(expected, captured), set()
    else:
        pairs, reordered = align(
            [e.target_en for e in expected],
            [c.body_text for c in captured],
            nz.similarity,
            cfg.align,
        )

    issues: list[Issue] = []
    matched: list[tuple[ExpectedLine, CapturedLine]] = []
    for pair in pairs:
        exp = expected[pair.exp_idx] if pair.exp_idx is not None else None
        cap = captured[pair.cap_idx] if pair.cap_idx is not None else None

        # 文本有、畫面沒有
        if exp is not None and cap is None:
            # 綁定模式下「沒有這張截圖」只代表使用者跳過或漏拍，
            # 不能推論成「遊戲裡沒有這句」—— 那是完全不同的結論，
            # 混在一起會讓報告產出錯誤的判斷
            issues.append(
                Issue(
                    category=Category.NOT_CAPTURED if bound else Category.MISSING,
                    expected=exp,
                    detail=("這條沒有截圖（跳過或漏拍），請自行複核"
                            if bound else "這句在錄製過程中完全沒有出現"),
                    expected_order=exp.order + 1,
                )
            )
            continue

        # 畫面有、文本沒有
        if exp is None and cap is not None:
            if nz.cjk_ratio(cap.body_text) >= cfg.untranslated_cjk_ratio:
                src, score = _guess_untranslated_source(cap, expected)
                issues.append(
                    Issue(
                        category=Category.UNTRANSLATED,
                        expected=src,
                        captured=cap,
                        similarity=score,
                        detail=(
                            "畫面顯示中文（未套用翻譯）"
                            + (f"，比對中文原文推測為 {src.dialogue_id}" if src else "，無法推測對應ID")
                        ),
                        actual_order=cap.seq + 1,
                    )
                )
            else:
                issues.append(
                    Issue(
                        category=Category.EXTRA,
                        captured=cap,
                        detail="畫面出現此句，但翻譯文本中找不到對應內容",
                        actual_order=cap.seq + 1,
                    )
                )
            continue

        assert exp is not None and cap is not None
        # 重算而不是沿用 pair.score：綁定模式的配對沒有經過對齊，
        # 沿用的話相似度會永遠是 0
        score = nz.similarity(exp.target_en, cap.body_text)
        base = Issue(
            expected=exp,
            captured=cap,
            similarity=score,
            category=Category.PASS,
            expected_order=exp.order + 1,
            actual_order=cap.seq + 1,
        )

        if nz.cjk_ratio(cap.body_text) >= cfg.untranslated_cjk_ratio:
            base.category = Category.UNTRANSLATED
            base.detail = "畫面顯示中文（未套用翻譯）"
        elif pair.cap_idx in reordered:
            base.category = Category.ORDER
            base.detail = (
                f"文本中是第 {exp.order + 1} 句，卻在畫面第 {cap.seq + 1} 句出現"
            )
        elif score >= cfg.pass_threshold:
            base.category = Category.PASS
        elif nz.has_placeholder(exp.target_en) and nz.placeholder_match(
            exp.target_en, cap.body_text
        ):
            base.category = Category.PASS
            base.detail = "含變數，固定片段全數相符"
        elif _is_truncated(exp.target_en, cap.body_text, cfg):
            base.category = Category.TRUNCATED
            base.detail = (
                f"畫面文字為譯文前綴且明顯較短（長度比 "
                f"{nz.length_ratio(exp.target_en, cap.body_text):.0%}），高機率超框截斷"
            )
            base.extras["missing_tail"] = nz.display_key(exp.target_en)[
                len(nz.display_key(cap.body_text)):
            ].strip()
        else:
            other, other_score = (
                _find_elsewhere(cap.body_text, expected, pair.exp_idx,
                                cfg.order_threshold)
                if bound else (None, 0.0))
            if other is not None:
                base.category = Category.ORDER
                base.detail = (
                    f"畫面顯示的是文本第 {other.order + 1} 句"
                    f"（{other.dialogue_id}，相似度 {other_score:.0%}）—— "
                    "可能是遊戲順序和文本不同，或截圖時游標沒對上")
                base.extras["actual_dialogue_id"] = other.dialogue_id
            else:
                base.category = Category.MISMATCH
                base.detail = f"與譯文不一致（相似度 {score:.0%}）"

        issues.append(base)

        # 對白本身就對不上時，這張截圖和這個條目的配對本來就不成立，
        # 拿它的發話者去比沒有意義 —— 只會在「順序不一致」之類的問題上面
        # 再疊一筆發話者錯誤，把真正的問題淹掉。
        # 超框留著：文字被截斷但確實是這一句，發話者仍有參考價值
        if base.category not in (Category.PASS, Category.TRUNCATED):
            continue
        matched.append((exp, cap))
        spk = _speaker_issue(exp, cap, cfg, speaker_enabled)
        if spk is not None:
            issues.append(spk)

    issues.extend(_variant_issues(matched, cfg, speaker_enabled))
    return CompareResult(issues=issues, expected=expected, captured=captured)
