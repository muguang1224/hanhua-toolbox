"""生成汉化包目录：翻译表、报告、回填后的文件、备份、安装脚本。"""

from __future__ import annotations

import csv
import json
import os
import shutil
from collections import defaultdict

from .asar import AsarArchive, AsarError
from .extract import Entry

CSV_FIELDS = ["id", "kind", "container", "file", "key", "original", "translation", "note"]


# ---------- 翻译表 ----------

def write_template(out_dir: str, entries: list[Entry], path: str | None = None) -> str:
    path = path or os.path.join(out_dir, "strings.csv")
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS, quoting=csv.QUOTE_ALL)
        w.writeheader()
        for e in entries:
            w.writerow({
                "id": e.id, "kind": e.kind, "container": e.container,
                "file": e.file, "key": e.key, "original": e.original,
                "translation": "", "note": e.note,
            })
    return path


def read_translations(csv_path: str) -> dict[str, str]:
    for enc in ("utf-8-sig", "gbk"):
        try:
            with open(csv_path, "r", encoding=enc, newline="") as f:
                rows = list(csv.DictReader(f))
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError("翻译表编码不认识，请用 UTF-8 或 GBK 另存")

    out: dict[str, str] = {}
    for row in rows:
        eid = (row.get("id") or "").strip()
        tr = (row.get("translation") or "").strip()
        if eid and tr:
            out[eid] = tr
    return out


# ---------- 回填 ----------

def _set_path(obj, path: list, value: str) -> None:
    cur = obj
    for p in path[:-1]:
        cur = cur[p]
    cur[path[-1]] = value


def _patch_json_bytes(data: bytes, changes: list[tuple[list, str]]) -> tuple[bytes | None, int, list[str]]:
    """返回 (新字节, 成功条数, 失败原因)。失败不静默。"""
    try:
        obj = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, 0, [f"JSON 解析失败：{exc}"]

    applied = 0
    failed: list[str] = []
    for path, text in changes:
        try:
            _set_path(obj, path, text)
            applied += 1
        except (KeyError, IndexError, TypeError):
            failed.append(f"路径不存在：{' > '.join(map(str, path))}")
    if applied == 0:
        return None, 0, failed
    blob = (json.dumps(obj, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    return blob, applied, failed


def _patch_utf16(buf: bytearray, offset: int, span: int, text: str) -> bool:
    enc = text.encode("utf-16le")
    if len(enc) > span:
        return False
    buf[offset:offset + len(enc)] = enc
    rest = span - len(enc)
    if rest:
        buf[offset + len(enc):offset + span] = b"\x00" * rest
    return True


def apply_translations(info: dict, entries: list[Entry], translations: dict[str, str],
                       out_dir: str, allow_binary: bool = False) -> dict:
    root = info["app_dir"]
    out_root = os.path.join(out_dir, "out")
    bak_root = os.path.join(out_dir, "backup")
    os.makedirs(out_root, exist_ok=True)

    done = 0
    skipped: list[str] = []
    warnings: list[str] = []
    written: set[str] = set()

    by_id = {e.id: e for e in entries}
    json_changes: dict[str, list[tuple[list, str]]] = defaultdict(list)
    asar_changes: dict[str, dict[str, list[tuple[list, str]]]] = defaultdict(lambda: defaultdict(list))
    bin_changes: dict[str, list[tuple[str, str, list[dict]]]] = defaultdict(list)

    for eid, text in translations.items():
        e = by_id.get(eid)
        if not e:
            skipped.append(f"id {eid}：翻译表里有、本次提取里没有（翻译表是不是别的程序留下的？）")
            continue
        if text == e.original:
            continue
        try:
            key = json.loads(e.key)
        except json.JSONDecodeError:
            key = None

        if e.kind == "json" and key is not None:
            json_changes[e.file].append((key, text))
        elif e.kind == "asar-json" and key is not None:
            asar_changes[e.container][e.file].append((key, text))
        elif e.kind == "utf16":
            if not allow_binary:
                skipped.append(f"{e.original[:24]}…：二进制回填未开启（勾选后才写 exe/dll）")
                continue
            bin_changes[e.file].append((text, e.original, e.meta.get("offsets") or []))

    # 1) 普通 JSON
    for rel, changes in json_changes.items():
        src = os.path.join(root, rel)
        try:
            with open(src, "rb") as f:
                data = f.read()
        except OSError as exc:
            skipped.append(f"{rel}：读不到（{exc}）")
            continue
        patched, n, failed = _patch_json_bytes(data, changes)
        skipped.extend(f"{rel}：{why}" for why in failed)
        if patched is None:
            if not failed:
                skipped.append(f"{rel}：没有可落地的改动")
            continue
        _emit(src, out_root, bak_root, rel, patched)
        written.add(rel)
        done += n

    # 2) asar
    for asar_rel, files in asar_changes.items():
        src = os.path.join(root, asar_rel)
        try:
            arc = AsarArchive(src)
        except (AsarError, OSError) as exc:
            skipped.append(f"{asar_rel}：打不开（{exc}）")
            continue
        if arc.has_integrity():
            warnings.append(
                f"{asar_rel} 带完整性校验字段。已重算文件哈希，但如果该程序启用了"
                " EnableEmbeddedAsarIntegrityValidation（exe 里另存了一份 header 哈希），"
                "改包后仍可能拒绝启动；这种情况只能换思路，不要反复试。"
            )
        replacements: dict[str, bytes] = {}
        for inner, changes in files.items():
            try:
                data = arc.read(inner)
            except AsarError as exc:
                skipped.append(f"{asar_rel}!{inner}：读不到（{exc}）")
                continue
            patched, n, failed = _patch_json_bytes(data, changes)
            skipped.extend(f"{asar_rel}!{inner}：{why}" for why in failed)
            if patched is not None:
                replacements[inner] = patched
                done += n
        if not replacements:
            continue
        try:
            _emit(src, out_root, bak_root, asar_rel, arc.rebuild(replacements))
            written.add(asar_rel)
        except (AsarError, OSError) as exc:
            skipped.append(f"{asar_rel}：重打包失败（{exc}）")

    # 3) 二进制定长替换
    for rel, changes in bin_changes.items():
        src = os.path.join(root, rel)
        try:
            with open(src, "rb") as f:
                buf = bytearray(f.read())
        except OSError as exc:
            skipped.append(f"{rel}：读不到（{exc}）")
            continue
        hit = 0
        for text, original, spans in changes:
            enc = text.encode("utf-16le")
            orig_enc = original.encode("utf-16le")
            patched_any = False
            for span in spans:
                off, size = span["offset"], span["size"]
                if buf[off:off + len(orig_enc)] != orig_enc:
                    continue  # 文件被更新过或位置已变，绝对不能盲写
                if _patch_utf16(buf, off, size, text):
                    patched_any = True
                    hit += 1
            if not patched_any:
                if len(enc) > max((s["size"] for s in spans), default=0):
                    skipped.append(f"{rel}：『{original}』译文比原文长，放不下")
                else:
                    skipped.append(f"{rel}：『{original}』原位置已不是原文，跳过（程序可能被更新过）")
        if hit:
            _emit(src, out_root, bak_root, rel, bytes(buf))
            written.add(rel)
            done += hit

    _write_install_script(out_dir, root)
    return {
        "entries_written": done,
        "files": sorted(written),
        "skipped": skipped,
        "warnings": warnings,
    }


def _emit(src: str, out_root: str, bak_root: str, rel: str, data: bytes) -> None:
    dst = os.path.join(out_root, rel)
    bak = os.path.join(bak_root, rel)
    os.makedirs(os.path.dirname(dst) or out_root, exist_ok=True)
    os.makedirs(os.path.dirname(bak) or bak_root, exist_ok=True)
    if not os.path.exists(bak) and os.path.isfile(src):
        shutil.copy2(src, bak)  # 只备份第一次的原件，反复回填不会把备份覆盖成改过的版本
    with open(dst, "wb") as f:
        f.write(data)


def _write_install_script(out_dir: str, app_dir: str) -> None:
    script = f"""# 由汉化工具箱生成：把 out/ 里的文件覆盖到程序目录，覆盖前先备份到 backup/
$ErrorActionPreference = 'Stop'
$app = '{app_dir.replace("'", "''")}'
$src = Join-Path $PSScriptRoot 'out'
$bak = Join-Path $PSScriptRoot 'backup'

if (-not (Test-Path -LiteralPath $src)) {{ Write-Host '没有 out 目录，先回填一次'; exit 1 }}
if (-not (Test-Path -LiteralPath $app)) {{ Write-Host "程序目录不存在: $app"; exit 1 }}

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin -and $app -like 'C:\\Program Files*') {{
    Write-Host '警告：目标在 Program Files 下，需要管理员权限；请右键用管理员身份运行 PowerShell' -ForegroundColor Yellow
}}

Get-ChildItem -LiteralPath $src -Recurse -File | ForEach-Object {{
    $rel = $_.FullName.Substring($src.Length + 1)
    $dst = Join-Path $app $rel
    $b = Join-Path $bak $rel
    if ((Test-Path -LiteralPath $dst) -and -not (Test-Path -LiteralPath $b)) {{
        New-Item -ItemType Directory -Force -Path (Split-Path -LiteralPath $b) | Out-Null
        Copy-Item -LiteralPath $dst -Destination $b -Force
    }}
    New-Item -ItemType Directory -Force -Path (Split-Path -LiteralPath $dst) | Out-Null
    Copy-Item -LiteralPath $_.FullName -Destination $dst -Force
    Write-Host "已替换 $rel"
}}
Write-Host '完成。原始文件已备份在 backup/'
"""
    with open(os.path.join(out_dir, "install.ps1"), "w", encoding="utf-8-sig", newline="\r\n") as f:
        f.write(script)


# ---------- 报告 ----------

def write_report(out_dir: str, info: dict, entries: list[Entry], notes: list[str],
                 translations: dict[str, str] | None = None,
                 result: dict | None = None) -> str:
    path = os.path.join(out_dir, "report.md")
    pe_info = info.get("pe") or {}
    lines = [
        "# 汉化包报告",
        "",
        f"- 程序目录：`{info['app_dir']}`",
        f"- 主程序：`{info.get('exe') or '未确定'}`",
        f"- 识别结果：**{info.get('label', info['kind'])}**",
    ]
    if pe_info:
        lines.append(
            f"- 架构：{pe_info.get('machine')} / {'64 位' if pe_info.get('is64') else '32 位'}"
            f"（{'GUI' if pe_info.get('subsystem') == 2 else '控制台' if pe_info.get('subsystem') == 3 else '其他'}）"
        )
    if info.get("evidence"):
        lines.append(f"- 判断依据：{'；'.join(info['evidence'])}")

    kinds: dict[str, int] = defaultdict(int)
    for e in entries:
        kinds[e.kind] += 1

    lines += ["", f"## 待翻译条目：{len(entries)} 条", ""]
    if kinds:
        for k, n in kinds.items():
            lines.append(f"- `{k}`：{n} 条")
    else:
        lines.append("- 没有找到可提取的文案。多半是文案编译进了二进制、或者用了本工具还没支持的格式。")

    if translations is not None:
        filled = sum(1 for v in translations.values() if v.strip())
        line = f"## 回填：翻译表里填了 {filled} 条"
        if result:
            line += f"，实际写入 {result['entries_written']} 处，涉及 {len(result['files'])} 个文件"
        lines += ["", line]

    if result:
        for f in result["files"]:
            lines.append(f"- `{f}`")
        if result.get("warnings"):
            lines += ["", "### 警告", ""]
            lines += [f"- {w}" for w in result["warnings"]]
        if result["skipped"]:
            lines += ["", "### 跳过", ""]
            for s in result["skipped"][:40]:
                lines.append(f"- {s}")
            if len(result["skipped"]) > 40:
                lines.append(f"- …还有 {len(result['skipped']) - 40} 条")

    if notes:
        lines += ["", "## 注意", ""]
        lines += [f"- {n}" for n in notes]

    lines += ["", "## 怎么用", "",
              "1. 用 Excel 或记事本打开 `strings.csv`，只在 `translation` 列填中文（`original` 列别动）。",
              "2. 保存后回到工具点「导入翻译表并回填」。",
              "3. 生成的文件在 `out/` 里，原文件备份在 `backup/` 里。",
              "4. 确认没问题后，用 PowerShell 运行 `install.ps1`，把 `out/` 覆盖到程序目录。",
              "5. 想撤销：把 `backup/` 里的文件照原样复制回去。",
              ""]
    text = "\n".join(lines)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path
