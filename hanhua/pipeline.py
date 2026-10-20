"""把「解析 → 识别 → 提取 → 出包」串起来，并把中间结果存成 bundle.json，
这样关掉工具再打开，仍能拿着翻译表继续回填。"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict

from . import detect, extract, pack, resolve

BUNDLE_NAME = "bundle.json"


def default_out_dir(info: dict) -> str:
    exe = info.get("exe")
    name = os.path.splitext(os.path.basename(exe))[0] if exe else os.path.basename(info["app_dir"])
    name = re.sub(r'[\\/:*?"<>|\s]+', "_", name).strip("_") or "app"
    home = os.path.expanduser("~")
    desktop = os.path.join(home, "Desktop")
    base_dir = desktop if os.path.isdir(desktop) else home
    base = os.path.join(base_dir, f"汉化包_{name}")
    if not os.path.exists(base):
        return base
    i = 2
    while os.path.exists(f"{base}_{i}"):
        i += 1
    return f"{base}_{i}"


def _save_bundle(bundle: dict) -> None:
    info = {k: v for k, v in bundle["info"].items() if k not in ("files",)}
    payload = {
        "info": info,
        "notes": bundle["notes"],
        "entries": [asdict(e) for e in bundle["entries"]],
    }
    with open(os.path.join(bundle["out_dir"], BUNDLE_NAME), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)


def analyze(drop_path: str, out_root: str | None = None) -> dict:
    resolved = resolve.resolve_drop(drop_path)
    info = detect.detect(resolved["app_dir"], resolved.get("exe"))
    info.update(resolved)
    entries, notes = extract.build_entries(info)

    out_dir = out_root or default_out_dir(info)
    os.makedirs(out_dir, exist_ok=True)

    csv_path = os.path.join(out_dir, "strings.csv")
    if os.path.exists(csv_path):
        # 用户可能已经把译文填在里面了，绝不能直接覆盖
        stamp = time.strftime("%Y%m%d-%H%M%S")
        keep = os.path.join(out_dir, f"strings.旧-{stamp}.csv")
        os.replace(csv_path, keep)
        notes.append(f"输出目录里已有旧的翻译表，已保留为 {os.path.basename(keep)}")

    pack.write_template(out_dir, entries, csv_path)
    pack.write_report(out_dir, info, entries, notes)

    bundle = {"info": info, "entries": entries, "notes": notes,
              "out_dir": out_dir, "csv": csv_path}
    _save_bundle(bundle)
    return bundle


def load_bundle(out_dir: str) -> dict:
    path = os.path.join(out_dir, BUNDLE_NAME)
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    entries = [extract.Entry(**e) for e in payload["entries"]]
    return {
        "info": payload["info"],
        "entries": entries,
        "notes": payload.get("notes", []),
        "out_dir": out_dir,
        "csv": os.path.join(out_dir, "strings.csv"),
    }


def apply_csv(bundle: dict, csv_path: str, allow_binary: bool = False) -> dict:
    translations = pack.read_translations(csv_path)
    result = pack.apply_translations(
        bundle["info"], bundle["entries"], translations,
        bundle["out_dir"], allow_binary=allow_binary,
    )
    pack.write_report(bundle["out_dir"], bundle["info"], bundle["entries"],
                      bundle["notes"], translations, result)
    return result
