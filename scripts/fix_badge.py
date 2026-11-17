# -*- coding: utf-8 -*-
"""把 base.html 里三处条件渲染的 badge 改成无条件渲染（初始 display:none），
让 Socket 推送时 querySelectorAll 能找到元素更新红点。"""
import io, re

p = r'E:\项目\论坛\ai_forum\web\templates\base.html'
with io.open(p, 'r', encoding='utf-8') as f:
    c = f.read()

# 三处条件渲染 -> 无条件渲染
# 通用模式：{% if unread_msg_count + unread_count > 0 %}\n ... <span ... msg-total-badge ...>...</span>\n {% endif %}
# 直接把 if 包裹去掉，span 加初始 display:none

# 1) 下拉菜单"我的消息"
c = c.replace(
    '''<li><a class="dropdown-item" href="{{ url_for('messages_home') }}">我的消息
                        {% if unread_msg_count + unread_count > 0 %}
                        <span class="badge bg-danger rounded-pill ms-1 msg-total-badge">{{ unread_msg_count + unread_count }}</span>
                        {% endif %}</a></li>''',
    '''<li><a class="dropdown-item" href="{{ url_for('messages_home') }}">我的消息
                        <span class="badge bg-danger rounded-pill ms-1 msg-total-badge"{% if unread_msg_count + unread_count <= 0 %} style="display:none"{% endif %}>{{ unread_msg_count + unread_count }}</span></a></li>'''
)

# 2) 移动端侧边栏
c = c.replace(
    '''<a class="list-group-item list-group-item-action" href="{{ url_for('messages_home') }}"><i class="bi bi-bell nav-icon"></i> 我的消息
                {% if unread_msg_count + unread_count > 0 %}
                <span class="badge bg-danger rounded-pill ms-1 msg-total-badge">{{ unread_msg_count + unread_count }}</span>
                {% endif %}
            </a>''',
    '''<a class="list-group-item list-group-item-action" href="{{ url_for('messages_home') }}"><i class="bi bi-bell nav-icon"></i> 我的消息
                <span class="badge bg-danger rounded-pill ms-1 msg-total-badge"{% if unread_msg_count + unread_count <= 0 %} style="display:none"{% endif %}>{{ unread_msg_count + unread_count }}</span>
            </a>'''
)

# 3) 移动端底部导航
c = c.replace(
    '''<i class="bi bi-bell"></i><span>消息</span>
        {% if unread_msg_count + unread_count > 0 %}
        <span class="badge bg-danger rounded-pill msg-total-badge">{{ unread_msg_count + unread_count }}</span>
        {% endif %}''',
    '''<i class="bi bi-bell"></i><span>消息</span>
        <span class="badge bg-danger rounded-pill msg-total-badge"{% if unread_msg_count + unread_count <= 0 %} style="display:none"{% endif %}>{{ unread_msg_count + unread_count }}</span>'''
)

# JS 版本号 +1.5.0
c = c.replace("js/site-base.js') }}?v=1.4.0", "js/site-base.js') }}?v=1.5.0")

with io.open(p, 'w', encoding='utf-8', newline='') as f:
    f.write(c)

# 校验
remain = len(re.findall(r'unread_count > 0', c))
total = len(re.findall(r'msg-total-badge"', c))
print('条件渲染残留:', remain)
print('无条件 badge 数:', total)
