# -*- coding: utf-8 -*-
"""把提取出来的 cfg 表"提级"为权威数据，并解析成 JSON

做两件事：
  1. 每张表取 occ 编号最大的一份（最新版本）复制为 <table>.occ2，旧版备份为 .occ2_old
  2. 解析 flaircfgs（天赋表）→ flairs.json，字段：Name / Icon / TxtIcon / Desc

字段解析的坑（DBreeze 的 BSON 子集）：
  - 记录以 $k 标记开头：b'\\x19\\x01' + u32(2) + u16('$k') + u32(主键)
  - 字段名带长度前缀：u32(len) + utf16(name)
  - 字段值紧跟一个 0x01 类型字节，然后是 u32(len) + utf16(value)
  - 所以按"长度前缀 + 字段名"定位到 p 后，值在 p + len(tag) 处（不是 p + 4）

用法:
    python extract/promote_cfg.py --src cfg --dst cfg/all \
        --tables mercenarycfgs,armamentcfgs,flaircfgs
"""

import argparse
import json
import os
import re
import shutil
import struct
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

KMARK = b"\x19\x01" + (2).to_bytes(4, "little") + b"$\x00k\x00"
FLAIR_FIELDS = ["Name", "Icon", "TxtIcon", "Desc"]


def read_str(buf, off, max_len=4000):
    """读取 0x01 类型标记后的字符串。off 不对时返回 None。"""
    if off + 5 > len(buf) or buf[off] != 1:
        return None
    n = struct.unpack("<I", buf[off + 1:off + 5])[0]
    if n > max_len or off + 5 + n * 2 > len(buf):
        return None
    return buf[off + 5:off + 5 + n * 2].decode("utf-16le", "replace")


def parse_flairs(path):
    data = open(path, "rb").read()
    kpos = [(m.start(), m.end()) for m in re.finditer(re.escape(KMARK), data)]
    out = {}
    for idx, (_start, end) in enumerate(kpos):
        key = struct.unpack("<I", data[end:end + 4])[0]
        seg_end = kpos[idx + 1][0] if idx + 1 < len(kpos) else len(data)
        seg = data[end:seg_end]
        item = {"Id": key}
        for field in FLAIR_FIELDS:
            tag = len(field).to_bytes(4, "little") + field.encode("utf-16le")
            p = seg.find(tag)
            if p < 0:
                continue
            value = read_str(seg, p + len(tag), max_len=4000 if field == "Desc" else 200)
            if value:
                item[field] = value
        if item.get("Name"):
            out[key] = item
    return out, len(kpos)


def latest_occ(src_dir, table):
    occs = []
    for name in os.listdir(src_dir):
        m = re.match(re.escape(table) + r"\.occ(\d+)$", name)
        if m:
            occs.append(int(m.group(1)))
    if not occs:
        return None, None
    latest = max(occs)
    return latest, os.path.join(src_dir, "%s.occ%d" % (table, latest))


def main():
    ap = argparse.ArgumentParser(description="提级最新 cfg 表并解析 flaircfgs")
    ap.add_argument("--src", default="cfg", help="extract_cfg.py 的输出目录")
    ap.add_argument("--dst", default="cfg/all", help="权威数据目录")
    ap.add_argument("--tables", default="mercenarycfgs,armamentcfgs,flaircfgs")
    ap.add_argument("--anchor", default="", help="逗号分隔的自检天赋名，用于确认解析成功")
    args = ap.parse_args()

    os.makedirs(args.dst, exist_ok=True)
    for table in [t.strip() for t in args.tables.split(",") if t.strip()]:
        latest, src = latest_occ(args.src, table)
        if not src:
            print("!! %-14s 没有找到任何 occN" % table, flush=True)
            continue
        dst = os.path.join(args.dst, table + ".occ2")
        if os.path.exists(dst):
            bak = os.path.join(args.dst, table + ".occ2_old")
            shutil.copy2(dst, bak)
        shutil.copy2(src, dst)
        print("%-14s occ%d (%d 字节) -> %s" % (table, latest, os.path.getsize(src), dst), flush=True)

    flair_path = os.path.join(args.dst, "flaircfgs.occ2")
    if not os.path.exists(flair_path):
        print("!! 缺少 flaircfgs.occ2，跳过天赋表解析", flush=True)
        return 1

    flairs, records = parse_flairs(flair_path)
    out_path = os.path.join(args.dst, "flairs.json")
    old = out_path.replace(".json", "_old.json")
    if os.path.exists(out_path):
        shutil.copy2(out_path, old)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(flairs, f, ensure_ascii=False, indent=1)

    with_icon = sum(1 for v in flairs.values() if v.get("Icon"))
    print("\nflaircfgs: $k 记录 %d 条，解析出 %d 条，其中带 Icon 字段 %d 条"
          % (records, len(flairs), with_icon), flush=True)
    print("已写出 %s" % out_path, flush=True)

    anchors = [a.strip() for a in args.anchor.split(",") if a.strip()]
    if anchors:
        print("\n--- 自检 ---", flush=True)
        for name in anchors:
            hits = [(k, v.get("Icon")) for k, v in flairs.items() if v.get("Name") == name]
            print("  %-8s %s" % (name, hits[:4] if hits else "!! 未命中"), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
