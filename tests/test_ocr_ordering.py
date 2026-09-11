"""OCR 偵測框的排序測試。

OCR 會把一行對白切成好幾個框回傳，順序不保證。排錯的話整句會被打散，
比對階段就會變成假的「不一致」，而且畫面看起來完全正常，很難察覺。
"""

from lqa.ocr.base import OcrLine, order_lines


def line(text: str, x0: int, y0: int, x1: int, y1: int) -> OcrLine:
    return OcrLine(text=text, confidence=0.99, box=(x0, y0, x1, y1))


def joined(lines: list[OcrLine]) -> str:
    return " ".join(ln.text for ln in order_lines(lines))


class TestOrderLines:
    def test_same_row_sorted_left_to_right(self):
        out = joined([
            line("world", 120, 10, 200, 30),
            line("Hello", 10, 10, 100, 30),
        ])
        assert out == "Hello world"

    def test_rows_sorted_top_to_bottom(self):
        out = joined([
            line("second", 10, 60, 100, 80),
            line("first", 10, 10, 100, 30),
        ])
        assert out == "first second"

    def test_ragged_y_within_a_row_does_not_interleave(self):
        """回歸測試。

        同一行各框的 y_min 會因為字母升部與降部差好幾個像素
        （"This" 有 T 的升部、"your" 有 y 的降部）。
        嚴格按 y_min 排序會讓兩行交錯，實際輸出過
        'd for your hard work. the reward f This is Congratulations.'
        """
        out = joined([
            line("This is", 10, 12, 80, 30),          # 第一行，y_min 偏大
            line("the reward", 85, 8, 180, 32),       # 第一行，y_min 偏小
            line("for your hard work.", 185, 10, 340, 34),
            line("Congratulations.", 10, 40, 150, 62),  # 第二行
        ])
        assert out == "This is the reward for your hard work. Congratulations."

    def test_three_rows_keep_their_order(self):
        out = joined([
            line("c", 10, 80, 40, 100),
            line("a", 10, 10, 40, 30),
            line("b2", 50, 45, 80, 65),
            line("b1", 10, 47, 40, 63),
        ])
        assert out == "a b1 b2 c"

    def test_boxless_lines_go_last_without_crashing(self):
        out = joined([
            OcrLine(text="tail", confidence=0.5, box=None),
            line("head", 10, 10, 40, 30),
        ])
        assert out == "head tail"

    def test_all_boxless_input_is_preserved(self):
        lines = [OcrLine(text="a", confidence=0.5), OcrLine(text="b", confidence=0.5)]
        assert [ln.text for ln in order_lines(lines)] == ["a", "b"]

    def test_empty_input(self):
        assert order_lines([]) == []

    def test_tall_and_short_boxes_on_one_row_stay_together(self):
        """同一行裡有大小字（<size> 標記造成）時仍要歸成同一行。"""
        out = joined([
            line("BIG", 90, 4, 160, 40),     # 放大的字，框比較高
            line("small", 10, 14, 80, 30),
            line("next line", 10, 60, 100, 80),
        ])
        assert out == "small BIG next line"
