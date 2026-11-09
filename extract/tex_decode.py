# -*- coding: utf-8 -*-
"""Unity Texture2D 纹理解码（修正版）

背景：游戏资源里贴图是 Unity 序列化对象（DBreeze 存的是 LZ4 压过的对象字节），
常见格式 ETC2_RGBA8（枚举 47），尺寸 256×256 / 512×512 / 694×828。

★ 原实现（UnityPy 的 Texture2DConverter）在这里会解出花屏，根因不是解码器，
  而是"取数据起点"的方式：它用"arr_start 之后第一个 1000 < int32 < 20M 的数值"
  当作数组长度，小图（256×256 = 65 KB）上会抓到 2048 之类无关字段，长度全错。

★ 正确做法：
    起点 = 'System.Byte[], mscorlib' 之后第 8~24 字节（实测稳定在 +15）
    长度 = 解压总长 − 起点，而不是某个字段值
  由于偏移在不同对象上会差几个字节，这里遍历 delta 8..24，
  用"横向梯度平滑度"挑最像图的那一个（纯白线描图 smooth ≈ 0）。

★ 判据：本作天赋图标是 RGB 三通道完全相等的纯白 + alpha 0~255（游戏内靠 tint 上色），
  所以纯白不是缺色，smooth ≈ 0 也不代表解空。

用法:
    python extract/tex_decode.py utex2.icon_531_edu --src raw/db.bin --index tex_keys.json --out tex_out
    python extract/tex_decode.py --pat tt_talent_ --out tex_out
"""

import argparse
import json
import os
import struct
import sys

import lz4.frame as lz4f
from PIL import Image

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from UnityPy.export import Texture2DConverter as T2D  # noqa: E402

try:
    from UnityPy.export.Texture2DConverter import TextureFormat as TF
except ImportError:  # 新版 UnityPy 把枚举挪到了 enums
    from UnityPy.enums import TextureFormat as TF

LZ4_MAGIC = b"\x04\x22\x4d\x18"
BYTE_ARRAY_MARK = "System.Byte[], mscorlib".encode("utf-16le")
READ_AHEAD = 4 << 20


class _FakeReader:
    """满足 get_image_from_texture2d 需要的最小 object_reader 接口。"""
    version = (0, 0, 0, 0)
    platform = None


class FakeTexture2D:
    def __init__(self, data):
        self.image_data = data
        self.object_reader = _FakeReader()
        self.m_StreamData = None
        self.m_PlatformBlob = None

    def get_image_data(self):
        return self.image_data


def find_field(data, name):
    """定位字段值起点：u32(len) + utf16(name) 之后。找不到返回 None。"""
    raw = name.encode("utf-16le")
    i = data.find(raw)
    while i >= 0:
        if i >= 4 and struct.unpack("<I", data[i - 4:i])[0] == len(name):
            return i + len(raw)
        i = data.find(raw, i + 1)
    return None


def smoothness(img):
    """横向梯度均值，越小越像正常图像（噪声图会很大）。"""
    import numpy as np
    a = np.asarray(img.convert("L"), dtype="int16")
    return float(abs(a[:, 1:] - a[:, :-1]).mean())


def decode_tex(data, out_png=None, max_delta=24, good_enough=8.0):
    """解码一段已解压的 Unity Texture2D 对象字节。

    返回 (PIL.Image | None, 说明字符串)
    """
    wp = find_field(data, "Width")
    hp = find_field(data, "Height")
    fp = find_field(data, "Format")
    if None in (wp, hp, fp):
        return None, "no-header"
    width, height, fmt = (struct.unpack("<i", data[p:p + 4])[0] for p in (wp, hp, fp))

    content = find_field(data, "Content")
    base = data.find(BYTE_ARRAY_MARK, content) if content is not None else -1
    if base < 0:
        return None, "no-content"
    arr_start = base + len(BYTE_ARRAY_MARK)

    best = None
    for delta in range(8, max_delta + 1):
        start = arr_start + delta
        if start >= len(data):
            break
        tex = FakeTexture2D(data[start:])
        tex.m_Width, tex.m_Height, tex.m_TextureFormat = width, height, fmt
        try:
            img = T2D.get_image_from_texture2d(tex)
        except Exception:
            continue
        score = smoothness(img)
        if best is None or score < best[0]:
            best = (score, img, delta)
        if score < good_enough:
            break
    if best is None:
        return None, "decode-fail %dx%d fmt=%d" % (width, height, fmt)

    score, img, delta = best
    if out_png:
        os.makedirs(os.path.dirname(os.path.abspath(out_png)), exist_ok=True)
        img.save(out_png)
    info = "%dx%d fmt=%d(%s) delta=%d smooth=%.1f" % (
        width, height, fmt, TF(fmt).name, delta, score)
    return img, info


class TextureStore:
    """按索引从热点库读并解压贴图对象。"""

    def __init__(self, src, index):
        self.src = src
        if isinstance(index, str):
            with open(index, encoding="utf-8") as f:
                index = json.load(f)
        self.index = index

    def keys(self):
        return sorted(self.index)

    def load(self, key):
        """返回解压后的对象字节；键不存在或不是 LZ4 帧则返回 None。"""
        for off in self.index.get(key, []):
            with open(self.src, "rb") as f:
                f.seek(off)
                blob = f.read(READ_AHEAD)
            if blob[:4] != LZ4_MAGIC:
                continue
            try:
                return lz4f.decompress(blob)
            except Exception:
                dec = lz4f.LZ4FrameDecompressor()
                out = bytearray()
                try:
                    out += dec.decompress(blob)
                except Exception:
                    pass
                if out:
                    return bytes(out)
        return None


def main():
    ap = argparse.ArgumentParser(description="解码 Unity 贴图（修正版）")
    ap.add_argument("keys", nargs="*", help="完整键名，如 utex2.icon_531_edu")
    ap.add_argument("--pat", default="", help="按子串批量匹配键名")
    ap.add_argument("--src", default="raw/_DBreezeResources.bin")
    ap.add_argument("--index", default="tex_keys.json")
    ap.add_argument("--out", default="tex_out")
    args = ap.parse_args()

    store = TextureStore(args.src, args.index)
    keys = args.keys or []
    if args.pat:
        keys = [k for k in store.keys() if args.pat in k]
    if not keys:
        ap.error("请给出键名或 --pat")

    ok = fail = 0
    for key in keys:
        data = store.load(key)
        if data is None:
            print("  x %-40s no-data" % key, flush=True)
            fail += 1
            continue
        name = key.split(".", 1)[1]
        img, info = decode_tex(data, os.path.join(args.out, name + ".png"))
        print("  %s %-40s declen=%-8d %s" % ("v" if img else "x", key, len(data), info), flush=True)
        ok += bool(img)
        fail += not img
    print("\n成功 %d  失败 %d" % (ok, fail), flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
