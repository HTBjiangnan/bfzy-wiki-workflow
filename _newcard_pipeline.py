# -*- coding: utf-8 -*-
"""
_newcard_pipeline.py — 新卡建页与公告处理

提供「官方公告 → BWIKI 页面」的核心处理函数，供按需导入调用：
  1. build_char_page()  → 生成 {{角色}} 页 wikitext（属性隐藏留空 / 来源标注）
  2. build_equip_page() → 生成 {{装备}} 页 wikitext（灵武·系列）
  3. check_feed()       → 扫描 TapTap 官方 feed，返回最新的「更新说明」帖
  4. process()          → 一条公告的完整处理：搬公告页 / 建卡页 / 更新公告一览 / 落 JSON

每步落日志 _pipeline_log.txt；请求间隔 5-8s 防 BWIKI WAF 567。
"""
import sys, os, re, json, time, io, uuid
from datetime import datetime, timezone, timedelta

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8')
sys.argv = ['x']
exec(open('_gk_fill.py', encoding='utf-8').read().split('def main')[0])

import requests
sys.path.insert(0, os.getcwd())
import fetch_taptap_updates as ftu

LOG = '_pipeline_log.txt'
STG_CARD = os.path.join('_staging_cards', 'guess111_card.png')
STG_AV = os.path.join('_staging_cards', 'guess111_card_avatar.png')
DONE_SHUTDOWN = False

def log(msg):
    line = '[%s] %s' % (datetime.now().strftime('%H:%M:%S'), msg)
    print(line, flush=True)
    with open(LOG, 'a', encoding='utf-8') as f:
        f.write(line + '\n')

EFFECT_WORDS = [w[0] for w in json.load(open('data/effect_words.json', encoding='utf-8'))]
EFFECT_WORDS.sort(key=len, reverse=True)   # 长词优先

def wiki_skill(text):
    """公告纯文本技能 → wiki 格式：效果词包{{效果|}}，(灵力+X)灰字。
    效果词用正则 alternation 一次扫描（长词优先），避免"冻结"二次命中"冻结宝石"。"""
    t = text.replace('（', '(').replace('）', ')')
    t = re.sub(r'\(([^()]*灵力[^()]*)\)', r'<font color=#99999A>(\1)</font>', t)
    words = [w for w in EFFECT_WORDS if len(w) > 1]
    pat = re.compile('|'.join(re.escape(w) for w in words))
    t = pat.sub(lambda m: '{{效果|%s}}' % m.group(0), t)
    return t

def extract_tags(text):
    tags = [w for w in EFFECT_WORDS if w in text and w not in '冰火雷毒光厄']
    return '、'.join(tags)

def attr_pair(energy_line_text):
    e, form, elements = ftu.parse_energy_line(energy_line_text)
    return e, form, elements

# ---------------------------------------------------------------- TapTap 扫描
def check_feed(cutoff_epoch):
    """拉官方feed第1页，返回今晚新的「更新说明」(mid, created, title) 或 None"""
    s = requests.Session(); s.trust_env = False
    params = {"X-UA": ftu.XUA, "__times": "0", "group_id": ftu.GROUP_ID, "limit": "10",
              "sort": "created", "status": "0", "type": "official",
              "with_hot_comment": "false", "from": "0"}
    j = ftu.get_json(s, ftu.FEED_URL, params)
    if not j:
        return None
    for item in j.get('data', {}).get('list', []):
        m = item.get('moment', {})
        title = (m.get('seo', {}) or {}).get('title', '') or ''
        title = re.sub(r'\s*-\s*百分之一\s*-\s*TapTap\s*$', '', title)
        ct = m.get('created_time', 0)
        if ct >= cutoff_epoch and '更新说明' in title:
            return m.get('id_str'), ct, title
    return None

def parse_superstring(text):
    """解析超弦打造段。返回 card dict 或 None"""
    m = re.search(r'超弦打造[^\n]*\n+\s*([^\n]+)\n+\s*([^\n]+)\n', text)
    if not m:
        return None
    name = m.group(1).strip().strip('“”"')
    energy_line = m.group(2)
    e, form, elements = ftu.parse_energy_line(energy_line)
    # 技能与天赋行
    rest = text[m.end():]
    skill, talents = '', []
    for ln in rest.split('\n'):
        ln = ln.strip()
        if not ln:
            if skill or talents:
                break
            continue
        sm = ftu.SKILL_LINE.match(ln)
        if sm and not skill:
            skill = '%s：%s' % (sm.group(1), sm.group(2).strip()); continue
        tm = ftu.TALENT_LINE.match(ln)
        if tm:
            nm = tm.group(1).strip().lstrip('(')
            if nm in ('ID', 'id', 'I'):
                nm = ''
            talents.append({'name': nm, 'id': tm.group(2),
                            'desc': tm.group(3).strip()}); continue
        if skill or talents:
            break
    return {'name': name, 'energy': e, 'form': form, 'elements': elements,
            'skill': skill, 'talents': talents}

# ---------------------------------------------------------------- wiki 建页
def edit_page(title, text, summary):
    for attempt in range(3):
        d = api({'action': 'edit', 'title': title, 'text': text, 'summary': summary,
                 'token': get_token(refresh=(attempt > 0)), 'format': 'json'}, is_post=True)
        if d.get('edit', {}).get('result') == 'Success':
            return True
        if 'badtoken' in json.dumps(d):
            continue
        log('  edit err: %s' % json.dumps(d, ensure_ascii=False)[:160])
        return False
    return False

def page_exists(title):
    d = api({'action': 'query', 'titles': title, 'format': 'json'})
    return not any('missing' in p for p in d['query']['pages'].values())

def build_char_page(c, ann_date):
    """普通新卡 → {{角色}} wikitext（照克罗塞尔格式）"""
    name = c['name']
    els = c.get('elements') or []
    hidden = c.get('attr_hidden', False)
    a1 = els[0] if els else ''
    a2 = els[1] if len(els) > 1 else ''
    skill = wiki_skill(c.get('skill', '')) if c.get('skill') else ''
    stip = extract_tags(c.get('skill', ''))
    talent_rows, tnames, ttags = '', [], []
    for i, t in enumerate((c.get('talents') or [])[:3], 1):
        desc = wiki_skill(t.get('desc', ''))
        talent_rows += '|天赋%d图标=%s\n|天赋%d名称=%s\n|天赋%d说明=%s\n' % (i, t['name'], i, t['name'], i, desc)
        tnames.append(t['name']); ttags.append(t['name'])
        te = extract_tags(t.get('desc', ''))
        if te:
            ttags.append(te)
    tal_tags = '、'.join(ttags)
    src = '限时卡池（%s），%s 公告' % (c.get('pool') or '?', ann_date)
    note = '（属性于9.21维护后公开）' if hidden else ''
    # 改动记录（历史版本技能，c['chg_records'] 元素为纯文本或 {'date','text'}）
    chg_rows = ''
    for i, x in enumerate((c.get('chg_records') or [])[:5], 1):
        dt, tx = (x.get('date'), x.get('text')) if isinstance(x, dict) else (None, x)
        if dt:
            chg_rows += '\n|改动时间%d=%s' % (i, dt)
        chg_rows += '\n|改动记录%d=%s' % (i, wiki_skill(tx))
    wt = """{{角色|
<!-- 资料 -->
|编号=
|实装时间=%s
|来源=%s
|名称=%s
|头像=%s头像
|星级=5
|姓名=
|声优=
|外号=
|性别=
|形态=%s
|生日=
|出身=
|联动=
|所属=
|灵卫等级=
|喜好=
|不擅长=
|介绍=
<!-- 技能 -->
|技能图标=技能图标
|技能属性1=%s
|技能属性2=%s
|技能属性3=
|属性标签=%s
|技能能量=%s
|技能指定=%s
|技能说明=%s%s%s
|技能标签=%s
|技能标签2=%s
<!-- 天赋 -->
%s|天赋标签=%s
|天赋标签2=%s
<!-- 皮肤 -->
|皮肤0=%s
|皮肤0图片=%s
|皮肤0画师=
|皮肤1=
|皮肤1图片=
|皮肤1画师=
|皮肤1来源=
|皮肤2=
|皮肤2图片=
|皮肤2画师=
|皮肤2来源=
|皮肤3=
|皮肤3图片=
|皮肤3画师=
|皮肤3来源=
<!-- 属性 -->
|等级属性=
}}
""" % (ann_date, src, name, name, c.get('form', ''), a1, a2, '、'.join(els),
       c.get('energy', ''), '自动指定' if (c.get('skill', '').startswith('自动')) else '手动指定',
       skill, note, chg_rows, stip, stip, talent_rows, tal_tags, tal_tags, name, name)
    return wt

def build_equip_page(c):
    name = c['name']
    els = c.get('elements') or []
    skill = wiki_skill(c.get('skill', '')) if c.get('skill') else ''
    stip = extract_tags(c.get('skill', ''))
    d = api({'action': 'query', 'titles': 'File:' + name + '.png', 'format': 'json'})
    icon_exists = not any('missing' in p for p in d['query']['pages'].values())
    wt = """{{装备
|编号=
|名称=%s
|星级=
|属性1=%s
|属性2=%s
|能耗=%s
|指定=%s
|技能说明=%s
|技能标签=%s
|图标=%s
}}
""" % (name, els[0] if els else '', els[1] if len(els) > 1 else '',
       c.get('energy', ''), '自动指定' if (c.get('skill', '').startswith('自动')) else '手动指定',
       skill, stip, name if icon_exists else '')
    return wt

def upload_card_img(card_name):
    """预告图上传为 <卡名>.png + <卡名>头像.png"""
    ok = 0
    cmt = '新卡卡图（TapTap猜属性活动预告图 2026-09-15）'
    if upload_png(STG_CARD, card_name + '.png', cmt):
        ok += 1
    time.sleep(4)
    if os.path.exists(STG_AV) and upload_png(STG_AV, card_name + '头像.png', cmt):
        ok += 1
    return ok

# ---------------------------------------------------------------- 主流程
def process(mid, ct, title):
    ann_date = datetime.fromtimestamp(ct, tz=timezone.utc).astimezone(
        timezone(timedelta(hours=8))).strftime('%Y-%m-%d')
    ann_d8 = ann_date.replace('-', '.')
    s = requests.Session(); s.trust_env = False
    t, text, images, reveal = ftu.fetch_detail(s, mid)
    log('公告全文 %d 字，开始搬运' % len(text))

    # 1. 公告文章页
    page_title = t or title
    if page_exists(page_title):
        page_title = '%s（2026-09-18）' % page_title
        log('  页名冲突，改用: %s' % page_title)
    pool = ftu.parse_pool_changes(text)
    cards = ftu.parse_cards(text, pool)
    for c in cards:
        c.setdefault('first_date', ann_date)
        c.setdefault('source_url', ftu.MOMENT_URL.format(mid))
    wiki_names = ftu.load_wiki_names()
    for c in cards:
        c['in_wiki'] = ftu.norm_name(c['name']) in wiki_names
    sup = parse_superstring(text)
    log('  解析: 卡池%d项 普通新卡%d张 超弦打造=%s' % (
        len(pool), len(cards), sup['name'] if sup else '无'))

    pool_line = '\n'.join('* %s号卡池（%s）：[[%s]]' % (p['pool_day'], p['pool_size'], p['card']) for p in pool)
    new_links = '\n'.join('* [[%s]]%s' % (c['name'],
        ('（属性于9.21维护后公开）' if c.get('attr_hidden') else '')) for c in cards)
    sup_line = ''
    if sup:
        inwiki = page_exists(sup['name'])
        sup_line = '* 超弦打造：%s（%s新卡）' % (
            ('[[%s]]' % sup['name']) if inwiki else sup['name'],
            '确认为' if not inwiki else '疑为老卡复用')

    summary_part = ''
    if pool_line:
        summary_part += '===卡池调整===\n%s\n\n' % pool_line
    if new_links:
        summary_part += '===本期新卡===\n%s\n\n' % new_links
    if sup_line:
        summary_part += '===超弦打造===\n%s\n\n' % sup_line

    body = re.sub(r'\n+', '\n\n', text.strip())
    wt = """{{文章模板
|文章上级页面=公告
|时间=%s
|是否原创=
|作者=
|来源=TapTap
|原文地址=%s
}}
{{#seo:
|title=%s - 百分之一WIKI
|description=《百分之一》%s（%s发布）：TapTap官方公告全文，含维护时间、补偿内容、卡池调整与新卡信息。
|keywords=百分之一,更新说明,公告,2026-09
}}
'''发布日期'''：%s　|　'''来源'''：[https://www.taptap.cn/app/78938 TapTap官方动态]

%s===公告全文===

%s

----
全部官方公告见[[官方公告一览]]；每周更新动态自动同步自TapTap官方。
""" % (ann_d8, ftu.MOMENT_URL.format(mid), page_title, page_title, ann_date,
       ann_date, summary_part, body)
    r = edit_page(page_title, wt, '搬运TapTap官方更新公告（%s）' % ann_date)
    log('  公告文章页 %s: %s' % (page_title, 'OK' if r else 'FAIL'))
    time.sleep(6)

    # 2. 新卡建页 + 传图（cards 与 sup 按 norm_name 去重；sup 老卡不建页）
    normal = [c for c in cards if not c['name'].startswith('灵武·')]
    hidden_cards = [c for c in normal if c.get('attr_hidden')]
    img_target = None
    if len(hidden_cards) == 1:
        img_target = hidden_cards[0]
    elif len(normal) == 1:
        img_target = normal[0]
    build_list = list(cards)
    if sup and not page_exists(sup['name']):
        sup['in_wiki'] = False
        if ftu.norm_name(sup['name']) not in {ftu.norm_name(x['name']) for x in cards}:
            build_list.append(sup)
    for c in build_list:
        if c.get('in_wiki'):
            log('  %s 已在wiki，跳过建页' % c['name'])
            continue
        if c['name'].startswith('灵武·'):
            wt2 = build_equip_page(c)
        else:
            wt2 = build_char_page(c, ann_date)
        r = edit_page(c['name'], wt2, '创建%s页（%s公告）' % ('灵武' if c['name'].startswith('灵武·') else '角色', ann_date))
        log('  建页 %s: %s' % (c['name'], 'OK' if r else 'FAIL'))
        time.sleep(6)
        if r and c is img_target and os.path.exists(STG_CARD):
            n = upload_card_img(c['name'])
            log('  预告图上传 %s: %d/2' % (c['name'], n))
            time.sleep(4)

    # 3. 官方公告一览：更新说明表顶部插行
    time.sleep(5)
    d = api({'action': 'parse', 'page': '官方公告一览', 'prop': 'wikitext', 'format': 'json'})
    wt3 = d['parse']['wikitext']['*']
    points = []
    for p in pool:
        points.append('卡池%s号%s→[[%s]]' % (p['pool_day'], p['pool_size'], p['card']))
    for c in cards:
        points.append('新卡 [[%s]]' % c['name'])
    if sup:
        exists = page_exists(sup['name'])
        points.append(('超弦新卡 [[%s]]' if exists else '超弦打造:%s') % sup['name'])
    ns = re.search(r'下周超弦属性[:：]\s*([冰火雷毒光厄/、]+)', text)
    if ns:
        points.append('下周超弦:%s' % ns.group(1))
    row = '|-\n| %s || %s || style="text-align:left" | %s || [%s TapTap]' % (
        ann_date, page_title, '<br>'.join(points), ftu.MOMENT_URL.format(mid))
    anchor = "id=\"2026年9月\"|'''2026年9月'''"
    i = wt3.find(anchor)
    if i >= 0:
        j = wt3.find('\n|-', i)
        if j >= 0:
            wt3 = wt3[:j] + '\n' + row + wt3[j:]
            r = edit_page('官方公告一览', wt3, '新增%s更新说明行' % ann_date)
            log('  一览页插行: %s' % ('OK' if r else 'FAIL'))
    time.sleep(6)

    # 4. taptap_updates.json 增量
    try:
        db = json.load(open('data/taptap_updates.json', encoding='utf-8'))
        ann = ftu.parse_announcement(page_title, text, mid, ct, 'update')
        ann['cards_extracted'] = [c['name'] for c in cards]
        db['announcements'] = sorted(db.get('announcements', []) + [ann],
                                     key=lambda a: a.get('created_time', 0), reverse=True)
        db['cards'] = ftu.merge_cards(db.get('cards', []), cards, wiki_names)
        if sup:
            sup2 = dict(sup); sup2['first_date'] = ann_date
            sup2['source_url'] = ftu.MOMENT_URL.format(mid); sup2['pool'] = '超弦打造'
            db['cards'] = ftu.merge_cards(db['cards'], [sup2], wiki_names)
        db['meta']['updated_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        db['meta']['announcement_count'] = len(db['announcements'])
        db['meta']['card_count'] = len(db['cards'])
        sid = set(db['meta'].get('seen_ids', [])); sid.add(mid)
        db['meta']['seen_ids'] = sorted(sid)
        json.dump(db, open('data/taptap_updates.json', 'w', encoding='utf-8'),
                  ensure_ascii=False, indent=1)
        log('  taptap_updates.json 已更新')
    except Exception as e:
        log('  taptap_updates 更新失败: %s' % str(e)[:100])

# ---------------------------------------------------------------- 入口
# 本文件对外提供的是「函数库」形态，不含无人值守的定时入口。典型用法：
#
#   import _newcard_pipeline as ncp
#   ncp.build_char_page(card, ann_date)   # 生成 {{角色}} 页 wikitext
#   ncp.build_equip_page(card)            # 生成 {{装备}} 页 wikitext
#   ncp.check_feed(cutoff_epoch)          # 扫 TapTap feed，返回最新「更新说明」帖
#   ncp.process(mid, created, title)      # 处理一条公告（搬页 / 建卡页 / 更新一览 / 落 JSON）
#
# 建页结果请自行交给 wiki_upload.py 发布。
