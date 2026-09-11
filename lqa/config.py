"""擷取設定檔（profile）。

一台機器、一個遊戲解析度對應一份 profile。校準一次之後長期沿用。
座標一律用 [x, y, w, h]。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

Rect = tuple[int, int, int, int]


@dataclass
class MaskConfig:
    """文字遮罩抽取設定。

    對白框是半透明、後面背景會動，所以不能直接比對原始像素。
    先把 ROI 轉成「只剩文字」的二值遮罩，再拿遮罩做變動偵測與 OCR。
    """

    method: str = "otsu"        # otsu | adaptive | bright
    invert: bool = False        # 深色底淺色字時不用開；淺底深字才開
    upscale: int = 2            # OCR 前放大倍率，小字很吃這個
    clahe: bool = True          # 先做局部對比強化，壓掉半透明背景
    blur: int = 3               # 中值濾波核大小，0 表示不做
    bright_threshold: int = 170  # method=bright 時的亮度門檻
    min_text_pixels: int = 40   # 遮罩前景像素少於此值視為「畫面沒有文字」


@dataclass
class StabilityConfig:
    """打字機效果處理：等文字不再變動才擷取。"""

    poll_interval_ms: int = 80
    diff_threshold: float = 0.010   # 遮罩差異比例，超過視為畫面在變
    stable_frames: int = 4          # 連續幾幀沒變才算穩定
    min_gap_ms: int = 200           # 兩次擷取之間的最小間隔
    rearm_threshold: float = 0.010  # 要再次觸發前必須先觀察到的變動量


@dataclass
class OcrConfig:
    engine: str = "rapidocr"
    lang: str = "en"
    min_confidence: float = 0.45
    join_with: str = " "        # 多行結果合併方式
    edge_margin_px: int = 3     # 文字 bbox 距離 ROI 邊緣多少像素內算「貼邊」
    source: str = "mask"        # mask | gray | color，決定送進 OCR 的影像
    screenshot_quality: int = 85


@dataclass
class Profile:
    name: str = "default"
    # 擷取來源：優先用視窗標題找雷電視窗，找不到才退回絕對螢幕座標
    window_title: Optional[str] = "雷電模擬器"
    capture_region: Optional[Rect] = None
    # ROI 一律相對於擷取來源的左上角
    body_roi: Optional[Rect] = None
    speaker_roi: Optional[Rect] = None
    mask: MaskConfig = field(default_factory=MaskConfig)
    speaker_mask: Optional[MaskConfig] = None   # 不給就沿用 mask
    stability: StabilityConfig = field(default_factory=StabilityConfig)
    ocr: OcrConfig = field(default_factory=OcrConfig)

    def effective_speaker_mask(self) -> MaskConfig:
        return self.speaker_mask or self.mask

    def validate(self) -> None:
        if not self.body_roi:
            raise ValueError("profile 缺少 body_roi，請先執行 calibrate 校準對白框範圍")
        if self.window_title is None and self.capture_region is None:
            raise ValueError("profile 必須提供 window_title 或 capture_region 其中之一")

    # --- 序列化 ---

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Profile":
        def rect(value: Any) -> Optional[Rect]:
            return tuple(int(v) for v in value) if value else None  # type: ignore[return-value]

        mask = MaskConfig(**data.get("mask", {}))
        speaker_mask_raw = data.get("speaker_mask")
        return cls(
            name=data.get("name", "default"),
            window_title=data.get("window_title"),
            capture_region=rect(data.get("capture_region")),
            body_roi=rect(data.get("body_roi")),
            speaker_roi=rect(data.get("speaker_roi")),
            mask=mask,
            speaker_mask=MaskConfig(**speaker_mask_raw) if speaker_mask_raw else None,
            stability=StabilityConfig(**data.get("stability", {})),
            ocr=OcrConfig(**data.get("ocr", {})),
        )

    def save(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return p

    @classmethod
    def load(cls, path: str | Path) -> "Profile":
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"找不到 profile：{p}")
        return cls.from_dict(json.loads(p.read_text(encoding="utf-8")))
