# -*- coding: utf-8 -*-
# ============================================================
# 通用滑动窗口限速器（内存版）
# 用于：改密尝试、举报提交、申诉提交等防滥用场景。
# 设计：
#   - 固定窗口内最多 max_hits 次尝试，窗口外时间戳自动清理（防内存无限增长）
#   - 单进程 gevent 部署适用；多进程部署需换 Redis 等共享存储（见 app.py 注释）
#   - 用法：limiter = SlidingWindowLimiter(5, 60); if limiter.hit(key): 拒绝
# ============================================================
import time


class SlidingWindowLimiter:
    def __init__(self, max_hits, window_seconds):
        self.max_hits = max_hits
        self.window = window_seconds
        self._hits = {}   # key -> [timestamp, ...]

    def hit(self, key):
        """记录一次尝试；返回 True 表示该主体已超限（应拒绝）"""
        now = time.time()
        q = self._hits.setdefault(key, [])
        # 清理窗口外的旧时间戳
        self._hits[key] = q = [t for t in q if now - t <= self.window]
        if len(q) >= self.max_hits:
            return True
        q.append(now)
        return False

    def reset(self):
        """清空所有窗口计数（仅测试钩子使用；生产不暴露）"""
        self._hits.clear()
