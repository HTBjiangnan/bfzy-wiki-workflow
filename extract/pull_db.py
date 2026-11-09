# -*- coding: utf-8 -*-
"""拉取游戏热点库 _DBreezeResources 到本地

用法:
    python extract/pull_db.py --out raw/_DBreezeResources.bin
    python extract/pull_db.py --out raw/db.bin --port 16384

先确认模拟器已启动、adb 端口通:
    python extract/adb.py 16384 "ls /sdcard/Android/data/com.woowaygames.onepercent/files/Assets/Database"
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import adb  # noqa: E402

DEFAULT_REMOTE = ("/sdcard/Android/data/com.woowaygames.onepercent/files/"
                  "Assets/Database/_DBreezeResources")


def main():
    ap = argparse.ArgumentParser(description="流式拉取游戏热点库（DBreeze 单文件数据库）")
    ap.add_argument("--remote", default=DEFAULT_REMOTE, help="设备上的数据库路径")
    ap.add_argument("--out", default="raw/_DBreezeResources.bin", help="本地保存路径")
    ap.add_argument("--port", type=int, default=adb.DEFAULT_PORT, help="adb 端口（MuMu 实例 0 = 16384）")
    ap.add_argument("--host", default=adb.DEFAULT_HOST)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    remote_size = adb.shell("stat -c %s " + args.remote, port=args.port, host=args.host).strip()
    if remote_size.isdigit():
        print("设备端大小: %.2f GB" % (int(remote_size) / 1e9), flush=True)
    else:
        print("读不到设备端大小（继续尝试拉取）: %r" % remote_size, flush=True)

    n, elapsed = adb.pull_stream(args.remote, args.out, port=args.port,
                                 host=args.host, quiet=args.quiet)
    print("DONE %s -> %s  %.2f GB  %.1fs  %.1f MB/s"
          % (args.remote, args.out, n / 1e9, elapsed, n / 1048576.0 / max(0.01, elapsed)),
          flush=True)

    if remote_size.isdigit() and int(remote_size) != n:
        print("!! 大小不一致（设备 %s / 本地 %d），建议重拉" % (remote_size, n), flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
