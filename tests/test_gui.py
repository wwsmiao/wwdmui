# -*- coding: utf-8 -*-
"""v6.0 桌面悬浮球 + 托盘：位置纯函数 / GUI 降级判定 / 开机自启 / 设置校验 /
托盘命令路由 / 依赖方向。

GUI 本身（tkinter 窗口、Shell_NotifyIcon 托盘）**不可单测**，这里只测能测的：
  * pos 的位置解析、钳制、默认值（纯函数，换分辨率不丢球靠它）；
  * should_run_gui 的三条降级路径；
  * autostart 的注册表读写（winreg 打桩，绝不碰真注册表）；
  * 设置 API 对三个新键的校验 —— 特别是 **保存设置不许写注册表**
    （测试跑 POST 会真改用户环境，这个断言是防回归的闸门）；
  * 托盘的纯逻辑：tooltip 截断、NOTIFYICONDATAW 字段、命令路由
    （打桩 actions / 假 fab，不建真托盘）、dry_run；
  * 依赖方向：gui → core，**不许 import services/blueprints**；
    fab/tray/__init__ 顶层不许 import tkinter（延迟到方法内，否则 import
    包就要 Tk，无 tkinter 的测试解释器直接炸）。
"""
import ast
import ctypes
import io
import os
import threading
import time

import pytest

import wwdm_app.services as svc
from wwdm_app.core import config
from wwdm_app.gui import actions, autostart, pos, tray, tasks_win


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GUI_DIR = os.path.join(ROOT, "wwdm_app", "gui")


def _tkinter_usable():
    """测试解释器（如 ComfyUI 的 python312）可能没装 tkinter。"""
    try:
        import tkinter  # noqa: F401
    except Exception:
        return False
    return True


def _real_desktop():
    """Windows 交互式桌面判定（会话 0 / 无头 CI 建不了真托盘）。"""
    if os.name != "nt" or not _tkinter_usable():
        return False
    try:
        return bool(ctypes.windll.user32.GetShellWindow())
    except Exception:
        return False


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
        # 4K 上拖到右下角的坐标，换到 1080p 笔记本必须被钳回来
        assert pos.clamp_pos(99999, 99999, (1920, 1080)) == (1848, 1008)
        assert pos.clamp_pos(-50, -50, (1920, 1080)) == (8, 8)
        assert pos.clamp_pos(500, 500, (1920, 1080)) == (500, 500)

    def test_default_is_bottom_right_with_taskbar(self):
        assert pos.default_pos((1920, 1080)) == (1848, 968)
        # 极小屏幕：x 还能放下就贴右下，y 放不下就贴顶，绝不出负数
        assert pos.default_pos((100, 100)) == (28, 0)

    def test_resolve_saved_vs_fallback(self):
        assert pos.resolve_pos("300,400", (1920, 1080)) == (300, 400)
        assert pos.resolve_pos("", (1920, 1080)) == pos.default_pos((1920, 1080))
        assert pos.resolve_pos("垃圾", (1920, 1080)) == pos.default_pos((1920, 1080))
        assert pos.resolve_pos("99999,99999", (1920, 1080)) == (1848, 1008)


# ============================================================
# ② GUI 降级判定
# ============================================================
class TestShouldRunGui:
    def test_no_gui_flag(self):
        from wwdm_app import gui
        assert gui.should_run_gui(argv=["--no-gui"], env={}, has_tk=True) is False

    def test_env_flag(self):
        from wwdm_app import gui
        assert gui.should_run_gui(argv=[], env={"WWDMUI_NO_GUI": "1"},
                                  has_tk=True) is False
        assert gui.should_run_gui(argv=[], env={"WWDMUI_NO_GUI": "true"},
                                  has_tk=True) is False

    def test_no_tkinter(self):
        from wwdm_app import gui
        assert gui.should_run_gui(argv=[], env={}, has_tk=False) is False

    def test_all_clear_runs_gui(self, monkeypatch):
        from wwdm_app import gui
        # _has_display 打桩：CI/无显示环境不该让这条用例依赖真实桌面
        monkeypatch.setattr(gui, "_has_display", lambda: True)
        assert gui.should_run_gui(argv=[], env={}, has_tk=True) is True


# ============================================================
# ③ 开机自启（winreg 全打桩，不碰真注册表）
# ============================================================
class _FakeKey:
    """假装是 winreg 打开的键；记录写操作，按 script 演读操作。"""

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
    """winreg 的打桩口径：autostart.py 用的是**模块级函数**
    （``winreg.OpenKey(...)`` / ``winreg.QueryValueEx(k, name)``，真实 winreg
    就是这个用法），所以假件必须把这些函数挂在假 winreg 模块上，挂在假 key
    上不生效（「模块内直接名字引用打在源模块上」铁律的镜像版）。
    """

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

        def OpenKey(*a, **k):
            return _FakeKey()

        monkeypatch.setattr(autostart, "winreg",
                            self._fake_winreg(OpenKey=OpenKey,
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
            raise FileNotFoundError(name)   # 值本来就不存在

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
    """整字典快照还原（口径同 test_theme.py：POST /api/settings 是合并语义，
    只还原自己关心的键会污染后续文件的沙箱断言）。"""
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
        """闸门：保存设置只改 settings.json，**不写注册表**。

        autostart 的注册表同步刻意放在 run.py 启动时做 —— 否则测试里跑一次
        POST /api/settings 就会真改用户的开机启动项。
        """
        calls = []
        monkeypatch.setattr(autostart, "sync", lambda d: calls.append(d))
        r = client.post("/api/settings", headers=origin,
                        json={"autostart": True, "fab_visible": False})
        assert r.get_json()["ok"]
        assert calls == []


# ============================================================
# ⑤ 托盘（Shell_NotifyIcon ctypes）—— 只测不建真托盘的部分
# ============================================================
class TestTray:
    """托盘的动作路由打在这里：WndProc / Shell_NotifyIcon 走真 Win32 太依赖
    桌面环境（且 CI 无显示会挂），冒烟口子 smoke_ms 留给 D:\\python312 手工跑。

    v6.0(4) 起例外一个：``test_real_tray_messages_survive_mainloop`` 会在
    **有 tkinter + 交互式桌面**时打真消息跑真 mainloop —— 它守的是一次进程级
    Fatal Error（WndProc 里调 tkinter），回归了 pytest 会直接死在该测试里。
    """

    @staticmethod
    def _poison_root():
        """毒 root：任何 tkinter 调用都炸（WndProc 碰它就现形）。"""

        class _Poison:
            def after(self, *a, **k):
                raise AssertionError("WndProc 不许调 tkinter（GIL 崩溃根源）")

            def after_cancel(self, *a, **k):
                raise AssertionError("WndProc 不许调 tkinter")

        return _Poison()

    @staticmethod
    def _fake_windll(monkeypatch):
        class _FakeWindll:
            class user32:
                @staticmethod
                def DefWindowProcW(*a):
                    return 0

        monkeypatch.setattr(tray.ctypes, "windll", _FakeWindll, raising=False)

    # ---------------- v6.0(4) WndProc GIL 崩溃回归 ----------------
    def test_wndproc_never_touches_tkinter(self, monkeypatch):
        """🔴 崩溃回归（单测版）：WndProc 里调 ``root.after`` 会把线程局部
        ``tcl_tstate`` 置 NULL，同一次 Tcl_DoOneEvent 内随后派发的定时器事件
        → ``PyEval_RestoreThread(NULL)`` → 进程 Fatal Error（点一次托盘崩一次，
        已写复现脚本实证）。毒 root 驱动真 _wndproc：它只许纯 Python 入队。
        """
        self._fake_windll(monkeypatch)
        ic = tray.TrayIcon(7861)
        ic._root = self._poison_root()
        ic._wndproc(0, tray.WM_TRAY, 1, tray.WM_LBUTTONUP)
        ic._wndproc(0, tray.WM_TRAY, 1, tray.WM_RBUTTONUP)
        ic._wndproc(0, tray.WM_COMMAND, tray.CMD_OPEN, 0)
        assert len(ic._pending) == 3
        assert all(callable(f) for f in ic._pending)

    def test_wndproc_without_root_is_noop(self, monkeypatch):
        """没 attach 过（_root=None）时 WndProc 不许炸（冒烟/单测路径）。"""
        self._fake_windll(monkeypatch)
        ic = tray.TrayIcon(7861)
        ic._wndproc(0, tray.WM_TRAY, 1, tray.WM_LBUTTONUP)
        assert ic._pending == []

    def test_drain_runs_pending_in_order(self):
        ran = []
        ic = tray.TrayIcon(7861)
        ic._root = type("R", (), {"after": lambda *a: None})()
        ic._enqueue(lambda: ran.append(1))
        ic._enqueue(lambda: ran.append(2))
        ic._drain_pending()
        assert ran == [1, 2]
        assert ic._pending == []

    def test_drain_failure_does_not_block_rest(self):
        """单个动作抛异常不许阻断后面的（菜单动作互不相干）。"""
        ran = []

        def boom():
            raise RuntimeError("x")

        ic = tray.TrayIcon(7861)
        ic._root = type("R", (), {"after": lambda *a: None})()
        ic._enqueue(boom)
        ic._enqueue(lambda: ran.append("ok"))
        ic._drain_pending()
        assert ran == ["ok"]

    def test_drain_reentrancy_guard(self):
        """模态循环（messagebox / tk_popup 嵌套事件循环）里定时器重入：
        只放一层消费，且不会挂出双份定时器链。"""
        order, armed = [], []

        class _Root:
            def after(self, ms, fn):
                armed.append(ms)
                return len(armed)

        ic = tray.TrayIcon(7861)
        ic._root = _Root()

        def modal():
            order.append("in")
            ic._drain_pending()               # 重入：应被卫兵挡掉
            order.append("out")

        ic._enqueue(modal)
        ic._drain_pending()
        assert order == ["in", "out"]
        assert armed == [tray._DRAIN_MS]

    def test_arm_drain_fast_cadence(self):
        armed = []

        class _Root:
            def after(self, ms, fn):
                armed.append((ms, fn))
                return 7

        ic = tray.TrayIcon(7861)
        ic._root = _Root()
        ic._arm_drain()
        assert armed[0][0] == tray._DRAIN_MS
        assert ic._drain_id == 7

    def test_cancel_drain_clears_id(self):
        cancelled = []

        class _Root:
            def after_cancel(self, aid):
                cancelled.append(aid)

        ic = tray.TrayIcon(7861)
        ic._root = _Root()
        ic._drain_id = 42
        ic._cancel_drain()
        assert cancelled == [42]
        assert ic._drain_id is None

    @pytest.mark.skipif(os.name != "nt" or not _tkinter_usable(),
                        reason="需要 Windows + tkinter（假 Menu，不弹真菜单）")
    def test_drain_runs_post_menu_via_fake_menu(self, monkeypatch):
        """右键菜单动作在消费上下文里真的跑通（Menu/tk_popup 全打桩）。"""
        import tkinter
        built = []

        class _FakeMenu:
            def __init__(self, *a, **k):
                built.append(self)
                self.popped = None

            def add_command(self, **k):
                pass

            def add_separator(self):
                pass

            def tk_popup(self, x, y):
                self.popped = (x, y)

            def grab_release(self):
                pass

        monkeypatch.setattr(tkinter, "Menu", _FakeMenu)
        monkeypatch.setattr(tray.actions, "autostart_enabled", lambda: False)
        ic = tray.TrayIcon(7861, host="127.0.0.1", token="")
        ic._root = type("R", (), {"after": lambda *a: None})()
        ic._enqueue(ic._post_menu)
        ic._drain_pending()
        assert len(built) == 1
        assert built[0].popped is not None

    @pytest.mark.skipif(not _real_desktop(),
                        reason="需要 tkinter + 交互式桌面（会话 0 / CI 跳过）")
    def test_real_tray_messages_survive_mainloop(self, monkeypatch):
        """🔴 真·崩溃回归：真托盘 + 真 WM_TRAY 消息 + 真 mainloop。

        修复前（WndProc 里 ``root.after``）：一条 WM_TRAY 就能让整个进程
        Fatal Error（PyEval_RestoreThread NULL）。谁把 ``root.after`` 写回
        WndProc，pytest 就会直接死在本测试里 —— 不可能看不见。
        """
        import tkinter as tk
        from ctypes import wintypes

        u32 = ctypes.windll.user32
        u32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                     wintypes.WPARAM, wintypes.LPARAM]
        u32.PostMessageW.restype = wintypes.BOOL

        ran = []
        monkeypatch.setattr(tray.actions, "open_ui",
                            lambda h, p, t="": ran.append((h, p, t)))

        root = tk.Tk()
        root.withdraw()
        try:
            icon = tray.TrayIcon(7861, host="127.0.0.1", token="",
                                 server_dead=threading.Event())
            assert icon.attach(root)

            def _driver():
                for _ in range(20):
                    u32.PostMessageW(icon._hwnd, tray.WM_TRAY, 1,
                                     tray.WM_LBUTTONUP)
                    time.sleep(0.03)

            threading.Thread(target=_driver, daemon=True).start()
            root.after(1500, root.quit)
            root.mainloop()
            assert len(ran) == 20      # 每条消息的动作都被消费执行
            assert icon._pending == []
        finally:
            try:
                root.destroy()
            except Exception:
                pass

    def test_tip_truncated(self):
        assert tray.format_tip("abc") == "abc"
        assert tray.format_tip(None) == "None"
        assert len(tray.format_tip("x" * 300)) == 127

    def test_tip_shows_dead_server(self):
        ev = threading.Event()
        ic = tray.TrayIcon(7861, server_dead=ev)
        assert "服务已停止" not in ic._tip_text()
        ev.set()
        assert "服务已停止" in ic._tip_text()

    def test_command_ids_unique(self):
        ids = [tray.CMD_OPEN, tray.CMD_COPY, tray.CMD_AUTOSTART,
               tray.CMD_TOGGLE_FAB, tray.CMD_QUIT]
        assert len(set(ids)) == 5

    def test_nid_fields(self):
        """NOTIFYICONDATAW 字段顺序/容量（cbSize 错 → Shell_NotifyIcon 静默失败）。

        超长串赋值是 **ValueError 而非静默截断** —— 这就是 format_tip 必须
        先把 tooltip 钳到 127 的原因（否则一条长 tip 直接炸托盘）。
        """
        nid = tray.NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(tray.NOTIFYICONDATAW)
        nid.szTip = "hi"
        assert nid.szTip == "hi"
        assert nid.cbSize > 0
        with pytest.raises(ValueError):
            nid.szTip = "x" * 500

    def test_dry_run(self):
        assert tray.TrayIcon(7861).start(dry_run=True) is True

    def test_dispatch_open(self, monkeypatch):
        calls = []
        monkeypatch.setattr(tray.actions, "open_ui",
                            lambda h, p, t="": calls.append((h, p, t)) or True)
        tray.TrayIcon(7861, host="127.0.0.1", token="tk")._dispatch(tray.CMD_OPEN)
        assert calls == [("127.0.0.1", 7861, "tk")]

    def test_dispatch_copy_uses_root(self):
        class _Clip:
            def __init__(self):
                self.buf = None

            def clipboard_clear(self):
                self.buf = ""

            def clipboard_append(self, s):
                self.buf = s

        clip = _Clip()
        ic = tray.TrayIcon(7861, host="127.0.0.1", token="")
        ic._root = clip
        ic._dispatch(tray.CMD_COPY)
        assert clip.buf == "http://127.0.0.1:7861"

    def test_dispatch_autostart_syncs_fab(self, monkeypatch):
        monkeypatch.setattr(tray.actions, "toggle_autostart", lambda: True)
        fab_stub = type("F", (), {"autostart_flag": False})()
        tray.TrayIcon(7861, fab=fab_stub)._dispatch(tray.CMD_AUTOSTART)
        assert fab_stub.autostart_flag is True

    def test_dispatch_autostart_none_keeps_fab(self, monkeypatch):
        """注册表写失败（toggle 返回 None）不许把 fab 缓存改成相反值。"""
        monkeypatch.setattr(tray.actions, "toggle_autostart", lambda: None)
        fab_stub = type("F", (), {"autostart_flag": True})()
        tray.TrayIcon(7861, fab=fab_stub)._dispatch(tray.CMD_AUTOSTART)
        assert fab_stub.autostart_flag is True

    def test_dispatch_quit(self, monkeypatch):
        calls = []
        monkeypatch.setattr(tray.actions, "quit_app",
                            lambda h, p: calls.append((h, p)))
        tray.TrayIcon(7861, host="h")._dispatch(tray.CMD_QUIT)
        assert calls == [("h", 7861)]

    def test_toggle_fab_show_hide(self):
        state = {"visible": False, "saved": None}

        class _Fab:
            def is_visible(self):
                return state["visible"]

            def show(self):
                state["visible"] = True

            def hide(self, save=True):
                state["visible"] = False
                state["saved"] = save

        ic = tray.TrayIcon(7861, fab=_Fab())
        ic._toggle_fab()                       # 不可见 → 显示
        assert state["visible"] is True
        ic._toggle_fab()                       # 可见 → 隐藏并存盘
        assert state["visible"] is False
        assert state["saved"] is True

    def test_toggle_fab_no_fab_is_noop(self):
        tray.TrayIcon(7861, fab=None)._toggle_fab()   # 不许抛

    def test_fab_label_follows_visibility(self):
        vis = type("F", (), {"is_visible": lambda self: True})()
        hid = type("F", (), {"is_visible": lambda self: False})()
        assert tray.TrayIcon(7861, fab=vis)._fab_label() == "隐藏悬浮球"
        assert tray.TrayIcon(7861, fab=hid)._fab_label() == "显示悬浮球"
        assert tray.TrayIcon(7861, fab=None)._fab_label() == "显示悬浮球"


# ============================================================
# ⑥ 托盘/悬浮球公共动作（gui.actions）
# ============================================================
# ============================================================
# ⑥-b 图标解码（gui.icoimage，纯函数无 tkinter）与悬浮球可见性 / run 绑定
# ============================================================
class TestIcoImage:
    """icoimage 纯函数（无 tkinter）：解码 / hover 变体 / 资源文件。"""

    def test_asset_is_png_ico(self):
        from wwdm_app.gui import icoimage
        png = icoimage.ico_png_bytes(icoimage.default_icon_path())
        assert png is not None and png[:8] == b"\x89PNG\r\n\x1a\n"

    def test_decode_yields_rgba(self):
        from wwdm_app.gui import icoimage
        png = icoimage.ico_png_bytes(icoimage.default_icon_path())
        dec = icoimage.decode_png(png)
        assert dec is not None
        w, h, rgba = dec
        assert w == 64 and h == 64
        assert len(rgba) == 64 * 64 * 4
        # 中心白猫脸不透明；角落透明
        i_center = (32 * 64 + 32) * 4
        assert rgba[i_center + 3] == 255
        assert rgba[3] == 0

    def test_recolor_keeps_alpha_darkens_rgb(self):
        from wwdm_app.gui import icoimage
        png = icoimage.ico_png_bytes(icoimage.default_icon_path())
        dec = icoimage.decode_png(png)
        w, h, rgba = dec
        hover = icoimage.recolor(png, 0.85)
        assert hover is not None and hover[:8] == b"\x89PNG\r\n\x1a\n"
        hd = icoimage.decode_png(hover)
        assert hd is not None
        i_center = (32 * 64 + 32) * 4
        # R 暗化（<255）、alpha 不变
        assert hd[2][i_center] < rgba[i_center]
        assert hd[2][i_center + 3] == rgba[i_center + 3]

    @pytest.mark.skipif(not _tkinter_usable(),
                        reason="需要 tkinter（PhotoImage 创建依赖真 Tk）")
    def test_ball_images_returns_pair(self):
        import tkinter as tk
        from wwdm_app.gui import icoimage
        tk.Tk().withdraw()           # 提供默认 root（PhotoImage 需要）
        imgs = icoimage.ball_images(icoimage.default_icon_path(), 64)
        assert imgs is not None and len(imgs) == 2


class TestFabVisibility:
    """悬浮球可见性 API（v6.0(5) 修的回归：tray 调 fab.hide 曾缺失 →
    菜单「显示悬浮球」点了没反应）。"""

    def test_show_requires_root(self):
        from wwdm_app.gui import fab as _fab
        b = _fab.FabBall(7861)
        assert b.show() is False        # root 未绑（gui.run 必须先绑）

    def test_hide_without_window_is_safe(self, monkeypatch):
        from wwdm_app.gui import fab as _fab
        saved = {}
        monkeypatch.setattr(_fab, "_save_flag", lambda k, v: saved.update({k: v}))
        monkeypatch.setattr(_fab.icoimage, "ball_images", lambda *a: None)
        b = _fab.FabBall(7861)
        b.hide(save=True)               # 没建过窗也不许炸
        assert saved == {"fab_visible": False}

    def test_tray_hide_calls_real_fab_hide(self, monkeypatch):
        """端到端：托盘 _toggle_fab 必须走 fab.hide（此前调缺失的 _hide →
        AttributeError 被 except 吞掉，回归闸门）。"""
        from wwdm_app.gui import fab as _fab, tray as _tray
        saved = {}
        monkeypatch.setattr(_fab, "_save_flag", lambda k, v: saved.update({k: v}))
        monkeypatch.setattr(_fab.icoimage, "ball_images", lambda *a: None)
        b = _fab.FabBall(7861)
        monkeypatch.setattr(b, "is_visible", lambda: True)
        ic = tray.TrayIcon(7861, fab=b)
        ic._toggle_fab()                # 可见 → 隐藏并存盘
        assert saved == {"fab_visible": False}


class TestRunBindsBall:
    """v6.0(5) 回归：fab_visible=False 也必须建 FabBall 实例并绑 root，
    否则托盘「显示悬浮球」fab=None 永远无功能。"""

    def test_run_always_creates_ball_and_binds_root(self, monkeypatch):
        import sys
        import types
        from wwdm_app import gui
        from wwdm_app.gui import fab as _fab, tray as _tray

        fake_tk = types.ModuleType("tkinter")

        class _Stop(Exception):
            pass

        class _Root:
            def withdraw(self):
                pass

            def after(self, *a):
                return 1

            def after_cancel(self, *a):
                pass

            def mainloop(self):
                raise _Stop()           # 让 run() 在 mainloop 处立刻返回

            def destroy(self):
                pass

        fake_tk.Tk = lambda: _Root()
        monkeypatch.setitem(sys.modules, "tkinter", fake_tk)

        made = {}

        class _Ball:
            def __init__(self, *args, **kw):
                made["ball"] = self
                self.root = None
                self._win = None

            def build(self, root):
                made["built_root"] = root

            def is_visible(self):
                return "built_root" in made

        monkeypatch.setattr(_fab, "FabBall", _Ball)
        monkeypatch.setattr(_fab, "enable_dpi_awareness", lambda: True)

        class _Icon:
            def __init__(self, *args, **kw):
                made["icon_fab"] = kw.get("fab")

            def attach(self, root):
                made["icon_attach_root"] = root
                made["attached"] = True
                return True

            def destroy(self):
                pass

        monkeypatch.setattr(_tray, "TrayIcon", _Icon)

        with pytest.raises(_Stop):
            gui.run(port=7861, fab_visible=False)
        assert made["ball"] is not None           # 实例必须建
        assert made["ball"].root is not None      # root 必须绑（菜单可拉球）
        assert "built_root" not in made           # fab_visible=False 不建窗
        assert made["icon_fab"] is made["ball"]   # 托盘拿得到球
        # 真绑上的 root 与 attach 收到的 root 是同一个对象（共享 root 链）
        assert made["ball"].root is made["icon_attach_root"]


class TestActions:
    def test_ui_url_token(self):
        assert actions.ui_url("h", 1, "t") == "http://h:1/?t=t"
        assert actions.ui_url("h", 1, "") == "http://h:1"
        assert actions.ui_url("h", 1, "t", with_token=False) == "http://h:1"

    def test_autostart_label(self):
        assert actions.autostart_label(True) == "关闭开机自启动"
        assert actions.autostart_label(False) == "开机自动启动"

    def test_toggle_autostart_writes_registry_and_settings(self, monkeypatch):
        calls = []

        class _FakeAS:
            @staticmethod
            def sync(desired):
                calls.append(desired)
                return True

        monkeypatch.setattr(tray.actions, "_autostart", _FakeAS)
        svc.settings["autostart"] = False
        want = tray.actions.toggle_autostart()
        assert want is True
        assert calls == [True]
        assert svc.settings["autostart"] is True

    def test_toggle_autostart_registry_fail_returns_none(self, monkeypatch):
        class _FakeAS:
            @staticmethod
            def sync(desired):
                return False

        monkeypatch.setattr(tray.actions, "_autostart", _FakeAS)
        assert tray.actions.toggle_autostart() is None

    def test_quit_falls_back_to_exit(self, monkeypatch):
        """回环失败 → 直接 os._exit，不把 GUI 留在桌上。

        ⚠️ os._exit 是**立即终止进程**（不抛异常、不走 finally）—— 直接调会把
        pytest 本身杀掉（症状：进度条停在中途、无汇总无 junit）。必须打桩成
        抛 SystemExit 才能观测。
        """
        monkeypatch.setattr(tray.actions, "http_post", lambda *a: False)

        def _fake_exit(code):
            raise SystemExit(code)

        monkeypatch.setattr(tray.actions.os, "_exit", _fake_exit)
        with pytest.raises(SystemExit):
            tray.actions.quit_app("127.0.0.1", 7861)

    def test_quit_posts_loopback(self, monkeypatch):
        calls = []
        monkeypatch.setattr(tray.actions, "http_post",
                            lambda h, p, path: calls.append((h, p, path)) or True)
        tray.actions.quit_app("127.0.0.1", 7861)
        assert calls == [("127.0.0.1", 7861, "/api/system/quit")]


# ============================================================
# ⑦ 发布包白名单
# ============================================================
class TestReleaseWhitelist:
    def test_in_release_keys(self):
        from wwdm_app import release
        for k in ("fab_visible", "fab_pos", "autostart"):
            assert k in release.RELEASE_KEYS

    def test_autostart_blanked_for_buyers(self):
        """不能替买家决定开机自启：出包置空（=False），想用自己勾。"""
        from wwdm_app import release
        assert "autostart" in release.RELEASE_BLANK
        out = release.build_release_settings(config.DEFAULT_SETTINGS)
        assert out["autostart"] == ""
        assert out["fab_visible"] is True
        assert out["fab_pos"] == ""


# ============================================================
# ⑧ 依赖方向与延迟 import（结构铁律）
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

    def test_gui_never_imports_business(self):
        """gui → core 单向；反向 import services/blueprints 会拉起全局状态。"""
        for fn in os.listdir(GUI_DIR):
            if not fn.endswith(".py"):
                continue
            for mod in self._imports(os.path.join(GUI_DIR, fn)):
                assert "services" not in mod and "blueprints" not in mod, \
                    "%s 不许 import 业务层：%s" % (fn, mod)

    def _assert_no_top_tkinter(self, fn):
        """顶层不许 import tkinter —— 否则 import wwdm_app.gui 就要 Tk，
        pytest / 无 tkinter 的精简解释器直接炸。tkinter 只许出现在方法体里。"""
        path = os.path.join(GUI_DIR, fn)
        with io.open(path, "rb") as f:
            tree = ast.parse(f.read().decode("utf-8"))
        top = {n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))}
        for node in top:
            names = ([a.name for a in node.names]
                     if isinstance(node, ast.Import) else [node.module or ""])
            assert not any("tkinter" in n for n in names), \
                "%s 顶层 import 了 tkinter，必须延迟到方法内" % fn

    def test_fab_defers_tkinter(self):
        self._assert_no_top_tkinter("fab.py")

    def test_tray_defers_tkinter(self):
        """tray.py 同理：ctypes 消息窗 + tkinter 菜单都只在方法里建。"""
        self._assert_no_top_tkinter("tray.py")

    def test_package_defers_tkinter(self):
        """__init__.py 的 run() 里才 import tkinter，顶层不许。"""
        self._assert_no_top_tkinter("__init__.py")

    def test_icon_assets_exist_and_unified(self):
        """exe/托盘/球/网页 favicon 图标统一为 wwdm_cat.ico。"""
        from wwdm_app.gui import icoimage
        base = os.path.join(ROOT, "wwdm_app")
        for rel in ("gui/assets/wwdm_cat.ico", "static/favicon.ico"):
            p = os.path.join(base, rel)
            assert os.path.isfile(p), "缺图标资源: " + rel
        # 随包资产必须含 PNG 条目（托盘 LoadImageW / 球 PhotoImage 都用它）
        png = icoimage.ico_png_bytes(os.path.join(base, "gui/assets/wwdm_cat.ico"))
        assert png is not None, "托盘/球图标必须含 PNG 条目"

    def test_run_py_has_gui_branch(self):
        """run.py 入口三分支：--no-gui / GUI 悬浮球+托盘 / 降级主线程。"""
        with io.open(os.path.join(ROOT, "run.py"), "rb") as f:
            src = f.read().decode("utf-8")
        assert "gui.should_run_gui()" in src
        assert '"--no-gui" in sys.argv' in src
        assert '"--minimized" in sys.argv' in src
        assert "server_dead=dead" in src
        # v6.0(2)：GUI 分支走 gui.run 协调入口，且 fab_visible 真正生效
        assert "gui.run(port=port" in src
        assert 'settings.get("fab_visible", True)' in src
        # 降级路径仍在：app.run 主线程（两处 except 里的日志是同一模式的两次出现）
        assert src.count("app.run(host=host, port=port, debug=False, threaded=True)") == 2

    def test_config_bridge_imports(self):
        """gui → core 的相对导入链（``..core``）真的能走通。

        v6.0 踩过：写成 ``.core`` 解析成 ``wwdm_app.gui.core`` 直接
        ModuleNotFoundError，而 dry_run 冒烟在建窗之前就返回、覆盖不到 ——
        这个用例就是堵那个盲区的（打桩在 conftest，不会写真 settings.json）。
        v6.0(2) 起 fab/tray 的 settings 访问都走 :mod:`gui.actions`，桥只剩
        这一处，更要盯死。
        """
        from wwdm_app.gui import fab
        from wwdm_app import gui
        svc.settings["fab_pos"] = "12,34"
        assert fab._saved_pos() == "12,34"
        svc.settings["fab_pos"] = ""
        gui.save_fab_pos(7, 9)
        assert svc.settings["fab_pos"] == "7,9"
        fab._save_flag("fab_visible", False)
        assert svc.settings["fab_visible"] is False
        svc.settings["fab_visible"] = True      # 还原，别污染同文件后续用例
        svc.settings["autostart"] = True
        assert tray.actions.autostart_enabled() is True
        svc.settings["autostart"] = False
        assert tray.actions.autostart_enabled() is False


# ============================================================
# ⑨ ui_theme 设置键（球/托盘菜单切日/夜间）
# ============================================================
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
        # 非法值回退 light
        r = client.post("/api/settings", headers=origin, json={"ui_theme": "neon"})
        assert r.status_code == 200
        assert svc.settings["ui_theme"] == "light"

    def test_get_includes_ui_theme(self, client):
        data = client.get("/api/settings").get_json()
        assert data.get("ui_theme") in ("light", "dark")


class TestGuiActionsHttp:
    """actions 新增的 HTTP 客户端（关机 / 日夜间）。全部走禁代理回环，失败返回 None。"""

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
# ⑩ 任务中心窗口（gui.tasks_win）—— 仅测逻辑，不建真 Tk
# ============================================================
class TestTasksWin:
    def test_open_tasks_none_root_noop(self):
        tasks_win.open_tasks("127.0.0.1", 7861, None)        # 不许抛
        assert ("127.0.0.1:7861") not in tasks_win._WINS

    def test_http_json_parses(self, monkeypatch):
        import urllib.request as ureq

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b'{"tasks":[]}'

        class _Opener:
            def open(self, req, timeout=3):
                return _Resp()

        monkeypatch.setattr(ureq, "build_opener", lambda *a, **k: _Opener())
        assert tasks_win._http_json("h", 1, "/api/tasks") == {"tasks": []}

    def test_http_json_failure_returns_none(self, monkeypatch):
        import urllib.request as ureq

        class _Boom:
            def open(self, req, timeout=3):
                raise OSError("boom")

        monkeypatch.setattr(ureq, "build_opener", lambda *a, **k: _Boom())
        assert tasks_win._http_json("h", 1, "/api/tasks") is None

    def test_render_maps_rows(self):
        class _Tree:
            def __init__(self):
                self.rows = []
                self.deletes = 0

            def get_children(self, *a):
                return []

            def delete(self, *a):
                self.deletes += 1

            def insert(self, *a, **k):
                self.rows.append(k.get("values"))

        class _Win:
            def __init__(self):
                self.tree = _Tree()
                self.exists = True

            def winfo_exists(self):
                return self.exists

        w = tasks_win._TasksWindow("127.0.0.1", 7861, None)
        fw = _Win()
        w.win = fw
        w._tree = fw.tree
        data = {"tasks": [
            {"task_id": "d1", "kind": "download", "title": "下模型",
             "status": "running", "progress": 42.0},
            {"task_id": "c1", "kind": "clone", "title": "克隆插件",
             "status": "done", "progress": None},
        ]}
        w._render(data)
        assert fw.tree.rows[0] == ("下模型", "download", "running", "42%")
        assert fw.tree.rows[1] == ("克隆插件", "clone", "done", "—")

    def test_render_no_data_shows_unreachable_and_stops_poll(self):
        class _Tree:
            def __init__(self):
                self.rows = []
                self.deletes = 0

            def get_children(self, *a):
                return []

            def delete(self, *a):
                self.deletes += 1

            def insert(self, *a, **k):
                self.rows.append(k.get("values"))

        w = tasks_win._TasksWindow("127.0.0.1", 7861, None)
        w.win = type("W", (), {"winfo_exists": lambda self: True})()
        w._tree = _Tree()
        w._alive = True
        w._render(None)
        assert w._tree.rows[0][0] == "服务未启动或无法连接"
        assert w._alive is False

    def test_clear_finished_calls_delete(self, monkeypatch):
        import time
        calls = []
        monkeypatch.setattr(tasks_win, "_http_json",
                            lambda h, p, path, method="GET":
                            calls.append((path, method)) or {"tasks": []})
        w = tasks_win._TasksWindow("127.0.0.1", 7861, None)
        w._clear_finished()           # 走 worker 线程
        time.sleep(0.15)
        assert ("/api/tasks", "DELETE") in calls


class TestFabTrayMenuWiring:
    """回归：球/托盘菜单项确实调用新动作（别像 v6.0(5) 那样漏接）。"""

    def test_fab_open_tasks_wired(self, monkeypatch):
        calls = []
        monkeypatch.setattr(tasks_win, "open_tasks",
                            lambda h, p, root: calls.append((h, p, root)))
        from wwdm_app.gui import fab
        b = fab.FabBall(7861)
        b._open_tasks()
        assert calls == [("127.0.0.1", 7861, None)]

    def test_fab_toggle_ui_theme_wired(self, monkeypatch):
        monkeypatch.setattr(actions, "set_ui_theme", lambda h, p, t: t)
        monkeypatch.setitem(config.settings, "ui_theme", "light")
        from wwdm_app.gui import fab
        b = fab.FabBall(7861)
        b._toggle_ui_theme()
        assert config.settings["ui_theme"] == "dark"

    def test_tray_open_tasks_wired(self, monkeypatch):
        calls = []
        monkeypatch.setattr(tasks_win, "open_tasks",
                            lambda h, p, root: calls.append((h, p, root)))
        ic = tray.TrayIcon(7861)
        ic._open_tasks()
        assert calls == [("127.0.0.1", 7861, None)]

    def test_tray_toggle_ui_theme_wired(self, monkeypatch):
        monkeypatch.setattr(actions, "set_ui_theme", lambda h, p, t: t)
        monkeypatch.setitem(config.settings, "ui_theme", "light")
        ic = tray.TrayIcon(7861)
        ic._toggle_ui_theme()
        assert config.settings["ui_theme"] == "dark"
