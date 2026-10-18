# -*- coding: utf-8 -*-
"""任务中心 tkinter 窗口（v6.0 批次：把网页任务中心搬到 exe 悬浮球/托盘菜单）。

设计约束（同 fab.py / tray.py，铁律不可破）
------------------------------------------
  * 本模块**顶层不碰 tkinter**、不 import services/blueprints —— 任务数据走
    回环 HTTP 打 ``GET /api/tasks``（读）/ ``DELETE /api/tasks``（清终态），
    与 actions.quit_app 同一套禁代理 urllib 范式。
  * 窗口**懒建**（菜单点击才建），且**单实例**（重复点击只前置已开的窗）。
  * HTTP 在 **worker 线程**里跑，结果用 ``root.after(0, fn)`` 回写 UI
    —— 绝不在 worker 线程里直接碰 tkinter 控件，也绝不在 ctypes WndProc 里碰。
  * 服务没起来：请求返回 None，UI 显示「服务未启动或无法连接」并停轮询。
"""
import threading

from . import theme

_REFRESH_MS = 1500


def _http_json(host, port, path, method="GET"):
    """回环 GET/DELETE 取 JSON（禁代理）。失败返回 None。"""
    import json
    import urllib.request
    url = "http://%s:%d%s" % (host, port, path)
    req = urllib.request.Request(url, method=method,
                                 headers={"Content-Type": "application/json"})
    if method == "POST":
        req.data = json.dumps({}).encode("utf-8")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=3) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:
        return None


class _TasksWindow:
    """一个 host:port 对应一个任务中心窗口（由模块级 _WINS 做单实例）。"""

    def __init__(self, host, port, root):
        self.host = host
        self.port = int(port)
        self.root = root
        self.win = None
        self._tree = None
        self._after_id = None
        self._alive = False

    # ---------------- 开 / 关 ----------------
    def open(self):
        import tkinter as tk
        from tkinter import ttk
        if self.win is not None and self.win.winfo_exists():
            self.win.lift()
            self.win.focus_force()
            return
        win = tk.Toplevel(self.root)
        self.win = win
        win.title("任务中心")
        win.geometry("560x360")
        theme.apply_style(self.root)        # 确保 ttk 样式就位（幂等）
        try:
            win.attributes("-topmost", True)
        except Exception:
            pass
        try:
            from . import icoimage
            win.iconbitmap(icoimage.default_icon_path())   # 标题栏猫图标，与网页/托盘统一
        except Exception:
            pass
        p = theme.palette()
        try:
            win.configure(background=p["bg"])              # 窗外底色跟随网页
        except Exception:
            pass
        cols = ("title", "kind", "status", "progress")
        tree = ttk.Treeview(win, columns=cols, show="headings", height=14)
        tree.heading("title", text="任务")
        tree.heading("kind", text="类型")
        tree.heading("status", text="状态")
        tree.heading("progress", text="进度")
        tree.column("title", width=270)
        tree.column("kind", width=90)
        tree.column("status", width=90)
        tree.column("progress", width=80)
        tree.pack(fill="both", expand=True, padx=8, pady=(8, 4))
        self._tree = tree

        bar = ttk.Frame(win)
        bar.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Button(bar, text="刷新", command=self._refresh_now).pack(side="left")
        ttk.Button(bar, text="清终态", command=self._clear_finished).pack(side="left", padx=6)
        ttk.Button(bar, text="关闭", command=self.close).pack(side="right")

        self._alive = True
        win.protocol("WM_DELETE_WINDOW", self.close)
        self._poll()

    def close(self):
        self._alive = False
        aid, self._after_id = self._after_id, None
        if aid and self.root is not None:
            try:
                self.root.after_cancel(aid)
            except Exception:
                pass
        if self.win is not None:
            try:
                self.win.destroy()
            except Exception:
                pass
        self.win = None

    # ---------------- 轮询 / 取数 ----------------
    def _poll(self):
        if not self._alive:
            return
        self._fetch()

    def _refresh_now(self):
        self._fetch()

    def _clear_finished(self):
        def work():
            _http_json(self.host, self.port, "/api/tasks", method="DELETE")
            self._fetch()
        threading.Thread(target=work, daemon=True).start()

    def _fetch(self):
        def work():
            data = _http_json(self.host, self.port, "/api/tasks")
            if self.root is not None:
                try:
                    self.root.after(0, lambda: self._render(data))
                except Exception:
                    pass
        threading.Thread(target=work, daemon=True).start()

    # ---------------- 渲染（主线程） ----------------
    def _render(self, data):
        if self.win is None or not self.win.winfo_exists():
            return
        tree = self._tree
        if tree is None:
            return
        tree.delete(*tree.get_children())
        if not data:
            tree.insert("", "end", values=("服务未启动或无法连接", "", "", ""))
            # 服务不可达：停轮询，避免无意义空转（可点「刷新」重试）
            self._alive = False
            return
        for t in data.get("tasks", []):
            title = str(t.get("title") or t.get("task_id") or "")
            kind = str(t.get("kind") or "")
            status = str(t.get("status") or "")
            prog = t.get("progress")
            if prog is None:
                prog = "—"
            else:
                try:
                    prog = "%.0f%%" % float(prog)
                except (TypeError, ValueError):
                    prog = str(prog)
            tree.insert("", "end", values=(title, kind, status, prog))
        if self._alive and self.root is not None:
            try:
                self._after_id = self.root.after(_REFRESH_MS, self._poll)
            except Exception:
                pass


# 模块级单实例（按 host:port 区分）；窗口关掉后引用留着，下次 open 重建。
_WINS = {}


def open_tasks(host, port, root):
    """打开/前置任务中心窗口。root 为 tkinter 根（球或托盘的共享 root）；为 None 时安全空操作。"""
    if root is None:
        return
    key = "%s:%d" % (host, int(port))
    w = _WINS.get(key)
    if w is None or w.win is None or not w.win.winfo_exists():
        w = _TasksWindow(host, port, root)
        _WINS[key] = w
    w.open()
