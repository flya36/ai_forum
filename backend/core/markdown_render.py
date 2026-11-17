# -*- coding: utf-8 -*-
"""Markdown 渲染与 XSS 白名单过滤（backend/core/markdown_render.py）

设计原则：
- 入库一律保存原始 Markdown 文本（new_post/edit_post/add_comment 原样存储，便于再编辑）；
- 展示时再渲染：markdown.markdown() 转 HTML -> bleach.clean() 白名单过滤；
- 禁止 <script>、事件属性（onerror 等）、javascript:/data: 等危险协议与标签；
- 服务端渲染结果是唯一可信 HTML 来源，前端只做展示，不做二次解析执行。
"""
import markdown
import bleach

# 标签白名单：仅保留基础排版标签（含表格/代码块/图片），
# 禁止 script/iframe/object/embed/form/input/style/link/meta 等可执行或可逃逸标签。
ALLOWED_TAGS = [
    "p", "br", "hr",
    "strong", "em", "u", "s", "del", "sup", "sub", "mark",
    "a", "ul", "ol", "li",
    "blockquote", "pre", "code",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "table", "thead", "tbody", "tr", "th", "td",
    "img",
]

# 站内图片允许的前缀（与 app.py serve_post_image / serve_avatar 路由一致）
_IMG_ALLOWED_PREFIXES = ("/uploads/post_images/", "/uploads/avatars/")
_IMG_ALLOWED_ATTRS = ("src", "alt", "title")


def _img_attr_filter(tag, name, value):
    """bleach 属性过滤器 callable(tag, name, value) -> bool：
    img 仅保留 src/alt/title；src 只允许站内相对路径，外链一律剥除"""
    if tag != "img":
        return True
    if name not in _IMG_ALLOWED_ATTRS:
        return False
    if name == "src" and not value.startswith(_IMG_ALLOWED_PREFIXES):
        return False
    return True


# 属性白名单：a 仅保留 href/title；img 的属性经 _img_attr_filter 过滤
# （仅 src/alt/title，且 src 只允许站内相对路径，禁止 http/https/data: 外链，
#   防内网探测/SSRF 式外链与追踪像素）；不开放 style/class/id。
ALLOWED_ATTRIBUTES = {
    "a": ["href", "title"],
    "img": _img_attr_filter,
}

# 协议白名单：http/https/mailto/相对链接；javascript:、data:、vbscript: 一律剥除。
ALLOWED_PROTOCOLS = {"http", "https", "mailto", "relative"}


def render_markdown(text):
    """将原始 Markdown 渲染为经过白名单过滤的安全 HTML（字符串）"""
    if not text:
        return ""
    html = markdown.markdown(
        text,
        extensions=["extra", "sane_lists", "nl2br"],
        output_format="html5",
    )
    return bleach.clean(
        html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        protocols=ALLOWED_PROTOCOLS,
        strip=True,          # 删除白名单外标签（保留其文本内容）
        strip_comments=True,  # 删除 HTML 注释
    )
