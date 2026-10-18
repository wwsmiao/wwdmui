# -*- coding: utf-8 -*-
"""笔记迷你编辑器 tkinter 窗口（v6.0(8)：把网页笔记本搬到 exe 托盘菜单）。

设计约束（同 fab / tray / tasks_win，铁律不可破）
------------------------------------------------
  * 本模块**顶层不碰 tkinter**、不 import services/blueprints —— 笔记数据走
    回环 HTTP 打 ``/api/notes`` 系列（禁代理 urllib），与 actions.quit_app 同范式。
  * 窗口**懒建**（菜单点击才建）、**单实例**（按 host:port）。
  * HTTP 在 **worker 线程**里跑，结果经**线程安全队列 + 主线程定时器消费**
    （``self._post`` / ``_drain``，与 tray 同款）回写 UI —— 绝不在 worker 线程
    里直接碰 tkinter 控件（共享 root 是 withdraw 的，跨线程 root.after 有明显
    延迟），也绝不在 ctypes WndProc 里碰。
  * 界面用 :mod:`gui.theme` 的网页水彩配色（Listbox/Text 手动着色，按钮走 ttk）。
"""
import collections
import threading

from . import theme

_COLOR_DEFAULT = "#4a6fa5"
# 与网页水彩预设一致的强调色（新建/着色下拉）
_COLOR_PRESETS = [
    ("水彩蓝", "#4a6fa5"),
    ("水彩紫", "#c38d94"),
    ("水彩绿", "#2d8f83"),
    ("水彩琥珀", "#d4a373"),
    ("水彩红", "#d96c6c"),
]


def _http_json(host, port, path, method="GET", payload=None):
    """回环 GET/POST/PUT/DELETE 取 JSON（禁代理）。失败返回 None。"""
    import json
    import urllib.request
    url = "http://%s:%d%s" % (host, port, path)
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=4) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:
        return None


def _note_label(item):
    """列表里一行笔记的显示文案（纯函数，便于无 tkinter 测试）。"""
    pin = "\U0001F4CC " if item.get("pinned") else ""      # 📌
    title = item.get("title") or "未命名笔记"
    tag = item.get("tag") or ""
    return pin + title + (("  [" + tag + "]") if tag else "")


class _NotesWindow:
    """一个 host:port 对应一个笔记窗口（模块级 _WINS 做单实例）。"""

    def __init__(self, host, port, root):
        self.host = host
        self.port = int(port)
        self.root = root
        self.win = None
        self._items = []
        self._cur_id = None
        self._listbox = None
        self._title_var = None
        self._tag_var = None
        self._color_var = None
        self._content = None
        self._status_var = None
        self._pin_btn = None
        # 🔴 共享 root 是 withdraw 的，worker 线程直接 root.after 回主线程会有
        # 明显延迟（实测 ~300ms/跳）→ 改用线程安全队列 + 主线程定时器消费
        # （与 tray._enqueue/_drain_pending 同款），保证切换笔记及时刷新。
        self._q = collections.deque()
        self._drain_id = None
        self._alive = False

    # ---------------- 跨线程回 UI：队列 + 主线程消费 ----------------
    def _post(self, fn):
        """worker 线程把「回 UI 的纯 Python 回调」入队；主线程定时器消费。"""
        self._q.append(fn)

    def _start_drain(self):
        if self.root is None:
            return
        try:
            self._drain_id = self.root.after(40, self._drain)
        except Exception:
            self._drain_id = None

    def _drain(self):
        self._drain_id = None
        if not self._alive:
            return
        while True:
            try:
                fn = self._q.popleft()
            except IndexError:
                break
            try:
                fn()
            except Exception:
                pass
        if self._alive and self.root is not None:
            try:
                self._drain_id = self.root.after(40, self._drain)
            except Exception:
                self._drain_id = None

    # ---------------- 开 / 关 ----------------
    def open(self):
        import tkinter as tk
        from tkinter import ttk
        if self.win is not None and self.win.winfo_exists():
            self.win.lift()
            self.win.focus_force()
            return
        theme.apply_style(self.root)
        p = theme.palette()

        win = tk.Toplevel(self.root)
        self.win = win
        win.title("笔记本")
        win.geometry("780x520")
        try:
            win.attributes("-topmost", True)
        except Exception:
            pass
        try:
            from . import icoimage
            win.iconbitmap(icoimage.default_icon_path())
        except Exception:
            pass
        try:
            win.configure(background=p["bg"])
        except Exception:
            pass

        outer = ttk.Frame(win, padding=10)
        outer.pack(fill="both", expand=True)

        # ---- 左栏：搜索 + 列表 ----
        left = ttk.Frame(outer)
        left.pack(side="left", fill="y")
        sbar = ttk.Frame(left)
        sbar.pack(fill="x", pady=(0, 6))
        self._search_var = tk.StringVar()
        se = ttk.Entry(sbar, textvariable=self._search_var, width=22)
        se.pack(side="left", fill="x", expand=True)
        se.bind("<Return>", lambda e: self._load_list(autoload=False))
        ttk.Button(sbar, text="新建", width=6,
                   command=self._new).pack(side="left", padx=(4, 0))
        ttk.Button(sbar, text="刷新", width=6,
                   command=lambda: self._load_list(autoload=False)).pack(
                       side="left", padx=(4, 0))

        lb_wrap = ttk.Frame(left)
        lb_wrap.pack(fill="both", expand=True)
        lb = tk.Listbox(lb_wrap, width=30, activestyle="none",
                        exportselection=False,
                        bg=p["panel"], fg=p["text"],
                        selectbackground=p["accent"], selectforeground="#ffffff",
                        highlightthickness=1, highlightbackground=p["border"],
                        relief="flat", font=(p["font"], 10))
        sb = ttk.Scrollbar(lb_wrap, orient="vertical", command=lb.yview)
        lb.configure(yscrollcommand=sb.set)
        lb.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        lb.bind("<<ListboxSelect>>", self._on_select)
        self._listbox = lb

        # ---- 右栏：标题 / 标签+颜色 / 正文 / 操作 ----
        right = ttk.Frame(outer, padding=(10, 0, 0, 0))
        right.pack(side="left", fill="both", expand=True)

        self._title_var = tk.StringVar()
        ttk.Entry(right, textvariable=self._title_var,
                  font=(p["font"], 12)).pack(fill="x", pady=(0, 6))

        meta = ttk.Frame(right)
        meta.pack(fill="x", pady=(0, 6))
        ttk.Label(meta, text="标签").pack(side="left")
        self._tag_var = tk.StringVar()
        ttk.Entry(meta, textvariable=self._tag_var, width=18).pack(
            side="left", padx=(4, 12))
        ttk.Label(meta, text="颜色").pack(side="left")
        self._color_var = tk.StringVar(value=_COLOR_DEFAULT)
        cb = ttk.Combobox(meta, textvariable=self._color_var, width=10,
                          state="readonly",
                          values=[c[1] for c in _COLOR_PRESETS])
        cb.pack(side="left", padx=(4, 0))

        body = ttk.Frame(right)
        body.pack(fill="both", expand=True)
        txt = tk.Text(body, wrap="word", undo=True,
                      bg=p["panel"], fg=p["text"], insertbackground=p["text"],
                      selectbackground=p["accent"], selectforeground="#ffffff",
                      highlightthickness=1, highlightbackground=p["border"],
                      relief="flat", font=(p["font"], 10), padx=8, pady=6)
        tsb = ttk.Scrollbar(body, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=tsb.set)
        txt.pack(side="left", fill="both", expand=True)
        tsb.pack(side="right", fill="y")
        self._content = txt

        foot = ttk.Frame(right)
        foot.pack(fill="x", pady=(8, 0))
        self._status_var = tk.StringVar(value="就绪")
        ttk.Label(foot, textvariable=self._status_var).pack(side="left")
        ttk.Button(foot, text="保存", command=self._save).pack(side="right")
        ttk.Button(foot, text="删除", command=self._delete).pack(
            side="right", padx=(0, 8))
        self._pin_btn = ttk.Button(foot, text="置顶", command=self._pin)
        self._pin_btn.pack(side="right", padx=(0, 8))

        win.bind("<Control-s>", lambda e: (self._save(), "break")[1])
        win.bind("<Escape>", lambda e: self.close())
        win.protocol("WM_DELETE_WINDOW", self.close)

        self._q.clear()
        self._alive = True
        self._start_drain()
        self._load_list(autoload=True)

    def close(self):
        self._alive = False
        aid, self._drain_id = self._drain_id, None
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

    # ---------------- 列表 ----------------
    def _load_list(self, autoload=True):
        if self.root is None:
            return
        kw = ""
        try:
            kw = (self._search_var.get() or "").strip()
        except Exception:
            pass

        def work():
            import urllib.parse
            path = "/api/notes?sort=updated_desc"
            if kw:
                path += "&q=" + urllib.parse.quote(kw)
            data = _http_json(self.host, self.port, path)
            self._post(lambda: self._render_list(data, autoload))
        threading.Thread(target=work, daemon=True).start()

    def _render_list(self, data, autoload=True):
        if self.win is None or not self.win.winfo_exists():
            return
        lb = self._listbox
        if lb is None:
            return
        if not data:
            self._items = []
            lb.delete(0, "end")
            lb.insert("end", "服务未启动或无法连接")
            self._status("服务不可达")
            return
        items = data.get("items") or []
        self._items = items
        lb.delete(0, "end")
        if not items:
            lb.insert("end", "（暂无笔记，点「新建」）")
            self._status("暂无笔记")
            return
        idx = None
        for i, it in enumerate(items):
            lb.insert("end", _note_label(it))
            if self._cur_id is not None and it.get("id") == self._cur_id:
                idx = i
        if idx is None:
            idx = 0
        try:
            lb.selection_clear(0, "end")
            lb.selection_set(idx)
            lb.activate(idx)
            lb.see(idx)
        except Exception:
            pass
        if autoload:
            self._load_note(items[idx].get("id"))

    def _on_select(self, evt=None):
        lb = self._listbox
        if lb is None:
            return
        try:
            sel = lb.curselection()
        except Exception:
            return
        if not sel:
            return
        idx = sel[0]
        if idx >= len(self._items):
            return
        rid = self._items[idx].get("id")
        if rid is not None:
            self._load_note(rid)

    # ---------------- 单篇 ----------------
    def _load_note(self, rid):
        if rid is None:
            return
        self._cur_id = rid

        def work():
            data = _http_json(self.host, self.port, "/api/notes/%d" % int(rid))
            self._post(lambda: self._fill_editor(data))
        threading.Thread(target=work, daemon=True).start()

    def _fill_editor(self, data):
        if self.win is None or not self.win.winfo_exists():
            return
        if not data or not data.get("ok"):
            self._status("读取失败")
            return
        n = data.get("note") or {}
        self._cur_id = n.get("id")
        self._title_var.set(n.get("title") or "")
        self._tag_var.set(n.get("tag") or "")
        try:
            self._content.delete("1.0", "end")
            self._content.insert("1.0", n.get("content") or "")
        except Exception:
            pass
        self._color_var.set(n.get("color") or _COLOR_DEFAULT)
        if self._pin_btn is not None:
            try:
                self._pin_btn.configure(
                    text=("取消置顶" if n.get("pinned") else "置顶"))
            except Exception:
                pass
        self._status("%d 字" % len(n.get("content") or ""))

    # ---------------- 增删改 ----------------
    def _collect(self):
        try:
            content = self._content.get("1.0", "end-1c")
        except Exception:
            content = ""
        return {
            "title": (self._title_var.get().strip() or "未命名笔记"),
            "content": content,
            "tag": self._tag_var.get().strip(),
            "color": self._color_var.get() or _COLOR_DEFAULT,
        }

    def _new(self):
        payload = {"title": "未命名笔记", "content": "",
                   "color": _COLOR_DEFAULT, "tag": ""}

        def work():
            data = _http_json(self.host, self.port, "/api/notes",
                              method="POST", payload=payload)
            self._post(lambda: self._after_create(data))
        threading.Thread(target=work, daemon=True).start()

    def _after_create(self, data):
        if not data or not data.get("ok"):
            self._status("新建失败")
            return
        self._cur_id = data.get("id")
        self._status("已新建")
        self._load_note(self._cur_id)
        self._load_list(autoload=False)

    def _save(self):
        if self.win is None or not self.win.winfo_exists():
            return
        payload = self._collect()
        cur = self._cur_id
        if cur is None:
            # 没选中就保存 → 当作新建
            def work_create():
                data = _http_json(self.host, self.port, "/api/notes",
                                  method="POST", payload=payload)
                self._post(lambda: self._after_create(data))
            threading.Thread(target=work_create, daemon=True).start()
            return
        self._status("保存中…")

        def work():
            data = _http_json(self.host, self.port,
                              "/api/notes/%d" % int(cur), method="PUT",
                              payload=payload)
            self._post(lambda: self._after_save(data))
        threading.Thread(target=work, daemon=True).start()

    def _after_save(self, data):
        if not data or not data.get("ok"):
            self._status("保存失败")
            return
        self._status("已保存")
        self._load_list(autoload=False)

    def _delete(self):
        cur = self._cur_id
        if cur is None:
            self._status("未选中笔记")
            return

        def work():
            data = _http_json(self.host, self.port,
                              "/api/notes/%d" % int(cur), method="DELETE")
            self._post(lambda: self._after_delete(data))
        threading.Thread(target=work, daemon=True).start()

    def _after_delete(self, data):
        if not data or not data.get("ok"):
            self._status("删除失败")
            return
        self._cur_id = None
        try:
            self._title_var.set("")
            self._tag_var.set("")
            self._content.delete("1.0", "end")
        except Exception:
            pass
        self._status("已删除")
        self._load_list(autoload=True)

    def _pin(self):
        cur = self._cur_id
        if cur is None:
            self._status("未选中笔记")
            return

        def work():
            data = _http_json(self.host, self.port,
                              "/api/notes/%d/pin" % int(cur), method="POST")
            self._post(lambda: self._after_pin(data))
        threading.Thread(target=work, daemon=True).start()

    def _after_pin(self, data):
        if not data or not data.get("ok"):
            self._status("置顶失败")
            return
        self._status("已切换置顶")
        self._load_list(autoload=False)
        if self._cur_id is not None:
            self._load_note(self._cur_id)

    def _status(self, msg):
        if self._status_var is not None:
            try:
                self._status_var.set(msg)
            except Exception:
                pass


# 模块级单实例（按 host:port 区分）；窗口关掉后引用留着，下次 open 重建。
_WINS = {}


def open_notes(host, port, root):
    """打开/前置笔记窗口。root 为 tkinter 根；为 None 时安全空操作。"""
    if root is None:
        return
    key = "%s:%d" % (host, int(port))
    w = _WINS.get(key)
    if w is None or w.win is None or not w.win.winfo_exists():
        w = _NotesWindow(host, port, root)
        _WINS[key] = w
    w.open()
