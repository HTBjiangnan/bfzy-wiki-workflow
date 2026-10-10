# -*- coding: utf-8 -*-
"""GameKee 卡面图补全缺图页（用户已确认卡面风格可接受）
用法: python _gk_fill.py sample   # 先跑 3 页样例
      python _gk_fill.py all      # 跑剩余全部
流程: 拉页面真实参数引用名(|头像=/|皮肤0图片=) → 查文件存在性 → 下载 GameKee 卡面
      → 全图上传为皮肤0引用名 + 顶方裁剪 256 上传为头像引用名 → 末尾管道分隔 purge
"""
import urllib.request, urllib.parse, http.cookiejar
import sys, json, time, os, re, uuid, io

sys.stdout.reconfigure(encoding='utf-8')
os.chdir(os.path.dirname(os.path.abspath(__file__)))
API = 'https://wiki.biligame.com/bfzy/api.php'
DONE_FILE = '_gk_fill_done.json'
TMP_CARD = '_tmp_gk_card.png'
TMP_AV = '_tmp_gk_avatar.png'

cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(
    urllib.request.ProxyHandler({}),
    urllib.request.HTTPCookieProcessor(cj))
opener.addheaders = [
    ('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36'),
    ('Accept', 'application/json, text/plain, */*'),
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

_token_cache = [None]
def get_token(refresh=False):
    if _token_cache[0] is None or refresh:
        d = api({'action': 'query', 'meta': 'tokens', 'type': 'csrf', 'format': 'json'})
        _token_cache[0] = d['query']['tokens']['csrftoken']
    return _token_cache[0]

def api(params, is_post=False):
    data = urllib.parse.urlencode(params).encode('utf-8')
    url = API if is_post else API + '?' + data.decode('utf-8')
    for attempt in range(5):
        try:
            r = opener.open(url, data if is_post else None, timeout=90)
            return json.loads(r.read().decode('utf-8'))
        except Exception as e:
            if attempt == 4: raise
            print('  %s，冷却 30s 重试...' % type(e).__name__, flush=True); time.sleep(30)
        finally:
            time.sleep(5)

# ---- GameKee 下载 ----
GK_H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0",
        "Referer": "https://www.gamekee.com/hundredth", "game-id": "50075"}
gk_op = urllib.request.build_opener(urllib.request.ProxyHandler({}))

def gk_fetch(url):
    req = urllib.request.Request(url if url.startswith('http') else 'https:' + url, headers=GK_H)
    for attempt in range(4):
        try:
            with gk_op.open(req, timeout=30) as r:
                return r.read()
        except Exception as e:
            if attempt == 3: raise
            print('  gk retry: %s' % str(e)[:60], flush=True); time.sleep(5)

def load_targets():
    m2 = json.load(open('_gk_img_match2.json', encoding='utf-8'))
    targets = [(w, g, u) for w, g, u, _ in m2['hit']]
    # 错别字变体补充: wiki 凤凌云髻 = GameKee 风凌云髻
    t = json.load(open('_gk_trees_fresh.json', encoding='utf-8'))
    def walk(nodes, res):
        for n in nodes:
            if isinstance(n, dict): res.append(n); walk(n.get('child') or [], res)
    res = []; walk([t['data']], res)
    leafmap = {n['name']: n['icon'] for n in res if n.get('name') and n.get('icon')}
    have = {g for _, g, _ in targets}
    for w in m2['nohit']:
        w2 = w.replace('·', '*')
        if '凤凌云髻' in w2:
            gname = w2.replace('凤凌云髻', '风凌云髻')
            if gname in leafmap:
                targets.append((w, gname, leafmap[gname]))
    return targets

def pull_refs(titles):
    """title -> dict(avatar=引用名, skin=引用名)（不含扩展名）"""
    out = {}
    for i in range(0, len(titles), 50):
        batch = titles[i:i+50]
        d = api({'action': 'query', 'titles': '|'.join(batch), 'prop': 'revisions',
                 'rvprop': 'content', 'rvslots': 'main', 'format': 'json'})
        for pid, p in d['query']['pages'].items():
            if 'revisions' not in p:
                print('  ! 无内容:', p.get('title'), flush=True); continue
            wt = p['revisions'][0]['slots']['main']['*']
            av = re.search(r'^\|头像=(.*)$', wt, re.M)
            sk = re.search(r'^\|皮肤0图片=(.*)$', wt, re.M)
            out[p['title']] = {
                'avatar': av.group(1).strip() if av else '',
                'skin': sk.group(1).strip() if sk else ''}
        print('  wikitext %d/%d' % (min(i+50, len(titles)), len(titles)), flush=True)
        time.sleep(3)
    return out

def missing_files(names):
    """批量查 File:<名>.png 存在性 → 返回缺失集合"""
    need = sorted({n for n in names if n})
    need = [n if n.lower().endswith('.png') else n + '.png' for n in need]
    miss = set()
    for i in range(0, len(need), 50):
        batch = need[i:i+50]
        d = api({'action': 'query', 'titles': '|'.join('File:' + f for f in batch), 'format': 'json'})
        for pid, p in d['query']['pages'].items():
            if 'missing' in p or int(pid) < 0:
                # API 返回本地化前缀「文件:」或「File:」，统一剥离
                base = re.sub(r'^(File|文件)\s*:', '', p.get('title', ''))
                miss.add(base)
        print('  文件存在性 %d/%d' % (min(i+50, len(need)), len(need)), flush=True)
        time.sleep(3)
    return miss

def upload_png(png_path, target_filename, comment):
    content = open(png_path, 'rb').read()
    for attempt in range(3):
        boundary = uuid.uuid4().hex
        body = bytearray()
        fields = {'action': 'upload', 'filename': target_filename,
                  'comment': comment, 'token': get_token(refresh=(attempt > 0)),
                  'format': 'json', 'ignorewarnings': '1'}
        for k, v in fields.items():
            body += ('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n' % (boundary, k, v)).encode('utf-8')
        body += ('--%s\r\nContent-Disposition: form-data; name="file"; filename="%s"\r\n'
                 'Content-Type: image/png\r\n\r\n' % (boundary, os.path.basename(png_path))).encode('utf-8')
        body += content + b'\r\n' + ('--%s--\r\n' % boundary).encode()
        req = urllib.request.Request(API, data=bytes(body),
            headers={'Content-Type': 'multipart/form-data; boundary=%s' % boundary})
        try:
            r = opener.open(req, timeout=180)
            d = json.loads(r.read().decode('utf-8'))
            if d.get('upload', {}).get('result') == 'Success':
                return True
            if 'badtoken' in json.dumps(d):
                get_token(refresh=True); continue
            print('    upload err:', json.dumps(d, ensure_ascii=False)[:150], flush=True)
            return False
        except Exception as e:
            if attempt == 2:
                print('    upload fail:', str(e)[:80], flush=True); return False
            time.sleep(30)
    return False

def purge_batch(titles):
    for i in range(0, len(titles), 20):
        batch = titles[i:i+20]
        params = {'action': 'purge', 'forcelinkupdate': '1', 'token': get_token(), 'format': 'json',
                  'titles': '|'.join(batch)}
        try:
            d = api(params, is_post=True)
            n = len(d.get('purge', [])) if isinstance(d.get('purge'), list) else -1
            print('  purge %d-%d OK(%s)' % (i, i + len(batch), n), flush=True)
        except Exception as e:
            print('  purge fail:', str(e)[:80], flush=True)
        time.sleep(5)

def crop_avatar(card_bytes):
    from PIL import Image
    img = Image.open(io.BytesIO(card_bytes)).convert('RGBA')
    w, h = img.size
    side = min(w, h)
    box = img.crop((0, 0, side, side))
    box = box.resize((256, 256), Image.LANCZOS)
    box.save(TMP_AV)

def main(mode):
    targets = load_targets()
    print('GameKee 匹配目标: %d 页' % len(targets), flush=True)
    tmap = {w: (g, u) for w, g, u in targets}
    done = json.load(open(DONE_FILE, encoding='utf-8')) if os.path.exists(DONE_FILE) else {}

    refs = pull_refs(list(tmap.keys()))
    # 需要检查的引用名
    allrefs = []
    for t, r in refs.items():
        allrefs += [r['avatar'], r['skin']]
    miss = missing_files(allrefs)
    print('缺失引用文件: %d 个' % len(miss), flush=True)

    order = list(tmap.keys())
    if mode == 'sample':
        order = order[:3]
    else:
        order = [t for t in order if t not in done or not done[t].get('all')]

    purged, uploaded = [], 0
    for idx, title in enumerate(order):
        gname, icon_url = tmap[title]
        r = refs.get(title, {})
        st = done.setdefault(title, {})
        need_skin = r['skin'] and (r['skin'] if r['skin'].lower().endswith('.png') else r['skin'] + '.png') in miss
        need_av = r['avatar'] and (r['avatar'] if r['avatar'].lower().endswith('.png') else r['avatar'] + '.png') in miss
        if not need_skin and not need_av:
            st['all'] = True; print('[%d/%d] %s 无缺引用跳过' % (idx+1, len(order), title), flush=True); continue
        try:
            raw = gk_fetch(icon_url)
            open(TMP_CARD, 'wb').write(raw)
        except Exception as e:
            print('[%d/%d] %s 下载失败: %s' % (idx+1, len(order), title, str(e)[:60]), flush=True)
            st['dl_fail'] = True; continue
        cmt = '角色图片补全：卡面立绘（来源：GameKee百科）'
        if need_skin:
            fn = r['skin'] if r['skin'].lower().endswith('.png') else r['skin'] + '.png'
            ok = upload_png(TMP_CARD, fn, cmt)
            st['skin'] = ok; uploaded += ok
            print('[%d/%d] %s 皮肤0 -> %s %s' % (idx+1, len(order), title, fn, 'OK' if ok else 'FAIL'), flush=True)
            time.sleep(3)
        if need_av:
            fn = r['avatar'] if r['avatar'].lower().endswith('.png') else r['avatar'] + '.png'
            try:
                crop_avatar(raw)
                ok = upload_png(TMP_AV, fn, cmt)
            except Exception as e:
                print('    裁剪失败:', str(e)[:60], flush=True); ok = False
            st['avatar'] = ok; uploaded += ok
            print('[%d/%d] %s 头像 -> %s %s' % (idx+1, len(order), title, fn, 'OK' if ok else 'FAIL'), flush=True)
            time.sleep(3)
        st['all'] = bool(st.get('skin', need_skin is False) and st.get('avatar', need_av is False)) and not st.get('dl_fail')
        json.dump(done, open(DONE_FILE, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        purged.append(title)

    print('上传完成: %d 个文件' % uploaded, flush=True)
    if purged:
        time.sleep(5)
        purge_batch(purged)
        json.dump(done, open(DONE_FILE, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('DONE', flush=True)

if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'all')
