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


def _cmd_windows(_args: argparse.Namespace) -> int:
    from .capture.window import ensure_dpi_aware, list_windows

    ensure_dpi_aware()
    for hwnd, title in list_windows():
        print(f"{hwnd:>10}  {title}")
    return 0


def _cmd_calibrate(args: argparse.Namespace) -> int:
    from .tools_calibrate import run_calibration

    return run_calibration(
        profile_path=Path(args.profile),
        window_title=args.window,
        region=tuple(args.region) if args.region else None,
    )


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

    speaker_map = load_speaker_map(args.speakers)
    expected = load_script(args.script, speaker_map, sheet=args.sheet)
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
    from .compare.script_loader import load_script, load_speaker_map, unknown_speakers

    speaker_map = load_speaker_map(args.speakers)
    lines = load_script(args.script, speaker_map, sheet=args.sheet)

    print(f"解析成功，共 {len(lines)} 句對話。")
    empty_target = [ln for ln in lines if not ln.target_en.strip()]
    if empty_target:
        print(f"警告：{len(empty_target)} 句沒有英文翻譯（列號 "
              f"{', '.join(str(l.sheet_row) for l in empty_target[:10])}...）")

    unknown = unknown_speakers(lines)
    if unknown:
        print(f"缺少英文名的發話者（{len(unknown)}）：" + "、".join(unknown))

    print("")
    print("前 5 句：")
    for ln in lines[:5]:
        print(f"  [{ln.dialogue_id}] {ln.speaker_en or ln.speaker_zh or '(旁白)'}: {ln.target_en}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lqa", description="遊戲在地化品質檢查工具")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("windows", help="列出目前所有可見視窗的標題")
    p.set_defaults(func=_cmd_windows)

    p = sub.add_parser("calibrate", help="框選對白框與姓名框，產生 profile")
    p.add_argument("--profile", default="config/profile.json", help="輸出的 profile 路徑")
    p.add_argument("--window", default="雷電模擬器", help="要擷取的視窗標題（部分符合即可）")
    p.add_argument("--region", type=int, nargs=4, metavar=("X", "Y", "W", "H"),
                   help="改用絕對螢幕座標，不用視窗定位")
    p.set_defaults(func=_cmd_calibrate)

    p = sub.add_parser("record", help="錄製一段對話")
    p.add_argument("--profile", default="config/profile.json")
    p.add_argument("--out", default="sessions", help="session 存放根目錄")
    p.add_argument("--name", default=None, help="這次錄製的名稱，例如 ch1_school")
    p.add_argument("--max-lines", type=int, default=None)
    p.add_argument("--max-seconds", type=float, default=None)
    p.set_defaults(func=_cmd_record)

    p = sub.add_parser("compare", help="把錄製結果與翻譯文本比對並輸出報告")
    p.add_argument("--script", required=True, help="翻譯文本 xlsx / csv / tsv")
    p.add_argument("--session", required=True, nargs="+", help="一個或多個 session 目錄")
    p.add_argument("--speakers", default=None, help="發話者中英對照表 csv")
    p.add_argument("--sheet", default=None, help="xlsx 工作表名稱")
    p.add_argument("--out", default="reports/lqa_report", help="輸出檔名（不含副檔名）")
    p.add_argument("--pass-threshold", type=float, default=None)
    p.add_argument("--include-pass", action="store_true", help="csv 也輸出一致的句子")
    p.add_argument("--show", type=int, default=0, help="在終端機列出前 N 筆問題")
    p.add_argument("--fail-on-issue", action="store_true", help="有問題時回傳非 0 結束碼")
    p.set_defaults(func=_cmd_compare)

    p = sub.add_parser("check-script", help="檢查翻譯文本是否能正確解析")
    p.add_argument("--script", required=True)
    p.add_argument("--speakers", default=None)
    p.add_argument("--sheet", default=None)
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
