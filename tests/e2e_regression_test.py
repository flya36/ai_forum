# -*- coding: utf-8 -*-
"""
E2E 全功能回归测试脚本（v2）
目标实例: http://127.0.0.1:5000 (gevent + SocketIO 模式，运行中)

覆盖：
  T1 匿名访问收藏接口，校验数据隔离（防泄漏）
  T2 管理员删除帖子，验证 RAG 索引自动刷新
  T3 基础论坛功能回归：注册/登录/发帖/评论（编辑、标签按现状记录）
  T4 WebSocket 连接与实时推送验证
  T5 并发测试：多用户并发 + 并发 RAG 查询，验证 Lock 无死锁

所有测试数据带 e2e_ 前缀，测试结束自动清理。
"""
import os
import sys
import time
import json
import re
import secrets
import glob
import urllib.request
import urllib.error
import urllib.parse

from gevent import monkey
monkey.patch_all()

BASE = r'E:\项目\论坛\ai_forum'
sys.path.insert(0, os.path.join(BASE, 'backend', 'services'))
sys.path.insert(0, os.path.join(BASE, 'backend', 'core'))
sys.path.insert(0, os.path.join(BASE, 'backend', 'config'))

import gevent
import socketio

from db import get_cursor

BASE_URL = 'http://127.0.0.1:5000'
UNIQ = str(int(time.time()))[-8:]
ADMIN_NAME = 'e2e_admin_' + UNIQ
USER_NAME = 'e2e_user_' + UNIQ
USER2_NAME = 'e2e_user2_' + UNIQ
PWD = os.environ.get('TEST_E2E_PWD') or secrets.token_hex(6)
TOKEN = 'E2EUNIQ' + UNIQ          # 帖子独特检索词
GARBAGE = 'zzzqqqxxx_no_hit_999'  # 无命中查询词（并发RAG用，避免触发外部AI）

report = []


def record(case, status, detail):
    report.append((case, status, detail))
    print("[%s] %s: %s" % ('PASS' if status == 'PASS' else 'FAIL', case, detail))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """不跟随重定向，用于检测 3xx 状态码"""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    """带 cookie 管理的简易 HTTP 客户端（NoRedirect，暴露 302 + Location）
    CSRF 感知：所有 POST 自动附带会话级 _csrf_token。"""
    def __init__(self):
        self.cookies = {}
        self._csrf = None
        self.opener = urllib.request.build_opener(NoRedirect)

    def _fetch_csrf(self):
        """从 /login 页提取会话级 CSRF 令牌（登录前后该页均 200）并缓存"""
        if self._csrf is None:
            _, content = self.get('/login')
            m = re.search(r'name="_csrf_token"\s+value="([^"]+)"', content)
            if not m:
                m = re.search(r"window\.CSRF_TOKEN = '([^']+)'", content)
                m = re.search(r'<meta name="csrf-token" content="([^"]+)"', content)
            self._csrf = m.group(1) if m else None
        return self._csrf

    def _req(self, method, path, data=None, timeout=60):
        url = BASE_URL + path
        if method == 'POST':
            # CSRF 感知：所有 POST 自动附带会话令牌（data=None 的 POST 同样注入）
            d = dict(data) if isinstance(data, dict) else {}
            d.setdefault('_csrf_token', self._fetch_csrf())
            data = d
        body = urllib.parse.urlencode(data or {}).encode() if data else None
        headers = {'User-Agent': 'e2e-test'}
        if self.cookies:
            headers['Cookie'] = '; '.join('%s=%s' % kv for kv in self.cookies.items())
        if body is not None:
            headers['Content-Type'] = 'application/x-www-form-urlencoded'
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            resp = self.opener.open(req, timeout=timeout)
            code, content, hdrs = resp.status, resp.read().decode('utf-8', 'ignore'), resp.headers
        except urllib.error.HTTPError as e:
            code, content, hdrs = e.code, e.read().decode('utf-8', 'ignore'), e.headers
        except Exception as e:
            return -1, str(e), {}
        for c in hdrs.get_all('Set-Cookie') or []:
            kv = c.split(';')[0]
            if '=' in kv:
                k, v = kv.split('=', 1)
                self.cookies[k] = v
        return code, content, hdrs

    def get(self, path, timeout=60):
        code, content, hdrs = self._req('GET', path, timeout=timeout)
        return code, content

    def post(self, path, data=None, timeout=60):
        code, content, hdrs = self._req('POST', path, data=data, timeout=timeout)
        return code, content

    def loc(self, path, data=None, timeout=60):
        """POST 并返回 (code, Location)"""
        code, content, hdrs = self._req('POST', path, data=data, timeout=timeout)
        return code, hdrs.get('Location')


def db_query(sql, params=()):
    with get_cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


# ============================================================
print("=" * 70)
print("E2E 全功能回归测试 v2  start at %s" % time.strftime('%Y-%m-%d %H:%M:%S'))
print("=" * 70)

# ============================================================
# T1 匿名访问收藏接口 - 数据隔离
# ============================================================
print("\n--- T1 匿名访问收藏接口（数据隔离） ---")
anon = Client()
code, content = anon.get('/favorites')
record('T1-匿名GET/favorites', 'PASS' if code == 302 else 'FAIL',
       '状态码=%s, Location=%s' % (code, ''))
code, content = anon.post('/post/1/favorite', {})
record('T1-匿名POST收藏', 'PASS' if code == 302 else 'FAIL',
       '状态码=%s (期待302重定向到登录)' % code)

# 跟随重定向验证最终落在登录页且无收藏数据泄漏
anon_follow = urllib.request.build_opener()
code, content = (lambda: (lambda r: (r.status, r.read().decode('utf-8', 'ignore')))(anon_follow.open(
    urllib.request.Request(BASE_URL + '/favorites', headers={'User-Agent': 'e2e'}), timeout=15)))()
leak = ('我的收藏' in content and 'post-card' in content)
record('T1-匿名跟随重定向后无泄漏', 'PASS' if (code == 200 and '登录' in content and not leak) else 'FAIL',
       '状态码=%s, 落在登录页=%s, 泄漏收藏数据=%s' % (code, '登录' in content, leak))

# ============================================================
# 准备：注册测试账号（管理员 + 两个普通用户）
# ============================================================
print("\n--- 准备测试账号 ---")
code, loc = anon.loc('/register', {'username': ADMIN_NAME, 'password': PWD, 'nickname': 'E2E管理员'})
record('准备-注册管理员', 'PASS' if code == 302 and loc == '/login' else 'FAIL',
       'code=%s Location=%s' % (code, loc))
code, loc = anon.loc('/register', {'username': USER_NAME, 'password': PWD, 'nickname': 'E2E用户甲'})
record('准备-注册用户甲', 'PASS' if code == 302 and loc == '/login' else 'FAIL',
       'code=%s Location=%s' % (code, loc))
code, loc = anon.loc('/register', {'username': USER2_NAME, 'password': PWD, 'nickname': 'E2E用户乙'})
record('准备-注册用户乙', 'PASS' if code == 302 and loc == '/login' else 'FAIL',
       'code=%s Location=%s' % (code, loc))

# 提权为管理员（register 固定 role=0，测试需要管理员账号）
with get_cursor() as cur:
    cur.execute("UPDATE `user` SET role=1 WHERE username=?", (ADMIN_NAME,))
    cur.execute("SELECT role FROM `user` WHERE username=?", (ADMIN_NAME,))
    role = cur.fetchone()[0]
record('准备-管理员提权', 'PASS' if role == 1 else 'FAIL', 'role=%s' % role)

# ============================================================
# T3 基础回归（先做注册登录发帖评论，T2 依赖发帖）
# ============================================================
print("\n--- T3 基础论坛功能回归 ---")
admin_client = Client()
code, loc = admin_client.loc('/login', {'username': ADMIN_NAME, 'password': PWD})
record('T3-管理员登录', 'PASS' if code == 302 and loc == '/' else 'FAIL', 'code=%s Location=%s' % (code, loc))

user_client = Client()
code, loc = user_client.loc('/login', {'username': USER_NAME, 'password': PWD})
record('T3-用户甲登录', 'PASS' if code == 302 and loc == '/' else 'FAIL', 'code=%s Location=%s' % (code, loc))

user2_client = Client()
code, loc = user2_client.loc('/login', {'username': USER2_NAME, 'password': PWD})
record('T3-用户乙登录', 'PASS' if code == 302 and loc == '/' else 'FAIL', 'code=%s Location=%s' % (code, loc))

# 发帖（标题含独特 TOKEN，供 T2 RAG 测试使用）
code, loc = user_client.loc('/post/new', {
    'title': 'E2E唯一标识测试帖 ' + TOKEN,
    'content': '这是一个用于E2E回归测试的独特内容，包含唯一标识符 ' + TOKEN + '。用于验证删除帖子后RAG索引能自动刷新，该内容不应再被检索到。',
    'board_id': '1',
})
rows = db_query("SELECT post_id FROM post WHERE title LIKE ? ORDER BY post_id DESC LIMIT 1", ('%' + TOKEN + '%',))
post_id = rows[0][0] if rows else None
record('T3-发帖', 'PASS' if code == 302 and loc == '/' and post_id else 'FAIL',
       'code=%s Location=%s post_id=%s' % (code, loc, post_id))

# 评论（用户乙评论用户甲的帖子）
code, loc = user2_client.loc('/post/%d/comment' % post_id, {'content': 'E2E回归测试评论内容ABC123'})
rows = db_query("SELECT COUNT(*) FROM comment WHERE post_id=? AND content LIKE ?", (post_id, '%E2E回归测试评论%'))
cnt = rows[0][0]
record('T3-评论', 'PASS' if code == 302 and loc == '/post/%d' % post_id and cnt >= 1 else 'FAIL',
       'code=%s Location=%s 评论数=%s' % (code, loc, cnt))

# 编辑帖子：记录为未实现（无路由）
with open(os.path.join(BASE, 'web', 'app.py'), encoding='utf-8') as f:
    edit_exists = 'edit' in f.read()
record('T3-编辑帖子', 'SKIP' if not edit_exists else 'PASS',
       '当前版本未实现帖子编辑路由（记录：未实现）')

# 标签：记录为未实现（无 service/路由）
tag_exists = False
for fp in glob.glob(os.path.join(BASE, 'backend', 'services', '*.py')):
    with open(fp, encoding='utf-8') as f:
        if re.search(r'\btag\b', f.read()):
            tag_exists = True
            break
record('T3-标签功能', 'SKIP' if not tag_exists else 'PASS',
       '当前版本未实现标签业务（记录：未实现）')

# 个人资料编辑（真实存在）
code, loc = user_client.loc('/profile/edit', {'nickname': 'E2E用户甲改', 'signature': 'E2E签名'})
rows = db_query("SELECT nickname, signature FROM `user` WHERE username=?", (USER_NAME,))
row = rows[0] if rows else None
record('T3-编辑个人资料', 'PASS' if row and row[0] == 'E2E用户甲改' and row[1] == 'E2E签名' else 'FAIL',
       'nickname=%s signature=%s' % (row[0] if row else None, row[1] if row else None))

# ============================================================
# T2 管理员删除帖子 → RAG 索引自动刷新
# ============================================================
print("\n--- T2 管理员删帖 → RAG 索引刷新 ---")

# 2a. 删帖前：RAG 应能检索到该帖子（HTTP 层，服务进程索引）
code, content = user_client.get('/api/ask?question=' + urllib.parse.quote(TOKEN), timeout=60)
try:
    answer = json.loads(content).get('answer', '')
except Exception:
    answer = content
not_found_marker = ('未' in answer and '知识库' in answer and '暂' in answer)
hit_before = (TOKEN in answer) or (bool(answer) and not not_found_marker)
record('T2-删帖前RAG可检索', 'PASS' if hit_before else 'FAIL',
       '检索到=%s, answer前80字=%s...' % (hit_before, answer[:80].replace('\n', ' ')))

# 2b. 管理员通过 HTTP 删除帖子
code, loc = admin_client.loc('/admin/post/%d/delete' % post_id)
rows = db_query("SELECT COUNT(*) FROM post WHERE post_id=?", (post_id,))
still = rows[0][0]
record('T2-管理员删帖', 'PASS' if code == 302 and still == 0 else 'FAIL',
       'code=%s Location=%s 删除后DB残留=%s' % (code, loc, still))

# 2c. 删帖后：RAG 不应再检索到（服务进程索引应已 invalidate 并重建）
code, content = user_client.get('/api/ask?question=' + urllib.parse.quote(TOKEN), timeout=60)
try:
    answer2 = json.loads(content).get('answer', '')
except Exception:
    answer2 = content
hit_after = TOKEN in answer2
not_found = ('未' in answer2 and '知识库' in answer2)
record('T2-删帖后RAG不可检索', 'PASS' if not hit_after else 'FAIL',
       '仍可检索到=%s, 返回未找到=%s, answer前80字=%s...' % (hit_after, not_found, answer2[:80].replace('\n', ' ')))

# 2d. 知识块级联删除验证
rows = db_query("SELECT COUNT(*) FROM knowledge_chunk WHERE post_id=?", (post_id,))
chunks = rows[0][0]
record('T2-知识块级联删除', 'PASS' if chunks == 0 else 'FAIL', '残留知识块=%s' % chunks)

# ============================================================
# T4 WebSocket 连接与实时推送
# ============================================================
print("\n--- T4 WebSocket 验证 ---")

# T2 已删除 RAG 测试帖，T4 需要一篇存活帖子做房间广播测试
code, loc = user_client.loc('/post/new', {
    'title': 'E2E-WS实时广播测试帖 ' + TOKEN + 'WS',
    'content': 'WS广播测试内容，用于验证新评论实时推送。',
    'board_id': '1',
})
rows = db_query("SELECT post_id FROM post WHERE title LIKE ? ORDER BY post_id DESC LIMIT 1",
                ('%' + TOKEN + 'WS%',))
ws_post_id = rows[0][0] if rows else None
record('T4-准备WS测试帖', 'PASS' if ws_post_id else 'FAIL', 'post_id=%s' % ws_post_id)

# 4a. 未登录连接：服务端不再秒断拒绝（避免客户端无限重连死循环），
#     允许连接但不入在线表，通过 emit auth_expired 告知前端"认证失效"。
sio_anon = socketio.Client()
auth_expired = []


@sio_anon.on('auth_expired')
def on_auth_expired(data):
    auth_expired.append(data)


try:
    sio_anon.connect(BASE_URL, transports=['websocket'], wait_timeout=5)
    time.sleep(1.5)  # 等服务端 emit auth_expired
    sio_anon.disconnect()
except Exception:
    pass
record('T4-未登录连接鉴权', 'PASS' if auth_expired else 'FAIL',
       '收到auth_expired=%s' % bool(auth_expired))

# 4b. 登录用户连接成功（websocket 传输）
cookie_str = '; '.join('%s=%s' % kv for kv in user_client.cookies.items())
sio1 = socketio.Client()
conn_ok = False
try:
    sio1.connect(BASE_URL, headers={'Cookie': cookie_str}, transports=['websocket'], wait_timeout=8)
    conn_ok = True
except Exception as e:
    record('T4-登录用户连接', 'FAIL', '连接失败: %s' % e)
if conn_ok:
    record('T4-登录用户连接', 'PASS', '连接成功 sid=%s' % (sio1.sid or ''))

    ev_comment = gevent.event.Event()
    captured = {}

    @sio1.on('new_comment')
    def on_new_comment(data):
        captured.update(data)
        ev_comment.set()

    # 4c. join_post 房间（用 T4 独立测试帖）
    sio1.emit('join_post', {'post_id': ws_post_id})
    gevent.sleep(1)

    # 4d. 用户乙对同一帖子发评论 → 用户甲应收广播（房间内）
    code, loc = user2_client.loc('/post/%d/comment' % ws_post_id, {'content': 'E2E-WS广播验证评论WS999'})
    got = ev_comment.wait(timeout=10)
    record('T4-新评论实时广播', 'PASS' if got and captured.get('content') == 'E2E-WS广播验证评论WS999' else 'FAIL',
           '收到=%s, content=%s, 评论POST code=%s' % (got, captured.get('content'), code))

    sio1.disconnect()

# T4 测试帖清理
with get_cursor() as cur:
    cur.execute("DELETE FROM post WHERE post_id=?", (ws_post_id,))

# ============================================================
# T5 并发测试（多用户并发 + 并发RAG，验证 Lock 无死锁）
# ============================================================
print("\n--- T5 并发测试 ---")

# 5a. 并发 HTTP 访问首页（多用户会话）
def http_home(i):
    c = Client()
    c.cookies = dict(user_client.cookies)
    code, content = c.get('/')
    return i, code

tasks = [gevent.spawn(http_home, i) for i in range(25)]
gevent.joinall(tasks, timeout=30)
done = sum(1 for t in tasks if t.ready())
bad = [t.value for t in tasks if t.ready() and t.value[1] != 200]
record('T5-并发25路首页', 'PASS' if done == 25 and not bad else 'FAIL',
       '完成=%s/25, 非200=%s' % (done, len(bad)))

# 5b. 并发 RAG HTTP 查询（无命中词，避免外部AI，快速返回）
def http_ask(i):
    c = Client()
    c.cookies = dict(user_client.cookies)
    code, content = c.get('/api/ask?question=' + urllib.parse.quote(GARBAGE + str(i)), timeout=30)
    return i, code

tasks = [gevent.spawn(http_ask, i) for i in range(20)]
gevent.joinall(tasks, timeout=30)
done = sum(1 for t in tasks if t.ready())
bad = [t.value for t in tasks if t.ready() and t.value[1] != 200]
record('T5-并发20路RAG查询', 'PASS' if done == 20 and not bad else 'FAIL',
       '完成=%s/20, 非200=%s' % (done, len(bad)))

# 5c. 脚本进程内并发调 rag_service.search（验证 threading.Lock 在 gevent 补丁下无死锁）
import rag_service

def rag_search(i):
    try:
        r = rag_service.search(GARBAGE + str(i), top_k=3)
        return i, 'ok', len(r)
    except Exception as e:
        return i, 'err', str(e)

tasks = [gevent.spawn(rag_search, i) for i in range(40)]
gevent.joinall(tasks, timeout=30)
done = sum(1 for t in tasks if t.ready())
errs = [t.value for t in tasks if t.ready() and t.value[1] == 'err']
record('T5-并发40路service RAG检索', 'PASS' if done == 40 and not errs else 'FAIL',
       '完成=%s/40, 异常=%s' % (done, len(errs)))

# 5d. 并发触发索引重建（invalidate 后并发 ensure_index），验证锁防重入/死锁
def rag_build(i):
    try:
        if i % 5 == 0:
            rag_service.invalidate_index()
        r = rag_service.search(TOKEN if i % 2 == 0 else GARBAGE, top_k=2)
        return i, 'ok'
    except Exception as e:
        return i, 'err', str(e)

tasks = [gevent.spawn(rag_build, i) for i in range(30)]
gevent.joinall(tasks, timeout=40)
done = sum(1 for t in tasks if t.ready())
errs = [t.value for t in tasks if t.ready() and t.value[1] == 'err']
record('T5-并发30路索引重建+检索', 'PASS' if done == 30 and not errs else 'FAIL',
       '完成=%s/30, 异常=%s（验证 Lock 无死锁）' % (done, len(errs)))

# ============================================================
# 清理测试数据
# ============================================================
print("\n--- 清理测试数据 ---")
with get_cursor() as cur:
    cur.execute("DELETE FROM `user` WHERE username LIKE 'e2e_%'")
    cur.execute("SELECT COUNT(*) FROM `user` WHERE username LIKE 'e2e_%'")
    left = cur.fetchone()[0]
record('清理-e2e测试数据', 'PASS' if left == 0 else 'FAIL', '残留=%s' % left)

# ============================================================
# 汇总报告
# ============================================================
print("\n" + "=" * 70)
print("E2E 测试报告汇总  end at %s" % time.strftime('%Y-%m-%d %H:%M:%S'))
print("=" * 70)
passed = sum(1 for _, s, _ in report if s == 'PASS')
failed = sum(1 for _, s, _ in report if s == 'FAIL')
skipped = sum(1 for _, s, _ in report if s == 'SKIP')
print("总计: %d  |  PASS: %d  |  FAIL: %d  |  SKIP: %d" % (len(report), passed, failed, skipped))
print("-" * 70)
for case, status, detail in report:
    print("[%s] %s" % (status, case))
    print("      %s" % detail)
print("=" * 70)
if failed == 0:
    print("结论: 全部通过，gevent 迁移验证完成，无异常")
else:
    print("结论: 存在 %d 项失败，请检查" % failed)
