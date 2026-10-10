# -*- coding: utf-8 -*-
"""Wiki发布 Step4：上传153张新角色页到 BWIKI（wiki.biligame.com/bfzy）

流程: 读 _wiki_cookie.txt → userinfo 验证登录 → csrf token → 逐页 action=edit(createonly=1)
安全: createonly 绝不覆盖已有页 | 断点续传 _wiki_uploaded.json | 失败不中断，末尾汇总
用法:
  python wiki_upload.py --dry-run      # 本地检查 + Cookie登录验证，不提交
  python wiki_upload.py --limit 3      # 试传前3页
  python wiki_upload.py                # 全量上传（自动跳过已成功页）
"""
import json, os, re, sys, time

sys.stdout.reconfigure(encoding="utf-8")
import requests

API = "https://wiki.biligame.com/bfzy/api.php"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
COOKIE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_wiki_cookie.txt")
PAGES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_wiki_pages.json")
DONE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_wiki_uploaded.json")
SUMMARY = "补充角色页面：技能/天赋/等级数值整理自官方公告与GameKee"
INTERVAL = 0.8  # 秒，每页间隔


def new_session():
    s = requests.Session()
    s.trust_env = False  # 绕过系统代理
    s.headers["User-Agent"] = UA
    return s


def load_cookie():
    if not os.path.exists(COOKIE_FILE):
        return None
    raw = open(COOKIE_FILE, encoding="utf-8").read().strip()
    if not raw:
        return None
    return raw


def api_get(s, **params):
    params["format"] = "json"
    r = s.get(API, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def api_post(s, **params):
    params["format"] = "json"
    r = s.post(API, data=params, timeout=60)
    r.raise_for_status()
    return r.json()


def check_login(s):
    """返回 (已登录?, 用户名/原因)"""
    try:
        d = api_get(s, action="query", meta="userinfo")
    except Exception as e:
        return False, f"网络请求失败: {e}"
    ui = d.get("query", {}).get("userinfo", {})
    if ui.get("id") == 0 or ui.get("anon"):
        return False, f"未登录（Cookie 失效或缺失）: {ui}"
    return True, ui.get("name", "?")


def get_csrf(s):
    d = api_get(s, action="query", meta="tokens", type="csrf")
    tok = d.get("query", {}).get("tokens", {}).get("csrftoken")
    if not tok or tok == "+\\":
        raise RuntimeError("无法获取编辑令牌（可能未登录）")
    return tok


def main():
    args = sys.argv[1:]
    dry = "--dry-run" in args
    limit = None
    if "--limit" in args:
        i = args.index("--limit")
        limit = int(args[i + 1])

    # ---- 1. 数据 ----
    if not os.path.exists(PAGES_FILE):
        print("[E] 缺少 _wiki_pages.json，先跑 wiki_export.py"); sys.exit(1)
    pages = json.load(open(PAGES_FILE, encoding="utf-8"))["pages"]
    done = {}
    if os.path.exists(DONE_FILE):
        done = json.load(open(DONE_FILE, encoding="utf-8"))
    todo = [n for n in pages if n not in done]
    print(f"[i] 共 {len(pages)} 页 | 已上传 {len(done)} | 待上传 {len(todo)}")
    if limit is not None:
        todo = todo[:limit]
        print(f"[i] --limit {limit} 模式，本次只处理: {todo}")

    # ---- 2. Cookie / 登录 ----
    cookie = load_cookie()
    s = new_session()
    if cookie:
        s.headers["Cookie"] = cookie
    ok, who = check_login(s)
    if not ok:
        print(f"[!] 登录验证失败: {who}")
        print("    请浏览器登录 wiki.biligame.com → F12 → Network → 任一请求 → 复制完整 Cookie 请求头")
        print(f"    存入: {COOKIE_FILE}")
        sys.exit(2)
    print(f"[✓] 登录身份: {who}")

    if dry:
        print("[✓] dry-run：登录验证通过，本地检查如下（不提交任何页面）")
        bad = [n for n, t in pages.items() if "{{角色|" not in t or len(t) < 120]
        print(f"    模板完整性: {'全部通过' if not bad else bad}")
        skills = sum(1 for t in pages.values() if re.search(r"\|技能说明=\S", t))
        lv = sum(1 for t in pages.values() if re.search(r"\|Lv\.1攻击力=\S", t))
        print(f"    有技能说明: {skills}/{len(pages)} | 有Lv数值: {lv}/{len(pages)}")
        sys.exit(0)

    # ---- 3. token ----
    tok = get_csrf(s)
    print("[✓] 已取得编辑令牌")

    # ---- 4. 逐页上传 ----
    ok_cnt, pend_cnt, fail = 0, 0, []
    for i, name in enumerate(todo, 1):
        try:
            d = api_post(s, action="edit", title=name, text=pages[name],
                         createonly=1, summary=SUMMARY, token=tok, bot=1)
        except Exception as e:
            print(f"  [{i}/{len(todo)}] {name} → 请求异常: {e}")
            fail.append((name, str(e)))
            time.sleep(INTERVAL)
            continue
        if d.get("edit", {}).get("result") == "Success":
            done[name] = {"pageid": d["edit"].get("pageid"), "revid": d["edit"].get("newrevid"),
                          "time": time.strftime("%Y-%m-%d %H:%M:%S")}
            json.dump(done, open(DONE_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            ok_cnt += 1
            print(f"  [{i}/{len(todo)}] ✓ {name} (pageid={done[name]['pageid']})")
        elif d.get("error", {}).get("code") == "moderation-edit-queued":
            # BWIKI审核队列：编辑已提交待人工批准，记录防重传，审核通过后页面自动创建
            done[name] = {"status": "pending_moderation", "time": time.strftime("%Y-%m-%d %H:%M:%S")}
            json.dump(done, open(DONE_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            pend_cnt += 1
            print(f"  [{i}/{len(todo)}] ⏳ {name} (已进审核队列，不重传)")
        else:
            code = d.get("error", {}).get("code", "?")
            info = d.get("error", {}).get("info", d)[:80]
            print(f"  [{i}/{len(todo)}] ✗ {name} → {code}: {info}")
            fail.append((name, code))
        time.sleep(INTERVAL)

    # ---- 5. 汇总 ----
    print(f"\n==== 完成: 成功 {ok_cnt} | 待审核 {pend_cnt} | 失败 {len(fail)} | 累计 {len(done)}/{len(pages)} ====")
    if fail:
        print("失败清单:")
        for n, c in fail:
            print(f"  - {n} ({c})")
        sys.exit(3)


if __name__ == "__main__":
    main()
