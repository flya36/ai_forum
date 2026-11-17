# -*- coding: utf-8 -*-
# ============================================================
# 敏感词库管理脚本（增删查，热重载仅对下次请求生效的进程内缓存）
# 用法（项目根目录）：
#   venv\Scripts\python.exe backend\scripts\manage_sensitive_words.py list
#   venv\Scripts\python.exe backend\scripts\manage_sensitive_words.py add 新词
#   venv\Scripts\python.exe backend\scripts\manage_sensitive_words.py remove 词
# 说明：修改后需重启应用（或调用 word_filter.reload()）使运行中的进程生效。
# 本脚本不提供 Web 接口，敏感词库对普通用户不可读/不可改。
# ============================================================
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WORD_FILE = os.path.join(BASE, "backend", "config", "sensitive_words.txt")


def _read():
    if not os.path.exists(WORD_FILE):
        return []
    with open(WORD_FILE, encoding="utf-8") as f:
        return [ln.strip() for ln in f]


def _write(lines):
    with open(WORD_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + ("\n" if lines else ""))


def main():
    args = sys.argv[1:]
    if not args or args[0] == "list":
        words = [w for w in _read() if w and not w.startswith("#")]
        print("当前敏感词（%d 条）：" % len(words))
        for w in words:
            print("  -", w)
        return

    cmd = args[0]
    if cmd == "add" and len(args) >= 2:
        word = args[1].strip()
        if not word:
            print("词不能为空")
            return 1
        words = [w for w in _read() if w and not w.startswith("#")]
        if word in words:
            print("已存在：%s" % word)
            return
        words.append(word)
        _write(words)
        print("已添加：%s（重启应用后生效）" % word)
        return

    if cmd == "remove" and len(args) >= 2:
        word = args[1].strip()
        words = [w for w in _read() if w and not w.startswith("#")]
        if word not in words:
            print("不存在：%s" % word)
            return 1
        words = [w for w in words if w != word]
        _write(words)
        print("已移除：%s（重启应用后生效）" % word)
        return

    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main())
