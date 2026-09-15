"""命令列介面。

    python -m lqa windows                     列出視窗標題，用來設定 profile
    python -m lqa calibrate  ...              框選對白框與姓名框，產生 profile
    python -m lqa record     ...              錄製（你自己手動點，程式只負責記錄）
    python -m lqa compare    ...              離線比對並輸出報告
    python -m lqa check-script ...            檢查翻譯文本能不能正確解析
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import Profile
from .model import CATEGORY_LABEL_ZH, Category


def _cmd_windows(args: argparse.Namespace) -> int:
    from .capture.window import list_windows

    windows = list_windows()
    if args.filter:
        needle = args.filter.lower()
        windows = [
            w for w in windows
            if needle in w.title.lower() or needle in w.process.lower()
        ]

    if not windows:
        print("找不到符合的視窗。")
        return 0

    print("")
    print(f"{'尺寸':<12}{'程序':<24}標題")
    print("-" * 76)
    for w in windows:
        size = f"{w.width}x{w.height}" if w.width else "-"
        mark = ""
        if w.is_emulator:
            mark = "   <-- 這個就是模擬器畫面"
        elif w.is_emulator_manager:
            mark = "   （多開管理器，不是遊戲畫面）"
        print(f"{size:<12}{w.process:<24}{w.title}{mark}")

    emulators = [w for w in windows if w.is_emulator]
    print("")
    if emulators:
        target = emulators[0]
        print(f"偵測到模擬器視窗：「{target.title}」 {target.width}x{target.height}")
        print("接著執行：")
        print(f'  lqa calibrate --window "{target.title}"')
    else:
        managers = [w for w in windows if w.is_emulator_manager]
        if managers:
            print("只看到多開管理器，沒看到模擬器本身。")
            print("請從管理器啟動一個模擬器實例，等遊戲畫面出現後再跑一次。")
        else:
            print("清單裡沒有認得出來的模擬器程序。")
            print("如果你知道是哪一個視窗，直接用它的標題：")
            print('  lqa calibrate --window "視窗標題"')
    return 0


DEFAULT_SCRIPT_DIR = Path("scripts")
DEFAULT_SPEAKERS = Path("config/speakers.csv")


def _resolve_script(path: str | None) -> str:
    """沒指定 --script 時，自動找 scripts/ 裡唯一的一份文本。

    這樣一般情況下不必打長路徑，也少一個打錯字的機會。
    """
    if path:
        return path
    if not DEFAULT_SCRIPT_DIR.is_dir():
        raise FileNotFoundError(
            f"沒有指定 --script，而且找不到 {DEFAULT_SCRIPT_DIR}/ 資料夾。"
            "請把翻譯文本放進 scripts/，或用 --script 指定路徑。"
        )
    found = sorted(
        p for p in DEFAULT_SCRIPT_DIR.iterdir()
        if p.suffix.lower() in (".xlsx", ".xlsm", ".csv", ".tsv") and not p.name.startswith("~$")
    )
    if not found:
        raise FileNotFoundError(
            f"{DEFAULT_SCRIPT_DIR}/ 裡沒有任何 xlsx / csv / tsv 檔案。"
            "請把翻譯文本放進去。"
        )
    if len(found) > 1:
        names = "、".join(p.name for p in found)
        raise ValueError(
            f"{DEFAULT_SCRIPT_DIR}/ 裡有多份文本（{names}），"
            "請用 --script 指定要用哪一份。"
        )
    print(f"使用文本：{found[0]}")
    return str(found[0])


def _resolve_speakers(path: str | None) -> str | None:
    """沒指定 --speakers 時，自動用 config/speakers.csv（存在的話）。"""
    if path:
        return path
    if DEFAULT_SPEAKERS.exists():
        print(f"使用發話者對照表：{DEFAULT_SPEAKERS}")
        return str(DEFAULT_SPEAKERS)
    return None


def _cmd_start(_args: argparse.Namespace) -> int:
    """檢查目前進度，並明確指出下一步該下哪個指令。

    使用者不是工程背景，最容易卡住的不是指令本身，
    而是不知道自己走到哪一步、下一步該做什麼。
    """
    from .compare.script_loader import list_dialogue_sheets, load_speaker_map

    print("")
    print("=== LQA 檢查工具 ===")
    print("")

    todo: list[str] = []

    # 1. 翻譯文本
    script: str | None = None
    sheets: list = []
    try:
        found = sorted(
            p for p in DEFAULT_SCRIPT_DIR.iterdir()
            if p.suffix.lower() in (".xlsx", ".xlsm", ".csv", ".tsv")
            and not p.name.startswith("~$")
        ) if DEFAULT_SCRIPT_DIR.is_dir() else []
        if not found:
            print("[ ] 翻譯文本    尚未放入。請把 xlsx 放進 scripts/ 資料夾")
            todo.append("把翻譯文本放進 scripts/ 資料夾")
        else:
            script = str(found[0])
            sheets = list_dialogue_sheets(script)
            extra = f"（另有 {len(found) - 1} 份，需用 --script 指定）" if len(found) > 1 else ""
            print(f"[v] 翻譯文本    {found[0].name}{extra}")
            if sheets:
                names = "、".join(f"{s.name}({s.line_count}句)" for s in sheets)
                print(f"                對白頁簽：{names}")
            else:
                print("                找不到對白頁簽，請用 lqa check-script 看詳情")
    except (OSError, ValueError) as exc:
        print(f"[!] 翻譯文本    讀取失敗：{exc}")

    # 2. 發話者對照表
    if DEFAULT_SPEAKERS.exists():
        try:
            mapping = load_speaker_map(DEFAULT_SPEAKERS)
            filled = len(mapping)
            total = filled
            if script:
                from .compare.script_loader import ALL_SHEETS, load_script

                names = {ln.speaker_zh for ln in load_script(script, sheets=ALL_SHEETS)
                         if ln.speaker_zh}
                total = len(names)
                filled = len(names & set(mapping))
            print(f"[v] 發話者對照  {DEFAULT_SPEAKERS}  已填 {filled}/{total}")
            if filled < total:
                print("                未填的發話者不會被檢查，可以之後再補")
        except (OSError, ValueError) as exc:
            print(f"[!] 發話者對照  讀取失敗：{exc}")
    elif script:
        print("[ ] 發話者對照  尚未產生")
        todo.append("lqa speakers          產生發話者對照表，再用 Excel 填英文名")

    # 3. profile
    profile_path = Path("config/profile.json")
    if profile_path.exists():
        try:
            profile = Profile.load(profile_path)
            speaker = profile.speaker_roi or "未設定"
            print(f"[v] 校準設定    {profile_path}")
            print(f"                對白框 {profile.body_roi}  姓名框 {speaker}")
            print(f"                取字方式 {profile.mask.method}")
        except (OSError, ValueError) as exc:
            print(f"[!] 校準設定    讀取失敗：{exc}")
    else:
        print("[ ] 校準設定    尚未校準")
        todo.append("lqa windows           先找出雷電模擬器的視窗標題")
        todo.append("lqa calibrate --window 雷電模擬器")

    # 4. 截圖與辨識
    from .record.store import session_shots

    sessions_dir = Path("sessions")
    sessions = sorted(
        (p for p in sessions_dir.iterdir() if p.is_dir()), reverse=True
    ) if sessions_dir.is_dir() else []
    latest = sessions[0] if sessions else None
    shots = len(session_shots(latest)) if latest else 0
    read_done = bool(latest and (latest / "lines.jsonl").exists())

    # 只認手動截圖的 session；舊的自動錄製沒有 shots，流程不同
    manual = [s for s in sessions if session_shots(s)]
    latest = manual[0] if manual else None
    shots = len(session_shots(latest)) if latest else 0
    read_done = bool(latest and (latest / "lines.jsonl").exists())

    if latest is None:
        note = f"（另有 {len(sessions)} 個舊的自動錄製 session）" if sessions else ""
        print(f"[ ] 截圖        尚未開始{note}")
    else:
        print(f"[v] 截圖        共 {len(manual)} 次，最新：{latest.name}（{shots} 張）")
        print(f"[{'v' if read_done else ' '}] 辨識        "
              + ("已完成" if read_done else "尚未辨識"))

    print("")
    print("下一步：")
    if todo:
        for item in todo:
            print(f"  {item}")
    elif latest is None:
        print("  lqa probe             先診斷校準對不對（很重要，不要跳過）")
        print("  lqa shoot --name ch1  看到句子顯示完整就按 F9 拍一張")
    elif not read_done:
        print(f"  lqa read sessions\\{latest.name}")
    else:
        print(f"  lqa show-session sessions\\{latest.name} --full    檢查辨識結果")
        print(f"  lqa compare --session sessions\\{latest.name} --out reports\\r1")
    print("")
    print("每個指令加 --help 可看完整參數。")
    return 0


def _cmd_speakers(args: argparse.Namespace) -> int:
    """從文本抓出所有發話者，產生對照表範本讓使用者填英文名。"""
    import csv as _csv

    from .compare.script_loader import load_script, load_speaker_map

    from .compare.script_loader import ALL_SHEETS

    script = _resolve_script(args.script)
    out = Path(args.out)

    existing: dict[str, str] = {}
    if out.exists():
        existing = load_speaker_map(out)
        print(f"已有 {out}，保留其中 {len(existing)} 筆已填好的英文名。")

    # 掃全部工作表，這樣對照表一次就涵蓋整個活動所有場景
    names: list[str] = []
    for line in load_script(script, sheets=ALL_SHEETS):
        if line.speaker_zh and line.speaker_zh not in names:
            names.append(line.speaker_zh)

    if not names:
        print("文本裡沒有任何發話者（名字欄都是空的），不需要對照表。")
        return 0

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as fh:
        writer = _csv.writer(fh)
        writer.writerow(["名字", "English"])
        for name in names:
            writer.writerow([name, existing.get(name, "")])

    blank = [n for n in names if not existing.get(n)]
    print(f"已寫出 {out}，共 {len(names)} 位發話者。")
    if blank:
        print("")
        print(f"還有 {len(blank)} 位沒有英文名，請用 Excel 或記事本打開檔案填第二欄：")
        for name in blank:
            print(f"  {name}")
        print("")
        print("留空的發話者不會被檢查，不影響對白比對，可以之後再補。")
    return 0


def _script_colors(script: str | None) -> list[str]:
    """把文本裡用過的 <color=#xxxxxx> 全部掃出來，當作 colorkey 的預設色盤。"""
    if not script:
        return ["#fefefe"]
    from .compare.normalize import extract_colors
    from .compare.script_loader import ALL_SHEETS, load_script

    found: set[str] = {"#fefefe"}
    for line in load_script(script, sheets=ALL_SHEETS):
        found |= extract_colors(line.target_en)
    return sorted(found)


def _cmd_colors(args: argparse.Namespace) -> int:
    import json

    colors = _script_colors(_resolve_script(args.script))
    print(f"文本中用到的文字顏色共 {len(colors)} 種（含預設白色）：")
    for c in colors:
        print(f"  {c}")
    print("")
    print("若要改用 colorkey 取字，把下面這行填進 profile 的 mask.text_colors：")
    print(f"  {json.dumps(colors)}")
    print("")
    print("提示：預設的 value 取字方式不需要色盤，通常直接用就好。")
    return 0


def _cmd_calibrate(args: argparse.Namespace) -> int:
    from .tools_calibrate import run_calibration

    return run_calibration(
        profile_path=Path(args.profile),
        window_title=args.window,
        region=tuple(args.region) if args.region else None,
        text_colors=_script_colors(args.script),
    )


def _cmd_probe(args: argparse.Namespace) -> int:
    from .tools_probe import run_probe

    return run_probe(
        profile_path=Path(args.profile),
        out_dir=Path(args.out),
        samples=args.samples,
        interval=args.interval,
        image=Path(args.image) if args.image else None,
        compare_sources=args.sources,
    )


def _cmd_show_session(args: argparse.Namespace) -> int:
    from .compare.normalize import display_key, has_cjk, strip_speaker_id
    from .record.store import load_session

    lines = load_session(args.session)
    print(f"共 {len(lines)} 句，來源：{args.session}")
    print("")
    width = args.width
    for line in lines:
        body = display_key(line.body_text)
        if not args.full and len(body) > width:
            body = body[: width - 3] + "..."
        speaker = strip_speaker_id(line.speaker_text)
        prefix = f"[{speaker}] " if speaker else ""
        flags = []
        if has_cjk(line.body_text):
            flags.append("中文")
        if line.still_growing:
            flags.append("可能沒顯示完")
        if 0 < line.samples <= 2:
            flags.append(f"只取樣{line.samples}次")
        flag = f"  <{' / '.join(flags)}>" if flags else ""
        print(f"{line.seq + 1:>4}. {prefix}{body}{flag}")
        if args.verbose:
            print(f"      信心值 {line.body_conf:.3f}  取樣 {line.samples} 次  "
                  f"截圖 {line.screenshot}")

    risky = [c for c in lines if c.still_growing or (0 < c.samples <= 2)]
    if risky:
        print("")
        print(f"提醒：{len(risky)} 句在換到下一句時文字還在增加，或取樣次數過少，")
        print("      可能沒抓到它顯示完整的樣子。點慢一點可以改善。")
    return 0


def _cmd_shoot(args: argparse.Namespace) -> int:
    from .record.shooter import Shooter
    from .record.store import SessionStore

    profile = Profile.load(args.profile)
    store = SessionStore(args.out, args.name)

    print("")
    print("=== 手動截圖 ===")
    print(f"存放位置：{store.dir}")
    print("")
    print(f"  {args.key.upper():<6} 截圖（看到這句顯示完整了再按）")
    print(f"  {args.undo_key.upper():<6} 取消上一張")
    print("  Ctrl+C  結束")
    print("")
    print("截圖當下不做辨識，所以按下去幾乎沒有延遲，模擬器也不會變慢。")
    print("辨識與比對留到之後離線做。")
    print("")

    def on_shot(count: int, _path: str) -> None:
        print(f"  第 {count} 張")

    shooter = Shooter(profile, store, shoot_key=args.key,
                      undo_key=args.undo_key, on_shot=on_shot)
    try:
        total = shooter.run()
    finally:
        store.close()

    print("")
    print(f"共 {total} 張截圖：{store.dir}")
    if total:
        print("接著執行：")
        print(f"  lqa read {store.dir}")
    return 0


def _ensure_read(session: str) -> None:
    """session 有截圖但還沒辨識的話，先跑一次辨識。"""
    from .record.reader import read_session
    from .record.store import session_shots

    path = Path(session)
    if (path / "lines.jsonl").exists():
        return
    if not session_shots(path):
        return
    print(f"{session} 尚未辨識，先跑一次 OCR：")
    read_session(path, on_progress=lambda done, total, _l: (
        print(f"  {done}/{total}") if done % 10 == 0 or done == total else None
    ))
    print("")


def _cmd_read(args: argparse.Namespace) -> int:
    from .compare.normalize import display_key
    from .record.reader import read_session

    profile = Profile.load(args.profile) if args.profile else None

    def on_progress(done: int, total: int, line) -> None:
        preview = display_key(line.body_text)
        if len(preview) > 60:
            preview = preview[:57] + "..."
        print(f"  {done:>4}/{total}  {preview}")

    lines = read_session(args.session, profile=profile, on_progress=on_progress,
                         do_clean=not args.no_clean)
    print("")
    print(f"辨識完成，共 {len(lines)} 句。")
    print("接著執行：")
    print(f"  lqa compare --session {args.session} --out reports/r1")
    return 0


def _cmd_record(args: argparse.Namespace) -> int:
    from .record.recorder import Recorder
    from .record.store import SessionStore

    profile = Profile.load(args.profile)
    store = SessionStore(args.out, args.name)

    print(f"錄製中 -> {store.dir}")
    print("請正常遊玩並手動推進對話。按 Ctrl+C 結束錄製。")
    print("提示：文字速度調成二倍速即可，程式會等文字停止變動才擷取。")
    print("")

    def on_line(line) -> None:
        speaker = f"[{line.speaker_text}] " if line.speaker_text else ""
        preview = line.body_text if len(line.body_text) <= 70 else line.body_text[:67] + "..."
        print(f"{line.seq + 1:>4}. {speaker}{preview}")

    recorder = Recorder(profile, store, on_line=on_line)
    try:
        total = recorder.run(max_lines=args.max_lines, max_seconds=args.max_seconds)
    finally:
        store.close()

    print("")
    print(f"錄製結束，共 {total} 句。結果：{store.dir}")
    return 0


def _cmd_compare(args: argparse.Namespace) -> int:
    from .compare.classify import CompareConfig, compare
    from .compare.report import print_summary, write_csv, write_xlsx
    from .compare.script_loader import load_script, load_speaker_map, unknown_speakers
    from .record.store import load_sessions

    speaker_map = load_speaker_map(_resolve_speakers(args.speakers))
    expected = load_script(_resolve_script(args.script), speaker_map, sheets=args.sheet)

    # 手動截圖的 session 還沒辨識過的話，先辨識再比對，不用多下一道指令
    for session in args.session:
        _ensure_read(session)
    captured = load_sessions(list(args.session))

    unknown = unknown_speakers(expected)
    if unknown:
        print("提醒：以下發話者在對照表中沒有英文名，發話者檢查會略過這些句子：")
        print("  " + "、".join(unknown))
        print("")

    cfg = CompareConfig()
    if args.pass_threshold is not None:
        cfg.pass_threshold = args.pass_threshold

    result = compare(expected, captured, cfg)
    print_summary(result)

    out = Path(args.out)
    xlsx_path = write_xlsx(result, out.with_suffix(".xlsx"), include_pass=True)
    csv_path = write_csv(result, out.with_suffix(".csv"), include_pass=args.include_pass)
    print(f"報告已輸出：{xlsx_path}")
    print(f"           {csv_path}")

    if args.show:
        for issue in result.problems[: args.show]:
            print("")
            print(f"[{CATEGORY_LABEL_ZH[issue.category]}] {issue.dialogue_id} {issue.detail}")
            if issue.expected:
                print(f"  文本: {issue.expected.target_en}")
            if issue.captured:
                print(f"  畫面: {issue.captured.body_text}")

    problems = len(result.problems)
    return 1 if (args.fail_on_issue and problems) else 0


def _cmd_check_script(args: argparse.Namespace) -> int:
    from .compare.script_loader import (
        list_dialogue_sheets,
        load_script,
        load_speaker_map,
        unknown_speakers,
    )

    script = _resolve_script(args.script)
    speaker_map = load_speaker_map(_resolve_speakers(args.speakers))

    sheets = list_dialogue_sheets(script)
    if not sheets:
        print("找不到任何對白工作表。需要至少有『對話ID』與『英文翻譯』兩個欄位標題。")
        return 2

    print("")
    print(f"找到 {len(sheets)} 個對白頁簽：")
    for info in sheets:
        print(f"  {info.name:<12}{info.line_count:>6} 句   "
              f"ID {info.first_id} ~ {info.last_id}")
    print("")

    if not args.sheet and len(sheets) > 1:
        print("要看某個頁簽的內容，加上 --sheet 指定，例如：")
        print(f"  lqa check-script --sheet {sheets[0].name}")
        print("比對時也一樣要指定，或用 --sheet all 把全部串成一條序列。")
        return 0

    lines = load_script(script, speaker_map, sheets=args.sheet)
    print(f"解析成功，共 {len(lines)} 句對話。")
    empty_target = [ln for ln in lines if not ln.target_en.strip()]
    if empty_target:
        print(f"警告：{len(empty_target)} 句沒有英文翻譯（列號 "
              f"{', '.join(str(l.sheet_row) for l in empty_target[:10])}...）")

    unknown = unknown_speakers(lines)
    if unknown:
        print(f"缺少英文名的發話者（{len(unknown)}）：" + "、".join(unknown))

    from .compare.normalize import display_key, extract_colors

    colors: set[str] = set()
    for ln in lines:
        colors |= extract_colors(ln.target_en)
    if colors:
        print(f"文本用到 {len(colors)} 種變色標記：" + "、".join(sorted(colors)))

    print("")
    print("前 5 句（已剝除標記，這是實際會拿去比對的內容）：")
    for ln in lines[:5]:
        speaker = ln.speaker_en or ln.speaker_zh or "(旁白)"
        print(f"  [{ln.dialogue_id}] {speaker}: {display_key(ln.target_en)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lqa", description="遊戲在地化品質檢查工具")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("start", help="檢查目前進度，並指出下一步該做什麼")
    p.set_defaults(func=_cmd_start)

    p = sub.add_parser("windows", help="列出可見視窗，並標出哪個是模擬器")
    p.add_argument("--filter", default=None, help="只顯示標題或程序名稱含有此字串的視窗")
    p.set_defaults(func=_cmd_windows)

    p = sub.add_parser("calibrate", help="框選對白框與姓名框，產生 profile")
    p.add_argument("--profile", default="config/profile.json", help="輸出的 profile 路徑")
    p.add_argument("--window", default="雷電模擬器", help="要擷取的視窗標題（部分符合即可）")
    p.add_argument("--region", type=int, nargs=4, metavar=("X", "Y", "W", "H"),
                   help="改用絕對螢幕座標，不用視窗定位")
    p.add_argument("--script", default=None,
                   help="順便從翻譯文本掃出用過的文字顏色，當作 colorkey 的預設色盤")
    p.set_defaults(func=_cmd_calibrate)

    p = sub.add_parser("colors", help="掃出翻譯文本用過的所有文字顏色")
    p.add_argument("--script", default=None, help="不給就自動用 scripts/ 裡唯一的文本")
    p.set_defaults(func=_cmd_colors)

    p = sub.add_parser("speakers", help="從文本掃出所有發話者，產生中英對照表範本")
    p.add_argument("--script", default=None, help="不給就自動用 scripts/ 裡唯一的文本")
    p.add_argument("--out", default=str(DEFAULT_SPEAKERS), help="對照表輸出路徑")
    p.set_defaults(func=_cmd_speakers)

    p = sub.add_parser("probe", help="抓一張畫面診斷 profile，正式錄製前先跑這個")
    p.add_argument("--profile", default="config/profile.json")
    p.add_argument("--out", default="debug", help="診斷圖檔輸出目錄")
    p.add_argument("--samples", type=int, default=1, help="連抓幾張")
    p.add_argument("--interval", type=float, default=1.0, help="每張之間隔幾秒")
    p.add_argument("--image", default=None,
                   help="改為診斷現成截圖，不抓螢幕（尺寸需與校準時一致）")
    p.add_argument("--sources", action="store_true",
                   help="逐一比較各種取字方式的辨識結果，用來挑最準的那個")
    p.set_defaults(func=_cmd_probe)

    p = sub.add_parser("show-session", help="列出某次錄製抓到的所有句子")
    p.add_argument("session", help="session 目錄")
    p.add_argument("--full", action="store_true", help="不截斷長句")
    p.add_argument("--width", type=int, default=80, help="截斷寬度")
    p.add_argument("--verbose", action="store_true", help="一併顯示信心值與截圖路徑")
    p.set_defaults(func=_cmd_show_session)

    p = sub.add_parser("shoot", help="手動截圖：看到句子顯示完整就按鍵拍一張")
    p.add_argument("--profile", default="config/profile.json")
    p.add_argument("--out", default="sessions", help="session 存放根目錄")
    p.add_argument("--name", default=None, help="這次的名稱，例如 ch1_school")
    p.add_argument("--key", default="f9", help="截圖鍵，預設 f9")
    p.add_argument("--undo-key", default="f10", help="取消上一張的按鍵，預設 f10")
    p.set_defaults(func=_cmd_shoot)

    p = sub.add_parser("read", help="把截圖離線辨識成文字")
    p.add_argument("session", help="session 目錄")
    p.add_argument("--profile", default=None,
                   help="不給就用截圖當下存在 meta.json 裡的那組設定")
    p.add_argument("--no-clean", action="store_true",
                   help="不要自動合併相鄰的重複與半句")
    p.set_defaults(func=_cmd_read)

    p = sub.add_parser("record", help="自動錄製（實驗中，偵測打字結束並不可靠）")
    p.add_argument("--profile", default="config/profile.json")
    p.add_argument("--out", default="sessions", help="session 存放根目錄")
    p.add_argument("--name", default=None, help="這次錄製的名稱，例如 ch1_school")
    p.add_argument("--max-lines", type=int, default=None)
    p.add_argument("--max-seconds", type=float, default=None)
    p.set_defaults(func=_cmd_record)

    p = sub.add_parser("compare", help="把錄製結果與翻譯文本比對並輸出報告")
    p.add_argument("--script", default=None,
                   help="翻譯文本 xlsx / csv / tsv，不給就自動用 scripts/ 裡唯一的文本")
    p.add_argument("--session", required=True, nargs="+", help="一個或多個 session 目錄")
    p.add_argument("--speakers", default=None,
                   help="發話者中英對照表 csv，不給就自動用 config/speakers.csv")
    p.add_argument("--sheet", default=None, nargs="+", metavar="NAME",
                   help="要比對的頁簽，例如 --sheet AVG1；可給多個，或用 all 表示全部")
    p.add_argument("--out", default="reports/lqa_report", help="輸出檔名（不含副檔名）")
    p.add_argument("--pass-threshold", type=float, default=None)
    p.add_argument("--include-pass", action="store_true", help="csv 也輸出一致的句子")
    p.add_argument("--show", type=int, default=0, help="在終端機列出前 N 筆問題")
    p.add_argument("--fail-on-issue", action="store_true", help="有問題時回傳非 0 結束碼")
    p.set_defaults(func=_cmd_compare)

    p = sub.add_parser("check-script", help="列出頁簽並檢查翻譯文本是否能正確解析")
    p.add_argument("--script", default=None, help="不給就自動用 scripts/ 裡唯一的文本")
    p.add_argument("--speakers", default=None, help="不給就自動用 config/speakers.csv")
    p.add_argument("--sheet", default=None, nargs="+", metavar="NAME",
                   help="要檢視的頁簽；不給則只列出有哪些頁簽")
    p.set_defaults(func=_cmd_check_script)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (FileNotFoundError, ValueError, RuntimeError, ImportError) as exc:
        print(f"錯誤：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
