"""控制台输出的一处跨平台坑。

Windows 上 stdout 连到终端时 Python 会用控制台 API，中文没问题；
但**一旦重定向到管道或文件**（CI、`> log.txt`、IDE 输出面板），
就会退回系统 locale 编码（英文 Windows 上是 cp1252），打印中文直接抛
UnicodeEncodeError。这里统一把 stdout/stderr 钉成 UTF-8。
"""

from __future__ import annotations

import sys


def ensure_utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            # 不是 TextIOWrapper（被替换成别的对象），或者已经关了
            pass
