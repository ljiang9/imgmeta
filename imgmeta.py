#!/usr/bin/env python3
"""imgmeta: 从文件头读取图片元数据，零依赖（只用标准库）。

支持：PNG（IHDR + tEXt 文本块）、JPEG（SOF 标记）、GIF、BMP、WebP（VP8/VP8L/VP8X）。
只读文件头，不解码像素；不解析 EXIF。
"""

import argparse
import json
import os
import struct
import sys

VERSION = "0.1.0"


class ImageError(Exception):
    """解析失败：未知格式或文件损坏。"""


PNG_SIG = b"\x89PNG\r\n\x1a\n"

# PNG 颜色类型
PNG_COLOR_TYPES = {
    0: "灰度",
    2: "真彩色（RGB）",
    3: "索引色",
    4: "灰度 + Alpha",
    6: "真彩色 + Alpha（RGBA）",
}

# JPEG：携带尺寸的 SOF 标记（排除只定义 Huffman/量化的）
JPEG_SOF = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
            0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
# JPEG：无长度字段的独立标记
JPEG_STANDALONE = {0x01} | set(range(0xD0, 0xD8 + 1))


def _read_png(f, size):
    if f.read(8) != PNG_SIG:
        raise ImageError("PNG 签名不匹配，文件可能损坏")
    width = height = bit_depth = color_type = None
    texts = []
    while True:
        head = f.read(8)
        if len(head) < 8:
            break  # 没有 IEND 也尽量返回已读到的信息
        length, ctype = struct.unpack(">I4s", head)
        if length > 16 * 1024 * 1024:
            raise ImageError(f"PNG 数据块长度异常（{length} 字节），文件可能损坏")
        data = f.read(length)
        f.read(4)  # CRC：元数据工具不校验，只跳过
        if len(data) < length:
            raise ImageError("PNG 数据块被截断，文件可能损坏")
        if ctype == b"IHDR":
            if len(data) < 13:
                raise ImageError("PNG IHDR 数据块过短，文件可能损坏")
            width, height, bit_depth, color_type, _, _, _ = struct.unpack(">IIBBBBB", data[:13])
        elif ctype == b"tEXt":
            keyword, sep, text = data.partition(b"\x00")
            if sep:
                texts.append({
                    "keyword": keyword.decode("latin-1", "replace"),
                    "text": text.decode("latin-1", "replace"),
                })
        elif ctype == b"IEND":
            break
    if width is None:
        raise ImageError("PNG 缺少 IHDR 数据块，文件可能损坏")
    return {
        "format": "PNG",
        "width": width,
        "height": height,
        "bit_depth": bit_depth,
        "color": PNG_COLOR_TYPES.get(color_type, f"未知（{color_type}）"),
        "text_chunks": texts,
    }


def _read_jpeg(f, size):
    if f.read(2) != b"\xff\xd8":
        raise ImageError("JPEG 缺少 SOI 标记，文件可能损坏")
    while True:
        byte = f.read(1)
        if not byte:
            raise ImageError("JPEG 提前结束，未找到 SOF 标记")
        if byte != b"\xff":
            continue
        marker = f.read(1)
        while marker == b"\xff":  # 跳过填充字节
            marker = f.read(1)
        if not marker:
            raise ImageError("JPEG 提前结束，未找到 SOF 标记")
        m = marker[0]
        if m == 0xD8:  # SOI（极少数文件会有内嵌缩略图）
            continue
        if m == 0xD9:
            raise ImageError("JPEG 到达 EOI 仍未找到 SOF 标记")
        if m in JPEG_STANDALONE:
            continue
        if m == 0xDA:  # SOS：正常文件 SOF 一定在 SOS 之前
            raise ImageError("JPEG 在扫描数据开始前未找到 SOF 标记")
        raw = f.read(2)
        if len(raw) < 2:
            raise ImageError("JPEG 标记长度被截断")
        (length,) = struct.unpack(">H", raw)
        if length < 2:
            raise ImageError("JPEG 标记长度异常")
        if m in JPEG_SOF:
            data = f.read(length - 2)
            if len(data) < 7:
                raise ImageError("JPEG SOF 数据块被截断")
            precision, height, width = struct.unpack(">BHH", data[:5])
            return {
                "format": "JPEG",
                "width": width,
                "height": height,
                "bit_depth": precision,
                "color": "YCbCr/灰度（按分量数）",
            }
        f.seek(length - 2, os.SEEK_CUR)


def _read_gif(f, size):
    magic = f.read(6)
    if magic not in (b"GIF87a", b"GIF89a"):
        raise ImageError("GIF 魔数不匹配，文件可能损坏")
    raw = f.read(4)
    if len(raw) < 4:
        raise ImageError("GIF 逻辑屏幕描述符被截断")
    width, height = struct.unpack("<HH", raw)
    return {
        "format": "GIF",
        "width": width,
        "height": height,
        "bit_depth": None,
        "color": "索引色（≤256 色）",
    }


def _read_bmp(f, size):
    if f.read(2) != b"BM":
        raise ImageError("BMP 签名不匹配")
    if len(f.read(12)) < 12:
        raise ImageError("BMP 文件头被截断")
    raw = f.read(4)
    if len(raw) < 4:
        raise ImageError("BMP DIB 头被截断")
    (dib_size,) = struct.unpack("<I", raw)
    if dib_size == 40:  # BITMAPINFOHEADER
        raw = f.read(12)
        if len(raw) < 12:
            raise ImageError("BMP DIB 头被截断")
        width, height, _planes, bpp = struct.unpack("<iiHH", raw)
    elif dib_size == 12:  # BITMAPCOREHEADER
        raw = f.read(8)
        if len(raw) < 8:
            raise ImageError("BMP DIB 头被截断")
        width, height, _planes, bpp = struct.unpack("<HHHH", raw)
    else:
        raise ImageError(f"不支持的 BMP DIB 头（{dib_size} 字节）")
    return {
        "format": "BMP",
        "width": abs(width),
        "height": abs(height),
        "bit_depth": bpp,
        "color": "RGB（未压缩）" if bpp >= 24 else f"索引色（{bpp} 位）",
    }


def _read_webp(f, size):
    if f.read(4) != b"RIFF":
        raise ImageError("WebP 缺少 RIFF 头")
    f.read(4)  # 文件总长度，元数据不需要
    if f.read(4) != b"WEBP":
        raise ImageError("WebP 缺少 WEBP 标识")
    fourcc = f.read(4)
    raw = f.read(4)
    if len(raw) < 4:
        raise ImageError("WebP 数据块头被截断")
    if fourcc == b"VP8 ":  # 有损
        f.read(3)  # 帧标签
        if f.read(3) != b"\x9d\x01\x2a":
            raise ImageError("WebP VP8 起始码异常，文件可能损坏")
        raw = f.read(4)
        if len(raw) < 4:
            raise ImageError("WebP VP8 帧头被截断")
        w, h = struct.unpack("<HH", raw)
        return {"format": "WebP", "variant": "VP8（有损）",
                "width": w & 0x3FFF, "height": h & 0x3FFF,
                "bit_depth": 8, "color": "YCbCr"}
    if fourcc == b"VP8L":  # 无损
        if f.read(1) != b"\x2f":
            raise ImageError("WebP VP8L 签名异常，文件可能损坏")
        raw = f.read(4)
        if len(raw) < 4:
            raise ImageError("WebP VP8L 头被截断")
        (v,) = struct.unpack("<I", raw)
        return {"format": "WebP", "variant": "VP8L（无损）",
                "width": (v & 0x3FFF) + 1, "height": ((v >> 14) & 0x3FFF) + 1,
                "bit_depth": 8, "color": "ARGB"}
    if fourcc == b"VP8X":  # 扩展（含动画/Alpha/EXIF 容器）
        f.read(4)  # 标志 + 保留
        w24 = f.read(3)
        h24 = f.read(3)
        if len(w24) < 3 or len(h24) < 3:
            raise ImageError("WebP VP8X 画布头被截断")
        return {"format": "WebP", "variant": "VP8X（扩展）",
                "width": int.from_bytes(w24, "little") + 1,
                "height": int.from_bytes(h24, "little") + 1,
                "bit_depth": None, "color": "见内嵌 VP8/VP8L 数据块"}
    raise ImageError(f"不支持的 WebP 子格式：{fourcc!r}")


_READERS = (
    (lambda m: m[:8] == PNG_SIG, _read_png),
    (lambda m: m[:2] == b"\xff\xd8", _read_jpeg),
    (lambda m: m[:6] in (b"GIF87a", b"GIF89a"), _read_gif),
    (lambda m: m[:2] == b"BM", _read_bmp),
    (lambda m: m[:4] == b"RIFF" and m[8:12] == b"WEBP", _read_webp),
)


def inspect(path):
    """读取单个图片文件的元数据。失败抛 ImageError / OSError。"""
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        magic = f.read(12)
        f.seek(0)
        for matches, reader in _READERS:
            if matches(magic):
                info = reader(f, size)
                info["path"] = path
                info["file_size"] = size
                return info
    raise ImageError("未知格式（仅支持 PNG / JPEG / GIF / BMP / WebP）")


def _human_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024


def format_card(info):
    lines = [
        f"文件：{info['path']}（{_human_size(info['file_size'])}）",
        f"格式：{info['format']}" + (f" {info['variant']}" if info.get("variant") else ""),
        f"尺寸：{info['width']} × {info['height']}",
    ]
    if info.get("bit_depth") is not None:
        lines.append(f"位深：{info['bit_depth']} 位")
    if info.get("color"):
        lines.append(f"颜色：{info['color']}")
    for t in info.get("text_chunks") or []:
        lines.append(f"文本块 [{t['keyword']}]：{t['text']}")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="imgmeta",
        description="从文件头读取图片元数据（PNG/JPEG/GIF/BMP/WebP），零依赖。",
    )
    parser.add_argument("--version", action="version", version=f"imgmeta {VERSION}")
    parser.add_argument("path", nargs="?", help="图片文件路径")
    parser.add_argument("--dir", metavar="目录", help="扫描目录下所有文件并输出表格")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    args = parser.parse_args(argv)

    targets = []
    if args.dir:
        if not os.path.isdir(args.dir):
            print(f"error: 不是目录：{args.dir}", file=sys.stderr)
            return 2
        for name in sorted(os.listdir(args.dir)):
            p = os.path.join(args.dir, name)
            if os.path.isfile(p):
                targets.append(p)
        if not targets:
            print(f"error: 目录为空：{args.dir}", file=sys.stderr)
            return 2
    elif args.path:
        targets = [args.path]
    else:
        parser.print_usage(sys.stderr)
        print("error: 请指定图片文件或 --dir 目录", file=sys.stderr)
        return 2

    results = []
    failed = 0
    for t in targets:
        try:
            results.append(inspect(t))
        except (ImageError, OSError) as e:
            print(f"error: {t}：{e}", file=sys.stderr)
            failed += 1

    if args.json:
        # 单文件输出对象，目录扫描输出数组
        print(json.dumps(results[0] if (len(targets) == 1 and results) else results,
                         ensure_ascii=False, indent=2))
    elif args.dir:
        print(f"  {'文件':<28} {'格式':<10} {'尺寸':<14} {'大小':<10}")
        print("  " + "-" * 66)
        for r in results:
            print(f"  {os.path.basename(r['path']):<28} "
                  f"{r['format']:<10} "
                  f"{r['width']}x{r['height']:<11} "
                  f"{_human_size(r['file_size']):<10}")
        print(f"\n共 {len(results)} 个成功，{failed} 个失败。")
    else:
        for r in results:
            print(format_card(r))

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
