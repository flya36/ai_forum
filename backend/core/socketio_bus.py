# -*- coding: utf-8 -*-
"""SocketIO 共享实例（延迟绑定模式）。

web/app.py 与 backend/services 需共用同一 SocketIO 实例才能互推事件：
- app.py 以 `python web/app.py` 运行时模块名为 __main__，
  service 层 `from app import socketio` 会重新加载 app.py 副本（空实例，emit 静默无效）。
- 本模块持有唯一实例，app.py 通过 init_app 绑定应用，
  service 层通过 `from socketio_bus import socketio` 拿到同一实例。
"""
from flask_socketio import SocketIO

socketio = SocketIO()
