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

    對白框背景是漸變變暗的，文字則是直接疊在上面的高亮度顏色，
    所以取字最準的方式是看亮度或直接比對顏色，而不是 Otsu 這種
    會被背景內容牽著走的自適應方法。

    method 的選擇：
      value    取 RGB 三通道的最大值（等同 HSV 的 V）再砍門檻。**預設，建議用這個。**
               白字 #fefefe 的 V 是 254，橘字 #ff8a00 是 255，藍字 #5dbcfe 是 254，
               一個門檻全收，不必事先知道文本用了哪些顏色。
      colorkey 只取指定顏色附近的像素。精度最高，但漏掉任何一個變色就會缺字，
               需要先用 `lqa colors --script` 把文本裡所有顏色掃出來填進 text_colors。
      bright   灰階亮度門檻。**不建議**：灰階會低估飽和色，
               #ff8a00 的灰階值只有 157，用 170 門檻會把變色字整段砍掉。
      otsu / adaptive
               自適應門檻，背景一複雜就會飄。留著當退路。
    """

    method: str = "value"       # value | colorkey | bright | otsu | adaptive
    invert: bool = False        # 深色底淺色字時不用開；淺底深字才開
    upscale: int = 3            # OCR 前放大倍率，小字很吃這個
    clahe: bool = False         # 只對 otsu / adaptive 有意義，會破壞絕對門檻
    blur: int = 0               # 中值濾波核大小，0 表示不做。
                                # 小字不要開，中值濾波會吃掉細筆畫
    # bright 與 value 共用的門檻。實測掃描：亮場景下 85 與 140 的辨識率是
    # 100% 與 99%，170 掉到 83%、200 只剩 32%。門檻太高會只留下筆畫核心
    # 把字挖空。140 在辨識率與遮罩乾淨度之間取得平衡。
    bright_threshold: int = 140
    color_tolerance: int = 60   # colorkey 的容許色距（BGR 歐氏距離）
    # 送 OCR 前把遮罩往外膨脹幾圈，用來補回門檻砍掉的筆畫本體。
    # 變動偵測要乾淨的遮罩（門檻高），OCR 要完整的筆畫（門檻低），
    # 兩者需求相反，所以用高門檻取遮罩再靠膨脹補回來。
    ocr_grow: int = 3
    text_colors: list[str] = field(
        default_factory=lambda: ["#fefefe"]
    )                           # colorkey 用的顏色清單
    min_text_pixels: int = 40   # 遮罩前景像素少於此值視為「畫面沒有文字」


@dataclass
class StabilityConfig:
    """打字機效果處理：等文字不再變動才擷取。

    兩個門檻都是「變動像素 / 文字像素量」的比例，不是佔 ROI 面積的比例，
    所以換解析度或改對白框大小都不需要重調。理由見 detect/stability.py。
    """

    poll_interval_ms: int = 80
    diff_threshold: float = 0.04    # 視窗內（現在 vs stable_frames 幀前）的容許變動量
    stable_frames: int = 4          # 視窗長度：連續幾幀都沒變才算穩定
    min_gap_ms: int = 200           # 兩次擷取之間的最小間隔
    rearm_threshold: float = 0.04   # 單幀變動量超過此值才重新進入「等待穩定」狀態


@dataclass
class OcrConfig:
    engine: str = "rapidocr"
    lang: str = "en"
    min_confidence: float = 0.45
    join_with: str = " "        # 多行結果合併方式
    edge_margin_px: int = 3     # 文字 bbox 距離 ROI 邊緣多少像素內算「貼邊」
    # 決定送進 OCR 的影像：masked_gray | mask | gray | color
    # masked_gray 保留文字灰階層次，純二值會砍掉抗鋸齒邊緣導致小字誤判
    source: str = "masked_gray"
    screenshot_quality: int = 85


@dataclass
class Profile:
    name: str = "default"
    # 擷取來源：優先用視窗標題找雷電視窗，找不到才退回絕對螢幕座標
    window_title: Optional[str] = "雷電模擬器"
    capture_region: Optional[Rect] = None
    # auto | printwindow | mss
    # auto 會優先用 PrintWindow（向視窗要畫面，被蓋住也抓得到），
    # 實測抓不到內容才退回 mss（抓螢幕區域，會被遮擋影響）
    capture_backend: str = "auto"
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
            capture_backend=data.get("capture_backend", "auto"),
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
