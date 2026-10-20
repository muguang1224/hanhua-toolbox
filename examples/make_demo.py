"""造一个假的 Electron 程序目录，用来试工具，不用真装任何软件。

    python examples/make_demo.py
    python -m hanhua.cli analyze demo/MyApp

会生成 demo/MyApp/resources/app.asar，里面有十几条英文界面文案。
"""

from __future__ import annotations

import json
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

OUT_DIR = os.path.join(ROOT, "demo", "MyApp")

EN = {
    "app": {"name": "Demo App", "tagline": "A tiny demo for the tool"},
    "menu": {"file": "File", "edit": "Edit", "help": "Help", "quit": "Quit"},
    "toolbar": {"open": "Open File...", "save": "Save", "export": "Export as PDF"},
    "dialog": {
        "title": "Are you sure?",
        "body": "This cannot be undone.",
        "ok": "Yes",
        "cancel": "No",
    },
    "status": {"ready": "Ready", "saving": "Saving, please wait..."},
}


def build_asar(entries: dict[str, bytes]) -> bytes:
    """按 asar 格式拼一个：[4][headerSize][jsonLen][json 补齐到 4][各文件内容]。"""
    tree: dict = {"files": {}}
    for path, data in entries.items():
        parts = path.split("/")
        node = tree
        for part in parts[:-1]:
            node = node["files"].setdefault(part, {"files": {}})
        node["files"][parts[-1]] = {"size": len(data), "offset": "0"}

    order: list[tuple[str, dict]] = []

    def walk(node: dict, prefix: str) -> None:
        for name, child in node["files"].items():
            p = f"{prefix}/{name}" if prefix else name
            if "files" in child:
                walk(child, p)
            else:
                order.append((p, child))

    walk(tree, "")

    blobs: list[bytes] = []
    cursor = 0
    for p, node in order:
        data = entries[p]
        pad = (4 - cursor % 4) % 4
        if pad:
            blobs.append(b"\0" * pad)
            cursor += pad
        node["offset"] = str(cursor)
        blobs.append(data)
        cursor += len(data)

    header = json.dumps(tree, separators=(",", ":")).encode()
    padded = (len(header) + 3) // 4 * 4
    return b"".join([
        struct.pack("<I", 4),
        struct.pack("<I", 4 + padded),
        struct.pack("<I", len(header)),
        header,
        b"\0" * (padded - len(header)),
        *blobs,
    ])


def main() -> None:
    from hanhua._console import ensure_utf8_stdout

    ensure_utf8_stdout()
    resources = os.path.join(OUT_DIR, "resources")
    os.makedirs(resources, exist_ok=True)

    files = {
        "package.json": json.dumps({"name": "demo-app", "version": "1.0.0"}, indent=2).encode(),
        "main.js": b"console.log('demo app');\n",
        "locales/en.json": json.dumps(EN, indent=2, ensure_ascii=False).encode(),
    }

    path = os.path.join(resources, "app.asar")
    with open(path, "wb") as f:
        f.write(build_asar(files))

    print(f"已生成：{path}")
    print("接着试：")
    print(f'  python -m hanhua.cli analyze "{OUT_DIR}"')


if __name__ == "__main__":
    main()
