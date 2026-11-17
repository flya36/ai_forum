# -*- coding: utf-8 -*-
"""修复 index.html 和 messages.html 中条件渲染的 badge。"""
import io, re

# 1. index.html 桌面侧边栏
p1 = r'E:\项目\论坛\ai_forum\web\templates\index.html'
with io.open(p1, 'r', encoding='utf-8') as f:
    c1 = f.read()

old1 = '''<a href="{{ url_for('messages_home') }}" class="list-group-item list-group-item-action">
                    <i class="bi bi-envelope nav-icon"></i> 我的消息
                    {% if unread_msg_count + unread_count > 0 %}
                    <span class="badge bg-danger rounded-pill ms-1 msg-total-badge">{{ unread_msg_count + unread_count }}</span>
                    {% endif %}
                </a>'''
new1 = '''<a href="{{ url_for('messages_home') }}" class="list-group-item list-group-item-action">
                    <i class="bi bi-envelope nav-icon"></i> 我的消息
                    <span class="badge bg-danger rounded-pill ms-1 msg-total-badge"{% if unread_msg_count + unread_count <= 0 %} style="display:none"{% endif %}>{{ unread_msg_count + unread_count }}</span>
                </a>'''
c1 = c1.replace(old1, new1)
with io.open(p1, 'w', encoding='utf-8', newline='') as f:
    f.write(c1)
print('index.html 修复:', c1.count('msg-total-badge'), '个 badge')

# 2. messages.html 私信 Tab 的 msg-unread-badge 也改无条件
p2 = r'E:\项目\论坛\ai_forum\web\templates\messages.html'
with io.open(p2, 'r', encoding='utf-8') as f:
    c2 = f.read()
# 看一下原文
print('--- messages.html 原文片段 ---')
for i, line in enumerate(c2.split('\n'), 1):
    if 'badge' in line.lower() or 'unread' in line.lower():
        print(f'  {i}: {line}')
