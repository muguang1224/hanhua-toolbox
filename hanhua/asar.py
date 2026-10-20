"""Electron 的 app.asar 读取与重打包（零依赖）。

格式（小端）：
    [0:4]   uint32 = 4
    [4:8]   uint32 = H   —— 紧跟在这 8 字节之后、整个 header 块的字节数
    [8:12]  uint32 = J   —— JSON 字符串长度（不含 4 字节对齐填充）
    [12:]   JSON，补齐到 4 字节
    之后是各文件内容首尾相接，header 里的 offset 相对数据区起点。
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import struct


class AsarError(RuntimeError):
    pass


class AsarArchive:
    def __init__(self, path: str):
        self.path = path
        self._size = os.path.getsize(path)
        with open(path, "rb") as f:
            first = f.read(4)
            if len(first) < 4:
                raise AsarError("文件太小，不是 asar")
            if struct.unpack("<I", first)[0] != 4:
                raise AsarError("首字段不是 4，不是 asar 文件")
            h = struct.unpack("<I", f.read(4))[0]
            j = struct.unpack("<I", f.read(4))[0]
            if j <= 0 or j > self._size:
                raise AsarError(f"header 长度非法：{j}")
            raw = f.read(j)
        try:
            self.header = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AsarError(f"header JSON 解析失败：{exc}") from None

        self._h = h
        self._j = j
        padded = (j + 3) // 4 * 4
        self.data_offset = 8 + h
        # 真实文件偶尔会有额外的对齐差，记下来，重打包时原样补回
        self._pad_extra = self.data_offset - (12 + padded)
        if self.data_offset >= self._size:
            raise AsarError("数据区偏移越界，文件可能被截断")

    # ---------- 读 ----------

    def _node(self, arc_path: str) -> dict:
        parts = [p for p in arc_path.replace("\\", "/").split("/") if p]
        node = self.header
        for part in parts:
            files = node.get("files")
            if not isinstance(files, dict) or part not in files:
                raise AsarError(f"asar 里没有：{arc_path}")
            node = files[part]
        return node

    def read(self, arc_path: str, _depth: int = 0) -> bytes:
        if _depth > 8:
            raise AsarError("链接层数过深")
        node = self._node(arc_path)
        if "link" in node:
            return self.read(node["link"], _depth + 1)
        if "files" in node:
            raise AsarError(f"{arc_path} 是目录")
        if node.get("unpacked"):
            raise AsarError(f"{arc_path} 在 app.asar.unpacked 里，不在包内")
        offset = self.data_offset + int(node["offset"])
        size = int(node["size"])
        if offset + size > self._size:
            raise AsarError(f"{arc_path} 越界（offset={offset}, size={size}）")
        with open(self.path, "rb") as f:
            f.seek(offset)
            data = f.read(size)
        if len(data) != size:
            raise AsarError(f"{arc_path} 读取不完整")
        return data

    def list_files(self) -> list[str]:
        out: list[str] = []

        def walk(node: dict, prefix: str) -> None:
            for name, child in (node.get("files") or {}).items():
                p = f"{prefix}/{name}" if prefix else name
                if "files" in child:
                    walk(child, p)
                elif "link" not in child:
                    out.append(p)

        walk(self.header, "")
        return out

    def has_integrity(self) -> bool:
        """header 里带 integrity 字段的程序开启了完整性校验，改包后可能起不来。"""

        def walk(node: dict) -> bool:
            if isinstance(node.get("integrity"), dict):
                return True
            return any(walk(c) for c in (node.get("files") or {}).values())

        return walk(self.header)

    # ---------- 写 ----------

    def rebuild(self, replacements: dict[str, bytes]) -> bytes:
        """返回重打包后的完整 asar 字节流。replacements 的 key 是包内路径。"""
        header = copy.deepcopy(self.header)

        order: list[tuple[str, dict]] = []

        def walk(node: dict, prefix: str) -> None:
            for name, child in (node.get("files") or {}).items():
                p = f"{prefix}/{name}" if prefix else name
                if "files" in child:
                    walk(child, p)
                elif "link" not in child and not child.get("unpacked"):
                    order.append((p, child))

        walk(header, "")

        payload: list[bytes] = []
        cursor = 0
        for p, node in order:
            if p in replacements:
                data = replacements[p]
                node["size"] = len(data)
                _refresh_integrity(node, data)
            else:
                data = self.read(p)
            pad = (4 - cursor % 4) % 4
            if pad:
                payload.append(b"\0" * pad)
                cursor += pad
            node["offset"] = str(cursor)
            payload.append(data)
            cursor += len(data)

        json_bytes = json.dumps(header, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        padded = (len(json_bytes) + 3) // 4 * 4
        block = 4 + padded + self._pad_extra
        out = [
            struct.pack("<I", 4),
            struct.pack("<I", block),
            struct.pack("<I", len(json_bytes)),
            json_bytes,
            b"\0" * (padded - len(json_bytes)),
        ]
        if self._pad_extra > 0:
            out.append(b"\0" * self._pad_extra)
        out.extend(payload)
        return b"".join(out)


def _refresh_integrity(node: dict, data: bytes) -> None:
    """Electron 会给 asar 内每个文件记哈希。改过内容就必须重算，否则读取时报 integrity 错。

    注意：@electron/asar 用的是 sha256 的 **hex**（64 字符），不是 base64。
    """
    integ = node.get("integrity")
    if not isinstance(integ, dict):
        return
    if "blocks" in integ:
        size = int(integ.get("blockSize") or 4 * 1024 * 1024)
        chunks = [data[i:i + size] for i in range(0, len(data), size)] or [b""]
        integ["blocks"] = [hashlib.sha256(c).hexdigest() for c in chunks]
    if "hash" in integ:
        integ["hash"] = hashlib.sha256(data).hexdigest()


def is_asar(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            head = f.read(4)
        return len(head) == 4 and struct.unpack("<I", head)[0] == 4
    except OSError:
        return False
