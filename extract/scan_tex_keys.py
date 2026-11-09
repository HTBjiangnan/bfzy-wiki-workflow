# -*- coding: utf-8 -*-
"""扫描热点库，建立"贴图键名 → 字节偏移"索引

为什么需要：纹理解码必须先知道数据在文件里的位置。
DBreeze 把键名（如 utex2.icon_531_edu）明文写在值前面，
键名之后紧跟 4 字节 + LZ4 魔数 04 22 4D 18，这就是该贴图数据的起点。

键名前缀的三种形态（本游戏实测）：
  tex2.*    / utex2.*   → UI、立绘、天赋图标
  glutex2.*             → 另一批（有些版本才有）
  utex2.icon_<天赋ID>_<拼音>   → 天赋图标（256×256 白色线描）
  utex2.icon_<拼音>            → 天赋图标（新版命名，无 ID）＋功能图标
  utex2.tt_talent_<拼音>       → 另一套天赋图，与 icon_* 零重叠，一般不用

用法:
    python extract/scan_tex_keys.py --src raw/_DBreezeResources.bin --out tex_keys.json
"""

import argparse
import collections
import json
import re
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

LZ4_MAGIC = b"\x04\x22\x4d\x18"
KEY_PATTERN = re.compile(rb"(?:u?tex2|glutex2)\.([A-Za-z0-9_]{2,64})")
CHUNK = 32 << 20
CARRY = 128


def scan(path, chunk=CHUNK):
    found = {}
    total = 0
    carry = b""
    with open(path, "rb") as f:
        while True:
            buf = f.read(chunk)
            if not buf:
                break
            data = carry + buf
            base = total - len(carry)
            for m in KEY_PATTERN.finditer(data):
                if data[m.end():m.end() + 4] == LZ4_MAGIC:
                    key = m.group(0).decode("utf-8", "replace")
                    found.setdefault(key, []).append(base + m.end())
            total += len(buf)
            carry = data[-CARRY:]
            print("%.2f GB  键 %d" % (total / 1e9, len(found)), flush=True)
    return found, total


def main():
    ap = argparse.ArgumentParser(description="建立贴图键名 → 偏移索引")
    ap.add_argument("--src", default="raw/_DBreezeResources.bin")
    ap.add_argument("--out", default="tex_keys.json")
    ap.add_argument("--chunk-mb", type=int, default=32)
    args = ap.parse_args()

    t0 = time.time()
    found, total = scan(args.src, args.chunk_mb << 20)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(found, f, ensure_ascii=False)
    print("完成：文件 %.2f GB，键 %d 个，用时 %.0fs -> %s"
          % (total / 1e9, len(found), time.time() - t0, args.out), flush=True)

    prefix = collections.Counter()
    for key in found:
        body = key.split(".", 1)[1]
        m = re.match(r"[a-z]+", body)
        prefix[m.group(0) if m else body[:6]] += 1
    print("\n--- 键名前缀 TOP 20 ---")
    for name, count in prefix.most_common(20):
        print("  %-16s %d" % (name, count))
    return 0


if __name__ == "__main__":
    sys.exit(main())
