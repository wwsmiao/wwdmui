# -*- coding: utf-8 -*-
"""任务总线上报薄封装（v5.0 第 7 步）。

三条后台线（下载 / 克隆 / 修复）统一在这里上报，业务侧只调一个动词：

    bus.start(kind, key, title=..., message=...)   开一条 / 改写为「在跑」
    bus.queued(kind, key, title=..., position=n)   排队中
    bus.step(kind, key, progress=..., message=...) 推进度
    bus.done(kind, key, message=...)               完成
    bus.fail(kind, key, message=...)               失败
    bus.cancel(kind, key, message=...)             取消

`key` 用业务主键（模型 rid / 插件 pid / 修复动作名），同一个活儿反复上报
一定落回同一条任务 —— 前端不会看到重复行。

硬约束
------
* **绝不反噬业务**：每个函数整体 try/except。总线出任何问题，最坏结果只是
  「任务中心少一条」，绝不能让下载/克隆/修复跟着失败或抛异常。
* **不碰 Flask 的 request / g**：后台线程没有请求上下文，这里只用 core.tasks。
* 只做内存字典的原子操作，不落盘、不发网络、不加锁等待（core.tasks 自带 RLock）。
"""
from ..core import tasks

PROGRESS_UNKNOWN = tasks.PROGRESS_UNKNOWN


def _report(fn, *a, **kw):
    """统一护栏：总线永远不许把业务带崩。"""
    try:
        return fn(*a, **kw)
    except Exception:
        return None


def _tid(kind, key):
    return tasks.make_id(kind, key)


def start(kind, key, title=None, message=None, detail=None, progress=None):
    """开一条任务（或在跑的改成 RUNNING）。"""
    return _report(tasks.upsert, _tid(kind, key), kind, title=title,
                   status=tasks.RUNNING, progress=progress,
                   message=message, detail=detail)


def queued(kind, key, title=None, position=0, detail=None):
    """排队中（还没真正开跑）。position 只用于文案，不进详情结构。"""
    msg = ("排队中 (位置%d)" % position) if position else "排队中"
    return _report(tasks.upsert, _tid(kind, key), kind, title=title,
                   status=tasks.PENDING, progress=0.0, message=msg,
                   detail=detail)


def step(kind, key, progress=None, message=None, detail=None):
    """推进度 / 换文案。status 保持 RUNNING（暂停这种也只是换文案）。"""
    return _report(tasks.upsert, _tid(kind, key), kind,
                   status=tasks.RUNNING, progress=progress,
                   message=message, detail=detail)


def done(kind, key, message="完成", detail=None):
    return _report(tasks.finish, _tid(kind, key), message=message, detail=detail)


def fail(kind, key, message="", detail=None):
    return _report(tasks.fail, _tid(kind, key), message=message, detail=detail)


def cancel(kind, key, message="已取消"):
    return _report(tasks.cancel, _tid(kind, key), message=message)


def drop(kind, key):
    """整条删掉（条目失效、插件记录被删时用）。"""
    return _report(tasks.remove, _tid(kind, key))
