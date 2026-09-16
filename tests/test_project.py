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


class TestGuiSettings:
    """介面設定要能跨重開保存，而且舊設定檔缺欄位時不能壞掉。"""

    def test_new_fields_round_trip(self, tmp_path):
        from lqa.gui.settings import GuiSettings

        path = tmp_path / "gui.json"
        settings = GuiSettings(developer_mode=True, auto_poll_ms=120,
                               accent="teal", dark=False)
        settings.save(path)
        loaded = GuiSettings.load(path)
        assert loaded.developer_mode is True
        assert loaded.auto_poll_ms == 120
        assert loaded.accent == "teal"
        assert loaded.dark is False

    def test_old_settings_file_gets_defaults(self, tmp_path):
        import json

        from lqa.gui.settings import GuiSettings

        path = tmp_path / "gui.json"
        path.write_text(json.dumps({"dark": True, "accent": "blue"}),
                        encoding="utf-8")
        loaded = GuiSettings.load(path)
        assert loaded.developer_mode is False
        assert loaded.auto_poll_ms > 0

    def test_missing_hotkeys_are_filled_in(self, tmp_path):
        import json

        from lqa.gui.settings import DEFAULT_HOTKEYS, GuiSettings

        path = tmp_path / "gui.json"
        path.write_text(json.dumps({"hotkeys": {"shoot": "f9"}}), encoding="utf-8")
        loaded = GuiSettings.load(path)
        assert set(loaded.hotkeys) == set(DEFAULT_HOTKEYS)
        assert loaded.hotkeys["shoot"] == "f9"

    def test_corrupt_settings_file_falls_back(self, tmp_path):
        from lqa.gui.settings import GuiSettings

        path = tmp_path / "gui.json"
        path.write_text("{ not json", encoding="utf-8")
        assert GuiSettings.load(path).accent == "blue"

    def test_clear_hotkey_exists(self):
        """清除這條截圖的熱鍵，讓拍錯時不必整輪重來。"""
        from lqa.gui.settings import DEFAULT_HOTKEYS, HOTKEY_LABELS

        assert "clear" in DEFAULT_HOTKEYS
        assert "clear" in HOTKEY_LABELS


class TestClearSheetWithAnOpenStore:
    """清除頁簽時 lines.jsonl 可能正開著。

    SessionStore 一建立就用附加模式握著這個檔，而 Windows 不讓人刪除
    開啟中的檔案。先前這裡會丟 PermissionError，整個清除從中間斷掉 ——
    截圖已經刪了，進度紀錄沒更新，介面也沒重畫，所以綠點還留在原地。
    """

    def _sheet_with_shots(self, tmp_path):
        from lqa.record.project import Project

        script = tmp_path / "文本.xlsx"
        script.write_bytes(b"x")
        project = Project(str(script), root=tmp_path / "projects")
        shots = project.sheet_dir("AVG1") / "shots"
        shots.mkdir(parents=True)
        for index in range(3):
            (shots / f"{index:05d}.png").write_bytes(b"png")
        project.record_shot("AVG1", 0, "80201001")
        return project

    def test_clearing_succeeds_while_the_store_is_open(self, tmp_path):
        project = self._sheet_with_shots(tmp_path)
        store = project.store("AVG1")          # 這會開著 lines.jsonl
        try:
            removed = project.clear_sheet("AVG1")
        finally:
            store.close()
        assert removed == 3

    def test_progress_is_really_reset(self, tmp_path):
        """截圖刪了但狀態沒更新的話，介面會繼續顯示已經不存在的進度。"""
        project = self._sheet_with_shots(tmp_path)
        store = project.store("AVG1")
        try:
            project.clear_sheet("AVG1")
        finally:
            store.close()
        progress = project.progress("AVG1")
        assert progress.taken == 0
        assert progress.dialogue_ids == {}

    def test_screenshots_are_gone_from_disk(self, tmp_path):
        project = self._sheet_with_shots(tmp_path)
        store = project.store("AVG1")
        try:
            project.clear_sheet("AVG1")
        finally:
            store.close()
        assert list((project.sheet_dir("AVG1") / "shots").glob("*.png")) == []
