"""把拖进来的东西（快捷方式 / 文件夹 / exe）解析成真正的程序目录。"""

from __future__ import annotations

import os
import shutil
import subprocess


class ResolveError(RuntimeError):
    pass


def _ps_quote(path: str) -> str:
    return path.replace("'", "''")


def _run_powershell(script: str, timeout: int = 25) -> str:
    exe = shutil.which("powershell") or shutil.which("pwsh")
    if not exe:
        raise ResolveError("找不到 PowerShell，无法解析 .lnk 快捷方式")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        proc = subprocess.run(
            [exe, "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=timeout,
            creationflags=flags, encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired:
        raise ResolveError("解析快捷方式超时（PowerShell 没有响应）") from None
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout).strip()[:300]
        raise ResolveError(f"PowerShell 解析失败：{msg}")
    return proc.stdout


def resolve_lnk(lnk_path: str) -> dict:
    lnk = os.path.abspath(lnk_path)
    if not os.path.isfile(lnk):
        raise ResolveError(f"快捷方式不存在：{lnk}")
    script = (
        # Windows PowerShell 5.1 重定向输出默认走 OEM 代码页，中文路径会乱码，
        # 必须先把控制台输出编码钉成 UTF-8，否则 .lnk 指向中文目录时必挂
        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
        "$OutputEncoding=[Console]::OutputEncoding;"
        "$ErrorActionPreference='Stop';"
        f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{_ps_quote(lnk)}');"
        "Write-Output ('TARGET=' + $s.TargetPath);"
        "Write-Output ('WORKDIR=' + $s.WorkingDirectory);"
        "Write-Output ('ARGS=' + $s.Arguments)"
    )
    out = _run_powershell(script)

    fields: dict[str, str] = {}
    for line in out.splitlines():
        key, sep, value = line.partition("=")
        key = key.strip()
        if sep and key in ("TARGET", "WORKDIR", "ARGS"):
            fields[key] = value.strip()

    target = fields.get("TARGET", "").strip()
    if not target:
        raise ResolveError(
            "这个快捷方式解析不出目标路径（可能是 MSI 广告快捷方式、商店应用或已失效）"
        )
    # 环境变量展开（有些快捷方式写的是 %ProgramFiles%\...）
    target = os.path.expandvars(target)
    return {
        "target": target,
        "workdir": fields.get("WORKDIR", ""),
        "arguments": fields.get("ARGS", ""),
    }


def resolve_drop(path: str) -> dict:
    """返回 app_dir / exe / source_kind / arguments。"""
    path = os.path.abspath(path)
    if not os.path.exists(path):
        raise ResolveError(f"路径不存在：{path}")

    if os.path.isdir(path):
        return {"app_dir": path, "exe": _guess_exe(path),
                "source_kind": "folder", "arguments": ""}

    ext = os.path.splitext(path)[1].lower()

    if ext == ".lnk":
        info = resolve_lnk(path)
        target = info["target"]
        if not os.path.exists(target):
            raise ResolveError(f"快捷方式指向的目标已不存在：{target}")
        if os.path.isdir(target):
            return {"app_dir": target, "exe": _guess_exe(target),
                    "source_kind": "shortcut", "arguments": info["arguments"],
                    "shortcut": path}
        return {"app_dir": os.path.dirname(target) or ".", "exe": target,
                "source_kind": "shortcut", "arguments": info["arguments"],
                "shortcut": path}

    if ext == ".url":
        raise ResolveError("这是网址快捷方式（.url），不是程序快捷方式")

    if ext == ".exe":
        return {"app_dir": os.path.dirname(path) or ".", "exe": path,
                "source_kind": "exe", "arguments": ""}

    # 拖进来一个 dll / 资源文件：按它所在目录处理
    return {"app_dir": os.path.dirname(path) or ".", "exe": None,
            "source_kind": "file", "arguments": "", "dropped": path}


def _guess_exe(app_dir: str) -> str | None:
    """目录里挑一个最像主程序的 exe。"""
    try:
        entries = [e for e in os.listdir(app_dir)
                   if e.lower().endswith(".exe") and os.path.isfile(os.path.join(app_dir, e))]
    except OSError:
        return None
    if not entries:
        return None
    noise = ("unins", "setup", "install", "update", "crash", "helper", "patch", "vc_redist")
    candidates = [e for e in entries if not any(n in e.lower() for n in noise)] or entries
    candidates.sort(key=lambda e: os.path.getsize(os.path.join(app_dir, e)), reverse=True)
    return os.path.join(app_dir, candidates[0])
