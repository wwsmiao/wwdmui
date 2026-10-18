# -*- coding: utf-8 -*-
"""操作审计日志（S7，v5.1）。

把「危险写操作」记进 notes.db 的 audit_log 表：时间、来源 IP、动作、对象。
存储全在 ``core/db.py`` 的 ``audit_log_*`` 系列，本文件只做三件事：

1. **自动取来源 IP**（有请求上下文时取 ``request.remote_addr``）；
2. **绝不反噬业务** —— :func:`audit` 全函数 try/except，写库失败只吞掉。
   一个审计故障绝不允许把「删除 / 关机 / 保存设置」搞失败，这与任务总线
   「总线绝不反噬业务」是同一条铁律；
3. 收拢**动作 key 字典** ``AUDIT_ACTIONS``（key → 中文标签），前端筛选器与
   列表都用它渲染，新增动作只改这一处。

埋点约定：在各蓝线的危险动作**之后**记日志。服务层返回值形态不统一
（dict / (ok, msg) 元组），统一走 :func:`audit_result` 归一化；只有
「目标/详情都自己攒好了」的场景才直接用 :func:`audit`。
"""
# 刻意不在顶层 import flask：services 也会被非 Web 上下文（将来的托盘 /
# CLI）使用，顶层 import 会把依赖钉死。请求上下文判断放函数里做。
from ..core import db as coredb

# 动作 key → 中文标签（GET /api/audit 会整份返回给前端）
AUDIT_ACTIONS = {
    "model.delete":      "删除模型",
    "localfile.delete":  "删除本地文件",
    "localfile.rename":  "重命名本地文件",
    "localfile.move":    "移动本地文件",
    "localfile.mkdir":   "新建文件夹",
    "localfile.upload":  "上传文件",
    "localfile.save":    "保存本地文件",
    "plugin.delete":     "删除插件记录",
    "comfyui.start":     "启动 ComfyUI",
    "comfyui.stop":      "停止 ComfyUI",
    "clone.start":       "克隆插件",
    "repair.run":        "执行修复",
    "pip.install":       "安装 Python 依赖",
    "shutdown.schedule": "定时关机",
    "shutdown.cancel":   "取消关机",
    "settings.save":     "保存设置",
    "settings.reset":    "恢复默认设置",
    "token.regenerate":  "重新生成令牌",
    "system.quit":       "退出管理器",
}
# key 形如 "域.动作"（前端下拉选项直接来自这个字典，新增动作只改这一处）
assert all("." in k and k == k.strip() for k in AUDIT_ACTIONS)


def _client_ip():
    """取来源 IP；没有请求上下文（后台线程 / CLI）时返回空串。"""
    try:
        from flask import has_request_context, request
        if has_request_context():
            return str(request.remote_addr or "")
    except Exception:
        pass
    return ""


def _audit_log_path():
    """审计文件日志路径：``wwdm_app/static/logs/audit.log``。"""
    import os
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    d = os.path.join(base, "static", "logs")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        return None
    return os.path.join(d, "audit.log")


def _write_file_log(action, target, detail, ip, ok):
    """静默追加一行 JSONL；超过 5MB 轮转成 .1。任何失败都吞掉。"""
    try:
        import json
        import os
        import time
        path = _audit_log_path()
        if not path:
            return
        try:
            if os.path.getsize(path) > 5 * 1024 * 1024:
                os.replace(path, path + ".1")
        except OSError:
            pass
        rec = {
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "action": action,
            "target": str(target or "")[:500],
            "detail": str(detail or "")[:2000],
            "ip": ip or "",
            "ok": bool(ok),
        }
        with open(path, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def audit(action, target="", detail="", ok=True):
    """记一条审计：内部 DB 保留（兼容 /api/audit），并静默写 static/logs/。

    v6.4：界面不再展示审计，改为**静默文件日志**（默认 static/logs/audit.log）。
    """
    ip = _client_ip()
    try:
        coredb.audit_log_add(action, target=target, detail=detail,
                             ip=ip, ok=bool(ok))
    except Exception:
        pass
    _write_file_log(action, target, detail, ip, bool(ok))


def audit_result(action, r, target="", extra=""):
    """按「服务层返回值」记审计，自动归一化返回形态。

    services 各危险函数的返回形态并不统一：关机 / 启停 ComfyUI / 修复系
    返回 **dict**，克隆返回 ``(ok, msg)`` **元组**；而 conftest 又把危险
    动作统一打桩成元组。埋点处若直接 ``r.get(...)``，会在**求参阶段**就
    AttributeError —— :func:`audit` 自己的 try/except 兜不住参数求值，
    业务接口会变 500（2026-10-18 真事：``/api/shutdown`` 500）。

    归一化口径：
      dict           -> ok=r.get("ok", True)，msg=r.get("msg")
      (ok, msg) 元组 -> ok=r[0]，msg=r[1]
      其它 / None    -> ok=True，msg=str(r)（None 记空详情）
    detail 统一拼成 ``extra · msg``（extra 可空，如 pip 命令行放这里）。
    """
    ok, msg = True, ""
    if isinstance(r, dict):
        ok = bool(r.get("ok", True))
        msg = str(r.get("msg") or "")
    elif isinstance(r, (tuple, list)) and len(r) >= 2:
        ok = bool(r[0])
        msg = str(r[1] or "")
    elif r is not None:
        msg = str(r)
    detail = (extra + " · " + msg) if (extra and msg) else (extra or msg)
    audit(action, target=target, detail=detail, ok=ok)


def audit_list(limit=100, action="", kw=""):
    """读审计记录；读失败返回空列表（界面上显示「暂无」而不是报错）。"""
    try:
        return coredb.audit_log_list(limit=limit, action=action, kw=kw)
    except Exception:
        return []


def audit_count():
    try:
        return coredb.audit_log_count()
    except Exception:
        return 0
