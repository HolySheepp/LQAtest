"""專案：一份文本的截圖與進度要能跨頁簽、跨重開保存。

先前截圖只存在記憶體裡，session 目錄還帶時間戳，所以切個頁簽
或關掉軟體，進度就看不見了。
"""

from __future__ import annotations

import numpy as np
import pytest

from lqa.record.project import Project, safe_name


def frame(value: int = 30) -> np.ndarray:
    return np.full((10, 20, 3), value, np.uint8)


@pytest.fixture
def project(tmp_path):
    return Project("scripts/2026_校園活動 EN.xlsx", root=tmp_path)


class TestSafeName:
    def test_strips_path_characters(self):
        assert "/" not in safe_name("AVG/1")
        assert ":" not in safe_name("A:B")

    def test_keeps_chinese(self):
        assert safe_name("活動簡介") == "活動簡介"

    def test_never_returns_empty(self):
        assert safe_name("...")
        assert safe_name("") == "untitled"


class TestPersistence:
    def test_shots_survive_a_new_project_object(self, tmp_path):
        """等同關掉軟體再開。"""
        first = Project("scripts/x.xlsx", root=tmp_path)
        first.save_shot("AVG6", 3, frame(), "80206004")
        second = Project("scripts/x.xlsx", root=tmp_path)
        assert second.progress("AVG6").shots.keys() == {3}

    def test_each_sheet_keeps_its_own_shots(self, project):
        """等同切換頁簽再切回來。"""
        project.save_shot("AVG1", 0, frame(), "a")
        project.save_shot("AVG2", 5, frame(), "b")
        assert set(project.progress("AVG1").shots) == {0}
        assert set(project.progress("AVG2").shots) == {5}

    def test_dialogue_id_is_remembered(self, project):
        project.save_shot("AVG6", 7, frame(), "80206008")
        assert project.progress("AVG6").dialogue_ids[7] == "80206008"

    def test_file_name_encodes_the_index(self, project):
        rel = project.save_shot("AVG6", 42, frame(), "x")
        assert rel.endswith("00042.png")
        assert (project.sheet_dir("AVG6") / rel).exists()

    def test_progress_trusts_the_files_not_the_record(self, project):
        """使用者可能手動刪過檔案，以紀錄為準的話介面會顯示不存在的截圖。"""
        project.save_shot("AVG6", 1, frame(), "a")
        (project.sheet_dir("AVG6") / "shots" / "00001.png").unlink()
        assert project.progress("AVG6").shots == {}

    def test_unrelated_files_are_ignored(self, project):
        project.save_shot("AVG6", 1, frame(), "a")
        (project.sheet_dir("AVG6") / "shots" / "notes.png").write_bytes(b"x")
        assert set(project.progress("AVG6").shots) == {1}

    def test_corrupt_state_falls_back_to_empty(self, tmp_path):
        p = Project("scripts/x.xlsx", root=tmp_path)
        p.state_path.write_text("{ not json", encoding="utf-8")
        assert Project("scripts/x.xlsx", root=tmp_path).progress("AVG1").taken == 0


class TestClearing:
    def test_clear_entry_removes_one_shot(self, project):
        project.save_shot("AVG6", 2, frame(), "a")
        project.save_shot("AVG6", 3, frame(), "b")
        assert project.clear_entry("AVG6", 2)
        progress = project.progress("AVG6")
        assert set(progress.shots) == {3}
        assert 2 not in progress.dialogue_ids

    def test_clear_entry_on_empty_slot(self, project):
        assert not project.clear_entry("AVG6", 9)

    def test_clear_sheet_removes_everything(self, project):
        for i in range(4):
            project.save_shot("AVG6", i, frame(), str(i))
        project.save_shot("AVG1", 0, frame(), "keep")
        assert project.clear_sheet("AVG6") == 4
        assert project.progress("AVG6").taken == 0
        assert project.progress("AVG1").taken == 1, "不該動到其他頁簽"

    def test_clear_sheet_drops_the_analysis(self, project):
        project.save_shot("AVG6", 0, frame(), "a")
        (project.sheet_dir("AVG6") / "lines.jsonl").write_text("x", encoding="utf-8")
        project.clear_sheet("AVG6")
        assert not (project.sheet_dir("AVG6") / "lines.jsonl").exists()

    def test_clearing_survives_a_reopen(self, tmp_path):
        first = Project("scripts/x.xlsx", root=tmp_path)
        first.save_shot("AVG6", 0, frame(), "a")
        first.clear_sheet("AVG6")
        assert Project("scripts/x.xlsx", root=tmp_path).progress("AVG6").taken == 0


class TestStaleDetection:
    """文本改動後索引會位移，舊截圖看起來還在卻對到了別條。"""

    def _expected(self, ids):
        from lqa.model import ExpectedLine

        return [ExpectedLine(order=i, dialogue_id=d) for i, d in enumerate(ids)]

    def test_matching_ids_are_not_stale(self, project):
        project.save_shot("AVG6", 1, frame(), "b")
        assert project.stale_entries("AVG6", self._expected(["a", "b", "c"])) == []

    def test_shifted_script_is_detected(self, project):
        project.save_shot("AVG6", 1, frame(), "b")
        # 前面插入一句，原本的 b 跑到索引 2
        assert project.stale_entries("AVG6", self._expected(["a", "new", "b"])) == [1]

    def test_shot_beyond_the_shortened_script(self, project):
        project.save_shot("AVG6", 5, frame(), "f")
        assert project.stale_entries("AVG6", self._expected(["a", "b"])) == [5]

    def test_shots_without_a_recorded_id_are_left_alone(self, project):
        """手動放進去的檔案沒有紀錄，不該被當成過期。"""
        store = project.store("AVG6")
        store.save_shot(frame(), 0)
        store.close()
        assert project.stale_entries("AVG6", self._expected(["a"])) == []


class TestAnalysedFlag:
    def test_marking_and_reading(self, project):
        project.save_shot("AVG6", 0, frame(), "a")
        assert not project.progress("AVG6").analysed
        project.mark_analysed("AVG6")
        assert project.progress("AVG6").analysed

    def test_new_shot_invalidates_the_analysis(self, project):
        project.save_shot("AVG6", 0, frame(), "a")
        project.mark_analysed("AVG6")
        project.save_shot("AVG6", 1, frame(), "b")
        assert not project.progress("AVG6").analysed


class TestSheetMeta:
    def test_meta_marks_bound_mode_for_the_reader(self, project):
        from lqa.config import Profile
        from lqa.record.store import session_meta

        profile = Profile(name="t", window_title="測試", body_roi=(0, 0, 10, 10))
        project.write_sheet_meta("AVG6", profile, ["AVG6"])
        meta = session_meta(project.sheet_dir("AVG6"))
        assert meta["mode"] == "bound"
        assert meta["sheets"] == ["AVG6"]
