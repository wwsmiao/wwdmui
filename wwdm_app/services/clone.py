# -*- coding: utf-8 -*-
"""克隆域：git clone 队列 / 进度解析 / 取消与清理。

自 services/__init__.py 拆出（v5.0 第 6 步 域 5/10），函数体逐字未改。
跨域依赖一律函数内延迟导入（`from . import X`）——既避开包初始化期的循环导入，
又保证「在 services 包上打桩后立刻生效」。

⚠️ 下面这些模块级容器是**活对象**：只能在原地修改（`[k] = v` / `.append()` / `.clear()`），
   不得重新赋值（`clone_queue = []`）。一旦重绑定，services 包再导出的别名就指向旧对象。
   本文件内已确认无 `global` 重绑定。
"""
import logging
import os
import shutil
import subprocess
import sys
import threading
import time

from ..config import IS_WINDOWS, get_comfyui_dir, get_git_path, settings

log = logging.getLogger("wwdm.services")   # 与 services 包同一个 logger 对象


# ---- 克隆状态（跨模块共享的活对象：只许原地改，不许重新赋值）----
clone_procs = {}
clone_infos = {}
clone_mirrors = {}
clone_queue = []
clone_lock = threading.Lock()
# 本轮克隆会话（供「停止克隆」清理 custom_nodes 下的半成品目录）
clone_session_started = set()   # 真正开始过克隆的 pid
clone_session_folder = {}       # pid -> custom_nodes 下的目标文件夹
clone_session_done = set()      # 已克隆完成的 pid（停止克隆时保留其文件夹，不删除）
clone_cleanup_lock = threading.Lock()


def _git_clone_thread(pid, repo_url, name, target_dir, use_mirror=None):
    """克隆线程。仓库地址始终按全局 GitHub 镜像设置改写（use_mirror 仅为兼容保留）"""
    # 延迟导入：这些符号仍住在 services/__init__.py
    from . import _console_bar, convert_git_mirror, github_mirror_label, github_mirror_prefix, parse_git_progress
    full_path = os.path.join(target_dir, name)
    with clone_cleanup_lock:
        clone_session_folder[pid] = full_path
    if os.path.exists(full_path):
        try:
            shutil.rmtree(full_path)
        except Exception as e:
            clone_infos[pid] = {"status": "error", "msg": "清理失败: " + str(e), "progress": 0, "phase": ""}
            clone_procs.pop(pid, None)
            _process_clone_queue()
            return
    # 克隆一律使用「全局设置 → GitHub 镜像」，页面不再单独选择镜像
    mkey = settings.get("github_mirror", "official")
    mlabel = github_mirror_label(mkey)
    mprefix = github_mirror_prefix(mkey)
    actual_url = convert_git_mirror(repo_url)
    clone_mirrors[pid] = bool(mprefix)
    git_path = get_git_path()
    cmd = [git_path, "clone", "--progress", actual_url, full_path]
    clone_infos[pid] = {"status": "cloning", "progress": 0, "phase": "remote", "msg": "",
                        "mirror_label": mlabel, "mirror_prefix": mprefix, "actual_url": actual_url}
    tag = (" [GitHub镜像: " + mlabel + "]") if mprefix else " [GitHub: 官方直连]"
    log.info("[克隆%s] %s", tag, name)
    log.info("  仓库地址: %s", actual_url)
    with clone_cleanup_lock:
        clone_session_started.add(pid)
        clone_session_done.discard(pid)   # 重新克隆 => 视为未完成，直到本次成功
    try:
        kw = {"stderr": subprocess.PIPE, "stdout": subprocess.DEVNULL, "text": True}
        if IS_WINDOWS:
            kw["creationflags"] = 0x08000000
        proc = subprocess.Popen(cmd, **kw)
        clone_procs[pid] = proc
    except Exception as e:
        clone_infos[pid] = {"status": "error", "msg": "启动失败: " + str(e), "progress": 0, "phase": ""}
        clone_procs.pop(pid, None)
        _process_clone_queue()
        return
    last_pct = 0
    try:
        for line in proc.stderr:
            pr = parse_git_progress(line)
            if pr:
                pct, phase, detail = pr
                if pct > 0:
                    last_pct = pct
                _ci = clone_infos.get(pid) or {}
                clone_infos[pid] = {"status": "cloning", "progress": max(last_pct, pct), "phase": phase,
                                    "msg": detail[:120], "mirror_label": _ci.get("mirror_label", mlabel),
                                    "mirror_prefix": _ci.get("mirror_prefix", mprefix),
                                    "actual_url": _ci.get("actual_url", actual_url)}
                bar = _console_bar(max(last_pct, pct))
                sys.stdout.write("\r  " + bar + " " + str(max(last_pct, pct)) + "% " + phase + "          ")
                sys.stdout.flush()
    except Exception:
        pass
    proc.wait()
    if proc.returncode == 0:
        clone_infos[pid] = {"status": "complete", "progress": 100, "phase": "done", "msg": "克隆完成"}
        with clone_cleanup_lock:
            clone_session_done.add(pid)   # 克隆完成 → 停止克隆时不再删除
        log.info("[克隆完成] %s -> %s", name, full_path)
    else:
        clone_infos[pid] = {"status": "error", "progress": last_pct, "phase": "failed", "msg": "退出码: " + str(proc.returncode)}
        log.error("[克隆失败] %s 退出码=%s", name, proc.returncode)
    clone_procs.pop(pid, None)
    time.sleep(10)
    clone_infos.pop(pid, None)
    _process_clone_queue()


def _get_clone_count():
    return len([p for p in clone_procs.values() if p and p.poll() is None])


def _process_clone_queue():
    # 延迟导入：这些符号仍住在 services/__init__.py
    from . import plugin_get
    mc = settings.get("max_concurrent_clones", 2)
    while True:
        with clone_lock:
            if not clone_queue:
                break
            pid = clone_queue[0]
        if _get_clone_count() >= mc:
            break
        with clone_lock:
            if pid in clone_queue:
                clone_queue.remove(pid)
            else:
                continue
        p = plugin_get(pid)
        if p:
            cn = os.path.join(get_comfyui_dir(), "custom_nodes")
            t = threading.Thread(target=_git_clone_thread, args=(pid, p["url"], p["name"], cn), daemon=True)
            t.start()
        time.sleep(1)


def start_clone(pid, use_mirror=None):
    """启动克隆。仓库地址一律按全局设置中的 GitHub 镜像改写（use_mirror 仅为兼容保留）"""
    # 延迟导入：这些符号仍住在 services/__init__.py
    from . import github_mirror_label, github_mirror_prefix, plugin_get
    if pid in clone_procs:
        return False, "已在克隆中"
    if pid in clone_queue:
        return False, "已在队列中"
    p = plugin_get(pid)
    if not p:
        return False, "插件不存在"
    use_mirror = bool(github_mirror_prefix())
    clone_mirrors[pid] = use_mirror
    if _get_clone_count() < settings.get("max_concurrent_clones", 2):
        cn = os.path.join(get_comfyui_dir(), "custom_nodes")
        t = threading.Thread(target=_git_clone_thread, args=(pid, p["url"], p["name"], cn, use_mirror), daemon=True)
        t.start()
        tag = (" [GitHub镜像: " + github_mirror_label() + "]") if use_mirror else " [GitHub: 官方直连]"
        return True, "开始克隆" + tag + ": " + p["name"]
    with clone_lock:
        clone_queue.append(pid)
    return True, "加入队列: " + p["name"] + " (位置" + str(len(clone_queue)) + ")"


def start_clone_batch(pids, use_mirror=None):
    """批量克隆（镜像一律取全局设置）"""
    return [{"id": p, "ok": ok, "msg": m} for p in pids for ok, m in [start_clone(p)]]


def cancel_clone(pid):
    with clone_lock:
        if pid in clone_queue:
            clone_queue.remove(pid)
            clone_mirrors.pop(pid, None)
            return True, "已从队列移除"
    proc = clone_procs.get(pid)
    if proc:
        try:
            proc.kill()
        except Exception:
            pass
        clone_procs.pop(pid, None)
        clone_infos.pop(pid, None)
        clone_mirrors.pop(pid, None)
        return True, "已停止克隆"
    return False, "未找到任务"


def _rmtree_force(path, tries=5):
    """尽力删除目录：处理 git 生成的只读文件、以及进程刚退出时的短暂占用"""
    def _onerr(func, p, exc):
        try:
            os.chmod(p, 0o700)
            func(p)
        except Exception:
            pass
    for _ in range(tries):
        if not os.path.isdir(path):
            return True
        try:
            shutil.rmtree(path, onerror=_onerr)
        except Exception:
            pass
        if not os.path.isdir(path):
            return True
        try:
            subprocess.run(["cmd", "/c", "rd", "/s", "/q", path],
                           capture_output=True, timeout=30)
        except Exception:
            pass
        if not os.path.isdir(path):
            return True
        time.sleep(0.7)
    return not os.path.isdir(path)


def stop_clone_and_cleanup():
    """停止所有克隆任务，并删除「尚未克隆完成」的插件文件夹
    （克隆到一半的会删除；已克隆完成的插件会保留，不动它的文件夹）"""
    # 延迟导入：这些符号仍住在 services/__init__.py
    from . import _kill_tree, plugin_get
    with clone_lock:
        queued_pids = list(clone_queue)
        clone_queue.clear()
    stopped, proc_pids, stopped_ids = [], set(), []
    # 排队中还没真正开始的也算「已停止」—— 解析弹窗要按 id 把这些行复位成「未安装」
    for pid in queued_pids:
        p = plugin_get(pid)
        stopped.append(p["name"] if p else ("#" + str(pid)))
        stopped_ids.append(pid)
    for pid in list(clone_procs.keys()):
        p = plugin_get(pid)
        stopped.append(p["name"] if p else ("#" + str(pid)))
        proc_pids.add(pid)
        stopped_ids.append(pid)
    for pid in list(proc_pids):
        proc = clone_procs.get(pid)
        if proc:
            try:
                _kill_tree(proc)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
    # 等进程退出；有排队任务被抢先启动时再补杀一轮
    for _round in range(3):
        for proc in list(clone_procs.values()):
            try:
                proc.wait(timeout=5)
            except Exception:
                pass
        time.sleep(0.6)
        alive = [pid for pid, pr in list(clone_procs.items()) if pr and pr.poll() is None]
        if not alive:
            break
        for pid in alive:
            if pid not in stopped:
                p = plugin_get(pid)
                stopped.append(p["name"] if p else ("#" + str(pid)))
            try:
                _kill_tree(clone_procs[pid])
            except Exception:
                pass

    removed, skipped, kept, partial = [], [], [], []
    removed_ids, kept_ids, partial_ids = [], [], []
    with clone_cleanup_lock:
        folder_pairs = [(pid, clone_session_folder.get(pid)) for pid in clone_session_started]
        done_pids = set(clone_session_done)
    for pid, folder in folder_pairs:
        p = plugin_get(pid)
        nm = p["name"] if p else ("#" + str(pid))
        if pid in done_pids:
            # 已克隆完成的插件：保留，不删除
            kept.append(nm)
            kept_ids.append(pid)
            log.info("[克隆清理] 保留（已克隆完成）: %s", nm)
            continue
        if not folder or not os.path.isdir(folder):
            skipped.append(nm)
            continue
        if _rmtree_force(folder):
            removed.append(nm)
            removed_ids.append(pid)
            log.info("[克隆清理] 已删除目录: %s", folder)
        else:
            skipped.append(nm + "(删除失败)")
            partial.append(nm + "(删除失败)")
            partial_ids.append(pid)

    clone_procs.clear()
    clone_infos.clear()
    clone_mirrors.clear()
    with clone_lock:
        clone_queue.clear()
    with clone_cleanup_lock:
        clone_session_started.clear()
        clone_session_folder.clear()
        clone_session_done.clear()

    stopped = list(dict.fromkeys(stopped))
    stopped_ids = list(dict.fromkeys(stopped_ids))
    parts = []
    if stopped:
        parts.append("已停止 %d 个克隆" % len(stopped))
    if removed:
        parts.append("已删除 %d 个未完成克隆的插件文件夹" % len(removed))
    if kept:
        parts.append("保留 %d 个已克隆完成的插件" % len(kept))
    if partial:
        parts.append("有 %d 个未完成目录删除失败，需手动删除" % len(partial))
    if not parts:
        parts.append("没有进行中的克隆任务")
    return {"ok": True, "stopped": stopped, "removed": removed, "skipped": skipped, "kept": kept,
            "stopped_ids": stopped_ids, "removed_ids": removed_ids, "kept_ids": kept_ids,
            "partial": partial, "partial_ids": partial_ids,
            "msg": "；".join(parts)}


def cancel_clone_with_cleanup(pid):
    """停止单个克隆，并删除「本次才克隆到一半」的插件文件夹。

    与 stop_clone_and_cleanup 同一套安全口径：只动本进程真正开始克隆过、
    且尚未克隆完成的目录 —— 已克隆完成的、以及不属于本次会话的目录一律不碰。

    返回 {ok, msg, name, folder, removed, kept, partial}
        removed=True  半成品目录已删掉
        partial=True  目录仍在（被占用删不掉）→ 前端要提示用户手动删除后再重新下载
    """
    # 延迟导入：这些符号仍住在 services/__init__.py
    from . import _kill_tree, plugin_get
    p = plugin_get(pid)
    name = p["name"] if p else ("#" + str(pid))
    proc = None
    was_queued = False
    with clone_lock:
        if pid in clone_queue:
            clone_queue.remove(pid)
            was_queued = True
        proc = clone_procs.pop(pid, None)
    running = proc is not None
    if running:
        try:
            _kill_tree(proc)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        try:
            proc.wait(timeout=8)
        except Exception:
            pass
    clone_infos.pop(pid, None)
    clone_mirrors.pop(pid, None)

    with clone_cleanup_lock:
        folder = clone_session_folder.get(pid) or ""
        started = pid in clone_session_started
        done = pid in clone_session_done

    if not running and not was_queued:
        if done:
            return {"ok": False, "msg": "克隆已完成，无需停止: " + name, "name": name,
                    "folder": "", "removed": False, "kept": True, "partial": False}
        if not started:
            return {"ok": False, "msg": "未找到任务: " + name, "name": name,
                    "folder": "", "removed": False, "kept": False, "partial": False}

    removed = False
    partial = False
    if started and not done:
        if folder and os.path.isdir(folder):
            # 进程刚被杀，句柄可能还没释放：_rmtree_force 自带重试 + rd /s /q 兜底
            if _rmtree_force(folder):
                removed = True
                with clone_cleanup_lock:
                    clone_session_started.discard(pid)
                    clone_session_folder.pop(pid, None)
            else:
                partial = True
        else:
            with clone_cleanup_lock:
                clone_session_started.discard(pid)
                clone_session_folder.pop(pid, None)

    if was_queued and not running and not removed and not partial:
        return {"ok": True, "msg": "已从队列移除: " + name, "name": name,
                "folder": "", "removed": False, "kept": False, "partial": False}

    # 空出一个并发位，让排队的任务立刻继续（克隆线程那边要等 10s 才调，太慢）
    try:
        _process_clone_queue()
    except Exception:
        pass

    bits = ["已停止克隆"]
    if removed:
        bits.append("，并删除了未完成的文件夹")
    if partial:
        bits.append("，但文件夹未能删除")
    return {"ok": True, "msg": "".join(bits) + ": " + name, "name": name,
            "folder": folder if partial else "", "removed": removed,
            "kept": False, "partial": partial}


def get_clone_status(pid):
    info = clone_infos.get(pid)
    if info:
        return info
    if pid in clone_queue:
        return {"status": "queue", "progress": 0, "phase": "waiting", "msg": "排队中 (位置" + str(clone_queue.index(pid) + 1) + ")"}
    if pid in clone_procs:
        return {"status": "starting", "progress": 0, "phase": "init", "msg": "启动中"}
    return {"status": "none", "progress": 0, "phase": "", "msg": ""}


def get_all_clone_status():
    # 同 get_all_download_status：前端每 2s 轮询，用一次 IN 查询代替循环里的 N 次 plugin_get
    # 延迟导入：这些符号仍住在 services/__init__.py
    from . import plugin_get_many
    infos = list(clone_infos.keys())
    starts = [p for p in clone_procs if p not in clone_infos]
    pmap = plugin_get_many(infos + starts + list(clone_queue))
    al = []
    for pid in infos:
        info = clone_infos[pid]
        p = pmap.get(pid)
        al.append({"id": pid, "name": p["name"] if p else "#" + str(pid), "repo_url": p["url"] if p else "",
               "mirror": clone_mirrors.get(pid, False), **info})
    for pid in starts:
        p = pmap.get(pid)
        al.append({"id": pid, "name": p["name"] if p else "#" + str(pid), "repo_url": p["url"] if p else "",
                   "status": "starting", "progress": 0, "phase": "init", "msg": "启动中",
                   "mirror": clone_mirrors.get(pid, False)})
    qu = []
    for i, pid in enumerate(clone_queue):
        p = pmap.get(pid)
        qu.append({"id": pid, "name": p["name"] if p else "#" + str(pid),
                   "repo_url": p["url"] if p else "", "queue_position": i + 1})
    return {"active": al, "queue": qu}
