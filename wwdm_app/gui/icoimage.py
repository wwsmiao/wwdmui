# -*- coding: utf-8 -*-
"""ICO → tkinter PhotoImage 的纯 Python 解码器（v6.0(5)）。

为什么不用 Pillow：打包 exe 要跟着多背一个几十 MB 的轮子，而需求只是
「把一枚 .ico 画到悬浮球上」。ico 里的 PNG 条目本身就是现成素材 —— 解出来
喂 ``tk.PhotoImage(data=...)`` 即可（Tk 8.6 原生支持 PNG + alpha，已实测：
品红挖孔背景下四角落显示桌面内容、中心猫脸纯白、红环 (215,39,71) 精确命中）。

契约（宁可回退也不猜）：
  * 只接 **PNG 压缩** 的 ico 条目（现代资源编辑器的默认格式）；BMP-DIB 老
    条目直接放弃 → 调用方回退默认绘制；
  * 只支持 8 位深、colortype 2(RGB) / 6(RGBA)、非隔行 —— 覆盖 99% 现代 ico；
    不满足的一律放弃；
  * hover 变体 = 同一份 RGBA 的 RGB×factor（alpha 不动），纯字节变换，可单测；
  * 缩放用 PhotoImage 自带的 zoom/subsample（最近邻，小图标够用）。

铁律（同 fab/tray）：本模块顶层不 import tkinter —— PhotoImage 只在工厂函数
``ball_images`` 里建，import 本包（含 pytest / 无头环境）不需要 Tk。
"""
import os
import struct
import zlib


def ico_png_bytes(path):
    """解析 ICONDIR，挑**最大**（按字节数，通常就是最高分辨率）的 PNG 条目。

    返回该条目的 PNG 字节；没有 PNG 条目 / 文件损坏 / 不存在则返回 ``None``。
    """
    try:
        b = open(path, "rb").read()
    except Exception:
        return None
    if len(b) < 6 or b[:4] != b"\x00\x00\x01\x00":
        return None
    count = struct.unpack("<H", b[4:6])[0]
    off = 6
    best, best_size = None, -1
    for _ in range(count):
        if off + 16 > len(b):
            break
        w, h, ncol, _, planes, bpp, size, entry_off = struct.unpack(
            "<BBBBHHII", b[off:off + 16])
        off += 16
        data = b[entry_off:entry_off + size]
        # PNG 条目：签名校验（(w or 256) 仅用于「最大」比较，不强制正方形）
        if data[:8] == b"\x89PNG\r\n\x1a\n" and size > best_size:
            best, best_size = data, size
    return best


def _unfilter(w, h, raw, channels):
    """PNG 反滤波（filter 0/1/2/3/4），输出 RGB/RGBA 行拼接的 bytearray。"""
    stride = w * channels
    out = bytearray()
    prev = bytearray(stride)
    pos = 0
    bppx = channels  # 8 位深 → 每通道 1 字节
    for _ in range(h):
        if pos >= len(raw):
            break
        f = raw[pos]
        pos += 1
        line = bytearray(raw[pos:pos + stride])
        pos += stride
        if f == 1:            # Sub
            for i in range(bppx, stride):
                line[i] = (line[i] + line[i - bppx]) & 255
        elif f == 2:          # Up
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 255
        elif f == 3:          # Average
            for i in range(stride):
                a = line[i - bppx] if i >= bppx else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 255
        elif f == 4:          # Paeth
            for i in range(stride):
                a = line[i - bppx] if i >= bppx else 0
                cc = prev[i - bppx] if i >= bppx else 0
                bb = prev[i]
                p = a + bb - cc
                pa, pb, pc = abs(p - a), abs(p - bb), abs(p - cc)
                pr = a if (pa <= pb and pa <= pc) else (bb if pb <= pc else cc)
                line[i] = (line[i] + pr) & 255
        out += line
        prev = line
    return out


def decode_png(png):
    """解析 PNG → ``(w, h, rgba)`` 或 ``None``。

    支持 8 位深、colortype 2/6、非隔行；其余（隔行、位深≠8、灰度/索引）
    一律回退 ``None``。返回 RGBA bytearray（colortype=2 时 alpha 填 255）。
    """
    if not png or png[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    idx = 8
    w = h = bd = ct = interlace = None
    idat = bytearray()
    end = len(png)
    while idx + 8 <= end:
        ln, typ = struct.unpack(">I4s", png[idx:idx + 8])
        chunk = png[idx + 8:idx + 8 + ln]
        if typ == b"IHDR":
            w, h, bd, ct, _, interlace, _ = struct.unpack(">IIBBBBB", chunk)
        elif typ == b"IDAT":
            idat += chunk
        elif typ == b"IEND":
            break
        idx += 12 + ln
    if w is None or h is None:
        return None
    if bd != 8 or ct not in (2, 6) or interlace != 0:
        return None
    try:
        raw = zlib.decompress(idat)
    except Exception:
        return None
    channels = 3 if ct == 2 else 4
    rgba = _unfilter(w, h, raw, channels)
    expected = w * h * 4
    if len(rgba) < expected:
        return None
    if channels == 3:
        # 扩成 RGBA（alpha=255）
        out = bytearray()
        for i in range(0, len(rgba), 3):
            out += rgba[i:i + 3] + b"\xff"
        rgba = out
    return w, h, rgba[:expected]


def _png_encode(w, h, rgba):
    """RGBA → PNG 字节（filter=0 + zlib）。纯字节变换，可单测。"""
    raw = b"".join(b"\x00" + rgba[y * w * 4:(y + 1) * w * 4]
                    for y in range(h))

    def _chunk(tag, data):
        body = tag + data
        return (struct.pack(">I", len(data)) + body
                + struct.pack(">I", zlib.crc32(body) & 0xffffffff))

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n"
            + _chunk(b"IHDR", ihdr)
            + _chunk(b"IDAT", zlib.compress(raw, 9))
            + _chunk(b"IEND", b""))


def recolor(png, factor=0.85):
    """同图 RGB×factor（alpha 不动）生成 hover 变体；不可解则回退 ``None``。"""
    dec = decode_png(png)
    if dec is None:
        return None
    w, h, rgba = dec
    for i in range(0, len(rgba), 4):
        rgba[i] = min(255, int(rgba[i] * factor)) & 255
        rgba[i + 1] = min(255, int(rgba[i + 1] * factor)) & 255
        rgba[i + 2] = min(255, int(rgba[i + 2] * factor)) & 255
    return _png_encode(w, h, rgba)


def ball_images(path, size, master=None):
    """读 .ico → ``(normal_photo, hover_photo)`` 或 ``None``（回退默认绘制）。

    ``size`` 为目标边长；与源不等时用 zoom/subsample 最近邻缩放。``master`` 为
    tkinter 根/Toplevel（必须已存在 —— 否则 PhotoImage 报「no default root」）。
    调用方负责持有返回的两个 PhotoImage（tkinter 经典坑：没引用会被 GC 掉导致空白）。
    """
    png = ico_png_bytes(path)
    if png is None:
        return None
    dec = decode_png(png)
    if dec is None:
        return None
    import tkinter as tk
    native = dec[0]
    try:
        normal = _scale(tk.PhotoImage(master=master, data=png), native, size)
    except Exception:
        return None
    hover = recolor(png, 0.85)
    if hover is None:
        return None
    try:
        hover_img = _scale(tk.PhotoImage(master=master, data=hover), native, size)
    except Exception:
        hover_img = normal
    return normal, hover_img


def _scale(photo, native, target):
    """最近邻缩放：native==target 原样；整除 subsample；否则 zoom×/subsample÷。"""
    if native == target:
        return photo
    if native % target == 0:
        return photo.subsample(native // target)
    k = 1
    while k < 16 and (native * k) % target != 0:
        k += 1
    if (native * k) % target != 0:
        return photo
    return photo.zoom(k).subsample(native * k // target)


def default_icon_path():
    """随包资源：gui/assets/wwdm_cat.ico（托盘与悬浮球共用）。"""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "assets", "wwdm_cat.ico")
