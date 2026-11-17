# -*- coding: utf-8 -*-
"""
第四章系统测试 - 真实截图采集脚本（Playwright 无头浏览器）
覆盖：4.4.1 登录、4.4.2 注册、首页浏览；4.5 安全维度部分页面/代码截图
运行：venv\Scripts\python.exe scripts\capture_shots.py
"""
import os
import random
import string
from playwright.sync_api import sync_playwright

BASE_URL = "http://127.0.0.1:5000"
SHOT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "screenshots")
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


def rand_user(prefix="sec_doc_"):
    return prefix + "".join(random.choices(string.ascii_lowercase + string.digits, k=8))


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1280, "height": 800}, locale="zh-CN")
        page = ctx.new_page()

        # ===== 4.4.1 登录模块 =====
        page.goto(BASE_URL + "/login", wait_until="networkidle")
        shot(page, "f_login_page.png")

        # uc_4.01 空提交（浏览器 required 校验气泡）
        page.goto(BASE_URL + "/login", wait_until="networkidle")
        page.click("button[type=submit]")
        page.wait_for_timeout(800)
        shot(page, "f_login_empty.png")

        # uc_4.02 不存在的用户名
        page.goto(BASE_URL + "/login", wait_until="networkidle")
        page.fill("input[name=username]", "no_such_user_zzz")
        page.fill("input[name=password]", "wrongpass")
        page.click("button[type=submit]")
        page.wait_for_timeout(1000)
        shot(page, "f_login_user_not_exist.png")

        # uc_4.03 正确用户名+错误密码
        page.goto(BASE_URL + "/login", wait_until="networkidle")
        page.fill("input[name=username]", ADMIN_U)
        page.fill("input[name=password]", "wrongpass123")
        page.click("button[type=submit]")
        page.wait_for_timeout(1000)
        shot(page, "f_login_wrong_pwd.png")

        # uc_4.04 正确登录 → 首页
        page.goto(BASE_URL + "/login", wait_until="networkidle")
        page.fill("input[name=username]", ADMIN_U)
        page.fill("input[name=password]", ADMIN_P)
        page.click("button[type=submit]")
        page.wait_for_url("**/", timeout=10000)
        page.wait_for_timeout(1500)
        shot(page, "f_login_success_home.png")

        # 首页帖子列表（uc_4.14 浏览帖子）
        page.goto(BASE_URL + "/", wait_until="networkidle")
        page.wait_for_timeout(1500)
        shot(page, "f_home_posts.png")

        # ===== 4.4.2 注册模块 =====
        page.goto(BASE_URL + "/register", wait_until="networkidle")
        shot(page, "f_register_page.png")

        # uc_4.08 空提交
        page.goto(BASE_URL + "/register", wait_until="networkidle")
        page.click("button[type=submit]")
        page.wait_for_timeout(800)
        shot(page, "f_register_empty.png")

        # uc_4.09 用户名过短
        page.goto(BASE_URL + "/register", wait_until="networkidle")
        page.fill("input[name=username]", "a")
        page.fill("input[name=password]", "abcdef")
        page.click("button[type=submit]")
        page.wait_for_timeout(1000)
        shot(page, "f_register_short_username.png")

        # uc_4.10 密码过短
        page.goto(BASE_URL + "/register", wait_until="networkidle")
        page.fill("input[name=username]", rand_user())
        page.fill("input[name=password]", "12345")
        page.click("button[type=submit]")
        page.wait_for_timeout(1000)
        shot(page, "f_register_short_pwd.png")

        # uc_4.11 用户名已存在
        page.goto(BASE_URL + "/register", wait_until="networkidle")
        page.fill("input[name=username]", ADMIN_U)
        page.fill("input[name=password]", "abc12345")
        page.click("button[type=submit]")
        page.wait_for_timeout(1000)
        shot(page, "f_register_dup_user.png")

        # uc_4.12 注册成功 → 跳转登录页
        page.goto(BASE_URL + "/register", wait_until="networkidle")
        page.fill("input[name=username]", rand_user())
        page.fill("input[name=password]", "abc12345")
        page.fill("input[name=nickname]", "文档测试用户")
        page.click("button[type=submit]")
        page.wait_for_url("**/login", timeout=10000)
        page.wait_for_timeout(1000)
        shot(page, "f_register_success_login.png")

        browser.close()
    print("ALL SHOTS DONE ->", SHOT_DIR)


if __name__ == "__main__":
    main()
