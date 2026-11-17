# -*- coding: utf-8 -*-
"""验证点赞/收藏通知 + 实时推送 + 收藏数展示"""
import sys, os, time, json, re, secrets, urllib.request, urllib.error, urllib.parse
import gevent
from gevent import monkey
monkey.patch_all()
import socketio

BASE = r'E:\项目\论坛\ai_forum'
sys.path.insert(0, os.path.join(BASE, 'backend', 'services'))
sys.path.insert(0, os.path.join(BASE, 'backend', 'core'))
sys.path.insert(0, os.path.join(BASE, 'backend', 'config'))
from db import get_cursor

BASE_URL = 'http://127.0.0.1:5000'
UNIQ = str(int(time.time()))[-6:]
AUTHOR = 'e2e_auth_' + UNIQ
ACTOR = 'e2e_actor_' + UNIQ
PWD = os.environ.get('TEST_E2E_PWD') or secrets.token_hex(6)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    def __init__(self):
        self.cookies = {}
        self._csrf = None
        self.opener = urllib.request.build_opener(NoRedirect)

    def _fetch_csrf(self):
        """从 /login 页提取会话级 CSRF 令牌（登录前后该页均 200）并缓存"""
        if self._csrf is None:
            _, content, _ = self._req('/login', method='GET')
            m = re.search(r'name="_csrf_token"\s+value="([^"]+)"', content)
            if not m:
                m = re.search(r"window\.CSRF_TOKEN = '([^']+)'", content)
                m = re.search(r'<meta name="csrf-token" content="([^"]+)"', content)
            self._csrf = m.group(1) if m else None
        return self._csrf

    def _req(self, path, data=None, method=None, extra_headers=None):
        effective_method = method if method is not None else ('POST' if data is not None else 'GET')
        if effective_method == 'POST':
            # CSRF 感知：所有 POST 自动附带会话令牌（data=None 的 POST 同样注入）
            d = dict(data) if isinstance(data, dict) else {}
            d.setdefault('_csrf_token', self._fetch_csrf())
            data = d
        body = urllib.parse.urlencode(data or {}).encode() if data else None
        if method is None:
            method = 'POST' if body is not None else 'GET'
        headers = {'User-Agent': 'verify'}
        if extra_headers:
            headers.update(extra_headers)
        if self.cookies:
            headers['Cookie'] = '; '.join('%s=%s' % kv for kv in self.cookies.items())
        if body is not None:
            headers['Content-Type'] = 'application/x-www-form-urlencoded'
        req = urllib.request.Request(BASE_URL + path, data=body, headers=headers, method=method)
        try:
            resp = self.opener.open(req, timeout=30)
            code, content, hdrs = resp.status, resp.read().decode('utf-8', 'ignore'), resp.headers
        except urllib.error.HTTPError as e:
            code, content, hdrs = e.code, e.read().decode('utf-8', 'ignore'), e.headers
        for c in hdrs.get_all('Set-Cookie') or []:
            kv = c.split(';')[0]
            if '=' in kv:
                k, v = kv.split('=', 1)
                self.cookies[k] = v
        return code, content, hdrs

    def post(self, path, data=None):
        code, content, hdrs = self._req(path, data, method='POST')
        return code, content

    def loc(self, path, data=None):
        code, content, hdrs = self._req(path, data)
        return code, hdrs.get('Location'), content

    def ajax(self, path, data=None):
        code, content, hdrs = self._req(path, data, method='POST',
                                        extra_headers={'X-Requested-With': 'XMLHttpRequest'})
        return code, hdrs.get('Location'), content

    def get(self, path):
        code, content, hdrs = self._req(path)
        return code, content


def register(client, username, nickname):
    return client.post('/register', {'username': username, 'password': PWD, 'nickname': nickname})


def login(client, username):
    return client.post('/login', {'username': username, 'password': PWD})


def db_q(sql, params=()):
    with get_cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


results = []
def rec(name, ok, detail):
    results.append((name, ok, detail))
    print('[%s] %s: %s' % ('PASS' if ok else 'FAIL', name, detail))


# ===== 准备：注册两个用户，作者发帖 =====
anon = Client()
r1 = register(anon, AUTHOR, '验证作者'); print('注册作者:', r1[0], r1[1][:60])
r2 = register(anon, ACTOR, '验证操作者'); print('注册操作者:', r2[0], r2[1][:60])
author_c = Client(); print('作者登录:', login(author_c, AUTHOR)[0], 'cookies:', list(author_c.cookies.keys()))
actor_c = Client(); print('操作者登录:', login(actor_c, ACTOR)[0], 'cookies:', list(actor_c.cookies.keys()))
code, content = actor_c.get('/')
print('操作者GET /:', code, '含退出登录:', '退出登录' in content)
code, content = author_c.get('/')
print('作者GET /:', code, '含退出登录:', '退出登录' in content, '含登录表单:', 'name="username"' in content)
code, loc, content = author_c.loc('/post/new', {'title': 'E2E实时验证帖' + UNIQ, 'content': '用于验证点赞收藏通知与实时推送的内容', 'board_id': '1'})
print('发帖返回:', code, 'Location:', loc)
rows = db_q("SELECT post_id FROM post WHERE title LIKE ? ORDER BY post_id DESC LIMIT 1", ('%E2E实时验证帖' + UNIQ + '%',))
post_id = rows[0][0]
rec('准备-发帖', bool(post_id), 'post_id=%s' % post_id)

# ===== 作者 socketio 连接 + 加入房间，等待事件 =====
ev_notify_like = gevent.event.Event(); ev_notify_fav = gevent.event.Event()
ev_like_update = gevent.event.Event(); ev_fav_update = gevent.event.Event()
captured = {'notify_like': None, 'notify_fav': None, 'like_update': None, 'fav_update': None}

sio = socketio.Client()
conn_ok = False
try:
    sio.connect(BASE_URL, headers={'Cookie': '; '.join('%s=%s' % kv for kv in author_c.cookies.items())},
                transports=['websocket'], wait_timeout=8)
    conn_ok = True
except Exception as e:
    rec('作者socket连接', False, str(e))
if conn_ok:
    rec('作者socket连接', True, 'sid=%s' % sio.sid)
    sio.emit('join_post', {'post_id': post_id})
    gevent.sleep(1)

    @sio.on('notification')
    def on_notify(d):
        if d.get('type') == 'like' and not captured['notify_like']:
            captured['notify_like'] = d; ev_notify_like.set()
        elif d.get('type') == 'favorite' and not captured['notify_fav']:
            captured['notify_fav'] = d; ev_notify_fav.set()

    @sio.on('like_update')
    def on_like_update(d):
        captured['like_update'] = d; ev_like_update.set()

    @sio.on('favorite_update')
    def on_fav_update(d):
        captured['fav_update'] = d; ev_fav_update.set()

    # ===== 操作者点赞（AJAX） =====
    code, loc, content = actor_c.ajax('/post/%d/like' % post_id)
    print('点赞AJAX: code=%s loc=%s' % (code, loc))
    try:
        j = json.loads(content)
    except Exception:
        j = {}
    rec('点赞AJAX返回', j.get('ok') is True and j.get('liked') is True and j.get('like_count') == 1,
        'code=%s json=%s' % (code, j))
    got = ev_notify_like.wait(timeout=10)
    rec('作者收到点赞通知推送', got and captured['notify_like']['type'] == 'like' and captured['notify_like']['unread'] >= 1,
        '收到=%s data=%s' % (got, captured['notify_like']))
    got2 = ev_like_update.wait(timeout=10)
    rec('作者收到like_update广播', got2 and captured['like_update'].get('like_count') == 1,
        '收到=%s data=%s' % (got2, captured['like_update']))

    # ===== 操作者收藏（AJAX） =====
    code, loc, content = actor_c.ajax('/post/%d/favorite' % post_id)
    try:
        j2 = json.loads(content)
    except Exception:
        j2 = {}
    rec('收藏AJAX返回', j2.get('ok') is True and j2.get('favorited') is True and j2.get('favorite_count') == 1,
        'code=%s json=%s' % (code, j2))
    got3 = ev_notify_fav.wait(timeout=10)
    rec('作者收到收藏通知推送', got3 and captured['notify_fav']['type'] == 'favorite' and captured['notify_fav']['unread'] >= 2,
        '收到=%s data=%s' % (got3, captured['notify_fav']))
    got4 = ev_fav_update.wait(timeout=10)
    rec('作者收到favorite_update广播', got4 and captured['fav_update'].get('favorite_count') == 1,
        '收到=%s data=%s' % (got4, captured['fav_update']))
    sio.disconnect()

# ===== 通知落库检查 =====
rows = db_q("SELECT type, content FROM notification WHERE post_id=? AND type IN ('like','favorite')", (post_id,))
types = [r[0] for r in rows]
rec('通知落库(like+favorite)', 'like' in types and 'favorite' in types, 'types=%s' % types)

# ===== 收藏数渲染检查（fav-count span 内为实际收藏数；文案随当前用户收藏态） =====
code, content = author_c.get('/post/%d' % post_id)
import re as _re
m = _re.search(r'id="fav-count">(\d+)<', content)
m2 = _re.search(r'id="fav-text">([^<]+)<', content)
fav_shown = m.group(1) if m else None
fav_text_author = m2.group(1) if m2 else None
code, content2 = actor_c.get('/post/%d' % post_id)
m3 = _re.search(r'id="fav-text">([^<]+)<', content2)
fav_text_actor = m3.group(1) if m3 else None
rec('收藏数页面展示',
    fav_shown == '1' and fav_text_author == '收藏' and fav_text_actor == '已收藏',
    'fav-count=%s 作者态=%s 收藏者态=%s' % (fav_shown, fav_text_author, fav_text_actor))

# ===== 清理 =====
with get_cursor() as cur:
    cur.execute("DELETE FROM post WHERE post_id=?", (post_id,))
    cur.execute("DELETE FROM `user` WHERE username IN (?, ?)", (AUTHOR, ACTOR))
print('\n=== 汇总 ===')
fails = [n for n, ok, _ in results if not ok]
print('共 %d 项, 失败 %d 项: %s' % (len(results), len(fails), fails if fails else '无'))
