"""识别程序类型：Electron / Qt / .NET / Java / 普通 exe。"""

from __future__ import annotations

import json
import os

from . import pe

SKIP_DIRS = {"node_modules", ".git", "__pycache__", "$recycle.bin"}
MAX_DEPTH = 4
MAX_FILES = 6000

QT_DLL_NAMES = {
    "qt5core.dll", "qt6core.dll", "qt5widgets.dll", "qt6widgets.dll",
    "qt5quick.dll", "qt6quick.dll", "qt5gui.dll", "qt6gui.dll",
}

KIND_LABELS = {
    "electron": "Electron 程序（asar 包）",
    "qt": "Qt 桌面程序",
    "dotnet": ".NET 程序",
    "java": "Java 程序",
    "generic": "普通 Windows 程序（PE）",
    "unknown": "无法识别",
}


def scan_files(app_dir: str) -> list[str]:
    """返回相对路径列表（限深度、限数量，避免扫到系统级大目录）。"""
    found: list[str] = []
    base = os.path.abspath(app_dir)
    for root, dirs, names in os.walk(base):
        rel_root = os.path.relpath(root, base)
        depth = 0 if rel_root == "." else rel_root.count(os.sep) + 1
        if depth >= MAX_DEPTH:
            dirs[:] = []
        dirs[:] = [d for d in dirs if d.lower() not in SKIP_DIRS]
        for n in names:
            found.append(os.path.normpath(os.path.join(rel_root, n)) if rel_root != "." else n)
            if len(found) >= MAX_FILES:
                return found
    return found


def _looks_like_electron(root: str, rels: list[str]) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    for rel in rels:
        low = rel.lower().replace("\\", "/")
        if low.endswith("resources/app.asar"):
            reasons.append("发现 resources/app.asar")
            break
    pkg = os.path.join(root, "resources", "app", "package.json")
    if os.path.isfile(pkg):
        try:
            with open(pkg, "r", encoding="utf-8", errors="replace") as f:
                meta = json.load(f)
            blob = json.dumps(meta).lower()
            if "electron" in blob:
                reasons.append("resources/app/package.json 里引用了 electron")
        except (OSError, json.JSONDecodeError):
            pass
    return bool(reasons), reasons


def detect(app_dir: str, exe: str | None = None) -> dict:
    root = os.path.abspath(app_dir)
    rels = scan_files(root)
    lower = [r.lower().replace("\\", "/") for r in rels]
    evidence: list[str] = []

    electron, why = _looks_like_electron(root, rels)
    evidence.extend(why)

    qt = any(os.path.basename(p) in QT_DLL_NAMES for p in lower)
    qm_files = [r for r in rels if r.lower().endswith(".qm")]
    ts_files = [r for r in rels if r.lower().endswith(".ts") and "translations" in r.lower()]
    if qt:
        evidence.append("目录里有 Qt*Core.dll")
    if qm_files:
        evidence.append(f"发现 {len(qm_files)} 个 .qm 翻译文件")

    jars = [r for r in rels if r.lower().endswith(".jar")]

    managed = False
    if exe and os.path.isfile(exe):
        info = pe.read_pe(exe)
        if info and info.get("managed"):
            managed = True
            evidence.append("主程序带 .NET CLR 头")
    if not managed:
        for rel in rels:
            low = rel.lower()
            if low.endswith((".dll", ".exe")):
                if low.endswith(".resources.dll"):
                    managed = True
                    evidence.append("发现 *.resources.dll（.NET 卫星程序集）")
                    break

    pe_info = pe.read_pe(exe) if exe and os.path.isfile(exe) else None

    if electron:
        kind = "electron"
    elif qt or qm_files:
        kind = "qt"
    elif managed:
        kind = "dotnet"
    elif jars:
        kind = "java"
    elif pe_info:
        kind = "generic"
    else:
        kind = "unknown"

    return {
        "kind": kind,
        "label": KIND_LABELS[kind],
        "app_dir": root,
        "exe": exe,
        "evidence": evidence,
        "pe": pe_info,
        "files": rels,
        "asar": next((r for r in rels if r.lower().endswith(".asar")), None),
        "qm_files": qm_files,
        "ts_files": ts_files,
        "jars": jars,
    }
