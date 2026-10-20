# 汉化工具箱 · hanhua-toolbox

把程序的**快捷方式拖进窗口**，自动找到程序目录、提取待翻译文案、生成可以回填的汉化包。

[![CI](https://github.com/muguang1224/hanhua-toolbox/actions/workflows/ci.yml/badge.svg)](https://github.com/muguang1224/hanhua-toolbox/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.10%2B-3776ab)
![Platform](https://img.shields.io/badge/platform-Windows-lightgrey)
![License](https://img.shields.io/badge/license-MIT-green)
![Deps](https://img.shields.io/badge/core%20deps-stdlib%20only-brightgreen)

---

## 下载

[Releases](https://github.com/muguang1224/hanhua-toolbox/releases) 里有打包好的 `HanhuaToolbox.exe`，单文件、双击即用，不用装 Python。

> 这个 exe 没有代码签名，第一次运行时 Windows SmartScreen 会拦一下，显示「Windows 已保护你的电脑」。点「更多信息」→「仍要运行」即可。介意的话按下面「打包成 exe」自己从源码构建。

## 先说清楚它不做什么

网上说的"一键全自动汉化"基本不存在。汉化本质是**修改别人已经编译好的程序**，能做到什么程度完全取决于程序怎么写的：有的程序把所有文案放在一个 JSON 里，有的编译进二进制，有的加密加壳。没有一个工具能同时正确处理这几种。

所以这个工具的原则是：**把能可靠做对的部分做对，做不对的部分直接告诉你，不假装成功。**

它会给你一份 `report.md`，写清楚识别成了什么类型、依据是什么、哪些文件没扫到、哪些译文没能写回去。

## 三步用起来

1. 把程序的 `.lnk` 快捷方式（或 exe、程序文件夹）拖进窗口。
2. 桌面会生成 `汉化包_<程序名>/`：

   ```
   汉化包_DemoApp/
   ├── report.md      识别结果、判断依据、注意事项
   ├── strings.csv    待翻译表 —— 只改 translation 列，original 列别动
   ├── bundle.json    中间状态，关掉工具再打开还能接着回填
   └── install.ps1    把 out/ 覆盖到程序目录，覆盖前先备份
   ```

3. 用 Excel 填完 `translation` 列保存，回到工具点「导入翻译表并回填」。结果在 `out/`，原件在 `backup/`。

想撤销：把 `backup/` 里的文件原样复制回去。

## 不想装东西？先跑 demo

```bash
git clone https://github.com/muguang1224/hanhua-toolbox.git
cd hanhua-toolbox
python examples/make_demo.py          # 造一个假的 Electron 程序
python -m hanhua.cli analyze demo/MyApp
```

输出：

```
识别：Electron 程序（asar 包）（electron）
目录：.../demo/MyApp
  · 发现 resources/app.asar
待翻译：15 条
  ! Electron 的文案也可能写死在 .js/.html 里，那部分本工具不提取
翻译表：~/Desktop/汉化包_MyApp/strings.csv
```

## 支持矩阵

| 程序类型 | 提取 | 回填 | 说明 |
|---|:---:|:---:|---|
| Electron（语言 JSON 在 asar 里） | ✅ | ✅ | 最可靠的一类，会顺带重算 asar 内的文件哈希 |
| Electron（文案写死在 js/html） | ❌ | ❌ | 不解析代码，硬做会改坏程序 |
| .NET / 普通 exe、dll | ⚠️ 扫 UTF-16 字符串 | ⚠️ 定长替换，默认关闭 | 中文不能比原文长；同一句出现多次会一起改 |
| Qt | ⚠️ 扫 exe/dll + 列出 `.qm` | ⚠️ 同上 | `.qm` 是二进制翻译文件，本工具不解析 |
| Java / Unity / 加壳程序 | ❌ | ❌ | 未支持 |

识别依据：有 `resources/app.asar` → Electron；有 `Qt*Core.dll` 或 `*.qm` → Qt；exe 带 CLR 头或 `*.resources.dll` → .NET；其余按普通 PE 处理。

## 打包成 exe

Windows 上装好 Python 3.10+（安装时勾选 tcl/tk），双击 `build.bat`：

```
dist\HanhuaToolbox.exe
```

单文件、双击即用。`build.bat` 会自动装 `pyinstaller` 和 `tkinterdnd2`。

> PyInstaller 不能跨平台编译，exe 必须在 Windows 上打。也可以在 Actions 里手动触发 **Build exe** 工作流。

不想打包就直接跑：

```bash
pip install tkinterdnd2
python run.py
```

没装 `tkinterdnd2` 也能启动，只是拖拽失灵，点框选文件就行。

## 命令行

```bash
python -m hanhua.cli analyze "C:\Users\me\Desktop\某程序.lnk"
python -m hanhua.cli apply "C:\Users\me\Desktop\汉化包_某程序\strings.csv" \
                           -o "C:\Users\me\Desktop\汉化包_某程序"
python -m hanhua.cli apply ... --allow-binary   # 允许写 exe/dll（定长替换）
```

## 工程上较真的几处

这些不是"顺手加的"，每一个都对应一种会让工具静默干坏事的坑：

**asar 重打包**严格按 `[uint32=4][uint32=headerSize][uint32=jsonLen][json 补齐到 4 字节][数据]` 布局写，文件偏移相对数据区起点、按 4 字节对齐。改过内容后会重算 `integrity`——用 **sha256 的 hex**（64 字符），不是 base64；写成 base64 会让 Electron 直接拒绝加载。

**二进制回填前逐处比对原文**。程序更新一次，旧翻译表里的字节偏移就全废了，照旧写入会把中文塞进无关字节、产出打不开的 exe。所以每一处都先验证"这里还是不是原来那句话"，不是就跳过并报告。

**同一句英文出现多次会一起改**。提取时按文本归并、把所有出现位置记在一条记录里；只改第一处是这类工具最常见的"看起来成功"的假象。

**快捷方式解析走 PowerShell 时先把控制台编码钉成 UTF-8**。Windows PowerShell 5.1 重定向输出默认走 OEM 代码页，中文目录名会变成乱码，随后"目标不存在"——这个 bug 只在中文路径下出现。

**tkinter 只在主线程碰**。后台线程只往队列塞结果，主线程用 `after()` 取，避免 Windows 上随机的 RuntimeError 或界面假死。

**旧的 `strings.csv` 不会被覆盖**。重新分析同一个输出目录时，旧文件改名为 `strings.旧-<时间戳>.csv` 保留。

## 目录结构

```
hanhua/
  resolve.py    .lnk → 目标 exe（PowerShell，编码已钉成 UTF-8）
  pe.py         PE 头解析：位数、.NET CLR、GUI/控制台子系统
  asar.py       asar 读写与重打包（含 integrity 重算）
  detect.py     程序类型识别
  extract.py    JSON 叶子提取 + 二进制 UTF-16 串扫描
  pack.py       翻译表、回填、备份、install.ps1、报告
  pipeline.py   串联流程，bundle.json 持久化
  gui.py        拖拽界面（主线程轮询队列）
  cli.py        命令行
examples/make_demo.py   造一个假程序用来试
tests/test_smoke.py     54 项冒烟测试
```

## 测试

```bash
python tests/test_smoke.py
```

覆盖：asar 往返一致性与损坏文件、完整性哈希重算、重复字符串多点替换、源文件被改后拒绝盲写、超长译文拒绝、空目录/坏文件不崩溃、bundle 重载后可继续回填。CI 在 Ubuntu 和 Windows 上各跑一遍。

## 已知限制

都是真的，别指望绕过：

- **数字签名**：改过的文件签名会失效，部分程序因此拒绝运行。
- **完整性校验**：Electron 如果开了 `EnableEmbeddedAsarIntegrityValidation`，header 哈希存在 exe 里，工具改不到——报告里会警告，别反复试。
- **定长替换**：二进制回填要求中文不比原文长，超了直接跳过并记录，不会写坏。
- **漏译**：扫不到的是真扫不到，报告里会写明没扫描哪些文件（比如超过 12MB 的）。
- **只认中文界面**：工具本身没做多语言。

## 许可

MIT。

只建议用来汉化你自己合法持有的软件、供个人使用。分发修改过的程序之前，先看那款软件的许可协议——大部分商业软件不允许。
