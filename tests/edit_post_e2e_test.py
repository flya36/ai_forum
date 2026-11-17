# -*- coding: utf-8 -*-
"""编辑帖子功能 E2E 测试：权限 / 编辑 / 标签同步 / 清空 / RAG 刷新 / 上限 / 图片 / 匿名 / 管理员
CSRF 感知：登录/注册/所有 POST 自动附带 _csrf_token；测试账号密码由环境变量 TEST_E2E_PWD
提供，缺失时每次运行随机生成（不落库明文，测试账号由本脚本自建自清）。"""
import os, re, sys, time, json, secrets, urllib.request, urllib.error, urllib.parse
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 项目根（tests/ 上一级）
sys.path.insert(0, os.path.join(BASE, 'backend', 'services'))
sys.path.insert(0, os.path.join(BASE, 'backend', 'core'))
sys.path.insert(0, os.path.join(BASE, 'backend', 'config'))
from gevent import monkey
monkey.patch_all()
from db import get_cursor
BASE_URL = 'http://127.0.0.1:5000'
P = 'edit' + str(int(time.time()))[-6:]
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

def multipart(fields, file_field=None, file_name=None, file_bytes=None):
    boundary = '----EditTestBoundary' + str(int(time.time() * 1000))
    parts = []
    for k, v in fields.items():
        parts.append(('--' + boundary).encode())
        parts.append(('Content-Disposition: form-data; name="%s"' % k).encode())
        parts.append(b'')
        parts.append(str(v).encode('utf-8'))
    if file_field:
        parts.append(('--' + boundary).encode())
        parts.append(('Content-Disposition: form-data; name="%s"; filename="%s"' % (file_field, file_name)).encode())
        parts.append(b'Content-Type: image/png')
        parts.append(b'')
        parts.append(file_bytes)
    parts.append(('--' + boundary + '--').encode())
    parts.append(b'')
    return b'\r\n'.join(parts), 'multipart/form-data; boundary=' + boundary

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
    def _req(self, method, path, data=None, content_type=None):
        body = None
        headers = {'User-Agent': 'edit-e2e'}
        if method == 'POST' and not content_type:
            # CSRF 感知：所有 POST 自动附带会话令牌（data=None 的 POST 同样注入）
            d = dict(data) if isinstance(data, dict) else {}
            d.setdefault('_csrf_token', self._fetch_csrf())
            data = d
        if data is not None:
            if isinstance(data, dict):
                if content_type:
                    body, content_type = multipart(data) if content_type == 'multipart' else (data, content_type)
                    headers['Content-Type'] = content_type
                else:
                    body = urllib.parse.urlencode(data).encode()
                    headers['Content-Type'] = 'application/x-www-form-urlencoded'
            else:
                body, content_type = multipart(data) if content_type == 'multipart' else (data, content_type)
                headers['Content-Type'] = content_type
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
    def post_mp(self, path, fields, file_field=None, file_name=None, file_bytes=None):
        fields = dict(fields)
        fields.setdefault('_csrf_token', self._fetch_csrf())
        body, ct = multipart(fields, file_field, file_name, file_bytes)
        return self._req('POST', path, body, ct)

def reg(c, u):
    c.post('/register', {'username': u, 'password': PWD, 'nickname': u})
def login(c, u):
    return c.post('/login', {'username': u, 'password': PWD})
def mkadmin(u):
    with get_cursor() as cur:
        cur.execute('UPDATE `user` SET role=1 WHERE username=?', (u,))

print('=' * 70)
print('编辑帖子功能 E2E  prefix=%s' % P)
print('=' * 70)
fails = []

board_id = q0('SELECT board_id FROM board ORDER BY board_id LIMIT 1')

# 准备账号
a, b, adm = Client(), Client(), Client()
reg(a, P + '_a'); reg(b, P + '_b'); reg(adm, P + '_adm')
mkadmin(P + '_adm')
login(a, P + '_a'); login(b, P + '_b'); login(adm, P + '_adm')

# ===== E1 发帖 =====
code, _, _ = a.post('/post/new', {'title': '编辑测试原标题', 'content': '原内容AB', 'board_id': board_id,
                                  'tags': json.dumps([P + '_tag1'])})
pid = q0('SELECT post_id FROM post WHERE title=?', ('编辑测试原标题',))
print('E1-发帖: post_id=%s code=%s' % (pid, code))
assert pid, '发帖失败'
print('[PASS] E1-发帖')

# ===== E2 未登录访问编辑页 =====
anon = Client()
code, _, hdrs = anon.get('/post/%d/edit' % pid)
print('E2-未登录: code=%s Location=%s' % (code, hdrs.get('Location')))
assert code == 302 and '/login' in (hdrs.get('Location') or ''), '未登录应重定向登录页'
print('[PASS] E2-未登录访问编辑页->302 /login')

# ===== E3 编辑页 GET 回填 =====
code, pg, _ = a.get('/post/%d/edit' % pid)
backfill = code == 200 and '编辑测试原标题' in pg and '原内容AB' in pg and \
           ('value="%d"' % board_id in pg or 'selected' in pg) and (P + '_tag1') in pg
print('E3-编辑页回填: %s (标题/正文/板块/标签初始化)' % ('OK' if backfill else 'NO'))
assert backfill
print('[PASS] E3-编辑页GET回填')

# ===== E4 作者编辑成功（标题/正文/标签替换） =====
code, _, hdrs = a.post('/post/%d/edit' % pid, {'title': '编辑测试新标题', 'content': '新内容XYZ',
                                               'board_id': board_id,
                                               'tags': json.dumps([P + '_tag2', P + '_tag3'])})
assert code == 302 and '/post/%d' % pid in (hdrs.get('Location') or ''), '编辑应重定向详情页'
tags_now = sorted(r[0] for r in q1('SELECT t.tag_name FROM post_tag pt JOIN tag t ON pt.tag_id=t.tag_id WHERE pt.post_id=?', (pid,)))
code, pg, _ = a.get('/post/%d' % pid)
assert '编辑测试新标题' in pg and '新内容XYZ' in pg and (P + '_tag2') in pg and (P + '_tag3') in pg
assert tags_now == sorted([P + '_tag2', P + '_tag3']), '关联应为 tag2,tag3, 实际 %s' % tags_now
print('[PASS] E4-作者编辑+标签替换为2个 (关联=%s)' % tags_now)

# ===== E5 编辑清空标签 =====
a.post('/post/%d/edit' % pid, {'title': '编辑测试新标题', 'content': '新内容XYZ', 'board_id': board_id, 'tags': ''})
n_pt = q0('SELECT COUNT(*) FROM post_tag WHERE post_id=?', (pid,))
code, pg, _ = a.get('/post/%d' % pid)
assert n_pt == 0 and (P + '_tag2') not in pg, '清空后关联=0 且详情页无标签'
print('[PASS] E5-编辑清空标签 (post_tag=%d)' % n_pt)

# ===== E6 非作者无权 =====
code, _, hdrs = b.get('/post/%d/edit' % pid)
assert code == 302 and '/post/%d' % pid in (hdrs.get('Location') or ''), '非作者GET应重定向详情页'
code, _, hdrs = b.post('/post/%d/edit' % pid, {'title': '恶意改标题', 'content': '恶意内容', 'board_id': board_id})
t_now = q0('SELECT title FROM post WHERE post_id=?', (pid,))
assert code == 302 and t_now == '编辑测试新标题', '非作者POST不应生效, 实际标题=%s' % t_now
print('[PASS] E6-非作者GET/POST被拒 (标题未变=%s)' % (t_now == '编辑测试新标题'))

# ===== E7 管理员可编辑他人帖子 =====
code, _, hdrs = adm.post('/post/%d/edit' % pid, {'title': '编辑测试新标题[管理改]', 'content': '新内容XYZ',
                                                 'board_id': board_id})
assert code == 302 and q0('SELECT title FROM post WHERE post_id=?', (pid,)) == '编辑测试新标题[管理改]'
print('[PASS] E7-管理员编辑他人帖子成功')

# ===== E8 标签上限：6 个拒绝 =====
before_title = q0('SELECT title FROM post WHERE post_id=?', (pid,))
before_tags = [r[0] for r in q1('SELECT tag_id FROM post_tag WHERE post_id=?', (pid,))]
code, pg, _ = a.post('/post/%d/edit' % pid, {'title': before_title, 'content': '新内容XYZ', 'board_id': board_id,
                                             'tags': json.dumps([P + '_t%d' % i for i in range(6)])})
after_title = q0('SELECT title FROM post WHERE post_id=?', (pid,))
after_tags = [r[0] for r in q1('SELECT tag_id FROM post_tag WHERE post_id=?', (pid,))]
assert code == 200 and '标签最多选择 5 个' in pg and after_title == before_title and after_tags == before_tags
print('[PASS] E8-6标签被拒 (页面提示+内容未变+关联未变)')

# ===== E9 RAG 刷新：旧块删除、新块存在 =====
old_chunk = q0('SELECT COUNT(*) FROM knowledge_chunk WHERE post_id=? AND chunk_content LIKE ?', (pid, '%原内容AB%'))
new_chunk = q0('SELECT COUNT(*) FROM knowledge_chunk WHERE post_id=? AND chunk_content LIKE ?', (pid, '%新内容XYZ%'))
print('E9-RAG: 旧块=%d 新块=%d' % (old_chunk, new_chunk))
assert old_chunk == 0 and new_chunk >= 1, '编辑后旧内容块应删除、新内容块应存在'
print('[PASS] E9-RAG索引自动刷新 (旧块删除/新块入库)')

# ===== E10 匿名帖作者可编辑 =====
code, _, _ = a.post('/post/new', {'title': P + '_匿名帖', 'content': '匿名原内容', 'board_id': board_id,
                                  'is_anonymous': '1', 'tags': ''})
apid = q0('SELECT post_id FROM post WHERE title=?', (P + '_匿名帖',))
code, pg, _ = a.get('/post/%d/edit' % apid)
assert code == 200 and 'checked' in pg and '匿名原内容' in pg, '匿名帖编辑页应回填匿名勾选+内容'
a.post('/post/%d/edit' % apid, {'title': P + '_匿名帖', 'content': '匿名改后内容', 'board_id': board_id,
                                'is_anonymous': '1'})  # 前端编辑页回填匿名勾选，提交须带该字段
code, pg, _ = a.get('/post/%d' % apid)
assert '匿名改后内容' in pg and '匿名用户' in pg, '匿名帖编辑后仍匿名展示'
print('[PASS] E10-匿名帖作者可编辑, 编辑后仍掩码')

# ===== E11 图片：不发图保留原图 / 发新图替换并删旧文件 =====
IMG_DIR = os.path.join(BASE, 'backend', 'post_images')
from PIL import Image as PILImage
import io


def _real_png(w=80, h=80, rgb=(200, 30, 30)):
    """用 Pillow 生成真实 PNG（服务端会做真实格式校验+压缩重存，伪 PNG 已被拒绝）"""
    buf = io.BytesIO()
    PILImage.new('RGB', (w, h), rgb).save(buf, 'PNG')
    return buf.getvalue()


png = _real_png()
code, _, _ = a.post_mp('/post/new', {'title': P + '_图帖', 'content': '图片内容', 'board_id': board_id},
                       'image', 'orig.png', png)
ipid = q0('SELECT post_id FROM post WHERE title=?', (P + '_图帖',))
img1 = q0('SELECT image_path FROM post WHERE post_id=?', (ipid,))
f1 = os.path.join(IMG_DIR, img1) if img1 else ''
assert img1 and os.path.exists(f1), '发帖应存图: %s' % img1
# 不带图编辑 → 保留原图
a.post('/post/%d/edit' % ipid, {'title': P + '_图帖', 'content': '图片内容改', 'board_id': board_id})
img2 = q0('SELECT image_path FROM post WHERE post_id=?', (ipid,))
assert img2 == img1 and os.path.exists(f1), '不带图编辑应保留原图'
# 带新图编辑 → 替换 + 删旧文件（uuid 命名，同秒发帖→编辑换图也不冲突）
a.post_mp('/post/%d/edit' % ipid, {'title': P + '_图帖', 'content': '图片内容改2', 'board_id': board_id},
          'image', 'new.png', png)
img3 = q0('SELECT image_path FROM post WHERE post_id=?', (ipid,))
assert img3 != img1 and os.path.exists(os.path.join(IMG_DIR, img3)) and not os.path.exists(f1), \
    '带新图编辑应替换且删除旧文件: %s -> %s' % (img1, img3)
print('[PASS] E11-图片编辑 (保留原图=%s / 替换删旧=%s)' % (img2 == img1, img3 != img1 and not os.path.exists(f1)))

# ===== 清理 =====
print('\n--- 清理 ---')
pids = [r[0] for r in q1('SELECT post_id FROM post WHERE title LIKE ?', (P + '%',))]
# 删帖前先采集图片文件名（主图 + 缩略图），避免删帖后无法反查
img_names = set()
if pids:
    ph = ','.join('?' * len(pids))
    for row in q1('SELECT image_path FROM post WHERE post_id IN (%s)' % ph, pids):
        if row[0]:
            img_names.add(row[0])
            img_names.add(os.path.splitext(row[0])[0] + '_thumb.jpg')
with get_cursor() as cur:
    if pids:
        ph = ','.join('?' * len(pids))
        cur.execute('DELETE FROM knowledge_chunk WHERE post_id IN (%s)' % ph, pids)
        cur.execute('DELETE FROM post_tag WHERE post_id IN (%s)' % ph, pids)
        cur.execute('DELETE FROM post WHERE post_id IN (%s)' % ph, pids)
    cur.execute('DELETE FROM post_tag WHERE tag_id IN (SELECT tag_id FROM tag WHERE tag_name LIKE ?)', (P + '%',))
    cur.execute('DELETE FROM tag WHERE tag_name LIKE ?', (P + '%',))
    cur.execute('DELETE FROM `user` WHERE username LIKE ?', (P + '%',))
# 删除测试图片文件（主图 + 缩略图），不误删真实用户图片；Windows 句柄占用时等待重试
for nm in img_names:
    fp = os.path.join(IMG_DIR, nm)
    if not os.path.exists(fp):
        continue
    for _ in range(3):
        try:
            os.remove(fp)
            break
        except PermissionError:
            time.sleep(1)
left = q0('SELECT COUNT(*) FROM post WHERE title LIKE ?', (P + '%',))
print('残留=%d' % left)
print('=' * 70)
print('结果: %d 项断言全部通过' % (11 - len(fails)))
