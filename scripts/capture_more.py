# -*- coding: utf-8 -*-
"""
第四章系统测试 - 补充截图（页面级 + 安全代码级）
页面：发帖、帖子详情、我的收藏、消息中心、管理后台、搜索
代码：SQL参数化、PBKDF2、CSRF、安全响应头（HTML 包装真实代码段 → 无头截图）
"""
import os
import html as html_mod
from playwright.sync_api import sync_playwright

BASE_URL = "http://127.0.0.1:5000"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHOT_DIR = os.path.join(ROOT, "screenshots")
os.makedirs(SHOT_DIR, exist_ok=True)

# 管理员账号从环境变量读取，禁止写死在脚本里（截图前先设置 SHOT_ADMIN_PASSWORD）
ADMIN_U = os.environ.get("SHOT_ADMIN_USER", "fly")
ADMIN_P = os.environ.get("SHOT_ADMIN_PASSWORD", "")
if not ADMIN_P:
    raise SystemExit("请先设置环境变量 SHOT_ADMIN_PASSWORD（论坛管理员登录口令）后再运行本脚本")


def shot(page, name):
    path = os.path.join(SHOT_DIR, name)
    page.screenshot(path=path, full_page=False)
    print("saved:", name)


def code_html(title, code, start_line=1):
    lines = code.splitlines()
    body = []
    for i, ln in enumerate(lines, start=start_line):
        body.append(
            '<div style="display:flex;font-family:Consolas,monospace;font-size:14px;line-height:1.5;">'
            '<span style="width:60px;color:#888;text-align:right;padding-right:12px;user-select:none;">%d</span>'
            '<span style="white-space:pre-wrap;color:#111;">%s</span></div>' % (i, html_mod.escape(ln))
        )
    return (
        '<html><head><meta charset="utf-8"><title>%s</title></head>'
        '<body style="margin:0;background:#fff;padding:24px;">'
        '<div style="font-family:Microsoft YaHei,sans-serif;font-size:16px;font-weight:bold;color:#0b57d0;'
        'padding-bottom:10px;border-bottom:2px solid #eee;margin-bottom:14px;">%s</div>'
        '%s</body></html>' % (html_mod.escape(title), html_mod.escape(title), "".join(body))
    )


def read_code(rel_path):
    with open(os.path.join(ROOT, rel_path), "r", encoding="utf-8") as f:
        return f.read()


def take_code_shot(page, title, rel_path, start, end, out_name):
    code = "\n".join(read_code(rel_path).splitlines()[start - 1:end])
    html_doc = code_html(title, code, start)
    tmp = os.path.join(SHOT_DIR, "_tmp_code.html")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(html_doc)
    page.goto("file:///" + tmp.replace("\\", "/"), wait_until="load")
    page.wait_for_timeout(400)
    shot(page, out_name)


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1280, "height": 800}, locale="zh-CN")
        page = ctx.new_page()

        # ---- 登录（后续页面操作前） ----
        page.goto(BASE_URL + "/login", wait_until="networkidle")
        page.fill("input[name=username]", ADMIN_U)
        page.fill("input[name=password]", ADMIN_P)
        page.click("button[type=submit]")
        page.wait_for_url("**/", timeout=10000)
        page.wait_for_timeout(1200)

        # ---- 页面级截图 ----
        # 发帖页
        page.goto(BASE_URL + "/post/new", wait_until="networkidle")
        page.wait_for_timeout(1200)
        shot(page, "f_post_new.png")

        # 帖子详情页（取首页第一个帖子链接）
        page.goto(BASE_URL + "/", wait_until="networkidle")
        page.wait_for_timeout(800)
        first = page.query_selector("a.post-title")
        href = first.get_attribute("href") if first else "/post/1"
        page.goto(BASE_URL + href, wait_until="networkidle")
        page.wait_for_timeout(1200)
        shot(page, "f_post_detail.png")

        # 我的收藏
        page.goto(BASE_URL + "/favorites", wait_until="networkidle")
        page.wait_for_timeout(1200)
        shot(page, "f_favorites.png")

        # 消息中心
        page.goto(BASE_URL + "/messages", wait_until="networkidle")
        page.wait_for_timeout(1200)
        shot(page, "f_messages.png")

        # 管理后台
        page.goto(BASE_URL + "/admin", wait_until="networkidle")
        page.wait_for_timeout(1200)
        shot(page, "f_admin.png")

        # 搜索页
        page.goto(BASE_URL + "/search?q=python", wait_until="networkidle")
        page.wait_for_timeout(1200)
        shot(page, "f_search.png")

        # ---- 代码级截图（4.5 安全维度证据） ----
        # 1) SQL 注入拦截：login 参数化查询
        take_code_shot(
            page,
            "backend/services/auth.py · login() 参数化查询（SQL 注入拦截证据）",
            "backend/services/auth.py", 129, 139, "s_sqli_parameterized.png",
        )
        # 2) 密码加密：PBKDF2 哈希
        take_code_shot(
            page,
            "backend/services/auth.py · hash_password() / verify_password()（PBKDF2-SHA256）",
            "backend/services/auth.py", 11, 46, "s_auth_pbkdf2.png",
        )
        # 3) CSRF 全局校验
        take_code_shot(
            page,
            "web/app.py · csrf_protect()（POST 全局 CSRF 校验）",
            "web/app.py", 294, 301, "s_csrf_protect.png",
        )
        # 4) 安全响应头 / CSP
        take_code_shot(
            page,
            "web/app.py · set_security_headers()（安全响应头基线 + CSP）",
            "web/app.py", 304, 317, "s_security_headers.png",
        )

        browser.close()
    print("MORE SHOTS DONE ->", SHOT_DIR)


if __name__ == "__main__":
    main()
