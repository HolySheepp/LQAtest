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


def _build(cls, data: Any):
    """從 dict 建立設定物件，忽略認不得的欄位。

    參數會隨著演算法調整而增減，舊的 profile.json 裡留著已經移除的鍵
    是常態。直接 **data 會丟 TypeError，讓使用者的 profile 突然打不開，
    所以這裡只取目前還存在的欄位。
    """
    if not data:
        return cls()
    known = set(cls.__dataclass_fields__)
    return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class MaskConfig:
    """文字遮罩抽取設定。

    對白框背景是漸變變暗的，文字則是直接疊在上面的高亮度顏色，
    所以取字最準的方式是看亮度或直接比對顏色，而不是 Otsu 這種
    會被背景內容牽著走的自適應方法。

    method 的選擇：
      hysteresis 雙門檻：seed_threshold 找筆畫核心當種子，bright_threshold 取完整
               筆畫，只保留連通到種子的部分。**預設，建議用這個。**
               遊戲的字是「純白核心 -> 灰白過渡 -> 灰黑描邊」的結構，
               單一門檻對它本質上就不管用：高門檻把字挖空，低門檻收進背景。
      value    取 RGB 三通道的最大值（等同 HSV 的 V）再砍單一門檻。
               白字 #fefefe 的 V 是 254，橘字 #ff8a00 是 255，藍字 #5dbcfe 是 254，
               所以不必事先知道文本用了哪些顏色。字體單純時夠用。
      colorkey 只取指定顏色附近的像素。精度最高，但漏掉任何一個變色就會缺字，
               需要先用 `lqa colors --script` 把文本裡所有顏色掃出來填進 text_colors。
      bright   灰階亮度門檻。**不建議**：灰階會低估飽和色，
               #ff8a00 的灰階值只有 157，用 170 門檻會把變色字整段砍掉。
      otsu / adaptive
               自適應門檻，背景一複雜就會飄。留著當退路。
    """

    method: str = "hysteresis"  # hysteresis | value | colorkey | bright | otsu | adaptive
    invert: bool = False        # 深色底淺色字時不用開；淺底深字才開
    # OCR 前放大倍率。實測放大到 3 倍不會比較準（辨識模型內部本來就會把
    # 文字裁切正規化到固定高度），卻讓偵測階段多算九倍像素、慢四倍。
    # 只有文字小到偵測不出來時才需要調高。
    upscale: int = 1
    clahe: bool = False         # 只對 otsu / adaptive 有意義，會破壞絕對門檻
    blur: int = 0               # 中值濾波核大小，0 表示不做。
                                # 小字不要開，中值濾波會吃掉細筆畫
    # bright 與 value 共用的門檻，在 hysteresis 裡是低門檻（取完整筆畫）。
    # 實測掃描：亮場景下 85 與 140 的辨識率是 100% 與 99%，170 掉到 83%、
    # 200 只剩 32%。門檻太高會只留下筆畫核心把字挖空，所以寧低勿高 ——
    # 低門檻收進來的背景雜訊由 seed_threshold 的連通判斷擋掉。
    # 100 是實際校準這款遊戲對白框之後採用的值。
    bright_threshold: int = 100
    # hysteresis 的高門檻（種子）。要高到只有筆畫核心過得了，
    # 背景再亮也不該碰到；字的核心接近純白，所以 200 以上都很安全。
    seed_threshold: int = 210
    color_tolerance: int = 60   # colorkey 的容許色距（BGR 歐氏距離）
    # 送 OCR 前把遮罩往外膨脹幾圈，用來補回門檻砍掉的筆畫本體。
    # 變動偵測要乾淨的遮罩（門檻高），OCR 要完整的筆畫（門檻低），
    # 兩者需求相反，所以用高門檻取遮罩再靠膨脹補回來。
    # 膨脹過頭會把相鄰筆畫黏在一起，反而更難認；配 100 的低門檻用 2 圈就夠。
    ocr_grow: int = 2
    text_colors: list[str] = field(
        default_factory=lambda: ["#fefefe"]
    )                           # colorkey 用的顏色清單
    min_text_pixels: int = 40   # 遮罩前景像素少於此值視為「畫面沒有文字」


@dataclass
class StabilityConfig:
    """逐句偵測設定。判準見 detect/linetracker.py。

    核心是「打字只會增加筆畫、換句才會讓舊筆畫消失」，
    所以不需要等畫面靜止，也就沒有靜止時間長短的門檻。
    """

    poll_interval_ms: int = 60
    # 舊筆畫消失多少比例才算換了一句。打字中的消失量只有抗鋸齒抖動，
    # 換句時整句會被換掉，所以這個值可以放得很寬鬆。
    line_change_ratio: float = 0.25
    # 消失像素的絕對下限。比例的分母是文字量，短句的遮罩只有一千多像素，
    # 抗鋸齒邊緣抖個三十幾像素就會衝到 3%，句子越短越誇張。
    # 所以比例與絕對量要同時達標才算真的換句。
    min_changed_pixels: int = 40
    # 一句被取樣少於這個次數就標記出來：可能沒抓到它顯示完整的那一刻
    min_samples_warn: int = 2


@dataclass
class OcrConfig:
    engine: str = "rapidocr"
    lang: str = "en"
    # onnxruntime 執行緒數。預設 0 代表用滿所有核心，那是病態設定：
    # 20 核實測每張 2.9~4.1 秒、吃掉約 10 顆核心，因為大量執行緒搶小運算而忙等空轉。
    # 限制成 4 之後每張 0.7 秒（快 4~6 倍），而且不會把模擬器餓死。
    threads: int = 3
    # 解析期間把行程降到低優先權。解析通常和遊玩同時進行，
    # 讓出 CPU 給模擬器比早幾秒跑完重要。
    low_priority: bool = True
    # 文字偵測階段的縮放規則。RapidOCR 預設是 min/736，意思是
    # 「把短邊放大到 736」—— 對白框只有 404x161，短邊會被放大 4.6 倍成
    # 1846x736，光偵測就要 2.9 秒。改成限制長邊之後不再放大，
    # 同一張圖 97ms，準確率完全相同（實測皆 100%）。
    det_limit_type: str = "max"
    det_limit_side_len: int = 960
    min_confidence: float = 0.45
    join_with: str = " "        # 多行結果合併方式
    edge_margin_px: int = 3     # 文字 bbox 距離 ROI 邊緣多少像素內算「貼邊」
    # 決定送進 OCR 的影像：masked_gray | mask | gray | color
    # masked_gray 保留文字灰階層次，純二值會砍掉抗鋸齒邊緣導致小字誤判
    source: str = "masked_gray"
    screenshot_quality: int = 85


# 四種可以框選的範圍。遊戲有兩種對白版面 —— 一般劇情對白，以及 NPC 對白，
# 兩者的框在畫面上位置不同，所以各自要框、各自可以有自己的取字參數。
# 欄位是 (代號, 中文名, ROI 欄位, 取字參數欄位)。
REGION_SPECS: list[tuple[str, str, str, str]] = [
    ("body", "一般對白框", "body_roi", "mask"),
    ("speaker", "一般姓名框", "speaker_roi", "speaker_mask"),
    ("npc_body", "NPC對白框", "npc_body_roi", "npc_mask"),
    ("npc_speaker", "NPC姓名框", "npc_speaker_roi", "npc_speaker_mask"),
]

REGION_LABELS = {key: label for key, label, _roi, _mask in REGION_SPECS}
REGION_KEYS = [key for key, *_rest in REGION_SPECS]

# 取字參數沒設定時往哪裡退。NPC 的框通常和一般對白長得一樣，
# 沒特別調過就沿用一般的，不必每個都重調一次。
MASK_FALLBACK = {
    "body": [],
    "speaker": ["mask"],
    "npc_body": ["mask"],
    "npc_speaker": ["speaker_mask", "mask"],
}


@dataclass(frozen=True)
class RegionSet:
    """一套對白版面：對白框加姓名框，各自的範圍與取字參數。"""

    key: str
    label: str
    body_roi: Optional[Rect]
    speaker_roi: Optional[Rect]
    body_mask: MaskConfig
    speaker_mask: MaskConfig


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
    # NPC 對白的版面。沒框就代表這個遊戲只有一種版面，一切照舊
    npc_body_roi: Optional[Rect] = None
    npc_speaker_roi: Optional[Rect] = None
    mask: MaskConfig = field(default_factory=MaskConfig)
    speaker_mask: Optional[MaskConfig] = None   # 不給就沿用 mask
    npc_mask: Optional[MaskConfig] = None       # 不給就沿用 mask
    npc_speaker_mask: Optional[MaskConfig] = None  # 不給就沿用 speaker_mask
    stability: StabilityConfig = field(default_factory=StabilityConfig)
    ocr: OcrConfig = field(default_factory=OcrConfig)

    def effective_speaker_mask(self) -> MaskConfig:
        return self.mask_for("speaker")

    # --- 四種範圍 ---

    def roi_of(self, region: str) -> Optional[Rect]:
        return getattr(self, _spec(region)[2])

    def set_roi(self, region: str, rect: Optional[Rect]) -> None:
        setattr(self, _spec(region)[2], rect)

    def mask_for(self, region: str) -> MaskConfig:
        """這個範圍實際會用到的取字參數，沒設定就照 MASK_FALLBACK 往下退。"""
        for attr in [_spec(region)[3]] + MASK_FALLBACK[region]:
            cfg = getattr(self, attr)
            if cfg is not None:
                return cfg
        return self.mask

    def set_mask(self, region: str, cfg: MaskConfig) -> None:
        setattr(self, _spec(region)[3], cfg)

    def layouts(self) -> list[RegionSet]:
        """已經框好的版面。一般永遠在第一個，沒框 NPC 就只有一套。"""
        sets = [RegionSet("normal", "一般", self.body_roi, self.speaker_roi,
                          self.mask_for("body"), self.mask_for("speaker"))]
        if self.npc_body_roi:
            sets.append(RegionSet("npc", "NPC", self.npc_body_roi,
                                  self.npc_speaker_roi,
                                  self.mask_for("npc_body"),
                                  self.mask_for("npc_speaker")))
        return sets

    def watch_roi(self) -> Optional[Rect]:
        """擷取後端用來判斷「有沒有被蓋住」的範圍。

        兩套版面的對白框都要顧到，所以取外接矩形 —— 只盯一般對白框的話，
        NPC 對白被別的視窗蓋住時不會退回 PrintWindow，就會抓到蓋在上面的東西。
        """
        boxes = [r for r in (self.body_roi, self.npc_body_roi) if r]
        if not boxes:
            return None
        left = min(r[0] for r in boxes)
        top = min(r[1] for r in boxes)
        return (left, top,
                max(r[0] + r[2] for r in boxes) - left,
                max(r[1] + r[3] for r in boxes) - top)

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

        mask = _build(MaskConfig, data.get("mask"))
        speaker_mask_raw = data.get("speaker_mask")
        return cls(
            name=data.get("name", "default"),
            window_title=data.get("window_title"),
            capture_region=rect(data.get("capture_region")),
            capture_backend=data.get("capture_backend", "auto"),
            body_roi=rect(data.get("body_roi")),
            speaker_roi=rect(data.get("speaker_roi")),
            npc_body_roi=rect(data.get("npc_body_roi")),
            npc_speaker_roi=rect(data.get("npc_speaker_roi")),
            mask=mask,
            speaker_mask=_build(MaskConfig, speaker_mask_raw) if speaker_mask_raw else None,
            npc_mask=_build(MaskConfig, data["npc_mask"]) if data.get("npc_mask") else None,
            npc_speaker_mask=(_build(MaskConfig, data["npc_speaker_mask"])
                              if data.get("npc_speaker_mask") else None),
            stability=_build(StabilityConfig, data.get("stability")),
            ocr=_build(OcrConfig, data.get("ocr")),
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


def _spec(region: str) -> tuple[str, str, str, str]:
    for spec in REGION_SPECS:
        if spec[0] == region:
            return spec
    raise KeyError(f"沒有這種範圍：{region}")
