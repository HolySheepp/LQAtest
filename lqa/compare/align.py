"""序列對齊：把「錄到的句子」對到「翻譯文本的句子」。

遊戲畫面不顯示對話ID，所以 ID 只能靠位置推回去。做法是全域對齊
（Needleman-Wunsch，加對角帶限制）：

  1. 先用前幾句錄製內容找錨點，決定對角帶的中心偏移
  2. 帶狀 DP 做單調對齊，缺口就是「文本有、遊戲沒有」或反過來
  3. 單調對齊抓不到「順序互換」，所以對剩下沒配到的兩邊再做一次
     交叉比對，配得上的就是順序不一致

對齊本身刻意寬鬆：同一個位置就算文字完全不同也要配成一對，
這樣才抓得到「不一致」；真正配不上的才留成缺口。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Sequence

NEG_INF = float("-inf")

SimFn = Callable[[str, str], float]


@dataclass
class AlignPair:
    """一組對齊結果。任一邊為 None 代表缺口。"""

    exp_idx: Optional[int]
    cap_idx: Optional[int]
    score: float = 0.0


@dataclass
class AlignConfig:
    match_offset: float = 0.50   # 配對得分 = similarity - match_offset
    gap_penalty: float = 0.30    # 每個缺口的成本
    band: int = 96               # 對角帶半寬
    anchor_probe: int = 5        # 用前幾句錄製內容找錨點
    anchor_threshold: float = 0.70
    reorder_threshold: float = 0.85  # 殘留項交叉比對的門檻


class _SimCache:
    def __init__(self, expected: Sequence[str], captured: Sequence[str], sim: SimFn):
        self._exp = expected
        self._cap = captured
        self._sim = sim
        self._cache: dict[tuple[int, int], float] = {}

    def __call__(self, i: int, j: int) -> float:
        key = (i, j)
        hit = self._cache.get(key)
        if hit is None:
            hit = self._sim(self._exp[i], self._cap[j])
            self._cache[key] = hit
        return hit


def _find_anchor(sim: _SimCache, n_exp: int, n_cap: int, cfg: AlignConfig) -> int:
    """回傳對角帶的偏移量 offset，定義為 (exp_idx - cap_idx)。

    只掃前 anchor_probe 句錄製內容；找不到夠像的就回 0（假設從頭開始錄）。
    """
    probe = min(cfg.anchor_probe, n_cap)
    best_offset, best_score = 0, cfg.anchor_threshold
    for j in range(probe):
        for i in range(n_exp):
            score = sim(i, j)
            if score > best_score:
                best_offset, best_score = i - j, score
    return best_offset


def _banded_nw(
    n_exp: int,
    n_cap: int,
    sim: _SimCache,
    cfg: AlignConfig,
    offset: int,
) -> list[AlignPair]:
    """帶狀 Needleman-Wunsch。回傳由前到後的對齊配對。"""
    band = cfg.band
    gap = -cfg.gap_penalty

    def in_band(i: int, j: int) -> bool:
        return abs((i - j) - offset) <= band

    # dp[i][j]：expected 前 i 句與 captured 前 j 句的最佳分數
    # 用 dict 稀疏存，只算帶內的格子
    dp: dict[tuple[int, int], float] = {(0, 0): 0.0}
    back: dict[tuple[int, int], tuple[int, int]] = {}

    for i in range(n_exp + 1):
        for j in range(n_cap + 1):
            if i == 0 and j == 0:
                continue
            if not in_band(i, j) and not (i == n_exp and j == n_cap):
                continue
            best, best_from = NEG_INF, None

            if i > 0 and j > 0:
                prev = dp.get((i - 1, j - 1))
                if prev is not None:
                    cand = prev + (sim(i - 1, j - 1) - cfg.match_offset)
                    if cand > best:
                        best, best_from = cand, (i - 1, j - 1)
            if i > 0:
                prev = dp.get((i - 1, j))
                if prev is not None and prev + gap > best:
                    best, best_from = prev + gap, (i - 1, j)
            if j > 0:
                prev = dp.get((i, j - 1))
                if prev is not None and prev + gap > best:
                    best, best_from = prev + gap, (i, j - 1)

            if best_from is not None:
                dp[(i, j)] = best
                back[(i, j)] = best_from

    if (n_exp, n_cap) not in dp:
        # 帶寬不夠（錄製內容與文本嚴重錯位），退回無帶限制重算
        if band < max(n_exp, n_cap):
            wide = AlignConfig(**{**cfg.__dict__, "band": max(n_exp, n_cap)})
            return _banded_nw(n_exp, n_cap, sim, wide, 0)
        raise RuntimeError("序列對齊失敗：無法回溯路徑")

    pairs: list[AlignPair] = []
    node = (n_exp, n_cap)
    while node != (0, 0):
        prev = back[node]
        pi, pj = prev
        ci, cj = node
        if ci == pi + 1 and cj == pj + 1:
            pairs.append(AlignPair(pi, pj, sim(pi, pj)))
        elif ci == pi + 1:
            pairs.append(AlignPair(pi, None, 0.0))
        else:
            pairs.append(AlignPair(None, pj, 0.0))
        node = prev
    pairs.reverse()
    return pairs


def _resolve_reorders(
    pairs: list[AlignPair],
    sim: _SimCache,
    cfg: AlignConfig,
) -> tuple[list[AlignPair], list[tuple[int, int, float]]]:
    """對剩下的缺口做交叉比對，找出順序互換。

    回傳 (清理過的 pairs, reorder 清單)。reorder 項為 (exp_idx, cap_idx, score)。
    """
    free_exp = [p.exp_idx for p in pairs if p.exp_idx is not None and p.cap_idx is None]
    free_cap = [p.cap_idx for p in pairs if p.cap_idx is not None and p.exp_idx is None]
    if not free_exp or not free_cap:
        return pairs, []

    # 貪婪取最高分配對，數量通常很少，不需要匈牙利演算法
    candidates: list[tuple[float, int, int]] = []
    for i in free_exp:
        for j in free_cap:
            score = sim(i, j)
            if score >= cfg.reorder_threshold:
                candidates.append((score, i, j))
    candidates.sort(reverse=True)

    used_exp: set[int] = set()
    used_cap: set[int] = set()
    reorders: list[tuple[int, int, float]] = []
    for score, i, j in candidates:
        if i in used_exp or j in used_cap:
            continue
        used_exp.add(i)
        used_cap.add(j)
        reorders.append((i, j, score))

    if not reorders:
        return pairs, []

    merged: dict[int, tuple[int, float]] = {j: (i, s) for i, j, s in reorders}
    cleaned: list[AlignPair] = []
    for p in pairs:
        if p.exp_idx is not None and p.cap_idx is None and p.exp_idx in used_exp:
            continue  # 這句其實有出現，只是位置不對，會掛在 captured 那一側
        if p.cap_idx is not None and p.exp_idx is None and p.cap_idx in used_cap:
            exp_idx, score = merged[p.cap_idx]
            cleaned.append(AlignPair(exp_idx, p.cap_idx, score))
            continue
        cleaned.append(p)

    return cleaned, reorders


def align(
    expected_texts: Sequence[str],
    captured_texts: Sequence[str],
    sim_fn: SimFn,
    cfg: AlignConfig | None = None,
) -> tuple[list[AlignPair], set[int]]:
    """對齊兩個序列。

    回傳 (pairs, reordered_cap_indices)。
    reordered_cap_indices 裡的 captured 索引代表「有配到，但位置不對」。
    """
    cfg = cfg or AlignConfig()
    n_exp, n_cap = len(expected_texts), len(captured_texts)
    if n_exp == 0 or n_cap == 0:
        pairs = [AlignPair(i, None) for i in range(n_exp)]
        pairs += [AlignPair(None, j) for j in range(n_cap)]
        return pairs, set()

    sim = _SimCache(expected_texts, captured_texts, sim_fn)
    offset = _find_anchor(sim, n_exp, n_cap, cfg)
    pairs = _banded_nw(n_exp, n_cap, sim, cfg, offset)
    pairs, reorders = _resolve_reorders(pairs, sim, cfg)
    return pairs, {j for _, j, _ in reorders}
