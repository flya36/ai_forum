# -*- coding: utf-8 -*-
"""板块管理模块 E2E 测试：申请/重名/审核/通知/删除校验/权限/频率/数据隔离
CSRF 感知：登录/注册/所有 POST 自动附带 _csrf_token；测试账号密码由环境变量
TEST_E2E_PWD 提供，缺失时每次运行随机生成（不落库明文，测试账号由本脚本自建自清）。"""
import os, re, sys, time, json, secrets, urllib.request, urllib.error, urllib.parse
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 项目根（tests/ 上一级）
sys.path.insert(0, os.path.join(BASE, 'backend', 'services'))
sys.path.insert(0, os.path.join(BASE, 'backend', 'core'))
sys.path.insert(0, os.path.join(BASE, 'backend', 'config'))
from gevent import monkey
monkey.patch_all()
from db import get_cursor
BASE_URL = 'http://127.0.0.1:5000'
P = 'bd' + str(int(time.time()))[-6:]
PWD = os.environ.get('TEST_E2E_PWD') or secrets.token_hex(6)

def q0(sql, params=()):
    with get_cursor() as cur:
        cur.execute(sql, params)
        r = cur.fetchone()
        return r[0] if r else None
def q1(sql, params=()):
    with get_cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()

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
            _, body, _ = self.get('/login')
            m = re.search(r'name="_csrf_token"\s+value="([^"]+)"', body)
            if not m:
                m = re.search(r"window\.CSRF_TOKEN = '([^']+)'", body)
                m = re.search(r'<meta name="csrf-token" content="([^"]+)"', body)
            self._csrf = m.group(1) if m else None
        return self._csrf
    def _req(self, method, path, data=None):
        if method == 'POST':
            # CSRF 感知：所有 POST 自动附带会话令牌（未登录也需令牌，400 防护；
            # data=None 的 POST 同样注入，例如 approve/delete 无表单体）
            d = dict(data) if isinstance(data, dict) else {}
            d.setdefault('_csrf_token', self._fetch_csrf())
            data = d
        body = urllib.parse.urlencode(data or {}).encode() if data is not None else None
        headers = {'User-Agent': 'board-e2e'}
        if body is not None:
            headers['Content-Type'] = 'application/x-www-form-urlencoded'
        if self.cookies:
            headers['Cookie'] = '; '.join('%s=%s' % kv for kv in self.cookies.items())
        req = urllib.request.Request(BASE_URL + path, data=body, headers=headers, method=method)
        try:
            resp = self.opener.open(req, timeout=60)
            code, content, hdrs = resp.status, resp.read().decode('utf-8', 'ignore'), resp.headers
        except urllib.error.HTTPError as e:
            code, content, hdrs = e.code, e.read().decode('utf-8', 'ignore'), e.headers
        for c in hdrs.get_all('Set-Cookie') or []:
            kv = c.split(';')[0]
            if '=' in kv:
                k, v = kv.split('=', 1)
                self.cookies[k] = v
        return code, content, hdrs
    def get(self, path):
        return self._req('GET', path)
    def post(self, path, data=None):
        return self._req('POST', path, data)

def reg(c, u):
    c.post('/register', {'username': u, 'password': PWD, 'nickname': u})
def mkadmin(u):
    with get_cursor() as cur:
        cur.execute('UPDATE `user` SET role=1 WHERE username=?', (u,))

print('=' * 70)
print('板块管理 E2E  prefix=%s' % P)
print('=' * 70)
BD_A, BD_B, BD_C = P + '_甲', P + '_乙', P + '_丙'

# 账号：U 申请人 / U2 另一用户 / ADM 管理员
u, u2, adm = Client(), Client(), Client()
reg(u, P + '_u'); reg(u2, P + '_u2'); reg(adm, P + '_adm')
mkadmin(P + '_adm')
u.post('/login', {'username': P + '_u', 'password': PWD})
u2.post('/login', {'username': P + '_u2', 'password': PWD})
adm.post('/login', {'username': P + '_adm', 'password': PWD})

fails = []
def check(name, ok, extra=''):
    tag = 'PASS' if ok else 'FAIL'
    print('[%s] %s %s' % (tag, name, extra))
    if not ok:
        fails.append(name)

# ===== 1. 未登录申请被拒 =====
anon = Client()
code, _, hdrs = anon.get('/boards/apply')
check('T1-未登录GET申请页->302 /login', code == 302 and '/login' in (hdrs.get('Location') or ''), 'code=%s' % code)
code, _, hdrs = anon.post('/boards/apply', {'board_name': 'x', 'description': ''})
check('T1-未登录POST申请->302 /login', code == 302 and '/login' in (hdrs.get('Location') or ''), 'code=%s' % code)

# ===== 2. 登录用户申请成功 =====
code, _, hdrs = u.post('/boards/apply', {'board_name': BD_A, 'description': '测试板块A'})
app_a = q0('SELECT application_id FROM board_application WHERE board_name=? AND status=0', (BD_A,))
check('T2-登录申请成功(status=0)', code == 302 and app_a is not None, 'app=%s code=%s' % (app_a, code))

# ===== 3. 与现有板块重名被拒 =====
code, pg, _ = u.post('/boards/apply', {'board_name': '技术交流', 'description': ''})
check('T3-与现有板块重名被拒', code == 200 and '已存在' in pg, 'code=%s' % code)

# ===== 4. 与待审核申请重名被拒（其他用户） =====
code, pg, _ = u2.post('/boards/apply', {'board_name': BD_A, 'description': ''})
check('T4-与待审核申请重名被拒', code == 200 and '已有待审核申请' in pg, 'code=%s' % code)

# ===== 5. 管理员查看待审核申请 =====
code, pg, _ = adm.get('/admin/board-applications?status=0')
check('T5-管理员查看待审核列表含申请', code == 200 and BD_A in pg, '')

# ===== 6. 管理员通过 → 建板块 + 通知 =====
code, _, hdrs = adm.post('/admin/board-applications/%d/approve' % app_a)
board_ok = q0('SELECT board_id FROM board WHERE board_name=?', (BD_A,)) is not None
st_ok = q0('SELECT status FROM board_application WHERE application_id=?', (app_a,)) == 1
# 旧 /notifications 已重定向到消息中心"系统通知"Tab（信息架构整合），直接访问新地址
code, pg, _ = u.get('/messages?tab=system')
check('T6-通过后建板块+状态1+申请人收通知',
      board_ok and st_ok and '已通过审核' in pg and BD_A in pg,
      'board=%s status=%s notify=%s' % (board_ok, st_ok, '已通过审核' in pg))

# ===== 7. 驳回并写原因 + 通知 =====
u.post('/boards/apply', {'board_name': BD_B, 'description': '测试板块B'})
app_b = q0('SELECT application_id FROM board_application WHERE board_name=? AND status=0', (BD_B,))
adm.post('/admin/board-applications/%d/reject' % app_b, {'reject_reason': '名称不合适'})
st_b = q0('SELECT status FROM board_application WHERE application_id=?', (app_b,))
rs_b = q0('SELECT reject_reason FROM board_application WHERE application_id=?', (app_b,))
code, pg, _ = u.get('/messages?tab=system')
check('T7-驳回写原因+状态2+申请人收通知',
      st_b == 2 and rs_b == '名称不合适' and '未通过审核' in pg and '名称不合适' in pg,
      'status=%s reason=%s notify=%s' % (st_b, rs_b, '未通过审核' in pg))

# ===== 8. 重复审核失败 =====
code, _, _ = adm.post('/admin/board-applications/%d/approve' % app_b)   # 已驳回再通过
code2, _, _ = adm.post('/admin/board-applications/%d/reject' % app_a, {'reject_reason': 'x'})  # 已通过再驳回
code3, pg3, _ = adm.get('/admin/board-applications')
if '已被处理' not in pg3:
    print('    [T8调试] 页面片段: %s' % (pg3[pg3.find('alert'):pg3.find('alert') + 300] if 'alert' in pg3 else pg3[:200]))
check('T8-重复审核失败(已驳回再通过/已通过再驳回)',
      '已被处理' in pg3, '')

# ===== 9. 非管理员审核失败 =====
u2.post('/boards/apply', {'board_name': BD_C, 'description': '测试板块C'})
app_c = q0('SELECT application_id FROM board_application WHERE board_name=? AND status=0', (BD_C,))
code, _, hdrs = u2.post('/admin/board-applications/%d/approve' % app_c)
st_c = q0('SELECT status FROM board_application WHERE application_id=?', (app_c,))
check('T9-非管理员审核被拒(状态仍0)',
      code == 302 and (hdrs.get('Location') or '') == '/' and st_c == 0,
      'code=%s loc=%s status=%s' % (code, hdrs.get('Location'), st_c))

# ===== 10. 编辑板块重名失败 =====
board_a = q0('SELECT board_id FROM board WHERE board_name=?', (BD_A,))
code, _, _ = adm.post('/admin/boards/%d/edit' % board_a, {'board_name': '技术交流', 'description': 'x'})
name_now = q0('SELECT board_name FROM board WHERE board_id=?', (board_a,))
check('T10-编辑板块重名失败(名称未变)', name_now == BD_A, 'name=%s' % name_now)

# ===== 11. 删除有帖板块被拒并提示 N 篇 =====
u.post('/post/new', {'title': P + '_帖', 'content': '内容', 'board_id': board_a})
cnt = q0('SELECT COUNT(*) FROM post WHERE board_id=?', (board_a,))
code, _, _ = adm.post('/admin/boards/%d/delete' % board_a)
alive = q0('SELECT COUNT(*) FROM board WHERE board_id=?', (board_a,))
code2, pg2, _ = adm.get('/admin/boards')
check('T11-删除有帖板块被拒(提示N篇,板块仍在)',
      alive == 1 and '%d 篇帖子' % cnt in pg2, 'post=%d 提示=%s' % (cnt, '%d 篇帖子' % cnt in pg2))

# ===== 12. 删除无帖板块成功 =====
adm.post('/admin/boards/create', {'board_name': BD_C + '_临时', 'description': ''})
tmp_bid = q0('SELECT board_id FROM board WHERE board_name=?', (BD_C + '_临时',))
code, _, _ = adm.post('/admin/boards/%d/delete' % tmp_bid)
check('T12-删除无帖板块成功', q0('SELECT COUNT(*) FROM board WHERE board_id=?', (tmp_bid,)) == 0, '')

# ===== 13. 我的申请状态展示 =====
code, pg, _ = u.get('/boards/my-applications')
check('T13-我的申请展示(已通过/已驳回+原因)',
      code == 200 and BD_A in pg and '已通过' in pg and BD_B in pg and '已驳回' in pg and '名称不合适' in pg, '')

# ===== 14. 驳回后重新申请成功 =====
code, _, hdrs = u.post('/boards/apply', {'board_name': BD_B, 'description': '重新申请B'})
new_b = q0('SELECT application_id FROM board_application WHERE board_name=? AND status=0', (BD_B,))
check('T14-驳回后重新申请成功(新status=0)',
      code == 302 and new_b is not None and new_b != app_b, 'new=%s' % new_b)

# ===== 15. 频率限制：待审核申请数上限 =====
for i in range(3):
    u2.post('/boards/apply', {'board_name': '%s_f%d' % (P, i), 'description': ''})
code, pg, _ = u2.post('/boards/apply', {'board_name': '%s_f9' % P, 'description': ''})
check('T15-待审核上限3条(第4条被拒)',
      code == 200 and '3 条待审核申请' in pg, 'code=%s' % code)

# ===== 16. 用户只能看自己的申请（数据隔离） =====
code, pg, _ = u.get('/boards/my-applications')
check('T16-用户看不到他人申请', (P + '_f0') not in pg and (P + '_u2申请') not in pg, '')

# ===== 17. 板块管理页渲染 =====
code, pg, _ = adm.get('/admin/boards')
check('T17-管理端板块列表含新板块与帖子数', code == 200 and BD_A in pg and '帖子数' in pg, '')

print('=' * 70)
print('结果: %d/%d 通过' % (17 - len(fails), 17))

# ===== 清理 =====
print('--- 清理 ---')
with get_cursor() as cur:
    pids = [r[0] for r in q1('SELECT post_id FROM post WHERE title LIKE ?', (P + '%',))]
    if pids:
        ph = ','.join('?' * len(pids))
        cur.execute('DELETE FROM knowledge_chunk WHERE post_id IN (%s)' % ph, pids)
        cur.execute('DELETE FROM post_tag WHERE post_id IN (%s)' % ph, pids)
        cur.execute('DELETE FROM post WHERE post_id IN (%s)' % ph, pids)
    cur.execute('DELETE FROM notification WHERE content LIKE ?', ('%' + P + '%',))
    cur.execute('DELETE FROM board_application WHERE board_name LIKE ?', (P + '%',))
    # 删除测试板块（先确认无帖子引用）
    bds = [r[0] for r in q1('SELECT board_id FROM board WHERE board_name LIKE ?', (P + '%',))]
    for bid in bds:
        cur.execute('SELECT COUNT(*) FROM post WHERE board_id=?', (bid,))
        if cur.fetchone()[0] == 0:
            cur.execute('DELETE FROM board WHERE board_id=?', (bid,))
    cur.execute('DELETE FROM `user` WHERE username LIKE ?', (P + '%',))
left = q0('SELECT COUNT(*) FROM board_application WHERE board_name LIKE ?', (P + '%',))
print('残留=%d' % left)
