# -*- coding: utf-8 -*-
"""按天赋名批量导出天赋图标（256×256 白色线描）

图标键名有两种，都从配置表解析出来，不要靠拼音猜：
  - 新版：utex2.icon_<拼音>          （如 钢铁之躯 → icon_gtzqu）
  - 旧版：utex2.icon_<天赋ID>_<拼音>  （如 恶妒 → icon_531_edu）

权威映射在 flaircfgs 的 Icon 字段（promote_cfg.py 已写进 flairs.json）。
⚠ 实测坑：靠"名字转拼音"猜会张冠李戴——灵力膨胀(ID470) 是 icon_llpz，
  而 icon_496_llpz 是"灵力屏障"。必须用 Icon 字段。

输出：
  <out>/<天赋名>.png    可直接上传到 wiki 的 File:<天赋名>.png
  <out>/../icons_manifest.json   {天赋名: {key, fid, file, info}}

用法:
    python extract/extract_icons.py --src raw/db.bin --index tex_keys.json \
        --flairs cfg/all/flairs.json --out tex_out/icons
"""

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tex_decode import TextureStore, decode_tex  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

LEGACY = re.compile(r"^u?tex2\.icon_(\d+)_(.+)$")


def safe_name(name):
    return name.replace("/", "_").replace("\\", "_").replace(":", "_")


def main():
    ap = argparse.ArgumentParser(description="按配置表 Icon 字段批量导出天赋图标")
    ap.add_argument("--src", default="raw/_DBreezeResources.bin")
    ap.add_argument("--index", default="tex_keys.json")
    ap.add_argument("--flairs", default="cfg/all/flairs.json")
    ap.add_argument("--out", default="tex_out/icons")
    ap.add_argument("--manifest", default="", help="清单输出路径（默认 <out>/../icons_manifest.json）")
    args = ap.parse_args()

    with open(args.flairs, encoding="utf-8") as f:
        flairs = json.load(f)
    store = TextureStore(args.src, args.index)
    index_keys = set(store.index)

    os.makedirs(args.out, exist_ok=True)
    manifest_path = args.manifest or os.path.join(os.path.dirname(os.path.abspath(args.out)),
                                                  "icons_manifest.json")

    manifest, missing_key, no_icon, decode_fail = {}, [], [], []
    for fid, item in sorted(flairs.items()):
        name = (item.get("Name") or "").strip()
        if not name:
            continue
        icon = (item.get("Icon") or "").strip()
        if not icon:
            # 兜底：老命名 icon_<ID>_<拼音>
            cand = [k for k in index_keys if LEGACY.match(k) and LEGACY.match(k).group(1) == str(fid)]
            if not cand:
                no_icon.append(name)
                continue
            key = cand[0]
        else:
            key = "utex2." + icon
            if key not in index_keys:
                missing_key.append((name, key))
                continue

        data = store.load(key)
        if data is None:
            decode_fail.append((name, key, "no-data"))
            continue
        path = os.path.join(args.out, safe_name(name) + ".png")
        img, info = decode_tex(data, path)
        if img is None:
            decode_fail.append((name, key, info))
            continue
        manifest[name] = {"key": key, "fid": int(fid), "file": path, "info": info}

    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)

    print("\n导出 %d 张 -> %s" % (len(manifest), args.out))
    print("配置有 Icon 但资源库无此键（配置残留）: %d" % len(missing_key))
    for name, key in missing_key[:10]:
        print("  ! %-10s %s" % (name, key))
    print("无 Icon 字段且无老命名可匹配: %d  %s" % (len(no_icon), no_icon[:8]))
    print("解码失败: %d  %s" % (len(decode_fail), decode_fail[:5]))
    print("清单: %s" % manifest_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
