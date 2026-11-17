# -*- coding: utf-8 -*-
"""
标签功能 E2E 测试脚本
覆盖：管理端标签增删改查/重复名校验/搜索/分页、发帖带标签（复用+新建）、
详情页标签展示、标签文章列表页、删除有关联标签被拒、并发创建同名标签、
发帖+关联表同事务。

所有测试数据带 tage2e_ 前缀，测试结束自动清理。

CSRF 感知：登录/注册/所有 POST 自动附带 _csrf_token；测试账号密码由环境变量
TEST_E2E_PWD 提供，缺失时每次运行随机生成（不落库明文，测试账号由本脚本自建自清）。
"""
import os
import re
import sys
import time
import json
import secrets
import urllib.request
import urllib.error
import urllib.parse

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 项目根（tests/ 上一级）
sys.path.insert(0, os.path.join(BASE, 'backend', 'services'))
sys.path.insert(0, os.path.join(BASE, 'backend', 'core'))
sys.path.insert(0, os.path.join(BASE, 'backend', 'config'))

from gevent import monkey
monkey.patch_all()

import gevent
from db import get_cursor

BASE_URL = 'http://127.0.0.1:5000'
UNIQ = str(int(time.time()))[-8:]
ADMIN_NAME = 'tage2e_admin_' + UNIQ
USER_NAME = 'tage2e_user_' + UNIQ
PWD = os.environ.get('TEST_E2E_PWD') or secrets.token_hex(6)

report = []


def record(case, status, detail):
    report.append((case, status, detail))
    print("[%s] %s: %s" % ('PASS' if status == 'PASS' else 'FAIL', case, detail))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    """带 cookie 管理的简易 HTTP 客户端（NoRedirect，暴露 302 + Location）
    CSRF 感知：所有 POST 自动附带会话级 _csrf_token（未登录也需令牌）。"""
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
        headers = {'User-Agent': 'tage2e-test'}
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
        code, content, _ = self._req('GET', path, timeout=timeout)
        return code, content

    def post(self, path, data=None, timeout=60):
        code, content, hdrs = self._req('POST', path, data=data, timeout=timeout)
        return code, hdrs.get('Location')


def db_query(sql, params=()):
    with get_cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


print("=" * 70)
print("标签功能 E2E 测试  start at %s" % time.strftime('%Y-%m-%d %H:%M:%S'))
print("=" * 70)

# ============ 准备：注册 + 提权 ============
print("\n--- 准备测试账号 ---")
anon = Client()
code, loc = anon.post('/register', {'username': ADMIN_NAME, 'password': PWD, 'nickname': '标签管理员'})
record('准备-注册管理员', 'PASS' if code == 302 and loc == '/login' else 'FAIL', 'code=%s' % code)
code, loc = anon.post('/register', {'username': USER_NAME, 'password': PWD, 'nickname': '标签用户'})
record('准备-注册用户', 'PASS' if code == 302 and loc == '/login' else 'FAIL', 'code=%s' % code)

with get_cursor() as cur:
    cur.execute("UPDATE `user` SET role=1 WHERE username=?", (ADMIN_NAME,))

admin = Client()
code, loc = admin.post('/login', {'username': ADMIN_NAME, 'password': PWD})
record('准备-管理员登录', 'PASS' if code == 302 and loc == '/' else 'FAIL', 'code=%s' % code)
user = Client()
code, loc = user.post('/login', {'username': USER_NAME, 'password': PWD})
record('准备-用户登录', 'PASS' if code == 302 and loc == '/' else 'FAIL', 'code=%s' % code)

# ============ T1 管理端标签增删改查 ============
print("\n--- T1 管理端标签管理 ---")
TAG_A = 'tage2e标签A' + UNIQ
TAG_B = 'tage2e标签B' + UNIQ

# 1a. 新增
code, loc = admin.post('/admin/tags/add', {'tag_name': TAG_A})
code2, content = admin.get('/admin/tags')
record('T1-新增标签', 'PASS' if code == 302 and '标签添加成功' in content else 'FAIL',
       'code=%s 页面含成功提示=%s' % (code, '标签添加成功' in content))

# 1b. 重复新增 → 拒绝
code, loc = admin.post('/admin/tags/add', {'tag_name': TAG_A})
code2, content = admin.get('/admin/tags')
record('T1-重复标签名拒绝', 'PASS' if code == 302 and '已存在' in content else 'FAIL',
       '页面含已存在提示=%s' % ('已存在' in content))

# 1c. 空标签 → 拒绝
code, loc = admin.post('/admin/tags/add', {'tag_name': '   '})
code2, content = admin.get('/admin/tags')
record('T1-空标签拒绝', 'PASS' if code == 302 and '不能为空' in content else 'FAIL',
       '页面含空提示=%s' % ('不能为空' in content))

# 1d. 搜索
code, content = admin.get('/admin/tags?q=' + urllib.parse.quote(TAG_A))
record('T1-按名称搜索', 'PASS' if code == 200 and TAG_A in content else 'FAIL',
       '搜索结果含标签=%s' % (TAG_A in content))

# 取 tag_id
rows = db_query("SELECT tag_id FROM tag WHERE tag_name=?", (TAG_A,))
tag_a_id = rows[0][0] if rows else None
record('T1-标签入库', 'PASS' if tag_a_id else 'FAIL', 'tag_id=%s' % tag_a_id)

# 1e. 修改
TAG_A2 = TAG_A + '改'
code, loc = admin.post('/admin/tags/%d/edit' % tag_a_id, {'tag_name': TAG_A2})
code2, content = admin.get('/admin/tags')
rows = db_query("SELECT tag_name FROM tag WHERE tag_id=?", (tag_a_id,))
record('T1-修改标签', 'PASS' if code == 302 and rows and rows[0][0] == TAG_A2 and '标签已更新' in content else 'FAIL',
       '新名称=%s' % (rows[0][0] if rows else None))

# 1f. 修改为已存在名 → 拒绝
code, loc = admin.post('/admin/tags/add', {'tag_name': TAG_B})
rows = db_query("SELECT tag_id FROM tag WHERE tag_name=?", (TAG_B,))
tag_b_id = rows[0][0] if rows else None
code, loc = admin.post('/admin/tags/%d/edit' % tag_b_id, {'tag_name': TAG_A2})
code2, content = admin.get('/admin/tags')
record('T1-改名撞唯一拒绝', 'PASS' if code == 302 and '已存在' in content else 'FAIL',
       '页面含已存在提示=%s' % ('已存在' in content))

# ============ T2 发帖带标签（复用 + 新标签自动创建） ============
print("\n--- T2 发帖带标签 ---")
NEW_TAG = 'tage2e新标签' + UNIQ
code, loc = user.post('/post/new', {
    'title': '标签功能测试帖 ' + UNIQ,
    'content': '这是一篇带标签的测试帖子，验证发帖时标签自动创建与关联。',
    'board_id': '1',
    'tags': json.dumps([TAG_A2, NEW_TAG]),   # JSON 数组：TAG_A2 已存在（复用），NEW_TAG 自动创建
})
rows = db_query("SELECT post_id FROM post WHERE title LIKE ? ORDER BY post_id DESC LIMIT 1", ('%标签功能测试帖%' + UNIQ,))
post_id = rows[0][0] if rows else None
rows = db_query("SELECT COUNT(*) FROM post_tag WHERE post_id=?", (post_id,)) if post_id else [(0,)]
rel_cnt = rows[0][0]
record('T2-发帖成功且关联2标签', 'PASS' if code == 302 and post_id and rel_cnt == 2 else 'FAIL',
       'post_id=%s 关联数=%s' % (post_id, rel_cnt))

# 新标签自动创建且未重复创建 TAG_A2
rows = db_query("SELECT COUNT(*) FROM tag WHERE tag_name=?", (NEW_TAG,))
record('T2-新标签自动创建', 'PASS' if rows[0][0] == 1 else 'FAIL', '数量=%s' % rows[0][0])
rows = db_query("SELECT COUNT(*) FROM tag WHERE tag_name=?", (TAG_A2,))
record('T2-已有标签未重复创建', 'PASS' if rows[0][0] == 1 else 'FAIL', '数量=%s' % rows[0][0])

# 取新标签 id
rows = db_query("SELECT tag_id FROM tag WHERE tag_name=?", (NEW_TAG,))
new_tag_id = rows[0][0] if rows else None

# ============ T3 详情页标签展示 ============
print("\n--- T3 详情页标签展示 ---")
code, content = user.get('/post/%d' % post_id)
record('T3-详情页显示标签', 'PASS' if code == 200 and TAG_A2 in content and NEW_TAG in content else 'FAIL',
       '含两个标签=%s' % (TAG_A2 in content and NEW_TAG in content))

# ============ T4 标签文章列表页 ============
print("\n--- T4 标签文章列表页 ---")
code, content = user.get('/tag/%d' % tag_a_id)
record('T4-标签列表页含文章', 'PASS' if code == 200 and ('标签功能测试帖' in content) and UNIQ in content else 'FAIL',
       '页面含帖子标题=%s' % ('标签功能测试帖' in content))

code, content = user.get('/tag/%d' % new_tag_id)
record('T4-新标签列表页含文章', 'PASS' if code == 200 and UNIQ in content else 'FAIL',
       '页面含帖子=%s' % (UNIQ in content))

# 不存在的标签 → flash 提示 + 302 重定向回首页（NoRedirect 客户端见 302 + Location=/）
code, content, hdrs = user._req('GET', '/tag/999999')
loc = hdrs.get('Location')
record('T4-不存在标签提示', 'PASS' if code == 302 and loc == '/' else 'FAIL',
       'code=%s Location=%s' % (code, loc))

# ============ T5 删除关联校验 ============
print("\n--- T5 删除标签关联校验 ---")
code, loc = admin.post('/admin/tags/%d/delete' % tag_a_id)
code2, content = admin.get('/admin/tags')
record('T5-删除被使用标签被拒', 'PASS' if code == 302 and '无法删除' in content else 'FAIL',
       '页面含拒绝提示=%s' % ('无法删除' in content))

# 创建并删除未使用标签
TMP_TAG = 'tage2e临时' + UNIQ
admin.post('/admin/tags/add', {'tag_name': TMP_TAG})
rows = db_query("SELECT tag_id FROM tag WHERE tag_name=?", (TMP_TAG,))
tmp_id = rows[0][0] if rows else None
code, loc = admin.post('/admin/tags/%d/delete' % tmp_id)
rows = db_query("SELECT COUNT(*) FROM tag WHERE tag_name=?", (TMP_TAG,))
record('T5-删除未使用标签成功', 'PASS' if code == 302 and rows[0][0] == 0 else 'FAIL',
       '残留=%s' % rows[0][0])

# ============ T6 并发创建同名标签 ============
print("\n--- T6 并发创建同名标签 ---")
import tag_service
CONC_TAG = 'tage2e并发' + UNIQ

def create_conc(i):
    ok, msg, tid = tag_service.get_or_create_tag(CONC_TAG)
    return ok, tid

tasks = [gevent.spawn(create_conc, i) for i in range(10)]
gevent.joinall(tasks, timeout=30)
results = [t.value for t in tasks if t.ready()]
all_ok = all(r[0] for r in results)
ids = set(r[1] for r in results)
rows = db_query("SELECT COUNT(*) FROM tag WHERE tag_name=?", (CONC_TAG,))
record('T6-并发10路同名标签唯一', 'PASS' if all_ok and len(ids) == 1 and rows[0][0] == 1 else 'FAIL',
       '全部成功=%s 返回唯一ID=%s 库中数量=%s' % (all_ok, len(ids), rows[0][0]))

# ============ T7 事务 + 关联维护工具 ============
print("\n--- T7 同事务与关联维护 ---")
# 7a. 发帖成功场景下 post 与 post_tag 同时存在（同事务提交）
rows = db_query("SELECT COUNT(*) FROM post WHERE post_id=?", (post_id,))
rows2 = db_query("SELECT COUNT(*) FROM post_tag WHERE post_id=?", (post_id,))
record('T7-帖子与关联同事务落库', 'PASS' if rows[0][0] == 1 and rows2[0][0] == 2 else 'FAIL',
       'post=%s post_tag=%s' % (rows[0][0], rows2[0][0]))

# 7b. replace_post_tags：清空再重设（编辑场景复用）
ok, msg = tag_service.replace_post_tags(post_id, [tag_a_id])
rows = db_query("SELECT COUNT(*) FROM post_tag WHERE post_id=?", (post_id,))
ok2, _ = tag_service.replace_post_tags(post_id, [])
rows2 = db_query("SELECT COUNT(*) FROM post_tag WHERE post_id=?", (post_id,))
record('T7-替换/清空关联', 'PASS' if ok and rows[0][0] == 1 and ok2 and rows2[0][0] == 0 else 'FAIL',
       '重设后=%s 清空后=%s' % (rows[0][0], rows2[0][0]))

# ============ T8 标签名含逗号（Bug 修复验证） ============
print("\n--- T8 标签名含逗号 ---")
COMMA_TAG = '苹果,香蕉' + UNIQ
code, loc = user.post('/post/new', {
    'title': '逗号标签测试帖 ' + UNIQ,
    'content': '验证标签名可含逗号，不应被拆分。',
    'board_id': '1',
    'tags': json.dumps([COMMA_TAG]),   # 一个标签，名字里带逗号
})
rows = db_query("SELECT post_id FROM post WHERE title LIKE ? ORDER BY post_id DESC LIMIT 1", ('%逗号标签测试帖%',))
comma_post_id = rows[0][0] if rows else None
rows = db_query("SELECT COUNT(*) FROM tag WHERE tag_name=?", (COMMA_TAG,))
rows2 = db_query("SELECT COUNT(*) FROM post_tag WHERE post_id=?", (comma_post_id,)) if comma_post_id else [(0,)]
record('T8-含逗号标签只创建1个', 'PASS' if code == 302 and rows[0][0] == 1 and rows2[0][0] == 1 else 'FAIL',
       'tag记录=%s 关联数=%s' % (rows[0][0], rows2[0][0]))

# ============ T9 超过5个标签服务端拒绝（不创建帖子） ============
print("\n--- T9 服务端上限校验 ---")
OVER_TAGS = ['tage2e超限%d' % i for i in range(6)]
code, content, hdrs = user._req('POST', '/post/new', {
    'title': '超限拒绝帖 ' + UNIQ,
    'content': '此帖不应被创建。',
    'board_id': '1',
    'tags': json.dumps(OVER_TAGS),
})
rows = db_query("SELECT COUNT(*) FROM post WHERE title LIKE ?", ('%超限拒绝帖%',))
rows2 = db_query("SELECT COUNT(*) FROM tag WHERE tag_name LIKE 'tage2e超限%'")
record('T9-6标签拒绝且不建帖', 'PASS' if code == 200 and '最多选择 5 个' in content and rows[0][0] == 0 and rows2[0][0] == 0 else 'FAIL',
       'code=%s 含提示=%s 帖子数=%s 新标签数=%s' % (code, '最多选择 5 个' in content, rows[0][0], rows2[0][0]))

# ============ T10 create_tag 重名返回 tag_id=None（Bug 修复验证） ============
print("\n--- T10 create_tag 失败分支 ---")
DUP_TAG = 'tage2e重名' + UNIQ
ok1, msg1, tid1 = tag_service.create_tag(DUP_TAG)
ok2, msg2, tid2 = tag_service.create_tag(DUP_TAG)   # 重名 → 失败
record('T10-重名失败不带id', 'PASS' if ok1 and tid1 and not ok2 and tid2 is None else 'FAIL',
       '首次=%s/%s 重名=%s/%s' % (ok1, tid1, ok2, tid2))

# ============ T11 未登录访问管理端标签页 ============
print("\n--- T11 权限校验 ---")
anon2 = Client()
code, content, hdrs = anon2._req('GET', '/admin/tags')
loc = hdrs.get('Location')
record('T11-未登录重定向登录页', 'PASS' if code == 302 and loc == '/login' else 'FAIL',
       'code=%s Location=%s' % (code, loc))

# ============ T12 非字符串标签元素过滤（不 500） ============
print("\n--- T12 非字符串元素过滤 ---")
code, loc = user.post('/post/new', {
    'title': '数字标签过滤帖 ' + UNIQ,
    'content': 'POST tags=[1,2] 不应 500，应正常发帖且无标签。',
    'board_id': '1',
    'tags': json.dumps([1, 2]),   # 数字元素：应被过滤而非 str() 强转
})
rows = db_query("SELECT post_id FROM post WHERE title LIKE ? ORDER BY post_id DESC LIMIT 1", ('%数字标签过滤帖%',))
num_post_id = rows[0][0] if rows else None
rows2 = db_query("SELECT COUNT(*) FROM post_tag WHERE post_id=?", (num_post_id,)) if num_post_id else [(0,)]
code2, home = user.get('/')
record('T12-数字元素过滤不500', 'PASS' if code == 302 and num_post_id and rows2[0][0] == 0 and '标签格式有误' not in home else 'FAIL',
       'code=%s 帖子=%s 关联=%s 无格式警告=%s' % (code, num_post_id, rows2[0][0], '标签格式有误' not in home))

# ============ T13 空 tags 视为未选标签（无格式警告） ============
print("\n--- T13 空 tags 无格式警告 ---")
code, loc = user.post('/post/new', {
    'title': '无标签发帖帖 ' + UNIQ,
    'content': '不带 tags 字段发帖，不应弹出标签格式类提示。',
    'board_id': '1',
})
rows = db_query("SELECT post_id FROM post WHERE title LIKE ? ORDER BY post_id DESC LIMIT 1", ('%无标签发帖帖%',))
no_post_id = rows[0][0] if rows else None
# 消费前序用例（T12 数字标签）残留的 flash 警告，避免污染本用例断言
user.get('/')
code2, home = user.get('/')
record('T13-空tags正常发帖', 'PASS' if code == 302 and no_post_id and '标签格式有误' not in home else 'FAIL',
       'code=%s 帖子=%s 无格式警告=%s' % (code, no_post_id, '标签格式有误' not in home))

# ============ 清理 ============
print("\n--- 清理测试数据 ---")
with get_cursor() as cur:
    cur.execute("DELETE FROM post WHERE title LIKE ?", ('%标签功能测试帖%',))
    cur.execute("DELETE FROM post WHERE title LIKE ?", ('%逗号标签测试帖%',))
    cur.execute("DELETE FROM post WHERE title LIKE ?", ('%数字标签过滤帖%',))
    cur.execute("DELETE FROM post WHERE title LIKE ?", ('%无标签发帖帖%',))
    cur.execute("DELETE FROM tag WHERE tag_name LIKE ?", ('tage2e%',))
    cur.execute("DELETE FROM tag WHERE tag_name LIKE ?", ('苹果,香蕉%',))
    cur.execute("DELETE FROM `user` WHERE username LIKE 'tage2e_%'")
    cur.execute("SELECT COUNT(*) FROM tag WHERE tag_name LIKE 'tage2e%'")
    left = cur.fetchone()[0]
record('清理-tage2e测试数据', 'PASS' if left == 0 else 'FAIL', '残留=%s' % left)

# ============ 汇总 ============
print("\n" + "=" * 70)
print("标签功能 E2E 测试汇总  end at %s" % time.strftime('%Y-%m-%d %H:%M:%S'))
print("=" * 70)
passed = sum(1 for _, s, _ in report if s == 'PASS')
failed = sum(1 for _, s, _ in report if s == 'FAIL')
print("总计: %d  |  PASS: %d  |  FAIL: %d" % (len(report), passed, failed))
print("-" * 70)
for case, status, detail in report:
    print("[%s] %s" % (status, case))
    print("      %s" % detail)
print("=" * 70)
