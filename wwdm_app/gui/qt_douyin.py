# -*- coding: utf-8 -*-
"""抖音提取弹窗（悬浮球右键 → 抖音提取…）。

复用 ``qt_theme.GlassDialog``（半透明玻璃、跟随主题/日夜间），布局参考
网页解析工具：粘贴链接 → 解析 → **4 列卡片**展示图片/视频封面；多链接时
顶部有横向导航切换；保存目录默认 ComfyUI/input 且可临时修改；下载统一
走服务端全局 aria2c。

铁律：顶层不 import PyQt6；类在 :func:`_impl` 内定义。
"""
import os
import threading

from . import actions, qt_theme

_IMPL = None


def _impl():
    global _IMPL
    if _IMPL is not None:
        return _IMPL

    from PyQt6.QtCore import QObject, QSize, Qt, pyqtSignal
    from PyQt6.QtGui import QIcon, QPixmap
    from PyQt6.QtWidgets import (QApplication, QFileDialog, QGridLayout,
                                 QHBoxLayout, QLabel, QLineEdit, QPushButton,
                                 QScrollArea, QVBoxLayout, QWidget)

    G = qt_theme.ui_classes()
    GlassDialog = G["GlassDialog"]

    class _Bridge(QObject):
        parsed = pyqtSignal(object)
        done = pyqtSignal(object)
        thumb = pyqtSignal(int, int, object)

    class DouyinDialog(GlassDialog):
        def __init__(self, host, port):
            super().__init__(
                None, "抖音提取",
                subtitle="粘贴链接 → 解析 → 选择图片/视频，经 aria2c 下载到 ComfyUI/input")
            self.host = host
            self.port = int(port)
            self._groups = []       # [{title, items:[{kind,url,cover}], msg}]
            self._sel = set()       # {(group, item)}
            self._cards = {}        # {(group, item): QPushButton}
            self._cur = 0
            self._thumbs = set()
            self._bridge = _Bridge()
            self._bridge.parsed.connect(self._on_parsed)
            self._bridge.done.connect(self._on_done)
            self._bridge.thumb.connect(self._on_thumb)
            self._build()
            qt_theme.on_theme_change(self.update)
            self.setMinimumSize(920, 740)
            self.resize(950, 800)

        # ---------------- 基础 ----------------
        def _default_dir(self):
            from ..core import config
            d = (config.settings.get("input_dir") or "").strip()
            if d:
                return d
            return os.path.join(config.get_comfyui_dir(), "input")

        def _set_status(self, msg):
            try:
                self.status.setText(msg)
            except Exception:
                pass

        def _clear(self, lay):
            while lay.count():
                it = lay.takeAt(0)
                w = it.widget()
                if w is not None:
                    w.setParent(None)
                    w.deleteLater()

        # ---------------- 布局 ----------------
        def _build(self):
            b = self.body
            row = QHBoxLayout()
            row.setSpacing(8)
            self.url_edit = QLineEdit()
            self.url_edit.setPlaceholderText("请粘贴视频链接进行解析（支持多个链接）")
            self.url_edit.returnPressed.connect(self._parse)
            row.addWidget(self.url_edit, 1)
            b_paste = QPushButton("粘贴")
            b_paste.setObjectName("Ghost")
            b_paste.clicked.connect(self._paste)
            self.btn_parse = QPushButton("解析视频")
            self.btn_parse.clicked.connect(self._parse)
            row.addWidget(b_paste)
            row.addWidget(self.btn_parse)
            b.addLayout(row)

            drow = QHBoxLayout()
            drow.setSpacing(8)
            lab = QLabel("保存到")
            lab.setObjectName("Hint")
            drow.addWidget(lab)
            self.dir_edit = QLineEdit(self._default_dir())
            self.dir_edit.setPlaceholderText("默认 ComfyUI/input，可临时修改")
            drow.addWidget(self.dir_edit, 1)
            b_browse = QPushButton("浏览…")
            b_browse.setObjectName("Ghost")
            b_browse.clicked.connect(self._browse)
            drow.addWidget(b_browse)
            b.addLayout(drow)

            # 多链接横向导航（>1 组才显示）
            self.nav = QScrollArea()
            self.nav.setWidgetResizable(False)
            self.nav.setFixedHeight(44)
            self.nav.setVerticalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.nav.setHorizontalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            self.nav_host = QWidget()
            self.nav_lay = QHBoxLayout(self.nav_host)
            self.nav_lay.setContentsMargins(0, 0, 0, 0)
            self.nav_lay.setSpacing(6)
            self.nav.setWidget(self.nav_host)
            self.nav.setVisible(False)
            b.addWidget(self.nav)

            # 4 列卡片网格
            self.grid_scroll = QScrollArea()
            self.grid_scroll.setWidgetResizable(True)
            self.grid_host = QWidget()
            self.grid = QGridLayout(self.grid_host)
            self.grid.setContentsMargins(4, 4, 4, 4)
            self.grid.setSpacing(8)
            self.grid.setAlignment(Qt.AlignmentFlag.AlignTop
                                   | Qt.AlignmentFlag.AlignLeft)
            self.grid_scroll.setWidget(self.grid_host)
            self.grid_scroll.setMinimumHeight(240)
            b.addWidget(self.grid_scroll, 1)

            frow = QHBoxLayout()
            frow.setSpacing(8)
            self.status = QLabel("等待解析…")
            self.status.setObjectName("Hint")
            frow.addWidget(self.status, 1)
            b_all = QPushButton("全选")
            b_all.setObjectName("Ghost")
            b_all.clicked.connect(lambda: self._select_all(True))
            b_none = QPushButton("取消全选")
            b_none.setObjectName("Ghost")
            b_none.clicked.connect(lambda: self._select_all(False))
            b_dl = QPushButton("下载选中")
            b_dl.clicked.connect(self._download)
            frow.addWidget(b_all)
            frow.addWidget(b_none)
            frow.addWidget(b_dl)
            b.addLayout(frow)
            self.add_button("关闭", on_click=self.close)

        # ---------------- 交互 ----------------
        def _paste(self):
            cb = QApplication.clipboard()
            if cb is not None:
                self.url_edit.setText(cb.text() or "")

        def _browse(self):
            d = QFileDialog.getExistingDirectory(
                self, "选择保存目录", self.dir_edit.text() or "")
            if d:
                self.dir_edit.setText(d)

        def _parse(self):
            text = self.url_edit.text().strip()
            if not text:
                self._set_status("请先粘贴链接")
                return
            self.btn_parse.setEnabled(False)
            self._set_status("解析中…（多链接可能需要一点时间）")

            def work():
                try:
                    data = actions.http_json(
                        self.host, self.port, "/api/douyin/fetch",
                        method="POST", payload={"url": text}, timeout=60)
                except Exception:
                    data = None
                self._bridge.parsed.emit(data or {})
            threading.Thread(target=work, daemon=True).start()

        def _on_parsed(self, data):
            self.btn_parse.setEnabled(True)
            if not data or not data.get("ok"):
                self._set_status((data or {}).get("msg") or "解析失败")
                return
            results = data.get("results") or []
            if not results:
                self._set_status("没有解析到内容")
                return
            start = len(self._groups)
            for r in results:
                self._groups.append(self._group(r))
            self._rebuild_nav()
            self._show_group(start)
            ok = sum(1 for g in self._groups[start:] if g["items"])
            self._set_status("解析完成：%d 个链接，%d 项可下载" % (len(results), ok))

        def _group(self, r):
            typ = str(r.get("type") or "")
            if typ == "error":
                return {"title": "解析失败", "items": [],
                        "msg": r.get("msg") or ""}
            title = (r.get("desc") or r.get("aweme_id") or "抖音内容").strip()
            items = []
            if r.get("video_url"):
                items.append({"kind": "video", "url": r["video_url"],
                              "cover": r.get("cover_url") or ""})
            for u in (r.get("image_urls") or []):
                items.append({"kind": "img", "url": u, "cover": u})
            return {"title": title, "items": items, "msg": ""}

        # ---------------- 导航 / 网格 ----------------
        def _rebuild_nav(self):
            self._clear(self.nav_lay)
            for i, g in enumerate(self._groups):
                n = len(g["items"])
                label = "%d. %s" % (i + 1, (g["title"] or "链接")[:16])
                label += (" · %d项" % n) if n else " · 空"
                btn = QPushButton(label)
                btn.setObjectName("Chip")
                btn.setCheckable(True)
                btn.setChecked(i == self._cur)
                btn.clicked.connect(
                    lambda _=False, k=i: self._show_group(k))
                self.nav_lay.addWidget(btn)
            self.nav_host.adjustSize()
            self.nav.setVisible(len(self._groups) > 1)

        def _update_nav_checked(self):
            btns = []
            for k in range(self.nav_lay.count()):
                w = self.nav_lay.itemAt(k).widget()
                if isinstance(w, QPushButton):
                    btns.append(w)
            for k, b in enumerate(btns):
                b.setChecked(k == self._cur)

        def _show_group(self, i):
            if i < 0 or i >= len(self._groups):
                return
            self._cur = i
            self._update_nav_checked()
            self._clear(self.grid)
            self._cards = {}
            g = self._groups[i]
            if not g["items"]:
                lab = QLabel(g.get("msg") or "这个链接没有可下载的内容")
                lab.setObjectName("Hint")
                self.grid.addWidget(lab, 0, 0)
                return
            for idx, it in enumerate(g["items"]):
                card = QPushButton()
                card.setObjectName("DyCard")
                card.setCheckable(True)
                card.setFixedSize(164, 164)
                card.setIconSize(QSize(150, 150))
                card.setCursor(Qt.CursorShape.PointingHandCursor)
                if it["kind"] == "video":
                    card.setText("\u25B6 视频")
                    card.setToolTip("视频：点击选择下载")
                else:
                    card.setToolTip("图片 %d" % (idx + 1))
                card.setChecked((i, idx) in self._sel)
                card.clicked.connect(
                    lambda checked, gg=i, ii=idx: self._toggle(gg, ii, checked))
                self.grid.addWidget(card, idx // 4, idx % 4)
                self._cards[(i, idx)] = card
                self._load_thumb(i, idx, it.get("cover") or it.get("url") or "")

        def _toggle(self, g, i, checked):
            if checked:
                self._sel.add((g, i))
            else:
                self._sel.discard((g, i))

        def _select_all(self, on):
            g = self._cur
            if g >= len(self._groups):
                return
            for i in range(len(self._groups[g]["items"])):
                if on:
                    self._sel.add((g, i))
                else:
                    self._sel.discard((g, i))
                c = self._cards.get((g, i))
                if c is not None:
                    c.setChecked(on)

        # ---------------- 缩略图 ----------------
        def _load_thumb(self, g, i, url):
            if not url:
                return
            key = (g, i, url)
            if key in self._thumbs:
                return
            self._thumbs.add(key)

            def work():
                data = b""
                try:
                    import urllib.request
                    req = urllib.request.Request(url, headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                        "Referer": "https://www.douyin.com/"})
                    opener = urllib.request.build_opener(
                        urllib.request.ProxyHandler({}))
                    with opener.open(req, timeout=15) as r:
                        data = r.read(4 * 1024 * 1024)
                except Exception:
                    data = b""
                self._bridge.thumb.emit(g, i, data)
            threading.Thread(target=work, daemon=True).start()

        def _on_thumb(self, g, i, data):
            card = self._cards.get((g, i))
            if card is None or not data:
                return
            pm = QPixmap()
            if pm.loadFromData(data):
                card.setIcon(QIcon(pm.scaled(
                    150, 150,
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation)))

        # ---------------- 下载 ----------------
        def _download(self):
            if self._cur >= len(self._groups):
                return
            g = self._groups[self._cur]
            sel = [it for i, it in enumerate(g["items"])
                   if (self._cur, i) in self._sel]
            if not sel:
                self._set_status("请先选择要下载的内容")
                return
            save_dir = (self.dir_edit.text() or "").strip() or self._default_dir()
            video = next((it for it in sel if it["kind"] == "video"), None)
            if video is not None:
                payload = {"video_url": video["url"], "save_dir": save_dir,
                           "video_name": g["title"] or "video"}
            else:
                payload = {"urls": [it["url"] for it in sel if it["kind"] == "img"],
                           "save_dir": save_dir}
            self._set_status("已提交下载…")

            def work():
                data = actions.http_json(
                    self.host, self.port, "/api/douyin/download",
                    method="POST", payload=payload, timeout=30)
                self._bridge.done.emit(data or {})
            threading.Thread(target=work, daemon=True).start()

        def _on_done(self, data):
            if data and data.get("ok"):
                if data.get("added") is not None:
                    self._set_status("已加入 aria2c：%d 个文件 → %s" % (
                        data.get("added"), data.get("save_dir") or ""))
                else:
                    self._set_status("已加入 aria2c：%s"
                                     % (data.get("out") or "视频"))
            else:
                self._set_status((data or {}).get("msg") or "下载失败")

    _IMPL = DouyinDialog
    return DouyinDialog


_WINS = {}


def open_douyin(host, port):
    """打开/前置抖音提取弹窗（按 host:port 单实例）。需在 GUI 线程调用。"""
    key = "%s:%d" % (host, int(port))
    w = _WINS.get(key)
    cls = _impl()
    if w is None:
        w = cls(host, port)
        _WINS[key] = w
    w.show()
    w.raise_()
    w.activateWindow()
    return w
