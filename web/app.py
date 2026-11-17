# -*- coding: utf-8 -*-
"""
AI增强型知识社区 - Web版主应用
Flask + Jinja2 模板渲染，复用 backend/ 下的 service 层
运行：venv\\Scripts\\python.exe web\\app.py
"""
# ============================================================
# gevent 协程补丁：必须位于所有其他 import 之前执行，才能正确接管
# socket/ssl/threading。backend/services 里的 threading.Lock（RAG 索引锁）
# 在补丁后导入，自动获得协程安全版本；全项目无真实 OS 线程，
# 不存在 greenlet 跨线程使用问题。gevent 未安装时直接 ImportError（fail-fast）。
# ============================================================
import sys
import os
import zlib
# 将项目的 backend 目录加入系统路径，让 app.py 能找到模块
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'backend')))
from gevent import monkey
monkey.patch_all()

import os
import sys
import time
import json
import uuid
import hmac
import secrets
import urllib.parse
from functools import wraps
from io import BytesIO
from PIL import Image

# 将 backend/ 下的 services、core、config 加入路径，复用业务逻辑层
BACKEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend")
sys.path.insert(0, os.path.abspath(os.path.join(BACKEND_DIR, "services")))
sys.path.insert(0, os.path.abspath(os.path.join(BACKEND_DIR, "core")))
sys.path.insert(0, os.path.abspath(os.path.join(BACKEND_DIR, "config")))

from flask import Flask, render_template, request, redirect, url_for, session, flash, send_from_directory, jsonify, abort
from flask_socketio import SocketIO, join_room, leave_room
from auth import login as auth_login, register as auth_register, change_password as auth_change_password
from board_service import (
    get_all_boards, ensure_default_boards,
    create_board, update_board, delete_board, get_board_post_count, top_boards,
    apply_board, get_applications, get_applications_by_user, review_application,
)
from post_service import (
    get_hot_posts, get_posts_by_board, get_post_count,
    create_post, update_post, get_post_detail, increment_view_count, delete_post,
    search_posts, search_suggest, get_post_id_by_image_path, get_latest_post_id_by_author,
)
from comment_service import get_comments_by_post, add_comment
from favorite_service import get_favorites_by_user, is_favorited, toggle_favorite
from like_service import is_liked, toggle_like
from comment_like_service import (
    is_liked as comment_is_liked,
    toggle_like as comment_toggle_like,
    get_like_count as comment_like_count,
)
from notification_service import get_unread_count, get_notifications, mark_all_read, add_notification
from mention_service import notify_mentions
from user_service import get_user_detail, update_profile, get_user_posts, update_avatar
from follow_service import (
    follow_user, unfollow_user, is_following as is_following_rel,
    get_follow_counts, get_following_list, get_followers_list,
    get_friends_list, is_friend as is_friend_rel, is_blocked as is_blocked_rel,
)
from admin_service import (
    get_all_users, get_all_posts as admin_get_all_posts,
    get_all_comments, toggle_user_status, toggle_top, toggle_essence,
    delete_post as admin_delete_post, delete_comment, revoke_ai_reply,
    get_ai_assistant_id,
    get_post_for_review,
)
from ban_service import is_user_banned, ban_user, unban_user
from report_service import (
    create_report, create_appeal, get_pending_tickets, get_my_tickets,
    handle_report, handle_appeal,
)
from rag_service import generate_answer
from ai_reply_scheduler import scan_and_reply, ensure_ai_assistant
from settings_service import ai_reply_enabled as _ai_reply_flag, set_setting as _set_setting, get_setting as _get_setting
import semantic_search_service as sem
from message_service import (
    send_message, get_thread, mark_thread_read, unread_message_count,
    get_inbox, get_sent, get_conversation_partners,
)
import gevent
from tag_service import (
    get_all_tags, create_tag, update_tag, delete_tag,
    get_or_create_tag, get_tags_by_post, get_posts_by_tag,
    get_tag_by_id, get_all_tags_simple, search_tags_suggest,
    MAX_TAGS_PER_POST,
)
from tag_ai_service import recommend_tags as ai_recommend_tags

app = Flask(__name__)
# secret_key：生产环境必须通过环境变量 FLASK_SECRET_KEY 注入；
# 仅在本地开发且未设置时回退 config.py 内置值（内置值不应视为机密）
from config import SECRET_KEY as _DEFAULT_SECRET_KEY
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or _DEFAULT_SECRET_KEY
if not os.environ.get("FLASK_SECRET_KEY"):
    app.logger.warning("FLASK_SECRET_KEY 未设置，正在使用 config.py 内置密钥（生产环境必须改用环境变量注入并轮换）")
# 会话 Cookie 加固
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("FLASK_COOKIE_SECURE", "0") == "1"
# 限制上传文件最大 10MB
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024
# 静态资源强缓存：模板引用处均带 ?v=版本号，升级资源时改版本号即可刷新客户端缓存
from datetime import timedelta
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = timedelta(days=365)

# gzip 压缩响应：只压文本类 MIME；图片/字体本身已压缩不重复压；500 字节以下不压
from flask_compress import Compress
app.config["COMPRESS_MIMETYPES"] = ["text/html", "text/css", "text/xml", "application/json", "application/javascript", "text/javascript", "image/svg+xml"]
app.config["COMPRESS_MIN_SIZE"] = 500
app.config["COMPRESS_LEVEL"] = 6
Compress(app)

# SocketIO 实时推送：gevent 模式，配合 gevent-websocket 支持 WebSocket 全双工
# CORS 收紧为同源：默认 [] 表示仅允许同源页面握手，阻断跨站 WebSocket 劫持（CSWSH）
# 实例来自 socketio_bus（延迟绑定）：service 层需与 app 共用同一实例才能互推事件
from socketio_bus import socketio
socketio.init_app(app, async_mode="gevent", cors_allowed_origins=[])

# 文件上传目录
AVATARS_DIR = os.path.abspath(os.path.join(BACKEND_DIR, "avatars"))
POST_IMAGES_DIR = os.path.abspath(os.path.join(BACKEND_DIR, "post_images"))
os.makedirs(AVATARS_DIR, exist_ok=True)
os.makedirs(POST_IMAGES_DIR, exist_ok=True)

# ========== 图片安全处理：真实格式校验 / 剥离EXIF / 限尺寸 / 压缩 / 缩略图 ==========
MAX_IMAGE_BYTES = 5 * 1024 * 1024      # 上传文件大小上限 5MB（超出直接拒绝）
MAX_IMAGE_EDGE = 1920                  # 图片长边上限，超出等比缩放（不放大）
THUMB_WIDTH = 300                      # 帖子图片缩略图宽度（列表页优先加载）
IMAGE_SAVE_QUALITY = 85                # JPEG 统一保存质量（85%）
ALLOWED_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp")


def _process_uploaded_image(file_storage, dest_dir, name_prefix, need_thumb=False):
    """安全处理上传图片并落盘（原始输入不落盘，防伪装文件/超大文件落地）：
    1) 扩展名白名单 + Pillow 真实格式校验（verify），拒绝非图片/损坏文件；
    2) 大小上限 5MB：只读上限+1 字节判断，不把超限文件整体读入内存；
    3) 重建 Image 对象（丢弃 EXIF/ICC/缩略图等全部元数据），透明图合成白底后转 RGB；
    4) 长边 >1920px 等比缩放（LANCZOS），小图不放大；
    5) 统一按 85% 质量重存为 JPEG；need_thumb 时额外生成 300px 宽缩略图。
    返回 (True, 主文件名) 或 (False, 错误消息)；失败时清理已落盘的半成品。"""
    if not file_storage or not file_storage.filename:
        return False, "未选择文件"
    ext = os.path.splitext(file_storage.filename)[1].lower()
    if ext not in ALLOWED_IMAGE_EXTS:
        return False, "图片格式仅支持 png/jpg/jpeg/gif/bmp/webp"
    raw = file_storage.read(MAX_IMAGE_BYTES + 1)
    if len(raw) > MAX_IMAGE_BYTES:
        return False, "图片大小超过 5MB 上限，已拒绝"
    try:
        probe = Image.open(BytesIO(raw))
        probe.verify()  # 真实格式校验：伪装文件/损坏数据在此抛异常
    except Exception:
        return False, "文件不是有效图片，已拒绝"
    fname = f"{name_prefix}{uuid.uuid4().hex}.jpg"  # 统一重存为 JPEG，剥离原格式
    try:
        img = Image.open(BytesIO(raw))  # verify() 后对象不可复用，重新打开
        if img.mode in ("RGBA", "LA", "P"):
            rgba = img.convert("RGBA")
            canvas = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
            canvas.alpha_composite(rgba)
            img = canvas.convert("RGB")
        else:
            img = img.convert("RGB")
        if max(img.size) > MAX_IMAGE_EDGE:
            img.thumbnail((MAX_IMAGE_EDGE, MAX_IMAGE_EDGE), Image.LANCZOS)
        img.save(os.path.join(dest_dir, fname), "JPEG",
                 quality=IMAGE_SAVE_QUALITY, optimize=True)
        if need_thumb:
            thumb = img.copy()
            thumb.thumbnail((THUMB_WIDTH, THUMB_WIDTH), Image.LANCZOS)
            thumb.save(os.path.join(dest_dir, fname.replace(".jpg", "_thumb.jpg")),
                       "JPEG", quality=IMAGE_SAVE_QUALITY, optimize=True)
        return True, fname
    except Exception as e:
        for name in (fname, fname.replace(".jpg", "_thumb.jpg")):
            try:
                os.remove(os.path.join(dest_dir, name))
            except OSError:
                pass
        return False, f"图片处理失败：{e}"


def _remove_post_image(filename):
    """删除帖子图片主文件及其缩略图（历史无缩略图时静默忽略）"""
    if not filename:
        return
    for name in (filename, os.path.splitext(filename)[0] + "_thumb.jpg"):
        try:
            os.remove(os.path.join(POST_IMAGES_DIR, name))
        except OSError:
            pass


@app.template_filter("post_thumb")
def post_thumb(filename):
    """模板过滤器：post_xxx.jpg -> post_xxx_thumb.jpg（列表页缩略图文件名）"""
    if not filename:
        return None
    return os.path.splitext(filename)[0] + "_thumb.jpg"


@app.template_filter("board_color")
def board_color(board_name):
    """模板过滤器：按板块名稳定 hash 生成 6 种图标配色之一（c0~c5）。
    用 zlib.crc32 保证跨进程/跨重启稳定，不同板块尽量不同色。"""
    if not board_name:
        return "c0"
    return "c%d" % (zlib.crc32(board_name.encode("utf-8")) % 6)


# ========== 登录装饰器 ==========
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        if session.get("role") != 1:
            flash("需要管理员权限", "error")
            return redirect(url_for("index"))
        return f(*args, **kwargs)
    return decorated


def not_banned(f):
    """写操作二次拦截：会话级检查（ban_session_check）之外的纵深防御。
    即使会话检查未触发（例如请求恰好绕过），被封禁用户执行写操作也会被拒绝。"""
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" in session and is_user_banned(session["user_id"])[0]:
            flash("账号已被封禁，无法执行此操作", "error")
            return redirect(url_for("index"))
        return f(*args, **kwargs)
    return decorated


def _can_view_post(post, viewer_id):
    """帖子可见性判断：帖子不存在返回 False，私密帖仅作者本人可见"""
    return bool(post) and (not post["is_private"] or post["author_id"] == viewer_id)


# ========== 安全加固：CSRF / 安全响应头 / 跳转校验 / 审计 ==========
from audit import log_audit  # backend/core/audit.py（敏感操作审计）
from markdown_render import render_markdown  # backend/core/markdown_render.py（Markdown→安全HTML）
from markupsafe import Markup


def _get_csrf_token():
    """生成/读取会话级 CSRF 令牌（模板经 csrf_token() 全局注入隐藏字段）"""
    if "_csrf_token" not in session:
        session["_csrf_token"] = secrets.token_hex(32)
    return session["_csrf_token"]


app.jinja_env.globals["csrf_token"] = _get_csrf_token


@app.before_request
def ban_session_check():
    """被封禁用户的会话立即失效：每次请求检查 is_banned（含惰性解封），
    命中则清空会话并跳转登录页提示原因/解封时间。
    放行范围：静态资源与登出请求（登出需在清理会话前执行）。
    这是封禁拦截的第一道防线（路由层），写操作另有 @not_banned 与 Service 层检查。"""
    if "user_id" not in session:
        return None
    if request.path.startswith("/static") or request.endpoint in ("logout",):
        return None
    banned, reason, until = is_user_banned(session["user_id"])
    if banned:
        detail = f"原因：{reason}" if reason else ""
        detail += f"；解封时间：{until}" if until else "；永久封禁"
        session.clear()
        flash("账号已被封禁，如有疑问请联系管理员" + f"（{detail}）", "error")
        return redirect(url_for("login"))
    return None


@app.before_request
def csrf_protect():
    """所有 POST 请求必须携带与会话匹配的 CSRF 令牌（登录/注册页由模板注入）"""
    if request.method == "POST":
        token = session.get("_csrf_token", "")
        form_token = request.form.get("_csrf_token", "")
        if not token or not form_token or not hmac.compare_digest(str(token), str(form_token)):
            return "CSRF 校验失败", 400


@app.after_request
def set_security_headers(resp):
    """安全响应头基线
    CSP：script-src 已收紧为仅 'self'（全部内联脚本已外部化为本地 static/js/*.js）；
    style-src 保留 'unsafe-inline'（模板内联 style 属性遍布，外部化不可行，且不执行脚本）。
    """
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self'; connect-src 'self' ws: wss:;",
    )
    return resp


def _safe_redirect_target(url):
    """仅允许站内跳转：无 Referer 或 Referer 主机与请求主机一致才放行"""
    if not url:
        return None
    try:
        parts = urllib.parse.urlparse(url)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https", ""):
        return None
    if parts.netloc and parts.netloc != request.host:
        return None
    return url


# ========== SocketIO 实时推送 ==========
# 在线用户表：{sid: user_id}，仅存内存，服务重启即清空
online_users = {}


@socketio.on("connect")
def on_connect():
    """连接鉴权：校验 Flask Session 登录态。
    未登录/会话失效：允许连接但不入在线表、不加入房间，通过 emit 告知前端
    "认证失效"（前端收到后主动断开），避免客户端因被拒而无限自动重连（秒断秒连）。
    登录用户加入私有房间 user_{id}，用于实时推送通知（点赞/评论/收藏等）。"""
    if "user_id" not in session:
        socketio.emit("auth_expired", {"message": "认证失效，请重新登录"}, to=request.sid)
        return
    online_users[request.sid] = session["user_id"]
    join_room(f"user_{session['user_id']}")
    # 管理员加入全局 admins 房间：实时接收新举报/申诉工单推送（参数化 SQL 校验角色）
    try:
        from db import get_cursor
        with get_cursor() as cur:
            cur.execute("SELECT role FROM `user` WHERE user_id = ?", (session["user_id"],))
            row = cur.fetchone()
        if row and row[0] == 1:
            join_room("admins")
            print(f"[SocketIO] 管理员 {session.get('nickname')}({session['user_id']}) 加入 admins 房间")
    except Exception as e:
        print(f"[SocketIO] 管理员房间加入失败: {e}")
    print(f"[SocketIO] 用户 {session.get('nickname')}({session['user_id']}) 已连接，当前在线 {len(online_users)} 人")


@socketio.on("disconnect")
def on_disconnect():
    """断开连接时清理在线状态（关闭/刷新页面会自动触发）"""
    user_id = online_users.pop(request.sid, None)
    print(f"[SocketIO] 连接 {request.sid} 断开（user_id={user_id}），当前在线 {len(online_users)} 人")


def _parse_post_id(data):
    """从客户端事件数据中安全解析帖子ID，非法返回 None（客户端只允许发房间号，不允许发内容）"""
    try:
        post_id = int(data.get("post_id"))
    except (TypeError, ValueError, AttributeError):
        return None
    return post_id if post_id > 0 else None


@socketio.on("join_post")
def on_join_post(data):
    """进入帖子详情页：加入 post_{id} 房间；私密帖仅作者本人可加入"""
    if "user_id" not in session:
        return
    post_id = _parse_post_id(data)
    if post_id is None:
        return
    post = get_post_detail(post_id)
    if not _can_view_post(post, session["user_id"]):
        return
    join_room(f"post_{post_id}")


@socketio.on("leave_post")
def on_leave_post(data):
    """离开帖子详情页：退出房间（页面关闭时也会随连接断开自动退出）"""
    post_id = _parse_post_id(data)
    if post_id is not None:
        leave_room(f"post_{post_id}")


def _broadcast_new_post(is_private):
    """发帖成功后向所有在线用户广播 new_post 事件；私密帖不广播，避免内容泄露。
    create_post 不返回 post_id，回查当前用户最新一篇帖子获取广播数据。
    广播失败仅打印日志，不影响发帖主流程。"""
    if is_private:
        return
    try:
        latest = get_user_posts(session["user_id"], viewer_id=session["user_id"], limit=1)
        if not latest:
            return
        p = get_post_detail(latest[0]["post_id"])
        if not p:
            return
        socketio.emit("new_post", {
            "post_id": p["post_id"],
            "title": p["title"],
            "author": p["author"],  # post_service 已对匿名帖作者做掩码
            "board_id": p["board_id"],
            "board_name": p["board_name"],
            "is_anonymous": p["is_anonymous"],
            "image_path": p["image_path"],
            "create_time": p["create_time"][:16],
            "excerpt": p["content"][:80] + ("..." if len(p["content"]) > 80 else ""),
        })
    except Exception as e:
        print(f"[SocketIO] new_post 广播失败：{e}")


def _broadcast_new_comment(post_id):
    """评论成功后向 post_{id} 房间广播 new_comment 事件（不做全量广播）；失败不影响评论主流程"""
    try:
        comments = get_comments_by_post(post_id)
        if not comments:
            return
        c = comments[-1]  # 刚插入的评论自增ID最大，按时间升序排列在最后
        socketio.emit("new_comment", {
            "comment_id": c["comment_id"],
            "post_id": post_id,
            "user_id": c["user_id"],
            "nickname": c["nickname"],
            "avatar_path": c["avatar_path"],
            "content": c["content"],
            "content_html": render_markdown(c["content"]),  # 服务端过滤后的安全 HTML
            "create_time": c["create_time"][:16],
            "floor": c["floor"],
        }, room=f"post_{post_id}")
    except Exception as e:
        print(f"[SocketIO] new_comment 广播失败：{e}")


def _broadcast_delete_post(post_id):
    """帖子删除后向所有在线用户广播 delete_post 事件，列表/详情页同步移除"""
    try:
        socketio.emit("delete_post", {"post_id": post_id})
    except Exception as e:
        print(f"[SocketIO] delete_post 广播失败：{e}")


def _broadcast_delete_comment(comment_id):
    """评论删除后向所有在线用户广播 delete_comment 事件，详情页同步移除"""
    try:
        socketio.emit("delete_comment", {"comment_id": comment_id})
    except Exception as e:
        print(f"[SocketIO] delete_comment 广播失败：{e}")


def _notify_user(user_id, notify_type, content):
    """向指定用户实时推送通知：写入其私有房间 user_{id}。
    用户不在线时静默跳过（通知已落库，下次打开通知页可见）。"""
    try:
        unread = get_unread_count(user_id)
        socketio.emit("notification", {
            "type": notify_type,
            "content": content,
            "unread": unread,
        }, room=f"user_{user_id}")
    except Exception as e:
        print(f"[SocketIO] 通知推送失败：{e}")


def _broadcast_like(post_id, like_count, liked, user_id):
    """点赞/取消点赞后向帖子房间广播 like_update，在线用户实时更新计数与按钮状态"""
    try:
        socketio.emit("like_update", {
            "post_id": post_id,
            "like_count": like_count,
            "liked": liked,
            "user_id": user_id,
        }, room=f"post_{post_id}")
    except Exception as e:
        print(f"[SocketIO] like_update 广播失败：{e}")


def _broadcast_comment_like(post_id, comment_id, like_count):
    """评论点赞/取消后向帖子房间广播 comment_like_update。
    前端仅更新其他在线用户看到的点赞数（不覆盖点赞者自己的已赞状态）。"""
    try:
        socketio.emit("comment_like_update", {
            "post_id": post_id,
            "comment_id": comment_id,
            "like_count": like_count,
        }, room=f"post_{post_id}")
    except Exception as e:
        print(f"[SocketIO] comment_like_update 广播失败：{e}")


def _broadcast_favorite(post_id, favorite_count, favorited, user_id):
    """收藏/取消收藏后向帖子房间广播 favorite_update，在线用户实时更新计数与按钮状态"""
    try:
        socketio.emit("favorite_update", {
            "post_id": post_id,
            "favorite_count": favorite_count,
            "favorited": favorited,
            "user_id": user_id,
        }, room=f"post_{post_id}")
    except Exception as e:
        print(f"[SocketIO] favorite_update 广播失败：{e}")


# ========== 模板全局变量 ==========
@app.context_processor
def inject_globals():
    return {
        "current_user": {
            "user_id": session.get("user_id"),
            "nickname": session.get("nickname"),
            "role": session.get("role"),
            "avatar_path": session.get("avatar_path"),
        } if "user_id" in session else None,
        "unread_count": get_unread_count(session["user_id"]) if "user_id" in session else 0,
        "unread_msg_count": unread_message_count(session["user_id"]) if "user_id" in session else 0,
    }


# ========== 静态文件：头像和帖子图片 ==========
# 上传文件均为 uuid 命名（不会同名覆盖），可安全使用 7 天强缓存


def _post_id_by_image(filename):
    """按图片文件名反查帖子 ID（含缩略图后缀归一；历史旧扩展名兜底）"""
    name = filename
    if name.endswith("_thumb.jpg"):
        name = name[:-len("_thumb.jpg")] + ".jpg"
    pid = get_post_id_by_image_path(name)
    if pid is not None:
        return pid
    stem = os.path.splitext(name)[0]
    for ext in (".png", ".jpeg", ".gif", ".bmp", ".webp"):
        pid = get_post_id_by_image_path(stem + ext)
        if pid is not None:
            return pid
    return None


def _can_view_image(filename):
    """帖子图片访问权限（PRIV-07 越权防护）：
    - 能反查到帖子：公开帖任何人可看；私密帖仅作者本人或管理员可看，其余 403；
    - 反查不到（孤儿/历史遗留文件）：uuid 文件名不可枚举，保持放行以兼容旧引用。"""
    pid = _post_id_by_image(filename)
    if pid is None:
        return True
    post = get_post_detail(pid)
    if not post:
        return True
    if post["is_private"]:
        uid = session.get("user_id")
        return uid is not None and (uid == post["author_id"] or session.get("role") == 1)
    return True


@app.route("/uploads/avatars/<path:filename>")
def serve_avatar(filename):
    resp = send_from_directory(AVATARS_DIR, filename)
    resp.headers["Cache-Control"] = "public, max-age=604800"
    return resp

@app.route("/uploads/post_images/<path:filename>")
def serve_post_image(filename):
    if not _can_view_image(filename):
        return "无权访问该图片", 403
    path = os.path.join(POST_IMAGES_DIR, filename)
    if not os.path.isfile(path) and filename.endswith("_thumb.jpg"):
        # 历史帖子没有缩略图：回退到同文件名的原图，避免列表页破图
        stem = filename[:-len("_thumb.jpg")]
        for ext in (".jpg", ".png", ".jpeg", ".gif", ".bmp", ".webp"):
            if os.path.isfile(os.path.join(POST_IMAGES_DIR, stem + ext)):
                resp = send_from_directory(POST_IMAGES_DIR, stem + ext)
                resp.headers["Cache-Control"] = "public, max-age=604800"
                return resp
    resp = send_from_directory(POST_IMAGES_DIR, filename)
    resp.headers["Cache-Control"] = "public, max-age=604800"
    return resp


# ========== 认证 ==========
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        success, msg, user = auth_login(username, password)
        if success:
            session["user_id"] = user["user_id"]
            session["username"] = user["username"]
            session["nickname"] = user["nickname"]
            session["role"] = user["role"]
            # 加载头像路径
            detail = get_user_detail(user["user_id"])
            session["avatar_path"] = detail.get("avatar_path") if detail else None
            return redirect(url_for("index"))
        flash(msg, "error")
    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        nickname = request.form.get("nickname", "").strip() or username
        success, msg = auth_register(username, password, nickname)
        if success:
            flash("注册成功，请登录", "success")
            return redirect(url_for("login"))
        flash(msg, "error")
    return render_template("register.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ========== 首页 / 帖子列表 ==========
@app.route("/")
def index():
    if "user_id" not in session:
        return redirect(url_for("login"))
    board_id = request.args.get("board", type=int)
    page = request.args.get("page", 1, type=int)
    if page < 1:  # 防止 page=0/负数生成负 OFFSET 导致 SQL 报错
        page = 1
    page_size = 20

    boards = get_all_boards()
    if board_id:
        posts = get_posts_by_board(board_id, page, page_size,
                                   current_user_id=session["user_id"])
        total = get_post_count(board_id)
        current_board = next((b for b in boards if b["board_id"] == board_id), None)
        title = current_board["board_name"] if current_board else "板块"
    else:
        posts = get_hot_posts(page, page_size, current_user_id=session["user_id"])
        total = get_post_count()
        title = "热门帖子"

    total_pages = max(1, (total + page_size - 1) // page_size)
    hot_boards = top_boards(5)
    return render_template("index.html",
                           boards=boards, posts=posts, title=title,
                           board_id=board_id, page=page, total_pages=total_pages,
                           hot_boards=hot_boards)


@app.route("/boards")
@login_required
def boards_page():
    """板块列表页（热门板块排行榜“查看更多”入口）"""
    all_boards = get_all_boards()
    board_stats = top_boards(len(all_boards) or 1)
    stats_map = {b["board_id"]: b["post_count"] for b in board_stats}
    return render_template("boards.html",
                           boards=all_boards,
                           post_counts=stats_map)


# ========== 发帖 ==========
def _resolve_tag_ids(raw_tags):
    """解析发帖/编辑表单提交的标签 JSON，返回 (tag_ids, flash_msg, rejected)。
    - 空串/无标签 → ([], None, False)（视为未选标签，不提示）
    - JSON 解析失败或结果非数组 → ([], "标签格式有误，已忽略标签", False)（忽略标签继续）
    - 非字符串元素（数字/布尔等）→ 直接过滤，防止 _normalize_name 对非 str 报错
    - 去重保序（list(dict.fromkeys)），防止重复 tag_id 触发唯一索引
    - 超过 MAX_TAGS_PER_POST → ([], 提示, True)（调用方应拒绝提交）
    - get_or_create_tag 失败 → ([], 提示, False)
    """
    tag_ids = []
    raw = (raw_tags or "").strip()
    tag_names = []
    if raw:
        try:
            parsed = json.loads(raw)
            if not isinstance(parsed, list):
                raise ValueError
            tag_names = [n.strip() for n in parsed if isinstance(n, str) and n.strip()]
        except (ValueError, TypeError):
            return [], "标签格式有误，已忽略标签", False
    tag_names = list(dict.fromkeys(tag_names))  # 去重保序，防止重复 tag_id 触发唯一索引
    if len(tag_names) > MAX_TAGS_PER_POST:
        return [], f"标签最多选择 {MAX_TAGS_PER_POST} 个，当前 {len(tag_names)} 个", True
    for name in tag_names:
        ok, msg, tid = get_or_create_tag(name)
        if ok and tid:
            tag_ids.append(tid)
        else:
            return [], f"标签『{name}』处理失败：{msg}", False
    return tag_ids, None, False


@app.route("/post/new", methods=["GET", "POST"])
@login_required
@not_banned
def new_post():
    boards = get_all_boards()
    all_tags = get_all_tags_simple()  # 发帖页"选择已有标签"组件数据
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        content = request.form.get("content", "").strip()
        board_id = request.form.get("board_id", type=int)
        is_private = 1 if request.form.get("is_private") else 0
        is_anonymous = 1 if request.form.get("is_anonymous") else 0

        # 处理图片上传（安全处理：真实格式校验/剥离EXIF/限尺寸/85%压缩/300px缩略图）
        image_path = None
        file = request.files.get("image")
        if file and file.filename:
            ok, result = _process_uploaded_image(file, POST_IMAGES_DIR, "post_", need_thumb=True)
            if ok:
                image_path = result
            else:
                flash(result, "error")

        # 处理标签：复用 _resolve_tag_ids（JSON 数组解析、非字符串过滤、去重保序、
        # 服务端上限校验、get_or_create_tag 复用/自动创建）
        tag_ids, tag_msg, tag_rejected = _resolve_tag_ids(request.form.get("tags", ""))
        if tag_msg:
            flash(tag_msg, "error")
        if tag_rejected:
            if image_path:
                _remove_post_image(image_path)
            return render_template("new_post.html", boards=boards, all_tags=all_tags)

        success, msg = create_post(title, content, session["user_id"], board_id,
                                   is_private, is_anonymous, image_path, tag_ids)
        if success:
            # 实时广播：新帖推送给所有在线用户（私密帖不广播）
            _broadcast_new_post(is_private)
            # @提及：正文中 @用户名 → 站内通知 + 实时推送（参数化查询，不存在/本人自动跳过）
            try:
                pid = get_latest_post_id_by_author(session["user_id"])
                for uid, msg in notify_mentions(
                        content, pid, session["user_id"], session["nickname"], "发布的帖子"):
                    _notify_user(uid, "mention", msg)
            except Exception as e:
                print(f"[@提及] 发帖通知失败：{e}")
            # 语义搜索索引：公开帖后台异步入索引（私密帖不入索引）
            if not is_private:
                gevent.spawn(sem.index_post, pid, title, content)
            return redirect(url_for("index"))
        # 发帖失败时删除刚上传的图片（含缩略图），避免残留孤儿文件
        if image_path:
            _remove_post_image(image_path)
        flash(msg, "error")
    return render_template("new_post.html", boards=boards, all_tags=all_tags)


# ========== 帖子详情 ==========
@app.route("/post/<int:post_id>")
def post_detail(post_id):
    if "user_id" not in session:
        return redirect(url_for("login"))
    post = get_post_detail(post_id)
    if not post:
        flash("帖子不存在或已被删除", "error")
        return redirect(url_for("index"))
    # 私密帖权限检查
    if post["is_private"] and post["author_id"] != session["user_id"]:
        flash("这是私密帖子，您无权查看", "error")
        return redirect(url_for("index"))

    increment_view_count(post_id)
    comments = get_comments_by_post(post_id, viewer_id=session.get("user_id"))
    # Markdown 渲染（服务端转换 + XSS 白名单过滤；入库保存原始 MD，展示时再渲染）
    post["content"] = Markup(render_markdown(post["content"]))
    comments = [dict(c, content=Markup(render_markdown(c["content"]))) for c in comments]
    liked = is_liked(session["user_id"], post_id)
    favorited = is_favorited(session["user_id"], post_id)
    tags = get_tags_by_post(post_id)  # 详情页标签展示
    # 分享弹窗好友改为前端按需分页拉取（POST /api/friends/list），避免一次性渲染全部好友
    return render_template("post_detail.html",
                           post=post, comments=comments,
                           liked=liked, favorited=favorited, tags=tags)


# ========== 评论 ==========
@app.route("/api/markdown/preview", methods=["POST"])
@login_required
def markdown_preview():
    """Markdown 实时预览：服务端渲染 + XSS 白名单过滤后返回安全 HTML
    （CSRF 由全局 before_request 统一校验；前端 fetch 携带 _csrf_token）"""
    content = request.form.get("content", "")
    return {"html": render_markdown(content)}


@app.route("/post/<int:post_id>/comment", methods=["POST"])
@login_required
@not_banned
def add_comment_route(post_id):
    post = get_post_detail(post_id)
    # 私密帖仅作者本人可评论
    if not _can_view_post(post, session["user_id"]):
        flash("帖子不存在或无权访问", "error")
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return {"ok": False, "message": "帖子不存在或无权访问"}, 403
        return redirect(url_for("index"))
    # 拉黑限制：帖子作者拉黑了当前用户 → 不能评论该作者的帖子
    from follow_service import is_blocked as _is_blocked_by
    if post["author_id"] != session["user_id"] and _is_blocked_by(post["author_id"], session["user_id"]):
        flash("无法评论该用户的帖子", "error")
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return {"ok": False, "message": "无法评论该用户的帖子"}, 403
        return redirect(url_for("post_detail", post_id=post_id))
    content = request.form.get("content", "").strip()
    success, msg, new_cid = add_comment(post_id, session["user_id"], content)
    if success:
        if post["author_id"] != session["user_id"]:
            # 通知作者（落库）+ 实时推送（未读角标即时 +1，无需刷新）
            add_notification(
                user_id=post["author_id"], notify_type="comment",
                post_id=post_id, from_user_id=session["user_id"],
                content=f"{session['nickname']} 评论了你的帖子：{content[:30]}",
            )
            _notify_user(post["author_id"], "comment",
                         f"{session['nickname']} 评论了你的帖子：{content[:30]}")
        # @提及：评论中 @用户名 → 站内通知 + 实时推送（参数化查询，不存在/本人自动跳过）
        # 携带 comment_id：前端可跳转 /post/<post_id>#comment-<comment_id>
        try:
            for uid, msg in notify_mentions(
                    content, post_id, session["user_id"], session["nickname"], "评论的帖子",
                    comment_id=new_cid):
                _notify_user(uid, "mention", msg)
        except Exception as e:
            print(f"[@提及] 评论通知失败：{e}")
        # 实时广播：向帖子房间推送新评论（广播失败不影响评论主流程）
        _broadcast_new_comment(post_id)
        # AJAX 提交：返回新评论数据由前端局部插入；普通表单提交保持原有重定向兜底
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            c = None
            try:
                comments = get_comments_by_post(post_id)
                c = comments[-1] if comments else None
            except Exception as e:
                # 回查失败时评论已入库：返回 ok=true 由前端整页刷新，避免重复提交
                print(f"[评论回查失败] {e}")
            if c:
                # content_html 为服务端 bleach 过滤后的安全 HTML，前端可安全 innerHTML 插入
                return {"ok": True, "comment": {
                    "comment_id": c["comment_id"],
                    "user_id": c["user_id"],
                    "nickname": c["nickname"],
                    "avatar_path": c["avatar_path"],
                    "content": c["content"],
                    "content_html": render_markdown(c["content"]),
                    "create_time": c["create_time"][:16],
                    "floor": c["floor"],
                }}
            return {"ok": True, "comment": None}
    elif request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return {"ok": False, "message": msg}, 400
    return redirect(url_for("post_detail", post_id=post_id))


# ========== 点赞/收藏 ==========
@app.route("/post/<int:post_id>/like", methods=["POST"])
@login_required
@not_banned
def like_post(post_id):
    post = get_post_detail(post_id)
    # 私密帖仅作者本人可点赞
    if not _can_view_post(post, session["user_id"]):
        flash("帖子不存在或无权访问", "error")
        return redirect(url_for("index"))
    liked, like_count, msg = toggle_like(session["user_id"], post_id)
    if liked and post["author_id"] != session["user_id"]:
        # 点赞通知：仅在"点赞"时发送（取消点赞不发），触发者=帖主本人不发
        add_notification(
            user_id=post["author_id"], notify_type="like",
            post_id=post_id, from_user_id=session["user_id"],
            content=f"{session['nickname']} 赞了你的帖子：{post['title'][:30]}",
        )
        # 实时推送通知给帖主（未读角标即时 +1）
        _notify_user(post["author_id"], "like",
                     f"{session['nickname']} 赞了你的帖子：{post['title'][:30]}")
    # 实时广播：详情页在线用户同步更新点赞数与按钮状态
    _broadcast_like(post_id, like_count, liked, session["user_id"])
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return {"ok": True, "liked": liked, "like_count": like_count, "message": msg}
    flash(msg, "success")
    return redirect(_safe_redirect_target(request.referrer) or url_for("post_detail", post_id=post_id))


@app.route("/api/comment/<int:comment_id>/like", methods=["POST"])
@login_required
@not_banned
def like_comment(comment_id):
    """评论点赞/取消点赞。
    - 限速：同一用户每分钟最多 60 次（复用 rate_limit.py）
    - 校验：评论存在(404) / 不能赞自己(400) / 拉黑(403)
    - 点赞成功后：通知评论作者 + SocketIO 广播 comment_like_update
    - user_id 一律取 session，不接受前端传入（防代点赞）"""
    if _comment_like_limiter.hit("cl_%d" % session["user_id"]):
        return {"ok": False, "message": "操作太频繁，请稍后再试"}, 429

    liked, like_count, msg = comment_toggle_like(session["user_id"], comment_id)
    if msg == "评论不存在":
        return {"ok": False, "message": msg}, 404
    if msg == "不能给自己的评论点赞":
        return {"ok": False, "message": msg}, 400
    if msg == "无法点赞该用户的评论":
        return {"ok": False, "message": msg}, 403
    if not liked and msg != "已取消点赞":
        # 封禁等其它失败（@not_banned 已挡封禁；此处兜底）
        return {"ok": False, "message": msg}, 400

    # 取评论作者与所属帖子（点赞成功后才需要；Service 已校验评论存在）
    from db import get_cursor
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT user_id, post_id FROM comment WHERE comment_id = ?",
            (comment_id,),
        )
        row = cursor.fetchone()
    if row:
        author_id, post_id = row[0], row[1]
        if liked and author_id != session["user_id"]:
            # 点赞通知：仅在"点赞"时发送（取消点赞不发），触发者=评论作者本人不发
            add_notification(
                user_id=author_id, notify_type="comment_like",
                post_id=post_id, comment_id=comment_id,
                from_user_id=session["user_id"],
                content=f"{session['nickname']} 赞了你的评论",
            )
            # 实时推送通知给评论作者（未读角标即时 +1）
            _notify_user(author_id, "comment_like",
                         f"{session['nickname']} 赞了你的评论")
        if post_id:
            # 实时广播：详情页在线用户同步更新点赞数（点赞/取消都广播；不覆盖点赞者自己的已赞状态）
            _broadcast_comment_like(post_id, comment_id, like_count)
    return {"ok": True, "liked": liked, "count": like_count, "message": msg}


@app.route("/post/<int:post_id>/favorite", methods=["POST"])
@login_required
@not_banned
def favorite_post(post_id):
    post = get_post_detail(post_id)
    # 私密帖仅作者本人可收藏
    if not _can_view_post(post, session["user_id"]):
        flash("帖子不存在或无权访问", "error")
        return redirect(url_for("index"))
    favorited, fav_count, msg = toggle_favorite(session["user_id"], post_id)
    if favorited and post["author_id"] != session["user_id"]:
        # 收藏通知：仅在"收藏"时发送（取消收藏不发），触发者=帖主本人不发
        add_notification(
            user_id=post["author_id"], notify_type="favorite",
            post_id=post_id, from_user_id=session["user_id"],
            content=f"{session['nickname']} 收藏了你的帖子：{post['title'][:30]}",
        )
        # 实时推送通知给帖主（未读角标即时 +1）
        _notify_user(post["author_id"], "favorite",
                     f"{session['nickname']} 收藏了你的帖子：{post['title'][:30]}")
    # 实时广播：详情页在线用户同步更新收藏数与按钮状态
    _broadcast_favorite(post_id, fav_count, favorited, session["user_id"])
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return {"ok": True, "favorited": favorited, "favorite_count": fav_count, "message": msg}
    flash(msg, "success")
    return redirect(_safe_redirect_target(request.referrer) or url_for("post_detail", post_id=post_id))


@app.route("/post/<int:post_id>/delete", methods=["POST"])
@login_required
@not_banned
def delete_post_route(post_id):
    post = get_post_detail(post_id)
    if post and post["author_id"] == session["user_id"]:
        success, msg = delete_post(post_id)
        if success:
            # 实时广播：所有在线用户的列表页/详情页同步移除该帖
            _broadcast_delete_post(post_id)
            # 语义搜索索引：从向量库移除
            gevent.spawn(sem.remove_post, post_id)
    return redirect(url_for("index"))


@app.route("/post/<int:post_id>/edit", methods=["GET", "POST"])
@login_required
@not_banned
def edit_post(post_id):
    post = get_post_detail(post_id)
    if not post:
        flash("帖子不存在或已被删除", "error")
        return redirect(url_for("index"))
    # 权限：作者本人 或 管理员（匿名帖 author_id 为内部字段，可正常判断归属）
    if post["author_id"] != session["user_id"] and session.get("role") != 1:
        flash("无权编辑该帖子", "error")
        return redirect(url_for("post_detail", post_id=post_id))

    boards = get_all_boards()
    all_tags = get_all_tags_simple()
    tags = get_tags_by_post(post_id)  # 帖子当前标签，编辑页回填选中态

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        content = request.form.get("content", "").strip()
        board_id = request.form.get("board_id", type=int)
        is_private = 1 if request.form.get("is_private") else 0
        is_anonymous = 1 if request.form.get("is_anonymous") else 0

        # 图片：上传了新图才替换，否则保留原图（无"移除图片"选项，保持简单）
        image_path = post["image_path"]
        file = request.files.get("image")
        if file and file.filename:
            ok, result = _process_uploaded_image(file, POST_IMAGES_DIR, "post_", need_thumb=True)
            if ok:
                image_path = result
            else:
                flash(result, "error")

        # 标签：与发帖共用同一套解析（非字符串过滤/去重保序/服务端上限校验/自动创建）
        tag_ids, tag_msg, tag_rejected = _resolve_tag_ids(request.form.get("tags", ""))
        if tag_msg:
            flash(tag_msg, "error")
        if tag_rejected:
            # 超限：不保存，删除本次刚上传的新图（含缩略图）避免孤儿文件
            if image_path != post["image_path"]:
                _remove_post_image(image_path)
            return render_template("edit_post.html", post=post, boards=boards,
                                   all_tags=all_tags, tags=tags)

        success, msg = update_post(post_id, title, content, board_id,
                                   is_private, is_anonymous, image_path, tag_ids)
        if success:
            # 保存成功且替换了图片：删除旧图文件（含缩略图），避免孤儿文件残留
            if image_path != post["image_path"] and post["image_path"]:
                _remove_post_image(post["image_path"])
            # @提及（编辑场景）：正文中新增的 @用户名 → 站内通知；
            # skip_already_notified 保证同一帖子已通知过的用户不重复通知
            try:
                for uid, msg in notify_mentions(
                        content, post_id, session["user_id"], session["nickname"],
                        "编辑的帖子", skip_already_notified=True):
                    _notify_user(uid, "mention", msg)
            except Exception as e:
                print(f"[@提及] 编辑通知失败：{e}")
            # 语义搜索索引：编辑后更新向量（转私密则从索引移除）
            gevent.spawn(sem.update_post_index, post_id, title, content, bool(is_private))
            flash("帖子修改成功", "success")
            return redirect(url_for("post_detail", post_id=post_id))
        # 保存失败：删除本次新上传的图片（含缩略图）
        if image_path != post["image_path"]:
            _remove_post_image(image_path)
        flash(msg, "error")

    return render_template("edit_post.html", post=post, boards=boards,
                           all_tags=all_tags, tags=tags)


# ========== 个人中心 ==========
@app.route("/user/<int:user_id>")
def user_profile(user_id):
    if "user_id" not in session:
        return redirect(url_for("login"))
    profile = get_user_detail(user_id, session["user_id"])
    if not profile:
        flash("用户不存在", "error")
        return redirect(url_for("index"))
    # 拉黑可见性：目标用户拉黑了当前访问者 → 视为用户不存在（404）
    from follow_service import is_blocked as _is_blocked
    if _is_blocked(user_id, session["user_id"]):
        abort(404)
    posts = get_user_posts(user_id, session["user_id"])
    following_count, follower_count = get_follow_counts(user_id)
    friend_count, _ = get_friends_list(user_id, 1, 1)
    is_self = (user_id == session["user_id"])
    is_following = False if is_self else is_following_rel(session["user_id"], user_id)
    is_blocked_by_me = False if is_self else _is_blocked(session["user_id"], user_id)
    return render_template("profile.html", profile=profile, posts=posts,
                           is_self=is_self, is_following=is_following,
                           is_blocked_by_me=is_blocked_by_me,
                           following_count=following_count, follower_count=follower_count,
                           friend_count=friend_count)


@app.route("/user/<int:user_id>/follow", methods=["POST"])
@login_required
@not_banned
def follow_user_route(user_id):
    """关注/取关（AJAX）：CSRF 全局校验 + 登录/非封禁 + 限速（20 次/分钟/用户）。
    越权防护：仅能操作 session 所属用户自己的关注关系，无法代他人关注。
    action=follow 幂等关注（已关注拒绝，防重复插入）；action=unfollow 幂等取关。"""
    if _follow_limiter.hit(session["user_id"]):
        return jsonify({"ok": False, "msg": "操作过于频繁，请稍后再试"}), 429
    action = request.form.get("action", "follow")
    if action == "unfollow":
        ok, msg = unfollow_user(session["user_id"], user_id)
        act = "unfollow" if ok else "none"
    else:
        ok, msg = follow_user(session["user_id"], user_id)
        act = "follow" if ok else "none"
        if ok:
            # 被关注者站内通知（落库 + 实时 socket 推送）
            from_nick = session.get("nickname") or "用户"
            content = "%s 关注了你" % from_nick
            add_notification(user_id, "follow", None, session["user_id"], content)
            _notify_user(user_id, "follow", content)
    following_count, follower_count = get_follow_counts(user_id)
    return jsonify({"ok": ok, "action": act, "msg": msg,
                    "following_count": following_count, "follower_count": follower_count})


@app.route("/user/<int:user_id>/block", methods=["POST"])
@login_required
@not_banned
def block_user_route(user_id):
    """拉黑：CSRF 全局校验 + 登录/非封禁 + 不能拉黑自己 + 审计。
    拉黑时删除双方关注关系；私信会话保留但对拉黑者隐藏。"""
    if user_id == session["user_id"]:
        return jsonify({"ok": False, "msg": "不能拉黑自己"}), 400
    from follow_service import block_user
    ok, msg = block_user(session["user_id"], user_id)
    if ok:
        try:
            log_audit(session["user_id"], "block_user", "user", user_id,
                      f"blocked user {user_id}")
        except Exception as e:
            print("[审计] block_user 写入失败：", e)
    return jsonify({"ok": ok, "msg": msg}), (200 if ok else 400)


@app.route("/user/<int:user_id>/unblock", methods=["POST"])
@login_required
@not_banned
def unblock_user_route(user_id):
    """解除拉黑：CSRF + 登录/非封禁 + 审计。"""
    if user_id == session["user_id"]:
        return jsonify({"ok": False, "msg": "不能操作自己"}), 400
    from follow_service import unblock_user
    ok, msg = unblock_user(session["user_id"], user_id)
    if ok:
        try:
            log_audit(session["user_id"], "unblock_user", "user", user_id,
                      f"unblocked user {user_id}")
        except Exception as e:
            print("[审计] unblock_user 写入失败：", e)
    return jsonify({"ok": ok, "msg": msg}), (200 if ok else 400)


@app.route("/user/<int:user_id>/following")
@login_required
def user_following(user_id):
    """关注列表（分页，每页 20 条，LIMIT/OFFSET 参数化）"""
    page = max(1, request.args.get("page", 1, type=int))
    total, users = get_following_list(user_id, page, 20)
    target = get_user_detail(user_id)
    return render_template("follow_list.html", users=users, total=total,
                           page=page, page_size=20, mode="following", target=target)


@app.route("/user/<int:user_id>/followers")
@login_required
def user_followers(user_id):
    """粉丝列表（分页）"""
    page = max(1, request.args.get("page", 1, type=int))
    total, users = get_followers_list(user_id, page, 20)
    target = get_user_detail(user_id)
    return render_template("follow_list.html", users=users, total=total,
                           page=page, page_size=20, mode="followers", target=target)


@app.route("/user/<int:user_id>/friends")
@login_required
def user_friends(user_id):
    """好友列表（互相关注，分页）。仅本人可见，他人访问 403。"""
    if user_id != session["user_id"]:
        abort(403)
    page = max(1, request.args.get("page", 1, type=int))
    total, users = get_friends_list(user_id, page, 20)
    target = get_user_detail(user_id)
    return render_template("follow_list.html", users=users, total=total,
                           page=page, page_size=20, mode="friends", target=target)


@app.route("/profile/edit", methods=["GET", "POST"])
@login_required
@not_banned
def edit_profile():
    if request.method == "POST":
        nickname = request.form.get("nickname", "").strip()
        signature = request.form.get("signature", "").strip()
        # 先保存昵称/签名，失败则直接返回，不再处理头像
        success, msg = update_profile(session["user_id"], nickname, signature)
        if not success:
            flash(msg, "error")
            return redirect(url_for("edit_profile"))
        # 头像上传（安全处理：真实格式校验/剥离EXIF/限尺寸/85%压缩）
        file = request.files.get("avatar")
        if file and file.filename:
            ok, result = _process_uploaded_image(
                file, AVATARS_DIR, f"avatar_{session['user_id']}_")
            if ok:
                avatar_filename = result
                success, msg = update_avatar(session["user_id"], avatar_filename)
                if success:
                    session["avatar_path"] = avatar_filename
                else:
                    # 数据库写入失败时删除刚落盘的头像文件，避免残留
                    try:
                        os.remove(os.path.join(AVATARS_DIR, avatar_filename))
                    except OSError:
                        pass
                    flash(msg, "error")
            else:
                flash(result, "error")
        session["nickname"] = nickname
        flash("资料已更新", "success")
        return redirect(url_for("user_profile", user_id=session["user_id"]))
    profile = get_user_detail(session["user_id"])
    from follow_service import get_block_list
    block_list = get_block_list(session["user_id"])
    return render_template("edit_profile.html", profile=profile, block_list=block_list)


# ========== 修改密码限速（内存滑动窗口，按登录用户维度） ==========
# ========== 通用限速器（统一实现，见 backend/core/rate_limit.py） ==========
# 内存滑动窗口，单进程 gevent 部署适用；多进程部署需换 Redis 等共享存储
from rate_limit import SlidingWindowLimiter

# 修改密码：按 user_id 限速（登录后），未登录按 IP 兜底：
# 防撞库/暴力破解按账号维度更精准，且不同测试账号之间互不干扰
_change_pwd_limiter = SlidingWindowLimiter(max_hits=5, window_seconds=60)
# 举报提交：防举报被用作攻击工具
_report_limiter = SlidingWindowLimiter(max_hits=10, window_seconds=60)
# 申诉提交：严格 IP 限速（每小时 3 次，防恶意刷申诉）
_appeal_limiter = SlidingWindowLimiter(max_hits=3, window_seconds=3600)
# 关注/取关：同一用户每分钟最多 20 次（防脚本刷关注）
_follow_limiter = SlidingWindowLimiter(max_hits=20, window_seconds=60)
# AI 标签推荐：同一用户每分钟最多 5 次、每天最多 50 次（AI 调用有成本）
_ai_tag_limiter_min = SlidingWindowLimiter(max_hits=5, window_seconds=60)
_ai_tag_limiter_day = SlidingWindowLimiter(max_hits=50, window_seconds=86400)
# 语义搜索：同一用户每分钟最多 20 次（embedding 有计算成本）
_semantic_limiter = SlidingWindowLimiter(max_hits=20, window_seconds=60)
# 私信发送：同一用户每分钟最多 20 条（防私信轰炸）
_message_limiter = SlidingWindowLimiter(max_hits=20, window_seconds=60)
_comment_like_limiter = SlidingWindowLimiter(max_hits=60, window_seconds=60)


# ========== 测试钩子：限速器重置（仅测试模式 FLASK_TEST=1 时注册，生产 404） ==========
# 防止测试套件连续多轮运行时同 IP 限速窗口残留导致误伤（见历史偶发 429 根因）。
if os.environ.get("FLASK_TEST") == "1":
    @app.route("/_test/reset-rate-limit", methods=["POST"])
    def _test_reset_rate_limit():
        _change_pwd_limiter.reset()
        _report_limiter.reset()
        _appeal_limiter.reset()
        _follow_limiter.reset()
        _ai_tag_limiter_min.reset()
        _ai_tag_limiter_day.reset()
        _semantic_limiter.reset()
        _message_limiter.reset()
        return "ok"

    @app.route("/_test/ai-reply/run", methods=["POST"])
    def _test_ai_reply_run():
        """测试钩子：手动触发一次 AI 回复扫描。
        mock=ok    注入成功生成器（返回固定回复）
        mock=fail  注入失败生成器（返回 None，模拟 AI 调用失败）
        post_id=N  只处理指定帖子（测试专用，防止污染真实帖子）
        默认 none 使用真实 AI 调用。"""
        mock = request.form.get("mock", "none")
        only_pid = request.form.get("post_id", type=int) or None
        if mock == "ok":
            def _mock_ok(t, c):
                return "感谢分享！这个思路很实用，建议补充具体示例数据会让内容更清晰，期待你的后续实践。"
            processed = scan_and_reply(_mock_ok, only_post_id=only_pid)
        elif mock == "fail":
            processed = scan_and_reply(lambda t, c: None, only_post_id=only_pid)
        else:
            processed = scan_and_reply(only_post_id=only_pid)
        return {"ok": True, "processed": processed}

    @app.route("/_test/semantic/rebuild", methods=["POST"])
    def _test_semantic_rebuild():
        """测试钩子：重建语义索引（仅测试环境）。mode=disable 模拟向量库不可用。"""
        if request.form.get("mode") == "disable":
            sem.DISABLED = True
            return {"ok": True, "disabled": True}
        sem.DISABLED = False
        n = sem.rebuild_index()
        return {"ok": True, "indexed": n}

    @app.route("/_test/semantic/count")
    def _test_semantic_count():
        return {"ok": True, "count": sem.count()}

    @app.route("/_test/semantic/enable", methods=["POST"])
    def _test_semantic_enable():
        sem.DISABLED = False
        return {"ok": True, "disabled": False}


@app.route("/profile/change-password", methods=["POST"])
@login_required
def change_password_route():
    """修改密码：IP 限速 → 校验旧密码 + 新密码合规 → 审计 → 成功后强制注销会话
    安全最佳实践：修改密码后强制注销当前会话，用新密码重新登录"""
    ip = request.remote_addr or "unknown"
    # 限速：登录用户每分钟最多 5 次改密尝试（按 user_id 维度；未登录按 IP），超出返回 429
    if _change_pwd_limiter.hit("u:%s" % session.get("user_id") if session.get("user_id") else "ip:%s" % ip):
        log_audit(session.get("user_id", 0), "change_password", "user",
                  session.get("user_id"), f"ip={ip};result=rate_limited")
        return "尝试过于频繁，请稍后再试", 429
    old_pwd = request.form.get("old_password", "")
    new_pwd = request.form.get("new_password", "")
    confirm = request.form.get("confirm_password", "")
    if new_pwd != confirm:
        # 确认密码不一致同样走统一提示，不区分失败原因
        log_audit(session["user_id"], "change_password", "user",
                  session["user_id"], f"ip={ip};result=fail")
        flash("密码修改失败，请检查输入", "error")
        return redirect(url_for("edit_profile"))
    success, msg = auth_change_password(session["user_id"], old_pwd, new_pwd)
    if success:
        # 审计成功后落库（best-effort，不影响业务），再清空会话
        log_audit(session["user_id"], "change_password", "user",
                  session["user_id"], f"ip={ip};result=success")
        session.clear()
        flash("密码已修改，请使用新密码重新登录", "success")
        return redirect(url_for("login"))
    log_audit(session["user_id"], "change_password", "user",
              session["user_id"], f"ip={ip};result=fail")
    flash(msg, "error")
    return redirect(url_for("edit_profile"))


# ========== 板块申请（用户侧） ==========
@app.route("/boards/apply", methods=["GET", "POST"])
@login_required
def board_apply():
    """申请新板块：表单 + 我的申请列表"""
    if request.method == "POST":
        success, msg = apply_board(
            session["user_id"],
            request.form.get("board_name", ""),
            request.form.get("description", ""),
        )
        flash(msg, "success" if success else "error")
        if success:
            return redirect(url_for("board_apply"))
    applications = get_applications_by_user(session["user_id"])
    return render_template("board_apply.html", applications=applications, hide_form=False)


@app.route("/boards/my-applications")
@login_required
def my_board_applications():
    """我的申请列表（只能看自己的）"""
    applications = get_applications_by_user(session["user_id"])
    return render_template("board_apply.html", applications=applications, hide_form=True)


# ========== 举报 / 账号申诉 / 我的工单 ==========
@app.route("/report", methods=["POST"])
@login_required
@not_banned
def report_content():
    """提交举报（帖子/评论）。限速防滥用；CSRF 由全局 before_request 校验。"""
    ip = request.remote_addr or "unknown"
    if _report_limiter.hit("u:%s" % session.get("user_id") if session.get("user_id") else "ip:%s" % ip):
        return "举报过于频繁，请稍后再试", 429
    target_type = request.form.get("target_type", "").strip()
    target_id = request.form.get("target_id", type=int)
    category = request.form.get("category", "other").strip()
    description = request.form.get("description", "").strip()
    success, msg = create_report(
        session["user_id"], target_type, target_id, category, description)
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return {"ok": success, "message": msg}, (200 if success else 400)
    flash(msg, "success" if success else "error")
    if success and target_type == "post":
        return redirect(url_for("post_detail", post_id=target_id))
    return redirect(request.referrer or url_for("index"))


@app.route("/appeal", methods=["GET", "POST"])
def appeal():
    """账号申诉：被封禁用户（session 已失效）可通过未登录接口提交；
    实名验证 = 提交被封账号的用户名，后端校验该账号确处于封禁状态。
    严格 IP 限速（每小时 3 次）防刷；失败统一模糊提示防账号状态探测。"""
    if request.method == "POST":
        ip = request.remote_addr or "unknown"
        # IP 严格限速：每小时最多 3 次申诉尝试，超出返回 429
        if _appeal_limiter.hit("ip:%s" % ip):
            return "申诉请求过于频繁，请稍后再试", 429
        username = request.form.get("username", "").strip()
        reason = request.form.get("reason", "").strip()
        success, msg, _ = create_appeal(0, username, reason)
        flash(msg, "success" if success else "error")
        if success:
            return redirect(url_for("login"))
        return render_template("appeal.html", username=username, reason=reason)
    return render_template("appeal.html")


@app.route("/my-reports")
@login_required
def my_reports():
    """普通用户查看自己的举报/申诉记录（不得访问管理端队列）"""
    tickets = get_my_tickets(session["user_id"])
    return render_template("my_reports.html", tickets=tickets)


# ========== 收藏 ==========
@app.route("/favorites")
@login_required
def my_favorites():
    posts = get_favorites_by_user(session["user_id"], viewer_id=session["user_id"])
    hot_boards = top_boards(5)
    return render_template("index.html", boards=get_all_boards(),
                           posts=posts, title="我的收藏", board_id=None,
                           page=1, total_pages=1, hot_boards=hot_boards)


# ========== 通知 ==========
@app.route("/notifications")
@login_required
def notifications():
    # 信息架构整合：旧通知列表页重定向到消息中心"系统通知" Tab
    return redirect(url_for("messages_home", tab="system"))


# ========== 搜索 ==========
@app.route("/api/search")
@login_required
def api_search():
    """搜索自动补全API，返回JSON"""
    q = request.args.get("q", "").strip()
    if len(q) < 1:
        return {"results": []}
    return {"results": search_suggest(q)}


@app.route("/search")
@login_required
def search():
    q = request.args.get("q", "").strip()
    posts = search_posts(q) if q else []
    return render_template("search.html", q=q, posts=posts)


@app.route("/api/search/semantic")
@login_required
def api_search_semantic():
    """语义搜索：本地向量索引（ChromaDB + 本地 embedding）。
    降级链：向量库不可用/空 → SQL LIKE；输入 <2 字 → SQL LIKE。"""
    q = (request.args.get("q") or "").strip()[:200]
    if _semantic_limiter.hit(session["user_id"]):
        return jsonify({"ok": False, "msg": "操作过于频繁，请稍后再试"}), 429
    if len(q) < 2:
        posts = search_posts(q) if q else []
        return jsonify({"ok": True, "mode": "like", "results": _fmt_search(posts)})
    try:
        results = sem.search_semantic_posts(q, top=10, threshold=sem.SIM_THRESHOLD)
        if results:
            return jsonify({"ok": True, "mode": "semantic", "results": results})
        posts = search_posts(q)
        return jsonify({"ok": True, "mode": "like", "results": _fmt_search(posts)})
    except Exception as e:
        print(f"[语义搜索] 降级 SQL LIKE: {e}")
        posts = search_posts(q)
        return jsonify({"ok": True, "mode": "like", "results": _fmt_search(posts)})


def _fmt_search(posts):
    out = []
    for p in posts or []:
        out.append({
            "post_id": p["post_id"], "title": p["title"], "author": p["author"],
            "create_time": str(p["create_time"])[:16] if p.get("create_time") else "",
            "view_count": p.get("view_count", 0), "reply_count": p.get("reply_count", 0),
            "similarity": None,
        })
    return out


# ========== 站内私信 ==========
@app.route("/messages")
@login_required
def messages_home():
    """消息中心：私信 / 系统通知 两个 Tab（tab 参数切换）"""
    tab = request.args.get("tab", "private")
    if tab == "system":
        notes = get_notifications(session["user_id"])
        mark_all_read(session["user_id"])
        return render_template("messages.html", active_tab="system", partners=[],
                               notes=notes)
    partners = get_conversation_partners(session["user_id"])
    return render_template("messages.html", active_tab="private", partners=partners,
                           notes=[])


@app.route("/messages/<int:user_id>")
@login_required
def message_thread(user_id):
    """与指定用户的对话页（越权防护：仅双方可看）"""
    if user_id == session["user_id"]:
        flash("不能与自己私信", "error")
        return redirect(url_for("messages_home"))
    # 校验对方存在且状态正常（私信对象校验）
    from user_service import get_user_detail
    other = get_user_detail(user_id)
    if not other:
        flash("用户不存在", "error")
        return redirect(url_for("messages_home"))
    messages = get_thread(session["user_id"], user_id)
    for m in messages:
        m["content_html"] = Markup(render_markdown(m["content"]))
        # 分享消息附带帖子卡片信息（标题/作者），供前端渲染可点击卡片；
        # 帖子已删除（软删/硬删）时标记 deleted=True，前端显示占位而非死链
        m["post_info"] = None
        if m.get("post_id"):
            try:
                _p = get_post_detail(m["post_id"])
                if _p:
                    m["post_info"] = {
                        "post_id": _p["post_id"],
                        "title": _p["title"],
                        "author": _p.get("author", ""),
                    }
                else:
                    m["post_info"] = {"post_id": m["post_id"], "deleted": True}
            except Exception:
                m["post_info"] = {"post_id": m["post_id"], "deleted": True}
    mark_thread_read(session["user_id"], user_id)
    return render_template("message_thread.html", other=other, messages=messages,
                           other_id=user_id)


@app.route("/api/message/share", methods=["POST"])
@login_required
@not_banned
def api_message_share():
    """分享帖子给好友（本质是带 post_id 的私信）：
    CSRF 全局校验 + 私信限速 + 必须好友（互相关注）+ 仅公开帖 + 非拉黑。
    通知 type=share（带 post_id，点击跳转帖子）。"""
    receiver_id = request.form.get("receiver_id", type=int)
    post_id = request.form.get("post_id", type=int)
    if not receiver_id or not post_id:
        return {"ok": False, "msg": "参数缺失"}, 400
    if receiver_id == session["user_id"]:
        return {"ok": False, "msg": "不能分享给自己"}, 400
    # 接收者存在且未被封禁（基础校验）
    receiver = get_user_detail(receiver_id)
    if not receiver:
        return {"ok": False, "msg": "用户不存在"}, 404
    if is_user_banned(receiver_id)[0]:
        return {"ok": False, "msg": "对方账号已被封禁，无法分享"}, 400
    # ① 拉黑检查（双向）：被拉黑的人绝不能收到分享
    if is_blocked_rel(session["user_id"], receiver_id) or is_blocked_rel(receiver_id, session["user_id"]):
        return {"ok": False, "msg": "对方已屏蔽你或你已屏蔽对方"}, 403
    # ② 好友检查：必须是互相关注
    if not is_friend_rel(session["user_id"], receiver_id):
        return {"ok": False, "msg": "只能分享给好友"}, 403
    # ③ 帖子可见性：必须存在且为公开帖（私密帖不可分享）
    post = get_post_detail(post_id)
    if not post:
        return {"ok": False, "msg": "帖子不存在"}, 404
    if post["is_private"]:
        return {"ok": False, "msg": "私密帖不能分享"}, 403
    # ④ 私信限速（通过前置校验后才计次，避免无效请求刷额度）
    if _message_limiter.hit(session["user_id"]):
        return {"ok": False, "msg": "发送过于频繁，请稍后再试"}, 429
    try:
        content = "分享帖子：《%s》" % post["title"]
        msg_id = send_message(session["user_id"], receiver_id, content, post_id)
    except Exception as e:
        print(f"[分享] 落库失败：{e}")
        return {"ok": False, "msg": "分享失败，请稍后再试"}, 500
    # 通知 + 实时推送
    try:
        add_notification(receiver_id, "share", post_id, session["user_id"],
                         f"{session['nickname']} 给你分享了一篇帖子：《{post['title'][:20]}》")
        # 实时推送只走 new_message 事件（同私信，避免系统通知红点污染）
        socketio.emit("new_message", {
            "message_id": msg_id,
            "from_user_id": session["user_id"],
            "from_nickname": session["nickname"],
            "content": content,
            "created_at": "",
            "post_id": post_id,
        }, room=f"user_{receiver_id}")
    except Exception as e:
        print(f"[分享] 通知/推送失败：{e}")
    return {"ok": True, "msg": "分享成功", "message_id": msg_id, "post_id": post_id}


@app.route("/api/friends/list", methods=["POST"])
@login_required
def api_friends_list():
    """当前用户好友分页列表（分享弹窗按需拉取）。
    POST + CSRF（全局 before_request 校验）；LIMIT/OFFSET 参数化；仅返回本人好友。"""
    page = max(1, request.form.get("page", 1, type=int))
    page_size = min(50, max(1, request.form.get("page_size", 20, type=int)))
    total, users = get_friends_list(session["user_id"], page, page_size)
    friends = [{
        "user_id": u["user_id"],
        "nickname": u["nickname"] or u["username"],
        "username": u["username"],
        "avatar_path": u["avatar_path"],
        "friend_at": u["friend_at"],
    } for u in users]
    return {"ok": True, "friends": friends, "total": total,
            "page": page, "page_size": page_size}


@app.route("/api/messages/unread-count")
@login_required
def api_messages_unread():
    return {"ok": True, "unread": unread_message_count(session["user_id"])}


@app.route("/api/messages/send", methods=["POST"])
@login_required
@not_banned
def api_messages_send():
    """发送私信：CSRF 全局校验 + 限速 + 参数化 + 实时推送"""
    receiver_id = request.form.get("receiver_id", type=int)
    content = (request.form.get("content") or "").strip()[:2000]
    if not receiver_id or receiver_id == session["user_id"]:
        return {"ok": False, "msg": "无效的接收者"}, 400
    if not content:
        return {"ok": False, "msg": "私信内容不能为空"}, 400
    if _message_limiter.hit(session["user_id"]):
        return {"ok": False, "msg": "发送过于频繁，请稍后再试"}, 429
    from user_service import get_user_detail
    receiver = get_user_detail(receiver_id)
    if not receiver:
        return {"ok": False, "msg": "用户不存在"}, 404
    # 封禁用户不能接收私信（接收方封禁时拒绝，防止骚扰被封禁用户）
    if is_user_banned(receiver_id)[0]:
        return {"ok": False, "msg": "对方账号已被封禁，无法发送私信"}, 400
    # 社交权限收紧：拉黑 / 好友 / 单向关注（后端强制，前端仅提示）
    from message_service import check_message_permission
    perm_ok, perm_msg = check_message_permission(session["user_id"], receiver_id)
    if not perm_ok:
        return {"ok": False, "msg": perm_msg}, 403
    try:
        msg_id = send_message(session["user_id"], receiver_id, content)
    except Exception as e:
        print(f"[私信] 发送失败：{e}")
        return {"ok": False, "msg": "发送失败，请稍后再试"}, 500
    # 站内通知 + 实时推送（接收方在线则立即收到）
    try:
        add_notification(receiver_id, "message", None, session["user_id"],
                         f"{session['nickname']} 给你发来一条私信：{content[:30]}")
        # 实时推送只走 new_message 事件（notification 事件仅供系统通知类型，
        # 避免 message/share 污染系统通知红点与总未读重复计数）
        socketio.emit("new_message", {
            "message_id": msg_id,
            "from_user_id": session["user_id"],
            "from_nickname": session["nickname"],
            "content": content,
            "created_at": "",
        }, room=f"user_{receiver_id}")
    except Exception as e:
        print(f"[私信] 通知/推送失败：{e}")
    return {"ok": True, "msg": "发送成功", "message_id": msg_id}


# ========== 标签：前台列表页 + API ==========
@app.route("/tag/<int:tag_id>")
def tag_posts(tag_id):
    """某标签下的文章列表（分页）；只展示公开帖"""
    if "user_id" not in session:
        return redirect(url_for("login"))
    tag = get_tag_by_id(tag_id)
    if not tag:
        flash("标签不存在或已被删除", "error")
        return redirect(url_for("index"))
    page = request.args.get("page", 1, type=int)
    if page < 1:  # 防止 page=0/负数生成负 OFFSET 导致 SQL 报错
        page = 1
    posts, total, total_pages = get_posts_by_tag(tag_id, page)
    return render_template("tag_posts.html", tag=tag, posts=posts,
                           total=total, page=page, total_pages=total_pages)


@app.route("/api/tags")
@login_required
def api_tags():
    """全部标签简表（发帖页"选择已有标签"组件初始化）"""
    return {"results": get_all_tags_simple()}


@app.route("/api/tags/search")
@login_required
def api_tags_search():
    """标签搜索建议（发帖页输入新标签时提示已有标签，避免重复创建）"""
    q = request.args.get("q", "").strip()
    return {"results": search_tags_suggest(q)}


@app.route("/api/ask")
@login_required
def api_ask():
    """AI问答API，返回JSON"""
    q = request.args.get("question", "").strip()
    if not q:
        return {"answer": ""}
    result = generate_answer(q)
    return {"answer": result.get("answer", "")}


@app.route("/api/ai/recommend_tags", methods=["POST"])
@login_required
def api_ai_recommend_tags():
    """AI 标签推荐：接收 title/content，返回候选标签列表子集（JSON）。
    - CSRF 由全局 before_request 校验（fetch 携带 _csrf_token）；
    - 限速：每分钟 5 次 / 每天 50 次（AI 调用有成本）；
    - 输入截断（标题 300 字、正文 1000 字），防 prompt 注入与 token 消耗；
    - AI 失败/超时/非法 JSON → 降级返回空列表，不 500。"""
    uid = session["user_id"]
    if _ai_tag_limiter_min.hit(uid) or _ai_tag_limiter_day.hit("d:%s" % uid):
        return jsonify({"ok": False, "msg": "AI 推荐调用过于频繁，请稍后再试", "tags": []}), 429
    title = (request.form.get("title") or "").strip()[:300]
    content = (request.form.get("content") or "").strip()[:1000]
    if not title and not content:
        return jsonify({"ok": False, "msg": "请先输入标题或内容", "tags": []})
    tags = ai_recommend_tags(title, content)
    return jsonify({"ok": True, "tags": tags, "msg": ""})


# ========== AI 问答 ==========
@app.route("/ask", methods=["GET", "POST"])
@login_required
def ai_ask():
    answer = None
    if request.method == "POST":
        query = request.form.get("question", "").strip()
        if query:
            result = generate_answer(query)
            answer = result
    return render_template("ask.html", answer=answer)


# ========== 管理员后台 ==========
@app.route("/admin")
@admin_required
def admin():
    tab = request.args.get("tab", "posts")
    log_audit(session["user_id"], "view_admin", "admin_page", None, detail=f"tab={tab}")
    if tab == "users":
        data = get_all_users()
        return render_template("admin.html", tab=tab, users=data,
                               ai_reply_enabled=_ai_reply_flag(),
                               ai_reply_daily_limit=_get_setting("ai_reply_daily_limit", "50"))
    elif tab == "comments":
        data = get_all_comments()
        return render_template("admin.html", tab=tab, comments=data,
                               ai_assistant_id=get_ai_assistant_id())
    elif tab == "reports":
        page = request.args.get("page", 1, type=int)
        if page < 1:  # 防 page=0/负数生成负 OFFSET
            page = 1
        page_size = 20
        tickets, total = get_pending_tickets(page=page, page_size=page_size)
        total_pages = max(1, (total + page_size - 1) // page_size)
        if page > total_pages:
            page = total_pages
            tickets, total = get_pending_tickets(page=page, page_size=page_size)
        return render_template("admin.html", tab=tab, tickets=tickets,
                               page=page, total_pages=total_pages, total=total)
    else:
        posts = admin_get_all_posts()
        return render_template("admin.html", tab=tab, posts=posts)


@app.route("/admin/user/<int:user_id>/toggle", methods=["POST"])
@admin_required
def admin_toggle_user(user_id):
    new_status, msg = toggle_user_status(user_id)
    log_audit(session["user_id"], "toggle_user_status", "user", user_id, detail=f"new_status={new_status}")
    return redirect(url_for("admin", tab="users"))


@app.route("/admin/user/<int:user_id>/ban", methods=["POST"])
@login_required
@admin_required
def admin_ban_user(user_id):
    """封禁用户：原因必填，时长 1/7/30 天或永久；审计 + 站内通知被封用户"""
    reason = request.form.get("reason", "").strip()
    days = request.form.get("days", "").strip()
    success, msg = ban_user(user_id, reason, days, session["user_id"])
    if success:
        log_audit(
            session["user_id"], "ban_user", "user", user_id,
            detail=f"reason={reason};days={days or 'forever'};operator={session['user_id']}",
        )
        try:
            add_notification(
                user_id=user_id, notify_type="ban", post_id=None,
                from_user_id=session["user_id"],
                content=f"你的账号已被封禁，原因：{reason}",
            )
        except Exception as e:
            print(f"[封禁通知失败] {e}")
        flash("封禁成功", "success")
    else:
        flash(msg, "error")
    return redirect(url_for("admin", tab="users"))


@app.route("/admin/user/<int:user_id>/unban", methods=["POST"])
@login_required
@admin_required
def admin_unban_user(user_id):
    """解封用户：恢复 status=0 并清空封禁信息；审计"""
    success, msg = unban_user(user_id, session["user_id"])
    if success:
        log_audit(
            session["user_id"], "unban_user", "user", user_id,
            detail=f"operator={session['user_id']}",
        )
        # 补全双向通知：后台直接解禁也通知用户（与申诉解封 handle_appeal 通知口径一致）
        add_notification(
            user_id=user_id, notify_type="system",
            post_id=None, from_user_id=session["user_id"],
            content="你的账号已解封，请重新登录。",
        )
        flash("解封成功", "success")
    else:
        flash(msg, "error")
    return redirect(url_for("admin", tab="users"))


@app.route("/admin/ai-reply/toggle", methods=["POST"])
@login_required
@admin_required
def admin_ai_reply_toggle():
    """AI 智能回复全局开关：enabled=1 开启 / 0 关闭；daily_limit 可调每日上限。审计。"""
    enabled = request.form.get("enabled", "")
    if enabled not in ("1", "0"):
        flash("参数不合法", "error")
        return redirect(url_for("admin", tab="users"))
    _set_setting("ai_reply_enabled", enabled)
    log_audit(
        session["user_id"], "toggle_ai_reply", "setting", None,
        detail=f"ai_reply_enabled={enabled}",
    )
    # 可选：同步设置每日上限（正整数或 0=禁用）
    daily_limit = request.form.get("daily_limit", "").strip()
    if daily_limit:
        try:
            limit = int(daily_limit)
            if 0 <= limit <= 100000:
                _set_setting("ai_reply_daily_limit", str(limit))
                log_audit(
                    session["user_id"], "toggle_ai_reply", "setting", None,
                    detail=f"ai_reply_daily_limit={limit}",
                )
            else:
                flash("每日上限需为 0-100000 的整数", "error")
                return redirect(url_for("admin", tab="users"))
        except (TypeError, ValueError):
            flash("每日上限格式不合法", "error")
            return redirect(url_for("admin", tab="users"))
    flash("AI 智能回复已开启" if enabled == "1" else "AI 智能回复已关闭", "success")
    return redirect(url_for("admin", tab="users"))


@app.route("/admin/post/<int:post_id>/toggle/<field>", methods=["POST"])
@admin_required
def admin_toggle_post(post_id, field):
    if field == "top":
        toggle_top(post_id, session["user_id"])
        log_audit(session["user_id"], "toggle_post_top", "post", post_id)
    elif field == "essence":
        toggle_essence(post_id, session["user_id"])
        log_audit(session["user_id"], "toggle_post_essence", "post", post_id)
    return redirect(url_for("admin", tab="posts"))


@app.route("/admin/post/<int:post_id>")
@admin_required
def admin_post_detail(post_id):
    """后台帖子审核预览（脱敏）：
    - 公开帖正常显示作者；私密帖"保密"、匿名帖"匿名用户"；
    - 任何帖子都不下发 author_id；私密/匿名帖不暴露真实昵称；
    - 查看私密/匿名帖详情必须记录审计（action=view_post_detail）。
    操作区仅保留删除，不提供"封禁作者"（需去用户管理手动操作，权限隔离）。"""
    post = get_post_for_review(post_id)
    if not post:
        flash("帖子不存在或已被删除", "error")
        return redirect(url_for("admin", tab="posts"))
    if post["is_private"] or post["is_anonymous"]:
        log_audit(
            session["user_id"], "view_post_detail", "post", post_id,
            detail="查看%s帖详情" % ("私密" if post["is_private"] else "匿名"))
    # 正文以 Markdown 渲染 + XSS 白名单过滤后展示（与前台一致的安全渲染）
    post["content"] = Markup(render_markdown(post["content"]))
    return render_template("admin_post_detail.html", post=post)


@app.route("/admin/post/<int:post_id>/delete", methods=["POST"])
@admin_required
def admin_delete_post_route(post_id):
    success, msg = admin_delete_post(post_id, session["user_id"])
    if success:
        log_audit(session["user_id"], "delete_post", "post", post_id)
        # 实时广播：在线用户的页面同步移除该帖
        _broadcast_delete_post(post_id)
        # 语义搜索索引：从向量库移除
        gevent.spawn(sem.remove_post, post_id)
    return redirect(url_for("admin", tab="posts"))


@app.route("/admin/comment/<int:comment_id>/delete", methods=["POST"])
@admin_required
def admin_delete_comment_route(comment_id):
    success, msg = delete_comment(comment_id)
    if success:
        log_audit(session["user_id"], "delete_comment", "comment", comment_id)
        # 实时广播：在线用户的详情页同步移除该评论
        _broadcast_delete_comment(comment_id)
    return redirect(url_for("admin", tab="comments"))


@app.route("/admin/comment/<int:comment_id>/revoke-ai", methods=["POST"])
@admin_required
def admin_revoke_ai_reply(comment_id):
    """撤回 AI 助手评论：删评论 + 删通知 + 重置 ai_replied=0。审计。"""
    success, msg, post_id = revoke_ai_reply(comment_id)
    if success:
        log_audit(
            session["user_id"], "revoke_ai_reply", "comment", comment_id,
            detail=f"post_id={post_id}")
        flash("已撤回 AI 回复", "success")
    else:
        flash(msg, "error")
    return redirect(url_for("admin", tab="comments"))


# ========== 管理后台 - 标签管理 ==========
@app.route("/admin/tags")
@admin_required
def admin_tags():
    """标签管理列表：分页 + 按名称搜索"""
    page = request.args.get("page", 1, type=int)
    if page < 1:
        page = 1
    q = request.args.get("q", "").strip()
    data = get_all_tags(page, keyword=q or None)
    return render_template("admin_tags.html", **data)


@app.route("/admin/tags/add", methods=["POST"])
@admin_required
def admin_tag_add():
    success, msg, _ = create_tag(request.form.get("tag_name", ""))
    log_audit(session["user_id"], "create_tag", "tag", None, detail=request.form.get("tag_name", "")[:100])
    flash(msg, "success" if success else "error")
    return redirect(url_for("admin_tags", q=request.form.get("q", "").strip()))


@app.route("/admin/tags/<int:tag_id>/edit", methods=["POST"])
@admin_required
def admin_tag_edit(tag_id):
    success, msg = update_tag(tag_id, request.form.get("tag_name", ""))
    log_audit(session["user_id"], "update_tag", "tag", tag_id, detail=request.form.get("tag_name", "")[:100])
    flash(msg, "success" if success else "error")
    return redirect(url_for("admin_tags", q=request.form.get("q", "").strip()))


@app.route("/admin/tags/<int:tag_id>/delete", methods=["POST"])
@admin_required
def admin_tag_delete(tag_id):
    """删除标签：有关联文章时 service 层拒绝并提示解除关联"""
    success, msg = delete_tag(tag_id)
    if success:
        log_audit(session["user_id"], "delete_tag", "tag", tag_id)
    flash(msg, "success" if success else "error")
    return redirect(url_for("admin_tags", q=request.form.get("q", "").strip()))


# ========== 管理后台 - 板块管理 ==========
@app.route("/admin/boards")
@admin_required
def admin_boards():
    """板块列表（含各板块帖子数）"""
    boards = get_all_boards()
    for b in boards:
        b["post_count"] = get_board_post_count(b["board_id"])
    return render_template("admin_boards.html", boards=boards)


@app.route("/admin/boards/create", methods=["POST"])
@admin_required
def admin_board_create():
    """新增板块"""
    success, msg = create_board(
        request.form.get("board_name", ""), request.form.get("description", ""))
    if success:
        log_audit(session["user_id"], "create_board", "board", None, detail=request.form.get("board_name", "")[:100])
    flash(msg, "success" if success else "error")
    return redirect(url_for("admin_boards"))


@app.route("/admin/boards/<int:board_id>/edit", methods=["POST"])
@admin_required
def admin_board_edit(board_id):
    """编辑板块"""
    success, msg = update_board(
        board_id, request.form.get("board_name", ""), request.form.get("description", ""))
    if success:
        log_audit(session["user_id"], "update_board", "board", board_id, detail=request.form.get("board_name", "")[:100])
    flash(msg, "success" if success else "error")
    return redirect(url_for("admin_boards"))


@app.route("/admin/boards/<int:board_id>/delete", methods=["POST"])
@admin_required
def admin_board_delete(board_id):
    """删除板块：有帖子时 service 层拒绝并提示数量"""
    success, msg = delete_board(board_id)
    if success:
        log_audit(session["user_id"], "delete_board", "board", board_id)
    flash(msg, "success" if success else "error")
    return redirect(url_for("admin_boards"))


# ========== 管理后台 - 板块申请审核 ==========
@app.route("/admin/board-applications")
@admin_required
def admin_board_applications():
    """申请列表：status 参数筛选（0待审核 1已通过 2已驳回，缺省全部）"""
    status = request.args.get("status", type=int)
    page = request.args.get("page", type=int) or 1
    data = get_applications(status=status, page=page)
    return render_template("admin_board_applications.html", **data)


@app.route("/admin/board-applications/<int:application_id>/approve", methods=["POST"])
@admin_required
def admin_application_approve(application_id):
    """通过申请：创建板块 + 通知申请人"""
    success, msg = review_application(application_id, session["user_id"], approve=True)
    log_audit(session["user_id"], "review_approve", "board_application", application_id, detail=msg[:100])
    flash(msg, "success" if success else "error")
    return redirect(url_for("admin_board_applications"))


@app.route("/admin/board-applications/<int:application_id>/reject", methods=["POST"])
@admin_required
def admin_application_reject(application_id):
    """驳回申请：必须填写原因 + 通知申请人"""
    success, msg = review_application(
        application_id, session["user_id"], approve=False,
        reject_reason=request.form.get("reject_reason", ""))
    log_audit(session["user_id"], "review_reject", "board_application", application_id,
              detail=request.form.get("reject_reason", "")[:100])
    flash(msg, "success" if success else "error")
    return redirect(url_for("admin_board_applications"))


# ========== 审核队列：举报 / 申诉工单处理 ==========
@app.route("/admin/report/<int:ticket_id>/handle", methods=["POST"])
@login_required
@admin_required
def admin_handle_report(ticket_id):
    """处理举报工单：action=approve(删内容) / approve_ban(删内容+封禁作者) / reject(驳回)"""
    action = request.form.get("action", "").strip()
    if action not in ("approve", "approve_ban", "reject"):
        flash("操作类型不合法", "error")
        return redirect(url_for("admin", tab="reports"))
    success, msg, ticket = handle_report(ticket_id, session["user_id"], action)
    if success and ticket:
        log_audit(
            session["user_id"], "handle_report", "report", ticket_id,
            detail=f"action={action};target={ticket['target_type']}:{ticket['target_id']};result={msg}")
        # 通知举报者（处理闭环）
        try:
            label = {
                "approve": "已通过并删除违规内容",
                "approve_ban": "已通过并删除违规内容、封禁发布者",
                "reject": "已驳回",
            }[action]
            add_notification(ticket["reporter_id"], "report", None,
                             session["user_id"], f"你的举报已处理：{label}")
        except Exception as e:
            print(f"[举报通知失败] {e}")
    flash(msg, "success" if success else "error")
    return redirect(url_for("admin", tab="reports"))


@app.route("/admin/appeal/<int:ticket_id>/handle", methods=["POST"])
@login_required
@admin_required
def admin_handle_appeal(ticket_id):
    """处理申诉工单：action=approve(解封) / reject(维持封禁)"""
    action = request.form.get("action", "").strip()
    if action not in ("approve", "reject"):
        flash("操作类型不合法", "error")
        return redirect(url_for("admin", tab="reports"))
    success, msg, ticket = handle_appeal(ticket_id, session["user_id"], action)
    if success and ticket:
        log_audit(
            session["user_id"], "handle_appeal", "report", ticket_id,
            detail=f"action={action};target_user={ticket['target_id']};result={msg}")
        # 通知申诉者（被封禁无法登录 → 通知落库，解封后可见）
        try:
            label = "申诉已通过，账号已解封" if action == "approve" else "申诉未通过，维持封禁"
            add_notification(ticket["target_id"], "appeal", None,
                             session["user_id"], label)
        except Exception as e:
            print(f"[申诉通知失败] {e}")
    flash(msg, "success" if success else "error")
    return redirect(url_for("admin", tab="reports"))


if __name__ == "__main__":
    ensure_default_boards()
    # AI 智能回复：确保系统账号存在 + 启动后台定时任务（每 5 分钟扫描一次）
    ensure_ai_assistant()

    def _ai_reply_loop():
        while True:
            gevent.sleep(300)  # 5 分钟
            try:
                scan_and_reply()
            except Exception as e:
                print(f"[AI助手] 定时任务异常（下次继续）: {e}")

    gevent.spawn(_ai_reply_loop)

    # 语义搜索预热：后台加载 embedding 模型 + 初始化向量存储，
    # 避免首次发帖/搜索触发 10s+ 同步模型加载导致服务器请求排队（偶发超时根因）
    gevent.spawn(sem.warmup)

    # 语义搜索：若向量库为空，启动时后台分批重建索引（不阻塞启动；分批+sleep 控资源）
    try:
        if sem.vector_store_ready() and sem.count() == 0:
            gevent.spawn(sem.rebuild_index)
    except Exception as e:
        print(f"[语义搜索] 启动重建跳过：{e}")
    # 仅监听本机回环地址，不对外网/局域网暴露
    # debug 可通过环境变量 FLASK_DEBUG=1 开启，默认关闭
    # 端口可通过环境变量 PORT 修改，默认 5000（被占用时：set PORT=5001 后启动）
    debug_mode = os.environ.get("FLASK_DEBUG", "0") == "1"
    port = int(os.environ.get("PORT", "5000"))
    # gevent 启动：async_mode="gevent" 下 socketio.run 内部即用
    # gevent.pywsgi.WSGIServer（挂载 SocketIO 中间件 + gevent-websocket 的
    # WebSocketHandler 做协议升级）。不直接裸起 WSGIServer——那样会绕过
    # SocketIO 中间件，丢失实时推送能力。reloader 与 monkey 补丁不兼容，强制关闭。
    socketio.run(app, host="127.0.0.1", port=port, debug=debug_mode, use_reloader=False)
