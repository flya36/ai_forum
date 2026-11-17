# -*- coding: utf-8 -*-
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend', 'core'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend', 'config'))
from db import get_cursor
with get_cursor() as c:
    c.execute("SELECT user_id, username, nickname, role, status FROM `user` WHERE username IN ('fly','flya36','fqq')")
    for r in c.fetchall():
        print(r)
