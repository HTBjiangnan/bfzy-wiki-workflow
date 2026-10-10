# -*- coding: utf-8 -*-
"""Wiki发布准备 Step3：生成 {{角色}} 模板 wikitext（153张新页面）
输入: data/taptap_updates.json + data/gamekee_cards.json + _wiki_candidates.json
输出: _wiki_pages.json（上传用）/ _wiki_preview.txt（人工预览）/ _wiki_upload_list.csv
风格参照页面: 临界次元斩·阿加雷斯（pageid=488）
- 关键词用 {{效果|X}} 包裹（43词表，长词优先）
- 公式 (灵力+N) → 半角+灰字 font
- createonly 上传，绝不覆盖已有页"""
import json, re, csv, sys, time

sys.stdout.reconfigure(encoding="utf-8")

# ---- 效果词表（来源: 模板:效果 #switch 全部case，长词优先匹配防拆分）----
EFFECT_WORDS = [
    "燃烧宝石", "冻结宝石", "匹配宝石", "惩戒石板", "祝福石板", "四元素宝石",
    "双星宝石", "封灵宝石", "炸弹宝石", "纯净冰结晶宝石", "纯净火结晶宝石",
    "纯净毒结晶宝石", "纯净雷结晶宝石", "纯净光结晶宝石", "纯净厄结晶宝石",
    "灵能潮汐", "能量爆散",
    "燃烧", "冻结", "中毒", "流血", "沉默", "虚弱", "恐惧", "混乱",
    "灵蚀", "晕眩", "卸甲", "封灵",
    "灵隐", "狂暴", "充能", "护盾", "神恩",
    "诅咒", "净化", "魅惑", "吞灭", "召唤",
    "溅射", "散射", "摧毁", "爆炸",
]
EFFECT_WORDS.sort(key=len, reverse=True)

# ---- 技能指定前缀 ----
ASSIGN_RE = re.compile(r"^(手动指定|自动指定|手动选择|自动选择)[:：]\s*")

# ---- 公式括号（全/半角，内容≤10字符且为公式特征）----
FORMULA_RE = re.compile(r"([（(])([^（）()]{1,10})([)）])")
FORMULA_INNER = re.compile(r"^[\d\s+\-*/.~×÷灵力护甲生值命]+$")


def wrap_effects(text):
    """效果词→{{效果|X}}，占位符防重入"""
    if not text:
        return text
    tokens = {}
    for i, w in enumerate(EFFECT_WORDS):
        ph = f"\x00{i}\x00"
        tokens[ph] = w
        text = text.replace(w, ph)
    for ph, w in tokens.items():
        text = text.replace(ph, f"{{{{效果|{w}}}}}")
    return text


def formula_font(text):
    """（灵力+5）→ <font color=#99999A>(灵力+5)</font>；条件长括号不动"""
    def rep(m):
        inner = m.group(2)
        if FORMULA_INNER.match(inner) and ("灵力" in inner or re.search(r"\d", inner)):
            return f'<font color=#99999A>({inner})</font>'
        return m.group(0)
    return FORMULA_RE.sub(rep, text)


def extract_effects(text):
    """从已包裹文本提取效果词列表（保序去重）"""
    out = []
    for w in re.findall(r"\{\{效果\|([^}]+)\}\}", text or ""):
        if w not in out:
            out.append(w)
    return out


def process_skill_text(raw):
    """技能/天赋文本 → (指定方式, Wiki风格说明)"""
    if not raw:
        return "", ""
    txt = raw.strip()
    assign = ""
    m = ASSIGN_RE.match(txt)
    if m:
        assign = m.group(1)
        txt = txt[m.end():]
    txt = formula_font(txt)
    txt = wrap_effects(txt)
    return assign, txt


def join_tags(words):
    return "、".join(words)


def build_wikitext(c, src):
    """单卡 → {{角色|...}} wikitext"""
    name = c["name"]
    # 技能文本来源：gk用wiki_skill（关键词版），ann用skill（公告原文）
    raw_skill = c.get("wiki_skill") if src == "gk" else c.get("skill", "")
    raw_skill = raw_skill or (c.get("skill", "") if src == "gk" else "")
    assign, skill_txt = process_skill_text(raw_skill)
    skill_effects = extract_effects(skill_txt)

    # 天赋
    talents = (c.get("wiki_talents") if src == "gk" else c.get("talents")) or []
    tal_names, tal_effects = [], []
    tal_rows = []
    for t in talents[:3]:
        tn = (t.get("name") or "").strip()
        _, td = process_skill_text(t.get("desc") or "")
        tal_rows.append((tn, td))
        if tn:
            tal_names.append(tn)
        tal_effects += extract_effects(td)
    tal_tags = join_tags(tal_names + [w for w in tal_effects if w not in tal_names])

    elements = c.get("elements") or []
    energy = c.get("energy") or ""

    # 等级数值（gk fight_info）
    lv1, lv20 = {}, {}
    fi = (c.get("fight_info") or {}).get("levels") or {}
    for attr, key in (("攻击力", "Lv.1攻击力"), ("生命值", "Lv.1生命值"),
                      ("护甲值", "Lv.1护盾值"), ("灵力值", "Lv.1灵力值")):
        v = (fi.get("1") or {}).get(attr, "")
        if v:
            lv1[key] = v
    for attr, key in (("攻击力", "Lv.20攻击力"), ("生命值", "Lv.20生命值"),
                      ("护甲值", "Lv.20护盾值"), ("灵力值", "Lv.20灵力值")):
        v = (fi.get("20") or {}).get(attr, "")
        if v:
            lv20[key] = v

    # 来源
    if src == "gk":
        origin = c.get("obtain") or ""
    else:
        pool = c.get("pool") or ""
        d = c.get("first_date") or ""
        origin = f"限时卡池（{pool}）" if pool else "官方公告"
        if d:
            origin += f"，{d} 公告"

    lines = []
    A = lines.append
    A("{{角色|")
    A("<!-- 资料 -->")
    A(f"|编号=")
    A(f"|来源={origin}")
    A(f"|名称={name}")
    A(f"|头像={name}头像")
    A(f"|星级={c.get('stars','')}")
    A(f"|姓名={(c.get('profile') or {}).get('姓名','') if isinstance(c.get('profile'), dict) else ''}")
    A("|声优=")
    A(f"|外号={(c.get('profile') or {}).get('外号','') if isinstance(c.get('profile'), dict) else ''}")
    A("|性别=")
    A(f"|形态={c.get('form','')}")
    A("|生日=")
    A("|出身=")
    A("|联动=")
    A("|所属=")
    A("|灵卫等级=")
    A("|喜好=")
    A("|不擅长=")
    A(f"|介绍={(c.get('profile') or {}).get('介绍','') if isinstance(c.get('profile'), dict) else ''}")
    A("<!-- 技能 -->")
    A("|技能图标=技能图标")
    for i in range(3):
        A(f"|技能属性{i+1}={elements[i] if i < len(elements) else ''}")
    A(f"|属性标签={join_tags(elements)}")
    A(f"|技能能量={energy}")
    A(f"|技能指定={assign}")
    A(f"|技能说明={skill_txt}")
    # 改动记录（历史版本，c['chg_records'] 列表，元素为纯文本或 {'date':日期,'text':文本}）
    for i, chg in enumerate(c.get("chg_records") or [], 1):
        if i > 5:
            break
        if isinstance(chg, dict):
            if chg.get("date"):
                A(f"|改动时间{i}={chg['date']}")
            A(f"|改动记录{i}={process_skill_text(chg['text'])}")
        else:
            A(f"|改动记录{i}={process_skill_text(chg)}")
    A(f"|技能标签={join_tags(skill_effects)}")
    A(f"|技能标签2={join_tags(skill_effects)}")
    A("<!-- 天赋 -->")
    for i in range(3):
        tn, td = tal_rows[i] if i < len(tal_rows) else ("", "")
        A(f"|天赋{i+1}图标={tn}")
        A(f"|天赋{i+1}名称={tn}")
        A(f"|天赋{i+1}说明={td}")
    A(f"|天赋标签={tal_tags}")
    A(f"|天赋标签2={tal_tags}")
    A("<!-- 皮肤 -->")
    A(f"|皮肤0={name}")
    A(f"|皮肤0图片={name}")
    A("|皮肤0画师=")
    A("|皮肤1=")
    A("|皮肤1图片=")
    A("|皮肤1画师=")
    A("|皮肤1来源=")
    A("|皮肤2=")
    A("|皮肤2图片=")
    A("|皮肤2画师=")
    A("|皮肤2来源=")
    A("|皮肤3=")
    A("|皮肤3图片=")
    A("|皮肤3画师=")
    A("|皮肤3来源=")
    A("<!-- 属性 -->")
    for key in ("Lv.1攻击力", "Lv.1生命值", "Lv.1护盾值", "Lv.1灵力值",
                "Lv.20攻击力", "Lv.20生命值", "Lv.20护盾值", "Lv.20灵力值"):
        v = lv1.get(key) or lv20.get(key) or ""
        A(f"|{key}={v}")
    A("}}")
    return "\n".join(lines), {
        "has_skill": bool(skill_txt), "has_talent": bool(tal_rows),
        "has_lv": bool(lv1 and lv20), "skill_len": len(skill_txt),
    }


def main():
    tt = json.load(open("data/taptap_updates.json", encoding="utf-8"))
    gk = json.load(open("data/gamekee_cards.json", encoding="utf-8"))
    ann = {c["name"]: c for c in tt["cards"] if not c.get("in_wiki")}
    gkm = {c["name"]: c for c in gk["cards"]}
    cands = json.load(open("_wiki_candidates.json", encoding="utf-8"))["candidates"]

    pages, rows = {}, []
    for cand in cands:
        name, src = cand["name"], cand["src"]
        c = (ann if src == "ann" else gkm).get(name)
        if not c:
            print(f"! 数据缺失: {name} [{src}]")
            continue
        wikitext, info = build_wikitext(c, src)
        pages[name] = wikitext
        rows.append([name, src, info["has_skill"], info["has_talent"], info["has_lv"],
                     info["skill_len"]])

    json.dump({"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "pages": pages},
              open("_wiki_pages.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    # 预览文件
    with open("_wiki_preview.txt", "w", encoding="utf-8") as f:
        for i, (name, wt) in enumerate(pages.items(), 1):
            f.write(f"{'='*70}\n【{i}/{len(pages)}】页面: {name}\n{'='*70}\n{wt}\n\n")

    # 清单CSV
    with open("_wiki_upload_list.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["页面名", "来源", "有技能", "有天赋", "有等级数值", "技能字数"])
        w.writerows(rows)

    n_ann = sum(1 for r in rows if r[1] == "ann")
    n_gk = len(rows) - n_ann
    print(f"生成 {len(pages)} 页（公告 {n_ann} + GameKee {n_gk}）")
    print(f"无技能: {sum(1 for r in rows if not r[2])} | 无天赋: {sum(1 for r in rows if not r[3])} | "
          f"无Lv数据: {sum(1 for r in rows if not r[4])}")
    print("输出: _wiki_pages.json / _wiki_preview.txt / _wiki_upload_list.csv")


if __name__ == "__main__":
    main()
