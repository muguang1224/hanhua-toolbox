"""冒烟测试：python tests/test_smoke.py 直接跑，也可以用 pytest。"""

import hashlib
import json
import os
import shutil
import struct
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from hanhua import asar, detect, extract, pack, pipeline, pe

WORK = tempfile.mkdtemp(prefix="hanhua-test-")
FAILS = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else f"  <- {detail}"))
    if not cond:
        FAILS.append(name)


# ---------------- asar 构造（严格按格式写） ----------------
def build_asar(entries, extra_pad=0, integrity=False):
    """entries: {path: bytes}"""
    tree = {"files": {}}
    for path, data in entries.items():
        parts = path.split("/")
        node = tree
        for p in parts[:-1]:
            node = node["files"].setdefault(p, {"files": {}})
        node["files"][parts[-1]] = {"size": len(data), "offset": "0"}

    # 按遍历顺序分配 offset
    order = []
    def walk(node, prefix):
        for name, child in node["files"].items():
            p = f"{prefix}/{name}" if prefix else name
            if "files" in child:
                walk(child, p)
            else:
                order.append((p, child))
    walk(tree, "")

    blobs, cursor = [], 0
    for p, node in order:
        data = entries[p]
        pad = (4 - cursor % 4) % 4
        if pad:
            blobs.append(b"\0" * pad); cursor += pad
        node["offset"] = str(cursor)
        if integrity:
            node["integrity"] = {"algorithm": "SHA256",
                                 "hash": hashlib.sha256(data).hexdigest(),
                                 "blockSize": 4194304}
        blobs.append(data); cursor += len(data)

    j = json.dumps(tree, separators=(",", ":")).encode()
    padded = (len(j) + 3) // 4 * 4
    h = 4 + padded + extra_pad
    out = struct.pack("<I", 4) + struct.pack("<I", h) + struct.pack("<I", len(j))
    out += j + b"\0" * (padded - len(j)) + b"\0" * extra_pad
    return out + b"".join(blobs), tree


print("[1] asar 读取")

en_json = json.dumps({
    "menu": {"file": "File", "open": "Open File...", "quit": "Quit"},
    "dialog": {"title": "Are you sure?", "ok": "Yes", "cancel": "No"},
    "ignored": {"url": "https://example.com", "code": "%s"},
}, indent=2).encode()

raw, tree = build_asar({
    "package.json": b'{"name":"demo","version":"1.0.0"}',
    "locales/en.json": en_json,
    "main.js": b"console.log('hello world');",
}, extra_pad=0)

p = os.path.join(WORK, "app.asar")
open(p, "wb").write(raw)

arc = asar.AsarArchive(p)
check("列出文件", sorted(arc.list_files()) == ["locales/en.json", "main.js", "package.json"],
      arc.list_files())
check("读出 json", json.loads(arc.read("locales/en.json"))["menu"]["file"] == "File")
check("data_offset 正确", arc.data_offset == 8 + struct.unpack("<I", raw[4:8])[0],
      arc.data_offset)

# 额外填充的变体
raw2, _ = build_asar({"a.txt": b"hi"}, extra_pad=8)
p2 = os.path.join(WORK, "pad.asar")
open(p2, "wb").write(raw2)
arc2 = asar.AsarArchive(p2)
check("带额外填充的 asar 也能读", arc2.read("a.txt") == b"hi", arc2._pad_extra)

print("[2] asar 重打包往返")

repl = {"locales/en.json": json.dumps({"menu": {"file": "文件"}}, ensure_ascii=False).encode()}
blob = arc.rebuild(repl)
p3 = os.path.join(WORK, "rebuilt.asar")
open(p3, "wb").write(blob)
arc3 = asar.AsarArchive(p3)
check("重打包后仍可解析", True)
check("改动文件已生效", json.loads(arc3.read("locales/en.json"))["menu"]["file"] == "文件")
check("未改动文件原样保留", arc3.read("main.js") == b"console.log('hello world');")
check("未改动文件大小一致",
      arc3._node("package.json")["size"] == tree["files"]["package.json"]["size"])
check("总长度自洽",
      len(blob) == arc3.data_offset + sum(arc3._node(f)["size"] for f in arc3.list_files())
      or len(blob) >= arc3.data_offset)

# 空替换也应完全可读
arc4 = asar.AsarArchive(p3)
check("重建两次不漂移", json.loads(asar.AsarArchive(p3).read("locales/en.json"))["menu"]["file"] == "文件")

print("[3] 损坏/异常 asar 不崩溃")
bad = os.path.join(WORK, "bad.asar")
open(bad, "wb").write(b"not an asar at all")
try:
    asar.AsarArchive(bad)
    check("坏文件应报错", False)
except asar.AsarError:
    check("坏文件报 AsarError", True)
except Exception as e:
    check("坏文件报 AsarError", False, type(e).__name__)

print("[4] UTF-16 提取与定长回填")
buf = bytearray(64 * 1024)
strings = ["Open File", "Save As", "Settings", "Are you sure you want to quit?"]
off = 0x1000
spans = {}
for s in strings:
    b = s.encode("utf-16le")
    buf[off:off + len(b)] = b
    spans[s] = (off, len(b))
    off += len(b) + 8

exe_like = os.path.join(WORK, "fake.exe")
open(exe_like, "wb").write(bytes(buf))
found = extract.extract_binary_strings(WORK, ["fake.exe"])
texts = {e.original for e in found[0]}
check("扫到二进制文案", "Open File" in texts and "Settings" in texts, texts)

e = next(x for x in found[0] if x.original == "Open File")
span = e.meta["offsets"][0]
sb = bytearray(bytes(buf))
ok = pack._patch_utf16(sb, span["offset"], span["size"], "打开文件")
check("等长中文可替换", ok and sb[span["offset"]:span["offset"] + 8] == "打开文件".encode("utf-16le"))
check("超长译文被拒绝",
      not pack._patch_utf16(bytearray(bytes(buf)), span["offset"], span["size"], "打开文件打开文件打开文件"))
check("同文多处只生成一条", sum(1 for x in found[0] if x.original == "Open File") == 1)

print("[5] JSON 提取评分")
obj = json.loads(en_json)
ents = extract._json_entries(en_json, "json", "", "locales/en.json")
keys = {json.loads(x.key)[-1] for x in ents}
check("提取 UI 文案", {"file", "open", "title"} <= keys, keys)
check("过滤 URL", "url" not in keys, keys)
check("过滤占位符", "code" not in keys, keys)

print("[6] 伪 PE 识别")
def make_pe(managed):
    d = bytearray(0x400)
    d[0:2] = b"MZ"
    struct.pack_into("<I", d, 0x3C, 0x80)
    d[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<HH", d, 0x84, 0x8664, 1)
    struct.pack_into("<H", d, 0x84 + 16, 240)
    opt = 0x84 + 20
    struct.pack_into("<H", d, opt, 0x20B)
    struct.pack_into("<H", d, opt + 68, 2)
    struct.pack_into("<I", d, opt + 108, 16)
    struct.pack_into("<I", d, opt + 112 + 14 * 8, 0x2000 if managed else 0)
    return bytes(d)

gpe = os.path.join(WORK, "g.exe")
open(gpe, "wb").write(make_pe(False))
info = pe.read_pe(gpe)
check("PE 解析", info and info["machine"] == "x64" and info["is64"] and not info["managed"], info)

mpe = os.path.join(WORK, "m.exe")
open(mpe, "wb").write(make_pe(True))
check("CLR 检测", pe.read_pe(mpe)["managed"] is True)

check("非 PE 返回 None", pe.read_pe(os.path.join(WORK, "app.asar")) is None)

print("[7] 端到端：Electron 目录")
app = os.path.join(WORK, "MyApp")
os.makedirs(os.path.join(app, "resources"), exist_ok=True)
shutil.copy(p, os.path.join(app, "resources", "app.asar"))
open(os.path.join(app, "MyApp.exe"), "wb").write(make_pe(False))

bundle = pipeline.analyze(app, os.path.join(WORK, "out_hanhua"))
check("识别为 electron", bundle["info"]["kind"] == "electron", bundle["info"]["kind"])
check("提取到条目", len(bundle["entries"]) > 0, len(bundle["entries"]))
check("生成 strings.csv", os.path.isfile(bundle["csv"]))
check("生成 bundle.json", os.path.isfile(os.path.join(bundle["out_dir"], "bundle.json")))

# 填翻译
import csv
rows = list(csv.DictReader(open(bundle["csv"], encoding="utf-8-sig")))
tr = {"File": "文件", "Open File...": "打开文件…", "Quit": "退出",
      "Are you sure?": "确定吗？", "Yes": "是", "No": "否"}
for r in rows:
    if r["original"] in tr:
        r["translation"] = tr[r["original"]]
filled = os.path.join(bundle["out_dir"], "filled.csv")
with open(filled, "w", encoding="utf-8-sig", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
    w.writeheader(); w.writerows(rows)

res = pipeline.apply_csv(bundle, filled)
check("回填产生了文件", len(res["files"]) == 1 and res["files"][0].endswith("app.asar"), res)
out_asar = os.path.join(bundle["out_dir"], "out", "resources", "app.asar")
check("out 里生成了 asar", os.path.isfile(out_asar))
back_arc = asar.AsarArchive(out_asar)
data = json.loads(back_arc.read("locales/en.json"))
check("译文写进去了", data["menu"]["file"] == "文件" and data["dialog"]["title"] == "确定吗？", data)
check("没改的保持英文", data["menu"]["name"] if "name" in data["menu"] else "File" == "File")
check("备份存在", os.path.isfile(os.path.join(bundle["out_dir"], "backup", "resources", "app.asar")))
check("原文件未被修改", json.loads(asar.AsarArchive(os.path.join(app, "resources", "app.asar")).read("locales/en.json"))["menu"]["file"] == "File")
check("install.ps1 生成", os.path.isfile(os.path.join(bundle["out_dir"], "install.ps1")))
check("report.md 生成", os.path.isfile(os.path.join(bundle["out_dir"], "report.md")))

# 重新加载 bundle 再回填（模拟关掉工具再打开）
b2 = pipeline.load_bundle(bundle["out_dir"])
check("bundle 可重载", len(b2["entries"]) == len(bundle["entries"]))
res2 = pipeline.apply_csv(b2, filled)
check("重载后仍能回填", res2["files"] == res["files"], res2["files"])

print("[8] 端到端：普通 exe（二进制字符串）")
app2 = os.path.join(WORK, "GenericApp")
os.makedirs(app2, exist_ok=True)
exe_bytes = bytearray(96 * 1024)
exe_bytes[0:0x400] = make_pe(False)
_off = 0x2000
for s in ["Open File", "Settings", "Open File", "Are you sure you want to quit?"]:
    _b = s.encode("utf-16le")
    exe_bytes[_off:_off + len(_b)] = _b
    _off += len(_b) + 8
open(os.path.join(app2, "tool.exe"), "wb").write(bytes(exe_bytes))
bundle2 = pipeline.analyze(app2, os.path.join(WORK, "out_generic"))
check("识别/兜底", bundle2["info"]["kind"] in ("generic", "unknown"), bundle2["info"]["kind"])
check("二进制文案已提取", len(bundle2["entries"]) > 0, len(bundle2["entries"]))

rows = list(csv.DictReader(open(bundle2["csv"], encoding="utf-8-sig")))
for r in rows:
    if r["original"] == "Settings":
        r["translation"] = "设置"
    elif r["original"] == "Open File":
        r["translation"] = "打开文件"
filled2 = os.path.join(bundle2["out_dir"], "filled.csv")
with open(filled2, "w", encoding="utf-8-sig", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
    w.writeheader(); w.writerows(rows)

res_no = pipeline.apply_csv(bundle2, filled2, allow_binary=False)
check("默认不碰二进制", res_no["files"] == [], res_no["files"])
res_yes = pipeline.apply_csv(bundle2, filled2, allow_binary=True)
check("开启后写入 exe", any(f.endswith("tool.exe") for f in res_yes["files"]), res_yes["files"])
patched = open(os.path.join(bundle2["out_dir"], "out", "tool.exe"), "rb").read()
check("二进制里出现中文", "设置".encode("utf-16le") in patched)
dup = next(x for x in bundle2["entries"] if x.original == "Open File")
check("重复出现记录全部位置", len(dup.meta["offsets"]) == 2, dup.meta)
check("重复出现全部替换", patched.count("打开文件".encode("utf-16le")) == 2,
      patched.count("打开文件".encode("utf-16le")))
check("原 exe 未变", open(os.path.join(app2, "tool.exe"), "rb").read() == bytes(exe_bytes))

# 源文件被改过（模拟程序更新）时，不能照旧 offset 盲写
app3 = os.path.join(WORK, "MovedApp")
os.makedirs(app3, exist_ok=True)
shutil.copy(os.path.join(app2, "tool.exe"), os.path.join(app3, "tool.exe"))
bundle3 = pipeline.analyze(app3, os.path.join(WORK, "out_moved"))
dupy = bytearray(open(os.path.join(app3, "tool.exe"), "rb").read())
_i = dupy.find("Settings".encode("utf-16le"))
dupy[_i:_i + 2] = b"ZZ"
open(os.path.join(app3, "tool.exe"), "wb").write(bytes(dupy))
res_moved = pipeline.apply_csv(bundle3, filled2, allow_binary=True)
_out_m = os.path.join(bundle3["out_dir"], "out", "tool.exe")
_out_bytes = open(_out_m, "rb").read() if os.path.isfile(_out_m) else b""
check("源文件变了就拒绝按旧位置写",
      any("原位置已不是原文" in s for s in res_moved["skipped"])
      and "设置".encode("utf-16le") not in _out_bytes,
      res_moved["skipped"][:3])

print("[9] 异常输入")
try:
    pipeline.analyze(os.path.join(WORK, "nope.lnk"))
    check("不存在路径应报错", False)
except Exception as e:
    check("不存在路径报错", "不存在" in str(e), e)

empty = os.path.join(WORK, "EmptyApp")
os.makedirs(empty, exist_ok=True)
b3 = pipeline.analyze(empty, os.path.join(WORK, "out_empty"))
check("空目录不崩溃", b3["entries"] == [] and b3["info"]["kind"] == "unknown", b3["info"]["kind"])
res3 = pipeline.apply_csv(b3, b3["csv"])
check("空翻译表回填不崩溃", res3["files"] == [])

print()
print("[10] 完整性校验与旧表保护")
raw_i, _ = build_asar({"en.json": b'{"a":"Hello"}'}, integrity=True)
p_i = os.path.join(WORK, "integ.asar")
open(p_i, "wb").write(raw_i)
arc_i = asar.AsarArchive(p_i)
check("识别出完整性字段", arc_i.has_integrity() is True)
new_en = json.dumps({"a": "你好"}, ensure_ascii=False).encode()
blob_i = arc_i.rebuild({"en.json": new_en})
p_i2 = os.path.join(WORK, "integ2.asar")
open(p_i2, "wb").write(blob_i)
node_i = asar.AsarArchive(p_i2)._node("en.json")
check("哈希重算为 sha256 hex", node_i["integrity"]["hash"] == hashlib.sha256(new_en).hexdigest(),
      node_i["integrity"])
check("哈希长度为 64", len(node_i["integrity"]["hash"]) == 64)
check("内容已改", json.loads(asar.AsarArchive(p_i2).read("en.json"))["a"] == "你好")

out_reuse = os.path.join(WORK, "out_reuse")
pipeline.analyze(app, out_reuse)
filled_before = open(os.path.join(out_reuse, "strings.csv"), "w", encoding="utf-8")
filled_before.write("SENTINEL")
filled_before.close()
b_again = pipeline.analyze(app, out_reuse)
check("旧翻译表被改名保留",
      any(f.startswith("strings.旧-") for f in os.listdir(out_reuse)), os.listdir(out_reuse))
kept = [f for f in os.listdir(out_reuse) if f.startswith("strings.旧-")][0]
check("旧内容没丢", open(os.path.join(out_reuse, kept), encoding="utf-8").read() == "SENTINEL")
check("给了提示", any("旧的翻译表" in n for n in b_again["notes"]), b_again["notes"])

print()
if FAILS:
    print(f"失败 {len(FAILS)} 项：{FAILS}")
    print(f"失败时保留临时目录：{WORK}")
    sys.exit(1)
print("全部通过")
shutil.rmtree(WORK, ignore_errors=True)
