# -*- coding: utf-8 -*-
"""修复 messages.html 里两个条件渲染的 badge。"""
import io

p = r'E:\项目\论坛\ai_forum\web\templates\messages.html'
with io.open(p, 'r', encoding='utf-8') as f:
    c = f.read()

# 私信 Tab badge
c = c.replace(
    '{% if unread_msg_count > 0 %}<span class="badge bg-danger rounded-pill ms-1 msg-unread-badge">{{ unread_msg_count }}</span>{% endif %}',
    '<span class="badge bg-danger rounded-pill ms-1 msg-unread-badge"{% if unread_msg_count <= 0 %} style="display:none"{% endif %}>{{ unread_msg_count }}</span>'
)

# 系统通知 Tab badge
c = c.replace(
    '{% if unread_count > 0 %}<span class="badge bg-danger rounded-pill ms-1 msg-notice-badge">{{ unread_count }}</span>{% endif %}',
    '<span class="badge bg-danger rounded-pill ms-1 msg-notice-badge"{% if unread_count <= 0 %} style="display:none"{% endif %}>{{ unread_count }}</span>'
)

with io.open(p, 'w', encoding='utf-8', newline='') as f:
    f.write(c)

# 校验
remain = c.count('{% if unread')
print('messages.html 条件渲染残留:', remain)
print('msg-unread-badge:', c.count('msg-unread-badge'))
print('msg-notice-badge:', c.count('msg-notice-badge'))
