# -*- coding: utf-8 -*-
"""任务总线（v5.0 骨架）：把「机器上现在有哪些后台活儿在跑」收进一个可查询的注册表。

为什么要有它
------------
现状是 4 条各自为政的轮询（D19）：

    kind        触发点                        前端轮询        字段
    ------------------------------------------------------------------
    下载        模型 / 插件包下载             每 2.0s         独立端点 + 自有字段
    克隆        插件 git clone                每 2.0s         独立端点 + 自有字段
    修复        pip / git / 依赖修复          每 0.8s         独立端点 + 自有字段
    ComfyUI     本体日志                       每 1.2s         独立端点 + 自有字段

每加一种后台活儿，就要多一个端点、多一个定时器、多一套字段。用户也看不到
「一共有几件事在跑」。本模块提供一个**与具体业务无关**的注册表：

* 后台线程只调 ``upsert()`` / ``finish()`` / ``fail()`` 上报；
* HTTP 侧只调 ``snapshot()`` 读取；
* 双方都不需要知道对方的字段结构 —— 各 kind 的附加信息统一塞在 ``detail`` 里。

设计约束
--------
* **纯标准库**（不 import Flask 的 request/g）—— 后台线程没有请求上下文，
  这条是硬要求，不是风格偏好。
* 一把 ``RLock`` 保护全部状态；读操作返回**深拷贝**，调用方随便改都不会串场。
* 内存态，进程重启即清空（与现有行为一致，没有跨进程需求）。
* **有上限**：已完成任务最多留 ``MAX_DONE`` 条 / ``DONE_TTL`` 秒，超出即淘汰，
  避免长跑的管理器把内存吃掉。
* 任何上报都**不能反噬业务**：总线只在内存字典上做原子操作，不落盘、不发网络。

进度语义
--------
``progress`` 是 0~100 的百分比；``PROGRESS_UNKNOWN``(-1) 表示「在跑但算不出百分比」
（git clone 早期、pip 安装等）。前端请把 -1 渲染成「不确定」，**不要当 0**。
"""
import threading
import time

# ---- 进度 / 状态常量 ----
PROGRESS_UNKNOWN = -1.0

PENDING = "pending"
RUNNING = "running"
DONE = "done"
FAILED = "failed"
CANCELED = "canceled"
FINISHED = (DONE, FAILED, CANCELED)

# ---- 已知的 kind（只作常量方便引用，业务可以传自己的字符串）----
KIND_DOWNLOAD = "download"
KIND_CLONE = "clone"
KIND_REPAIR = "repair"
KIND_BRANCHES = "branches"
KIND_DOUYIN = "douyin"
KIND_COMFYUI = "comfyui"
KIND_ZIP = "zip"

MAX_DONE = 200               # 已完成任务最多保留条数
DONE_TTL = 6 * 3600.0        # 已完成任务最长保留时间（秒）

_lock = threading.RLock()
_tasks = {}                  # task_id -> task dict
_seq = 0                     # 全局单调序号：每次写入 +1，供前端判断「有没有新的变化」


# ============================================================
# 内部工具
# ============================================================
def _now():
    return time.time()


def _bump():
    """全局序号 +1 并返回。前端据此判断「相对上次拿到的 seq，有没有新变化」。"""
    global _seq
    _seq += 1
    return _seq


def _blank(task_id, kind):
    t = _now()
    return {
        "id": task_id,
        "kind": kind,
        "title": "",
        "status": PENDING,
        "progress": PROGRESS_UNKNOWN,
        "message": "",
        "detail": {},
        "seq": 0,
        "started_at": t,
        "updated_at": t,
        "finished_at": 0.0,
    }


def _prune_locked(now):
    """淘汰过老的已完成任务，保证内存有界。调用方需持锁。"""
    rows = [(t["finished_at"] or t["updated_at"], tid)
            for tid, t in _tasks.items() if t["status"] in FINISHED]

    # ① 超过 TTL 的直接扔
    for ts, tid in rows:
        if now - ts > DONE_TTL:
            _tasks.pop(tid, None)
    # ② 还超条数就按完成时间留最新的
    left = sorted(r for r in rows if r[1] in _tasks)
    for _ts, tid in left[:max(0, len(left) - MAX_DONE)]:
        _tasks.pop(tid, None)


def make_id(kind, key=""):
    """拼一个稳定 id。同一件事反复上报必须落回同一条，不要每次新建。

        make_id(KIND_DOWNLOAD, 12)              -> "download:12"
        make_id(KIND_BRANCHES, r"D:\\ComfyUI")  -> "branches:D:\\ComfyUI"
    """
    key = str(key or "").strip()
    return "%s:%s" % (kind, key) if key else str(kind)


def _kind_from_id(task_id):
    """从 id 前缀反推 kind。

    收尾函数（finish/fail/cancel）走的是 ``upsert(task_id, None, ...)``：万一
    调用方没先 upsert 就收尾，这里保证新建出来的那条**不会退化成 kind=None**
    （否则 ``?kind=clone`` 就永远筛不到它）。
    """
    s = str(task_id or "")
    return s.split(":", 1)[0] if ":" in s else s


# ============================================================
# 写入
# ============================================================
def upsert(task_id, kind, title=None, status=None, progress=None,
           message=None, detail=None):
    """创建或**局部**更新一条任务：只覆盖显式传入（非 None）的字段。

    已存在的任务会先更新、再用 ``kind`` 修正类型（允许后续补上更准确的 kind）。
    返回任务快照（深拷贝）。
    """
    with _lock:
        now = _now()
        t = _tasks.get(task_id)
        if t is None:
            t = _blank(task_id, kind or _kind_from_id(task_id))
            _tasks[task_id] = t
        if kind:
            t["kind"] = kind
        if title is not None:
            t["title"] = title
        if message is not None:
            t["message"] = message
        if progress is not None:
            try:
                t["progress"] = float(progress)
            except (TypeError, ValueError):
                pass
        if detail is not None:
            t["detail"] = dict(detail)
        if status is not None:
            t["status"] = status
            # 从「非终态」跨到「终态」时记完成时间；反过来重跑则清掉
            if status in FINISHED:
                if not t["finished_at"]:
                    t["finished_at"] = now
            else:
                t["finished_at"] = 0.0
        t["updated_at"] = now
        t["seq"] = _bump()
        _prune_locked(now)
        return _copy(t)


def update(task_id, **fields):
    """``upsert`` 的别名，读起来顺一点；kind 缺省不会覆盖已有的。"""
    with _lock:
        kind = _tasks.get(task_id, {}).get("kind") or ""
    return upsert(task_id, kind, **fields)


def finish(task_id, message=None, status=DONE, detail=None):
    """收尾成功（或指定 status）。"""
    if status not in FINISHED:
        status = DONE
    return upsert(task_id, None, status=status, message=message, detail=detail,
                  progress=100.0 if status == DONE else None)


def fail(task_id, message="", detail=None):
    """收尾失败。"""
    return upsert(task_id, None, status=FAILED, message=message, detail=detail)


def cancel(task_id, message=None):
    return upsert(task_id, None, status=CANCELED, message=message)


def remove(task_id):
    """整条删掉（任务被撤销、条目失效时用）。"""
    global _seq
    with _lock:
        gone = _tasks.pop(task_id, None)
        if gone is not None:
            _seq += 1
        return gone is not None


def _scope_status(scope):
    """把 scope 参数翻译成一组允许清的 status。"""
    s = str(scope or "").strip().lower()
    if s in ("failed", "fail", "error"):
        return {FAILED}
    if s in ("canceled", "cancelled"):
        return {CANCELED}
    if s in ("done", "success", "finished_ok"):
        return {DONE}
    # finished / all / 没传 / 不认识 —— 一律按「全部终态」，宁可少删不许多删
    return set(FINISHED)


def clear(ids=None, scope="finished"):
    """手动清理**终态**任务，返回 ``{"removed": n, "skipped": n}``。

    ⚠️ 设计上只允许删终态，这是刻意的：

    正在跑的任务背后有真实线程在干活，删掉注册表条目**并不会让线程停下**，
    而下一次心跳上报又把条目原样重建出来 —— 用户刚清完它就冒回来，比不清
    更让人困惑。真要中止请用各自的业务端点（下载 pause、克隆 cancel、
    修复 stop），那条才是真正会让线程退出的路。

    ``skipped`` 的口径是「**用户点名要删、却因为在跑被拒绝**的条数」，
    不是「遍历到但没删」—— 按 scope 清失败时，剩下的成功/进行中条目本来
    就不在用户意图里，把它们报成 skipped 是噪音。所以只有显式传 ids 时才
    计 skipped；范围筛选模式下恒为 0。

    :param ids:   精确 id 列表（与 scope 取交集；None = 不限 id）
    :param scope: failed / canceled / done / finished(=全部终态) / all(=finished)
    """
    global _seq
    by_id = bool(ids)
    want_ids = None
    if by_id:
        want_ids = set(str(i) for i in ids if str(i))
        if not want_ids:
            return {"removed": 0, "skipped": 0}
    want = _scope_status(scope)

    removed = skipped = 0
    with _lock:
        for tid in list(_tasks.keys()):
            t = _tasks.get(tid)
            if t is None:
                continue
            if want_ids is not None and tid not in want_ids:
                continue
            if t["status"] not in want:
                if by_id:
                    skipped += 1          # 点名了却删不掉，这才需要回告
                continue
            _tasks.pop(tid, None)
            removed += 1
        if removed:
            _seq += 1
    return {"removed": removed, "skipped": skipped}


def reset():
    """清空（测试用）。"""
    global _seq
    with _lock:
        _tasks.clear()
        _seq += 1


# ============================================================
# 读取
# ============================================================
def _copy(t):
    """深拷贝一层就够：detail 是浅 dict，值都是基本类型。"""
    c = dict(t)
    c["detail"] = dict(t.get("detail") or {})
    return c


def get(task_id):
    with _lock:
        t = _tasks.get(task_id)
        return _copy(t) if t else None


def active_count():
    with _lock:
        return sum(1 for t in _tasks.values() if t["status"] not in FINISHED)


def snapshot(kinds=None, active_only=False, limit=0):
    """给 HTTP 侧用的整体快照。

    :param kinds:       只取这些 kind（None = 全部）
    :param active_only: 只要还在跑的
    :param limit:       >0 则最多返回这么多条，**优先保留在跑的**，其余按
                       更新时间倒序（保证「正在跑的不会被完成的老任务挤掉」）
    """
    with _lock:
        now = _now()
        items = [_copy(t) for t in _tasks.values()]
        seq = _seq

    if kinds:
        want = set(kinds)
        items = [t for t in items if t["kind"] in want]
    if active_only:
        items = [t for t in items if t["status"] not in FINISHED]

    actives = [t for t in items if t["status"] not in FINISHED]
    dones = [t for t in items if t["status"] in FINISHED]
    actives.sort(key=lambda t: t["started_at"])
    dones.sort(key=lambda t: t["updated_at"], reverse=True)

    out = actives + dones
    if limit and limit > 0:
        out = out[:limit]
    return {
        "tasks": out,
        "seq": seq,
        "active": len(actives),
        "total": len(items),
        "server_time": now,
    }


# ============================================================
# HTTP 注册（只读）
# ============================================================
def register(app):
    """挂 ``GET /api/tasks``（读）与 ``DELETE /api/tasks``（清理）。

    任务的「开始 / 暂停 / 取消」各自有自己的业务端点（下载有 aria2 的暂停恢复、
    克隆有 cancel、修复有 stop），总线不抢它们的职责 —— DELETE 只清**已经结束**
    的记录，不提供「取消任务」，那是业务端点的事。

    鉴权走全局 security 闸（回环免令牌、非回环要令牌；写请求过同源校验），
    与其它 /api/* 一致。
    """
    from flask import jsonify, request

    @app.route("/api/tasks", methods=["GET"])
    def api_tasks():
        raw = (request.args.get("kind") or "").strip()
        kinds = [k for k in raw.split(",") if k] if raw else None
        active_only = request.args.get("active") in ("1", "true", "yes")
        try:
            limit = int(request.args.get("limit") or 0)
        except (TypeError, ValueError):
            limit = 0
        return jsonify(snapshot(kinds=kinds, active_only=active_only, limit=limit))

    @app.route("/api/tasks", methods=["DELETE"])
    def api_tasks_clear():
        """清理已结束的任务记录。

        参数（query 或 JSON body 都认，query 优先）：
            id=a,b,c    精确删这几条
            scope=failed|done|canceled|finished|all   按状态批量删（默认 finished）

        只删终态；撞上「还在跑」的计入 skipped，前端据此提示用户。
        """
        body = request.get_json(silent=True) or {}
        if not isinstance(body, dict):
            body = {}

        raw_ids = request.args.get("id") or request.args.get("ids") or ""
        ids = [x.strip() for x in str(raw_ids).split(",") if x.strip()]
        if not ids:
            v = body.get("ids")
            if isinstance(v, (list, tuple)):
                ids = [str(x).strip() for x in v if str(x).strip()]
            elif v:
                ids = [str(v).strip()]

        scope = request.args.get("scope") or body.get("scope") or "finished"
        r = clear(ids=ids or None, scope=scope)
        return jsonify({
            "ok": True,
            "removed": r["removed"],
            "skipped": r["skipped"],
            # 还剩多少在跑。前端据此说「还剩 N 项在进行中」——即便这次清的不是它们，
            # 这个数字也能解释「为什么列表没变空」。
            "running": active_count(),
        })
