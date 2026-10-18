# -*- coding: utf-8 -*-
"""v6.1 PyQt6 桌面层：位置纯函数 / GUI 降级 / 开机自启 / 设置校验 /
动作与 HTTP / 主题与菜单纯逻辑 / 依赖方向。

GUI 本体（QWidget / QSystemTrayIcon）**不可在无 Qt 的解释器上单测**，这里只测
能测的纯逻辑与结构：
  * pos 位置解析/钳制/默认值；
  * should_run_gui 的三条降级路径（PyQt6 / 显示 / 显式关闭）；
  * autostart 注册表读写（winreg 全打桩，绝不碰真注册表）；
  * 设置 API 对悬浮球键的校验 —— 特别是 **保存设置不许写注册表** 的闸门；
  * actions 的 HTTP 客户端（关机 / 日夜间），失败一律 None；
  * qt_theme 调色板 / QSS、qt_fab 菜单规格、qt_notes 的 HTTP 与标签（纯函数）；
  * 结构铁律：gui → core 单向；gui 各模块**不 import tkinter / 不在顶层 import PyQt6**。
"""
import ast
import io
import os

import pytest

import wwdm_app.services as svc
from wwdm_app.core import config
from wwdm_app.gui import actions, autostart, pos, qt_fab, qt_notes, qt_theme


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GUI_DIR = os.path.join(ROOT, "wwdm_app", "gui")


# ============================================================
# ① 位置持久化（纯函数）
# ============================================================
class TestPos:
    def test_parse_ok(self):
        assert pos.parse_pos("100,200") == (100, 200)
        assert pos.parse_pos(" 10 , 20 ") == (10, 20)
        assert pos.parse_pos("0,0") == (0, 0)

    @pytest.mark.parametrize("bad", ["", None, 123, "x,y", "-5,3", "1,2,3",
                                     "10", "1.5,2", "999999,0", "a,b"])
    def test_parse_dirty(self, bad):
        assert pos.parse_pos(bad) is None

    def test_fmt(self):
        assert pos.fmt_pos(100, 200) == "100,200"
        assert pos.fmt_pos(12.7, 3.2) == "12,3"

    def test_clamp_keeps_on_screen(self):
        assert pos.clamp_pos(99999, 99999, (1920, 1080)) == (1848, 1008)
        assert pos.clamp_pos(-50, -50, (1920, 1080)) == (8, 8)
        assert pos.clamp_pos(500, 500, (1920, 1080)) == (500, 500)

    def test_default_is_bottom_right_with_taskbar(self):
        assert pos.default_pos((1920, 1080)) == (1848, 968)
        assert pos.default_pos((100, 100)) == (28, 0)

    def test_resolve_saved_vs_fallback(self):
        assert pos.resolve_pos("300,400", (1920, 1080)) == (300, 400)
        assert pos.resolve_pos("", (1920, 1080)) == pos.default_pos((1920, 1080))
        assert pos.resolve_pos("垃圾", (1920, 1080)) == pos.default_pos((1920, 1080))
        assert pos.resolve_pos("99999,99999", (1920, 1080)) == (1848, 1008)


# ============================================================
# ② GUI 降级判定（PyQt6）
# ============================================================
class TestShouldRunGui:
    def test_no_gui_flag(self):
        from wwdm_app import gui
        assert gui.should_run_gui(argv=["--no-gui"], env={}, has_qt=True) is False

    def test_env_flag(self):
        from wwdm_app import gui
        assert gui.should_run_gui(argv=[], env={"WWDMUI_NO_GUI": "1"},
                                  has_qt=True) is False
        assert gui.should_run_gui(argv=[], env={"WWDMUI_NO_GUI": "true"},
                                  has_qt=True) is False

    def test_no_qt(self):
        from wwdm_app import gui
        assert gui.should_run_gui(argv=[], env={}, has_qt=False) is False

    def test_all_clear_runs_gui(self, monkeypatch):
        from wwdm_app import gui
        monkeypatch.setattr(gui, "_has_display", lambda: True)
        assert gui.should_run_gui(argv=[], env={}, has_qt=True) is True


# ============================================================
# ③ 开机自启（winreg 全打桩，不碰真注册表）
# ============================================================
class _FakeKey:
    def __init__(self, store=None, raise_on_query=None):
        self.store = store if store is not None else {}
        self.written = {}
        self.deleted = []
        self._raise_on_query = raise_on_query

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestAutostart:
    @staticmethod
    def _fake_winreg(**funcs):
        ns = {"HKEY_CURRENT_USER": 1, "KEY_SET_VALUE": 2, "REG_SZ": 1}
        ns.update(funcs)
        return type("W", (), ns)

    def test_startup_command_dev_mode(self, monkeypatch):
        monkeypatch.delattr(autostart.sys, "frozen", raising=False)
        cmd = autostart.startup_command()
        assert cmd.endswith('run.py"')
        assert "python" in cmd.lower()

    def test_startup_command_frozen(self, monkeypatch):
        monkeypatch.setattr(autostart.sys, "frozen", True, raising=False)
        monkeypatch.setattr(autostart.sys, "executable", r"C:\app\run.exe")
        assert autostart.startup_command() == '"C:\\app\\run.exe"'

    def test_disabled_when_no_winreg(self, monkeypatch):
        monkeypatch.setattr(autostart, "winreg", None)
        assert autostart.is_enabled() is False
        assert autostart.enable() is False
        assert autostart.disable() is False
        assert autostart.sync(True) is False

    def test_is_enabled_true(self, monkeypatch):
        store = {"wwdmui": "cmd"}

        def QueryValueEx(key, name):
            if name in store:
                return store[name], 1
            raise FileNotFoundError(name)

        monkeypatch.setattr(autostart, "winreg",
                            self._fake_winreg(OpenKey=lambda *a, **k: _FakeKey(),
                                              QueryValueEx=QueryValueEx))
        assert autostart.is_enabled() is True

    def test_is_enabled_false(self, monkeypatch):
        def QueryValueEx(key, name):
            raise FileNotFoundError(name)

        monkeypatch.setattr(autostart, "winreg",
                            self._fake_winreg(OpenKey=lambda *a, **k: _FakeKey(),
                                              QueryValueEx=QueryValueEx))
        assert autostart.is_enabled() is False

    def test_enable_writes_value(self, monkeypatch):
        written = {}

        def SetValueEx(key, name, reserved, typ, value):
            written.update({"name": name, "type": typ, "value": value})

        monkeypatch.setattr(autostart.sys, "frozen", True, raising=False)
        monkeypatch.setattr(autostart.sys, "executable", r"C:\app\run.exe")
        monkeypatch.setattr(autostart, "winreg",
                            self._fake_winreg(OpenKey=lambda *a, **k: _FakeKey(),
                                              SetValueEx=SetValueEx))
        assert autostart.enable() is True
        assert written == {"name": "wwdmui", "type": 1,
                           "value": '"C:\\app\\run.exe"'}

    def test_disable_missing_value_is_ok(self, monkeypatch):
        deleted = []

        def DeleteValue(key, name):
            deleted.append(name)
            raise FileNotFoundError(name)

        monkeypatch.setattr(autostart, "winreg",
                            self._fake_winreg(OpenKey=lambda *a, **k: _FakeKey(),
                                              DeleteValue=DeleteValue))
        assert autostart.disable() is True
        assert deleted == ["wwdmui"]

    def test_sync_routes(self, monkeypatch):
        calls = []
        monkeypatch.setattr(autostart, "enable", lambda: calls.append("on") or True)
        monkeypatch.setattr(autostart, "disable", lambda: calls.append("off") or True)
        assert autostart.sync(True) is True
        assert autostart.sync(False) is True
        assert calls == ["on", "off"]


# ============================================================
# ④ 设置 API 校验
# ============================================================
@pytest.fixture(autouse=True)
def _snapshot_settings():
    """整字典快照还原（POST /api/settings 是合并语义，只还原关心的键会污染后续）。"""
    old = dict(svc.settings)
    yield
    svc.settings.clear()
    svc.settings.update(old)


class TestSettingsApi:
    def test_defaults(self):
        assert config.DEFAULT_SETTINGS["fab_visible"] is True
        assert config.DEFAULT_SETTINGS["fab_pos"] == ""
        assert config.DEFAULT_SETTINGS["autostart"] is False

    def test_save_fab_flags(self, client, origin):
        r = client.post("/api/settings", headers=origin,
                        json={"fab_visible": False, "autostart": True})
        assert r.get_json()["ok"]
        assert svc.settings["fab_visible"] is False
        assert svc.settings["autostart"] is True

    def test_save_fab_pos_ok(self, client, origin):
        r = client.post("/api/settings", headers=origin, json={"fab_pos": "300,400"})
        assert r.get_json()["ok"]
        assert svc.settings["fab_pos"] == "300,400"

    @pytest.mark.parametrize("bad", ["", "  ", "垃圾", "1,2,3", "-1,5", "abc"])
    def test_save_fab_pos_dirty_falls_back_empty(self, client, origin, bad):
        svc.settings["fab_pos"] = "300,400"
        r = client.post("/api/settings", headers=origin, json={"fab_pos": bad})
        assert r.get_json()["ok"]
        assert svc.settings["fab_pos"] == ""

    def test_missing_keys_keep_current(self, client, origin):
        """别的测试/前端只发部分字段时，不许把用户的悬浮球配置冲掉。"""
        svc.settings["fab_visible"] = False
        svc.settings["autostart"] = True
        svc.settings["fab_pos"] = "10,20"
        r = client.post("/api/settings", headers=origin,
                        json={"hf_channel": "hf-mirror"})
        assert r.get_json()["ok"]
        assert svc.settings["fab_visible"] is False
        assert svc.settings["autostart"] is True
        assert svc.settings["fab_pos"] == "10,20"

    def test_get_returns_fab_keys(self, client):
        data = client.get("/api/settings").get_json()
        for k in ("fab_visible", "fab_pos", "autostart"):
            assert k in data

    def test_save_never_touches_registry(self, client, origin, monkeypatch):
        """闸门：保存设置只改 settings.json，**不写注册表**。"""
        calls = []
        monkeypatch.setattr(autostart, "sync", lambda d: calls.append(d))
        r = client.post("/api/settings", headers=origin,
                        json={"autostart": True, "fab_visible": False})
        assert r.get_json()["ok"]
        assert calls == []


# ============================================================
# ⑤ 公共动作
# ============================================================
class TestActions:
    def test_ui_url_token(self):
        assert actions.ui_url("h", 1, "t") == "http://h:1/?t=t"
        assert actions.ui_url("h", 1, "") == "http://h:1"
        assert actions.ui_url("h", 1, "t", with_token=False) == "http://h:1"

    def test_autostart_label(self):
        assert actions.autostart_label(True) == "关闭开机自启动"
        assert actions.autostart_label(False) == "开机自动启动"

    def test_copy_url_never_raises(self):
        # 测试解释器多半没 PyQt6 → 返回 False；装了且无 app → 也 False。不抛即可。
        assert actions.copy_url("h", 1, "t") in (True, False)

    def test_toggle_autostart_writes_registry_and_settings(self, monkeypatch):
        calls = []

        class _FakeAS:
            @staticmethod
            def sync(desired):
                calls.append(desired)
                return True

        monkeypatch.setattr(actions, "_autostart", _FakeAS)
        svc.settings["autostart"] = False
        want = actions.toggle_autostart()
        assert want is True
        assert calls == [True]
        assert svc.settings["autostart"] is True

    def test_toggle_autostart_registry_fail_returns_none(self, monkeypatch):
        class _FakeAS:
            @staticmethod
            def sync(desired):
                return False

        monkeypatch.setattr(actions, "_autostart", _FakeAS)
        assert actions.toggle_autostart() is None

    def test_quit_falls_back_to_exit(self, monkeypatch):
        """回环失败 → 直接 os._exit（打桩成 SystemExit，别杀 pytest）。"""
        monkeypatch.setattr(actions, "http_post", lambda *a: False)

        def _fake_exit(code):
            raise SystemExit(code)

        monkeypatch.setattr(actions.os, "_exit", _fake_exit)
        with pytest.raises(SystemExit):
            actions.quit_app("127.0.0.1", 7861)

    def test_quit_posts_loopback(self, monkeypatch):
        calls = []
        monkeypatch.setattr(actions, "http_post",
                            lambda h, p, path: calls.append((h, p, path)) or True)
        actions.quit_app("127.0.0.1", 7861)
        assert calls == [("127.0.0.1", 7861, "/api/system/quit")]


# ============================================================
# ⑥ 发布包白名单
# ============================================================
class TestReleaseWhitelist:
    def test_in_release_keys(self):
        from wwdm_app import release
        for k in ("fab_visible", "fab_pos", "autostart"):
            assert k in release.RELEASE_KEYS

    def test_autostart_blanked_for_buyers(self):
        from wwdm_app import release
        assert "autostart" in release.RELEASE_BLANK
        out = release.build_release_settings(config.DEFAULT_SETTINGS)
        assert out["autostart"] == ""
        assert out["fab_visible"] is True
        assert out["fab_pos"] == ""


# ============================================================
# ⑦ ui_theme 设置键
# ============================================================
class TestThemeSync:
    """v6.4+：网页 ↔ 悬浮球日夜切换必须双向即时（不再有 5s 抑制窗）。"""

    def test_init_js_removed_5s_guard(self):
        p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "wwdm_app", "static", "js", "pages", "init.js")
        src = io.open(p, encoding="utf-8").read()
        assert "_themeLocalAt" not in src          # 旧 5s 抑制已删
        assert "_posting" in src                   # 只压 POST 在途的旧值
        assert "EventSource('/api/theme/stream')" in src
        assert "data-theme" in src and "data-ui-theme" in src

    def test_get_ui_theme_prefers_server(self, monkeypatch):
        monkeypatch.setattr(actions, "http_json",
                            lambda *a, **k: {"ui_theme": "dark"})
        assert actions.get_ui_theme("127.0.0.1", 7862) == "dark"

    def test_get_ui_theme_falls_back_to_settings(self, monkeypatch):
        monkeypatch.setattr(actions, "http_json", lambda *a, **k: None)
        monkeypatch.setitem(config.settings, "ui_theme", "dark")
        assert actions.get_ui_theme("127.0.0.1", 7862) == "dark"


class TestUiThemeSetting:
    def test_default_present(self):
        assert config.DEFAULT_SETTINGS["ui_theme"] == "light"

    def test_release_whitelist(self):
        from wwdm_app import release
        assert "ui_theme" in release.RELEASE_KEYS
        assert "ui_theme" not in release.RELEASE_BLANK

    def test_save_roundtrip(self, client, origin):
        r = client.post("/api/settings", headers=origin, json={"ui_theme": "dark"})
        assert r.status_code == 200
        assert svc.settings["ui_theme"] == "dark"
        r = client.post("/api/settings", headers=origin, json={"ui_theme": "neon"})
        assert r.status_code == 200
        assert svc.settings["ui_theme"] == "light"

    def test_get_includes_ui_theme(self, client):
        data = client.get("/api/settings").get_json()
        assert data.get("ui_theme") in ("light", "dark")


# ============================================================
# ⑧ actions 的 HTTP 客户端（关机 / 日夜间）
# ============================================================
class TestWebThemeServerRendered:
    """网页首屏明暗由服务端 ui_theme 直出（球/托盘切换后刷新即生效，不再被 localStorage 挡）。"""

    def test_index_data_theme_follows_setting(self, client):
        svc.settings["ui_theme"] = "dark"
        html = client.get("/").get_data(as_text=True)
        assert 'data-theme="dark"' in html
        assert 'data-ui-theme="dark"' in html
        svc.settings["ui_theme"] = "light"
        html = client.get("/").get_data(as_text=True)
        assert 'data-theme="light"' in html


class TestGuiActionsHttp:
    def test_schedule_shutdown_posts_seconds(self, monkeypatch):
        calls = []
        monkeypatch.setattr(actions, "http_json",
                            lambda h, p, path, method="GET", payload=None, timeout=3:
                            calls.append((h, p, path, method, payload)) or {"ok": True})
        assert actions.schedule_shutdown("h", 7861, 120) == {"ok": True}
        assert calls == [("h", 7861, "/api/shutdown", "POST", {"delay": 120})]

    def test_cancel_shutdown_posts(self, monkeypatch):
        calls = []
        monkeypatch.setattr(actions, "http_json",
                            lambda h, p, path, method="GET", payload=None, timeout=3:
                            calls.append((path, method)) or {"ok": True})
        actions.cancel_shutdown("h", 7861)
        assert ("/api/shutdown/cancel", "POST") in calls

    def test_shutdown_status_get(self, monkeypatch):
        calls = []
        monkeypatch.setattr(actions, "http_json",
                            lambda h, p, path, method="GET", payload=None, timeout=3:
                            calls.append((path, method)) or {})
        actions.shutdown_status("h", 7861)
        assert ("/api/shutdown/status", "GET") in calls

    def test_set_ui_theme_ok(self, monkeypatch):
        monkeypatch.setattr(actions, "http_json", lambda *a, **k: {"ok": True})
        assert actions.set_ui_theme("h", 7861, "dark") == "dark"

    def test_set_ui_theme_fail_returns_none(self, monkeypatch):
        monkeypatch.setattr(actions, "http_json", lambda *a, **k: {"ok": False})
        assert actions.set_ui_theme("h", 7861, "dark") is None

    def test_set_ui_theme_invalid_normalized(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(actions, "http_json",
                            lambda h, p, path, method="GET", payload=None, timeout=3:
                            seen.update(payload) or {"ok": True})
        assert actions.set_ui_theme("h", 7861, "rainbow") == "light"
        assert seen == {"ui_theme": "light"}

    def test_ui_theme_label_reflects_settings(self, monkeypatch):
        monkeypatch.setitem(config.settings, "ui_theme", "light")
        assert actions.ui_theme_label() == "切换到夜间"
        monkeypatch.setitem(config.settings, "ui_theme", "dark")
        assert actions.ui_theme_label() == "切换到日间"


# ============================================================
# ⑨ PyQt6 主题（纯函数部分）
# ============================================================
class TestQtTheme:
    def test_palette_default_light(self, monkeypatch):
        monkeypatch.setitem(config.settings, "ui_theme", "light")
        p = qt_theme.palette()
        assert p["accent"] == "#4a6fa5"
        assert p["bg"] == "#faf8f5"

    def test_palette_dark(self, monkeypatch):
        monkeypatch.setitem(config.settings, "ui_theme", "dark")
        p = qt_theme.palette()
        assert p["bg"] == "#171a21"
        assert p["accent"] == "#8fb3e0"

    def test_qss_has_core_selectors_and_color(self, monkeypatch):
        monkeypatch.setitem(config.settings, "ui_theme", "light")
        css = qt_theme.qss()
        for sel in ("QMenu", "QPushButton", "QListWidget", "QLineEdit"):
            assert sel in css
        assert "#4a6fa5" in css          # 强调色随调色板注入
        assert "border-radius" in css

    def test_icon_path_exists(self):
        assert os.path.isfile(qt_theme.icon_path())


# ============================================================
# ⑩ 悬浮球右键菜单规格（纯函数）
# ============================================================
class TestQtFabMenu:
    def _labels(self, state):
        return [l for l, k in qt_fab.menu_entries(state) if l]

    def test_required_entries(self):
        labels = self._labels({"autostart": False, "visible": True,
                               "theme_label": "切换到夜间"})
        for need in ("打开管理器页面", "复制访问地址", "切换到夜间",
                     "打开笔记本…", "外观主题", "背景壁纸", "毛玻璃",
                     "定时关机…", "开机自动启动", "隐藏悬浮球", "退出管理器"):
            assert need in labels

    def test_no_tasks_center(self):
        assert "任务中心" not in self._labels({"autostart": False})

    def test_autostart_label_flips(self):
        labels = self._labels({"autostart": True})
        assert "关闭开机自启动" in labels

    def test_toggle_label_follows_visibility(self):
        assert "显示悬浮球" in self._labels({"visible": False})
        assert "隐藏悬浮球" in self._labels({"visible": True})

    def test_keys_order(self):
        keys = [k for l, k in qt_fab.menu_entries({"autostart": False}) if l]
        assert keys == ["open", "copy", "theme", "notes",
                        "look_theme", "look_wall", "look_glass",
                        "shutdown", "autostart", "toggle", "quit"]


# ============================================================
# ⑪ 笔记弹窗的纯逻辑（HTTP / 标签）
# ============================================================
class TestQtNotesPure:
    def test_note_label(self):
        assert qt_notes._note_label(
            {"title": "A", "pinned": 1}).startswith("\U0001F4CC ")
        # v6.4：标签不再出现在列表文案里
        assert "[" not in qt_notes._note_label({"title": "A", "tag": "x"})
        assert qt_notes._note_label({"title": ""}) == "未命名笔记"

    def test_http_json_parses(self, monkeypatch):
        import urllib.request as ureq

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b'{"ok":true,"items":[]}'

        class _Opener:
            def open(self, req, timeout=4):
                return _Resp()

        monkeypatch.setattr(ureq, "build_opener", lambda *a, **k: _Opener())
        assert qt_notes._http_json("h", 1, "/api/notes") == {"ok": True, "items": []}

    def test_http_json_posts_payload(self, monkeypatch):
        import urllib.request as ureq
        captured = {}

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b'{"ok":true,"id":7}'

        class _Opener:
            def open(self, req, timeout=4):
                captured["method"] = req.get_method()
                captured["data"] = req.data
                return _Resp()

        monkeypatch.setattr(ureq, "build_opener", lambda *a, **k: _Opener())
        out = qt_notes._http_json("h", 1, "/api/notes", method="POST",
                                  payload={"title": "t"})
        assert out == {"ok": True, "id": 7}
        assert captured["method"] == "POST"
        assert b"title" in captured["data"]

    def test_http_json_failure_returns_none(self, monkeypatch):
        import urllib.request as ureq

        class _Boom:
            def open(self, req, timeout=4):
                raise OSError("x")

        monkeypatch.setattr(ureq, "build_opener", lambda *a, **k: _Boom())
        assert qt_notes._http_json("h", 1, "/api/notes") is None


class TestMarkdown:
    """笔记编辑器用的 Markdown 渲染（纯函数）。"""

    def test_inline(self):
        out = qt_notes._md_to_html("**b** *i* ~~s~~")
        assert "<b>b</b>" in out
        assert "<i>i</i>" in out
        assert "<s>s</s>" in out

    def test_blocks(self):
        out = qt_notes._md_to_html("# H\n- a\n1. b\n> q")
        assert "<h1>H</h1>" in out
        assert "<li>a</li>" in out
        assert "<li>b</li>" in out
        assert "<blockquote>q</blockquote>" in out

    def test_code_and_link(self):
        out = qt_notes._md_to_html("`x` [y](http://z)")
        assert "<code>x</code>" in out
        assert 'href="http://z"' in out

    def test_escapes_html(self):
        out = qt_notes._md_to_html("<script>")
        assert "&lt;script&gt;" in out



# ============================================================
# ⑫ 依赖方向与延迟 import（结构铁律）
# ============================================================
class TestStructure:
    def _imports(self, path):
        with io.open(path, "rb") as f:
            tree = ast.parse(f.read().decode("utf-8"))
        out = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                out += [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                out.append(node.module or "")
        return out

    def _top_imports(self, path):
        with io.open(path, "rb") as f:
            tree = ast.parse(f.read().decode("utf-8"))
        names = []
        for node in tree.body:
            if isinstance(node, ast.Import):
                names += [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names.append(node.module or "")
                names += [a.name for a in node.names]
        return names

    def _gui_files(self):
        return [fn for fn in os.listdir(GUI_DIR) if fn.endswith(".py")]

    def test_gui_never_imports_business(self):
        """gui → core 单向；反向 import services/blueprints 会拉起全局状态。"""
        for fn in self._gui_files():
            for mod in self._imports(os.path.join(GUI_DIR, fn)):
                assert "services" not in mod and "blueprints" not in mod, \
                    "%s 不许 import 业务层：%s" % (fn, mod)

    def test_gui_no_tkinter_import(self):
        """tk 代码全部移除：gui 下任何模块都不许 import tkinter。"""
        for fn in self._gui_files():
            for mod in self._imports(os.path.join(GUI_DIR, fn)):
                assert "tkinter" not in mod, "%s 仍在 import tkinter：%s" % (fn, mod)

    def test_gui_defers_pyqt6(self):
        """顶层不许 import PyQt6 —— 无 Qt 的测试解释器要能 import 整个包。"""
        for fn in self._gui_files():
            for mod in self._top_imports(os.path.join(GUI_DIR, fn)):
                assert "PyQt6" not in mod, \
                    "%s 顶层 import 了 PyQt6，必须延迟到方法内" % fn

    def test_icon_assets_exist_and_unified(self):
        """exe/托盘/球/网页 favicon 图标统一为 static/images/app.ico。"""
        base = os.path.join(ROOT, "wwdm_app")
        for rel in ("static/images/app.ico", "static/images/logo-16.ico",
                    "static/images/logo-32.ico", "static/images/logo-256.ico",
                    "static/images/logo.png"):
            p = os.path.join(base, rel)
            assert os.path.isfile(p), "缺图标资源: " + rel
            assert os.path.getsize(p) > 0
        assert os.path.isfile(qt_theme.icon_path())
        assert os.path.basename(qt_theme.icon_path()) == "app.ico"
        for old in ("gui/assets/wwdm_cat.ico", "static/favicon.ico",
                    "static/images/wwdm_cat.ico"):
            assert not os.path.exists(os.path.join(base, old)), "旧图标未删除: " + old

    def test_run_py_has_gui_branch(self):
        """run.py 入口三分支：--no-gui / GUI 悬浮球+托盘 / 降级主线程。"""
        with io.open(os.path.join(ROOT, "run.py"), "rb") as f:
            src = f.read().decode("utf-8")
        assert "gui.should_run_gui()" in src
        assert '"--no-gui" in sys.argv' in src
        assert '"--minimized" in sys.argv' in src
        assert "server_dead=dead" in src
        assert "gui.run(port=port" in src
        assert 'settings.get("fab_visible", True)' in src
        assert src.count("app.run(host=host, port=port, debug=False, threaded=True)") == 2

    def test_config_bridge_imports(self):
        """gui → core 的相对导入链（``..core``）真的能走通。"""
        from wwdm_app import gui
        svc.settings["fab_pos"] = ""
        gui.save_fab_pos(7, 9)
        assert svc.settings["fab_pos"] == "7,9"
        actions.save_flag("fab_visible", False)
        assert svc.settings["fab_visible"] is False
        svc.settings["fab_visible"] = True      # 还原
        svc.settings["autostart"] = True
        assert actions.autostart_enabled() is True
        svc.settings["autostart"] = False
        assert actions.autostart_enabled() is False
