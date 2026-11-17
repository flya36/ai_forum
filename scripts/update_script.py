# -*- coding: utf-8 -*-
import io
p = r'E:\项目\论坛\ai_forum\答辩讲解稿_视频版.md'
with io.open(p, 'r', encoding='utf-8') as f:
    c = f.read()

old = """），并开两个浏览器窗口分别登录两个测试账号。"""
new = """），并开两个浏览器窗口分别登录两个测试账号。

### 录制前必做检查（5 分钟，否则演示会翻车）

1. **硬刷新两个浏览器窗口**：Ctrl+Shift+R，强制加载修复后的 badge 元素（之前初始未读为 0 时红点 DOM 不存在，Socket 推送无法更新）。
2. **管理员窗口**：用 `fly` 账号登录（role=1），停留在任意页面（如 `/admin?tab=reports`），不要关掉。
3. **普通用户 A、B 关系必须是"单向关注"**：录制前在数据库里清掉两人之间的关注和私信记录，确保：
   - A 关注了 B，但 B 没关注 A
   - A、B 之间没有历史私信
   - 这样 Step 3 的"第一条成功、第二条被拒"才能演示出来。
4. **验证 Socket 连接**：F12 → Network → WS，确认有一条 `socket.io/?EIO=4...` 连接，状态 101 Switching Protocols。没有这条连接，实时推送不会到。"""

c = c.replace(old, new)
with io.open(p, 'w', encoding='utf-8', newline='') as f:
    f.write(c)
print('OK')
