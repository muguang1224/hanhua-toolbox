"""拖拽窗口。

线程约定：tkinter 不是线程安全的，所有界面操作只在主线程做；
后台线程只往队列里塞结果，主线程用 after() 轮询取出。
"""

from __future__ import annotations

import os
import queue
import subprocess
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    HAS_DND = True
except Exception:  # 没装 tkinterdnd2 也能用，只是不能拖拽
    HAS_DND = False

from . import __version__, pipeline

BG = "#f4f5f7"
CARD = "#ffffff"
BORDER = "#d8dbe0"
ACCENT = "#2f6fed"
MUTED = "#6b7280"


def _font(size: int = 10, bold: bool = False) -> tuple:
    family = "Microsoft YaHei UI" if os.name == "nt" else "Noto Sans CJK SC"
    return (family, size, "bold") if bold else (family, size)


class App:
    def __init__(self) -> None:
        self.root = TkinterDnD.Tk() if HAS_DND else tk.Tk()
        self.root.title(f"汉化工具箱 v{__version__}")
        self.root.geometry("720x580")
        self.root.minsize(620, 500)
        self.root.configure(bg=BG)

        self.bundle: dict | None = None
        self.busy = False
        self.events: queue.Queue = queue.Queue()
        self._build()
        self.root.after(80, self._poll)

    # ---------- 界面 ----------

    def _build(self) -> None:
        pad = {"padx": 18}

        tk.Label(self.root, text="汉化工具箱", font=_font(16, True), bg=BG, fg="#111827") \
            .pack(anchor="w", pady=(16, 2), **pad)
        tk.Label(self.root, text="把程序的快捷方式（.lnk）拖进来：自动找路径 → 提取文案 → 生成汉化包",
                 font=_font(9), bg=BG, fg=MUTED).pack(anchor="w", **pad)

        self.zone = tk.Frame(self.root, bg=CARD, highlightbackground=BORDER,
                             highlightthickness=2, cursor="hand2")
        self.zone.pack(fill="x", pady=14, **pad)
        self.zone_label = tk.Label(
            self.zone,
            text=("把快捷方式拖到这里" if HAS_DND else "拖拽不可用：点这里选择快捷方式"),
            font=_font(12), bg=CARD, fg=MUTED, pady=34)
        self.zone_label.pack(fill="both", expand=True)

        for w in (self.zone, self.zone_label):
            w.bind("<Button-1>", lambda _e: self.choose())
            if HAS_DND:
                try:
                    w.drop_target_register(DND_FILES)
                    w.dnd_bind("<<Drop>>", self._on_drop)
                except tk.TclError:
                    pass

        self.status = tk.Label(self.root, text="尚未选择程序", font=_font(9), bg=BG,
                               fg=MUTED, anchor="w", justify="left")
        self.status.pack(fill="x", **pad)

        opts = tk.Frame(self.root, bg=BG)
        opts.pack(fill="x", pady=(8, 4), **pad)
        self.allow_binary = tk.BooleanVar(value=False)
        tk.Checkbutton(opts, text="允许回填二进制（定长替换 exe/dll，可能出问题，默认关闭）",
                       variable=self.allow_binary, bg=BG, font=_font(9),
                       activebackground=BG).pack(anchor="w")

        btns = tk.Frame(self.root, bg=BG)
        btns.pack(fill="x", pady=6, **pad)
        self.btn_apply = ttk.Button(btns, text="导入翻译表并回填", command=self.do_apply, state="disabled")
        self.btn_apply.pack(side="left")
        self.btn_open = ttk.Button(btns, text="打开输出文件夹", command=self.open_out, state="disabled")
        self.btn_open.pack(side="left", padx=8)
        ttk.Button(btns, text="重新选择", command=self.choose).pack(side="left")

        box = tk.Frame(self.root, bg=BG)
        box.pack(fill="both", expand=True, pady=(6, 16), **pad)
        self.log_text = tk.Text(box, height=12, bg="#1f2430", fg="#d6deeb",
                                insertbackground="#d6deeb", font=("Consolas", 9),
                                relief="flat", wrap="word", padx=10, pady=8)
        scroll = ttk.Scrollbar(box, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.log_text.pack(side="left", fill="both", expand=True)
        self.log_text.configure(state="disabled")

        self.log("准备就绪。把 .lnk、exe 或程序文件夹拖进上面的框里。")
        if not HAS_DND:
            self.log("提示：没装 tkinterdnd2，拖拽不可用，点框选择文件即可。")

    # ---------- 主线程事件循环 ----------

    def _poll(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "done":
                    self._done(payload)
                elif kind == "fail":
                    self._fail(payload)
                elif kind == "applied":
                    self._applied(payload)
                elif kind == "apply_fail":
                    self._apply_failed(payload)
        except queue.Empty:
            pass
        try:
            self.root.after(80, self._poll)
        except tk.TclError:  # 窗口已经关了
            pass

    def log(self, msg: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", msg + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    # ---------- 交互 ----------

    def _on_drop(self, event) -> None:
        try:
            items = self.root.tk.splitlist(event.data)
        except tk.TclError:
            items = [event.data]
        if items:
            self.run(items[0])

    def choose(self) -> None:
        path = filedialog.askopenfilename(
            title="选择程序快捷方式或 exe",
            filetypes=[("快捷方式 / 程序", "*.lnk *.exe"), ("所有文件", "*.*")])
        if not path:
            path = filedialog.askdirectory(title="或者选择程序文件夹")
        if path:
            self.run(path)

    def run(self, path: str) -> None:
        if self.busy:
            return
        self.busy = True
        self.btn_apply.configure(state="disabled")
        self.zone_label.configure(text="正在分析…", fg=ACCENT)
        self.status.configure(text=f"正在处理：{path}")
        self.log(f"→ {path}")
        threading.Thread(target=self._work, args=(path,), daemon=True).start()

    def _work(self, path: str) -> None:
        try:
            self.events.put(("done", pipeline.analyze(path)))
        except Exception as exc:  # 任何异常都要显示，不能静默失败
            self.events.put(("fail", exc))

    def _done(self, bundle: dict) -> None:
        self.busy = False
        self.bundle = bundle
        info = bundle["info"]
        n = len(bundle["entries"])
        self.zone_label.configure(text="拖进下一个，或继续用下面按钮", fg=MUTED)
        self.status.configure(
            text=f"识别为：{info.get('label')}\n程序目录：{info['app_dir']}\n"
                 f"待翻译：{n} 条　输出：{bundle['out_dir']}")

        self.log(f"识别为 {info.get('label')}（{info['kind']}）")
        if info.get("exe"):
            self.log(f"主程序：{info['exe']}")
        for ev in info.get("evidence") or []:
            self.log(f"  依据：{ev}")
        self.log(f"提取到 {n} 条待翻译文案")
        for note in bundle["notes"]:
            self.log(f"注意：{note}")
        self.log(f"翻译表：{bundle['csv']}")
        self.log("下一步：用 Excel 打开 strings.csv，只填 translation 列，保存后再点「导入翻译表并回填」")

        self.btn_apply.configure(state="normal" if n else "disabled")
        self.btn_open.configure(state="normal")
        if n == 0:
            self.log("没有可提取的文案——这个程序大概把文字编译进二进制了，或者格式暂不支持。")

    def _fail(self, exc: Exception) -> None:
        self.busy = False
        self.zone_label.configure(text="出错了，重新拖一次", fg="#c0392b")
        self.log(f"失败：{exc}")
        messagebox.showerror("处理失败", str(exc))

    def do_apply(self) -> None:
        if not self.bundle or self.busy:
            return
        path = filedialog.askopenfilename(
            title="选择填好的翻译表", initialdir=self.bundle["out_dir"],
            filetypes=[("CSV 翻译表", "*.csv"), ("所有文件", "*.*")])
        if not path:
            return
        self.busy = True
        self.btn_apply.configure(state="disabled")
        self.log(f"读取翻译表：{path}")
        allow = bool(self.allow_binary.get())  # 变量只在主线程读
        threading.Thread(target=self._apply_work, args=(path, allow), daemon=True).start()

    def _apply_work(self, csv_path: str, allow: bool) -> None:
        try:
            self.events.put(("applied", pipeline.apply_csv(self.bundle, csv_path, allow)))
        except Exception as exc:
            self.events.put(("apply_fail", exc))

    def _applied(self, result: dict) -> None:
        self.busy = False
        self.btn_apply.configure(state="normal")
        self.log(f"回填完成：实际写入 {result['entries_written']} 处，涉及 {len(result['files'])} 个文件")
        for f in result["files"]:
            self.log(f"  已生成 out/{f}")
        for w in result.get("warnings") or []:
            self.log(f"警告：{w}")
        for s in result["skipped"][:20]:
            self.log(f"  跳过：{s}")
        if len(result["skipped"]) > 20:
            self.log(f"  …还有 {len(result['skipped']) - 20} 条跳过，详见 report.md")
        if not result["files"]:
            self.log("没有任何文件被写入——检查 translation 列是不是真的填了内容。")
            messagebox.showwarning("没有写入", "没有生成任何文件，看看下面的日志。")
            return
        self.log(f"原文件备份在 {os.path.join(self.bundle['out_dir'], 'backup')}")
        self.log("确认无误后，用 PowerShell 运行 install.ps1 覆盖到程序目录。")
        messagebox.showinfo("完成", f"已生成 {len(result['files'])} 个文件，在 out/ 里。\n原文件已备份。")

    def _apply_failed(self, exc: Exception) -> None:
        self.busy = False
        self.btn_apply.configure(state="normal")
        self.log(f"回填失败：{exc}")
        messagebox.showerror("回填失败", str(exc))

    def open_out(self) -> None:
        if not self.bundle:
            return
        path = self.bundle["out_dir"]
        try:
            if os.name == "nt":
                os.startfile(path)  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as exc:
            self.log(f"打不开文件夹：{exc}")

    def mainloop(self) -> None:
        self.root.mainloop()


def main() -> None:
    App().mainloop()
