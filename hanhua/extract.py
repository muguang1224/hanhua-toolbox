"""从程序里把待翻译的字符串挖出来。"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field

from .asar import AsarArchive, AsarError

I18N_HINTS = ("i18n", "lang", "locale", "strings", "messages", "translation", "en-us", "en_us")
SKIP_JSON = ("package-lock", "package.json", "tsconfig", "composer.lock", ".map")

URLISH = re.compile(r"^\s*(https?://|file://|mailto:|www\.)")
PLACEHOLDER = re.compile(r"^(%[sd]|\{\d*\}|\{[\w.]+\}|\\n|&)$")
HAS_LETTERS = re.compile(r"[A-Za-z]{2}")

MAX_JSON_BYTES = 3 * 1024 * 1024
MAX_ENTRIES = 8000
MAX_BINARY_BYTES = 12 * 1024 * 1024
MAX_BINARY_TEXTS = 3000

UTF16_RUN = re.compile(rb"(?:[\x20-\x7e]\x00){4,}")

# 二进制里的噪音：文件名、常量名、GUID、路径、注册表键
_NOISE_EXT = re.compile(r"\.(dll|exe|pdb|sys|drv|ocx|cpl|ax|tlb|manifest|xml)$", re.I)
_NOISE_UPPER = re.compile(r"^[A-Z_]{8,}$")   # 不能加 re.I，否则会误杀 Settings 这种正常词
_NOISE_HEX = re.compile(r"^[0-9A-Fa-f]{16,}$")
_NOISE_PATH = re.compile(r"^[A-Za-z]:\\")
_NOISE_REG = re.compile(r"^HKEY_|^SOFTWARE\\", re.I)


def _is_noise(text: str) -> bool:
    if "\\" in text:
        return True
    return bool(
        _NOISE_EXT.search(text) or _NOISE_UPPER.match(text)
        or _NOISE_HEX.match(text) or _NOISE_PATH.match(text) or _NOISE_REG.match(text)
    )


@dataclass
class Entry:
    id: str
    kind: str            # json | asar-json | utf16
    container: str       # asar 相对路径；普通文件为空
    file: str            # asar 内路径 / 磁盘相对路径
    key: str             # JSON 数组字符串，或首个 0x 偏移
    original: str
    translation: str = ""
    note: str = ""
    writable: bool = True
    meta: dict = field(default_factory=dict)


def make_id(*parts: str) -> str:
    return hashlib.sha1("\x1f".join(parts).encode("utf-8", "replace")).hexdigest()[:12]


def is_ui_text(s: str) -> bool:
    if not s or len(s) > 300:
        return False
    if URLISH.match(s):
        return False
    if PLACEHOLDER.match(s.strip()):
        return False
    if not HAS_LETTERS.search(s):
        return False
    if "\x00" in s:
        return False
    return True


def walk_json(obj, prefix: tuple = ()):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from walk_json(v, prefix + (k,))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from walk_json(v, prefix + (i,))
    elif isinstance(obj, str):
        yield prefix, obj


def _score(obj) -> tuple[int, int]:
    good = total = 0
    for _, v in walk_json(obj):
        total += 1
        if is_ui_text(v):
            good += 1
    return good, total


def _json_entries(data: bytes, kind: str, container: str, file: str) -> list[Entry]:
    try:
        obj = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return []

    good, total = _score(obj)
    low = file.lower()
    hinted = any(h in low for h in I18N_HINTS)
    # 没有 i18n 线索时，要求这个文件大部分叶子都是可读文案
    if not hinted and (total == 0 or good / total < 0.5 or good < 5):
        return []

    out: list[Entry] = []
    for path, value in walk_json(obj):
        if not path or not is_ui_text(value):
            continue
        key = json.dumps(list(path), ensure_ascii=False)
        out.append(Entry(
            id=make_id(kind, container, file, key),
            kind=kind, container=container, file=file, key=key, original=value,
        ))
        if len(out) >= 2500:
            break
    return out


def extract_from_json_files(root: str, rels: list[str]) -> tuple[list[Entry], list[str]]:
    entries: list[Entry] = []
    notes: list[str] = []
    for rel in rels:
        low = rel.lower()
        if not low.endswith(".json") or any(s in low for s in SKIP_JSON):
            continue
        full = os.path.join(root, rel)
        try:
            if os.path.getsize(full) > MAX_JSON_BYTES:
                continue
            with open(full, "rb") as f:
                data = f.read()
        except OSError:
            continue
        entries.extend(_json_entries(data, "json", "", rel.replace("\\", "/")))
        if len(entries) > MAX_ENTRIES:
            notes.append(f"待翻译条目超过 {MAX_ENTRIES} 条，已截断")
            break
    return entries[:MAX_ENTRIES], notes


def extract_from_asar(root: str, asar_rel: str) -> tuple[list[Entry], list[str]]:
    entries: list[Entry] = []
    notes: list[str] = []
    arc = AsarArchive(os.path.join(root, asar_rel))
    for p in arc.list_files():
        low = p.lower()
        if not low.endswith(".json") or any(s in low for s in SKIP_JSON):
            continue
        try:
            if int(arc._node(p).get("size", 0)) > MAX_JSON_BYTES:
                continue
            data = arc.read(p)
        except AsarError:
            continue
        entries.extend(_json_entries(data, "asar-json", asar_rel.replace("\\", "/"), p))
        if len(entries) > MAX_ENTRIES:
            notes.append(f"asar 内待翻译条目超过 {MAX_ENTRIES} 条，已截断")
            break
    return entries[:MAX_ENTRIES], notes


def extract_binary_strings(root: str, rels: list[str],
                           limit_files: int = 6) -> tuple[list[Entry], list[str]]:
    """从 exe/dll 里扫 UTF-16LE 文案。

    同一个字符串在文件里出现多次只生成一条，但会把**所有**出现位置都记下来，
    回填时一起改，避免"改了一处、其余还是英文"。
    """
    entries: list[Entry] = []
    notes: list[str] = []
    candidates: list[tuple[int, str]] = []
    too_big: list[str] = []

    for rel in rels:
        if not rel.lower().endswith((".exe", ".dll")):
            continue
        full = os.path.join(root, rel)
        try:
            size = os.path.getsize(full)
        except OSError:
            continue
        if size < 32 * 1024:
            continue
        if size > MAX_BINARY_BYTES:
            too_big.append(f"{rel}（{size // 1048576} MB）")
            continue
        candidates.append((size, rel))

    if too_big:
        notes.append("文件太大没扫（上限 12MB）：" + "、".join(too_big[:5]))
    candidates.sort(reverse=True)
    if len(candidates) > limit_files:
        notes.append(f"二进制文件较多，只扫了最大的 {limit_files} 个")

    for _, rel in candidates[:limit_files]:
        with open(os.path.join(root, rel), "rb") as f:
            data = f.read()

        spans: dict[str, list[tuple[int, int]]] = {}
        for m in UTF16_RUN.finditer(data):
            try:
                text = m.group().decode("utf-16le")
            except UnicodeDecodeError:
                continue
            if not is_ui_text(text) or _is_noise(text):
                continue
            spans.setdefault(text, []).append((m.start(), m.end() - m.start()))

        rel_norm = rel.replace("\\", "/")
        for text, occ in spans.items():
            entries.append(Entry(
                id=make_id("utf16", rel_norm, text),
                kind="utf16", container="", file=rel_norm,
                key=f"0x{occ[0][0]:08x}",
                original=text,
                note=f"定长替换，原文出现 {len(occ)} 次；中文不能比原文长",
                meta={"offsets": [{"offset": o, "size": s} for o, s in occ]},
            ))
            if len(entries) >= MAX_BINARY_TEXTS:
                notes.append(f"二进制文案超过 {MAX_BINARY_TEXTS} 条，已截断")
                return entries, notes
    return entries, notes


def build_entries(info: dict) -> tuple[list[Entry], list[str]]:
    root = info["app_dir"]
    rels = info["files"]
    kind = info["kind"]
    entries: list[Entry] = []
    notes: list[str] = []

    if kind == "electron":
        if info.get("asar"):
            try:
                found, n = extract_from_asar(root, info["asar"])
                entries.extend(found)
                notes.extend(n)
            except AsarError as exc:
                notes.append(f"asar 解析失败：{exc}")
        found, n = extract_from_json_files(root, rels)
        entries.extend(found)
        notes.extend(n)
        notes.append("Electron 的文案也可能写死在 .js/.html 里，那部分本工具不提取")

    elif kind in ("generic", "dotnet"):
        found, n = extract_binary_strings(root, rels)
        entries.extend(found)
        notes.extend(n)
        found, n = extract_from_json_files(root, rels)
        entries.extend(found)
        notes.extend(n)

    elif kind == "qt":
        # .qm 里是 Qt 自己的翻译；程序自己的文案通常在 exe/dll 里，所以两条路都走
        found, n = extract_binary_strings(root, rels)
        entries.extend(found)
        notes.extend(n)
        found, n = extract_from_json_files(root, rels)
        entries.extend(found)
        notes.extend(n)
        if info.get("qm_files"):
            notes.append(
                "Qt 的 .qm 是二进制翻译文件，本工具不解析；"
                "如果程序自带 translations/*.qm，替换同名文件通常就能生效"
            )

    else:
        found, n = extract_from_json_files(root, rels)
        entries.extend(found)
        notes.extend(n)
        found, n = extract_binary_strings(root, rels)
        entries.extend(found)
        notes.extend(n)

    return entries, notes
