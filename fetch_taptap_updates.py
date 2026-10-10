# -*- coding: utf-8 -*-
"""
fetch_taptap_updates.py — 百分之一 TapTap 官方更新公告抓取脚本（可重复运行）

功能：
  1. 拉取 TapTap 官方公告话题（group_id=60973, type=official）的帖子列表
  2. 只处理标题含「更新说明」的帖子（自动跳过「猜猜我属啥」等活动帖）
  3. 从公告文本解析：卡池变更、新卡（属性/能量/形态/技能/天赋）、超弦属性预告
  4. 三层去重：
     - vs Wiki：卡名与 data/characters.json 比对（* 与 · 统一），标记 in_wiki
     - 公告间：同一张卡多期出现时合并为一条，保留最新最全信息
     - 自身增量：记录已处理的 moment id，重跑不重复
  5. 输出 data/taptap_updates.json

用法：
  python fetch_taptap_updates.py                # 增量抓取（起点=已有数据/Wiki更新时间）
  python fetch_taptap_updates.py --since 2026-01-01
  python fetch_taptap_updates.py --full         # 忽略已有moment记录，全量重抓
"""
import requests, re, json, time, argparse, io, sys, os
from datetime import datetime, timezone

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
OUT_JSON = os.path.join(DATA, "taptap_updates.json")
WIKI_JSON = os.path.join(DATA, "characters.json")

GROUP_ID = "60973"          # 百分之一官方公告话题组
APP_ID = "78938"            # 百分之一
FEED_URL = "https://www.taptap.cn/webapiv2/feed/v7/by-group"
DETAIL_URL = "https://www.taptap.cn/webapiv2/moment/v3/detail"
MOMENT_URL = "https://www.taptap.cn/moment/{}"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0",
    "Referer": f"https://www.taptap.cn/app/{APP_ID}/topic",
}
XUA = ("V=1&PN=WebApp&LANG=zh_CN&VN_CODE=100000000&LOC=CN&PLT=PC&DS=PC"
       "&UID=&OS=Windows&OSV=10&DT=PC")

ATTRS = ["冰", "火", "雷", "毒", "光", "厄"]
FORMS = ["人龙型", "机械型", "类兽型", "龙型", "人型"]


# ---------------------------------------------------------------- requests
def make_session():
    s = requests.Session()
    s.trust_env = False          # 绕过系统代理直连
    return s


def get_json(s, url, params, tries=4):
    for k in range(tries):
        try:
            r = s.get(url, params=params, headers=HEADERS, timeout=30)
            if r.status_code == 200:
                return r.json()
            print(f"  HTTP {r.status_code}, retry {k+1}")
        except Exception as e:
            print(f"  {str(e)[:80]}, retry {k+1}")
        time.sleep(2 * (k + 1))
    return None


# ---------------------------------------------------------------- feed 翻页
def iter_feed(s, since_epoch, max_pages=400):
    """按 created 降序翻页，返回 (moment_id, created_time, title_guess) 流，
    直到某页全部帖子早于 since_epoch 为止。"""
    frm, page = 0, 0
    while page < max_pages:
        params = {"X-UA": XUA, "__times": "0", "group_id": GROUP_ID, "limit": "10",
                  "sort": "created", "status": "0", "type": "official",
                  "with_hot_comment": "false", "from": str(frm)}
        j = get_json(s, FEED_URL, params)
        if not j or not j.get("success", True):
            print("  feed request failed, stop."); break
        lst = j.get("data", {}).get("list", [])
        if not lst:
            break
        stop = True
        for item in lst:
            m = item.get("moment", {})
            mid = m.get("id_str")
            ct = m.get("created_time", 0)
            if ct >= since_epoch:
                stop = False
                yield mid, ct
        if stop:
            break
        frm += len(lst)
        page += 1
        time.sleep(0.4)


def fetch_detail(s, mid):
    """拉取帖子详情。返回 (title, text, images, reveal_images)
    images: 正文全部图片URL；reveal_images: 「猜中与中奖如下」之后的图片（开奖截图）"""
    j = get_json(s, DETAIL_URL, {"X-UA": XUA, "id": mid})
    if not j:
        return None, None, [], []
    d = j.get("data", {})
    title = (d.get("moment", {}).get("seo", {}) or {}).get("title", "")
    title = re.sub(r"\s*-\s*百分之一\s*-\s*TapTap\s*$", "", title)
    blocks = d.get("first_post", {}).get("contents", {}).get("json", [])
    lines, images, reveal_imgs, revealing = [], [], [], False
    for b in blocks:
        if b.get("type") == "paragraph":
            text = "".join(ch.get("text", "") for ch in b.get("children", []))
            lines.append(text.strip())
            # 开奖图触发：正文「猜中与中奖如下：」之后（注意不能匹配活动说明里的「揭晓」字样）
            if ("猜中" in text and "如下" in text) or "中奖名单" in text:
                revealing = True
        elif b.get("type") == "image":
            u = ((b.get("info") or {}).get("img_url") or "")
            if u:
                images.append(u)
                if revealing:
                    reveal_imgs.append(u)
    return title, "\n".join(lines), images, reveal_imgs


# ---------------------------------------------------------------- 公告解析
def norm_name(name):
    """统一分隔符 * / · ，去空白，用于去重匹配"""
    return re.sub(r"\s+", "", str(name)).replace("*", "·").replace("＊", "·")


def parse_pool_changes(text):
    """卡池变更：X号50抽卡池改为“卡名”（容忍 2.1号 等编号前缀）"""
    out = []
    for m in re.finditer(r"(?:\d+\.)?(\d+)号(\d+)抽卡池改为[“\"‘']?([^“”\"’'\n]+?)[”\"’']?(?=\n|$)", text):
        out.append({"pool_day": m.group(1), "pool_size": m.group(2) + "抽", "card": m.group(3).strip()})
    return out


def parse_next_superstring(text):
    m = re.search(r"下周超弦属性[:：]\s*([冰火雷毒光厄/、\s]+)", text)
    return m.group(1).strip() if m else ""


ENERGY_LINE = re.compile(r"(?:([冰火雷毒光厄]{1,3}(?:[/、][冰火雷毒光厄])+)?[,，]?\s*能量\s*(\d+)\s*[,，]?\s*(机械型|人龙型|类兽型|龙型|人型)?|(机械型|人龙型|类兽型|龙型|人型)\s*[,，]?\s*能量\s*(\d+))")
TALENT_LINE = re.compile(r"^(.{1,24}?)[（(](?:ID[:：]\s*)?(\d{1,4})[）)][:：]\s*(.+)$")
SKILL_LINE = re.compile(r"^(手动选择|手动指定|自动选择|自动指定)[:：,，]\s*(.+)$", re.S)
HIDDEN_NOTE = re.compile("配合每周猜属性|隐藏对应属性|周一下午更新")
SECTION_HEADING = re.compile(r"^\d+\s*\.\s*\D")   # 「6.每月门将活动更新」等栏目标题


def parse_energy_line(text):
    """通用能量行解析，兼容属性/能量/形态的任意顺序与组合：
    「冰/厄，能量14」「能量14，机械型」「残骸装甲*傀儡姬 能量20，机械型，冰/火/雷」
    返回 (energy, form, elements)"""
    em = re.search(r"能量\s*(\d+)", text)
    fm = re.search(r"(人龙型|机械型|类兽型|龙型|人型)", text)
    elm = re.search(r"([冰火雷毒光厄](?:\s*[/、]\s*[冰火雷毒光厄])+)", text)
    elements = [e for e in re.split(r"[/、]", elm.group(1).replace(" ", ""))] if elm else []
    return (em.group(1) if em else "",
            fm.group(1) if fm else "",
            elements)


def parse_cards(text, pool_cards):
    """从公告正文提取新卡块。返回 [{name, elements, energy, form, skill, talents, attr_hidden}]"""
    lines = [l.strip() for l in text.split("\n")]
    cards = []
    seen = set()
    POOL_LINE = re.compile(r"(?:\d+\.)?(\d+)号(\d+)抽卡池改为[“\"‘']?([^“”\"’'\n]+?)[”\"’']?$")

    def collect_block(start_j, energy_text, hidden):
        """从 start_j 开始收集技能/天赋行，返回 (card_dict, next_i)"""
        energy, form, elements = parse_energy_line(energy_text)
        skill, talents = "", []
        j = start_j
        while j < len(lines):
            l2 = lines[j]
            if not l2:
                j += 1; continue
            if HIDDEN_NOTE.search(l2):
                hidden = True; j += 1; continue
            sm = SKILL_LINE.match(l2)
            if sm:
                skill = f"{sm.group(1)}：{sm.group(2).strip()}"; j += 1; continue
            tm = TALENT_LINE.match(l2)
            if tm:
                talents.append({"name": tm.group(1).strip(), "id": tm.group(2),
                                "desc": tm.group(3).strip()}); j += 1; continue
            break
        return {"elements": elements, "energy": energy, "form": form, "skill": skill,
                "talents": talents, "attr_hidden": hidden}, j

    def _is_junk(name):
        """过滤非新卡条目：编号列表项（门将活动/老卡强化等）"""
        return bool(re.match(r"^\d+\.", name)) or ("活动更新" in name) or ("老卡强化" in name)

    i = 0
    while i < len(lines):
        line = lines[i]
        # 路径A：卡池变更行 → 隐藏说明(可选) → 能量行
        pm = POOL_LINE.match(line)
        if pm:
            pool_name = pm.group(3).strip()
            j, hidden = i + 1, False
            while j < len(lines) and (not lines[j] or HIDDEN_NOTE.search(lines[j])):
                if HIDDEN_NOTE.search(lines[j]):
                    hidden = True
                j += 1
            if j < len(lines):
                em = ENERGY_LINE.search(lines[j])
                if em:
                    blk, ni = collect_block(j + 1, lines[j], hidden)
                    key = norm_name(pool_name)
                    if key not in seen:
                        seen.add(key)
                        blk.update({"name": pool_name, "pool": pm.group(2) + "抽"})
                        cards.append(blk)
                    i = ni
                    continue
        # 路径B0：卡名与能量同一行 → 「残骸装甲*傀儡姬 能量20，机械型，冰/火/雷」
        if line and not line.startswith(("手动", "自动")) and not HIDDEN_NOTE.search(line) \
                and not SECTION_HEADING.match(line) and "抽卡池" not in line:
            em_self = re.search(r"能量\s*\d+", line)
            if em_self and em_self.start() > 0:
                name = re.sub(r"[“”\"]", "", line[:em_self.start()]).strip()
                if name:
                    blk, ni = collect_block(i + 1, line, False)
                    key = norm_name(name)
                    if key not in seen:
                        seen.add(key)
                        blk.update({"name": name, "pool": next(
                            (p["pool_size"] for p in pool_cards
                             if norm_name(p["card"]) == key), "")})
                        cards.append(blk)
                    i = ni
                    continue
        # 路径B：独立卡名行 → 能量行（栏目标题行跳过，其后的卡片行会被单独处理）
        if SECTION_HEADING.match(line):
            i += 1
            continue
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        if (line and not line.startswith(("手动", "自动"))
                and not HIDDEN_NOTE.search(line) and "抽卡池" not in line
                and "打造图" not in line and ENERGY_LINE.search(nxt)):
            name = re.sub(r"[“”\"]", "", line).strip()
            key = norm_name(name)
            if key not in seen:
                blk, ni = collect_block(i + 2, nxt, False)
                seen.add(key)
                blk.update({"name": name, "pool": next(
                    (p["pool_size"] for p in pool_cards
                     if norm_name(p["card"]) == key), "")})
                cards.append(blk)
                i = ni
                continue
        i += 1
    return cards


def parse_announcement(title, text, mid, created, ann_type="update"):
    pool = parse_pool_changes(text)
    return {
        "moment_id": mid,
        "url": MOMENT_URL.format(mid),
        "title": title,
        "type": ann_type,       # update=更新说明 / guess=猜猜我属啥（属性竞猜）
        "date": datetime.fromtimestamp(created, tz=timezone.utc).astimezone(
            timezone.utc).strftime("%Y-%m-%d") if created else "",
        "created_time": created,
        "pool_changes": pool,
        "next_superstring_attr": parse_next_superstring(text),
        "raw_text": text,
    }


# ---------------------------------------------------------------- 去重与合并
def load_wiki_names():
    if not os.path.exists(WIKI_JSON):
        print("!! 未找到 characters.json，vs-Wiki 去重将不可用")
        return set()
    d = json.load(open(WIKI_JSON, encoding="utf-8"))
    return {norm_name(c.get("name", "")) for c in d.get("characters", [])}


def merge_cards(old_cards, new_cards, wiki_names):
    """按 norm_name 合并：新数据字段更全则覆盖，保留出现日期列表"""
    by_key = {}
    for c in old_cards:
        by_key.setdefault(norm_name(c["name"]), c)
    for c in new_cards:
        key = norm_name(c["name"])
        c["in_wiki"] = key in wiki_names
        if key in by_key:
            prev = by_key[key]
            dates = sorted(set(prev.get("seen_dates", []) + ([c.get("first_date", "")] if c.get("first_date") else [])))
            merged = dict(prev)
            for f in ("elements", "energy", "form", "skill", "talents", "pool"):
                if c.get(f) and not prev.get(f):
                    merged[f] = c[f]
            # 隐藏属性公开后清除标记
            if prev.get("attr_hidden") and not c.get("attr_hidden") and c.get("elements"):
                merged["attr_hidden"] = False
                merged["elements"] = c["elements"] or merged.get("elements", [])
            merged["seen_dates"] = dates
            if c.get("attr_hidden") is not None and not c["attr_hidden"]:
                merged["attr_hidden"] = False
            by_key[key] = merged
        else:
            c["seen_dates"] = [c.get("first_date", "")] if c.get("first_date") else []
            by_key[key] = c
    return list(by_key.values())


# ---------------------------------------------------------------- 属性回填（OCR开奖截图）
_OCR = None
ATTR_PAIR = re.compile(r"[冰火雷毒光厄][冰火雷毒光厄]")

def get_ocr():
    """惰性加载 RapidOCR（首次调用会下载/加载模型，需 rapidocr-onnxruntime）"""
    global _OCR
    if _OCR is None:
        try:
            from rapidocr_onnxruntime import RapidOCR
            _OCR = RapidOCR()
        except ImportError:
            print("!! 未安装 rapidocr-onnxruntime，跳过属性识别"
                  "（pip install rapidocr-onnxruntime）")
            _OCR = False
    return _OCR or None


def ocr_reveal_attrs(image_urls):
    """对开奖截图OCR，提取属性组合（如「冰雷」）取众数（≥2次才可信）。
    原理：开奖截图展示的全部是猜中玩家的回复，其回复内容即正确属性。
    返回属性列表如 ['冰','雷']，失败返回 []"""
    ocr = get_ocr()
    if not ocr or not image_urls:
        return []
    from collections import Counter
    import tempfile
    s = make_session()
    counter = Counter()
    for u in image_urls:
        try:
            r = s.get(u, headers=HEADERS, timeout=30)
            if r.status_code != 200 or len(r.content) < 1000:
                continue
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
                f.write(r.content)
                tmp = f.name
            result, _ = ocr(tmp)
            os.unlink(tmp)
            for item in (result or []):
                for m in ATTR_PAIR.finditer(str(item[1])):
                    counter[m.group()] += 1
        except Exception as e:
            print(f"  OCR fail: {str(e)[:70]}")
    if counter:
        top, n = counter.most_common(1)[0]
        # 众数≥2次可信；或全部识别结果仅一种属性组合（单中奖者截图）也可接受
        if n >= 2 or len(counter) == 1:
            return list(top)
    return []


def apply_reveals(db):
    """用猜属性帖的开奖截图回填隐藏卡属性。
    匹配规则：隐藏卡发布日期 ∈ [猜帖发布日, 开奖日]（开奖日取自开奖图URL时间戳），
    区间内隐藏卡唯一时自动回填，多张时提示人工确认。"""
    hidden = [c for c in db.get("cards", []) if c.get("attr_hidden")]
    if not hidden:
        print("无待回填的隐藏属性卡")
        return
    changed = 0
    for a in db.get("announcements", []):
        if a.get("type") != "guess" or a.get("revealed_attrs"):
            continue
        reveal_imgs = a.get("reveal_images") or []
        if not reveal_imgs:
            a["revealed_attrs"] = []
            a["reveal_status"] = "尚未揭晓"
            continue
        attrs = ocr_reveal_attrs(reveal_imgs)
        a["revealed_attrs"] = attrs
        if not attrs:
            a["reveal_status"] = "OCR未识别出答案"
            print(f"  ? {a['title'][:40]} 开奖图OCR无结果，请人工确认")
            continue
        a["reveal_status"] = "ok"
        # 开奖日取 reveal_images 中最晚的图内时间戳（开奖图上传时间）
        reveal_date = ""
        for u in reveal_imgs:
            for mm in re.finditer(r"_(\d{8})\d{6}\.", u):
                d = mm.group(1)
                if d > reveal_date.replace("-", ""):
                    reveal_date = f"{d[:4]}-{d[4:6]}-{d[6:]}"
        gdate = a.get("date") or ""
        cands = [c for c in hidden if c.get("attr_hidden")
                 and gdate <= (c.get("first_date") or "") <= (reveal_date or "9999-99-99")]
        if len(cands) == 1:
            c = cands[0]
            c["elements"] = list(attrs)
            c["attr_hidden"] = False
            c["revealed_by"] = a.get("url", "")
            hidden.remove(c)
            changed += 1
            print(f"  ✦ {c['name']} 属性公开: {'/'.join(attrs)}  ← {a['title'][:36]}")
        elif len(cands) > 1:
            print(f"  ! {a['title'][:40]} 开奖{'/'.join(attrs)} 对应{len(cands)}张隐藏卡，需人工确认:")
            for c in cands:
                print(f"      - {c['name']} ({c.get('first_date')})")
        else:
            print(f"  ! {a['title'][:40]} 开奖{'/'.join(attrs)} 未匹配到隐藏卡")
    print(f"属性回填完成: {changed} 张")


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="百分之一 TapTap 更新公告抓取")
    ap.add_argument("--since", help="起始日期 YYYY-MM-DD（默认：Wiki最后更新日）")
    ap.add_argument("--full", action="store_true", help="忽略已有数据全量重抓")
    args = ap.parse_args()

    os.makedirs(DATA, exist_ok=True)

    # 起始时间
    since = None
    if args.since:
        since = args.since
    else:
        if os.path.exists(WIKI_JSON):
            t = json.load(open(WIKI_JSON, encoding="utf-8")).get("meta", {}).get("wiki_last_edit", "")
            if t:
                since = t[:10]
        if not since:
            since = "2025-08-28"
    since_epoch = int(datetime.strptime(since, "%Y-%m-%d").replace(
        tzinfo=timezone.utc).timestamp())
    print(f"抓取起点: {since}")

    # 已有数据（增量）
    db = {"meta": {}, "announcements": [], "cards": []}
    seen_ids = set()
    if os.path.exists(OUT_JSON) and not args.full:
        try:
            db = json.load(open(OUT_JSON, encoding="utf-8"))
            # seen_ids: 已检查过详情的全部帖子（含猜属性帖、活动帖），避免重复请求
            seen_ids = set(db.get("meta", {}).get("seen_ids", []))
            if not seen_ids:   # 旧版数据无seen_ids，退化为已入库公告id
                seen_ids = {a["moment_id"] for a in db.get("announcements", [])}
            print(f"已有公告 {len(db.get('announcements', []))} 条 / 已见帖 {len(seen_ids)}，增量模式")
        except Exception:
            print("!! 已有JSON损坏，重新抓取")

    wiki_names = load_wiki_names()
    s = make_session()

    new_anns, new_cards = [], []
    checked = 0
    for mid, ct in iter_feed(s, since_epoch):
        if mid in seen_ids:
            continue
        seen_ids.add(mid)
        checked += 1
        title, text, images, reveal_imgs = fetch_detail(s, mid)
        time.sleep(1.5)
        if not title:
            continue
        if "更新说明" in title:
            print(f"  ✔ {title} ({datetime.fromtimestamp(ct, tz=timezone.utc):%Y-%m-%d})")
            ann = parse_announcement(title, text, mid, ct, "update")
            cards = parse_cards(text, ann["pool_changes"])
            for c in cards:
                c["first_date"] = ann["date"]
                c["source_url"] = ann["url"]
            ann["cards_extracted"] = [c["name"] for c in cards]
            new_anns.append(ann)
            new_cards.extend(cards)
        elif "猜" in title and "属" in title:
            # 猜属性帖：原文+图片入库；开奖截图（reveal_images）供OCR识别属性
            print(f"  ⭐ {title} ({datetime.fromtimestamp(ct, tz=timezone.utc):%Y-%m-%d})"
                  + (f" [开奖图x{len(reveal_imgs)}]" if reveal_imgs else " [未揭晓]"))
            ann = parse_announcement(title, text, mid, ct, "guess")
            ann["cards_extracted"] = []
            ann["images"] = images
            ann["reveal_images"] = reveal_imgs
            new_anns.append(ann)
        # 其他活动帖跳过

    # 合并入库
    db["announcements"] = sorted(db.get("announcements", []) + new_anns,
                                 key=lambda a: a.get("created_time", 0), reverse=True)
    db["cards"] = merge_cards(db.get("cards", []), new_cards, wiki_names)

    # 猜属性帖开奖截图 → 回填隐藏卡属性
    apply_reveals(db)

    db["meta"] = {
        "source": f"https://www.taptap.cn/app/{APP_ID}/topic?os=pc&type=official",
        "since": since,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "announcement_count": len(db["announcements"]),
        "card_count": len(db["cards"]),
        "seen_ids": sorted(seen_ids),
    }
    json.dump(db, open(OUT_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\n完成：本次新处理公告 {len(new_anns)} 条，提取新卡 {len(new_cards)} 张")
    print(f"累计：公告 {db['meta']['announcement_count']} 条 / 卡片 {db['meta']['card_count']} 张")
    print(f"输出: {OUT_JSON}")


if __name__ == "__main__":
    main()
