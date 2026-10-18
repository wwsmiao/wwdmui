# -*- coding: utf-8 -*-
"""开机自启动（Windows 注册表 Run 键，零依赖）。

``HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run`` 下建一个值：

  * 值名 ``wwdmui``；
  * 数据 = 启动命令：打包后的 exe 直接用 ``sys.executable``；开发模式
    （``python run.py``）拼 ``python run.py`` 全路径；
  * 加 ``--minimized`` 参数：开机自启时用户不一定想立刻看到浏览器，
    悬浮球起来就行（run.py 识别该参数时不自动开浏览器）。

非 Windows / 无 winreg / 注册表不可写 → 所有写操作静默失败返回 False，
读一律 False —— **绝不让自启功能把启动流程搞挂**（它是锦上添花，不是必需品）。

测试口径：winreg 在模块层 import，打桩打在 ``autostart.winreg`` 上
（「模块内直接名字引用必须打在源模块上」铁律，conftest 注释有案底）。
"""
import os
import sys

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_NAME = "wwdmui"

try:
    import winreg
except Exception:                       # 非 Windows / 极少数缺模块的解释器
    winreg = None


def _project_root():
    # 本文件在 wwdm_app/gui/autostart.py，项目根 = 上两级
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def startup_command():
    """自启动命令（带引号防路径空格）。"""
    if getattr(sys, "frozen", False):
        # Nuitka / PyInstaller 打包后 sys.executable 就是 run.exe
        return '"%s"' % sys.executable
    script = os.path.join(_project_root(), "run.py")
    return '"%s" "%s"' % (sys.executable, script)


def is_enabled():
    """当前是否已登记自启动。"""
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, APP_NAME)
        return True
    except Exception:
        return False


def enable():
    """登记自启动。成功 True；环境不支持/写入失败 False（不抛）。"""
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY,
                            0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, startup_command())
        return True
    except Exception:
        return False


def disable():
    """取消自启动。值不存在也算成功（幂等）。"""
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY,
                            0, winreg.KEY_SET_VALUE) as k:
            try:
                winreg.DeleteValue(k, APP_NAME)
            except FileNotFoundError:
                pass
        return True
    except Exception:
        return False


def sync(desired):
    """把注册表现状对齐到 desired（bool）。返回实际生效状态。"""
    if desired:
        return enable()
    return disable()
