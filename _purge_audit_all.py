# -*- coding: utf-8 -*-
"""全量purge + 全站断链审计（模板改动后缓存刷新 + wpDestFile红链扫描）"""
import requests, sys, json, time, re
from urllib.parse import unquote

sys.stdout.reconfigure(encoding='utf-8')
API = 'https://wiki.biligame.com/bfzy/api.php'
s = requests.Session()
s.trust_env = False
s.headers['User-Agent'] = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36'
for kv in open('_wiki_cookie.txt', encoding='utf-8').read().strip().split('; '):
    if '=' in kv:
        k, v = kv.split('=', 1)
        s.cookies.set(k, v, domain='wiki.biligame.com', path='/bfzy/')

def api(params, tries=4):
    for _ in range(tries):
        try:
            r = s.get(API, params=params, timeout=60)
            return r.json()
        except Exception:
            time.sleep(6)
    return None

def post(data, tries=4):
    for _ in range(tries):
        try:
            r = s.post(API, data=data, timeout=90)
            return r.json()
        except Exception:
            time.sleep(8)
    return None

# ===== 1. 最新页面清单 =====
titles = []
cont = None
while True:
    p = {'action': 'query', 'list': 'allpages', 'apnamespace': '0', 'aplimit': '500', 'format': 'json'}
    if cont: p['apcontinue'] = cont
    d = api(p)
    titles += [x['title'] for x in d['query']['allpages']]
    rc = d.get('continue', {})
    if rc:
        cont = rc.get('apcontinue') or rc.get('continue') or next(iter(rc.values()))
    else: break
print(f'页面总数: {len(titles)}', flush=True)
json.dump(titles, open('_content_pages_v2.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)

# ===== 2. 全量purge =====
ok, failed = 0, []
for i in range(0, len(titles), 20):
    batch = titles[i:i+20]
    d = post({'action': 'purge', 'titles': '|'.join(batch), 'forcelinkupdate': 1, 'format': 'json'})
    if d and 'purge' in d:
        purged = [p['title'] for p in d['purge'] if 'purged' in p]
        ok += len(purged)
        failed += [t for t in batch if t not in purged]
    else:
        failed += batch
    time.sleep(4)
    if (i // 20) % 10 == 0:
        print(f'-- purge {min(i+20, len(titles))}/{len(titles)} ok={ok}', flush=True)
print(f'purge完成: {ok}/{len(titles)} 失败{len(failed)}', flush=True)
json.dump(failed, open('_purge2_failed.json', 'w', encoding='utf-8'), ensure_ascii=False)

# ===== 3. 断链审计 =====
result = {}
parse_fail = []
for i, t in enumerate(titles):
    d = api({'action': 'parse', 'page': t, 'prop': 'text', 'format': 'json'})
    if d and 'parse' in d:
        html = d['parse']['text']['*']
        broken = set(unquote(m) for m in re.findall(r'wpDestFile=([^&"]+)', html))
        reds = set(unquote(m) for m in re.findall(r'title=([^&"]+)&amp;action=edit&amp;redlink=1', html))
        if broken or reds:
            result[t] = {'files': sorted(broken), 'pages': sorted(reds)}
            print(f'[{i+1}/{len(titles)}] {t}: 文件断链{broken} 页面红链{reds}', flush=True)
    else:
        parse_fail.append(t)
    time.sleep(2.5)
    if (i + 1) % 50 == 0:
        print(f'-- 审计进度 {i+1}/{len(titles)} 已发现问题页{len(result)}', flush=True)

json.dump(result, open('_broken_links_full.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
json.dump(parse_fail, open('_audit_parse_failed.json', 'w', encoding='utf-8'), ensure_ascii=False)
print(f'\n===== 审计完成 =====', flush=True)
print(f'存在断链的页面: {len(result)}', flush=True)
print(f'解析失败页面: {len(parse_fail)}', flush=True)
