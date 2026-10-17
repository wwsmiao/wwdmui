# -*- coding: utf-8 -*-
"""定时关机（v5.0 第 6 步 · 域 3/10）。

从 ``services/__init__.py`` 的「关机功能」整段搬来，**函数体一字未改**（连原有注释都逐字保留，
就在 ``_shutdown_lock`` 上方），只补了模块头与 import。

⚠️ 模块级状态（内部约定）
------------------------
``_shutdown_deadline`` / ``_shutdown_delay`` / ``_shutdown_reap`` 是**可变模块级状态**，
函数里用 ``global`` 重绑定。它们**刻意不在 services/__init__.py 里再导出** ——
``from .shutdown import _shutdown_deadline`` 只会复制一份绑定快照，读到的值永远停在
导入那一刻，属于"会撒谎的别名"。拆前它们也只在块内自用（已 grep 确认）。
需要状态请调 :func:`shutdown_status`。

⚠️ 末尾的 ``_restore_shutdown_state()`` 是**模块级调用**（拆前在 services/__init__.py
末尾）。搬家时必须跟着搬过来，否则 __init__ 里那行会在再导出之前执行 → NameError。
"""
import json
import os
import subprocess
import threading
import time

from ..config import BASE_DIR, IS_WINDOWS


# 计划用 Windows 原生倒计时 `shutdown /s /t <秒>`，取消用 `shutdown /a`。
# 好处：倒计时由系统托管 —— 管理端退出 / 重启后依然能取消，也依然会按时关机；
# 进程内的状态落盘到 BASE_DIR/shutdown_state.json，重启后可恢复显示与取消。
# delay <= 0 表示立即关机（`/t 0`），系统层面没有取消窗口，前端会明确提示。
_shutdown_lock = threading.Lock()
_shutdown_deadline = 0.0   # 计划关机的时间戳（time.time()），0 = 无计划
_shutdown_delay = 0        # 计划时使用的延迟秒数
_shutdown_reap = None      # 到点后只负责清理内存状态（真正关机的是系统）
_SHUTDOWN_STATE = os.path.join(BASE_DIR, "shutdown_state.json")


def _persist_shutdown_state():
    """把待执行的关机计划落盘，供进程重启后查询 / 取消。"""
    try:
        with open(_SHUTDOWN_STATE, "w", encoding="utf-8") as f:
            json.dump({"deadline": _shutdown_deadline, "delay": _shutdown_delay}, f)
    except Exception:
        pass


def _clear_shutdown_state():
    try:
        if os.path.exists(_SHUTDOWN_STATE):
            os.remove(_SHUTDOWN_STATE)
    except Exception:
        pass
    # 兜底：个别受控环境下 os.remove 会被安全策略拦截，退化成写入"无计划"状态
    try:
        if os.path.exists(_SHUTDOWN_STATE):
            with open(_SHUTDOWN_STATE, "w", encoding="utf-8") as f:
                json.dump({"deadline": 0, "delay": 0}, f)
    except Exception:
        pass


def _restore_shutdown_state():
    global _shutdown_deadline, _shutdown_delay
    try:
        with open(_SHUTDOWN_STATE, "r", encoding="utf-8") as f:
            d = json.load(f)
        dl = float(d.get("deadline") or 0)
        if dl > time.time():
            _shutdown_deadline = dl
            _shutdown_delay = int(d.get("delay") or 0)
        else:
            _clear_shutdown_state()
    except Exception:
        pass


def _shutdown_cmd(args, timeout=15):
    """执行 shutdown 命令，永不抛异常。返回 (returncode, err)。"""
    try:
        cf = getattr(subprocess, "CREATE_NO_WINDOW", 0) if IS_WINDOWS else 0
        p = subprocess.run(args, timeout=timeout, creationflags=cf)
        return (getattr(p, "returncode", 0), "")
    except Exception as e:
        return (-1, str(e))


def shutdown_status():
    """返回当前关机计划状态。"""
    global _shutdown_deadline, _shutdown_delay
    with _shutdown_lock:
        if not _shutdown_deadline or _shutdown_deadline - time.time() <= 1e-3:
            _shutdown_deadline = 0.0
            _shutdown_delay = 0
            return {"ok": True, "scheduled": False, "deadline": 0, "remaining": 0, "delay": 0}
        return {"ok": True, "scheduled": True, "deadline": _shutdown_deadline,
                "remaining": int(round(_shutdown_deadline - time.time())),
                "delay": _shutdown_delay}


def schedule_shutdown(delay):
    """delay 秒后关机；delay<=0 表示立即关机。返回 {ok, msg, immediate}。"""
    global _shutdown_deadline, _shutdown_delay, _shutdown_reap
    try:
        delay = int(delay)
    except (TypeError, ValueError):
        return {"ok": False, "msg": "关机延迟必须是整数秒"}
    if delay < 0:
        return {"ok": False, "msg": "关机延迟不能为负数"}
    delay = min(delay, 3600)

    if IS_WINDOWS:
        args = ["shutdown", "/s", "/t", str(delay)]
    elif delay <= 0:
        args = ["shutdown", "-h", "now"]
    else:
        args = ["shutdown", "-h", "+%d" % max(1, (delay + 59) // 60)]
    rc, err = _shutdown_cmd(args)
    if rc != 0:
        return {"ok": False,
                "msg": "关机指令执行失败" + ((": " + err) if err else "（返回码 %s）" % rc)}

    with _shutdown_lock:
        _shutdown_deadline = time.time() + delay
        _shutdown_delay = delay
        if _shutdown_reap is not None:
            _shutdown_reap.cancel()
            _shutdown_reap = None
        if delay > 0:
            t = threading.Timer(delay + 1, _reap_shutdown_state)
            t.daemon = True
            _shutdown_reap = t
            t.start()
        _persist_shutdown_state()

    if delay <= 0:
        return {"ok": True, "immediate": True, "msg": "已发出立即关机指令"}
    return {"ok": True, "immediate": False, "msg": "已计划 %d 秒后关机" % delay}


def _reap_shutdown_state():
    """倒计时结束（系统已执行关机）后清理内存态与落盘状态。"""
    global _shutdown_deadline, _shutdown_delay, _shutdown_reap
    with _shutdown_lock:
        _shutdown_deadline = 0.0
        _shutdown_delay = 0
        _shutdown_reap = None
    _clear_shutdown_state()


def cancel_shutdown():
    """取消已计划的定时关机（走系统 `shutdown /a`）。返回 {ok, msg}。"""
    global _shutdown_deadline, _shutdown_delay, _shutdown_reap
    with _shutdown_lock:
        scheduled = bool(_shutdown_deadline)
    if not scheduled:
        return {"ok": False, "msg": "没有待执行的关机任务"}
    rc, err = _shutdown_cmd(["shutdown", "/a"] if IS_WINDOWS else ["shutdown", "-c"])
    with _shutdown_lock:
        _shutdown_deadline = 0.0
        _shutdown_delay = 0
        if _shutdown_reap is not None:
            _shutdown_reap.cancel()
            _shutdown_reap = None
    _clear_shutdown_state()
    if rc != 0:
        return {"ok": True, "msg": "已取消关机（系统返回 %s，请确认没有残留倒计时）" % (err or rc)}
    return {"ok": True, "msg": "已取消关机"}


_restore_shutdown_state()
