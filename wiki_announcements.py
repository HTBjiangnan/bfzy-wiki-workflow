# -*- coding: utf-8 -*-
"""任务2a：生成「官方公告一览」页面并创建"""
import json, re, sys, requests, time
sys.stdout.reconfigure(encoding='utf-8')

tap = json.load(open('data/taptap_updates.json', encoding='utf-8'))
anns = tap['announcements']
# 已上传 wiki 的卡名（内链用）
up = json.load(open('_wiki_uploaded.json', encoding='utf-8'))
uploaded = set()
for k, v in (up.items() if isinstance(up, dict) else []):
    if isinstance(v, dict) and (v.get('pageid') or v.get('done')):
        uploaded.add(k.replace('*', '·'))

def wl(name):
    """卡名 -> wikilink（已上传的页面内链，名字统一·）"""
    n = name.replace('*', '·')
    return f"[[{n}]]" if n in uploaded else f"'''{n}'''"

# ---- 生成 wikitext ----
L = []
L.append("'''《百分之一》官方公告一览'''——收录 TapTap 官方动态全部公告（2025年8月至今），按时间倒序排列。")
L.append("")
L.append("'''本期更新摘要'''：每周一 15:30~18:30 例行维护；每周四发「猜猜我属啥」活动帖；新卡通常随更新说明公布。")
L.append("")

updates = [a for a in anns if a.get('type') != 'guess']
guesses = [a for a in anns if a.get('type') == 'guess']
updates.sort(key=lambda a: a.get('date', ''), reverse=True)
guesses.sort(key=lambda a: a.get('date', ''), reverse=True)

# 月度目录
months = sorted({a['date'][:7] for a in anns}, reverse=True)
L.append("==目录==")
L.append("")
for m in months:
    y, mo = m.split('-')
    L.append(f"* [[#{y}年{int(mo)}月|{y}年{int(mo)}月]]")
L.append("")

# ---- 更新说明表 ----
L.append("==更新说明==")
L.append("")
L.append('{| class="wikitable" style="width:100%; text-align:center"')
L.append('! style="width:90px" | 日期 !! 标题 !! 更新要点 !! 原文')
cur_month = None
for a in updates:
    m = a['date'][:7]
    if m != cur_month:
        y, mo = m.split('-')
        L.append(f'|- style="background:#eaecf0" |colspan=4| id="{y}年{int(mo)}月"|\'\'\'{y}年{int(mo)}月\'\'\'')
        cur_month = m
    points = []
    for pc in a.get('pool_changes', []):
        points.append(f"卡池{pc['pool_day']}号{pc['pool_size']}→{wl(pc['card'])}")
    for c in a.get('cards_extracted', []):
        points.append(f"新卡 {wl(c)}")
    if a.get('next_superstring_attr'):
        points.append(f"下周超弦:{a['next_superstring_attr']}")
    pt = '<br>'.join(points) if points else '例行维护'
    title = a['title'].replace('|', '｜')
    L.append('|-')
    L.append(f'| {a["date"]} || {title} || style="text-align:left" | {pt} || [{a["url"]} TapTap]')
L.append('|}')
L.append("")

# ---- 活动帖表 ----
L.append("==猜猜我属啥（每周活动）==")
L.append("")
L.append("每周四发布，猜下周新卡属性，周五揭晓。")
L.append("")
L.append('{| class="wikitable" style="width:100%; text-align:center"')
L.append('! style="width:90px" | 日期 !! 期数 !! 本期答案 !! 原文')
cur_month = None
for a in guesses:
    m = a['date'][:7]
    if m != cur_month:
        y, mo = m.split('-')
        L.append(f'|- style="background:#eaecf0" |colspan=4| \'\'\'{y}年{int(mo)}月\'\'\'')
        cur_month = m
    mm = re.search(r'第(\d+)期', a.get('title', ''))
    qishu = f"第{mm.group(1)}期" if mm else '-'
    attrs = a.get('revealed_attrs') or []
    ans = ''.join(attrs) if attrs else (a.get('reveal_status') or '-')
    L.append('|-')
    L.append(f'| {a["date"]} || {qishu} || {ans} || [{a["url"]} TapTap]')
L.append('|}')
L.append("")
L.append(f"共 {len(anns)} 条公告（更新说明 {len(updates)} + 活动帖 {len(guesses)}）。")
L.append("[[分类:公告]]")

wikitext = '\n'.join(L)
open('_wiki_announcements.wiki', 'w', encoding='utf-8').write(wikitext)
print(f'wikitext 已生成: {len(wikitext)} 字符, 更新说明{len(updates)}条, 活动帖{len(guesses)}条')

# ---- 创建页面 ----
API = 'https://wiki.biligame.com/bfzy/api.php'
s = requests.Session(); s.trust_env = False
s.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36'})
for kv in open('_wiki_cookie.txt', encoding='utf-8').read().strip().split('; '):
    if '=' in kv:
        k, v = kv.split('=', 1)
        s.cookies.set(k, v, domain='wiki.biligame.com', path='/bfzy/')
tok = s.get(API, params={'action': 'query', 'meta': 'tokens', 'type': 'csrf', 'format': 'json'}, timeout=30).json()['query']['tokens']['csrftoken']
r = s.post(API, data={'action': 'edit', 'title': '官方公告一览', 'text': wikitext,
                      'summary': '创建官方公告一览：TapTap全部107条公告索引（更新说明+活动帖）',
                      'token': tok, 'format': 'json'}, timeout=60)
d = r.json()
if d.get('edit', {}).get('result') == 'Success':
    print('页面创建成功 revid:', d['edit'].get('newrevid'))
else:
    print('创建响应:', json.dumps(d, ensure_ascii=False)[:300])
# 验证
time.sleep(2)
r2 = s.get(API, params={'action': 'parse', 'page': '官方公告一览', 'prop': 'text', 'format': 'json'}, timeout=30)
ok = '猜猜我属啥' in r2.json().get('parse', {}).get('text', {}).get('*', '')
print('渲染验证(含活动帖表):', ok)
