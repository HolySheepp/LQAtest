"""影像讀寫必須支援非 ASCII 路徑。

cv2.imread / cv2.imwrite 在 Windows 上走 ANSI API，路徑含中文就會壞掉：
實測寫出來的檔名夾雜私用區字元，大部分檔案根本沒落地，
而且 imwrite 只回傳 False 不丟例外，錯誤會被整個吞掉。

診斷圖的檔名本來就是中文，session 名稱也由使用者自訂，
所以這條路一定要是 Unicode 安全的。
"""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("cv2", reason="影像讀寫需要 opencv")

from lqa.imageio import imread, imwrite  # noqa: E402


@pytest.fixture
def sample_image():
    image = np.zeros((20, 40, 3), np.uint8)
    image[5:15, 10:30] = (30, 200, 250)
    return image


class TestNonAsciiPaths:
    @pytest.mark.parametrize(
        "name",
        ["01_對白框_遮罩.png", "01_送進OCR.png", "測試_ROI標示.png", "日本語テスト.png"],
        ids=["mask", "ocr-input", "roi", "kana"],
    )
    def test_roundtrip_with_chinese_filename(self, tmp_path, sample_image, name):
        path = imwrite(tmp_path / name, sample_image)
        assert path.exists(), "檔案沒有落地"
        assert path.name == name, "檔名被改掉了（編碼問題）"
        assert np.array_equal(imread(path), sample_image)

    def test_roundtrip_with_chinese_directory(self, tmp_path, sample_image):
        path = imwrite(tmp_path / "錄製結果" / "第一章" / "shot.png", sample_image)
        assert path.exists()
        assert np.array_equal(imread(path), sample_image)

    def test_parent_directories_are_created(self, tmp_path, sample_image):
        path = imwrite(tmp_path / "a" / "b" / "c" / "圖.png", sample_image)
        assert path.exists()


class TestFormats:
    def test_writes_grayscale_mask(self, tmp_path):
        mask = np.zeros((16, 24), np.uint8)
        mask[4:12, 6:18] = 255
        path = imwrite(tmp_path / "遮罩.png", mask)
        loaded = imread(path)
        assert loaded is not None
        # imread 預設回三通道，內容仍應一致
        assert np.array_equal(loaded[:, :, 0], mask)

    def test_jpeg_quality_is_applied(self, tmp_path, sample_image):
        big = np.random.default_rng(0).integers(
            0, 255, (200, 200, 3), dtype=np.uint8
        )
        low = imwrite(tmp_path / "低.jpg", big, quality=10)
        high = imwrite(tmp_path / "高.jpg", big, quality=95)
        assert low.stat().st_size < high.stat().st_size


class TestMissingFiles:
    def test_missing_file_returns_none(self, tmp_path):
        assert imread(tmp_path / "不存在.png") is None

    def test_directory_returns_none(self, tmp_path):
        assert imread(tmp_path) is None

    def test_empty_file_returns_none(self, tmp_path):
        path = tmp_path / "空的.png"
        path.write_bytes(b"")
        assert imread(path) is None


class TestSessionScreenshots:
    def test_session_with_chinese_name_saves_screenshots(self, tmp_path, sample_image):
        from lqa.record.store import SessionStore

        store = SessionStore(tmp_path, "第一章_校園")
        try:
            rel = store.save_screenshot(sample_image, 0, quality=85)
        finally:
            store.close()
        assert rel == "shots/00000.jpg"
        assert (store.dir / rel).exists()
