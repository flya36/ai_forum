# -*- coding: utf-8 -*-
"""实测 SocketIO 推送：
1. 以 B 身份连接 socket，监听 new_message / new_ticket_alert
2. 用 requests 以 A 身份登录并发私信
3. 看 B 的 socket 是否在 3 秒内收到事件
"""
import time, threading, requests, socketio

BASE = 'http://127.0.0.1:5000'
sio = socketio.Client(logger=False, engineio_logger=False)

received = []

@sio.on('new_message')
def on_msg(data):
    received.append(('new_message', data))
    print('[B 收到 new_message]', data)

@sio.on('new_ticket_alert')
def on_ticket(data):
    received.append(('new_ticket_alert', data))
    print('[B 收到 new_ticket_alert]', data)

@sio.on('notification')
def on_notif(data):
    print('[B 收到 notification]', data)

@sio.on('connect')
def on_connect():
    print('[B] Socket 已连接 sid=', sio.sid)

@sio.on('connect_error')
def on_err(e):
    print('[B] 连接错误:', e)

@sio.on('disconnect')
def on_disc():
    print('[B] 断开')

# 1. B 先登录拿 cookie
sB = requests.Session()
r = sB.post(f'{BASE}/login', data={'username': 'fqq', 'password': 'test123'}, allow_redirects=False)
print('[B] 登录状态:', r.status_code, 'cookie:', list(sB.cookies.keys()))

# 2. B 用这个 cookie 连 socket
# socketio-client 需要带 cookie
headers = {
    'Cookie': '; '.join(f'{k}={v}' for k, v in sB.cookies.items())
}
sio.connect(BASE, headers=headers, transports=['websocket'])
time.sleep(1)

# 3. A 登录并发私信给 B
sA = requests.Session()
r = sA.post(f'{BASE}/login', data={'username': 'flya36', 'password': 'test123'}, allow_redirects=False)
print('[A] 登录状态:', r.status_code)

# 查 B 的 user_id
r = sB.get(f'{BASE}/api/me')
print('[B] /api/me:', r.status_code, r.text[:200])

# 发私信
import json
r = sA.post(f'{BASE}/api/message/send', json={
    'receiver_id': 2,  # 改成 B 的真实 id
    'content': 'socket 推送测试 ' + str(int(time.time()))
})
print('[A] 发私信:', r.status_code, r.text[:200])

# 等 3 秒看 B 是否收到
time.sleep(3)
print('\n=== B 收到的事件 ===')
for ev in received:
    print(' -', ev[0], ev[1])

sio.disconnect()
