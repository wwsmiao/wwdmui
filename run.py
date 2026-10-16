#!/usr/bin/env python3
"""
ComfyUI 模型管理器 v4.0 — 入口脚本
用法: python run.py
"""

import sys, os, atexit, signal, platform, webbrowser, threading, time

# 确保项目根在 sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from wwdm_app import app
from wwdm_app.config import settings, BASE_DIR, DB_PATH, RUNTIME_INFO
from wwdm_app.services import aria2_start, cleanup_all


def _on_exit():
    """退出时强制清理所有子进程"""
    print("\n[v4.0] 正在清理子进程...")
    cleanup_all()
    print("[v4.0] 已退出")


atexit.register(_on_exit)
# 注册信号（Linux/macOS 都需要；Windows 只支持 SIGINT）
signal.signal(signal.SIGINT, lambda *_: sys.exit(0))
if hasattr(signal, "SIGTERM"):
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))


def _lan_ips():
    """尽力列出本机局域网 IPv4 地址（失败返回空列表，绝不影响启动）"""
    ips = []
    try:
        import socket
        sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sk.connect(("8.8.8.8", 80))    # UDP connect 不发包，只是问一下路由表
            ip = sk.getsockname()[0]
            if ip and not ip.startswith("127."):
                ips.append(ip)
        finally:
            sk.close()
    except Exception:
        pass
    return ips


if __name__ == "__main__":
    import platform as _p
    print("[v4.0] " + _p.system() + ", Python: " + sys.version.split()[0])
    print("[v4.0] BASE_DIR: " + BASE_DIR)
    print("[v4.0] DB: " + DB_PATH)
    if settings.get("auto_start_aria2", True):
        aria2_start()
    print("\n  === ComfyUI 模型管理器 v4.0 ===\n")

    # ---- 管理器访问（web_host / web_port 重启后生效）----
    host = (settings.get("web_host") or "0.0.0.0").strip() or "0.0.0.0"
    try:
        port = int(settings.get("web_port") or 7860)
    except (ValueError, TypeError):
        port = 7860
    if not (1024 <= port <= 65535):
        port = 7860
    token = (settings.get("access_token") or "").strip()
    token_on = bool(settings.get("token_required", True)) and bool(token)

    # 记录本进程真实监听的地址/端口，供设置页显示「有改动待重启生效」
    lan_ips = _lan_ips()
    RUNTIME_INFO.update({"host": host, "port": port, "lan_ips": lan_ips,
                         "started_at": time.time(), "token_enabled": token_on})

    print("  本机访问: http://127.0.0.1:%d" % port)
    if host == "0.0.0.0":
        for _ip in lan_ips:
            print("  局域网访问: http://%s:%d%s"
                  % (_ip, port, ("/?t=" + token) if token_on else ""))
    else:
        print("  已设为仅本机访问（可到管理器「设置 → 管理器访问」改成局域网可访问）")
    if token_on:
        print("  访问令牌: " + token)
    print("  提示: 改监听地址/端口需要重启管理器；访问令牌改动立即生效\n")

    # 自动打开浏览器（延迟 1.5s 等待 Flask 就绪）
    def _open_browser():
        time.sleep(1.5)
        webbrowser.open("http://127.0.0.1:%d" % port)
    threading.Thread(target=_open_browser, daemon=True).start()
    try:
        # threaded=True：Werkzeug 默认单线程串行，一个慢请求（扫盘统计 / 上传大模型 /
        # 拉取 ComfyUI 日志）会把其它请求一起掐断，浏览器表现为偶发 ERR_CONNECTION_RESET。
        # 开多线程后并发请求各走自己的线程，ComfyUI 运行期间界面也不再卡死。
        app.run(host=host, port=port, debug=False, threaded=True)
    except OSError as e:
        code = getattr(e, "winerror", None) or getattr(e, "errno", None)
        if code in (10048, 98, 48):        # Windows / Linux / macOS 的「端口被占用」
            print("\n  [ERROR] 端口 %d 已被占用，管理器没能启动。" % port)
            print("  处理办法：关掉占用该端口的程序，或者在「设置 → 管理器访问」里"
                  "换一个端口再重启。")
            print("  查占用：netstat -ano | findstr :%d" % port)
        else:
            print("\n  [ERROR] 启动失败：%s" % e)
        sys.exit(1)
    finally:
        _on_exit()
