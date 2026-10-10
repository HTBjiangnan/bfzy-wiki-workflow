# -*- coding: utf-8 -*-
"""Step2 批量写入：553 角色页 补星级/形态/Lv.1/Lv.20 + 插入|等级属性=
用法：
  python wiki_batch_edit.py           # Phase A+B 审计+dry-run（不写）
  python wiki_batch_edit.py --apply   # Phase C 逐页 edit + 批量 purge
数据源：data/_merc_levels.json（546 逐级四维）+ _apk/_mercs_v2.json（541 星级/形态）
"""
import urllib.request, urllib.parse, urllib.error, http.cookiejar
import sys, json, time, os, re

sys.stdout.reconfigure(encoding='utf-8')
os.chdir(os.path.dirname(os.path.abspath(__file__)))
API = 'https://wiki.biligame.com/bfzy/api.php'
APPLY = '--apply' in sys.argv

cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(
    urllib.request.ProxyHandler({}),
    urllib.request.HTTPCookieProcessor(cj))
opener.addheaders = [
    ('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36'),
    ('Accept', 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'),
    ('Accept-Language', 'zh-CN,zh;q=0.9'),
    ('Referer', 'https://wiki.biligame.com/bfzy/'),
]
for kv in open('_wiki_cookie.txt', encoding='utf-8').read().strip().split('; '):
    if '=' in kv:
        k, v = kv.split('=', 1)
        cj.set_cookie(http.cookiejar.Cookie(
            version=0, name=k, value=v, port=None, port_specified=False,
            domain='wiki.biligame.com', domain_specified=False, domain_initial_dot=False,
            path='/bfzy/', path_specified=True, secure=False, expires=None,
            discard=True, comment=None, comment_url=None, rest={}, rfc2109=False))

ETHNICITY_MAP = {'1': '人型', '2': '龙型', '3': '人龙型', '4': '机械型', '5': '类兽型'}
# 数据键名清洗：去尾随空格 + 括号统一
def _clean_key(k):
    return k.strip().replace('[', '【').replace(']', '】')
_raw_levels = json.load(open('data/_merc_levels.json', encoding='utf-8'))
LEVELS = {_clean_key(k): v for k, v in _raw_levels.items()}
_raw_mercs = json.load(open('_apk/_mercs_v2.json', encoding='utf-8'))
MERCS = {_clean_key(e['Name']): e for e in _raw_mercs if e.get('Name')}
# wiki 页名 → 数据键 显式别名（格式差异：X（Y）↔Y*X / X武装↔X武装*执行者 / 余暇↔余霞）
ALIAS = {
    '电*硕鼠': '硕鼠（电）', '蚀*硕鼠': '硕鼠（蚀）',
    '电磁武装*执行者': '电磁武装', '极地武装*执行者': '极地武装',
    '聚核武装*执行者': '聚核武装', '烈焰武装*执行者': '烈焰武装',
    '学士的余霞*菲琳': '学士的余暇*菲琳',
}
def data_key(wiki_title):
    if wiki_title in LEVELS: return wiki_title
    if wiki_title in ALIAS and ALIAS[wiki_title] in LEVELS: return ALIAS[wiki_title]
    return None

def api(params, is_post=False, retries=4):
    data = urllib.parse.urlencode(params).encode('utf-8')
    url = API if is_post else API + '?' + data.decode('utf-8')
    for attempt in range(retries):
        try:
            r = opener.open(url, data if is_post else None, timeout=90)
            return json.loads(r.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            if e.code == 567 and attempt < retries - 1:
                print('    [WAF 567] 冷却 45s 重试...'); time.sleep(45); continue
            raise
        except Exception:
            if attempt == retries - 1: raise
            time.sleep(10)

def get_token():
    return api({'action': 'query', 'meta': 'tokens', 'type': 'csrf', 'format': 'json'})['query']['tokens']['csrftoken']

# ---------- Phase A: 分类清单 ----------
def list_category():
    members, cont = [], None
    while True:
        p = {'action': 'query', 'list': 'categorymembers', 'cmtitle': '分类:角色',
             'cmlimit': 500, 'cmnamespace': 0, 'format': 'json'}
        if cont: p['cmcontinue'] = cont
        d = api(p)
        members += [m['title'] for m in d['query']['categorymembers']]
        cont = d.get('continue', {}).get('cmcontinue')
        time.sleep(4)
        if not cont: break
    return members

# ---------- Phase B: 批量拉 wikitext ----------
def fetch_batch(titles):
    """用 titles= 直接批量拉（上限50）"""
    result = {}
    for i in range(0, len(titles), 50):
        batch = titles[i:i+50]
        p = {'action': 'query', 'titles': '|'.join(batch), 'prop': 'revisions',
             'rvprop': 'content|ids', 'rvslots': 'main', 'format': 'json'}
        d = api(p)
        pages = d.get('query', {}).get('pages', {})
        for pid, pg in pages.items():
            if 'revisions' in pg and pg.get('title') in batch:
                rv = pg['revisions'][0]
                result[pg['title']] = (rv['slots']['main']['*'], rv['revid'])
            elif 'missing' in pg:
                result[pg.get('title', '?')] = (None, None)
        time.sleep(6)
    return result

# ---------- patch 逻辑 ----------
def build_attr_str(wiki_title, max_lv=20):
    key = data_key(wiki_title)
    ent = LEVELS.get(key) if key else None
    if not ent: return None, None, None
    lvs = sorted(int(k) for k in ent)
    use = [l for l in lvs if l <= max_lv]
    if not use: return None, None, None
    parts, lv1, lvN = [], None, None
    for l in use:
        p = ent[str(l)]['props']
        parts.append('%s,%s,%s,%s' % (p.get('2', 0), p.get('0', 0), p.get('1', 0), p.get('3', 0)))
        if l == 1: lv1 = p
        if l == max_lv: lvN = p
    return ';'.join(parts), lv1, lvN

def patch_page(title, wt):
    """返回 (new_wt, changes:list, problems:list)"""
    changes, problems = [], []
    attr, lv1, lv20 = build_attr_str(title)
    mrec = MERCS.get(title) or MERCS.get(data_key(title)) if data_key(title) else MERCS.get(title)

    if attr is None:
        problems.append('无逐级数据')
    lines = wt.split('\n')
    has_lv20_line = any(l.startswith('|Lv.20') for l in lines)
    out, done_attr = [], False
    for ln in lines:
        # 星级空值补
        if re.match(r'^\|星级=\s*$', ln) and mrec and mrec.get('Rarity'):
            ln = '|星级=%s' % mrec['Rarity']; changes.append('星级')
        # 形态空值补
        if re.match(r'^\|形态=\s*$', ln) and mrec and mrec.get('Ethnicity') in ETHNICITY_MAP:
            ln = '|形态=%s' % ETHNICITY_MAP[mrec['Ethnicity']]; changes.append('形态')
        # Lv.1/Lv.20 空槽补
        for tag, lv, pk in [('Lv.1攻击力', lv1, '2'), ('Lv.1生命值', lv1, '0'), ('Lv.1护盾值', lv1, '1'), ('Lv.1灵力值', lv1, '3'),
                            ('Lv.20攻击力', lv20, '2'), ('Lv.20生命值', lv20, '0'), ('Lv.20护盾值', lv20, '1'), ('Lv.20灵力值', lv20, '3')]:
            if re.match(r'^\|%s=\s*$' % re.escape(tag), ln) and lv:
                ln = '|%s=%s' % (tag, lv.get(pk, '')); changes.append(tag)
        out.append(ln)
        already = any(l.startswith('|等级属性=') for l in lines)
        # 主锚点：|Lv.20灵力值= 行后
        if ln.startswith('|Lv.20灵力值=') and not done_attr:
            if already:
                done_attr = True
            elif attr:
                out.append('|等级属性=%s' % attr); changes.append('等级属性(%d级)' % (attr.count(';') + 1)); done_attr = True
            else:
                done_attr = True
        # fallback 锚点：无 Lv.20 参数行时，用 |Lv.1灵力值= 行后插 Lv.20 四行 + 等级属性
        if ln.startswith('|Lv.1灵力值=') and not done_attr and not has_lv20_line:
            if already:
                done_attr = True
            elif attr and lv20:
                out.append('|Lv.20攻击力=%s' % lv20.get('2', ''))
                out.append('|Lv.20生命值=%s' % lv20.get('0', ''))
                out.append('|Lv.20护盾值=%s' % lv20.get('1', ''))
                out.append('|Lv.20灵力值=%s' % lv20.get('3', ''))
                out.append('|等级属性=%s' % attr)
                changes += ['Lv.20攻击力+', 'Lv.20生命值+', 'Lv.20护盾值+', 'Lv.20灵力值+',
                            '等级属性(%d级)' % (attr.count(';') + 1)]
                done_attr = True
            else:
                done_attr = True
    if attr is not None and not done_attr and not any(l.startswith('|等级属性=') for l in out):
        problems.append('无锚点')
    return '\n'.join(out), changes, problems

# ---------- Phase C: 写入 ----------
def edit(title, text, summary, baserevid):
    tok = get_token()
    d = api({'action': 'edit', 'title': title, 'text': text, 'summary': summary,
             'token': tok, 'baserevid': baserevid, 'format': 'json'}, is_post=True)
    if 'edit' in d and d['edit'].get('result') == 'Success':
        return d['edit'].get('newrevid')
    raise RuntimeError(json.dumps(d, ensure_ascii=False)[:200])

def purge_batch(titles):
    for i in range(0, len(titles), 20):
        batch = titles[i:i+20]
        try:
            tok = get_token()
            api({'action': 'purge', 'titles': '|'.join(batch), 'forcelinkupdate': 1,
                 'token': tok, 'format': 'json'}, is_post=True)
            print('  purge 批次 %d-%d OK' % (i, i + len(batch)))
        except Exception as e:
            print('  purge 批次失败:', str(e)[:100])
        time.sleep(6)

# ---------- 主流程 ----------
def main():
    print('=== Phase A: 拉取分类:角色 清单 ===')
    titles = list_category()
    print('分类成员:', len(titles), '页')
    json.dump(titles, open('_batch_titles.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)

    # 名称映射审计
    no_data = [t for t in titles if t not in LEVELS]
    data_not_wiki = [k for k in LEVELS if k not in set(titles)]
    print('数据缺失页(无逐级数据):', len(no_data))
    print('数据有但wiki无页:', len(data_not_wiki))

    print()
    print('=== Phase B: 批量拉 wikitext + dry-run ===')
    wt_map = fetch_batch(titles)
    print('拉到 wikitext:', len(wt_map), '页')

    plan = {}
    stats = {}
    for t in titles:
        if t not in wt_map or wt_map[t][0] is None:
            plan[t] = {'status': 'fetch_fail'}; continue
        wt, rid = wt_map[t]
        if '{{角色' not in wt and '{{:' not in wt[:500]:
            plan[t] = {'status': 'no_template'}; continue
        new_wt, changes, problems = patch_page(t, wt)
        if changes:
            # 有改动即写入（无逐级数据的页仍可补星级/形态/Lv.1）
            plan[t] = {'status': 'to_edit', 'changes': changes, 'revid': rid,
                       'new_len': len(new_wt), 'problems': problems or None}
            stats['to_edit'] = stats.get('to_edit', 0) + 1
        elif problems:
            plan[t] = {'status': 'problem', 'problems': problems, 'revid': rid}
            stats['problem'] = stats.get('problem', 0) + 1
        else:
            plan[t] = {'status': 'no_change', 'revid': rid}
            stats['no_change'] = stats.get('no_change', 0) + 1
        for c in changes:
            key = c.split('(')[0]
            stats[key] = stats.get(key, 0) + 1
    json.dump(plan, open('_batch_plan.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('dry-run 统计:', json.dumps(stats, ensure_ascii=False))
    print('明细: _batch_plan.json')

    if not APPLY:
        print('\n[dry-run 模式结束，加 --apply 执行写入]')
        return

    print()
    print('=== Phase C: 逐页写入 ===')
    done = json.load(open('_batch_done.json', encoding='utf-8')) if os.path.exists('_batch_done.json') else {}
    edited = []
    to_edit = [t for t, v in plan.items() if v.get('status') == 'to_edit']
    print('待写:', len(to_edit), '已完成:', len(done))
    for idx, t in enumerate(to_edit):
        if t in done: continue
        wt, rid = wt_map[t]
        new_wt, changes, problems = patch_page(t, wt)
        if not changes:
            done[t] = 'no_change'; continue
        try:
            nrid = edit(t, new_wt, '批量补全：星级/形态/Lv.1/Lv.20/等级属性（游戏配置数据校准）', rid)
            done[t] = nrid
            edited.append(t)
            print('[%d/%d] ✓ %s -> %s (%s)' % (idx + 1, len(to_edit), t, nrid, ','.join(changes[:4])))
        except Exception as e:
            print('[%d/%d] ✗ %s: %s' % (idx + 1, len(to_edit), t, str(e)[:120]))
            if '567' in str(e): time.sleep(60)
        json.dump(done, open('_batch_done.json', 'w', encoding='utf-8'), ensure_ascii=False)
        time.sleep(6)
    print()
    print('写入完成:', len(edited), '页，开始 purge...')
    purge_batch(edited)
    print('全部完成')

main()
