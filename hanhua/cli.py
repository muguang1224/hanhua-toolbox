"""命令行入口，方便不上 GUI 时用，也方便排查问题。

    python -m hanhua.cli analyze "C:\\Users\\me\\Desktop\\微信.lnk"
    python -m hanhua.cli apply  "C:\\...\\汉化包_x\\strings.csv"
"""

from __future__ import annotations

import argparse
import sys

from . import pipeline


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="hanhua", description="汉化工具箱命令行")
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("analyze", help="分析程序并生成翻译表")
    a.add_argument("path", help=".lnk / .exe / 程序文件夹")
    a.add_argument("-o", "--out", help="输出目录（默认桌面）")

    b = sub.add_parser("apply", help="用填好的翻译表回填")
    b.add_argument("csv", help="翻译表路径")
    b.add_argument("-o", "--out", required=True, help="汉化包目录（含 bundle.json）")
    b.add_argument("--allow-binary", action="store_true", help="允许二进制定长替换")

    args = ap.parse_args(argv)

    if args.cmd == "analyze":
        bundle = pipeline.analyze(args.path, args.out)
        info = bundle["info"]
        print(f"识别：{info['label']}（{info['kind']}）")
        print(f"目录：{info['app_dir']}")
        for ev in info.get("evidence") or []:
            print(f"  · {ev}")
        print(f"待翻译：{len(bundle['entries'])} 条")
        for n in bundle["notes"]:
            print(f"  ! {n}")
        print(f"翻译表：{bundle['csv']}")
        return 0

    bundle = pipeline.load_bundle(args.out)
    result = pipeline.apply_csv(bundle, args.csv, args.allow_binary)
    print(f"改动 {result['entries_written']} 处，写入 {len(result['files'])} 个文件")
    for f in result["files"]:
        print(f"  ✓ {f}")
    for s in result["skipped"]:
        print(f"  - {s}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
