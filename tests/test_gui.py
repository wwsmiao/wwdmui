# -*- coding: utf-8 -*-
"""v6.0 桌面悬浮球：位置纯函数 / GUI 降级判定 / 开机自启 / 设置校验 / 依赖方向。

GUI 本身（tkinter 窗口）**不可单测**，这里只测能测的：
  * pos 的位置解析、钳制、默认值（纯函数，换分辨率不丢球靠它）；
  * should_run_gui 的三条降级路径；
  * autostart 的注册表读写（winreg 打桩，绝不碰真注册表）；
  * 设置 API 对三个新键的校验 —— 特别是 **保存设置不许写注册表**
    （测试跑 POST 会真改用户环境，这个断言是防回归的闸门）；
  * 依赖方向：gui → core，**不许 import services/blueprints**；
    fab.py 顶层不许 import tkinter（延迟到 start()，否则 import 包就要 Tk）。
"""
import ast
import io
import os

import pytest

import wwdm_app.services as svc
from wwdm_app.core import config
from wwdm_app.gui import autostart, pos


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
        # 4K 上拖到右下角的坐标，换到 1080p 笔记本必须被钳回来
        assert pos.clamp_pos(99999, 99999, (1920, 1080)) == (1856, 1016)
        assert pos.clamp_pos(-50, -50, (1920, 1080)) == (8, 8)
        assert pos.clamp_pos(500, 500, (1920, 1080)) == (500, 500)

    def test_default_is_bottom_right_with_taskbar(self):
        assert pos.default_pos((1920, 1080)) == (1856, 976)
        # 极小屏幕：x 还能放下就贴右下，y 放不下就贴顶，绝不出负数
        assert pos.default_pos((100, 100)) == (36, 0)

    def test_resolve_saved_vs_fallback(self):
        assert pos.resolve_pos("300,400", (1920, 1080)) == (300, 400)
        assert pos.resolve_pos("", (1920, 1080)) == pos.default_pos((1920, 1080))
        assert pos.resolve_pos("垃圾", (1920, 1080)) == pos.default_pos((1920, 1080))
        assert pos.resolve_pos("99999,99999", (1920, 1080)) == (1856, 1016)


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
# ⑤ 发布包白名单
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
# ⑥ 依赖方向与延迟 import（结构铁律）
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

    def test_fab_defers_tkinter(self):
        """fab.py 顶层不许 import tkinter —— 否则 import wwdm_app.gui 就要 Tk，
        pytest / 无头环境直接炸。tkinter 只许出现在 start() 函数体里。"""
        path = os.path.join(GUI_DIR, "fab.py")
        with io.open(path, "rb") as f:
            tree = ast.parse(f.read().decode("utf-8"))
        top = {n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))}
        for node in top:
            names = ([a.name for a in node.names]
                     if isinstance(node, ast.Import) else [node.module or ""])
            assert not any("tkinter" in n for n in names), \
                "fab.py 顶层 import 了 tkinter，必须延迟到 start()"

    def test_run_py_has_gui_branch(self):
        """run.py 入口三分支：--no-gui / GUI 悬浮球 / 降级主线程。"""
        with io.open(os.path.join(ROOT, "run.py"), "rb") as f:
            src = f.read().decode("utf-8")
        assert "gui.should_run_gui()" in src
        assert '"--no-gui" in sys.argv' in src
        assert '"--minimized" in sys.argv' in src
        assert "server_dead=dead" in src
        # 降级路径仍在：app.run 主线程（两处 except 里的日志是同一模式的两次出现）
        assert src.count("app.run(host=host, port=port, debug=False, threaded=True)") == 2

    def test_config_bridge_imports(self):
        """gui → core 的相对导入链（``..core``）真的能走通。

        v6.0 踩过：写成 ``.core`` 解析成 ``wwdm_app.gui.core`` 直接
        ModuleNotFoundError，而 dry_run 冒烟在建窗之前就返回、覆盖不到 ——
        这个用例就是堵那个盲区的（打桩在 conftest，不会写真 settings.json）。
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
