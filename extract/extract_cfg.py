# -*- coding: utf-8 -*-
"""从热点库中定向提取配置表（cfg.*）

热点库是 DBreeze 单文件库，里面同一张表会有多个历史版本（occ1..occN）。
这里只做一件事：把指定表名的每一份出现都按 LZ4 帧解出来单独落盘，
后续 promote_cfg.py 再挑最新的一份当权威数据。

设计要点：
  - 只扫需要的表名，不枚举全库（全库 needle 扫十几分钟，定向 3 表约 3 秒）
  - 分块扫描 + 128 字节 carry，避免跨块截断把键名切两半
  - 值的起点 = 键名之后紧随的 4 字节 + LZ4 魔数（04 22 4D 18）

用法:
    python extract/extract_cfg.py --src raw/_DBreezeResources.bin --out cfg \
        --tables cfg.mercenarycfgs,cfg.armamentcfgs,cfg.flaircfgs
"""

import argparse
import os
import sys
import time

import lz4.frame

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

LZ4_MAGIC = b"\x04\x22\x4d\x18"
CHUNK = 64 << 20
CARRY = 128


def scan_offsets(path, tables):
    """返回 {table: [offset, ...]}，offset 指向该表名第一次出现的字节位置。"""
    occ = {}
    needles = [(t, t.encode()) for t in tables]
    total = 0
    with open(path, "rb") as f:
        carry = b""
        while True:
            buf = f.read(CHUNK)
            if not buf:
                break
            data = carry + buf
            base = total - len(carry)
            for name, needle in needles:
                start = 0
                while True:
                    i = data.find(needle, start)
                    if i < 0:
                        break
                    occ.setdefault(name, []).append(base + i)
                    start = i + 1
            total += len(buf)
            carry = data[-CARRY:]
    return occ, total


def decompress_frame(blob):
    """解第一帧 LZ4；不是 LZ4 帧则原样返回（部分表是裸数据）。"""
    if blob[:4] != LZ4_MAGIC:
        return blob, False
    try:
        return lz4.frame.decompress(blob), True
    except Exception:
        # 帧被截断 / 尾部混入后续对象：用增量解码器保住已解出的部分
        dec = lz4.frame.LZ4FrameDecompressor()
        out = bytearray()
        try:
            out += dec.decompress(blob)
        except Exception:
            pass
        return bytes(out) or blob, True


def main():
    ap = argparse.ArgumentParser(description="定向提取 DBreeze 热点库中的 cfg 表")
    ap.add_argument("--src", default="raw/_DBreezeResources.bin")
    ap.add_argument("--out", default="cfg")
    ap.add_argument("--tables", default="cfg.mercenarycfgs,cfg.armamentcfgs,cfg.flaircfgs",
                    help="逗号分隔的表名（带 cfg. 前缀）")
    ap.add_argument("--max-window", type=int, default=64 << 20, help="单次读取窗口上限（字节）")
    args = ap.parse_args()

    tables = [t.strip() for t in args.tables.split(",") if t.strip()]
    os.makedirs(args.out, exist_ok=True)

    t0 = time.time()
    occ, total = scan_offsets(args.src, tables)
    print("扫描 %.2f GB  %.0fs" % (total / 1e9, time.time() - t0), flush=True)
    print("命中：%s" % {k: len(v) for k, v in occ.items()}, flush=True)
    if not occ:
        print("!! 一个表都没命中，检查 --tables 表名或源文件是否正确")
        return 1

    points = sorted((off, name) for name, offs in occ.items() for off in offs)
    with open(args.src, "rb") as f:
        for idx, (off, name) in enumerate(points):
            vstart = off + len(name)
            vend = points[idx + 1][0] if idx + 1 < len(points) else vstart + args.max_window
            vend = min(vend, vstart + args.max_window)
            if vend <= vstart:
                vend = vstart + args.max_window
            f.seek(vstart)
            blob = f.read(vend - vstart)

            raw, compressed = decompress_frame(blob)
            short = name.replace("cfg.", "")
            n = sum(1 for o2, n2 in points if n2 == name and o2 <= off)
            out = os.path.join(args.out, "%s.occ%d" % (short, n))
            with open(out, "wb") as fp:
                fp.write(raw)
            print("  %-16s occ%-2d %8.2f MB  lz4=%s" % (short, n, len(raw) / 1048576.0, compressed),
                  flush=True)

    print("DONE %.0fs -> %s" % (time.time() - t0, args.out), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
