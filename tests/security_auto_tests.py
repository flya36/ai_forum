# -*- coding: utf-8 -*-
"""
授权安全评审 - 自动化测试套件（只读优先 / 低限速 / 不写入业务数据）

运行方式（项目根目录，使用 venv）：
    venv\\Scripts\\python.exe tests\\security_auto_tests.py

覆盖：
  S1 静态：所有 cursor.execute 均参数化（扫描 backend/ 源码）
  S2 静态：模板无 |safe / render_template_string 直渲（XSS 面）
  D1 动态：未认证端点必须 302 到 /login（访问控制）
  D2 动态：登录接口 SQL 注入载荷按字面量处理（不产生 302/500/拖库）
  D3 动态：用户名枚举差异记录（预期：存在/不存在提示不同 —— 已知发现）
  D4 动态：响应头安全基线（缺失 X-Frame-Options 等 —— 已知发现）

授权矩阵测试（需提供测试账号密码，缺失则 SKIP）：
  AUTHZ-1 普通用户访问 /admin 被拒
  AUTHZ-2 管理员访问 /admin 成功
  AUTHZ-3 用户 B 不可查看用户 A 私密帖
  AUTHZ-4 匿名帖详情页 HTML 不泄露真实作者链接（author_id）
  AUTHZ-5 管理端帖子列表对匿名帖显示掩码（预期失败 = 已知发现）
  U1-1  上传伪装图片（.jpg 但内容为文本）应被拒绝且不落盘
  U2-1  超尺寸图片应自动缩放（长边≤1920）+ 剥离 EXIF + 生成 300px 缩略图
  PRIV-07  私密帖图片越权：未登录/他人访问 403，作者 200，公开帖图片公开可访问
  PWD-1  未登录不能调用修改密码接口
  PWD-2  旧密码错误应拒绝（密码未被修改）
  PWD-3  新密码不合规应拒绝（<8位 / 与旧密码相同）
  PWD-4  成功改密 → 强制注销会话 → 旧密码登录失败 → 新密码登录成功（临时账号，结束清理）
  PWD-5  IP 限速：同一 IP 每分钟最多 5 次尝试，超过返回 429
  PWD-6  审计落库：change_password 的 success/fail/rate_limited 三类记录均存在
  MD-1  发帖含 <script>/javascript:/onerror → 详情页渲染后已过滤；编辑页回填原始 Markdown
  MD-2  预览接口返回过滤后 HTML；未登录访问被拒

凭据通过环境变量传入，不硬编码：
  TEST_ADMIN_PWD   超级管理员 fly 的密码
  TEST_USER_A_PWD  用户A flya36 的密码
  TEST_USER_B_PWD  用户B fqq  的密码
"""
import os
import re
import sys
import json
import glob
import time
import urllib.request
import urllib.error
import urllib.parse

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE_URL = os.environ.get("TEST_BASE_URL", "http://127.0.0.1:5000")
SRC_DIRS = [os.path.join(BASE, "backend"), os.path.join(BASE, "web")]
POST_IMAGES_DIR = os.path.join(BASE, "backend", "post_images")

PASS, FAIL, SKIP, INFO = "PASS", "FAIL", "SKIP", "INFO"
_report = []


def record(case, status, detail):
    _report.append((case, status, detail))
    print("[%s] %-12s %s" % (status, case, detail))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    """带 cookie 的轻量 HTTP 客户端（不跟随重定向）"""
    def __init__(self):
        self.cookies = {}
        self.opener = urllib.request.build_opener(NoRedirect)

    def _req(self, method, path, data=None, timeout=30):
        url = BASE_URL + path
        body = urllib.parse.urlencode(data or {}).encode() if data else None
        headers = {"User-Agent": "security-review"}
        if self.cookies:
            headers["Cookie"] = "; ".join("%s=%s" % kv for kv in self.cookies.items())
        if body is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            resp = self.opener.open(req, timeout=timeout)
            return resp.status, resp.read().decode("utf-8", "ignore"), dict(resp.headers)
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "ignore"), dict(e.headers)

    def get(self, path):
        return self._req("GET", path)

    def post(self, path, data=None):
        return self._req("POST", path, data)

    def save_cookies(self, headers):
        for k, v in headers.items():
            if k.lower() == "set-cookie":
                m = re.match(r"([^=]+)=([^;]+)", v)
                if m:
                    self.cookies[m.group(1)] = m.group(2)

    def get_with_csrf(self, path):
        """GET 页面并提取 CSRF 令牌（表单隐藏域 / window.CSRF_TOKEN / meta name=csrf-token）+ 保存会话 Cookie"""
        code, body, hdrs = self.get(path)
        self.save_cookies(hdrs)
        token = None
        m = re.search(r'name="_csrf_token"\s+value="([^"]+)"', body)
        if m:
            token = m.group(1)
        else:
            m = re.search(r"window\.CSRF_TOKEN = '([^']+)'", body)
            if m:
                token = m.group(1)
        if token is None:
            # CSP 收紧后 base.html 以 <meta name="csrf-token"> 注入令牌（外部 JS 读取）
            m = re.search(r'<meta name="csrf-token" content="([^"]+)"', body)
            if m:
                token = m.group(1)
        return code, body, hdrs, token

    def post_with_csrf(self, path, data, token=None):
        """POST 并附带 CSRF 令牌（补丁后所有 POST 均需令牌）"""
        d = dict(data)
        if token:
            d["_csrf_token"] = token
        return self.post(path, d)

    def post_multipart(self, path, fields, files, timeout=60):
        """multipart/form-data 上传（文件字段: {name: (filename, bytes)}）"""
        boundary = "----SecurityAutoTestsBoundary%x" % int(time.time() * 1000)
        parts = []
        for k, v in (fields or {}).items():
            parts.append(("--" + boundary).encode())
            parts.append(('Content-Disposition: form-data; name="%s"' % k).encode())
            parts.append(b"")
            parts.append(str(v).encode())
        for k, (filename, content) in (files or {}).items():
            parts.append(("--" + boundary).encode())
            parts.append(('Content-Disposition: form-data; name="%s"; filename="%s"'
                          % (k, filename)).encode())
            parts.append(b"Content-Type: application/octet-stream")
            parts.append(b"")
            parts.append(content)
        parts.append(("--" + boundary + "--").encode())
        body = b"\r\n".join(parts)
        headers = {"User-Agent": "security-review",
                   "Content-Type": "multipart/form-data; boundary=" + boundary}
        if self.cookies:
            headers["Cookie"] = "; ".join("%s=%s" % kv for kv in self.cookies.items())
        req = urllib.request.Request(url=BASE_URL + path, data=body,
                                     headers=headers, method="POST")
        try:
            resp = self.opener.open(req, timeout=timeout)
            return resp.status, resp.read().decode("utf-8", "ignore"), dict(resp.headers)
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "ignore"), dict(e.headers)


# ============================================================
# S1 静态：SQL 参数化检查
# ============================================================
def sql_python_files():
    files = []
    for d in SRC_DIRS:
        files += glob.glob(os.path.join(d, "**", "*.py"), recursive=True)
    return [f for f in files if "venv" not in f]


def test_s1_parameterized_sql():
    """所有 cursor.execute 必须使用 ? 占位；f-string 插值仅允许内部常量。"""
    risky = []
    fstring_exec = []
    for f in sql_python_files():
        src = open(f, encoding="utf-8").read()
        # 字符串拼接型：execute("... " + var)
        for m in re.finditer(r'execute\s*\(\s*["\'][^"\']*"\s*\+', src):
            risky.append((f, "string concat in execute"))
        # f-string 动态 SQL
        for m in re.finditer(r'execute\s*\(\s*f["\']', src):
            fstring_exec.append((f, src.count("\n", 0, m.start()) + 1))
    # 已知允许的白名单：内部常量拼接（where 子句、计数子查询），人工核查结论
    allowed_fstring_lines = {
        os.path.join(BASE, "backend", "services", "board_service.py"): {242, 254},
        os.path.join(BASE, "backend", "services", "tag_service.py"): {63, 73},
        os.path.join(BASE, "backend", "services", "user_service.py"): {18},
    }
    unexpected = [
        (f, ln) for f, ln in fstring_exec
        if ln not in allowed_fstring_lines.get(os.path.normpath(f), set())
    ]
    if risky:
        record("S1-1", FAIL, "发现字符串拼接型 execute: %s" % risky[:5])
    else:
        record("S1-1", PASS, "无字符串拼接型 execute")
    if unexpected:
        record("S1-2", FAIL, "f-string execute 超出已核查白名单: %s" % unexpected[:5])
    else:
        record("S1-2", PASS, "f-string execute 全部属于已人工核查的内部常量插值（board/tag/user 共 5 处，均不可注入）")


# ============================================================
# S2 静态：模板 XSS 面
# ============================================================
def test_s2_template_xss_surface():
    tpl_dir = os.path.join(BASE, "web", "templates")
    bad = []
    for f in glob.glob(os.path.join(tpl_dir, "*.html")):
        src = open(f, encoding="utf-8").read()
        for m in re.finditer(r"\{\{[^}]*\|safe\s*\}\}", src):
            bad.append((os.path.basename(f), "|safe filter"))
        if "render_template_string" in src:
            bad.append((os.path.basename(f), "render_template_string"))
    if bad:
        record("S2-1", FAIL, "模板 XSS 面: %s" % bad)
    else:
        record("S2-1", PASS, "模板无 |safe / render_template_string（Jinja 自动转义生效）")
    # 服务端构造 JSON 是否含 HTML？——检查 app.py 中 json.dumps 无 html 转义开关
    app_src = open(os.path.join(BASE, "web", "app.py"), encoding="utf-8").read()
    if 'json.dumps' in app_src and 'ensure_ascii=False' in app_src:
        record("S2-2", INFO, "json.dumps 使用 ensure_ascii=False（服务端 JSON 含未转义字符，前端须 textContent 写入）")


# ============================================================
# D1 动态：未认证访问控制
# ============================================================
AUTH_REQUIRED_ENDPOINTS = [
    ("/", "首页"),
    ("/admin", "管理后台"),
    ("/post/1", "帖子详情"),
    ("/api/search?q=x", "搜索API"),
    ("/api/ask?question=x", "AI问答API"),
    ("/favorites", "收藏"),
    ("/notifications", "通知"),
    ("/user/1", "个人主页"),
]


def test_d1_unauthenticated_access_control():
    c = Client()
    for path, name in AUTH_REQUIRED_ENDPOINTS:
        code, body, hdrs = c.get(path)
        if code == 302 and "/login" in hdrs.get("Location", ""):
            record("D1-%s" % name, PASS, "%s -> 302 /login" % path)
        else:
            record("D1-%s" % name, FAIL, "%s -> %s (期望302到/login)" % (path, code))


# ============================================================
# D2 动态：登录接口 SQL 注入行为（只读，载荷按字面量处理）
# ============================================================
SQLI_PAYLOADS = [
    "' OR '1'='1",
    "' OR 1=1 -- ",
    "admin'--",
    "'; DROP TABLE `user`; -- ",
    "1' UNION SELECT user_id,username,password_hash,4,5 FROM `user` -- ",
]


def test_d2_login_sqli_behavior():
    c = Client()
    for payload in SQLI_PAYLOADS:
        # 先取 CSRF 令牌与会话 Cookie（补丁后所有 POST 需令牌）
        _, _, _, token = c.get_with_csrf("/login")
        code, body, hdrs = c.post_with_csrf("/login", {"username": payload, "password": "x"}, token)
        loc = hdrs.get("Location", "")
        # 注入成功特征：302 到 /（登录成功）或 500/异常
        if code == 302 and "/login" not in loc:
            record("D2-%s" % payload[:20], FAIL, "注入疑似生效: %s -> %s" % (code, loc))
        elif code == 500:
            record("D2-%s" % payload[:20], FAIL, "服务端异常(500): %s" % payload)
        elif "用户名不存在" in body or "密码错误" in body or "用户名和密码" in body:
            record("D2-%s" % payload[:20], PASS, "载荷按字面量处理（%s）" % code)
        else:
            record("D2-%s" % payload[:20], INFO, "响应未识别: code=%s" % code)
        time.sleep(0.5)  # 低限速


# ============================================================
# D3 动态：用户名枚举
# ============================================================
def test_d3_user_enumeration():
    c = Client()
    _, _, _, t1_token = c.get_with_csrf("/login")
    _, b1, _ = c.post_with_csrf("/login", {"username": "nonexist_user_zzz_%d" % int(time.time()), "password": "x"}, t1_token)
    _, _, _, t2_token = c.get_with_csrf("/login")
    # 已存在用户取临时账号名（不依赖真实账号），错误密码触发与不存在用户一致的提示
    exist_user = _EPHEMERAL.get("userA", {}).get("name", "flya36")
    _, b2, _ = c.post_with_csrf("/login", {"username": exist_user, "password": "wrongpass_zzz"}, t2_token)
    m1 = re.search(r"alert[^>]*>([^<]+)", b1)
    m2 = re.search(r"alert[^>]*>([^<]+)", b2)
    t1 = m1.group(1).strip() if m1 else "?"
    t2 = m2.group(1).strip() if m2 else "?"
    if t1 != t2 and "不存在" in t1 and "密码" in t2:
        record("D3-1", FAIL, "用户名枚举: 不存在='%s' vs 已存在(错密)='%s' —— 攻击者可探测账号存在性" % (t1, t2))
    elif "密码" in t1 and "密码" in t2:
        record("D3-1", PASS, "登录提示已统一（无法枚举）: '%s'" % t1)
    else:
        record("D3-1", INFO, "提示差异: '%s' / '%s'" % (t1, t2))


# ============================================================
# D4 动态：安全响应头基线
# ============================================================
def test_d4_security_headers():
    c = Client()
    code, body, hdrs = c.get("/login")
    missing = [h for h in ("X-Frame-Options", "Content-Security-Policy", "X-Content-Type-Options", "Referrer-Policy") if h.lower() not in {k.lower() for k in hdrs}]
    if missing:
        record("D4-1", FAIL, "缺失安全响应头: %s" % ", ".join(missing))
    else:
        record("D4-1", PASS, "安全响应头完整")


# ============================================================
# 测试安全阀：绝不允许测试用例操作真实账号
# 规则：
#   - 若显式提供 TEST_*_PWD，对应 TEST_*_NAME 必须是 "sec" 前缀的临时账号，
#     否则直接抛异常终止（防止误用 fly/flya36/fqq 等真实账号）；
#   - 未提供 TEST_*_PWD 时，走"注册 sec* 临时账号"路径（安全），无需凭据。
# ============================================================
def _assert_test_safety():
    """启动安全阀：检测到试图使用真实账号立即终止。"""
    pairs = (
        ("TEST_ADMIN_PWD", "TEST_ADMIN_NAME"),
        ("TEST_USER_A_PWD", "TEST_USER_A_NAME"),
        ("TEST_USER_B_PWD", "TEST_USER_B_NAME"),
    )
    for pwd_var, name_var in pairs:
        pwd = os.environ.get(pwd_var)
        name = os.environ.get(name_var)
        if pwd:
            if not name or not name.startswith("sec"):
                raise RuntimeError(
                    "测试安全阀触发：%s 已设置但账号名 %r 不是 sec* 临时账号，"
                    "绝不允许测试操作真实账号，已终止运行" % (pwd_var, name))
            if name.startswith("sec"):
                print("    [安全阀] %s=%s 使用临时账号（sec* 前缀，通过）" % (name_var, name))
    print("    [安全阀] 通过：未检测到真实账号凭据，允许运行")


ADMIN_PWD = os.environ.get("TEST_ADMIN_PWD")
USER_A_PWD = os.environ.get("TEST_USER_A_PWD")
USER_B_PWD = os.environ.get("TEST_USER_B_PWD")
_EPHEMERAL = {}   # role -> {"name": ..., "pwd": ...}


def _ensure_ephemeral_accounts():
    """创建临时测试账号（注册接口 + DB 提权），运行结束清理。
    安全阀：不接受 fly/flya36/fqq 等真实账号——若环境变量提供了真实账号名，
    直接抛异常终止（由 _assert_test_safety 在启动时拦截）。"""
    global ADMIN_PWD, USER_A_PWD, USER_B_PWD
    # 显式提供凭据时，仅接受 sec* 前缀临时账号名（与安全阀一致）
    if ADMIN_PWD and USER_A_PWD and USER_B_PWD:
        names = (
            os.environ.get("TEST_ADMIN_NAME", ""),
            os.environ.get("TEST_USER_A_NAME", ""),
            os.environ.get("TEST_USER_B_NAME", ""),
        )
        if not all(n.startswith("sec") for n in names):
            raise RuntimeError(
                "测试安全阀触发：TEST_*_PWD 提供的账号名必须为 sec* 临时账号，"
                "拒绝使用真实账号（当前：%r）" % (names,))
        _EPHEMERAL["admin"] = {"name": names[0], "pwd": ADMIN_PWD}
        _EPHEMERAL["userA"] = {"name": names[1], "pwd": USER_A_PWD}
        _EPHEMERAL["userB"] = {"name": names[2], "pwd": USER_B_PWD}
        return
    ts = str(int(time.time()))[-6:]
    pwd = "SecTest%sA1" % ts
    roles = [("admin", "secadm%s" % ts), ("userA", "secusa%s" % ts), ("userB", "secusb%s" % ts)]
    ok_all = True
    for role, uname in roles:
        c = Client()
        _, _, _, token = c.get_with_csrf("/register")
        code, _, hdrs = c.post_with_csrf(
            "/register", {"username": uname, "password": pwd, "nickname": uname}, token)
        c.save_cookies(hdrs)
        if code != 302:
            ok_all = False
            print("    [临时账号] %s 注册失败 code=%s" % (role, code))
            continue
        _EPHEMERAL[role] = {"name": uname, "pwd": pwd}
    # admin 提权（DB，测试环境专用）
    if "admin" in _EPHEMERAL:
        try:
            _db_set_role(_EPHEMERAL["admin"]["name"], 1)
        except Exception as e:
            print("    [临时账号] admin 提权失败: %s" % e)
            ok_all = False
    if ok_all and len(_EPHEMERAL) == 3:
        ADMIN_PWD = USER_A_PWD = USER_B_PWD = pwd
    else:
        ADMIN_PWD = USER_A_PWD = USER_B_PWD = None
        print("    [临时账号] 创建不完整，相关用例将 SKIP")


def _db_set_role(username, role):
    """DB 更新角色（仅测试环境）"""
    import sys as _sys
    _sys.path.insert(0, os.path.join(BASE, "backend"))
    _sys.path.insert(0, os.path.join(BASE, "backend", "core"))
    _sys.path.insert(0, os.path.join(BASE, "backend", "config"))
    from db import get_cursor
    with get_cursor() as cur:
        cur.execute("UPDATE `user` SET role = ? WHERE username = ?", (role, username))


def _db_find_post_id_by_title(title):
    """DB 按标题取最新帖子 post_id（发帖成功重定向 /，列表异步刷新，无法从响应拿 ID）"""
    import sys as _sys
    _sys.path.insert(0, os.path.join(BASE, "backend"))
    _sys.path.insert(0, os.path.join(BASE, "backend", "core"))
    _sys.path.insert(0, os.path.join(BASE, "backend", "config"))
    from db import get_cursor
    with get_cursor() as cur:
        cur.execute("SELECT post_id FROM post WHERE title = ? ORDER BY post_id DESC LIMIT 1", (title,))
        row = cur.fetchone()
        return row[0] if row else None


def _db_get_user_id(username):
    """DB 查用户名对应的 user_id"""
    import sys as _sys
    _sys.path.insert(0, os.path.join(BASE, "backend"))
    _sys.path.insert(0, os.path.join(BASE, "backend", "core"))
    _sys.path.insert(0, os.path.join(BASE, "backend", "config"))
    from db import get_cursor
    with get_cursor() as cur:
        cur.execute("SELECT user_id FROM `user` WHERE username = ?", (username,))
        row = cur.fetchone()
        return row[0] if row else None


def _db_count_mention_by_post_user(post_id, user_id):
    """统计某帖子下发给指定用户的 mention 通知条数"""
    import sys as _sys
    _sys.path.insert(0, os.path.join(BASE, "backend"))
    _sys.path.insert(0, os.path.join(BASE, "backend", "core"))
    _sys.path.insert(0, os.path.join(BASE, "backend", "config"))
    from db import get_cursor
    with get_cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM notification WHERE post_id = ? AND type = 'mention' AND user_id = ?",
            (post_id, user_id))
        return cur.fetchone()[0]


def _db_count_notifications_by_post(post_id):
    """统计某帖子关联的全部通知条数"""
    import sys as _sys
    _sys.path.insert(0, os.path.join(BASE, "backend"))
    _sys.path.insert(0, os.path.join(BASE, "backend", "core"))
    _sys.path.insert(0, os.path.join(BASE, "backend", "config"))
    from db import get_cursor
    with get_cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM notification WHERE post_id = ?", (post_id,))
        return cur.fetchone()[0]


def _db_delete_user_by_name(username):
    """DB 清理指定用户名及其数据残留（通知/评论/帖子/用户）"""
    import sys as _sys
    _sys.path.insert(0, os.path.join(BASE, "backend"))
    _sys.path.insert(0, os.path.join(BASE, "backend", "core"))
    _sys.path.insert(0, os.path.join(BASE, "backend", "config"))
    from db import get_cursor
    with get_cursor() as cur:
        cur.execute("SELECT user_id FROM `user` WHERE username = ?", (username,))
        row = cur.fetchone()
        uid = row[0] if row else None
        if uid:
            cur.execute("DELETE FROM notification WHERE user_id = ?", (uid,))
            cur.execute("DELETE FROM `comment` WHERE user_id = ?", (uid,))
            cur.execute("DELETE FROM post WHERE author_id = ?", (uid,))
        cur.execute("DELETE FROM `user` WHERE username = ?", (username,))


def _cleanup_ephemeral_accounts():
    """清理临时测试账号及其数据残留（通知/评论/帖子）"""
    try:
        import sys as _sys
        _sys.path.insert(0, os.path.join(BASE, "backend"))
        _sys.path.insert(0, os.path.join(BASE, "backend", "core"))
        _sys.path.insert(0, os.path.join(BASE, "backend", "config"))
        from db import get_cursor
        for role in ("admin", "userA", "userB"):
            acc = _EPHEMERAL.get(role)
            if not acc:
                continue
            with get_cursor() as cur:
                cur.execute("SELECT user_id FROM `user` WHERE username = ?", (acc["name"],))
                row = cur.fetchone()
                uid = row[0] if row else None
                if uid:
                    cur.execute("DELETE FROM notification WHERE user_id = ?", (uid,))
                    cur.execute("DELETE FROM `comment` WHERE user_id = ?", (uid,))
                    cur.execute("DELETE FROM post WHERE author_id = ?", (uid,))
                    # 举报/申诉工单：清理该用户提交的工单及以其为目标的申诉工单
                    cur.execute("DELETE FROM report_tickets WHERE reporter_id = ? OR target_id = ?", (uid, uid))
                    # 关注关系：清理该用户发起或收到的关注
                    cur.execute("DELETE FROM user_follows WHERE follower_id = ? OR followee_id = ?", (uid, uid))
                    # 私信：清理该用户收发消息
                    cur.execute("DELETE FROM message WHERE sender_id = ? OR receiver_id = ?", (uid, uid))
                cur.execute("DELETE FROM `user` WHERE username = ?", (acc["name"],))
    except Exception as e:
        print("    [临时账号清理] 失败: %s" % e)


def _login(c, username, password):
    """CSRF 感知登录：取令牌 → POST → 保存会话 Cookie；成功返回 302"""
    _, _, _, token = c.get_with_csrf("/login")
    code, body, hdrs = c.post_with_csrf("/login", {"username": username, "password": password}, token)
    c.save_cookies(hdrs)
    return code, hdrs.get("Location", "")


def _login_ephemeral(role, client=None):
    """用临时测试账号登录（role: admin/userA/userB）；账号未就绪返回 (0, "")"""
    acc = _EPHEMERAL.get(role)
    if not acc:
        return (0, "")
    return _login(client or Client(), acc["name"], acc["pwd"])


def _parse_admin_posts(body):
    """从管理端帖子表格解析 (post_id, 标记集合)，用于 AUTHZ 夹具发现（只读）"""
    rows = []
    for block in re.findall(r"<tr>(.*?)</tr>", body, re.S):
        m = re.search(r"<td>(\d+)</td>", block)
        if not m:
            continue
        pid = int(m.group(1))
        flags = set()
        if "tag-private" in block:
            flags.add("private")
        if "tag-anon" in block:
            flags.add("anon")
        rows.append((pid, flags))
    return rows


def test_authz_matrix():
    if not (ADMIN_PWD and USER_A_PWD and USER_B_PWD):
        record("AUTHZ", SKIP, "临时测试账号未就绪，授权矩阵动态测试跳过（静态结论见报告）")
        return
    admin, ua, ub = Client(), Client(), Client()
    ra, rua, rub = _login_ephemeral("admin", admin), _login_ephemeral("userA", ua), _login_ephemeral("userB", ub)
    if not (ra[0] == 302 and rua[0] == 302 and rub[0] == 302):
        record("AUTHZ-0", FAIL, "登录失败 codes: admin=%s/%s ua=%s/%s ub=%s/%s"
               % (ra[0], ra[1], rua[0], rua[1], rub[0], rub[1]))
        return
    record("AUTHZ-0", PASS, "三个测试账号登录成功（临时账号）")
    # AUTHZ-1 普通用户访问 /admin 被拒（垂直越权）
    code, body, hdrs = ub.get("/admin")
    if code == 302 and "/admin" not in hdrs.get("Location", ""):
        record("AUTHZ-1", PASS, "用户B访问/admin被拒 (%s -> %s)" % (code, hdrs.get("Location", "")))
    else:
        record("AUTHZ-1", FAIL, "用户B访问/admin -> %s（可能越权）" % code)
    # AUTHZ-2 管理员访问 /admin
    code, body, hdrs = admin.get("/admin")
    if code != 200:
        record("AUTHZ-2", FAIL, "管理员访问/admin -> %s" % code)
        return
    record("AUTHZ-2", PASS, "管理员访问/admin -> 200")
    rows = _parse_admin_posts(body)
    # AUTHZ-3 用户B不可查看他人私密帖（水平越权）：自建私密帖夹具，不依赖真实数据
    priv_title = "AUTHZ-PRIV-%d" % int(time.time())
    _, _, _, token = ua.get_with_csrf("/post/new")
    code, _, hdrs = ua.post_with_csrf(
        "/post/new", {"title": priv_title, "content": "私密夹具", "board_id": 1, "is_private": "1"}, token)
    ua.save_cookies(hdrs)
    # 发帖成功统一重定向 /（新帖列表异步刷新），post_id 从 DB 按标题取
    pid = _db_find_post_id_by_title(priv_title)
    if pid:
        code, body, hdrs = ub.get("/post/%d" % pid)
        if code == 302 and "post/" not in hdrs.get("Location", ""):
            record("AUTHZ-3", PASS, "用户B访问他人私密帖#%d被拒 (%s)" % (pid, code))
        else:
            record("AUTHZ-3", FAIL, "用户B访问私密帖#%d -> %s（可能越权）" % (pid, code))
        _delete_post_by_id(ua, pid)
    else:
        record("AUTHZ-3", FAIL, "自建私密帖夹具失败（DB 未找到帖子）")
    # AUTHZ-4 匿名帖详情对普通用户不泄露作者链接
    anon = next((r for r in rows if "anon" in r[1]), None)
    if not anon:
        record("AUTHZ-4", INFO, "无匿名帖夹具，跳过")
    else:
        code, body, hdrs = ub.get("/post/%d" % anon[0])
        masked = '<div class="fw-bold text-muted">匿名用户</div>' in body
        # 仅检测"匿名用户"被包裹进作者链接（评论区的正常用户链接不算泄露）
        author_link_leak = bool(re.search(r'<a href="/user/\d+"[^>]*>\s*匿名用户', body))
        if code == 200 and masked and not author_link_leak:
            record("AUTHZ-4", PASS, "匿名帖#%d 显示匿名且无作者链接" % anon[0])
        elif code != 200:
            record("AUTHZ-4", FAIL, "匿名帖#%d 访问异常 code=%s" % (anon[0], code))
        else:
            record("AUTHZ-4", FAIL, "匿名帖#%d 掩码缺失(masked=%s)或作者链接泄露(leak=%s)" % (anon[0], masked, author_link_leak))


# ============================================================
# U1/U2 图片上传安全（需测试账号密码 + Pillow，缺失 SKIP）
# ============================================================
def _find_post_id_by_title(c, title):
    """在首页帖子卡片中按标题定位 post_id（卡片 id 与标题链接 id 需一致）"""
    code, body, _ = c.get("/")
    m = re.search(r'data-post-card="(\d+)"[\s\S]*?<a href="/post/\1" class="post-title[^"]*">'
                  + re.escape(title) + r"</a>", body)
    return int(m.group(1)) if m else None


def _cleanup_upload(c, title, files):
    """清理测试数据：删除测试帖子（HTTP）并移除上传产生的文件"""
    pid = _find_post_id_by_title(c, title)
    if pid:
        _, _, _, token = c.get_with_csrf("/post/%d" % pid)
        c.post_with_csrf("/post/%d/delete" % pid, {}, token)
    for f in files:
        try:
            os.remove(os.path.join(POST_IMAGES_DIR, f))
        except OSError:
            pass


def _delete_post_by_id(c, pid):
    """按 post_id 删除测试帖子（作者本人 HTTP 删除，触发级联清理）"""
    if pid:
        try:
            _, _, _, token = c.get_with_csrf("/post/%d" % pid)
            c.post_with_csrf("/post/%d/delete" % pid, {}, token)
        except Exception as e:
            print("    [清理] 帖子#%s 删除异常: %s" % (pid, e))


def test_u1_fake_image_rejected():
    """伪装图片（.jpg 扩展名但内容为文本）应被拒绝，不落盘"""
    if not USER_A_PWD:
        record("U1-1", SKIP, "未提供 TEST_USER_A_PWD，伪装文件上传测试跳过")
        return
    c = Client()
    _login_ephemeral("userA", c)
    before = set(os.listdir(POST_IMAGES_DIR))
    title = "UPLOAD-FAKE-%d" % int(time.time())
    _, _, _, token = c.get_with_csrf("/post/new")
    fake = (b"<html><body>this is definitely not an image</body></html>\n" * 20)
    code, body, hdrs = c.post_multipart(
        "/post/new",
        {"title": title, "content": "x", "board_id": 1, "_csrf_token": token},
        {"image": ("fake.jpg", fake)},
    )
    c.save_cookies(hdrs)  # 302 后会话 Cookie 携带 flash，需更新后再读页面
    after = set(os.listdir(POST_IMAGES_DIR))
    new_files = sorted(after - before)
    _, page, _ = c.get("/")
    rejected_msg = "不是有效图片" in page
    if not new_files and code == 302 and rejected_msg:
        record("U1-1", PASS, "伪装 .jpg（纯文本）被拒绝：无文件落盘，页面提示'不是有效图片'")
    else:
        record("U1-1", FAIL, "伪装文件未被正确拒绝: code=%s 新文件=%s 提示=%s" % (code, new_files, rejected_msg))
    _cleanup_upload(c, title, new_files)


def test_u2_oversized_image_scaled():
    """超大图（4000x2000 带EXIF）应自动缩放至长边≤1920、剥离EXIF、生成300px缩略图"""
    if not USER_A_PWD:
        record("U2-1", SKIP, "未提供 TEST_USER_A_PWD，超尺寸图片测试跳过")
        return
    from PIL import Image as PILImage
    import io
    src = PILImage.new("RGB", (4000, 2000), (120, 180, 240))
    exif = PILImage.Exif()
    exif[0x010F] = "SecurityTestCam"
    buf = io.BytesIO()
    src.save(buf, "JPEG", quality=95, exif=exif)
    c = Client()
    _login_ephemeral("userA", c)
    before = set(os.listdir(POST_IMAGES_DIR))
    title = "UPLOAD-BIG-%d" % int(time.time())
    _, _, _, token = c.get_with_csrf("/post/new")
    code, body, _ = c.post_multipart(
        "/post/new",
        {"title": title, "content": "x", "board_id": 1, "_csrf_token": token},
        {"image": ("big.png", buf.getvalue())},
    )
    new_files = sorted(set(os.listdir(POST_IMAGES_DIR)) - before)
    fulls = [f for f in new_files if f.endswith(".jpg") and not f.endswith("_thumb.jpg")]
    thumbs = [f for f in new_files if f.endswith("_thumb.jpg")]
    detail = ""
    if code == 302 and fulls and thumbs:
        fp = os.path.join(POST_IMAGES_DIR, fulls[0])
        with PILImage.open(fp) as im:
            w, h = im.size
            exif_left = len(im.getexif())
        with PILImage.open(os.path.join(POST_IMAGES_DIR, thumbs[0])) as im2:
            tw, th = im2.size
        if max(w, h) <= 1920 and tw == 300 and exif_left == 0:
            record("U2-1", PASS,
                   "4000x2000→%dx%d（≤1920），缩略图300px宽，EXIF已剥离" % (w, h))
        else:
            detail = "full=%dx%d exif=%d thumb=%dx%d" % (w, h, exif_left, tw, th)
            record("U2-1", FAIL, "缩放/缩略图/EXIF 未达预期: " + detail)
    else:
        record("U2-1", FAIL, "上传未成功或产物缺失: code=%s full=%d thumb=%d" % (code, len(fulls), len(thumbs)))
    _cleanup_upload(c, title, new_files)


def _admin_post_id_by_title(admin, title):
    """从管理端帖子表格按标题定位 post_id（私密帖在管理端可见）"""
    code, body, _ = admin.get("/admin")
    if code != 200:
        return None
    for block in re.findall(r"<tr>(.*?)</tr>", body, re.S):
        if re.search(re.escape(title), block):
            m = re.search(r"<td>(\d+)</td>", block)
            if m:
                return int(m.group(1))
    return None


def _admin_delete_post(admin, pid, files):
    """管理员删除测试帖（HTTP）+ 移除上传文件"""
    if pid:
        _, _, _, token = admin.get_with_csrf("/admin")
        admin.post_with_csrf("/admin/post/%d/delete" % pid, {}, token)
    for f in files:
        try:
            os.remove(os.path.join(POST_IMAGES_DIR, f))
        except OSError:
            pass


def test_priv07_private_image_acl():
    """私密帖图片越权防护：未登录/他人访问私密帖图片应 403，作者 200，公开帖图片公开可访问"""
    if not (ADMIN_PWD and USER_A_PWD and USER_B_PWD):
        record("PRIV-07", SKIP, "未提供测试账号密码，私密帖图片越权测试跳过")
        return
    from PIL import Image as PILImage
    import io
    buf = io.BytesIO()
    PILImage.new("RGB", (90, 60), (30, 120, 220)).save(buf, "PNG")
    admin, ua, ub = Client(), Client(), Client()
    _login_ephemeral("admin", admin)
    _login_ephemeral("userA", ua)
    _login_ephemeral("userB", ub)
    t_priv = "PRIV07-P-%d" % int(time.time())
    t_pub = "PRIV07-O-%d" % int(time.time())
    _, _, _, tk1 = ua.get_with_csrf("/post/new")
    c1, _, h1 = ua.post_multipart(
        "/post/new",
        {"title": t_priv, "content": "x", "board_id": 1, "is_private": "1", "_csrf_token": tk1},
        {"image": ("p.png", buf.getvalue())})
    ua.save_cookies(h1)
    _, _, _, tk2 = ua.get_with_csrf("/post/new")
    c2, _, h2 = ua.post_multipart(
        "/post/new",
        {"title": t_pub, "content": "x", "board_id": 1, "_csrf_token": tk2},
        {"image": ("o.png", buf.getvalue())})
    ua.save_cookies(h2)
    if c1 != 302 or c2 != 302:
        record("PRIV-07", FAIL, "测试帖创建失败 codes=%s/%s" % (c1, c2))
        _admin_delete_post(admin, _admin_post_id_by_title(admin, t_priv), [])
        _admin_delete_post(admin, _admin_post_id_by_title(admin, t_pub), [])
        return
    # 从详情页 HTML 提取各帖图片 URL（作者视角可读）
    def _img_of(title):
        pid = _admin_post_id_by_title(admin, title)
        if not pid:
            return None
        _, body, _ = ua.get("/post/%d" % pid)
        m = re.search(r"/uploads/post_images/([^\"'?]+)", body)
        return m.group(1) if m else None
    priv_img = _img_of(t_priv)
    pub_img = _img_of(t_pub)
    if not priv_img or not pub_img:
        record("PRIV-07", FAIL, "未从详情页提取到图片 URL: priv=%s pub=%s" % (priv_img, pub_img))
        _admin_delete_post(admin, _admin_post_id_by_title(admin, t_priv), [])
        _admin_delete_post(admin, _admin_post_id_by_title(admin, t_pub), [])
        return
    anon = Client()
    checks = [
        ("未登录访问私密帖图片", anon.get("/uploads/post_images/" + priv_img)[0] == 403),
        ("用户B访问私密帖图片", ub.get("/uploads/post_images/" + priv_img)[0] == 403),
        ("用户B访问私密帖缩略图", ub.get("/uploads/post_images/"
                                        + priv_img[:-4] + "_thumb.jpg")[0] == 403),
        ("作者访问私密帖图片", ua.get("/uploads/post_images/" + priv_img)[0] == 200),
        ("用户B访问公开帖图片", ub.get("/uploads/post_images/" + pub_img)[0] == 200),
        ("未登录访问公开帖图片", anon.get("/uploads/post_images/" + pub_img)[0] == 200),
    ]
    ok = all(v for _, v in checks)
    record("PRIV-07", "PASS" if ok else "FAIL",
           "; ".join("%s=%s" % (n, "OK" if v else "FAIL") for n, v in checks))
    _admin_delete_post(admin, _admin_post_id_by_title(admin, t_priv),
                       [priv_img, priv_img[:-4] + "_thumb.jpg"])
    _admin_delete_post(admin, _admin_post_id_by_title(admin, t_pub),
                       [pub_img, pub_img[:-4] + "_thumb.jpg"])


# ============================================================
# 修改密码（PWD）测试组
# ============================================================
def _pwd_change(c, old_pwd, new_pwd, confirm=None):
    """以当前会话提交修改密码，返回 (code, location, body)"""
    _, _, _, token = c.get_with_csrf("/profile/edit")
    confirm = confirm if confirm is not None else new_pwd
    return c.post_with_csrf(
        "/profile/change-password",
        {"old_password": old_pwd, "new_password": new_pwd,
         "confirm_password": confirm}, token)


def _db_cleanup_user(username_prefix):
    """删除测试自建账号（PWD 成功用例用，避免残留）"""
    try:
        import sys as _sys
        _sys.path.insert(0, os.path.join(BASE, "backend"))
        _sys.path.insert(0, os.path.join(BASE, "backend", "core"))
        _sys.path.insert(0, os.path.join(BASE, "backend", "config"))
        from db import get_cursor
        with get_cursor() as cur:
            cur.execute("DELETE FROM `user` WHERE username LIKE ?", (username_prefix + "%",))
    except Exception as e:
        print("    [PWD清理] DB 清理失败: %s" % e)


def test_pwd_01_requires_login():
    """未登录不能调修改密码接口：带 token 的 POST 应被重定向到 /login（会话未建立）"""
    anon = Client()
    _, _, _, token = anon.get_with_csrf("/login")
    code, body, hdrs = anon.post_with_csrf(
        "/profile/change-password",
        {"old_password": "x1234567", "new_password": "y1234567", "confirm_password": "y1234567"},
        token)
    loc = hdrs.get("Location", "")
    ok = code == 302 and "/login" in loc
    record("PWD-1", "PASS" if ok else "FAIL",
           "未登录 POST 修改密码 -> %s Location=%s" % (code, loc))


def test_pwd_02_old_password_wrong():
    """旧密码错误应拒绝：改密失败后旧密码登录依然有效（密码未被修改）"""
    if not USER_A_PWD:
        record("PWD-2", SKIP, "未提供 TEST_USER_A_PWD")
        return
    c = Client()
    _login_ephemeral("userA", c)
    code, loc, _ = _pwd_change(c, "wrong_password_zzz", "NewPass123!")
    # 失败应重定向回设置页
    ok = code == 302 and "/profile/edit" in (loc or "")
    # 用旧密码重新登录应成功（证明密码未变）
    c2 = Client()
    code2, _ = _login_ephemeral("userA", c2)
    ok = ok and code2 == 302
    record("PWD-2", "PASS" if ok else "FAIL",
           "旧密码错误 -> %s Location=%s; 旧密码仍可登录=%s" % (code, loc, code2 == 302))


def test_pwd_03_new_password_invalid():
    """新密码不合规应拒绝：过短（<8位）与新旧相同两种情况"""
    if not USER_A_PWD:
        record("PWD-3", SKIP, "未提供 TEST_USER_A_PWD")
        return
    # 3a. 新密码过短
    c = Client()
    _login_ephemeral("userA", c)
    code, loc, _ = _pwd_change(c, USER_A_PWD, "Ab12345")  # 7 位
    ok_a = code == 302 and "/profile/edit" in (loc or "")
    # 3b. 新密码与旧密码相同
    c = Client()
    _login_ephemeral("userA", c)
    code, loc, _ = _pwd_change(c, USER_A_PWD, USER_A_PWD)
    ok_b = code == 302 and "/profile/edit" in (loc or "")
    # 两种拒绝后旧密码仍可登录
    c2 = Client()
    code2, _ = _login_ephemeral("userA", c2)
    ok = ok_a and ok_b and code2 == 302
    record("PWD-3", "PASS" if ok else "FAIL",
           "过短=%s 新旧相同=%s 旧密码仍可登录=%s" % (ok_a, ok_b, code2 == 302))


def test_pwd_04_success_flow():
    """成功改密：强制注销会话 → 旧密码登录失败 → 新密码登录成功（用临时账号，结束清理）"""
    uniq = str(int(time.time()))[-6:]
    uname = "pwde2e_" + uniq
    orig = "OldPass_%s" % uniq
    newp = "NewPass_%s" % uniq
    c = Client()
    _, _, _, token = c.get_with_csrf("/register")
    code, _, hdrs = c.post_with_csrf(
        "/register", {"username": uname, "password": orig, "nickname": "改密测试"}, token)
    c.save_cookies(hdrs)
    if code != 302:
        record("PWD-4", FAIL, "临时账号注册失败 code=%s" % code)
        return
    _, _, _, token = c.get_with_csrf("/login")
    code, _, hdrs = c.post_with_csrf("/login", {"username": uname, "password": orig}, token)
    c.save_cookies(hdrs)
    if code != 302:
        record("PWD-4", FAIL, "临时账号登录失败 code=%s" % code)
        return
    # 成功改密 → 强制注销：重定向到 /login
    code, loc, _ = _pwd_change(c, orig, newp)
    ok = code == 302 and "/login" in (loc or "")
    # 会话已注销：旧密码登录失败（登录失败渲染登录页 200 或未跳转首页），新密码登录成功
    c3 = Client()
    code_old, loc_old = _login(c3, uname, orig)
    old_rejected = not (code_old == 302 and "/" in (loc_old or "")
                        and "/login" not in (loc_old or ""))
    ok = ok and old_rejected
    c4 = Client()
    code_new, loc_new = _login(c4, uname, newp)
    ok = ok and code_new == 302 and "/" in (loc_new or "") and "/login" not in (loc_new or "")
    record("PWD-4", "PASS" if ok else "FAIL",
           "改密后重定向=%s 旧密码登录被拒=%s(%s) 新密码登录成功=%s(%s)"
           % (loc, old_rejected, code_old, code_new == 302, loc_new))
    _db_cleanup_user("pwde2e_")


def test_pwd_05_rate_limited():
    """IP 限速：同一 IP 每分钟最多 5 次改密尝试，超过返回 429
    （PWD-2/3/4 已消耗 4 次，本用例连发应在窗口内触发 429）"""
    if not USER_A_PWD:
        record("PWD-5", SKIP, "未提供 TEST_USER_A_PWD")
        return
    c = Client()
    _login_ephemeral("userA", c)
    got_429 = False
    for i in range(6):
        code, _, _ = _pwd_change(c, "wrong_%d_zzz" % i, "NewPass123!")
        if code == 429:
            got_429 = True
            break
    record("PWD-5", "PASS" if got_429 else "FAIL",
           "6 次内触发 429=%s（窗口内 5 次上限）" % got_429)


def _db_audit_rows(action, result=None):
    """查询 audit_log（只读）：返回 (总条数, 各 result 计数 dict)"""
    import sys as _sys
    _sys.path.insert(0, os.path.join(BASE, "backend"))
    _sys.path.insert(0, os.path.join(BASE, "backend", "core"))
    _sys.path.insert(0, os.path.join(BASE, "backend", "config"))
    from db import get_cursor
    with get_cursor() as cur:
        cur.execute("SELECT detail FROM audit_log WHERE action = ?", (action,))
        rows = cur.fetchall()
    counts = {"success": 0, "fail": 0, "rate_limited": 0}
    for (detail,) in rows:
        for key in counts:
            if ("result=%s" % key) in (detail or ""):
                counts[key] += 1
    return len(rows), counts


def test_pwd_06_audit_logged():
    """审计落库：本轮改密测试在 audit_log 中留有 success/fail/rate_limited 三类记录"""
    total, counts = _db_audit_rows("change_password")
    ok = (total >= 3 and counts["success"] >= 1 and counts["fail"] >= 1
          and counts["rate_limited"] >= 1)
    record("PWD-6", "PASS" if ok else "FAIL",
           "change_password 审计条数=%s success=%s fail=%s rate_limited=%s"
           % (total, counts["success"], counts["fail"], counts["rate_limited"]))


# ============================================================
# MD-1/MD-2 Markdown XSS 过滤（需测试账号密码，缺失 SKIP）
# ============================================================
def test_md_01_post_xss_filtered():
    """发帖内容含 <script>/javascript:/onerror → 详情页渲染后被白名单过滤；编辑页回填原始 MD"""
    if not USER_A_PWD:
        record("MD-1", SKIP, "未提供 TEST_USER_A_PWD")
        return
    c = Client()
    _login_ephemeral("userA", c)
    title = "MD-XSS-%d" % int(time.time())
    payload = ("正常文本\n\n<script>alert(1)</script>\n\n"
               "[点我](javascript:alert(1))\n\n<img src=x onerror=alert(2)>")
    _, _, _, token = c.get_with_csrf("/post/new")
    code, _, hdrs = c.post_with_csrf(
        "/post/new", {"title": title, "content": payload, "board_id": 1}, token)
    c.save_cookies(hdrs)
    pid = _find_post_id_by_title(c, title)
    if not (code == 302 and pid):
        record("MD-1", FAIL, "发帖失败 code=%s pid=%s" % (code, pid))
        _cleanup_upload(c, title, [])
        return
    code, page, _ = c.get("/post/%d" % pid)
    lower = page.lower()
    # 页面框架自带 <script>，故不整体断言；只验证 payload 未形成可执行标签：
    # ① 无 `<script>alert` 真实脚本标签；② 无 javascript: 链接；③ 无 onerror 事件属性
    no_script = ("<script>alert" not in lower and "javascript:" not in lower
                 and "onerror" not in lower and "alert(2)" not in lower)
    rendered = code == 200 and "正常文本" in page and "alert(1)" in page
    # 编辑页应回填原始 Markdown 文本（Jinja 转义后显示 &lt;script&gt;），保证可继续编辑
    code2, edit_page, _ = c.get("/post/%d/edit" % pid)
    raw_back = (code2 == 200 and "&lt;script&gt;alert(1)&lt;/script&gt;" in edit_page
                and "[点我](javascript:alert(1))" in edit_page)
    if no_script and rendered and raw_back:
        record("MD-1", PASS, "详情页无 script/javascript:/onerror；alert(1)文本可见；编辑页回填原始MD")
    else:
        record("MD-1", FAIL, "no_script=%s rendered=%s raw_back=%s" % (no_script, rendered, raw_back))
    _cleanup_upload(c, title, [])


def test_md_02_preview_filtered():
    """预览接口 /api/markdown/preview：危险 Markdown 返回过滤后 HTML，未登录被拒"""
    if not USER_A_PWD:
        record("MD-2", SKIP, "未提供 TEST_USER_A_PWD")
        return
    c = Client()
    _login_ephemeral("userA", c)
    _, _, _, token = c.get_with_csrf("/post/new")
    code, body, _ = c.post_with_csrf(
        "/api/markdown/preview",
        {"content": "<script>alert(1)</script> [x](javascript:alert(1)) <img src=x onerror=alert(2)>"},
        token)
    lower = (body or "").lower()
    ok = code == 200 and "<script" not in lower and "javascript:" not in lower and "onerror" not in lower
    # 未登录访问预览接口应被拒（带 token 走 login_required 302；无 token 先被 CSRF 400 —— 均不泄漏）
    c2 = Client()
    code2, _, hdrs2 = c2.get("/post/new")
    code3, _, _ = c2.post("/api/markdown/preview", {"content": "x"})
    anon_rejected = code2 == 302 and "/login" in (hdrs2.get("Location") or "") \
                    and code3 in (302, 400)
    if ok and anon_rejected:
        record("MD-2", PASS, "预览接口过滤 script/javascript:/onerror；未登录被拒(%s)" % code3)
    else:
        record("MD-2", FAIL, "filter_ok=%s anon_rejected=%s code3=%s" % (ok, anon_rejected, code3))


# ============================================================
# MENT 1/2/3 @提及（临时测试账号）
# ============================================================
def test_ment_01_nonexistent_skipped():
    """发帖 @不存在的人 → 发帖成功且不产生任何通知"""
    if not USER_A_PWD:
        record("MENT-1", SKIP, "临时测试账号未就绪，跳过")
        return
    ua = Client()
    _login_ephemeral("userA", ua)
    ts = int(time.time())
    title = "MENT1-%d" % ts
    ghost = "ghost_%d" % ts
    _, _, _, token = ua.get_with_csrf("/post/new")
    code, _, hdrs = ua.post_with_csrf(
        "/post/new", {"title": title, "content": "hello @%s 世界" % ghost, "board_id": 1}, token)
    ua.save_cookies(hdrs)
    pid = _db_find_post_id_by_title(title)
    cnt = 0
    if pid:
        cnt = _db_count_notifications_by_post(pid)
        _delete_post_by_id(ua, pid)
    record("MENT-1", PASS if (bool(pid) and cnt == 0) else FAIL,
           "@不存在用户：post_id=%s 该帖通知数=%s" % (pid, cnt))


def test_ment_02_existing_notified():
    """发帖 @存在的人 → 被提及用户收到 mention 站内通知"""
    if not USER_A_PWD:
        record("MENT-2", SKIP, "临时测试账号未就绪，跳过")
        return
    ua = Client()
    _login_ephemeral("userA", ua)
    target = _EPHEMERAL["userB"]["name"]
    uid_b = _db_get_user_id(target)
    title = "MENT2-%d" % int(time.time())
    _, _, _, token = ua.get_with_csrf("/post/new")
    code, _, hdrs = ua.post_with_csrf(
        "/post/new", {"title": title, "content": "@%s 请查看这份内容" % target, "board_id": 1}, token)
    ua.save_cookies(hdrs)
    pid = _db_find_post_id_by_title(title)
    got = False
    if pid and uid_b:
        got = _db_count_mention_by_post_user(pid, uid_b) >= 1
        _delete_post_by_id(ua, pid)
    record("MENT-2", PASS if got else FAIL,
           "@存在用户：target=%s uid=%s post_id=%s" % (target, uid_b, pid))


def test_ment_03_comment_mention():
    """评论中 @存在的人 → 被提及用户收到 mention 通知"""
    if not USER_A_PWD:
        record("MENT-3", SKIP, "临时测试账号未就绪，跳过")
        return
    ua, ub = Client(), Client()
    _login_ephemeral("userA", ua)
    _login_ephemeral("userB", ub)
    target = _EPHEMERAL["userA"]["name"]
    uid_a = _db_get_user_id(target)
    title = "MENT3-%d" % int(time.time())
    _, _, _, token = ua.get_with_csrf("/post/new")
    code, _, hdrs = ua.post_with_csrf(
        "/post/new", {"title": title, "content": "普通帖子", "board_id": 1}, token)
    ua.save_cookies(hdrs)
    pid = _db_find_post_id_by_title(title)
    cnt = 0
    if pid:
        _, _, _, token = ub.get_with_csrf("/post/%d" % pid)
        code, _, hdrs = ub.post_with_csrf(
            "/post/%d/comment" % pid, {"content": "@%s 回复一下" % target}, token)
        ub.save_cookies(hdrs)
        if uid_a:
            cnt = _db_count_mention_by_post_user(pid, uid_a)
        _delete_post_by_id(ua, pid)
    record("MENT-3", PASS if (bool(pid) and cnt >= 1) else FAIL,
           "评论@存在用户：target=%s uid=%s post_id=%s mention条数=%s" % (target, uid_a, pid, cnt))


def test_ment_04_duplicate_same_user_single():
    """同一帖子 @ 同一用户 3 次 → 只发 1 条通知（有序去重）"""
    if not USER_A_PWD:
        record("MENT-4", SKIP, "临时测试账号未就绪，跳过")
        return
    ua = Client()
    _login_ephemeral("userA", ua)
    target = _EPHEMERAL["userB"]["name"]
    uid_b = _db_get_user_id(target)
    title = "MENT4-%d" % int(time.time())
    _, _, _, token = ua.get_with_csrf("/post/new")
    code, _, hdrs = ua.post_with_csrf(
        "/post/new", {"title": title, "content": "@%s 你好 @%s 在吗 @%s 看看" % (target, target, target), "board_id": 1}, token)
    ua.save_cookies(hdrs)
    pid = _db_find_post_id_by_title(title)
    cnt = 0
    if pid and uid_b:
        cnt = _db_count_mention_by_post_user(pid, uid_b)
        _delete_post_by_id(ua, pid)
    record("MENT-4", PASS if (bool(pid) and cnt == 1) else FAIL,
           "同一用户提及3次：post_id=%s mention通知数=%s（应为1）" % (pid, cnt))


def test_ment_05_limit_10():
    """发帖含 20 个 @（12 个不同存在用户 + 重复）→ 最多只发 10 条通知（MENTION_LIMIT）"""
    if not USER_A_PWD:
        record("MENT-5", SKIP, "临时测试账号未就绪，跳过")
        return
    ua = Client()
    _login_ephemeral("userA", ua)
    ts = int(time.time())
    pwd = "SecTest%sA1" % ts
    names = ["secd%d%s" % (i, ts) for i in range(12)]
    ok_reg = True
    for n in names:
        c = Client()
        _, _, _, token = c.get_with_csrf("/register")
        code, _, hdrs = c.post_with_csrf(
            "/register", {"username": n, "password": pwd, "nickname": n}, token)
        c.save_cookies(hdrs)
        if code != 302:
            ok_reg = False
    title = "MENT5-%d" % ts
    # 12 个不同用户 + 8 次重复提及 = 20 个 @
    content = " ".join("@" + n for n in names) + " " + " ".join("@" + names[i % 12] for i in range(8)) + " 请查看"
    _, _, _, token = ua.get_with_csrf("/post/new")
    code, _, hdrs = ua.post_with_csrf(
        "/post/new", {"title": title, "content": content, "board_id": 1}, token)
    ua.save_cookies(hdrs)
    pid = _db_find_post_id_by_title(title)
    cnt = _db_count_notifications_by_post(pid) if pid else 0
    if pid:
        _delete_post_by_id(ua, pid)
    for n in names:
        _db_delete_user_by_name(n)
    record("MENT-5", PASS if (ok_reg and bool(pid) and cnt == 10) else FAIL,
           "20个@(12用户)：post_id=%s 通知数=%s（应截断为10）" % (pid, cnt))


def test_ment_06_edit_trigger_dedup():
    """发帖不 @，编辑加 @ → 被提及者收到 1 条；再次编辑仍含 @ → 不重复通知"""
    if not USER_A_PWD:
        record("MENT-6", SKIP, "临时测试账号未就绪，跳过")
        return
    ua = Client()
    _login_ephemeral("userA", ua)
    target = _EPHEMERAL["userB"]["name"]
    uid_b = _db_get_user_id(target)
    title = "MENT6-%d" % int(time.time())
    _, _, _, token = ua.get_with_csrf("/post/new")
    code, _, hdrs = ua.post_with_csrf(
        "/post/new", {"title": title, "content": "普通内容", "board_id": 1}, token)
    ua.save_cookies(hdrs)
    pid = _db_find_post_id_by_title(title)
    cnt1 = cnt2 = 0
    if pid:
        # 编辑1：内容中新增 @ 目标用户
        _, _, _, token = ua.get_with_csrf("/post/%d/edit" % pid)
        ua.post_with_csrf("/post/%d/edit" % pid,
                          {"title": title, "content": "普通内容 @%s 请看" % target, "board_id": 1}, token)
        cnt1 = _db_count_mention_by_post_user(pid, uid_b)
        # 编辑2：仍包含对同一用户的 @ → 已通知过，不重复
        _, _, _, token = ua.get_with_csrf("/post/%d/edit" % pid)
        ua.post_with_csrf("/post/%d/edit" % pid,
                          {"title": title, "content": "普通内容 @%s 再看" % target, "board_id": 1}, token)
        cnt2 = _db_count_mention_by_post_user(pid, uid_b)
        _delete_post_by_id(ua, pid)
    record("MENT-6", PASS if (bool(pid) and cnt1 == 1 and cnt2 == 1) else FAIL,
           "post_id=%s 首次编辑通知=%s 二次编辑通知=%s（均应为1）" % (pid, cnt1, cnt2))


def test_ment_07_notify_link_jump():
    """发帖@ → 通知列表渲染 <a href="/post/<pid>">；评论@ → 渲染 <a href="/post/<pid>#comment-<cid>"> 锚点"""
    if not USER_A_PWD:
        record("MENT-7", SKIP, "临时测试账号未就绪，跳过")
        return
    ua, ub = Client(), Client()
    _login_ephemeral("userA", ua)
    _login_ephemeral("userB", ub)
    name_b = _EPHEMERAL["userB"]["name"]
    name_a = _EPHEMERAL["userA"]["name"]
    title = "MENT7-%d" % int(time.time())
    _, _, _, token = ua.get_with_csrf("/post/new")
    ua.post_with_csrf(
        "/post/new", {"title": title, "content": "@%s 看看这篇" % name_b, "board_id": 1}, token)
    pid = _db_find_post_id_by_title(title)
    href_ok, anchor_ok, cid = False, False, None
    if pid:
        # 发帖 @：userB 通知列表渲染为 /post/<pid> 链接（get_with_csrf 返回 code,body,hdrs,token）
        _, body, _, _ = ub.get_with_csrf("/messages?tab=system")
        href_ok = ('href="/post/%d"' % pid) in body
        # 评论 @：userB 评论 @userA → 通知应携带 comment_id 并渲染 #comment- 锚点
        _, _, _, token = ub.get_with_csrf("/post/%d" % pid)
        ub.post_with_csrf(
            "/post/%d/comment" % pid, {"content": "@%s 我来了" % name_a}, token)
        import sys as _sys
        _sys.path.insert(0, os.path.join(BASE, "backend"))
        _sys.path.insert(0, os.path.join(BASE, "backend", "core"))
        _sys.path.insert(0, os.path.join(BASE, "backend", "config"))
        from db import get_cursor
        with get_cursor() as cur:
            cur.execute(
                "SELECT comment_id FROM comment WHERE post_id = ? AND user_id = ? ORDER BY comment_id DESC LIMIT 1",
                (pid, _db_get_user_id(name_b)))
            row = cur.fetchone()
            cid = row[0] if row else None
        if cid:
            _, body_a, _, _ = ua.get_with_csrf("/messages?tab=system")
            anchor_ok = ('href="/post/%d#comment-%d"' % (pid, cid)) in body_a
        _delete_post_by_id(ua, pid)
    record("MENT-7", PASS if (bool(pid) and href_ok and anchor_ok) else FAIL,
           "发帖@链接=%s 评论@锚点=%s pid=%s cid=%s" % (href_ok, anchor_ok, pid, cid))


# ============================================================
# BAN：用户封禁全链路（临时账号，CSRF 感知）
# ============================================================
def test_ban_01_unauthenticated_blocked():
    """BAN-1 未登录调用封禁接口必须被拒（302/403/400 CSRF 均视为被拒）"""
    if not (ADMIN_PWD and USER_A_PWD and USER_B_PWD):
        record("BAN-1", SKIP, "临时测试账号未就绪")
        return
    c = Client()
    code, body, hdrs = c.post("/admin/user/1/ban", {"reason": "x", "days": "7"})
    if code in (302, 400, 403) and "封禁成功" not in body:
        record("BAN-1", PASS, "未登录调封禁接口 -> %s（被拒）" % code)
    else:
        record("BAN-1", FAIL, "未登录调封禁接口 -> %s（未被拦截）" % code)


def test_ban_02_normal_user_blocked():
    """BAN-2 普通用户（userB）调用封禁接口必须被拒（越权拦截）"""
    if not (ADMIN_PWD and USER_A_PWD and USER_B_PWD):
        record("BAN-2", SKIP, "临时测试账号未就绪")
        return
    ub = Client()
    rb = _login_ephemeral("userB", ub)
    if rb[0] != 302:
        record("BAN-2", FAIL, "userB 登录失败")
        return
    # 取 CSRF 令牌（登录页会话级，与当前会话匹配）
    _, _, _, token = ub.get_with_csrf("/login")
    target_id = _db_get_user_id(_EPHEMERAL["userA"]["name"])
    code, _, hdrs = ub.post_with_csrf(
        "/admin/user/%s/ban" % target_id, {"reason": "越权测试", "days": "7"}, token)
    if code in (302, 403) and "admin" not in hdrs.get("Location", ""):
        record("BAN-2", PASS, "普通用户调封禁接口 -> %s（被拒）" % code)
    else:
        record("BAN-2", FAIL, "普通用户调封禁接口 -> %s（可能越权）" % code)


def test_ban_03_banned_login_rejected():
    """BAN-3 管理员封禁 userB（7天）后，userB 登录被拒且提示含封禁文案"""
    if not (ADMIN_PWD and USER_A_PWD and USER_B_PWD):
        record("BAN-3", SKIP, "临时测试账号未就绪")
        return
    admin = Client()
    if _login_ephemeral("admin", admin)[0] != 302:
        record("BAN-3", FAIL, "admin 登录失败")
        return
    _, _, _, token = admin.get_with_csrf("/admin")
    ub_id = _db_get_user_id(_EPHEMERAL["userB"]["name"])
    code, _, _ = admin.post_with_csrf(
        "/admin/user/%s/ban" % ub_id, {"reason": "测试封禁", "days": "7"}, token)
    if code != 302:
        record("BAN-3", FAIL, "管理员封禁接口 -> %s（封禁失败）" % code)
        return
    # userB 重新登录（新会话）应被拒，页面提示包含封禁文案
    ub2 = Client()
    _, _, _, token = ub2.get_with_csrf("/login")
    code, body, hdrs = ub2.post_with_csrf(
        "/login", {"username": _EPHEMERAL["userB"]["name"],
                   "password": _EPHEMERAL["userB"]["pwd"]}, token)
    ub2.save_cookies(hdrs)
    if code != 302 and "账号已被封禁" in body:
        record("BAN-3", PASS, "被封禁用户登录被拒，提示含封禁文案")
    else:
        record("BAN-3", FAIL, "被封禁用户登录 -> code=%s（未被拒绝）" % code)


def test_ban_04_banned_session_invalidated():
    """BAN-4 已登录 userA 被封禁后，下次请求 session 失效并重定向登录（发帖被拒）"""
    if not (ADMIN_PWD and USER_A_PWD and USER_B_PWD):
        record("BAN-4", SKIP, "临时测试账号未就绪")
        return
    ua = Client()
    if _login_ephemeral("userA", ua)[0] != 302:
        record("BAN-4", FAIL, "userA 登录失败")
        return
    ua_id = _db_get_user_id(_EPHEMERAL["userA"]["name"])
    admin = Client()
    if _login_ephemeral("admin", admin)[0] != 302:
        record("BAN-4", FAIL, "admin 登录失败")
        return
    _, _, _, token = admin.get_with_csrf("/admin")
    code, _, _ = admin.post_with_csrf(
        "/admin/user/%s/ban" % ua_id, {"reason": "测试封禁A", "days": "forever"}, token)
    if code != 302:
        record("BAN-4", FAIL, "封禁 userA 失败 code=%s" % code)
        return
    # userA 旧会话访问发帖页：before_request 全局封禁检查应清会话并重定向 /login（会话失效）
    code, _, hdrs = ua.get("/post/new")
    if not (code == 302 and "login" in hdrs.get("Location", "")):
        record("BAN-4", FAIL, "被封禁用户旧会话访问发帖页 -> %s（session 未失效）" % code)
        return
    # 旧会话 POST 发帖：会话已失效 → 被拒（302 或 400 CSRF 均不可发布成功）
    _, _, _, token = ua.get_with_csrf("/login")
    code, body, hdrs = ua.post_with_csrf(
        "/post/new", {"title": "BAN-POST-%d" % int(time.time()),
                      "content": "不应发布", "board_id": 1}, token)
    if code in (302, 400) and "发布成功" not in body:
        record("BAN-4", PASS, "被封禁用户旧会话发帖被拒（session 已失效 -> %s）" % code)
    else:
        record("BAN-4", FAIL, "被封禁用户旧会话发帖 -> %s（未被拦截）" % code)


def test_ban_05_unban_restores():
    """BAN-5 解封后用户恢复可登录、可发帖；并验证到期惰性解封"""
    if not (ADMIN_PWD and USER_A_PWD and USER_B_PWD):
        record("BAN-5", SKIP, "临时测试账号未就绪")
        return
    ua_id = _db_get_user_id(_EPHEMERAL["userA"]["name"])
    admin = Client()
    if _login_ephemeral("admin", admin)[0] != 302:
        record("BAN-5", FAIL, "admin 登录失败")
        return
    # 1) 管理员解封 userA（BAN-4 已封禁）
    _, _, _, token = admin.get_with_csrf("/admin")
    code, _, _ = admin.post_with_csrf(
        "/admin/user/%s/unban" % ua_id, {}, token)
    # 2) userA 重新登录成功
    ua = Client()
    _, _, _, token = ua.get_with_csrf("/login")
    code, body, hdrs = ua.post_with_csrf(
        "/login", {"username": _EPHEMERAL["userA"]["name"],
                   "password": _EPHEMERAL["userA"]["pwd"]}, token)
    ua.save_cookies(hdrs)
    if code != 302:
        record("BAN-5", FAIL, "解封后 userA 登录 -> %s（未能恢复）" % code)
        return
    # 3) 发帖成功
    title = "BAN-OK-%d" % int(time.time())
    _, _, _, token = ua.get_with_csrf("/post/new")
    code, _, hdrs = ua.post_with_csrf(
        "/post/new", {"title": title, "content": "解封后可发帖", "board_id": 1}, token)
    pid = _db_find_post_id_by_title(title)
    if code == 302 and pid:
        record("BAN-5", PASS, "解封后 userA 登录成功且发帖成功 (#%s)" % pid)
        _delete_post_by_id(ua, pid)
    else:
        record("BAN-5", FAIL, "解封后发帖 -> code=%s pid=%s" % (code, pid))
        return
    # 4) 到期惰性解封：封 1 天后将 ban_until 改为过去，下次请求自动恢复
    _, _, _, token = admin.get_with_csrf("/admin")
    admin.post_with_csrf(
        "/admin/user/%s/ban" % ua_id, {"reason": "惰性解封测试", "days": "1"}, token)
    import sys as _sys
    _sys.path.insert(0, os.path.join(BASE, "backend"))
    _sys.path.insert(0, os.path.join(BASE, "backend", "core"))
    _sys.path.insert(0, os.path.join(BASE, "backend", "config"))
    from db import get_cursor
    with get_cursor() as cur:
        cur.execute(
            "UPDATE `user` SET ban_until = DATE_SUB(NOW(), INTERVAL 1 DAY) "
            "WHERE user_id = ?", (ua_id,))
    ua2 = Client()
    _, _, _, token2 = ua2.get_with_csrf("/login")
    code, body, hdrs = ua2.post_with_csrf(
        "/login", {"username": _EPHEMERAL["userA"]["name"],
                   "password": _EPHEMERAL["userA"]["pwd"]}, token2)
    if code == 302:
        record("BAN-5b", PASS, "到期惰性解封：ban_until 过期后登录成功（自动恢复）")
    else:
        record("BAN-5b", FAIL, "惰性解封未生效 -> %s" % code)


def test_ban_06_audit_logged():
    """BAN-6 封禁/解封操作落库 audit_log（action=ban_user/unban_user）"""
    if not (ADMIN_PWD and USER_A_PWD and USER_B_PWD):
        record("BAN-6", SKIP, "临时测试账号未就绪")
        return
    import sys as _sys
    _sys.path.insert(0, os.path.join(BASE, "backend"))
    _sys.path.insert(0, os.path.join(BASE, "backend", "core"))
    _sys.path.insert(0, os.path.join(BASE, "backend", "config"))
    from db import get_cursor
    ua_id = _db_get_user_id(_EPHEMERAL["userA"]["name"])
    ub_id = _db_get_user_id(_EPHEMERAL["userB"]["name"])
    with get_cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM audit_log WHERE action = 'ban_user' AND target_id IN (?, ?)",
            (ua_id, ub_id))
        ban_cnt = cur.fetchone()[0]
        cur.execute(
            "SELECT COUNT(*) FROM audit_log WHERE action = 'unban_user' AND target_id = ?",
            (ua_id,))
        unban_cnt = cur.fetchone()[0]
        cur.execute(
            "SELECT COUNT(*) FROM audit_log WHERE action = 'login_blocked' AND target_id = ?",
            (ub_id,))
        blocked_cnt = cur.fetchone()[0]
    if ban_cnt >= 1 and unban_cnt >= 1:
        record("BAN-6", PASS, "审计落库 ban_user=%s unban_user=%s login_blocked=%s"
               % (ban_cnt, unban_cnt, blocked_cnt))
    else:
        record("BAN-6", FAIL, "审计缺失 ban_user=%s unban_user=%s" % (ban_cnt, unban_cnt))


# ============================================================
# SW：敏感词过滤（词库：backend/config/sensitive_words.txt）
# ============================================================
def _db_count_posts_by_title(title):
    import sys as _sys
    _sys.path.insert(0, os.path.join(BASE, "backend"))
    _sys.path.insert(0, os.path.join(BASE, "backend", "core"))
    _sys.path.insert(0, os.path.join(BASE, "backend", "config"))
    from db import get_cursor
    with get_cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM post WHERE title = ?", (title,))
        return cur.fetchone()[0]


def _db_count_comments_by_content(content):
    import sys as _sys
    _sys.path.insert(0, os.path.join(BASE, "backend"))
    _sys.path.insert(0, os.path.join(BASE, "backend", "core"))
    _sys.path.insert(0, os.path.join(BASE, "backend", "config"))
    from db import get_cursor
    with get_cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM comment WHERE content = ?", (content,))
        return cur.fetchone()[0]


def test_sw_01_post_blocked():
    """SW-1 发帖含敏感词被拒且帖子未入库"""
    if not (ADMIN_PWD and USER_A_PWD and USER_B_PWD):
        record("SW-1", SKIP, "临时测试账号未就绪")
        return
    ua = Client()
    if _login_ephemeral("userA", ua)[0] != 302:
        record("SW-1", FAIL, "userA 登录失败")
        return
    title = "SW-TITLE-%d" % int(time.time())
    _, _, _, token = ua.get_with_csrf("/post/new")
    code, body, hdrs = ua.post_with_csrf(
        "/post/new", {"title": title, "content": "这里包含敏感词内容", "board_id": 1}, token)
    cnt = _db_count_posts_by_title(title)
    if cnt == 0 and code in (200, 302):
        record("SW-1", PASS, "发帖含敏感词被拒（code=%s，帖子未入库）" % code)
    else:
        record("SW-1", FAIL, "敏感词帖未拦截：code=%s db_cnt=%s" % (code, cnt))


def test_sw_02_comment_blocked():
    """SW-2 评论含敏感词被拒且评论未入库"""
    if not (ADMIN_PWD and USER_A_PWD and USER_B_PWD):
        record("SW-2", SKIP, "临时测试账号未就绪")
        return
    ua = Client()
    if _login_ephemeral("userA", ua)[0] != 302:
        record("SW-2", FAIL, "userA 登录失败")
        return
    # 先发一篇正常帖作为评论目标
    title = "SW-CMT-%d" % int(time.time())
    _, _, _, token = ua.get_with_csrf("/post/new")
    ua.post_with_csrf(
        "/post/new", {"title": title, "content": "正常评论目标帖", "board_id": 1}, token)
    pid = _db_find_post_id_by_title(title)
    if not pid:
        record("SW-2", FAIL, "评论目标帖创建失败")
        return
    bad_content = "这条评论含敏感词"
    _, _, _, token = ua.get_with_csrf("/post/%d" % pid)
    code, body, hdrs = ua.post_with_csrf(
        "/post/%d/comment" % pid, {"content": bad_content}, token)
    cnt = _db_count_comments_by_content(bad_content)
    _delete_post_by_id(ua, pid)
    if cnt == 0 and (code in (200, 302, 400)):
        record("SW-2", PASS, "评论含敏感词被拒（code=%s，评论未入库）" % code)
    else:
        record("SW-2", FAIL, "敏感词评论未拦截：code=%s db_cnt=%s" % (code, cnt))


def test_sw_03_profile_blocked():
    """SW-3 修改昵称/签名含敏感词被拒（原资料不变）"""
    if not (ADMIN_PWD and USER_A_PWD and USER_B_PWD):
        record("SW-3", SKIP, "临时测试账号未就绪")
        return
    ua = Client()
    if _login_ephemeral("userA", ua)[0] != 302:
        record("SW-3", FAIL, "userA 登录失败")
        return
    # 记录原昵称
    import sys as _sys
    _sys.path.insert(0, os.path.join(BASE, "backend"))
    _sys.path.insert(0, os.path.join(BASE, "backend", "core"))
    _sys.path.insert(0, os.path.join(BASE, "backend", "config"))
    from db import get_cursor
    uid = _db_get_user_id(_EPHEMERAL["userA"]["name"])
    with get_cursor() as cur:
        cur.execute("SELECT nickname, signature FROM `user` WHERE user_id = ?", (uid,))
        old_nick, old_sig = cur.fetchone()
    _, _, _, token = ua.get_with_csrf("/profile/edit")
    code, _, _ = ua.post_with_csrf(
        "/profile/edit", {"nickname": "敏感词昵称", "signature": "正常签名"}, token)
    with get_cursor() as cur:
        cur.execute("SELECT nickname, signature FROM `user` WHERE user_id = ?", (uid,))
        now_nick, now_sig = cur.fetchone()
    if now_nick == old_nick and now_sig == old_sig and code in (200, 302):
        record("SW-3", PASS, "昵称含敏感词被拒（资料未变更，code=%s）" % code)
    else:
        record("SW-3", FAIL, "敏感词昵称未拦截：code=%s nick=%s->%s" % (code, old_nick, now_nick))


def test_sw_04_variant_blocked():
    """SW-4 变体识别：符号分隔（敏 * 感 * 词）与全角（ｆｕｃｋ）均被拒绝"""
    if not (ADMIN_PWD and USER_A_PWD and USER_B_PWD):
        record("SW-4", SKIP, "临时测试账号未就绪")
        return
    ua = Client()
    if _login_ephemeral("userA", ua)[0] != 302:
        record("SW-4", FAIL, "userA 登录失败")
        return
    ok = True
    # 变体1：符号分隔
    t1 = "SW-VAR1-%d" % int(time.time())
    _, _, _, token = ua.get_with_csrf("/post/new")
    code, _, _ = ua.post_with_csrf(
        "/post/new", {"title": t1, "content": "敏 * 感 * 词测试", "board_id": 1}, token)
    if _db_count_posts_by_title(t1) != 0:
        ok = False
        print("    [SW-4] 变体1（符号分隔）未被拦截")
    # 变体2：全角字母
    t2 = "SW-VAR2-%d" % int(time.time())
    _, _, _, token = ua.get_with_csrf("/post/new")
    code, _, _ = ua.post_with_csrf(
        "/post/new", {"title": t2, "content": "内容包含ｆｕｃｋ全角", "board_id": 1}, token)
    if _db_count_posts_by_title(t2) != 0:
        ok = False
        print("    [SW-4] 变体2（全角字母）未被拦截")
    if ok:
        record("SW-4", PASS, "变体识别生效：符号分隔与全角均被拒绝")
    else:
        record("SW-4", FAIL, "变体识别存在遗漏")


# ============================================================
# RPT：举报系统 / 审核队列 / 账号申诉
# ============================================================
def _db_count_tickets(reporter_id, target_type, target_id, status=None):
    import sys as _sys
    _sys.path.insert(0, os.path.join(BASE, "backend"))
    _sys.path.insert(0, os.path.join(BASE, "backend", "core"))
    _sys.path.insert(0, os.path.join(BASE, "backend", "config"))
    from db import get_cursor
    with get_cursor() as cur:
        if status:
            cur.execute(
                "SELECT COUNT(*) FROM report_tickets WHERE reporter_id = ? AND target_type = ? "
                "AND target_id = ? AND status = ?",
                (reporter_id, target_type, target_id, status))
        else:
            cur.execute(
                "SELECT COUNT(*) FROM report_tickets WHERE reporter_id = ? AND target_type = ? "
                "AND target_id = ?",
                (reporter_id, target_type, target_id))
        return cur.fetchone()[0]


def _db_find_ticket_id(reporter_id, target_type, target_id):
    import sys as _sys
    _sys.path.insert(0, os.path.join(BASE, "backend"))
    _sys.path.insert(0, os.path.join(BASE, "backend", "core"))
    _sys.path.insert(0, os.path.join(BASE, "backend", "config"))
    from db import get_cursor
    with get_cursor() as cur:
        cur.execute(
            "SELECT id FROM report_tickets WHERE reporter_id = ? AND target_type = ? "
            "AND target_id = ? AND status = 'pending' ORDER BY id DESC LIMIT 1",
            (reporter_id, target_type, target_id))
        row = cur.fetchone()
        return row[0] if row else None


def _db_get_ticket_status(ticket_id):
    import sys as _sys
    _sys.path.insert(0, os.path.join(BASE, "backend"))
    _sys.path.insert(0, os.path.join(BASE, "backend", "core"))
    _sys.path.insert(0, os.path.join(BASE, "backend", "config"))
    from db import get_cursor
    with get_cursor() as cur:
        cur.execute("SELECT status FROM report_tickets WHERE id = ?", (ticket_id,))
        row = cur.fetchone()
        return row[0] if row else None


def _db_unread_count(user_id):
    """查询用户未读通知数（DB 直接断言，与 get_unread_count 同口径）"""
    import sys as _sys
    _sys.path.insert(0, os.path.join(BASE, "backend"))
    _sys.path.insert(0, os.path.join(BASE, "backend", "core"))
    _sys.path.insert(0, os.path.join(BASE, "backend", "config"))
    from db import get_cursor
    with get_cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM notification WHERE user_id = ? AND is_read = 0", (user_id,))
        return cur.fetchone()[0]


def test_rpt_01_unauth_report_rejected():
    """RPT-1 未登录用户提交举报被拒（302 到登录页）"""
    c = Client()
    _, _, _, token = c.get_with_csrf("/login")
    code, _, hdrs = c.post_with_csrf(
        "/report", {"target_type": "post", "target_id": 1,
                    "category": "violation", "description": "未登录举报"}, token)
    loc = hdrs.get("Location", "")
    if code == 302 and "/login" in loc:
        record("RPT-1", PASS, "未登录提交举报被重定向到登录页（%s）" % loc)
    else:
        record("RPT-1", FAIL, "未登录举报未被拦截：code=%s loc=%s" % (code, loc))


def test_rpt_02_report_submitted():
    """RPT-2 用户成功提交举报 → 落库 status=pending"""
    if not (ADMIN_PWD and USER_A_PWD and USER_B_PWD):
        record("RPT-2", SKIP, "临时测试账号未就绪")
        return
    ua = Client()
    if _login_ephemeral("userA", ua)[0] != 302:
        record("RPT-2", FAIL, "userA 登录失败")
        return
    title = "RPT-TARGET-%d" % int(time.time())
    _, _, _, token = ua.get_with_csrf("/post/new")
    ua.post_with_csrf("/post/new", {"title": title, "content": "举报目标帖", "board_id": 1}, token)
    pid = _db_find_post_id_by_title(title)
    if not pid:
        record("RPT-2", FAIL, "目标帖创建失败")
        return
    uid = _db_get_user_id(_EPHEMERAL["userA"]["name"])
    _, _, _, token = ua.get_with_csrf("/post/%d" % pid)
    code, body, hdrs = ua.post_with_csrf(
        "/report", {"target_type": "post", "target_id": pid,
                    "category": "violation", "description": "测试举报"}, token)
    cnt = _db_count_tickets(uid, "post", pid, "pending")
    _delete_post_by_id(ua, pid)
    if cnt == 1 and code in (200, 302):
        record("RPT-2", PASS, "举报落库 status=pending（code=%s，tickets=%s）" % (code, cnt))
    else:
        record("RPT-2", FAIL, "举报未落库：code=%s tickets=%s" % (code, cnt))


def test_rpt_03_duplicate_report_rejected():
    """RPT-3 同一用户重复举报同一帖子 → 被拒且仅保留一条"""
    if not (ADMIN_PWD and USER_A_PWD and USER_B_PWD):
        record("RPT-3", SKIP, "临时测试账号未就绪")
        return
    ua = Client()
    if _login_ephemeral("userA", ua)[0] != 302:
        record("RPT-3", FAIL, "userA 登录失败")
        return
    title = "RPT-DUP-%d" % int(time.time())
    _, _, _, token = ua.get_with_csrf("/post/new")
    ua.post_with_csrf("/post/new", {"title": title, "content": "重复举报目标", "board_id": 1}, token)
    pid = _db_find_post_id_by_title(title)
    if not pid:
        record("RPT-3", FAIL, "目标帖创建失败")
        return
    uid = _db_get_user_id(_EPHEMERAL["userA"]["name"])
    _, _, _, token = ua.get_with_csrf("/post/%d" % pid)
    ua.post_with_csrf("/report", {"target_type": "post", "target_id": pid,
                                  "category": "other", "description": "第一次"}, token)
    _, _, _, token = ua.get_with_csrf("/post/%d" % pid)
    code2, _, _ = ua.post_with_csrf("/report", {"target_type": "post", "target_id": pid,
                                                "category": "other", "description": "第二次"}, token)
    cnt = _db_count_tickets(uid, "post", pid, "pending")
    _delete_post_by_id(ua, pid)
    if cnt == 1 and code2 in (200, 302):
        record("RPT-3", PASS, "重复举报被拒，仅保留一条 pending（code=%s，tickets=%s）" % (code2, cnt))
    else:
        record("RPT-3", FAIL, "重复举报未拦截：code=%s tickets=%s" % (code2, cnt))


def test_rpt_04_user_cannot_view_queue():
    """RPT-4 普通用户访问管理端审核队列 → 被拒（302）"""
    if not (ADMIN_PWD and USER_A_PWD and USER_B_PWD):
        record("RPT-4", SKIP, "临时测试账号未就绪")
        return
    ua = Client()
    if _login_ephemeral("userA", ua)[0] != 302:
        record("RPT-4", FAIL, "userA 登录失败")
        return
    code, body, hdrs, _ = ua.get_with_csrf("/admin?tab=reports")
    loc = hdrs.get("Location", "")
    if code == 302 and ("/login" in loc or "/admin" not in loc):
        record("RPT-4", PASS, "普通用户访问审核队列被拒（code=%s loc=%s）" % (code, loc))
    else:
        record("RPT-4", FAIL, "普通用户可访问审核队列：code=%s" % code)


def test_rpt_05_admin_handle_report():
    """RPT-5 管理员处理举报：approve → 帖子被删除；reject → 状态更新"""
    if not (ADMIN_PWD and USER_A_PWD and USER_B_PWD):
        record("RPT-5", SKIP, "临时测试账号未就绪")
        return
    ua = Client()
    adm = Client()
    if _login_ephemeral("userA", ua)[0] != 302 or _login_ephemeral("admin", adm)[0] != 302:
        record("RPT-5", FAIL, "登录失败")
        return
    # 两个目标帖，分别走 approve 和 reject
    t_a = "RPT-APV-%d" % int(time.time())
    t_b = "RPT-RJT-%d" % int(time.time())
    _, _, _, token = ua.get_with_csrf("/post/new")
    ua.post_with_csrf("/post/new", {"title": t_a, "content": "待删除帖", "board_id": 1}, token)
    ua.post_with_csrf("/post/new", {"title": t_b, "content": "待驳回帖", "board_id": 1}, token)
    pid_a = _db_find_post_id_by_title(t_a)
    pid_b = _db_find_post_id_by_title(t_b)
    if not pid_a or not pid_b:
        record("RPT-5", FAIL, "目标帖创建失败")
        return
    uid = _db_get_user_id(_EPHEMERAL["userA"]["name"])
    _, _, _, token = ua.get_with_csrf("/post/%d" % pid_a)
    ua.post_with_csrf("/report", {"target_type": "post", "target_id": pid_a,
                                  "category": "ad", "description": "approve 用例"}, token)
    _, _, _, token = ua.get_with_csrf("/post/%d" % pid_b)
    ua.post_with_csrf("/report", {"target_type": "post", "target_id": pid_b,
                                  "category": "other", "description": "reject 用例"}, token)
    tid_a = _db_find_ticket_id(uid, "post", pid_a)
    tid_b = _db_find_ticket_id(uid, "post", pid_b)
    if not tid_a or not tid_b:
        record("RPT-5", FAIL, "工单创建失败")
        return
    # approve：删除帖子
    _, _, _, token = adm.get_with_csrf("/admin?tab=reports")
    code, _, _ = adm.post_with_csrf(
        "/admin/report/%d/handle" % tid_a, {"action": "approve"}, token)
    post_exists = _db_find_post_id_by_title(t_a) is not None
    status_a = _db_get_ticket_status(tid_a)
    # reject：仅驳回
    _, _, _, token = adm.get_with_csrf("/admin?tab=reports")
    code2, _, _ = adm.post_with_csrf(
        "/admin/report/%d/handle" % tid_b, {"action": "reject"}, token)
    status_b = _db_get_ticket_status(tid_b)
    if not post_exists and status_a == "resolved" and status_b == "rejected":
        record("RPT-5", PASS, "approve 删帖+resolved，reject 置 rejected（code=%s/%s）" % (code, code2))
    else:
        record("RPT-5", FAIL, "处理异常：post_exists=%s statusA=%s statusB=%s" % (
            post_exists, status_a, status_b))


def _reset_rate_limiters():
    """测试钩子：重置应用内存限速器（需应用以 FLASK_TEST=1 启动）。
    防止连续多轮测试同 IP 限速窗口残留导致误伤（历史偶发 429 根因）。"""
    c = Client()
    _, _, _, token = c.get_with_csrf("/login")
    code, _, _ = c.post_with_csrf("/_test/reset-rate-limit", {}, token)
    return code == 200


def test_rpt_06_appeal_flow():
    """RPT-6 封禁申诉闭环：管理员封禁 → 未登录申诉 → 管理员通过 → 解封可登录"""
    if not (ADMIN_PWD and USER_A_PWD and USER_B_PWD):
        record("RPT-6", SKIP, "临时测试账号未就绪")
        return
    if not _reset_rate_limiters():
        record("RPT-6", SKIP, "限速器重置钩子不可用（请以 FLASK_TEST=1 启动应用）")
        return
    ub = Client()
    adm = Client()
    # 注意：BAN-3 已封禁 userB 且未解封（BAN-5 只解封 userA），
    # 因此这里不需要 userB 预先登录——流程本身就是"被封禁 → 申诉 → 解封 → 重新登录"
    if _login_ephemeral("admin", adm)[0] != 302:
        record("RPT-6", FAIL, "admin 登录失败")
        return
    # 确保 userB 处于封禁状态（若已到期自动解封则重新封禁）
    ub_uid = _db_get_user_id(_EPHEMERAL["userB"]["name"])
    import sys as _sys
    _sys.path.insert(0, os.path.join(BASE, "backend", "services"))
    from ban_service import is_user_banned as _is_banned
    banned, _, _ = _is_banned(ub_uid)
    if not banned:
        _, _, _, token = adm.get_with_csrf("/admin?tab=users")
        adm.post_with_csrf(
            "/admin/user/%d/ban" % ub_uid, {"reason": "RPT测试封禁", "days": "1"}, token)
    # 未登录提交申诉（实名验证：用户名）
    guest = Client()
    _, _, _, token = guest.get_with_csrf("/appeal")
    code2, _, hdrs2 = guest.post_with_csrf(
        "/appeal", {"username": _EPHEMERAL["userB"]["name"], "reason": "RPT测试申诉"}, token)
    cnt = _db_count_tickets(ub_uid, "appeal", ub_uid, "pending")
    tid = _db_find_ticket_id(ub_uid, "appeal", ub_uid)
    if code2 not in (200, 302) or cnt != 1 or not tid:
        record("RPT-6", FAIL, "申诉未落库：code=%s cnt=%s" % (code2, cnt))
        return
    # 管理员通过申诉 → 解封
    _, _, _, token = adm.get_with_csrf("/admin?tab=reports")
    code3, _, _ = adm.post_with_csrf(
        "/admin/appeal/%d/handle" % tid, {"action": "approve"}, token)
    status = _db_get_ticket_status(tid)
    # userB 重新登录
    ub2 = Client()
    _, _, _, token = ub2.get_with_csrf("/login")
    code4, body, hdrs4 = ub2.post_with_csrf(
        "/login", {"username": _EPHEMERAL["userB"]["name"], "password": _EPHEMERAL["userB"]["pwd"]}, token)
    ub2.save_cookies(hdrs4)
    # 通知触达：解封通知已落库，登录后通知中心可见
    note_ok = False
    if code4 == 302 and "/login" not in hdrs4.get("Location", ""):
        ncode, nbody, _ = ub2.get("/messages?tab=system")
        note_ok = "账号已解封" in nbody
    if status == "resolved" and code4 == 302 and "/login" not in hdrs4.get("Location", "") and note_ok:
        record("RPT-6", PASS, "申诉通过 → 工单 resolved，userB 恢复可登录，解封通知中心可见")
    else:
        record("RPT-6", FAIL, "申诉闭环异常：status=%s login_code=%s note_ok=%s" % (status, code4, note_ok))


def test_rpt_07_appeal_anti_abuse():
    """RPT-7 申诉防刷：统一模糊提示（防账号状态探测）+ IP 严格限速（每小时 3 次）。
    先重置限速器（隔离 RPT-6 的 1 次提交），再连续 4 次 → 第 4 次必 429。"""
    if not _reset_rate_limiters():
        record("RPT-7", SKIP, "限速器重置钩子不可用（请以 FLASK_TEST=1 启动应用）")
        return
    guest = Client()
    # 1) 统一模糊提示：账号不存在 → 页面提示统一文案，不暴露"用户不存在"
    _, _, _, token = guest.get_with_csrf("/appeal")
    code1, body1, _ = guest.post_with_csrf(
        "/appeal", {"username": "no_such_user_probe_xyz", "reason": "探测"}, token)
    blur_ok = (code1 == 200 and "申诉请求已收到" in body1)
    # 2) IP 限速：reset 后（统一提示已占 1 次）连续 4 次提交，
    #    尝试 2、3 放行，尝试 4、5 超限 → 末两次必 429
    codes = []
    for i in range(4):
        _, _, _, token = guest.get_with_csrf("/appeal")
        c, _, _ = guest.post_with_csrf(
            "/appeal", {"username": "no_such_user_probe_%d" % i, "reason": "x"}, token)
        codes.append(c)
    if blur_ok and codes[-1] == 429 and codes.count(429) == 2:
        record("RPT-7", PASS, "统一模糊提示生效；IP 限速精确触发（3次/小时，末两次=429）")
    else:
        record("RPT-7", FAIL, "防刷异常：blur=%s codes=%s" % (blur_ok, codes))


def test_rpt_08_queue_pagination():
    """RPT-8 审核队列分页：21 条 pending 工单 → 第 1 页 20 条、第 2 页 1 条；
    LIMIT/OFFSET 参数化 + 每页上限 20，防恶意大页拖库。"""
    if not ADMIN_PWD:
        record("RPT-8", SKIP, "临时测试账号未就绪")
        return
    adm = Client()
    if _login_ephemeral("admin", adm)[0] != 302:
        record("RPT-8", FAIL, "admin 登录失败")
        return
    # DB 直插 21 条 pending 工单（绕过举报限速，仅测试分页）
    uid = _db_get_user_id(_EPHEMERAL["userA"]["name"])
    import sys as _sys
    _sys.path.insert(0, os.path.join(BASE, "backend"))
    _sys.path.insert(0, os.path.join(BASE, "backend", "core"))
    _sys.path.insert(0, os.path.join(BASE, "backend", "config"))
    from db import get_cursor
    try:
        with get_cursor() as cur:
            for i in range(21):
                # created_at 显式递增（i=0 最新），避免同秒插入导致 ORDER BY 不稳定（偶发失败根因）
                cur.execute(
                    "INSERT INTO report_tickets (reporter_id, target_type, target_id, "
                    "category, description, status, created_at) VALUES (?, 'post', 1, 'other', ?, "
                    "'pending', DATE_ADD(NOW(), INTERVAL ? MINUTE))",
                    (uid, "分页测试%d" % i, -i))
        # 第 1 页：20 条（含"分页测试0"第1条，不含"分页测试20"第21条）+ 分页控件
        code1, body1, _ = adm.get("/admin?tab=reports&page=1")
        page1_has_first = "分页测试0" in body1
        page1_no_last = "分页测试20" not in body1
        has_pager1 = ("第 1 / 2" in body1)
        # 第 2 页：1 条（仅"分页测试20"）
        code2, body2, _ = adm.get("/admin?tab=reports&page=2")
        page2_has_last = "分页测试20" in body2
        page2_no_first = "分页测试0" not in body2
        # 越界页自动回退到最后一页（不报错）
        code3, _, _ = adm.get("/admin?tab=reports&page=999")
        ok = (code1 == 200 and page1_has_first and page1_no_last
              and has_pager1 and code2 == 200 and page2_has_last and page2_no_first
              and code3 == 200)
        if ok:
            record("RPT-8", PASS, "分页生效：第1页含第1条不含第21条；第2页仅第21条；越界页200")
        else:
            record("RPT-8", FAIL, "分页异常：p1_first=%s p1_no_last=%s pager=%s p2_last=%s p2_no_first=%s code3=%s"
                   % (page1_has_first, page1_no_last, has_pager1, page2_has_last, page2_no_first, code3))
    finally:
        with get_cursor() as cur:
            cur.execute("DELETE FROM report_tickets WHERE description LIKE '分页测试%'")


def test_rpt_09_unban_notification_badge():
    """RPT-9 端到端：封禁 → 申诉 → 管理员解封 → 重新登录，
    断言 appeal 通知落库、未读数>0，且首页导航栏通知红点渲染正确。"""
    if not (ADMIN_PWD and USER_A_PWD and USER_B_PWD):
        record("RPT-9", SKIP, "临时测试账号未就绪")
        return
    if not _reset_rate_limiters():
        record("RPT-9", SKIP, "限速器重置钩子不可用（请以 FLASK_TEST=1 启动应用）")
        return
    adm = Client()
    if _login_ephemeral("admin", adm)[0] != 302:
        record("RPT-9", FAIL, "admin 登录失败")
        return
    # userA 作为被封禁对象（BAN-5 已解封，状态正常）
    ua = _db_get_user_id(_EPHEMERAL["userA"]["name"])
    import sys as _sys
    _sys.path.insert(0, os.path.join(BASE, "backend", "services"))
    from ban_service import is_user_banned as _is_banned
    banned, _, _ = _is_banned(ua)
    if not banned:
        _, _, _, token = adm.get_with_csrf("/admin?tab=users")
        adm.post_with_csrf("/admin/user/%d/ban" % ua, {"reason": "RPT9测试封禁", "days": "1"}, token)
    # userA 登录被拒
    ua1 = Client()
    _, _, _, token = ua1.get_with_csrf("/login")
    c1, _, _ = ua1.post_with_csrf(
        "/login", {"username": _EPHEMERAL["userA"]["name"], "password": _EPHEMERAL["userA"]["pwd"]}, token)
    banned_login = (c1 == 200)  # 登录页渲染 flash（被封禁提示），不产生 session
    # 未登录提交申诉
    _, _, _, token = ua1.get_with_csrf("/appeal")
    c2, body2, _ = ua1.post_with_csrf(
        "/appeal", {"username": _EPHEMERAL["userA"]["name"], "reason": "RPT9端到端红点验证"}, token)
    tid = _db_find_ticket_id(ua, "appeal", ua)
    # 管理员通过申诉 → 解封 + appeal 通知
    _, _, _, token = adm.get_with_csrf("/admin?tab=reports")
    adm.post_with_csrf("/admin/appeal/%d/handle" % tid, {"action": "approve"}, token)
    # DB 断言：appeal 通知落库且未读
    unread = _db_unread_count(ua)
    has_appeal = False
    import sys as _sys2
    _sys2.path.insert(0, os.path.join(BASE, "backend"))
    _sys2.path.insert(0, os.path.join(BASE, "backend", "core"))
    _sys2.path.insert(0, os.path.join(BASE, "backend", "config"))
    from db import get_cursor as _gc
    with _gc() as cur:
        cur.execute("SELECT COUNT(*) FROM notification WHERE user_id=? AND type='appeal' AND is_read=0", (ua,))
        has_appeal = cur.fetchone()[0] > 0
    # userA 重新登录 → 访问首页 → 断言导航栏红点渲染
    ua2 = Client()
    _, _, _, token = ua2.get_with_csrf("/login")
    c3, _, hdrs3 = ua2.post_with_csrf(
        "/login", {"username": _EPHEMERAL["userA"]["name"], "password": _EPHEMERAL["userA"]["pwd"]}, token)
    ua2.save_cookies(hdrs3)
    badge_ok = False
    badge_num = None
    if c3 == 302 and "/login" not in hdrs3.get("Location", ""):
        hc, home, _ = ua2.get("/")
        import re as _re
        m = _re.search(r'<a href="[^"]*messages[^"]*"[^>]*>.*?<span class="badge[^"]*"[^>]*>(\d+)</span>', home, _re.S)
        if m:
            badge_num = int(m.group(1))
            badge_ok = badge_num > 0
    if banned_login and tid and unread > 0 and has_appeal and badge_ok:
        record("RPT-9", PASS, "解封红点闭环：被封登录拒绝、申诉通过、未读=%d、首页红点=%d" % (unread, badge_num))
    else:
        record("RPT-9", FAIL, "红点闭环异常：banned_login=%s tid=%s unread=%s has_appeal=%s badge=%s"
               % (banned_login, tid, unread, has_appeal, badge_num))


def test_rpt_10_admin_notified_on_submit():
    """RPT-10 双向通知断链修复：用户提交举报/申诉后，
    所有管理员（role=1）立即收到 type=new_ticket 站内通知（含工单目标描述）。"""
    if not (ADMIN_PWD and USER_A_PWD and USER_B_PWD):
        record("RPT-10", SKIP, "临时测试账号未就绪")
        return
    if not _reset_rate_limiters():
        record("RPT-10", SKIP, "限速器重置钩子不可用（请以 FLASK_TEST=1 启动应用）")
        return
    import sys as _sys
    _sys.path.insert(0, os.path.join(BASE, "backend"))
    _sys.path.insert(0, os.path.join(BASE, "backend", "core"))
    _sys.path.insert(0, os.path.join(BASE, "backend", "config"))
    _sys.path.insert(0, os.path.join(BASE, "backend", "services"))
    from db import get_cursor
    adm_id = _db_get_user_id(_EPHEMERAL["admin"]["name"])
    ua_uid = _db_get_user_id(_EPHEMERAL["userA"]["name"])
    title = "RPT10双向通知测试帖%d" % int(time.time() % 100000)
    rep_ok = appeal_ok = False
    try:
        # ===== 路径一：举报通知 =====
        ua = Client()
        if _login_ephemeral("userA", ua)[0] != 302:
            record("RPT-10", FAIL, "userA 登录失败")
            return
        _, _, _, token = ua.get_with_csrf("/post/new")
        ua.post_with_csrf(
            "/post/new", {"title": title, "content": "RPT10 双向通知测试内容", "board_id": 1}, token)
        post_id = _db_find_post_id_by_title(title)
        _, _, _, token = ua.get_with_csrf("/post/%d" % post_id)
        code1, _, _ = ua.post_with_csrf(
            "/report", {"target_type": "post", "target_id": post_id,
                        "category": "violation", "description": "RPT10 双向通知测试"}, token)
        rep_note = None
        with get_cursor() as cur:
            cur.execute(
                "SELECT type, content FROM notification WHERE user_id = ? "
                "AND type = 'new_ticket' AND content LIKE ? ORDER BY id DESC LIMIT 1",
                (adm_id, "%RPT10双向通知测试帖%"))
            row = cur.fetchone()
            if row:
                rep_note = (row[0], row[1])
        rep_ok = (code1 in (200, 302) and rep_note is not None
                  and rep_note[0] == "new_ticket" and "举报" in rep_note[1]
                  and title in rep_note[1])
        # ===== 路径二：申诉通知 =====
        adm = Client()
        if _login_ephemeral("admin", adm)[0] != 302:
            record("RPT-10", FAIL, "admin 登录失败")
            return
        ub_uid = _db_get_user_id(_EPHEMERAL["userB"]["name"])
        # 确保 userB 被封禁（RPT-6 已解封，重新封禁以走申诉路径）
        from ban_service import is_user_banned as _is_banned
        banned, _, _ = _is_banned(ub_uid)
        if not banned:
            _, _, _, token = adm.get_with_csrf("/admin?tab=users")
            adm.post_with_csrf(
                "/admin/user/%d/ban" % ub_uid, {"reason": "RPT10申诉通知测试", "days": "1"}, token)
        guest = Client()
        _, _, _, token = guest.get_with_csrf("/appeal")
        code2, _, _ = guest.post_with_csrf(
            "/appeal", {"username": _EPHEMERAL["userB"]["name"], "reason": "RPT10申诉通知测试"}, token)
        app_note = None
        with get_cursor() as cur:
            cur.execute(
                "SELECT type, content FROM notification WHERE user_id = ? "
                "AND type = 'new_ticket' AND content LIKE ? ORDER BY id DESC LIMIT 1",
                (adm_id, "%" + _EPHEMERAL["userB"]["name"] + "%"))
            row = cur.fetchone()
            if row:
                app_note = (row[0], row[1])
        appeal_ok = (code2 in (200, 302) and app_note is not None
                     and app_note[0] == "new_ticket" and "申诉" in app_note[1]
                     and _EPHEMERAL["userB"]["name"] in app_note[1])
        # 解封 userB，保持状态干净（同 RPT-6 惯例）
        _, _, _, token = adm.get_with_csrf("/admin?tab=reports")
        tid = _db_find_ticket_id(ub_uid, "appeal", ub_uid)
        if tid:
            adm.post_with_csrf("/admin/appeal/%d/handle" % tid, {"action": "approve"}, token)
    finally:
        # 清理测试数据：帖子 / 举报工单 / 发给所有管理员的 new_ticket 通知（含真实管理员 fly）
        with get_cursor() as cur:
            cur.execute("DELETE FROM post WHERE title LIKE 'RPT10双向通知测试帖%'")
            cur.execute("DELETE FROM report_tickets WHERE description LIKE 'RPT10%'")
            cur.execute(
                "DELETE FROM notification WHERE type = 'new_ticket' "
                "AND (content LIKE '%RPT10双向通知测试帖%' OR content LIKE '%secusb%')")
    if rep_ok and appeal_ok:
        record("RPT-10", PASS, "双向通知：举报/申诉提交后管理员均收到 new_ticket 通知（含目标描述）")
    else:
        record("RPT-10", FAIL, "双向通知异常：rep_ok=%s appeal_ok=%s" % (rep_ok, appeal_ok))


def test_rpt_11_admin_direct_unban_notify():
    """RPT-11 双向通知闭环：管理员在后台直接解禁用户（非申诉工单路径），
    被解禁用户通知表收到 type=system 解封通知（登录后红点+列表可见）。"""
    if not (ADMIN_PWD and USER_A_PWD and USER_B_PWD):
        record("RPT-11", SKIP, "临时测试账号未就绪")
        return
    adm = Client()
    if _login_ephemeral("admin", adm)[0] != 302:
        record("RPT-11", FAIL, "admin 登录失败")
        return
    import sys as _sys
    _sys.path.insert(0, os.path.join(BASE, "backend"))
    _sys.path.insert(0, os.path.join(BASE, "backend", "core"))
    _sys.path.insert(0, os.path.join(BASE, "backend", "config"))
    _sys.path.insert(0, os.path.join(BASE, "backend", "services"))
    from db import get_cursor
    from ban_service import is_user_banned as _is_banned
    ua_uid = _db_get_user_id(_EPHEMERAL["userA"]["name"])
    try:
        # 确保 userA 处于封禁状态（RPT-9 已解封，重新封禁以走直接解禁路径）
        banned, _, _ = _is_banned(ua_uid)
        if not banned:
            _, _, _, token = adm.get_with_csrf("/admin?tab=users")
            adm.post_with_csrf(
                "/admin/user/%d/ban" % ua_uid, {"reason": "RPT11直接解禁测试", "days": "1"}, token)
        # 后台直接解禁（走 /admin/user/<id>/unban 路由）
        _, _, _, token = adm.get_with_csrf("/admin?tab=users")
        code, _, _ = adm.post_with_csrf("/admin/user/%d/unban" % ua_uid, {}, token)
        # DB 断言：解封通知落库（type=system，含"已解封"）
        note = None
        with get_cursor() as cur:
            cur.execute(
                "SELECT type, content FROM notification WHERE user_id = ? "
                "AND type = 'system' AND content LIKE '%已解封%' ORDER BY id DESC LIMIT 1",
                (ua_uid,))
            row = cur.fetchone()
            if row:
                note = (row[0], row[1])
        ok = (code == 302 and note is not None
              and note[0] == "system" and "你的账号已解封，请重新登录" in note[1])
        # 解封状态确认
        banned2, _, _ = _is_banned(ua_uid)
        ok = ok and not banned2
        if ok:
            record("RPT-11", PASS, "后台直接解禁 → 用户收到 system 解封通知（含重新登录文案）")
        else:
            record("RPT-11", FAIL, "直接解禁通知异常：code=%s note=%s banned_after=%s" % (code, note, banned2))
    finally:
        # 清理 userA 全部测试通知（新账号通知均为本测试产生）
        with get_cursor() as cur:
            cur.execute("DELETE FROM notification WHERE user_id = ?", (ua_uid,))
            cur.execute(
                "DELETE FROM report_tickets WHERE reporter_id = ? OR target_id = ?", (ua_uid, ua_uid))


def test_rpt_12_admin_socketio_alert():
    """RPT-12 管理员实时通知：普通用户提交申诉后，
    管理员 SocketIO 客户端实时收到 new_ticket_alert 事件（ticket_type=appeal）。"""
    if not (ADMIN_PWD and USER_A_PWD and USER_B_PWD):
        record("RPT-12", SKIP, "临时测试账号未就绪")
        return
    if not _reset_rate_limiters():
        record("RPT-12", SKIP, "限速器重置钩子不可用（请以 FLASK_TEST=1 启动应用）")
        return
    import sys as _sys
    _sys.path.insert(0, os.path.join(BASE, "backend"))
    _sys.path.insert(0, os.path.join(BASE, "backend", "core"))
    _sys.path.insert(0, os.path.join(BASE, "backend", "config"))
    _sys.path.insert(0, os.path.join(BASE, "backend", "services"))
    from db import get_cursor
    try:
        import socketio as _sio_client
    except ImportError:
        record("RPT-12", SKIP, "python-socketio 客户端未安装")
        return
    adm = Client()
    if _login_ephemeral("admin", adm)[0] != 302:
        record("RPT-12", FAIL, "admin 登录失败")
        return
    ub_uid = _db_get_user_id(_EPHEMERAL["userB"]["name"])
    from ban_service import is_user_banned as _is_banned
    received = []
    sio = None
    try:
        # 管理员 SocketIO 客户端连接（带会话 Cookie，withCredentials 等价）
        sio = _sio_client.Client()
        sio.on("new_ticket_alert", lambda d: received.append(d))
        cookie = "; ".join("%s=%s" % kv for kv in adm.cookies.items())
        sio.connect(BASE_URL, headers={"Cookie": cookie}, wait_timeout=5)
        # 触发申诉：确保 userB 被封禁 → 未登录提交申诉
        banned, _, _ = _is_banned(ub_uid)
        if not banned:
            _, _, _, token = adm.get_with_csrf("/admin?tab=users")
            adm.post_with_csrf(
                "/admin/user/%d/ban" % ub_uid, {"reason": "RPT12实时通知测试", "days": "1"}, token)
        guest = Client()
        _, _, _, token = guest.get_with_csrf("/appeal")
        guest.post_with_csrf(
            "/appeal", {"username": _EPHEMERAL["userB"]["name"], "reason": "RPT12实时通知测试"}, token)
        # 等待 SocketIO 事件到达（服务端 emit 为同步，网络往返留余量）
        for _ in range(10):
            if received:
                break
            time.sleep(0.5)
    except Exception as e:
        record("RPT-12", FAIL, "socketio 连接/事件异常：%s" % e)
        return
    finally:
        if sio and sio.connected:
            sio.disconnect()
        # 清理：解封 userB + 删除该测试产生的工单与通知（含管理员 new_ticket）
        try:
            from ban_service import unban_user as _unban
            _unban(ub_uid)
            with get_cursor() as cur:
                cur.execute("DELETE FROM report_tickets WHERE description LIKE 'RPT12%'")
                cur.execute("DELETE FROM notification WHERE content LIKE '%RPT12%'")
                cur.execute("DELETE FROM notification WHERE content LIKE '%secusb%'")
        except Exception:
            pass
    got = received[-1] if received else None
    ok = got is not None and got.get("ticket_type") == "appeal"
    if ok:
        record("RPT-12", PASS, "管理员 SocketIO 实时收到 new_ticket_alert（ticket_type=%s）" % got.get("ticket_type"))
    else:
        record("RPT-12", FAIL, "未收到 new_ticket_alert：received=%s" % (received,))


# ============================================================
# ADM：后台帖子管理权限收紧（私密/匿名帖作者脱敏、无封禁作者入口）
# ============================================================
def test_adm_04_private_post_review_masked():
    """ADM-4：管理员查看私密帖详情 → 200、正文可见、HTML 不含真实作者昵称/author_id、无封禁作者按钮"""
    if not (ADMIN_PWD and USER_A_PWD):
        record("ADM-4", SKIP, "临时测试账号未就绪，跳过")
        return
    ua = Client()
    _login_ephemeral("userA", ua)
    title = "ADM4-%d" % int(time.time())
    _, _, _, token = ua.get_with_csrf("/post/new")
    ua.post_with_csrf(
        "/post/new", {"title": title, "content": "ADM4私密帖审核正文", "board_id": 1, "is_private": "1"}, token)
    pid = _db_find_post_id_by_title(title)
    nickname_a = _EPHEMERAL["userA"]["name"]  # 临时账号昵称=用户名
    code, body = 0, ""
    if pid:
        adm = Client()
        _login_ephemeral("admin", adm)
        code, body, _, _ = adm.get_with_csrf("/admin/post/%d" % pid)
        ok = (code == 200
              and "ADM4私密帖审核正文" in body
              and "保密" in body
              and nickname_a not in body
              and "author_id" not in body
              and "封禁作者" not in body)
        # 清理：管理员删除该私密帖（级联评论/通知）
        _, _, _, token = adm.get_with_csrf("/admin")
        adm.post_with_csrf("/admin/post/%d/delete" % pid, {}, token)
    else:
        ok = False
    record("ADM-4", PASS if ok else FAIL,
           "私密帖预览：code=%s 正文=%s 保密掩码=%s 无真实昵称=%s 无author_id=%s 无封禁按钮=%s"
           % (code, "ADM4私密帖审核正文" in body, "保密" in body,
              nickname_a not in body, "author_id" not in body, "封禁作者" not in body))


# ============================================================
# FOL：基础关注/粉丝系统
# ============================================================
def _db_follow_counts(user_id):
    import sys as _sys
    _sys.path.insert(0, os.path.join(BASE, "backend"))
    _sys.path.insert(0, os.path.join(BASE, "backend", "core"))
    _sys.path.insert(0, os.path.join(BASE, "backend", "config"))
    from follow_service import get_follow_counts
    return get_follow_counts(user_id)


def _db_notification_count(user_id, ntype):
    import sys as _sys
    _sys.path.insert(0, os.path.join(BASE, "backend"))
    _sys.path.insert(0, os.path.join(BASE, "backend", "core"))
    _sys.path.insert(0, os.path.join(BASE, "backend", "config"))
    from db import get_cursor
    with get_cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM notification WHERE user_id = ? AND type = ?",
            (user_id, ntype))
        return cur.fetchone()[0]


def _db_clear_notifications(user_id):
    import sys as _sys
    _sys.path.insert(0, os.path.join(BASE, "backend"))
    _sys.path.insert(0, os.path.join(BASE, "backend", "core"))
    _sys.path.insert(0, os.path.join(BASE, "backend", "config"))
    from db import get_cursor
    with get_cursor() as cur:
        cur.execute("DELETE FROM notification WHERE user_id = ?", (user_id,))


def _follow_post(ua, target_id, action="follow", token=None):
    """登录态 POST 关注/取关，返回 (code, json)"""
    import json as _json
    if token is None:
        _, _, _, token = ua.get_with_csrf("/user/%d" % target_id)
    code, body, hdrs = ua.post_with_csrf(
        "/user/%d/follow" % target_id, {"action": action}, token)
    try:
        d = _json.loads(body)
    except Exception:
        d = {}
    return code, d


def test_fol_01_unauth_follow_rejected():
    """FOL-1：未登录 POST 关注 → 被拒（400 CSRF / 302 登录 / 403）"""
    if not USER_B_PWD:
        record("FOL-1", SKIP, "临时测试账号未就绪")
        return
    target_id = _db_get_user_id(_EPHEMERAL["userB"]["name"])
    c = Client()
    code, _, hdrs = c.post_with_csrf("/user/%d/follow" % target_id, {}, None)
    ok = code in (302, 400, 403) and "follow" not in hdrs.get("Location", "")
    record("FOL-1", PASS if ok else FAIL, "未登录关注 -> %s（被拒）" % code)


def test_fol_02_follow_success_notify():
    """FOL-2：userA 关注 userB → 成功、粉丝数+1、userB 收到 follow 通知"""
    if not (USER_A_PWD and USER_B_PWD):
        record("FOL-2", SKIP, "临时测试账号未就绪")
        return
    ua, ub = Client(), Client()
    _login_ephemeral("userA", ua)
    _login_ephemeral("userB", ub)
    uid_b = _db_get_user_id(_EPHEMERAL["userB"]["name"])
    _db_clear_notifications(uid_b)
    code, d = _follow_post(ua, uid_b, "follow")
    _, followers = _db_follow_counts(uid_b)
    cnt = _db_notification_count(uid_b, "follow")
    ok = (code == 200 and d.get("ok") is True and d.get("action") == "follow"
          and followers == 1 and cnt >= 1)
    record("FOL-2", PASS if ok else FAIL,
           "code=%s ok=%s action=%s 粉丝=%s follow通知=%s"
           % (code, d.get("ok"), d.get("action"), followers, cnt))
    # 还原：取关
    _follow_post(ua, uid_b, "unfollow")


def test_fol_03_duplicate_follow_rejected():
    """FOL-3：重复关注被拒（唯一索引兜底，关系保持 1 条）"""
    if not (USER_A_PWD and USER_B_PWD):
        record("FOL-3", SKIP, "临时测试账号未就绪")
        return
    ua = Client()
    _login_ephemeral("userA", ua)
    uid_b = _db_get_user_id(_EPHEMERAL["userB"]["name"])
    _follow_post(ua, uid_b, "follow")          # 先关注
    code, d = _follow_post(ua, uid_b, "follow")  # 重复关注（幂等拒绝）
    _, followers = _db_follow_counts(uid_b)
    ok = (d.get("ok") is False and followers == 1)
    record("FOL-3", PASS if ok else FAIL,
           "重复关注 code=%s ok=%s msg=%s 粉丝=%s" % (code, d.get("ok"), d.get("msg"), followers))
    _follow_post(ua, uid_b, "unfollow")  # 还原：取关


def test_fol_04_unfollow_success():
    """FOL-4：取消关注成功，粉丝数-1"""
    if not (USER_A_PWD and USER_B_PWD):
        record("FOL-4", SKIP, "临时测试账号未就绪")
        return
    ua = Client()
    _login_ephemeral("userA", ua)
    uid_b = _db_get_user_id(_EPHEMERAL["userB"]["name"])
    _follow_post(ua, uid_b, "follow")            # 关注
    code, d = _follow_post(ua, uid_b, "unfollow")  # 取关
    _, followers = _db_follow_counts(uid_b)
    ok = (d.get("ok") is True and d.get("action") == "unfollow" and followers == 0)
    record("FOL-4", PASS if ok else FAIL,
           "取关 code=%s ok=%s action=%s 粉丝=%s" % (code, d.get("ok"), d.get("action"), followers))


def test_fol_05_self_follow_rejected():
    """FOL-5：自己关注自己被拒"""
    if not USER_A_PWD:
        record("FOL-5", SKIP, "临时测试账号未就绪")
        return
    ua = Client()
    _login_ephemeral("userA", ua)
    uid_a = _db_get_user_id(_EPHEMERAL["userA"]["name"])
    code, d = _follow_post(ua, uid_a, "follow")
    ok = (d.get("ok") is False and "自己" in (d.get("msg") or ""))
    record("FOL-5", PASS if ok else FAIL,
           "自我关注 code=%s ok=%s msg=%s" % (code, d.get("ok"), d.get("msg")))


def test_fol_06_rate_limited_429():
    """FOL-6：连发 21 次关注 → 第 21 次 429（限速 20 次/分钟/用户）"""
    if not (USER_A_PWD and USER_B_PWD):
        record("FOL-6", SKIP, "临时测试账号未就绪")
        return
    _reset_rate_limiters()
    ua = Client()
    _login_ephemeral("userA", ua)
    uid_b = _db_get_user_id(_EPHEMERAL["userB"]["name"])
    _, _, _, token = ua.get_with_csrf("/user/%d" % uid_b)
    codes = []
    for i in range(21):
        code, _, hdrs = ua.post_with_csrf(
            "/user/%d/follow" % uid_b, {"action": "follow"}, token)
        codes.append(code)
    ok = codes[-1] == 429 and codes.count(429) >= 1
    record("FOL-6", PASS if ok else FAIL,
           "连发21次 状态码序列=%s" % (codes,))


def test_fol_07_follow_notify_link_jump():
    """FOL-7：userA 关注 userB → userB 通知页 HTML 含 <a href='/user/A真实ID'> 跳转链接"""
    if not (USER_A_PWD and USER_B_PWD):
        record("FOL-7", SKIP, "临时测试账号未就绪")
        return
    _reset_rate_limiters()  # FOL-6 已耗尽限速配额，先重置
    ua, ub = Client(), Client()
    _login_ephemeral("userA", ua)
    _login_ephemeral("userB", ub)
    uid_a = _db_get_user_id(_EPHEMERAL["userA"]["name"])
    uid_b = _db_get_user_id(_EPHEMERAL["userB"]["name"])
    _db_clear_notifications(uid_b)
    _follow_post(ua, uid_b, "unfollow")  # 先确保未关注（FOL-6 遗留关系，避免重复关注不写通知）
    _follow_post(ua, uid_b, "follow")   # 关注 → userB 收到通知
    code, body, _, _ = ub.get_with_csrf("/messages?tab=system")
    ok = (code == 200
          and 'href="/user/%d"' % uid_a in body
          and "关注了你" in body)
    record("FOL-7", PASS if ok else FAIL,
           "通知页 code=%s 含跳转/user/%d=%s 含文案=%s"
           % (code, uid_a, 'href="/user/%d"' % uid_a in body, "关注了你" in body))
    _follow_post(ua, uid_b, "unfollow")  # 还原：取关


# ============================================================
# AI：AI 标签推荐
# ============================================================
def _db_get_all_tag_names():
    """从 DB 查询全部标签名（候选列表）"""
    import sys as _sys
    _sys.path.insert(0, os.path.join(BASE, "backend"))
    _sys.path.insert(0, os.path.join(BASE, "backend", "core"))
    _sys.path.insert(0, os.path.join(BASE, "backend", "config"))
    from db import get_cursor
    with get_cursor() as cur:
        cur.execute("SELECT tag_name FROM tag")
        return [r[0] for r in cur.fetchall()]


def _ai_post(ua, title, content, token=None):
    """登录态 POST /api/ai/recommend_tags，返回 (code, json)"""
    import json as _json
    if token is None:
        _, _, _, token = ua.get_with_csrf("/post/new")
    code, body, _ = ua.post_with_csrf(
        "/api/ai/recommend_tags", {"title": title, "content": content}, token)
    try:
        d = _json.loads(body)
    except Exception:
        d = {}
    return code, d


def test_ai_01_unauth_rejected():
    """AI-1：未登录调用 AI 推荐 → 被拒（400 CSRF / 302 登录）"""
    c = Client()
    code, _, hdrs = c.post_with_csrf(
        "/api/ai/recommend_tags", {"title": "x", "content": "y"}, None)
    ok = code in (302, 400, 403) and "recommend_tags" not in hdrs.get("Location", "")
    record("AI-1", PASS if ok else FAIL, "未登录调用 AI 推荐 -> %s（被拒）" % code)


def test_ai_02_success_filtered():
    """AI-2：登录调用成功 → 200，且返回标签全部在数据库候选列表中"""
    if not USER_A_PWD:
        record("AI-2", SKIP, "临时测试账号未就绪")
        return
    _reset_rate_limiters()
    ua = Client()
    _login_ephemeral("userA", ua)
    code, d = _ai_post(ua, "Python 爬虫与数据分析实践", "如何使用 requests 抓取网页并用 pandas 分析数据")
    candidates = set(_db_get_all_tag_names())
    tags = d.get("tags") or []
    ok = (code == 200 and d.get("ok") is True
          and all(t in candidates for t in tags)
          and len(tags) <= 5)
    record("AI-2", PASS if ok else FAIL,
           "code=%s ok=%s tags=%s（全部在候选列表=%s）" % (code, d.get("ok"), tags, ok))


def test_ai_03_rate_limited():
    """AI-3：连发 6 次 → 第 6 次 429（每分钟 5 次）"""
    if not USER_A_PWD:
        record("AI-3", SKIP, "临时测试账号未就绪")
        return
    _reset_rate_limiters()
    ua = Client()
    _login_ephemeral("userA", ua)
    _, _, _, token = ua.get_with_csrf("/post/new")
    codes = []
    for i in range(6):
        code, _, _ = ua.post_with_csrf(
            "/api/ai/recommend_tags", {"title": "t%d" % i, "content": "c"}, token)
        codes.append(code)
    ok = codes[-1] == 429 and codes[:5].count(200) >= 4
    record("AI-3", PASS if ok else FAIL, "连发6次 状态码=%s" % (codes,))


def test_ai_04_invalid_json_graceful():
    """AI-4：AI 返回非法 JSON → 解析降级为空列表，不抛异常（单元验证 _parse_json_array 与无 key 降级）"""
    import sys as _sys
    _sys.path.insert(0, os.path.join(BASE, "backend"))
    _sys.path.insert(0, os.path.join(BASE, "backend", "core"))
    _sys.path.insert(0, os.path.join(BASE, "backend", "config"))
    try:
        from tag_ai_service import _parse_json_array, recommend_tags
    except Exception as e:
        record("AI-4", FAIL, "import tag_ai_service 失败: %s" % e)
        return
    # 非法 JSON / 非数组 / 代码块包裹的合法 JSON
    bad = _parse_json_array("not json {{{")
    wrapped = _parse_json_array('```json\n["a","b"]\n```')
    not_list = _parse_json_array('{"a":1}')
    empty = _parse_json_array("")
    # 无 key 时 recommend_tags 返回 []（不抛异常、不 500）
    try:
        orig = None
        import config as _cfg
        orig = _cfg.AI_CONFIG.get("api_key")
        _cfg.AI_CONFIG["api_key"] = ""
        degraded = recommend_tags("标题", "内容")
        if orig is not None:
            _cfg.AI_CONFIG["api_key"] = orig
    except Exception as e:
        record("AI-4", FAIL, "降级路径异常: %s" % e)
        return
    ok = (bad == [] and wrapped == ["a", "b"] and not_list == [] and empty == []
          and degraded == [])
    record("AI-4", PASS if ok else FAIL,
           "非法JSON=%s 代码块=%s 非数组=%s 空=%s 无key降级=%s"
           % (bad, wrapped, not_list, empty, degraded))


# ============================================================
# AI：AI 智能回复（定时任务 + ai_assistant 系统账号）
# ============================================================
def _db_exec(sql, params=()):
    """执行 SQL（仅测试环境）"""
    import sys as _sys
    _sys.path.insert(0, os.path.join(BASE, "backend"))
    _sys.path.insert(0, os.path.join(BASE, "backend", "core"))
    _sys.path.insert(0, os.path.join(BASE, "backend", "config"))
    from db import get_cursor
    with get_cursor() as cur:
        cur.execute(sql, params)
        return cur.rowcount


def _db_query(sql, params=()):
    """查询（仅测试环境）"""
    import sys as _sys
    _sys.path.insert(0, os.path.join(BASE, "backend"))
    _sys.path.insert(0, os.path.join(BASE, "backend", "core"))
    _sys.path.insert(0, os.path.join(BASE, "backend", "config"))
    from db import get_cursor
    with get_cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _db_get_ai_uid():
    row = _db_query("SELECT user_id FROM `user` WHERE username = 'ai_assistant'")
    return row[0][0] if row else None


def _ai_create_test_post(ua, tag):
    """创建测试帖并改时间（提前 11 分钟），返回 post_id"""
    title = "AI回复验证%s%d" % (tag, int(time.time()))
    _, _, _, token = ua.get_with_csrf("/post/new")
    ua.post_with_csrf("/post/new", {
        "board_id": "1", "title": title, "content": "用于验证 AI 智能回复的帖子内容。"}, token)
    pid = _db_find_post_id_by_title(title)
    _db_exec("UPDATE post SET create_time = DATE_SUB(NOW(), INTERVAL 11 MINUTE) WHERE post_id = ?", (pid,))
    return pid


def _ai_trigger(ua, pid, mock):
    """调用测试钩子触发一次扫描（限定 post_id）"""
    _, _, _, token = ua.get_with_csrf("/post/%d" % pid)
    return ua.post_with_csrf("/_test/ai-reply/run", {"mock": mock, "post_id": str(pid)}, token)


def test_ai_05_reply_success():
    """AI-5：无评论帖子 → 定时任务触发 → ai_assistant 成功发布评论、标记 ai_replied、作者收到通知"""
    if not USER_A_PWD:
        record("AI-5", SKIP, "临时测试账号未就绪")
        return
    _reset_rate_limiters()
    ua = Client()
    _login_ephemeral("userA", ua)
    pid = _ai_create_test_post(ua, "成功")
    ai_uid = _db_get_ai_uid()
    author_id = _db_get_user_id(_EPHEMERAL["userA"]["name"])
    code, body, _ = _ai_trigger(ua, pid, "ok")
    ai_comments = _db_query(
        "SELECT COUNT(*) FROM comment WHERE post_id = ? AND user_id = ?", (pid, ai_uid))
    replied = _db_query("SELECT ai_replied FROM post WHERE post_id = ?", (pid,))
    notifs = _db_query(
        "SELECT COUNT(*) FROM notification WHERE user_id = ? AND post_id = ? AND type = 'comment' AND from_user_id = ?",
        (author_id, pid, ai_uid))
    ok = (code == 200 and ai_comments[0][0] == 1 and replied[0][0] == 1 and notifs[0][0] == 1)
    record("AI-5", PASS if ok else FAIL,
           "code=%s AI评论=%s ai_replied=%s 作者通知=%s"
           % (code, ai_comments[0][0], replied[0][0], notifs[0][0]))


def test_ai_06_human_comment_no_reply():
    """AI-6：已有人类评论的帖子 → AI 不回复（不满足评论数=0）"""
    if not USER_A_PWD:
        record("AI-6", SKIP, "临时测试账号未就绪")
        return
    _reset_rate_limiters()
    ua = Client()
    _login_ephemeral("userA", ua)
    pid = _ai_create_test_post(ua, "有人")
    # 人类先评论
    _, _, _, token = ua.get_with_csrf("/post/%d" % pid)
    ua.post_with_csrf("/post/%d/comment" % pid, {"content": "真人评论，AI 不应回复。"}, token)
    ai_uid = _db_get_ai_uid()
    code, _, _ = _ai_trigger(ua, pid, "ok")
    ai_comments = _db_query(
        "SELECT COUNT(*) FROM comment WHERE post_id = ? AND user_id = ?", (pid, ai_uid))
    replied = _db_query("SELECT ai_replied FROM post WHERE post_id = ?", (pid,))
    ok = (code == 200 and ai_comments[0][0] == 0 and replied[0][0] == 0)
    record("AI-6", PASS if ok else FAIL,
           "code=%s AI评论=%s ai_replied=%s（应 0/0）" % (code, ai_comments[0][0], replied[0][0]))


def test_ai_07_fail_no_crash_retryable():
    """AI-7：AI 调用失败 → 任务不崩溃、帖子不标记 ai_replied、无评论（下次可重试）"""
    if not USER_A_PWD:
        record("AI-7", SKIP, "临时测试账号未就绪")
        return
    _reset_rate_limiters()
    ua = Client()
    _login_ephemeral("userA", ua)
    pid = _ai_create_test_post(ua, "失败")
    ai_uid = _db_get_ai_uid()
    code, body, _ = _ai_trigger(ua, pid, "fail")
    ai_comments = _db_query(
        "SELECT COUNT(*) FROM comment WHERE post_id = ? AND user_id = ?", (pid, ai_uid))
    replied = _db_query("SELECT ai_replied FROM post WHERE post_id = ?", (pid,))
    # 任务不崩溃：钩子仍返回 200 + processed=0
    ok = (code == 200 and '"processed":0' in body
          and ai_comments[0][0] == 0 and replied[0][0] == 0)
    record("AI-7", PASS if ok else FAIL,
           "code=%s body=%s AI评论=%s ai_replied=%s（应 0/0，可重试）"
           % (code, body[:60], ai_comments[0][0], replied[0][0]))


def test_ai_08_admin_revoke():
    """AI-8：管理员撤回 AI 评论 → 评论删除、通知删除、ai_replied 置 1（永久跳过）"""
    if not (ADMIN_PWD and USER_A_PWD):
        record("AI-8", SKIP, "临时测试账号未就绪")
        return
    _reset_rate_limiters()
    ua = Client()
    _login_ephemeral("userA", ua)
    pid = _ai_create_test_post(ua, "撤回")
    ai_uid = _db_get_ai_uid()
    # 触发 AI 回复
    code, _, _ = _ai_trigger(ua, pid, "ok")
    # 取 AI 评论 id
    row = _db_query("SELECT comment_id FROM comment WHERE post_id = ? AND user_id = ?", (pid, ai_uid))
    if not row:
        record("AI-8", FAIL, "前置失败：AI 评论未生成")
        return
    cid = row[0][0]
    # 管理员撤回
    adm = Client()
    _login_ephemeral("admin", adm)
    _, _, _, token = adm.get_with_csrf("/admin?tab=comments")
    code2, _, hdrs = adm.post_with_csrf(
        "/admin/comment/%d/revoke-ai" % cid, {}, token)
    comments_after = _db_query("SELECT COUNT(*) FROM comment WHERE comment_id = ?", (cid,))
    notifs_after = _db_query(
        "SELECT COUNT(*) FROM notification WHERE post_id = ? AND from_user_id = ? AND type = 'comment'",
        (pid, ai_uid))
    replied = _db_query("SELECT ai_replied FROM post WHERE post_id = ?", (pid,))
    audit = _db_query(
        "SELECT COUNT(*) FROM audit_log WHERE action = 'revoke_ai_reply' AND target_type = 'comment' AND target_id = ?",
        (cid,))
    ok = (code2 in (302, 200) and comments_after[0][0] == 0
          and notifs_after[0][0] == 0 and replied[0][0] == 1 and audit[0][0] >= 1)
    record("AI-8", PASS if ok else FAIL,
           "code=%s 评论残留=%s 通知残留=%s ai_replied=%s（应 1 永久跳过） 审计=%s"
           % (code2, comments_after[0][0], notifs_after[0][0], replied[0][0], audit[0][0]))


def test_ai_09_revoked_not_replied_again():
    """AI-9：撤回后再次运行扫描 → 该帖不再被 AI 回复（永久跳过语义）"""
    if not (ADMIN_PWD and USER_A_PWD):
        record("AI-9", SKIP, "临时测试账号未就绪")
        return
    _reset_rate_limiters()
    ua = Client()
    _login_ephemeral("userA", ua)
    pid = _ai_create_test_post(ua, "跳过")
    ai_uid = _db_get_ai_uid()
    # 触发 AI 回复 → 撤回
    _ai_trigger(ua, pid, "ok")
    row = _db_query("SELECT comment_id FROM comment WHERE post_id = ? AND user_id = ?", (pid, ai_uid))
    if not row:
        record("AI-9", FAIL, "前置失败：AI 评论未生成")
        return
    cid = row[0][0]
    adm = Client()
    _login_ephemeral("admin", adm)
    _, _, _, token = adm.get_with_csrf("/admin?tab=comments")
    adm.post_with_csrf("/admin/comment/%d/revoke-ai" % cid, {}, token)
    # 撤回后再跑一次扫描（mock=ok 也会处理，但该帖应被 ai_replied=1 排除）
    code2, body, _ = _ai_trigger(ua, pid, "ok")
    ai_comments = _db_query(
        "SELECT COUNT(*) FROM comment WHERE post_id = ? AND user_id = ?", (pid, ai_uid))
    replied = _db_query("SELECT ai_replied FROM post WHERE post_id = ?", (pid,))
    ok = (code2 == 200 and '"processed":0' in body
          and ai_comments[0][0] == 0 and replied[0][0] == 1)
    record("AI-9", PASS if ok else FAIL,
           "code=%s body=%s AI评论=%s ai_replied=%s（应 0/1，永久跳过）"
           % (code2, body[:50], ai_comments[0][0], replied[0][0]))


# ============================================================
# SEM-1~5：帖子语义搜索（ChromaDB/numpy 本地向量 + 降级策略）
# ============================================================
def _sem_count():
    """向量库当前索引数量（GET 测试钩子，无需 CSRF）"""
    c = Client()
    code, body, _ = c.get("/_test/semantic/count")
    if code == 200:
        try:
            import json as _json
            return _json.loads(body).get("count", 0)
        except Exception:
            return 0
    return 0


def _sem_wait_ready(timeout=240):
    """等待向量库就绪（启动后台重建完成，count>0）"""
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            if _sem_count() > 0:
                return True
        except Exception:
            pass
        time.sleep(3)
    return False


def _sem_search(ua, q):
    """调用语义搜索接口（登录态，GET 无需 CSRF）。返回 (code, body, headers)"""
    return ua.get("/api/search/semantic?q=" + urllib.parse.quote(q))


def test_sem_01_semantic_match():
    """SEM-1：语义搜索 'Python 爬虫' → 命中 'Scrapy 抓取网页数据' 帖（不依赖关键词同字面）"""
    if not USER_A_PWD:
        record("SEM-1", SKIP, "临时测试账号未就绪")
        return
    _reset_rate_limiters()
    ua = Client()
    _login_ephemeral("userA", ua)
    if not _sem_wait_ready():
        record("SEM-1", FAIL, "向量库未就绪（count=0）")
        return
    ts = str(int(time.time()))
    title = "如何用 Scrapy 抓取网页数据%s" % ts[-6:]
    _, _, _, token = ua.get_with_csrf("/post/new")
    ua.post_with_csrf("/post/new", {
        "board_id": "1", "title": title,
        "content": "使用 Scrapy 框架编写网络爬虫，批量抓取网站页面并提取正文内容。"}, token)
    pid = _db_find_post_id_by_title(title)
    # 等待发帖钩子的后台增量索引完成（轮询语义搜索命中该帖）
    hit = None
    t0 = time.time()
    while time.time() - t0 < 90:
        code, body, _ = _sem_search(ua, "Python 爬虫")
        if code == 200 and '"mode":"semantic"' in body.replace(" ", ""):
            try:
                import json as _json
                data = _json.loads(body)
                for r in data.get("results", []):
                    if r["post_id"] == pid:
                        hit = r
                        break
            except Exception:
                pass
            if hit:
                break
        time.sleep(2)
    ok = hit is not None and hit["similarity"] >= 0.2
    record("SEM-1", PASS if ok else FAIL,
           "命中=%s 相似度=%s 目标帖=%s" % (bool(hit), hit["similarity"] if hit else None, pid))


def test_sem_02_private_excluded():
    """SEM-2：私密帖不出现在语义搜索结果中"""
    if not USER_A_PWD:
        record("SEM-2", SKIP, "临时测试账号未就绪")
        return
    _reset_rate_limiters()
    ua = Client()
    _login_ephemeral("userA", ua)
    if not _sem_wait_ready():
        record("SEM-2", FAIL, "向量库未就绪（count=0）")
        return
    ts = str(int(time.time()))
    title = "私密语义独特词%s" % ts[-6:]
    _, _, _, token = ua.get_with_csrf("/post/new")
    ua.post_with_csrf("/post/new", {
        "board_id": "1", "title": title,
        "content": "这是仅自己可见的私密帖子，不应进入语义搜索索引。",
        "is_private": "1"}, token)
    pid = _db_find_post_id_by_title(title)
    # 等后台处理（私密帖不应被索引；即使索引也被回源过滤）
    time.sleep(4)
    code, body, _ = _sem_search(ua, "私密语义独特词")
    try:
        import json as _json
        data = _json.loads(body) if code == 200 else {}
        ids = [r["post_id"] for r in data.get("results", [])]
    except Exception:
        ids = []
    ok = (code == 200 and pid not in ids)
    record("SEM-2", PASS if ok else FAIL,
           "code=%s 私密帖未出现在结果=%s" % (code, ok))


def test_sem_03_short_query_degrades():
    """SEM-3：空输入或 1 个字 → 不触发语义搜索，降级普通搜索（不 500）"""
    if not USER_A_PWD:
        record("SEM-3", SKIP, "临时测试账号未就绪")
        return
    _reset_rate_limiters()
    ua = Client()
    _login_ephemeral("userA", ua)
    code1, body1, _ = _sem_search(ua, "")
    code2, body2, _ = _sem_search(ua, "P")
    ok = (code1 == 200 and '"mode":"like"' in body1
          and code2 == 200 and '"mode":"like"' in body2)
    record("SEM-3", PASS if ok else FAIL,
           "空=%s/%s 单字=%s/%s（应 200/like）"
           % (code1, body1[:30], code2, body2[:30]))


def test_sem_04_rate_limited():
    """SEM-4：连发 21 次语义搜索 → 第 21 次 429"""
    if not USER_A_PWD:
        record("SEM-4", SKIP, "临时测试账号未就绪")
        return
    _reset_rate_limiters()
    ua = Client()
    _login_ephemeral("userA", ua)
    codes = []
    for _ in range(21):
        code, _, _ = _sem_search(ua, "Python")
        codes.append(code)
    ok = codes[:20].count(200) >= 19 and codes[-1] == 429
    record("SEM-4", PASS if ok else FAIL,
           "状态码序列=%s（末次应 429）" % codes)


def test_sem_05_vector_unavailable_degrade():
    """SEM-5：向量库不可用/未初始化 → 降级 SQL LIKE 搜索，不报 500"""
    if not USER_A_PWD:
        record("SEM-5", SKIP, "临时测试账号未就绪")
        return
    _reset_rate_limiters()
    ua = Client()
    _login_ephemeral("userA", ua)
    # 模拟向量库不可用（测试钩子无需 admin，FLASK_TEST 下开放）
    _, _, _, token = ua.get_with_csrf("/")
    ua.post_with_csrf("/_test/semantic/rebuild", {"mode": "disable"}, token)
    try:
        code, body, _ = _sem_search(ua, "Python 爬虫")
        ok = code == 200 and '"mode":"like"' in body and '"results"' in body
    finally:
        ua.post_with_csrf("/_test/semantic/enable", {}, token)
    record("SEM-5", PASS if ok else FAIL,
           "code=%s body=%s（应 200/like）" % (code, body[:60]))


def test_sem_06_rebuild_process_locked():
    """SEM-6：进程锁拦截并发重建，不损坏向量库；释放后重建与语义搜索正常"""
    if not USER_A_PWD:
        record("SEM-6", SKIP, "临时测试账号未就绪")
        return
    _reset_rate_limiters()
    ua = Client()
    _login_ephemeral("userA", ua)
    _, _, _, token = ua.get_with_csrf("/")
    # 测试进程先持锁文件 → 应用进程 rebuild 应被跳过（rebuilt=0）
    import sys as _sys
    _sys.path.insert(0, os.path.join(BASE, "backend", "services"))
    from filelock import FileLock
    lock_path = os.path.join(BASE, "backend", "vector_store", ".rebuild.lock")
    lock = FileLock(lock_path)
    lock.acquire(timeout=0)
    try:
        code1, body1, _ = ua.post_with_csrf("/_test/semantic/rebuild", {}, token)
    finally:
        lock.release()
    # 释放锁后 → 正常重建
    code2, body2, _ = ua.post_with_csrf("/_test/semantic/rebuild", {}, token)
    # 语义搜索仍正常
    code3, body3, _ = _sem_search(ua, "Python 爬虫")
    ok = (code1 == 200 and '"indexed":0' in body1
          and code2 == 200 and '"indexed"' in body2
          and code3 == 200)
    record("SEM-6", PASS if ok else FAIL,
           "持锁=%s(%s) 释放=%s(%s) 搜索=%s（应 200/0 200/>0 200）"
           % (code1, body1[:50], code2, body2[:60], code3))




# ============================================================
# DM-1~6：站内私信
# ============================================================
def _db_uid_by_role(role):
    """临时测试账号 user_id"""
    acc = _EPHEMERAL.get(role)
    if not acc:
        return None
    rows = _db_query("SELECT user_id FROM `user` WHERE username = ?", (acc["name"],))
    return rows[0][0] if rows else None


def _db_exec(sql, params=()):
    """执行写 SQL（测试环境，DELETE/UPDATE 等）"""
    import sys as _sys
    _sys.path.insert(0, os.path.join(BASE, "backend"))
    _sys.path.insert(0, os.path.join(BASE, "backend", "core"))
    _sys.path.insert(0, os.path.join(BASE, "backend", "config"))
    from db import get_cursor
    with get_cursor() as cur:
        cur.execute(sql, params)


def _db_clean_messages(uid):
    _db_exec("DELETE FROM message WHERE sender_id = ? OR receiver_id = ?", (uid, uid))


def _db_follow(a, b):
    """建立 A→B 关注关系（幂等）"""
    _db_exec(
        "INSERT INTO user_follows (follower_id, followee_id) VALUES (?, ?) "
        "ON DUPLICATE KEY UPDATE follower_id = follower_id", (a, b))


def _db_clear_follows(a, b):
    """清除 A、B 之间所有关注关系（双向）"""
    _db_exec(
        "DELETE FROM user_follows WHERE (follower_id = ? AND followee_id = ?)"
        " OR (follower_id = ? AND followee_id = ?)", (a, b, b, a))


def _db_clear_blocks(a, b):
    """清除 A、B 之间所有拉黑关系（双向）"""
    _db_exec(
        "DELETE FROM user_blocks WHERE (blocker_id = ? AND blocked_id = ?)"
        " OR (blocker_id = ? AND blocked_id = ?)", (a, b, b, a))


def _db_clear_messages_between(a, b):
    """清除 A、B 之间所有私信（双向）"""
    _db_exec(
        "DELETE FROM message WHERE (sender_id = ? AND receiver_id = ?)"
        " OR (sender_id = ? AND receiver_id = ?)", (a, b, b, a))


def _db_is_following(a, b):
    """是否已存在 A→B 关注关系"""
    rows = _db_query(
        "SELECT 1 FROM user_follows WHERE follower_id = ? AND followee_id = ?", (a, b))
    return bool(rows)


def test_dm_01_unauth_send_rejected():
    """DM-1：未登录发送私信 → 被拒（302/403）"""
    c = Client()
    code, body, _ = c.post("/api/messages/send", {
        "receiver_id": "1", "content": "unauth dm"})
    ok = code in (302, 403, 401, 400)
    record("DM-1", PASS if ok else FAIL,
           "code=%s（应 302/403/401/400 被拒）" % code)


def test_dm_02_send_notify():
    """DM-2：A 发私信给 B → 落库成功，B 收到 message 通知"""
    if not (USER_A_PWD and USER_B_PWD):
        record("DM-2", SKIP, "临时测试账号未就绪")
        return
    _reset_rate_limiters()
    ua = Client(); ub = Client()
    _login_ephemeral("userA", ua)
    _login_ephemeral("userB", ub)
    ub_uid = _db_uid_by_role("userB")
    if not ub_uid:
        record("DM-2", SKIP, "userB 未创建")
        return
    _db_clean_messages(ub_uid)
    _db_exec("DELETE FROM notification WHERE user_id = ?", (ub_uid,))
    _db_follow(_db_uid_by_role("userA"), ub_uid)
    ts = str(int(time.time()))
    content = "DM2 私信内容 %s" % ts[-6:]
    _, _, _, token = ua.get_with_csrf("/")
    code, body, _ = ua.post_with_csrf("/api/messages/send", {
        "receiver_id": str(ub_uid), "content": content}, token)
    msgs = _db_query(
        "SELECT COUNT(*) FROM message WHERE sender_id = ? AND receiver_id = ? AND content = ?",
        (_db_uid_by_role("userA"), ub_uid, content))
    ntfs = _db_query(
        "SELECT COUNT(*) FROM notification WHERE user_id = ? AND type = 'message'",
        (ub_uid,))
    ok = code == 200 and msgs[0][0] == 1 and ntfs[0][0] >= 1
    record("DM-2", PASS if ok else FAIL,
           "code=%s 落库=%s 通知=%s（应 200/1/>=1）" % (code, msgs[0][0], ntfs[0][0]))


def test_dm_03_third_party_blocked():
    """DM-3：C 尝试查看 A、B 的私信线程 → 看不到 A→B 消息（越权隔离）"""
    if not (USER_A_PWD and USER_B_PWD):
        record("DM-3", SKIP, "临时测试账号未就绪")
        return
    _reset_rate_limiters()
    ua = Client(); ub = Client(); uc = Client()
    _login_ephemeral("userA", ua)
    _login_ephemeral("userB", ub)
    _login_ephemeral("admin", uc)
    a_uid = _db_uid_by_role("userA"); b_uid = _db_uid_by_role("userB")
    c_uid = _db_uid_by_role("admin")
    if not (a_uid and b_uid and c_uid):
        record("DM-3", SKIP, "测试账号未就绪")
        return
    secret = "DM3SECRET%d" % (int(time.time()) % 100000)
    _db_follow(a_uid, b_uid)
    _, _, _, token = ua.get_with_csrf("/")
    ua.post_with_csrf("/api/messages/send", {
        "receiver_id": str(b_uid), "content": secret}, token)
    # C（admin）访问 /messages/<B_id>：只能看到自己与 B 的对话，不能看到 A→B 的消息
    code, body, _ = uc.get("/messages/%d" % b_uid)
    ok = code == 200 and secret not in body
    record("DM-3", PASS if ok else FAIL,
           "code=%s 泄露私密内容=%s（应 200/不泄露）" % (code, secret in body))


def test_dm_04_inbox_shows():
    """DM-4：B 查看收件箱 → 能看到 A 发来的消息"""
    if not (USER_A_PWD and USER_B_PWD):
        record("DM-4", SKIP, "临时测试账号未就绪")
        return
    _reset_rate_limiters()
    ua = Client(); ub = Client()
    _login_ephemeral("userA", ua)
    _login_ephemeral("userB", ub)
    a_uid = _db_uid_by_role("userA"); b_uid = _db_uid_by_role("userB")
    if not (a_uid and b_uid):
        record("DM-4", SKIP, "测试账号未就绪")
        return
    marker = "DM4INBOX%d" % (int(time.time()) % 100000)
    _db_clear_messages_between(a_uid, b_uid)
    _db_follow(a_uid, b_uid)
    _, _, _, token = ua.get_with_csrf("/")
    ua.post_with_csrf("/api/messages/send", {
        "receiver_id": str(b_uid), "content": marker}, token)
    # B 的对话列表页（/messages）应显示 A 及该消息片段
    code, body, _ = ub.get("/messages")
    ok = code == 200 and marker in body and _EPHEMERAL["userA"]["name"] in body
    record("DM-4", PASS if ok else FAIL,
           "code=%s 含消息=%s 含A昵称=%s（应 200/True/True）"
           % (code, marker in body, _EPHEMERAL["userA"]["name"] in body))


def test_dm_05_rate_limited():
    """DM-5：连发 21 条 → 第 21 条 429"""
    if not USER_A_PWD:
        record("DM-5", SKIP, "临时测试账号未就绪")
        return
    _reset_rate_limiters()
    ua = Client()
    _login_ephemeral("userA", ua)
    b_uid = _db_uid_by_role("userB")
    if not b_uid:
        record("DM-5", SKIP, "userB 未创建")
        return
    _db_follow(_db_uid_by_role("userA"), b_uid)
    _db_follow(b_uid, _db_uid_by_role("userA"))
    _, _, _, token = ua.get_with_csrf("/")
    codes = []
    for i in range(21):
        code, body, _ = ua.post_with_csrf("/api/messages/send", {
            "receiver_id": str(b_uid), "content": "rate %d" % i}, token)
        codes.append(code)
    ok = codes[-1] == 429 and all(c == 200 for c in codes[:20])
    record("DM-5", PASS if ok else FAIL,
           "状态码序列=%s（前20应200，末次应429）" % str(codes[:3]) + "..." + str(codes[-1]))


def test_dm_06_xss_filtered():
    """DM-6：发送含 XSS 载荷的私信 → 渲染时被过滤"""
    if not (USER_A_PWD and USER_B_PWD):
        record("DM-6", SKIP, "临时测试账号未就绪")
        return
    _reset_rate_limiters()
    ua = Client(); ub = Client()
    _login_ephemeral("userA", ua)
    _login_ephemeral("userB", ub)
    a_uid = _db_uid_by_role("userA"); b_uid = _db_uid_by_role("userB")
    if not (a_uid and b_uid):
        record("DM-6", SKIP, "测试账号未就绪")
        return
    payload = "<script>alert(1)</script><img src=x onerror=alert(1)>DM6XSS"
    _db_follow(a_uid, b_uid)
    _, _, _, token = ua.get_with_csrf("/")
    ua.post_with_csrf("/api/messages/send", {
        "receiver_id": str(b_uid), "content": payload}, token)
    # B 打开与 A 的对话页：渲染后应无 <script>alert 载荷 / onerror 原始危险代码
    code, body, _ = ub.get("/messages/%d" % a_uid)
    ok = code == 200 and "<script>alert" not in body and "onerror=" not in body and "DM6XSS" in body
    record("DM-6", PASS if ok else FAIL,
           "code=%s 含<script>alert=%s 含onerror=%s 内容可见=%s（应 200/False/False/True）"
           % (code, "<script>alert" in body, "onerror=" in body, "DM6XSS" in body))


def test_dm_12_thread_avatars():
    """DM-12：私信对话页 HTML 含双方头像元素（36px 圆形容器，sender 与 receiver 各一）"""
    if not (USER_A_PWD and USER_B_PWD):
        record("DM-12", SKIP, "临时测试账号未就绪")
        return
    _reset_rate_limiters()
    ua = Client(); ub = Client()
    _login_ephemeral("userA", ua)
    _login_ephemeral("userB", ub)
    a_uid = _db_uid_by_role("userA"); b_uid = _db_uid_by_role("userB")
    if not (a_uid and b_uid):
        record("DM-12", SKIP, "测试账号未就绪")
        return
    marker = "DM12AVATAR%d" % (int(time.time()) % 100000)
    _db_follow(a_uid, b_uid)
    _, _, _, token = ua.get_with_csrf("/")
    ua.post_with_csrf("/api/messages/send", {
        "receiver_id": str(b_uid), "content": marker}, token)
    # B 打开与 A 的对话页：渲染 HTML 应含双方 36px 头像容器（默认头像 div，测试账号无 avatar_path）
    code, body, _ = ub.get("/messages/%d" % a_uid)
    cnt = body.count("width:36px;height:36px")
    ok = code == 200 and cnt >= 2 and "rounded-circle" in body and marker in body
    record("DM-12", PASS if ok else FAIL,
           "code=%s 36px头像容器=%s 内容可见=%s（应 200/>=2/True）"
           % (code, cnt, marker in body))


# ============================================================
# NAV-1~2：导航信息架构（我的消息中心）
# ============================================================
def test_nav_01_messages_center_tabs():
    """NAV-1：登录访问 /messages → 200，含'私信'与'系统通知'两个 Tab"""
    if not USER_A_PWD:
        record("NAV-1", SKIP, "临时测试账号未就绪")
        return
    ua = Client()
    _login_ephemeral("userA", ua)
    code, body, _ = ua.get("/messages")
    ok = code == 200 and "私信" in body and "系统通知" in body
    record("NAV-1", PASS if ok else FAIL,
           "code=%s 含私信Tab=%s 含系统通知Tab=%s（应 200/True/True）"
           % (code, "私信" in body, "系统通知" in body))
    # system tab 也应渲染通知列表（重定向后 mark read）
    code2, body2, _ = ua.get("/messages?tab=system")
    ok2 = code2 == 200 and "系统通知" in body2
    record("NAV-1b", PASS if ok2 else FAIL,
           "system tab code=%s 含系统通知=%s（应 200/True）" % (code2, "系统通知" in body2))


def test_nav_02_unauth_messages_redirect():
    """NAV-2：未登录访问 /messages → 302 /login"""
    c = Client()
    code, body, hdrs = c.get("/messages")
    ok = code in (302, 401, 403)
    record("NAV-2", PASS if ok else FAIL,
           "code=%s（应 302/401/403）" % code)


# ============================================================
# FRIEND-1/2：好友关系定义（互相关注 = 好友）
# DM-7~11：私信权限收紧（单向关注限 1 条 / 好友无限 / 拉黑双向拒）
# BLOCK-1/2：拉黑（主页 404 / 关注关系清除）
# ============================================================
def _sys_paths():
    import sys as _s
    for _p in ("backend", "backend/core", "backend/config", "backend/services"):
        _s.path.insert(0, os.path.join(BASE, _p))


def test_friend_01_mutual_is_friend():
    """FRIEND-1：A、B 互相关注 → is_friend 返回 True"""
    a = _db_uid_by_role("userA"); b = _db_uid_by_role("userB")
    if not (a and b):
        record("FRIEND-1", SKIP, "测试账号未就绪")
        return
    _sys_paths()
    from follow_service import is_friend, follow_user
    _db_clear_follows(a, b)
    follow_user(a, b); follow_user(b, a)
    ok = is_friend(a, b) and is_friend(b, a)
    record("FRIEND-1", PASS if ok else FAIL,
           "互相关注后 is_friend=%s（应 True）" % is_friend(a, b))


def test_friend_02_oneway_not_friend():
    """FRIEND-2：A 单向关注 B → is_friend 返回 False"""
    a = _db_uid_by_role("userA"); b = _db_uid_by_role("userB")
    if not (a and b):
        record("FRIEND-2", SKIP, "测试账号未就绪")
        return
    _sys_paths()
    from follow_service import is_friend, follow_user
    _db_clear_follows(a, b)
    follow_user(a, b)
    ok = not is_friend(a, b)
    record("FRIEND-2", PASS if ok else FAIL,
           "单向关注后 is_friend=%s（应 False）" % is_friend(a, b))


def test_dm_07_oneway_limit_one():
    """DM-7：A 单向关注 B → A 只能发 1 条，第 2 条被拒（403）"""
    if not (USER_A_PWD and USER_B_PWD):
        record("DM-7", SKIP, "临时测试账号未就绪")
        return
    _reset_rate_limiters()
    ua = Client()
    _login_ephemeral("userA", ua)
    a = _db_uid_by_role("userA"); b = _db_uid_by_role("userB")
    if not (a and b):
        record("DM-7", SKIP, "测试账号未就绪")
        return
    _db_clear_blocks(a, b); _db_clear_follows(a, b); _db_clear_messages_between(a, b)
    _db_follow(a, b)
    _, _, _, token = ua.get_with_csrf("/")
    c1, _, _ = ua.post_with_csrf("/api/messages/send", {
        "receiver_id": str(b), "content": "DM7 第1条"}, token)
    c2, body2, _ = ua.post_with_csrf("/api/messages/send", {
        "receiver_id": str(b), "content": "DM7 第2条"}, token)
    ok = c1 == 200 and c2 == 403
    record("DM-7", PASS if ok else FAIL,
           "第1条=%s 第2条=%s 提示=%s（应 200/403）"
           % (c1, c2, ("等待回复" in body2)))


def test_dm_08_reply_unlocks_next():
    """DM-8：B 未关注 A 也能直接回复；B 回复后 A 的等回复状态解除，可继续发"""
    if not (USER_A_PWD and USER_B_PWD):
        record("DM-8", SKIP, "临时测试账号未就绪")
        return
    _reset_rate_limiters()
    ua = Client(); ub = Client()
    _login_ephemeral("userA", ua)
    _login_ephemeral("userB", ub)
    a = _db_uid_by_role("userA"); b = _db_uid_by_role("userB")
    if not (a and b):
        record("DM-8", SKIP, "测试账号未就绪")
        return
    _db_clear_blocks(a, b); _db_clear_follows(a, b); _db_clear_messages_between(a, b)
    _db_follow(a, b)  # 仅 A 单向关注 B，B 不关注 A
    _, _, _, tok_a = ua.get_with_csrf("/")
    _, _, _, tok_b = ub.get_with_csrf("/")
    c1, _, _ = ua.post_with_csrf("/api/messages/send", {
        "receiver_id": str(b), "content": "DM8 A-1"}, tok_a)
    c2, _, _ = ua.post_with_csrf("/api/messages/send", {
        "receiver_id": str(b), "content": "DM8 A-2"}, tok_a)
    b_follows_a = _db_is_following(b, a)  # 必须为 False：B 未关注 A
    c3, _, _ = ub.post_with_csrf("/api/messages/send", {
        "receiver_id": str(a), "content": "DM8 B-回复"}, tok_b)
    c4, _, _ = ua.post_with_csrf("/api/messages/send", {
        "receiver_id": str(b), "content": "DM8 A-3"}, tok_a)
    ok = c1 == 200 and c2 == 403 and (not b_follows_a) and c3 == 200 and c4 == 200
    record("DM-8", PASS if ok else FAIL,
           "A第1条=%s A第2条=%s B未关注=%s B回复=%s A再发=%s（应 200/403/False/200/200）"
           % (c1, c2, b_follows_a, c3, c4))


def test_dm_13_reply_without_follow():
    """DM-13：A 单向关注 B 发 1 条成功；B 未关注 A 直接回复成功；A 继续发成功"""
    if not (USER_A_PWD and USER_B_PWD):
        record("DM-13", SKIP, "临时测试账号未就绪")
        return
    _reset_rate_limiters()
    ua = Client(); ub = Client()
    _login_ephemeral("userA", ua)
    _login_ephemeral("userB", ub)
    a = _db_uid_by_role("userA"); b = _db_uid_by_role("userB")
    if not (a and b):
        record("DM-13", SKIP, "测试账号未就绪")
        return
    _db_clear_blocks(a, b); _db_clear_follows(a, b); _db_clear_messages_between(a, b)
    _db_follow(a, b)  # 仅 A 单向关注 B
    _, _, _, tok_a = ua.get_with_csrf("/")
    _, _, _, tok_b = ub.get_with_csrf("/")
    c1, _, _ = ua.post_with_csrf("/api/messages/send", {
        "receiver_id": str(b), "content": "DM13 A-1"}, tok_a)
    c2, _, _ = ub.post_with_csrf("/api/messages/send", {
        "receiver_id": str(a), "content": "DM13 B-回复"}, tok_b)
    c3, _, _ = ua.post_with_csrf("/api/messages/send", {
        "receiver_id": str(b), "content": "DM13 A-2"}, tok_a)
    b_follows_a = _db_is_following(b, a)
    ok = c1 == 200 and c2 == 200 and c3 == 200 and (not b_follows_a)
    record("DM-13", PASS if ok else FAIL,
           "A发1=%s B未关注回复=%s A续发=%s B关注A=%s（应 200/200/200/False）"
           % (c1, c2, c3, b_follows_a))


def test_dm_09_friends_unlimited():
    """DM-9：A、B 是好友 → 可以无限互发"""
    if not (USER_A_PWD and USER_B_PWD):
        record("DM-9", SKIP, "临时测试账号未就绪")
        return
    _reset_rate_limiters()
    ua = Client()
    _login_ephemeral("userA", ua)
    a = _db_uid_by_role("userA"); b = _db_uid_by_role("userB")
    if not (a and b):
        record("DM-9", SKIP, "测试账号未就绪")
        return
    _db_clear_blocks(a, b); _db_clear_follows(a, b); _db_clear_messages_between(a, b)
    _db_follow(a, b); _db_follow(b, a)
    _, _, _, token = ua.get_with_csrf("/")
    codes = []
    for i in range(3):
        c, _, _ = ua.post_with_csrf("/api/messages/send", {
            "receiver_id": str(b), "content": "DM9 friend %d" % i}, token)
        codes.append(c)
    ok = all(c == 200 for c in codes)
    record("DM-9", PASS if ok else FAIL,
           "好友连发3条状态码=%s（应 200/200/200）" % (codes,))


def test_dm_10_blocked_both_direction():
    """DM-10：A 拉黑 B → 双方都发不了消息"""
    if not (USER_A_PWD and USER_B_PWD):
        record("DM-10", SKIP, "临时测试账号未就绪")
        return
    _reset_rate_limiters()
    ua = Client(); ub = Client()
    _login_ephemeral("userA", ua)
    _login_ephemeral("userB", ub)
    a = _db_uid_by_role("userA"); b = _db_uid_by_role("userB")
    if not (a and b):
        record("DM-10", SKIP, "测试账号未就绪")
        return
    _db_clear_blocks(a, b); _db_clear_messages_between(a, b)
    _db_follow(a, b); _db_follow(b, a)
    _, _, _, tok_a = ua.get_with_csrf("/")
    _, _, _, tok_b = ub.get_with_csrf("/")
    rc, _, _ = ua.post_with_csrf("/user/%d/block" % b, {}, tok_a)
    c1, body1, _ = ua.post_with_csrf("/api/messages/send", {
        "receiver_id": str(b), "content": "DM10 A→B"}, tok_a)
    c2, body2, _ = ub.post_with_csrf("/api/messages/send", {
        "receiver_id": str(a), "content": "DM10 B→A"}, tok_b)
    ok = rc == 200 and c1 == 403 and c2 == 403
    record("DM-10", PASS if ok else FAIL,
           "拉黑=%s A发B=%s(%s) B发A=%s(%s)（应 200/403/403）"
           % (rc, c1, "已屏蔽" in body1, c2, "已屏蔽" in body2))


def test_dm_11_unfollow_downgrade():
    """DM-11：A 取消关注 B（好友→单向关注）→ A 无法再发消息"""
    if not (USER_A_PWD and USER_B_PWD):
        record("DM-11", SKIP, "临时测试账号未就绪")
        return
    _reset_rate_limiters()
    ua = Client()
    _login_ephemeral("userA", ua)
    a = _db_uid_by_role("userA"); b = _db_uid_by_role("userB")
    if not (a and b):
        record("DM-11", SKIP, "测试账号未就绪")
        return
    _db_clear_blocks(a, b); _db_clear_follows(a, b); _db_clear_messages_between(a, b)
    _db_follow(a, b); _db_follow(b, a)
    _, _, _, token = ua.get_with_csrf("/")
    c1, _, _ = ua.post_with_csrf("/api/messages/send", {
        "receiver_id": str(b), "content": "DM11 m1"}, token)
    rc, _, _ = ua.post_with_csrf("/user/%d/follow" % b, {"action": "unfollow"}, token)
    c2, body2, _ = ua.post_with_csrf("/api/messages/send", {
        "receiver_id": str(b), "content": "DM11 m2"}, token)
    ok = c1 == 200 and rc == 200 and c2 == 403
    record("DM-11", PASS if ok else FAIL,
           "好友发送=%s 取关=%s 取关后再发=%s 提示=%s（应 200/200/403）"
           % (c1, rc, c2, ("等待回复" in body2 or "关注" in body2)))


def test_block_01_blocked_profile_404():
    """BLOCK-1：A 拉黑 B → B 访问 A 主页返回 404"""
    if not (USER_A_PWD and USER_B_PWD):
        record("BLOCK-1", SKIP, "临时测试账号未就绪")
        return
    _reset_rate_limiters()
    ua = Client(); ub = Client()
    _login_ephemeral("userA", ua)
    _login_ephemeral("userB", ub)
    a = _db_uid_by_role("userA"); b = _db_uid_by_role("userB")
    if not (a and b):
        record("BLOCK-1", SKIP, "测试账号未就绪")
        return
    _db_clear_blocks(a, b)
    _, _, _, tok = ua.get_with_csrf("/")
    ua.post_with_csrf("/user/%d/block" % b, {}, tok)
    code, body, _ = ub.get("/user/%d" % a)
    ok = code == 404
    record("BLOCK-1", PASS if ok else FAIL,
           "被拉黑者访问主页 code=%s（应 404）" % code)


def test_block_02_follows_cleared():
    """BLOCK-2：A 拉黑 B → 双方关注关系被清除"""
    a = _db_uid_by_role("userA"); b = _db_uid_by_role("userB")
    if not (a and b):
        record("BLOCK-2", SKIP, "测试账号未就绪")
        return
    _sys_paths()
    from follow_service import block_user
    _db_clear_follows(a, b); _db_clear_blocks(a, b)
    _db_follow(a, b); _db_follow(b, a)
    ok, _ = block_user(a, b)
    cnt = _db_query(
        "SELECT COUNT(*) FROM user_follows WHERE (follower_id = ? AND followee_id = ?)"
        " OR (follower_id = ? AND followee_id = ?)", (a, b, b, a))
    ok2 = ok and cnt[0][0] == 0
    record("BLOCK-2", PASS if ok2 else FAIL,
           "拉黑=%s 残留关注=%s（应 True/0）" % (ok, cnt[0][0]))





def test_friend_03_own_friends_page():
    """FRIEND-3：本人访问 /user/<id>/friends → 200，页面含好友 Tab 内容"""
    if not (USER_A_PWD and USER_B_PWD):
        record("FRIEND-3", SKIP, "临时测试账号未就绪")
        return
    ua = Client()
    _login_ephemeral("userA", ua)
    a = _db_uid_by_role("userA")
    if not a:
        record("FRIEND-3", SKIP, "测试账号未就绪")
        return
    code, body, _ = ua.get("/user/%d/friends" % a)
    ok = code == 200 and "好友" in body
    record("FRIEND-3", PASS if ok else FAIL,
           "GET /user/%s/friends code=%s 含好友文案=%s（应 200/True）" % (a, code, "好友" in body))


def test_friend_04_others_friends_forbidden():
    """FRIEND-4：他人访问 /user/<id>/friends → 403（仅本人可见）"""
    if not (USER_A_PWD and USER_B_PWD):
        record("FRIEND-4", SKIP, "临时测试账号未就绪")
        return
    ua = Client(); ub = Client()
    _login_ephemeral("userA", ua)
    _login_ephemeral("userB", ub)
    a = _db_uid_by_role("userA")
    if not a:
        record("FRIEND-4", SKIP, "测试账号未就绪")
        return
    code, _, _ = ub.get("/user/%d/friends" % a)
    record("FRIEND-4", PASS if code == 403 else FAIL,
           "他人访问好友列表 code=%s（应 403）" % code)


def test_share_01_friend_receives_postcard():
    """SHARE-1：A 分享公开帖给好友 B → 200，B 收到带 post_id 的消息 + share 通知"""
    if not (USER_A_PWD and USER_B_PWD):
        record("SHARE-1", SKIP, "临时测试账号未就绪")
        return
    _reset_rate_limiters()
    ua = Client(); ub = Client()
    _login_ephemeral("userA", ua)
    _login_ephemeral("userB", ub)
    a = _db_uid_by_role("userA"); b = _db_uid_by_role("userB")
    if not (a and b):
        record("SHARE-1", SKIP, "测试账号未就绪")
        return
    _db_clear_blocks(a, b); _db_clear_follows(a, b); _db_clear_messages_between(a, b)
    _db_follow(a, b); _db_follow(b, a)
    title = "SHARE1-%d" % int(time.time())
    _, _, _, token = ua.get_with_csrf("/post/new")
    ua.post_with_csrf("/post/new", {"title": title, "content": "分享测试内容", "board_id": 1}, token)
    pid = _db_find_post_id_by_title(title)
    if not pid:
        record("SHARE-1", FAIL, "测试帖创建失败")
        return
    _, _, _, token = ua.get_with_csrf("/")
    code, body, _ = ua.post_with_csrf("/api/message/share", {
        "receiver_id": str(b), "post_id": str(pid)}, token)
    row = _db_query(
        "SELECT COUNT(*) FROM message WHERE sender_id=? AND receiver_id=? AND post_id=?",
        (a, b, pid))[0]
    n_share = _db_notification_count(b, "share")
    ok = code == 200 and row[0] == 1 and n_share >= 1
    record("SHARE-1", PASS if ok else FAIL,
           "分享 code=%s 落库=%s share通知=%s（应 200/1/≥1）" % (code, row[0], n_share))


def test_share_02_non_friend_rejected():
    """SHARE-2：A 分享给非好友 B → 403"""
    if not (USER_A_PWD and USER_B_PWD):
        record("SHARE-2", SKIP, "临时测试账号未就绪")
        return
    _reset_rate_limiters()
    ua = Client()
    _login_ephemeral("userA", ua)
    a = _db_uid_by_role("userA"); b = _db_uid_by_role("userB")
    if not (a and b):
        record("SHARE-2", SKIP, "测试账号未就绪")
        return
    _db_clear_blocks(a, b); _db_clear_follows(a, b)
    title = "SHARE2-%d" % int(time.time())
    _, _, _, token = ua.get_with_csrf("/post/new")
    ua.post_with_csrf("/post/new", {"title": title, "content": "分享测试内容", "board_id": 1}, token)
    pid = _db_find_post_id_by_title(title)
    if not pid:
        record("SHARE-2", FAIL, "测试帖创建失败")
        return
    _, _, _, token = ua.get_with_csrf("/")
    code, body, _ = ua.post_with_csrf("/api/message/share", {
        "receiver_id": str(b), "post_id": str(pid)}, token)
    msg = ""
    try:
        msg = json.loads(body).get("msg", "")
    except Exception:
        msg = body
    ok = code == 403 and "好友" in msg
    record("SHARE-2", PASS if ok else FAIL,
           "非好友分享 code=%s 提示=%s（应 403/含好友）" % (code, "好友" in msg))


def test_share_03_private_post_rejected():
    """SHARE-3：A 尝试分享私密帖给好友 B → 403"""
    if not (USER_A_PWD and USER_B_PWD):
        record("SHARE-3", SKIP, "临时测试账号未就绪")
        return
    _reset_rate_limiters()
    ua = Client()
    _login_ephemeral("userA", ua)
    a = _db_uid_by_role("userA"); b = _db_uid_by_role("userB")
    if not (a and b):
        record("SHARE-3", SKIP, "测试账号未就绪")
        return
    _db_clear_blocks(a, b); _db_clear_follows(a, b); _db_clear_messages_between(a, b)
    _db_follow(a, b); _db_follow(b, a)
    title = "SHARE3-%d" % int(time.time())
    _, _, _, token = ua.get_with_csrf("/post/new")
    ua.post_with_csrf("/post/new", {"title": title, "content": "私密分享测试", "board_id": 1, "is_private": "1"}, token)
    pid = _db_find_post_id_by_title(title)
    if not pid:
        record("SHARE-3", FAIL, "测试帖创建失败")
        return
    _, _, _, token = ua.get_with_csrf("/")
    code, body, _ = ua.post_with_csrf("/api/message/share", {
        "receiver_id": str(b), "post_id": str(pid)}, token)
    msg = ""
    try:
        msg = json.loads(body).get("msg", "")
    except Exception:
        msg = body
    ok = code == 403 and "私密" in msg
    record("SHARE-3", PASS if ok else FAIL,
           "私密帖分享 code=%s 提示=%s（应 403/含私密）" % (code, "私密" in msg))


def test_share_04_blocked_rejected():
    """SHARE-4：A 分享给已拉黑的 B → 403"""
    if not (USER_A_PWD and USER_B_PWD):
        record("SHARE-4", SKIP, "临时测试账号未就绪")
        return
    _reset_rate_limiters()
    ua = Client()
    _login_ephemeral("userA", ua)
    a = _db_uid_by_role("userA"); b = _db_uid_by_role("userB")
    if not (a and b):
        record("SHARE-4", SKIP, "测试账号未就绪")
        return
    _db_clear_blocks(a, b); _db_clear_follows(a, b); _db_clear_messages_between(a, b)
    _db_follow(a, b); _db_follow(b, a)
    title = "SHARE4-%d" % int(time.time())
    _, _, _, token = ua.get_with_csrf("/post/new")
    ua.post_with_csrf("/post/new", {"title": title, "content": "分享测试内容", "board_id": 1}, token)
    pid = _db_find_post_id_by_title(title)
    if not pid:
        record("SHARE-4", FAIL, "测试帖创建失败")
        return
    _sys_paths()
    from follow_service import block_user
    block_user(a, b)
    _, _, _, token = ua.get_with_csrf("/")
    code, body, _ = ua.post_with_csrf("/api/message/share", {
        "receiver_id": str(b), "post_id": str(pid)}, token)
    _db_clear_blocks(a, b)
    ok = code == 403
    record("SHARE-4", PASS if ok else FAIL,
           "拉黑后分享 code=%s（应 403）" % code)




def test_pick_01_unauth_rejected():
    """PICK-1：未登录调 /api/friends/list → 被拒（302/401/403）"""
    c = Client()
    code, _, _ = c.post("/api/friends/list", {"page": "1"})
    record("PICK-1", PASS if code in (302, 401, 403, 400) else FAIL,
           "未登录调好友分页 code=%s（应 302/401/403/400 被拒）" % code)


def test_pick_02_paged_friends():
    """PICK-2：登录后调 /api/friends/list → 200，仅返回本人好友，分页参数生效"""
    if not (USER_A_PWD and USER_B_PWD):
        record("PICK-2", SKIP, "临时测试账号未就绪")
        return
    ua = Client()
    _login_ephemeral("userA", ua)
    a = _db_uid_by_role("userA"); b = _db_uid_by_role("userB")
    if not (a and b):
        record("PICK-2", SKIP, "测试账号未就绪")
        return
    _db_clear_blocks(a, b); _db_clear_follows(a, b)
    _db_follow(a, b); _db_follow(b, a)
    _, _, _, token = ua.get_with_csrf("/")
    code, body, _ = ua.post_with_csrf("/api/friends/list", {
        "page": "1", "page_size": "20"}, token)
    ok = code == 200
    d = {}
    try:
        d = json.loads(body)
    except Exception:
        ok = False
    if ok:
        ids = [f.get("user_id") for f in (d.get("friends") or [])]
        ok = d.get("ok") is True and b in ids and a not in ids and len(ids) <= 20
    record("PICK-2", PASS if ok else FAIL,
           "好友分页 code=%s total=%s 含B=%s 不含自己=%s（应 200/≥1/True/True）"
           % (code, d.get("total"), (b in ids) if ok else None, (a not in ids) if ok else None))


def test_share_05_nonfriend_no_rate_consume():
    """SHARE-5：拦截顺序——非好友分享不消耗限速配额（限速在好友检查之后）。
    非好友连发 25 次 → 全部 403 且无 429（若限速前置会被无效请求刷爆）。"""
    if not (USER_A_PWD and USER_B_PWD):
        record("SHARE-5", SKIP, "临时测试账号未就绪")
        return
    _reset_rate_limiters()
    ua = Client()
    _login_ephemeral("userA", ua)
    a = _db_uid_by_role("userA"); b = _db_uid_by_role("userB")
    if not (a and b):
        record("SHARE-5", SKIP, "测试账号未就绪")
        return
    _db_clear_blocks(a, b); _db_clear_follows(a, b)
    title = "SHARE5-%d" % int(time.time())
    _, _, _, token = ua.get_with_csrf("/post/new")
    ua.post_with_csrf("/post/new", {"title": title, "content": "顺序测试", "board_id": 1}, token)
    pid = _db_find_post_id_by_title(title)
    if not pid:
        record("SHARE-5", FAIL, "测试帖创建失败")
        return
    _, _, _, token = ua.get_with_csrf("/")
    codes = []
    for i in range(25):
        code, _, _ = ua.post_with_csrf("/api/message/share", {
            "receiver_id": str(b), "post_id": str(pid)}, token)
        codes.append(code)
    ok = set(codes) == {403} and 429 not in codes
    record("SHARE-5", PASS if ok else FAIL,
           "非好友25次状态码集合=%s（应全403且无429）" % sorted(set(codes)))


def test_share_06_deleted_post_placeholder():
    """SHARE-6：分享后帖子被删除 → 对话页显示"该帖子已删除"占位（无死链）"""
    if not (USER_A_PWD and USER_B_PWD):
        record("SHARE-6", SKIP, "临时测试账号未就绪")
        return
    _reset_rate_limiters()
    ua = Client(); ub = Client()
    _login_ephemeral("userA", ua)
    _login_ephemeral("userB", ub)
    a = _db_uid_by_role("userA"); b = _db_uid_by_role("userB")
    if not (a and b):
        record("SHARE-6", SKIP, "测试账号未就绪")
        return
    _db_clear_blocks(a, b); _db_clear_follows(a, b); _db_clear_messages_between(a, b)
    _db_follow(a, b); _db_follow(b, a)
    title = "SHARE6-%d" % int(time.time())
    _, _, _, token = ua.get_with_csrf("/post/new")
    ua.post_with_csrf("/post/new", {"title": title, "content": "删帖占位测试", "board_id": 1}, token)
    pid = _db_find_post_id_by_title(title)
    if not pid:
        record("SHARE-6", FAIL, "测试帖创建失败")
        return
    _, _, _, token = ua.get_with_csrf("/")
    code, _, _ = ua.post_with_csrf("/api/message/share", {
        "receiver_id": str(b), "post_id": str(pid)}, token)
    if code != 200:
        record("SHARE-6", FAIL, "分享失败 code=%s" % code)
        return
    # 硬删帖子（测试环境专用，模拟软删/硬删后 get_post_detail 返回 None）
    _db_exec("DELETE FROM post WHERE post_id = ?", (pid,))
    code2, body2, _ = ub.get("/messages/%d" % a)
    ok = code2 == 200 and "该帖子已删除" in body2
    record("SHARE-6", PASS if ok else FAIL,
           "删帖后对话页 code=%s 含占位=%s（应 200/True）" % (code2, "该帖子已删除" in body2))



def _sio_connect_listener(cookies):
    """python-socketio 客户端带 cookie 连接并注册 new_message 监听。
    返回 (client, payloads, ev)；调用方发送后需 ev.wait(timeout)。"""
    import socketio as _sio
    import gevent as _gevent
    payloads = []
    ev = _gevent.event.Event()
    client = _sio.Client()
    try:
        client.connect(BASE_URL, headers={"Cookie": "; ".join("%s=%s" % kv for kv in cookies.items())},
                       transports=["websocket"], wait_timeout=8)
    except Exception as e:
        print("  [sio] connect fail:", e)
        return None, [], ev
    @client.on("new_message")
    def _on(d):
        payloads.append(d)
        ev.set()
    return client, payloads, ev


def test_dm_14_realtime_new_message():
    """DM-14：python-socketio 模拟 B 登录监听，A 发普通私信 → B 3 秒内收到 new_message（payload 含 message_id）"""
    if not (USER_A_PWD and USER_B_PWD):
        record("DM-14", SKIP, "临时测试账号未就绪")
        return
    _reset_rate_limiters()
    ua = Client(); ub = Client()
    _login_ephemeral("userA", ua)
    _login_ephemeral("userB", ub)
    a = _db_uid_by_role("userA"); b = _db_uid_by_role("userB")
    if not (a and b):
        record("DM-14", SKIP, "测试账号未就绪")
        return
    _db_clear_blocks(a, b); _db_clear_follows(a, b); _db_clear_messages_between(a, b)
    _db_follow(a, b); _db_follow(b, a)
    sio, payloads, ev = _sio_connect_listener(ub.cookies)
    if sio is None:
        record("DM-14", FAIL, "B socket 连接失败")
        return
    # 监听建立后 A 发送
    _, _, _, token = ua.get_with_csrf("/")
    code, body, _ = ua.post_with_csrf("/api/messages/send", {
        "receiver_id": str(b), "content": "DM14-realtime-%d" % int(time.time())}, token)
    if code != 200:
        record("DM-14", FAIL, "A 发送失败 code=%s" % code)
        sio.disconnect()
        return
    got = ev.wait(timeout=3.5)
    ok = False
    payload = None
    for payload in payloads:
        if payload.get("message_id"):
            ok = True
            break
    try:
        sio.disconnect()
    except Exception:
        pass
    record("DM-14", PASS if ok else FAIL,
           "收到事件=%s payload含message_id=%s" % (got, bool(payload and payload.get("message_id"))))


def test_dm_15_realtime_share_post():
    """DM-15：A 分享帖子给 B → B 3 秒内收到 new_message（payload 含 post_id）"""
    if not (USER_A_PWD and USER_B_PWD):
        record("DM-15", SKIP, "临时测试账号未就绪")
        return
    _reset_rate_limiters()
    ua = Client(); ub = Client()
    _login_ephemeral("userA", ua)
    _login_ephemeral("userB", ub)
    a = _db_uid_by_role("userA"); b = _db_uid_by_role("userB")
    if not (a and b):
        record("DM-15", SKIP, "测试账号未就绪")
        return
    _db_clear_blocks(a, b); _db_clear_follows(a, b); _db_clear_messages_between(a, b)
    _db_follow(a, b); _db_follow(b, a)
    title = "DM15-%d" % int(time.time())
    _, _, _, token = ua.get_with_csrf("/post/new")
    ua.post_with_csrf("/post/new", {"title": title, "content": "分享推送测试", "board_id": 1}, token)
    pid = _db_find_post_id_by_title(title)
    if not pid:
        record("DM-15", FAIL, "测试帖创建失败")
        return
    sio, payloads, ev = _sio_connect_listener(ub.cookies)
    if sio is None:
        record("DM-15", FAIL, "B socket 连接失败")
        return
    _, _, _, token = ua.get_with_csrf("/")
    code, body, _ = ua.post_with_csrf("/api/message/share", {
        "receiver_id": str(b), "post_id": str(pid)}, token)
    if code != 200:
        record("DM-15", FAIL, "分享失败 code=%s" % code)
        sio.disconnect()
        return
    got = ev.wait(timeout=3.5)
    ok = False
    payload = None
    for payload in payloads:
        if payload.get("post_id") == pid:
            ok = True
            break
    try:
        sio.disconnect()
    except Exception:
        pass
    record("DM-15", PASS if ok else FAIL,
           "收到事件=%s payload含post_id=%s" % (got, bool(payload and payload.get("post_id") == pid)))



def test_dm_16_system_tab_excludes_share():
    """DM-16：分享帖子后，/messages?tab=system 不含分享通知文本（私信 Tab 独立展示）"""
    if not (USER_A_PWD and USER_B_PWD):
        record("DM-16", SKIP, "临时测试账号未就绪")
        return
    _reset_rate_limiters()
    ua = Client(); ub = Client()
    _login_ephemeral("userA", ua)
    _login_ephemeral("userB", ub)
    a = _db_uid_by_role("userA"); b = _db_uid_by_role("userB")
    if not (a and b):
        record("DM-16", SKIP, "测试账号未就绪")
        return
    _db_clear_blocks(a, b); _db_clear_follows(a, b); _db_clear_messages_between(a, b)
    _db_follow(a, b); _db_follow(b, a)
    title = "DM16-%d" % int(time.time())
    _, _, _, token = ua.get_with_csrf("/post/new")
    ua.post_with_csrf("/post/new", {"title": title, "content": "排除分享测试", "board_id": 1}, token)
    pid = _db_find_post_id_by_title(title)
    if not pid:
        record("DM-16", FAIL, "测试帖创建失败")
        return
    _, _, _, token = ua.get_with_csrf("/")
    code, _, _ = ua.post_with_csrf("/api/message/share", {
        "receiver_id": str(b), "post_id": str(pid)}, token)
    if code != 200:
        record("DM-16", FAIL, "分享失败 code=%s" % code)
        return
    code2, body2, _ = ub.get("/messages?tab=system")
    bad = "分享帖子" in body2 or "给你分享了一篇帖子" in body2
    record("DM-16", PASS if (code2 == 200 and not bad) else FAIL,
           "system Tab code=%s 含分享文本=%s（应 200/False）" % (code2, bad))


def test_dm_17_system_tab_excludes_message():
    """DM-17：普通私信后，/messages?tab=system 不含私信通知文本（私信 Tab 独立展示）"""
    if not (USER_A_PWD and USER_B_PWD):
        record("DM-17", SKIP, "临时测试账号未就绪")
        return
    _reset_rate_limiters()
    ua = Client(); ub = Client()
    _login_ephemeral("userA", ua)
    _login_ephemeral("userB", ub)
    a = _db_uid_by_role("userA"); b = _db_uid_by_role("userB")
    if not (a and b):
        record("DM-17", SKIP, "测试账号未就绪")
        return
    _db_clear_blocks(a, b); _db_clear_follows(a, b); _db_clear_messages_between(a, b)
    _db_follow(a, b); _db_follow(b, a)
    _, _, _, token = ua.get_with_csrf("/")
    code, _, _ = ua.post_with_csrf("/api/messages/send", {
        "receiver_id": str(b), "content": "DM17-排除私信-%d" % int(time.time())}, token)
    if code != 200:
        record("DM-17", FAIL, "私信发送失败 code=%s" % code)
        return
    code2, body2, _ = ub.get("/messages?tab=system")
    bad = "给你发来一条私信" in body2 or "DM17-" in body2
    record("DM-17", PASS if (code2 == 200 and not bad) else FAIL,
           "system Tab code=%s 含私信文本=%s（应 200/False）" % (code2, bad))


# ============================================================
# CL：评论点赞（comment_like）
# ============================================================
def _db_comment_like_count(comment_id):
    rows = _db_query("SELECT COUNT(*) FROM comment_like WHERE comment_id = ?", (comment_id,))
    return rows[0][0] if rows else 0


def _db_clear_comment_likes(comment_id):
    _db_exec("DELETE FROM comment_like WHERE comment_id = ?", (comment_id,))


def _cl_fixture():
    """CL 夹具：userA 发公开帖，userB 发评论；返回 (pid, cid)；失败返回 (None, None)"""
    ua, ub = Client(), Client()
    _login_ephemeral("userA", ua)
    _login_ephemeral("userB", ub)
    title = "CL-FIX-%d" % int(time.time())
    _, _, _, token = ua.get_with_csrf("/post/new")
    ua.post_with_csrf("/post/new", {"title": title, "content": "CL夹具正文", "board_id": "1"}, token)
    pid = _db_find_post_id_by_title(title)
    if not pid:
        return None, None
    _, _, _, token = ub.get_with_csrf("/post/%d" % pid)
    code, body, _ = ub.post_with_csrf("/post/%d/comment" % pid, {"content": "CL评论-" + title}, token)
    cid = None
    try:
        d = json.loads(body)
        cid = (d.get("comment") or {}).get("comment_id")
    except Exception:
        pass
    if not cid:
        rows = _db_query("SELECT comment_id FROM comment WHERE post_id = ? ORDER BY comment_id DESC LIMIT 1", (pid,))
        cid = rows[0][0] if rows else None
    return pid, cid


def _cl_cleanup(pid, cid):
    """清理夹具：删评论点赞、删帖子（评论随 FK 级联删除）"""
    if cid:
        _db_clear_comment_likes(cid)
    if pid:
        _db_exec("DELETE FROM post WHERE post_id = ?", (pid,))


def test_cl_01_unauth_like_rejected():
    """CL-1：未登录点赞评论 → 被拒（400 CSRF / 302 / 403）"""
    if not (USER_A_PWD and USER_B_PWD):
        record("CL-1", SKIP, "临时测试账号未就绪")
        return
    pid, cid = _cl_fixture()
    if not cid:
        record("CL-1", FAIL, "夹具创建失败")
        return
    c = Client()
    code, _, _ = c.post_with_csrf("/api/comment/%d/like" % cid, {}, None)
    ok = code in (302, 400, 403)
    record("CL-1", PASS if ok else FAIL, "未登录点赞 -> %s（被拒）" % code)
    _cl_cleanup(pid, cid)


def test_cl_02_like_success_notify():
    """CL-2：userA 点赞 userB 的评论 → 数+1，userB 收到 comment_like 通知"""
    if not (USER_A_PWD and USER_B_PWD):
        record("CL-2", SKIP, "临时测试账号未就绪")
        return
    pid, cid = _cl_fixture()
    if not cid:
        record("CL-2", FAIL, "夹具创建失败")
        return
    ua = Client()
    _login_ephemeral("userA", ua)
    uid_b = _db_uid_by_role("userB")
    _db_clear_notifications(uid_b)
    _, _, _, token = ua.get_with_csrf("/post/%d" % pid)
    code, body, _ = ua.post_with_csrf("/api/comment/%d/like" % cid, {}, token)
    d = json.loads(body)
    cnt = _db_comment_like_count(cid)
    n = _db_notification_count(uid_b, "comment_like")
    ok = (code == 200 and d.get("ok") is True and d.get("liked") is True
          and cnt == 1 and n >= 1)
    record("CL-2", PASS if ok else FAIL,
           "code=%s ok=%s liked=%s 点赞数=%s comment_like通知=%s" % (code, d.get("ok"), d.get("liked"), cnt, n))
    _db_clear_notifications(uid_b)
    _cl_cleanup(pid, cid)


def test_cl_03_toggle_cancel():
    """CL-3：重复点赞 → 取消点赞，数-1"""
    if not (USER_A_PWD and USER_B_PWD):
        record("CL-3", SKIP, "临时测试账号未就绪")
        return
    pid, cid = _cl_fixture()
    if not cid:
        record("CL-3", FAIL, "夹具创建失败")
        return
    ua = Client()
    _login_ephemeral("userA", ua)
    _, _, _, token = ua.get_with_csrf("/post/%d" % pid)
    code1, body1, _ = ua.post_with_csrf("/api/comment/%d/like" % cid, {}, token)
    d1 = json.loads(body1)
    code2, body2, _ = ua.post_with_csrf("/api/comment/%d/like" % cid, {}, token)
    d2 = json.loads(body2)
    cnt = _db_comment_like_count(cid)
    ok = (code1 == 200 and d1.get("liked") is True and d1.get("count") == 1
          and code2 == 200 and d2.get("liked") is False and d2.get("count") == 0 and cnt == 0)
    record("CL-3", PASS if ok else FAIL,
           "第一次 liked=%s/%s 第二次 liked=%s/%s DB计数=%s" % (d1.get("liked"), d1.get("count"), d2.get("liked"), d2.get("count"), cnt))
    _cl_cleanup(pid, cid)


def test_cl_04_self_like_rejected():
    """CL-4：自己赞自己的评论 → 400"""
    if not (USER_A_PWD and USER_B_PWD):
        record("CL-4", SKIP, "临时测试账号未就绪")
        return
    pid, cid = _cl_fixture()
    if not cid:
        record("CL-4", FAIL, "夹具创建失败")
        return
    ub = Client()
    _login_ephemeral("userB", ub)   # userB 是评论作者
    _, _, _, token = ub.get_with_csrf("/post/%d" % pid)
    code, body, _ = ub.post_with_csrf("/api/comment/%d/like" % cid, {}, token)
    cnt = _db_comment_like_count(cid)
    ok = code == 400 and cnt == 0
    record("CL-4", PASS if ok else FAIL, "自己赞自己 -> %s DB计数=%s（应 400/0）" % (code, cnt))
    _cl_cleanup(pid, cid)


def test_cl_05_delete_cascade():
    """CL-5：删除评论后 comment_like 记录同步删除（DB 断言）"""
    if not (USER_A_PWD and USER_B_PWD):
        record("CL-5", SKIP, "临时测试账号未就绪")
        return
    pid, cid = _cl_fixture()
    if not cid:
        record("CL-5", FAIL, "夹具创建失败")
        return
    ua = Client()
    _login_ephemeral("userA", ua)
    _, _, _, token = ua.get_with_csrf("/post/%d" % pid)
    ua.post_with_csrf("/api/comment/%d/like" % cid, {}, token)
    if _db_comment_like_count(cid) != 1:
        record("CL-5", FAIL, "点赞夹具失败")
        _cl_cleanup(pid, cid)
        return
    import sys as _sys
    _sys.path.insert(0, os.path.join(BASE, "backend", "services"))
    from admin_service import delete_comment
    ok_del, msg = delete_comment(cid)
    cnt_after = _db_comment_like_count(cid)
    ok = ok_del and cnt_after == 0
    record("CL-5", PASS if ok else FAIL,
           "删除评论 ok=%s msg=%s 删除后 comment_like=%s（应 0）" % (ok_del, msg, cnt_after))
    _cl_cleanup(pid, None)   # 评论已删，仅清理帖子


def test_cl_06_blocked_like_rejected():
    """CL-6：userB 拉黑 userA → userA 点赞 userB 的评论被拒（403）"""
    if not (USER_A_PWD and USER_B_PWD):
        record("CL-6", SKIP, "临时测试账号未就绪")
        return
    pid, cid = _cl_fixture()
    if not cid:
        record("CL-6", FAIL, "夹具创建失败")
        return
    uid_a = _db_uid_by_role("userA")
    uid_b = _db_uid_by_role("userB")
    _db_clear_blocks(uid_b, uid_a)
    _db_exec("INSERT INTO user_blocks (blocker_id, blocked_id) VALUES (?, ?)", (uid_b, uid_a))
    ua = Client()
    _login_ephemeral("userA", ua)
    _, _, _, token = ua.get_with_csrf("/post/%d" % pid)
    code, body, _ = ua.post_with_csrf("/api/comment/%d/like" % cid, {}, token)
    cnt = _db_comment_like_count(cid)
    ok = code == 403 and cnt == 0
    record("CL-6", PASS if ok else FAIL, "被拉黑点赞 -> %s DB计数=%s（应 403/0）" % (code, cnt))
    _db_clear_blocks(uid_b, uid_a)
    _cl_cleanup(pid, cid)


# ============================================================
# 主入口
# ============================================================
if __name__ == "__main__":
    print("== 授权安全评审自动化测试 ==")
    print("目标实例: %s" % BASE_URL)
    _assert_test_safety()          # 安全阀：拒绝真实账号
    _ensure_ephemeral_accounts()   # 创建临时测试账号（不依赖真实用户密码）
    try:
        test_s1_parameterized_sql()
        test_s2_template_xss_surface()
        c = Client()
        test_d1_unauthenticated_access_control()
        test_d2_login_sqli_behavior()
        test_d3_user_enumeration()
        test_d4_security_headers()
        test_authz_matrix()
        test_u1_fake_image_rejected()
        test_u2_oversized_image_scaled()
        test_priv07_private_image_acl()
        test_pwd_01_requires_login()
        test_pwd_02_old_password_wrong()
        test_pwd_03_new_password_invalid()
        test_pwd_04_success_flow()
        test_pwd_05_rate_limited()
        test_pwd_06_audit_logged()
        test_md_01_post_xss_filtered()
        test_md_02_preview_filtered()
        test_ment_01_nonexistent_skipped()
        test_ment_02_existing_notified()
        test_ment_03_comment_mention()
        test_ment_04_duplicate_same_user_single()
        test_ment_05_limit_10()
        test_ment_06_edit_trigger_dedup()
        test_ment_07_notify_link_jump()
        test_ban_01_unauthenticated_blocked()
        test_ban_02_normal_user_blocked()
        test_ban_03_banned_login_rejected()
        test_ban_04_banned_session_invalidated()
        test_ban_05_unban_restores()
        test_ban_06_audit_logged()
        test_sw_01_post_blocked()
        test_sw_02_comment_blocked()
        test_sw_03_profile_blocked()
        test_sw_04_variant_blocked()
        test_rpt_01_unauth_report_rejected()
        test_rpt_02_report_submitted()
        test_rpt_03_duplicate_report_rejected()
        test_rpt_04_user_cannot_view_queue()
        test_rpt_05_admin_handle_report()
        test_rpt_06_appeal_flow()
        test_rpt_07_appeal_anti_abuse()
        test_rpt_08_queue_pagination()
        test_rpt_09_unban_notification_badge()
        test_rpt_10_admin_notified_on_submit()
        test_rpt_11_admin_direct_unban_notify()
        test_rpt_12_admin_socketio_alert()
        test_adm_04_private_post_review_masked()
        test_fol_01_unauth_follow_rejected()
        test_fol_02_follow_success_notify()
        test_fol_03_duplicate_follow_rejected()
        test_fol_04_unfollow_success()
        test_fol_05_self_follow_rejected()
        test_fol_06_rate_limited_429()
        test_fol_07_follow_notify_link_jump()
        test_ai_01_unauth_rejected()
        test_ai_02_success_filtered()
        test_ai_03_rate_limited()
        test_ai_04_invalid_json_graceful()
        test_ai_05_reply_success()
        test_ai_06_human_comment_no_reply()
        test_ai_07_fail_no_crash_retryable()
        test_ai_08_admin_revoke()
        test_ai_09_revoked_not_replied_again()
        test_sem_01_semantic_match()
        test_sem_02_private_excluded()
        test_sem_03_short_query_degrades()
        test_sem_04_rate_limited()
        test_sem_05_vector_unavailable_degrade()
        test_sem_06_rebuild_process_locked()
        test_dm_01_unauth_send_rejected()
        test_dm_02_send_notify()
        test_dm_03_third_party_blocked()
        test_dm_04_inbox_shows()
        test_dm_05_rate_limited()
        test_dm_06_xss_filtered()
        test_dm_12_thread_avatars()
        test_nav_01_messages_center_tabs()
        test_nav_02_unauth_messages_redirect()
        test_friend_01_mutual_is_friend()
        test_friend_02_oneway_not_friend()
        test_dm_07_oneway_limit_one()
        test_dm_08_reply_unlocks_next()
        test_dm_13_reply_without_follow()
        test_dm_09_friends_unlimited()
        test_dm_10_blocked_both_direction()
        test_dm_11_unfollow_downgrade()
        test_block_01_blocked_profile_404()
        test_block_02_follows_cleared()
        test_friend_03_own_friends_page()
        test_friend_04_others_friends_forbidden()
        test_share_01_friend_receives_postcard()
        test_share_02_non_friend_rejected()
        test_share_03_private_post_rejected()
        test_share_04_blocked_rejected()
        test_pick_01_unauth_rejected()
        test_pick_02_paged_friends()
        test_share_05_nonfriend_no_rate_consume()
        test_share_06_deleted_post_placeholder()
        test_dm_14_realtime_new_message()
        test_dm_15_realtime_share_post()
        test_dm_16_system_tab_excludes_share()
        test_dm_17_system_tab_excludes_message()
        test_cl_01_unauth_like_rejected()
        test_cl_02_like_success_notify()
        test_cl_03_toggle_cancel()
        test_cl_04_self_like_rejected()
        test_cl_05_delete_cascade()
        test_cl_06_blocked_like_rejected()
    finally:
        _cleanup_ephemeral_accounts()
    print("\n== 汇总 ==")
    n_pass = sum(1 for _, s, _ in _report if s == PASS)
    n_fail = sum(1 for _, s, _ in _report if s == FAIL)
    n_skip = sum(1 for _, s, _ in _report if s == SKIP)
    n_info = sum(1 for _, s, _ in _report if s == INFO)
    print("PASS=%d FAIL=%d SKIP=%d INFO=%d" % (n_pass, n_fail, n_skip, n_info))
