"""把工具打包成单文件 exe。由 build.bat 调用，也可以直接 python build.py。"""

from __future__ import annotations

import os
import subprocess
import sys

NAME = "HanhuaToolbox"  # 输出文件名用 ASCII：中文文件名在部分系统和下载器里会乱码


def main() -> int:
    root = os.path.dirname(os.path.abspath(__file__))
    os.chdir(root)
    sys.path.insert(0, root)

    from hanhua._console import ensure_utf8_stdout

    ensure_utf8_stdout()
    py = sys.executable
    print(f"使用 Python：{py}")

    try:
        import tkinter  # noqa: F401
        print(f"tkinter 可用（Tk {tkinter.TkVersion}）")
    except ImportError:
        print("这个 Python 没有 tkinter，装一个官方安装包（勾选 tcl/tk）再试")
        return 1

    print("\n[1/2] 安装打包依赖…")
    subprocess.check_call([py, "-m", "pip", "install", "--upgrade", "pyinstaller", "tkinterdnd2"])

    print("\n[2/2] 打包…")
    args = [
        py, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--onefile", "--windowed",
        "--name", NAME,
        "--collect-all", "tkinterdnd2",
        "--hidden-import", "tkinterdnd2",
        "run.py",
    ]
    subprocess.check_call(args)

    print(f"\n完成：dist{os.sep}{NAME}.exe")
    return 0


if __name__ == "__main__":
    sys.exit(main())
