#!/usr/bin/env python3
"""
ComfyUI 模型管理器 v3.8 — 入口脚本
用法: python run.py
"""

import sys, os, atexit, signal, platform, webbrowser, threading, time

# 确保项目根在 sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from wwdm_app import app
from wwdm_app.config import settings, BASE_DIR, DB_PATH
from wwdm_app.services import aria2_start, cleanup_all


def _on_exit():
    """退出时强制清理所有子进程"""
    print("\n[v3.8] 正在清理子进程...")
    cleanup_all()
    print("[v3.8] 已退出")


atexit.register(_on_exit)
# 注册信号（Linux/macOS 都需要；Windows 只支持 SIGINT）
signal.signal(signal.SIGINT, lambda *_: sys.exit(0))
if hasattr(signal, "SIGTERM"):
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))


if __name__ == "__main__":
    import platform as _p
    print("[v3.8] " + _p.system() + ", Python: " + sys.version.split()[0])
    print("[v3.8] BASE_DIR: " + BASE_DIR)
    print("[v3.8] DB: " + DB_PATH)
    if settings.get("auto_start_aria2", True):
        aria2_start()
    print("\n  === ComfyUI 模型管理器 v3.8 ===\n  访问: http://127.0.0.1:7860\n")
    # 自动打开浏览器（延迟 1.5s 等待 Flask 就绪）
    def _open_browser():
        time.sleep(1.5)
        webbrowser.open("http://127.0.0.1:7860")
    threading.Thread(target=_open_browser, daemon=True).start()
    try:
        app.run(host="0.0.0.0", port=7860, debug=False)
    finally:
        _on_exit()
