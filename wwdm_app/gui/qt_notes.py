# -*- coding: utf-8 -*-
"""PyQt6 笔记弹窗（v6.1）：把网页笔记本搬到 exe 悬浮球/托盘菜单。

  * 左栏：搜索 + 新建/刷新 + 笔记列表（📌置顶、[标签]）；
  * 右栏：标题 / 标签 + 颜色 / 正文 / 保存·删除·置顶；
  * 数据走回环 HTTP ``/api/notes`` 系列（禁代理）；
  * HTTP 在 **worker 线程**跑，结果经 ``pyqtSignal``（跨线程自动排到 GUI 线程）
    回写 —— Qt 信号槽本身线程安全，不需要 tk 那套队列 + 定时器。

铁律：顶层不 import PyQt6；QWidget 子类在 :func:`_impl` 内定义。
"""
import threading

from . import qt_theme

_COLOR_DEFAULT = "#4a6fa5"
# (显示名, 颜色) —— 下拉框显示中文名，取色用 hex
_COLOR_OPTIONS = [
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
    """列表一行文案（纯函数，便于无 Qt 测试）。"""
    pin = "\U0001F4CC " if item.get("pinned") else ""
    title = item.get("title") or "未命名笔记"
    tag = item.get("tag") or ""
    return pin + title + (("  [" + tag + "]") if tag else "")


_IMPL = None


def _impl():
    """惰性构建（首次调用才 import PyQt6）。返回窗口类。"""
    global _IMPL
    if _IMPL is not None:
        return _IMPL

    from PyQt6.QtCore import QObject, Qt, pyqtSignal
    from PyQt6.QtGui import QIcon
    from PyQt6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QLineEdit,
                                 QListWidget, QListWidgetItem, QPushButton,
                                 QSplitter, QTextEdit, QVBoxLayout, QWidget)

    class _Bridge(QObject):
        done = pyqtSignal(str, object)

    class NotesWindow(QWidget):
        def __init__(self, host, port):
            super().__init__(None)
            self.host = host
            self.port = int(port)
            self._items = []
            self._cur_id = None
            self._bridge = _Bridge()
            self._bridge.done.connect(self._on_done)   # 跨线程自动排队到 GUI 线程

            self.setWindowTitle("笔记本")
            self.setWindowIcon(QIcon(qt_theme.icon_path()))
            self.setMinimumSize(620, 420)
            self._size_to_screen()
            self._build_ui()

        def _size_to_screen(self):
            """按屏幕可用区域自适应初始尺寸并居中（小屏不溢出，大屏不局促）。"""
            from PyQt6.QtWidgets import QApplication
            scr = QApplication.primaryScreen()
            g = scr.availableGeometry() if scr is not None else None
            sw, sh = (g.width(), g.height()) if g is not None else (1280, 800)
            w = max(680, min(1040, int(sw * 0.52)))
            h = max(460, min(720, int(sh * 0.60)))
            self.resize(w, h)
            if g is not None:
                self.move(g.x() + (g.width() - w) // 2,
                          g.y() + (g.height() - h) // 2)

        # ---------------- UI ----------------
        def _build_ui(self):
            root = QVBoxLayout(self)
            root.setContentsMargins(14, 12, 14, 14)
            root.setSpacing(10)

            header = QHBoxLayout()
            title = QLabel("笔记本")
            title.setObjectName("DlgTitle")
            header.addWidget(title)
            header.addStretch(1)
            hint = QLabel("Ctrl+S 保存 · Esc 关闭")
            hint.setObjectName("Hint")
            header.addWidget(hint)
            root.addLayout(header)

            splitter = QSplitter(Qt.Orientation.Horizontal)
            splitter.setChildrenCollapsible(False)
            root.addWidget(splitter, 1)

            # ---- 左栏：搜索 + 新建/刷新 + 列表 ----
            left = QWidget()
            lv = QVBoxLayout(left)
            lv.setContentsMargins(0, 0, 0, 0)
            lv.setSpacing(8)
            srow = QHBoxLayout()
            srow.setSpacing(6)
            self.search = QLineEdit()
            self.search.setPlaceholderText("搜索标题/正文/标签…")
            self.search.setClearButtonEnabled(True)
            self.search.returnPressed.connect(lambda: self._load_list(False))
            srow.addWidget(self.search, 1)
            b_new = QPushButton("新建")
            b_new.clicked.connect(self._new)
            b_ref = QPushButton("刷新")
            b_ref.setObjectName("Ghost")
            b_ref.clicked.connect(lambda: self._load_list(False))
            srow.addWidget(b_new)
            srow.addWidget(b_ref)
            lv.addLayout(srow)
            self.list = QListWidget()
            self.list.currentRowChanged.connect(self._on_select)
            lv.addWidget(self.list, 1)
            splitter.addWidget(left)

            # ---- 右栏：标题 / 标签·颜色 / 正文 / 操作 ----
            right = QWidget()
            rv = QVBoxLayout(right)
            rv.setContentsMargins(0, 0, 0, 0)
            rv.setSpacing(8)
            self.title_edit = QLineEdit()
            self.title_edit.setObjectName("NoteTitle")
            self.title_edit.setPlaceholderText("标题")
            rv.addWidget(self.title_edit)
            meta = QHBoxLayout()
            meta.setSpacing(8)
            meta.addWidget(QLabel("标签"))
            self.tag_edit = QLineEdit()
            self.tag_edit.setPlaceholderText("可选")
            meta.addWidget(self.tag_edit, 1)
            meta.addWidget(QLabel("颜色"))
            self.color_box = QComboBox()
            for _name, _hex in _COLOR_OPTIONS:
                self.color_box.addItem(_name, _hex)
            self.color_box.setMinimumWidth(100)
            meta.addWidget(self.color_box)
            rv.addLayout(meta)
            self.body = QTextEdit()
            self.body.setPlaceholderText("在这里写笔记…")
            self.body.setAcceptRichText(False)
            rv.addWidget(self.body, 1)
            foot = QHBoxLayout()
            foot.setSpacing(8)
            self.status = QLabel("就绪")
            self.status.setObjectName("Hint")
            foot.addWidget(self.status)
            foot.addStretch(1)
            b_pin = QPushButton("置顶")
            b_pin.setObjectName("Ghost")
            b_pin.clicked.connect(self._pin)
            b_del = QPushButton("删除")
            b_del.setObjectName("Ghost")
            b_del.clicked.connect(self._delete)
            b_save = QPushButton("保存")
            b_save.clicked.connect(self._save)
            self.pin_btn = b_pin
            foot.addWidget(b_pin)
            foot.addWidget(b_del)
            foot.addWidget(b_save)
            rv.addLayout(foot)
            splitter.addWidget(right)

            splitter.setStretchFactor(0, 1)
            splitter.setStretchFactor(1, 2)
            splitter.setSizes([270, 570])

        # ---------------- 线程回主线程 ----------------
        def _post(self, kind, payload):
            self._bridge.done.emit(kind, payload)

        def _on_done(self, kind, payload):
            if kind == "list":
                self._render_list(payload.get("data"), payload.get("autoload", True))
            elif kind == "note":
                self._fill_editor(payload)
            elif kind == "created":
                self._after_create(payload)
            elif kind == "saved":
                self._after_save(payload)
            elif kind == "deleted":
                self._after_delete(payload)
            elif kind == "pinned":
                self._after_pin(payload)

        # ---------------- 列表 ----------------
        def _load_list(self, autoload=True):
            kw = (self.search.text() or "").strip()

            def work():
                import urllib.parse
                path = "/api/notes?sort=updated_desc"
                if kw:
                    path += "&q=" + urllib.parse.quote(kw)
                data = _http_json(self.host, self.port, path)
                self._post("list", {"data": data, "autoload": autoload})
            threading.Thread(target=work, daemon=True).start()

        def _render_list(self, data, autoload=True):
            self.list.blockSignals(True)
            self.list.clear()
            if not data:
                self._items = []
                self.list.addItem("服务未启动或无法连接")
                self.status.setText("服务不可达")
                self.list.blockSignals(False)
                return
            items = data.get("items") or []
            self._items = items
            if not items:
                self.list.addItem("（暂无笔记，点「新建」）")
                self.status.setText("暂无笔记")
                self.list.blockSignals(False)
                return
            idx = 0
            for i, it in enumerate(items):
                item = QListWidgetItem(_note_label(it))
                item.setData(Qt.ItemDataRole.UserRole, it.get("id"))
                self.list.addItem(item)
                if self._cur_id is not None and it.get("id") == self._cur_id:
                    idx = i
            self.list.setCurrentRow(idx)
            self.list.blockSignals(False)
            if autoload:
                self._load_note(items[idx].get("id"))

        def _on_select(self, row):
            if row is None or row < 0 or row >= len(self._items):
                return
            rid = self._items[row].get("id")
            if rid is not None:
                self._load_note(rid)

        # ---------------- 单篇 ----------------
        def _load_note(self, rid):
            if rid is None:
                return
            self._cur_id = rid

            def work():
                data = _http_json(self.host, self.port, "/api/notes/%d" % int(rid))
                self._post("note", data)
            threading.Thread(target=work, daemon=True).start()

        def _fill_editor(self, data):
            if not data or not data.get("ok"):
                self.status.setText("读取失败")
                return
            n = data.get("note") or {}
            self._cur_id = n.get("id")
            self.title_edit.setText(n.get("title") or "")
            self.tag_edit.setText(n.get("tag") or "")
            self.body.setPlainText(n.get("content") or "")
            color = n.get("color") or _COLOR_DEFAULT
            cid = self.color_box.findData(color)
            if cid < 0:
                self.color_box.addItem(color, color)     # 未知色值：补一项并选中
                cid = self.color_box.findData(color)
            self.color_box.setCurrentIndex(cid)
            self.pin_btn.setText("取消置顶" if n.get("pinned") else "置顶")
            self.status.setText("%d 字" % len(n.get("content") or ""))

        # ---------------- 增删改 ----------------
        def _collect(self):
            return {
                "title": (self.title_edit.text().strip() or "未命名笔记"),
                "content": self.body.toPlainText(),
                "tag": self.tag_edit.text().strip(),
                "color": (self.color_box.currentData() or _COLOR_DEFAULT),
            }

        def _new(self):
            payload = {"title": "未命名笔记", "content": "",
                       "color": _COLOR_DEFAULT, "tag": ""}

            def work():
                data = _http_json(self.host, self.port, "/api/notes",
                                  method="POST", payload=payload)
                self._post("created", data)
            threading.Thread(target=work, daemon=True).start()

        def _after_create(self, data):
            if not data or not data.get("ok"):
                self.status.setText("新建失败")
                return
            self._cur_id = data.get("id")
            self.status.setText("已新建")
            self._load_note(self._cur_id)
            self._load_list(autoload=False)

        def _save(self):
            payload = self._collect()
            cur = self._cur_id
            if cur is None:
                def work_create():
                    data = _http_json(self.host, self.port, "/api/notes",
                                      method="POST", payload=payload)
                    self._post("created", data)
                threading.Thread(target=work_create, daemon=True).start()
                return
            self.status.setText("保存中…")

            def work():
                data = _http_json(self.host, self.port,
                                  "/api/notes/%d" % int(cur), method="PUT",
                                  payload=payload)
                self._post("saved", data)
            threading.Thread(target=work, daemon=True).start()

        def _after_save(self, data):
            if not data or not data.get("ok"):
                self.status.setText("保存失败")
                return
            self.status.setText("已保存")
            self._load_list(autoload=False)

        def _delete(self):
            cur = self._cur_id
            if cur is None:
                self.status.setText("未选中笔记")
                return

            def work():
                data = _http_json(self.host, self.port,
                                  "/api/notes/%d" % int(cur), method="DELETE")
                self._post("deleted", data)
            threading.Thread(target=work, daemon=True).start()

        def _after_delete(self, data):
            if not data or not data.get("ok"):
                self.status.setText("删除失败")
                return
            self._cur_id = None
            self.title_edit.setText("")
            self.tag_edit.setText("")
            self.body.setPlainText("")
            self.status.setText("已删除")
            self._load_list(autoload=True)

        def _pin(self):
            cur = self._cur_id
            if cur is None:
                self.status.setText("未选中笔记")
                return

            def work():
                data = _http_json(self.host, self.port,
                                  "/api/notes/%d/pin" % int(cur), method="POST")
                self._post("pinned", data)
            threading.Thread(target=work, daemon=True).start()

        def _after_pin(self, data):
            if not data or not data.get("ok"):
                self.status.setText("置顶失败")
                return
            self.status.setText("已切换置顶")
            self._load_list(autoload=False)
            if self._cur_id is not None:
                self._load_note(self._cur_id)

    _IMPL = NotesWindow
    return NotesWindow


def open_notes(host, port):
    """打开/前置笔记弹窗（按 host:port 单实例）。需在 GUI 线程调用。"""
    key = "%s:%d" % (host, int(port))
    w = _NOTES_WINS.get(key)
    cls = _impl()
    if w is None:
        w = cls(host, port)
        _NOTES_WINS[key] = w
        w._load_list(autoload=True)
    elif not w.isVisible():
        # 之前关掉了：重新打开时刷新一次列表并载入当前/第一篇
        w._load_list(autoload=True)
    w.show()
    w.raise_()
    w.activateWindow()
    return w


_NOTES_WINS = {}
