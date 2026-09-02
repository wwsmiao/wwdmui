import os, re, sys, time, json, shutil, tempfile, threading, subprocess
from .config import (settings, DEFAULT_SETTINGS, IS_WINDOWS, IS_LINUX,
                     ARIA2_RPC_PORT, ARIA2_RPC_SECRET, _DEFAULT_ARIA2C, COMFYUI_DIR)
from .database import db_get, plugin_get

# ---- 状态变量 ----
aria2_proc = None
download_gids = {}
download_mirrors = {}
console_threads = {}
download_queue = []
queue_lock = threading.Lock()

clone_procs = {}
clone_infos = {}
clone_mirrors = {}
clone_queue = []
clone_lock = threading.Lock()

BAR_WIDTH = 28


def fmt_size(n):
    if n is None:
        return "?"
    n = int(n)
    if n < 1024:
        return str(n) + "B"
    elif n < 1048576:
        return "%.1fKB" % (n / 1024)
    elif n < 1073741824:
        return "%.1fMB" % (n / 1048576)
    else:
        return "%.2fGB" % (n / 1073741824)


def fmt_speed(n):
    if n is None or n == 0:
        return "0B/s"
    return fmt_size(n) + "/s"


def _console_bar(pct):
    n = int(pct / 100 * BAR_WIDTH)
    return "[" + "=" * n + ">" + "-" * (BAR_WIDTH - n - 1) + "]"


def get_python_path():
    return settings.get("python_path", DEFAULT_SETTINGS["python_path"])


def get_aria2c_path():
    return settings.get("aria2c_path", _DEFAULT_ARIA2C)


def get_comfyui_dir():
    return settings.get("comfyui_dir", COMFYUI_DIR)


def get_git_path():
    return settings.get("git_path", "git")


# ---- 插件卸载 ----
def plugin_uninstall(plugin_name):
    """删除 custom_nodes 下对应的插件文件夹"""
    cn_dir = os.path.join(get_comfyui_dir(), "custom_nodes")
    target = os.path.join(cn_dir, plugin_name)
    if not os.path.isdir(target):
        return False, f"插件文件夹不存在: {plugin_name}"
    try:
        subprocess.run(["cmd", "/c", "rd", "/s", "/q", target],
                       check=True, capture_output=True, timeout=30)
        return True, f"已卸载: {plugin_name}"
    except subprocess.CalledProcessError as e:
        err = e.stderr.decode("gbk", errors="replace").strip()
        return False, err or "删除失败"
    except Exception as e:
        return False, str(e)

def convert_to_mirror(url):
    return url.replace("huggingface.co", "hf-mirror.com") if "huggingface.co" in url else url


def convert_git_mirror(url):
    if "github.com" not in url:
        return url
    url = url.rstrip("/")
    if not url.endswith(".git"):
        url += ".git"
    return url.replace("https://github.com/", "https://api.gitproxy.dev/github.com/")


# ---- 下载状态缓存（RPC 失败时返回最后已知状态） ----
_dl_cache = {}       # rid → {status, progress, total, completed, speed}
_dl_cache_lock = threading.Lock()


# ---- aria2c RPC（含重试 + 指数退避） ----
def aria2_rpc(method, params=None, retries=3, base_delay=0.5):
    import requests
    url = "http://127.0.0.1:" + str(ARIA2_RPC_PORT) + "/jsonrpc"
    payload = {"jsonrpc": "2.0", "id": "1", "method": method,
               "params": ["token:" + ARIA2_RPC_SECRET] + (params or [])}
    last_err = None
    for attempt in range(retries):
        try:
            r = requests.post(url, json=payload, timeout=5)
            j = r.json()
            if "error" in j:
                last_err = j["error"]["message"]
                if attempt < retries - 1:
                    time.sleep(base_delay * (2 ** attempt))
                continue
            return j.get("result"), None
        except Exception as e:
            last_err = str(e)
            if attempt < retries - 1:
                time.sleep(base_delay * (2 ** attempt))
    return None, last_err


def aria2_health_check():
    """轻量 ping 检测 aria2c 是否存活"""
    r, e = aria2_rpc("aria2.getVersion", retries=1)
    return r is not None and "version" in (r or {})


def get_active_count():
    r, e = aria2_rpc("aria2.tellActive")
    return len(r) if r and not e else 0


# ---- aria2c 进程管理 ----
def aria2_start():
    global aria2_proc
    if aria2_proc and aria2_proc.poll() is None:
        return True, "aria2c 已在运行"
    path = get_aria2c_path()
    if not os.path.isfile(path):
        if IS_LINUX and path == "aria2c":
            try:
                r = subprocess.run(["which", "aria2c"], capture_output=True, text=True, encoding="gbk", errors="replace", timeout=5)
                if r.returncode != 0:
                    return False, "未找到aria2c (sudo apt install aria2)"
            except Exception:
                return False, "无法检测aria2c"
        else:
            return False, "找不到aria2c: " + path
    aria2_stop()
    time.sleep(0.5)
    s = settings
    cmd = [path,
        "--rpc-secret=" + ARIA2_RPC_SECRET, "--rpc-listen-port=" + str(ARIA2_RPC_PORT),
        "--rpc-listen-all=false", "--enable-rpc=true",
        "--dir=" + get_comfyui_dir(), "--continue=true",
        "--max-concurrent-downloads=" + str(s.get("max_concurrent_downloads", 3)),
        "--max-connection-per-server=" + str(s.get("max_connection_per_server", 8)),
        "--split=" + str(s.get("split", 8)),
        "--min-split-size=" + s.get("min_split_size", "10M"),
        "--file-allocation=" + s.get("file_allocation", "none"),
        "--max-tries=" + str(s.get("max_tries", 0)),
        "--retry-wait=" + str(s.get("retry_wait", 3)),
        "--connect-timeout=" + str(s.get("connect_timeout", 60)),
        "--timeout=" + str(s.get("timeout", 60)),
        "--lowest-speed-limit=" + s.get("lowest_speed_limit", "1K"),
        "--check-certificate=" + ("true" if s.get("check_certificate", False) else "false"),
        "--log-level=warn", "--console-log-level=warn",
    ]
    try:
        kw = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        if IS_WINDOWS:
            kw["creationflags"] = 0x00000008
        aria2_proc = subprocess.Popen(cmd, **kw)
        time.sleep(1.5)
        if aria2_proc.poll() is None:
            ver, _ = aria2_rpc("aria2.getVersion")
            vstr = ver.get("version", "?") if ver else "?"
            print("[aria2c] 启动 PID=" + str(aria2_proc.pid) + " v" + vstr)
            return True, "aria2c 启动成功 (v" + vstr + ")"
        return False, "aria2c 启动后退出，检查路径"
    except Exception as e:
        return False, "启动失败: " + str(e)


def aria2_stop():
    global aria2_proc
    if aria2_proc and aria2_proc.poll() is None:
        aria2_proc.terminate()
        try:
            aria2_proc.wait(timeout=5)
        except Exception:
            try:
                aria2_proc.kill()
            except Exception:
                pass
    aria2_proc = None
    return "aria2c 已停止"


# ---- 下载流程 ----
def _do_start(rid, m, use_mirror):
    d = os.path.join(get_comfyui_dir(), m["save_path"])
    os.makedirs(d, exist_ok=True)
    url = convert_to_mirror(m["download_url"]) if use_mirror else m["download_url"]
    r, e = aria2_rpc("aria2.addUri", [[url], {"dir": d, "out": m["name"], "continue": "true"}])
    if e:
        print("[FAIL] " + m["name"] + ": " + e)
        return False, e
    download_gids[rid] = r
    download_mirrors[rid] = use_mirror
    t = threading.Thread(target=_console_progress_monitor, args=(rid, r, m["name"]), daemon=True)
    console_threads[rid] = t
    t.start()
    tag = " [镜像]" if use_mirror else ""
    print("\n[下载" + tag + "] " + m["name"] + " gid=" + str(r))
    return True, r


def _console_progress_monitor(rid, gid, name):
    sn = name[:32] + "..." if len(name) > 35 else name
    errs = 0
    last_status = None
    while rid in download_gids:
        r, e = aria2_rpc("aria2.tellStatus", [gid])
        if e:
            errs += 1
            if errs > 30:
                print("\n  [ABORT] " + name + " RPC 连续错误")
                break
            time.sleep(2)
            continue
        errs = 0
        s = r.get("status", "?")
        t = int(r.get("totalLength", 0))
        c = int(r.get("completedLength", 0))
        sp = int(r.get("downloadSpeed", 0))
        pct = (c / t * 100) if t > 0 else 0
        msg = "\r  " + _console_bar(pct) + " " + ("%.1f%%" % pct).rjust(6) + " " + fmt_size(c) + "/" + fmt_size(t) + " " + fmt_speed(sp) + " " + sn[:18]
        print(msg, end="", flush=True)
        if s in ("complete", "error", "removed"):
            print()
            if s == "complete":
                print("  [DONE] " + name + " (" + fmt_size(t) + ")")
            elif s == "error":
                print("  [ERROR] " + name)
            elif s == "removed":
                print("  [CANCEL] " + name)
            last_status = s
            break
        time.sleep(3 if s == "paused" else 1)
    if last_status == "complete":
        time.sleep(15)
    download_gids.pop(rid, None)
    console_threads.pop(rid, None)
    with _dl_cache_lock:
        _dl_cache.pop(rid, None)
    if last_status == "complete":
        _process_dl_queue()


def _process_dl_queue():
    mc = settings.get("max_concurrent_downloads", 3)
    while True:
        with queue_lock:
            if not download_queue:
                break
            rid = download_queue[0]
        if get_active_count() >= mc:
            break
        with queue_lock:
            if rid in download_queue:
                download_queue.remove(rid)
            else:
                continue
        m = db_get(rid)
        if m:
            _do_start(rid, m, download_mirrors.pop(rid, False))
        time.sleep(0.5)


def start_download(rid, use_mirror=False):
    if rid in download_gids:
        return False, "已在下载中"
    if rid in download_queue:
        return False, "已在队列中"
    m = db_get(rid)
    if not m:
        return False, "模型不存在"
    download_mirrors[rid] = use_mirror
    if get_active_count() < settings.get("max_concurrent_downloads", 3):
        ok, msg = _do_start(rid, m, use_mirror)
        if not ok:
            return False, msg
        return True, "下载开始: " + m["name"]
    with queue_lock:
        download_queue.append(rid)
    pos = len(download_queue)
    print("[队列] " + m["name"] + " (位置" + str(pos) + ")")
    return True, "加入队列: " + m["name"] + " (位置" + str(pos) + ")"


def start_batch(rids, use_mirror=False):
    return [{"id": r, "ok": ok, "msg": m} for r in rids for ok, m in [start_download(r, use_mirror)]]


def pause_download(rid):
    g = download_gids.get(rid)
    if not g:
        return False, "未找到任务"
    _, e = aria2_rpc("aria2.pause", [g])
    return (True, "已暂停") if not e else (False, str(e))


def resume_download(rid):
    g = download_gids.get(rid)
    if not g:
        return False, "未找到任务"
    _, e = aria2_rpc("aria2.unpause", [g])
    return (True, "已恢复") if not e else (False, str(e))


def cancel_download(rid):
    with queue_lock:
        if rid in download_queue:
            download_queue.remove(rid)
            download_mirrors.pop(rid, None)
            return True, "已从队列移除"
    g = download_gids.get(rid)
    if not g:
        return False, "未找到任务"
    _, e = aria2_rpc("aria2.remove", [g])
    download_gids.pop(rid, None)
    download_mirrors.pop(rid, None)
    console_threads.pop(rid, None)
    with _dl_cache_lock:
        _dl_cache.pop(rid, None)
    return (True, "已取消") if not e else (False, str(e))


def get_download_status(rid):
    g = download_gids.get(rid)
    if not g:
        return None
    r, e = aria2_rpc("aria2.tellStatus", [g])
    if r is None:
        # RPC 失败 → 返回缓存的最后已知状态
        with _dl_cache_lock:
            cached = _dl_cache.get(rid)
        if cached:
            return {**cached, "stale": True}
        return {"status": "retrying", "progress": 0, "total": 0, "completed": 0,
                "speed": 0, "stale": True, "mirror": download_mirrors.get(rid, False)}
    s = r.get("status", "?")
    t = int(r.get("totalLength", 0))
    c = int(r.get("completedLength", 0))
    sp = int(r.get("downloadSpeed", 0))
    pct = round((c / t * 100), 1) if t > 0 else 0
    # 用上次缓存中更大的 progress 值（避免 t=0 时归零）
    with _dl_cache_lock:
        last = _dl_cache.get(rid, {})
        if t == 0 and last.get("progress", 0) > 0:
            pct = last["progress"]
        fresh = {"status": s, "progress": pct, "total": t, "completed": c,
                 "speed": sp, "mirror": download_mirrors.get(rid, False)}
        _dl_cache[rid] = fresh
    return fresh


def get_all_download_status():
    al = []
    for rid in list(download_gids.keys()):
        st = get_download_status(rid)
        if st:
            m = db_get(rid)
            al.append({"id": rid, "name": m["name"] if m else "#" + str(rid),
                       "save_path": m["save_path"] if m else "", **st})
    qu = [{"id": rid, "name": (db_get(rid) or {}).get("name", "#" + str(rid)),
           "save_path": (db_get(rid) or {}).get("save_path", ""),
           "mirror": download_mirrors.get(rid, False),
           "queue_position": i + 1} for i, rid in enumerate(download_queue)]
    return {"active": al, "queue": qu}


# ---- Git 克隆 ----
def parse_git_progress(line):
    line = line.strip()
    m = re.search(r'Receiving objects:\s+(\d+)%', line)
    if m:
        return (int(m.group(1)), "receiving", line)
    m = re.search(r'Resolving deltas:\s+(\d+)%', line)
    if m:
        return (int(m.group(1)), "resolving", line)
    if 'remote:' in line.lower():
        return (0, "remote", line)
    if 'Cloning into' in line:
        return (0, "cloning", line)
    return None


def _git_clone_thread(pid, repo_url, name, target_dir, use_mirror=False):
    full_path = os.path.join(target_dir, name)
    if os.path.exists(full_path):
        try:
            shutil.rmtree(full_path)
        except Exception as e:
            clone_infos[pid] = {"status": "error", "msg": "清理失败: " + str(e), "progress": 0, "phase": ""}
            clone_procs.pop(pid, None)
            _process_clone_queue()
            return
    actual_url = convert_git_mirror(repo_url) if use_mirror else repo_url
    clone_mirrors[pid] = use_mirror
    git_path = get_git_path()
    cmd = [git_path, "clone", "--progress", actual_url, full_path]
    clone_infos[pid] = {"status": "cloning", "progress": 0, "phase": "remote", "msg": ""}
    tag = " [镜像]" if use_mirror else ""
    print("\n[Git Clone" + tag + "] " + name)
    print("  " + actual_url)
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
                clone_infos[pid] = {"status": "cloning", "progress": max(last_pct, pct), "phase": phase, "msg": detail[:120]}
                bar = _console_bar(max(last_pct, pct))
                sys.stdout.write("\r  " + bar + " " + str(max(last_pct, pct)) + "% " + phase + "          ")
                sys.stdout.flush()
    except Exception:
        pass
    proc.wait()
    if proc.returncode == 0:
        clone_infos[pid] = {"status": "complete", "progress": 100, "phase": "done", "msg": "克隆完成"}
        print("\n  [DONE] " + name + " -> " + full_path)
    else:
        clone_infos[pid] = {"status": "error", "progress": last_pct, "phase": "failed", "msg": "退出码: " + str(proc.returncode)}
        print("\n  [ERROR] " + name + " 退出码: " + str(proc.returncode))
    clone_procs.pop(pid, None)
    time.sleep(10)
    clone_infos.pop(pid, None)
    _process_clone_queue()


def _get_clone_count():
    return len([p for p in clone_procs.values() if p and p.poll() is None])


def _process_clone_queue():
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


def start_clone(pid, use_mirror=False):
    if pid in clone_procs:
        return False, "已在克隆中"
    if pid in clone_queue:
        return False, "已在队列中"
    p = plugin_get(pid)
    if not p:
        return False, "插件不存在"
    clone_mirrors[pid] = use_mirror
    if _get_clone_count() < settings.get("max_concurrent_clones", 2):
        cn = os.path.join(get_comfyui_dir(), "custom_nodes")
        t = threading.Thread(target=_git_clone_thread, args=(pid, p["url"], p["name"], cn, use_mirror), daemon=True)
        t.start()
        tag = " [镜像]" if use_mirror else ""
        return True, "开始克隆" + tag + ": " + p["name"]
    with clone_lock:
        clone_queue.append(pid)
    return True, "加入队列: " + p["name"] + " (位置" + str(len(clone_queue)) + ")"


def start_clone_batch(pids, use_mirror=False):
    return [{"id": p, "ok": ok, "msg": m} for p in pids for ok, m in [start_clone(p, use_mirror)]]


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
    al = []
    for pid in list(clone_infos.keys()):
        info = clone_infos[pid]
        p = plugin_get(pid)
        al.append({"id": pid, "name": p["name"] if p else "#" + str(pid), "repo_url": p["url"] if p else "",
               "mirror": clone_mirrors.get(pid, False), **info})
    for pid in list(clone_procs.keys()):
        if pid not in clone_infos:
            p = plugin_get(pid)
            al.append({"id": pid, "name": p["name"] if p else "#" + str(pid), "repo_url": p["url"] if p else "",
                       "status": "starting", "progress": 0, "phase": "init", "msg": "启动中",
                       "mirror": clone_mirrors.get(pid, False)})
    qu = []
    for i, pid in enumerate(clone_queue):
        p = plugin_get(pid)
        qu.append({"id": pid, "name": p["name"] if p else "#" + str(pid),
                   "repo_url": p["url"] if p else "", "queue_position": i + 1})
    return {"active": al, "queue": qu}


# ============================================================
# ComfyUI 启动器
# ============================================================
comfyui_proc = None
comfyui_logs = []
comfyui_log_lock = threading.Lock()
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")

# 清理日志中的不可见字符（UTF-8 解码失败残渣、孤立的 surrogate）
_SURROGATE_RE = re.compile(r"[\ud800-\udfff]")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_FFFD_RE = re.compile("�")

def _strip_ansi(s):
    s = _ANSI_RE.sub("", s)
    s = _FFFD_RE.sub("", s)
    s = _SURROGATE_RE.sub("", s)
    s = _CONTROL_RE.sub("", s)
    return s

# VRAM 模式映射
VRAM_MODES = {
    "highvram": "--highvram", "lowvram": "--lowvram", "novram": "--novram",
    "gpu_only": "--gpu-only", "cpu": "--cpu",
}


def build_comfyui_cmd(python_path, comfyui_dir):
    """根据全局设置构建 ComfyUI 启动命令 (不含环境变量预设前缀)"""
    from . import config
    s = config.settings
    main_py = os.path.join(comfyui_dir, "main.py")
    cmd = [python_path, main_py]
    listen_ip = (s.get("comfyui_listen") or "").strip()
    if listen_ip:
        cmd += ["--listen", listen_ip]
    port = (s.get("comfyui_port") or "").strip()
    if port:
        cmd += ["--port", port]
    if s.get("comfyui_auto_launch", True):
        cmd.append("--auto-launch")
    vram = (s.get("comfyui_vram") or "default").strip()
    if vram in VRAM_MODES:
        cmd.append(VRAM_MODES[vram])
    preview = (s.get("comfyui_preview_method") or "default").strip()
    if preview != "default":
        cmd += ["--preview-method", preview]
    cors = (s.get("comfyui_cors_origin") or "").strip()
    if cors:
        cmd += ["--enable-cors-header", cors]
    extra = (s.get("comfyui_extra_args") or "").strip()
    if extra:
        cmd += extra.split()
    return cmd


def _build_preset_env_prefix():
    """构建环境变量预设 shell 前缀（用于在 cmd /c 中注入）"""
    presets = settings.get("comfyui_presets", [])
    # 去重: 同名变量最后一次出现生效，过滤非法 key
    kv = {}
    for p in presets:
        k = (p.get("key", "") or "").strip()
        if k and _is_safe_env_key(k):
            v = (p.get("value", "") or "").strip()
            kv[k] = v
    parts = [f"set {k}={v}" for k, v in kv.items()]
    return " && ".join(parts) + " && " if parts else None


def _is_safe_env_key(k):
    """检查环境变量名是否合法 (不含空格、=、&、| 等危险字符)"""
    if not k.isidentifier():
        return False
    for ch in k:
        if ch in ('"', "'", "&", "|", ">", "<", "`", "$", "\n", "\r", " ", "=", ";", "\\"):
            return False
    return True


def start_comfyui(python_path, comfyui_dir):
    global comfyui_proc, comfyui_logs
    if comfyui_proc and comfyui_proc.poll() is None:
        return {"ok": False, "msg": "ComfyUI 已在运行中"}
    if not os.path.isfile(python_path):
        return {"ok": False, "msg": "Python 路径不存在: " + python_path}
    main_py = os.path.join(comfyui_dir, "main.py")
    if not os.path.exists(main_py):
        return {"ok": False, "msg": "未找到 main.py: " + main_py}
    cmd = build_comfyui_cmd(python_path, comfyui_dir)
    prefix = _build_preset_env_prefix()
    if prefix:
        # 用 cmd /c 注入环境变量预设
        wrapped = "cmd /c " + prefix + " ".join(f'"{a}"' if " " in a else a for a in cmd)
        cmd = wrapped
        shell = True
    else:
        shell = False
    with comfyui_log_lock:
        comfyui_logs = []
    creationflags = subprocess.CREATE_NO_WINDOW if IS_WINDOWS else 0
    try:
        comfyui_proc = subprocess.Popen(
            cmd,
            cwd=comfyui_dir,
            shell=shell,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="surrogateescape",
            creationflags=creationflags,
        )
    except FileNotFoundError as e:
        return {"ok": False, "msg": "找不到可执行文件: " + str(e)}
    except Exception as e:
        return {"ok": False, "msg": "启动失败: " + str(e)}

    def _reader():
        global comfyui_logs
        try:
            for line in comfyui_proc.stdout:
                with comfyui_log_lock:
                    comfyui_logs.append(_strip_ansi(line.rstrip()))
        except Exception:
            pass

    threading.Thread(target=_reader, daemon=True).start()
    return {"ok": True, "msg": "ComfyUI 已启动", "pid": comfyui_proc.pid}


def stop_comfyui():
    global comfyui_proc
    if not comfyui_proc:
        return {"ok": False, "msg": "ComfyUI 未在运行"}
    if comfyui_proc.poll() is not None:
        comfyui_proc = None
        return {"ok": True, "msg": "ComfyUI 已停止"}
    try:
        comfyui_proc.terminate()
        comfyui_proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        comfyui_proc.kill()
        comfyui_proc.wait(timeout=3)
    except Exception:
        try:
            comfyui_proc.kill()
        except Exception:
            pass
    comfyui_proc = None
    return {"ok": True, "msg": "ComfyUI 已停止"}


def get_comfyui_status():
    if comfyui_proc and comfyui_proc.poll() is None:
        return {"running": True, "pid": comfyui_proc.pid}
    return {"running": False, "pid": None}


def get_comfyui_logs(since=0):
    with comfyui_log_lock:
        total = len(comfyui_logs)
        if since >= total:
            return {"lines": [], "next_index": since, "total": total}
        new_lines = comfyui_logs[since:]
        return {"lines": new_lines, "next_index": total, "total": total}


# ============================================================
# 修复安装（克隆 / 依赖 / PyTorch）
# ============================================================
repair_proc = None
repair_logs = []
repair_log_lock = threading.Lock()
repair_action = ""


def parse_pytorch_info(path):
    """解析 pytorch信息.txt 为 [{version, variants: [{label, cmd}]}]"""
    options = []
    if not os.path.exists(path):
        return options
    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    cur_ver = None
    cur_vars = []
    last_comment = ""
    for line in lines:
        s = line.strip()
        if not s:
            last_comment = ""
            continue
        m = re.match(r"^torch\s+v([\d.]+)$", s)
        if m:
            if cur_ver and cur_vars:
                options.append({"version": cur_ver, "variants": cur_vars})
            cur_ver = m.group(1)
            cur_vars = []
            last_comment = ""
            continue
        if s.startswith("#"):
            last_comment = s[1:].strip()
            continue
        if s.startswith("pip install torch"):
            label = last_comment if last_comment else "通用"
            if "rocm" not in label.lower() and "linux" not in label.lower():
                cur_vars.append({"label": label, "cmd": s})
            last_comment = ""
    if cur_ver and cur_vars:
        options.append({"version": cur_ver, "variants": cur_vars})
    return options


def _repair_reader(proc):
    global repair_logs
    try:
        for line in proc.stdout:
            with repair_log_lock:
                repair_logs.append(_strip_ansi(line.rstrip()))
    except Exception:
        pass


def _start_repair_proc(cmd, cwd, action_name):
    global repair_proc, repair_logs, repair_action
    if repair_proc and repair_proc.poll() is None:
        return {"ok": False, "msg": "已有操作" + repair_action + " 正在运行，请先停止"}
    with repair_log_lock:
        repair_logs = []
    repair_action = action_name
    cf = subprocess.CREATE_NO_WINDOW if IS_WINDOWS else 0
    repair_proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
        text=True, encoding="gbk", errors="replace", creationflags=cf)
    threading.Thread(target=_repair_reader, args=(repair_proc,), daemon=True).start()
    return {"ok": True, "msg": action_name + " 已启动", "pid": repair_proc.pid}


def _repair_run_steps(steps, action_name):
    """跨平台多步修复：在后台线程中依次执行命令，输出写入 repair_logs
    steps: [(label, cmd_list, cwd), ...]
    完成后自动置 repair_proc=None"""
    global repair_proc, repair_logs, repair_action
    if repair_proc and repair_proc.poll() is None:
        return {"ok": False, "msg": "已有操作" + repair_action + " 正在运行，请先停止"}
    with repair_log_lock:
        repair_logs = []
    repair_action = action_name
    # 临时代理对象：poll() 返回 None 表示"运行中"，kill() 设标志位
    stop_flag = threading.Event()
    proxy = type("RepairProxy", (), {
        "poll": lambda self=None: None if not stop_flag.is_set() else 0,
        "pid": 0,
        "_stop_flag": stop_flag,
    })()
    repair_proc = proxy

    def _run():
        cf = subprocess.CREATE_NO_WINDOW if IS_WINDOWS else 0
        for label, cmd, cwd in steps:
            if stop_flag.is_set():
                with repair_log_lock:
                    repair_logs.append("!!! 操作已被用户停止")
                break
            with repair_log_lock:
                repair_logs.append("")
                repair_logs.append(f"--- {label} ---")
            try:
                proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                    text=True, encoding="gbk", errors="replace", creationflags=cf)
                for line in proc.stdout:
                    if stop_flag.is_set():
                        proc.terminate()
                        break
                    with repair_log_lock:
                        repair_logs.append(_strip_ansi(line.rstrip()))
                proc.wait()
                rc = proc.returncode
                with repair_log_lock:
                    repair_logs.append(f"--- {label} 完成 (exit={rc}) ---")
            except Exception as e:
                with repair_log_lock:
                    repair_logs.append(f"!!! {label} 异常: {e}")
        with repair_log_lock:
            repair_logs.append("")
            repair_logs.append(f"===== {action_name} 全部完成 =====")
        global repair_proc, repair_action
        repair_proc = None
        repair_action = ""

    threading.Thread(target=_run, daemon=True).start()
    return {"ok": True, "msg": action_name + " 已启动", "pid": 0}


def repair_clone(git_path, mirror, target_dir):
    if os.path.exists(os.path.join(target_dir, "main.py")):
        return {"ok": False, "msg": "ComfyUI 已存在于 " + target_dir + "，无需克隆"}
    url = "https://api.gitproxy.dev/github.com/Comfy-Org/ComfyUI.git" if mirror else \
          "https://github.com/Comfy-Org/ComfyUI"
    os.makedirs(target_dir, exist_ok=True)
    return _start_repair_proc([git_path, "clone", url, target_dir],
                              os.path.dirname(target_dir), "克隆 ComfyUI")


def get_comfyui_branches(git_path, comfyui_dir):
    """获取 ComfyUI 仓库的分支和 tag 列表，返回 {branches:[], tags:[], current:str}"""
    if not os.path.isdir(os.path.join(comfyui_dir, ".git")):
        return {"ok": False, "msg": "ComfyUI 目录不是 Git 仓库: " + comfyui_dir}
    cf = subprocess.CREATE_NO_WINDOW if IS_WINDOWS else 0
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "Never"

    def _run(cmd, cwd):
        try:
            p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                              encoding="gbk", errors="replace",
                              creationflags=cf, env=env, timeout=45)
            return p.stdout.strip(), p.returncode
        except Exception as e:
            return str(e), 1

    # fetch latest
    _run([git_path, "fetch", "--all", "--tags"], comfyui_dir)

    # get current branch
    cur_out, _ = _run([git_path, "rev-parse", "--abbrev-ref", "HEAD"], comfyui_dir)
    current = cur_out if cur_out else "unknown"

    # remote branches (origin/* only, distinct names)
    br_out, _ = _run([git_path, "branch", "-r"], comfyui_dir)
    branches = []
    for line in br_out.split("\n"):
        b = line.strip().lstrip("* ").strip()
        if b.startswith("origin/") and "HEAD" not in b:
            name = b[len("origin/"):]
            if name not in branches:
                branches.append(name)

    # tags (latest 50, sorted by version-ish)
    tag_out, _ = _run([git_path, "tag", "--sort=-creatordate"], comfyui_dir)
    tags = [t.strip() for t in tag_out.split("\n") if t.strip()][:50]

    return {"ok": True, "current": current, "branches": branches, "tags": tags}


def repair_comfyui_update(git_path, comfyui_dir, target):
    """更新 ComfyUI：target 为空则 git pull 当前分支；否则 checkout target 后 pull"""
    if not os.path.isdir(os.path.join(comfyui_dir, ".git")):
        return {"ok": False, "msg": "ComfyUI 目录不是 Git 仓库: " + comfyui_dir}
    target = (target or "").strip()
    label = "更新 ComfyUI 到最新版" if not target else f"更新 ComfyUI → {target}"
    if target:
        # fetch tags & branches, then checkout target and pull
        steps = [
            ("获取远端分支/标签", [git_path, "fetch", "--all", "--tags"], comfyui_dir),
            (f"切换到 {target}", [git_path, "checkout", target], comfyui_dir),
            ("拉取最新代码", [git_path, "pull"], comfyui_dir),
        ]
        return _repair_run_steps(steps, label)
    else:
        # just pull current branch
        steps = [
            ("拉取最新代码 (git pull)", [git_path, "pull"], comfyui_dir),
        ]
        return _repair_run_steps(steps, label)


def repair_requirements(python_path, comfyui_dir):
    req = os.path.join(comfyui_dir, "requirements.txt")
    if not os.path.exists(req):
        return {"ok": False, "msg": "未找到 requirements.txt: " + req}
    return _start_repair_proc(_pip_install_cmd(python_path, ["-r", req]),
                              comfyui_dir, "安装 requirements")


def repair_pytorch(python_path, cmd_str):
    if not cmd_str or not cmd_str.startswith("pip install"):
        return {"ok": False, "msg": "无效的安装命令"}
    parts = cmd_str.split()
    if parts[0] == "pip":
        parts = [python_path, "-m", "pip"] + parts[1:]
    return _start_repair_proc(parts, os.path.dirname(python_path), "安装 PyTorch")


def repair_stop():
    global repair_proc, repair_action
    if not repair_proc or repair_proc.poll() is not None:
        repair_proc = None
        repair_action = ""
        return {"ok": False, "msg": "没有正在运行的操作"}
    # 支持多步代理对象
    if hasattr(repair_proc, "_stop_flag"):
        repair_proc._stop_flag.set()
        repair_proc = None
        a = repair_action
        repair_action = ""
        return {"ok": True, "msg": a + " 已停止"}
    # 单步子进程
    try:
        repair_proc.terminate()
        repair_proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        repair_proc.kill()
        repair_proc.wait(timeout=3)
    except Exception:
        try:
            repair_proc.kill()
        except Exception:
            pass
    repair_proc = None
    a = repair_action
    repair_action = ""
    return {"ok": True, "msg": a + " 已停止"}


def repair_purge_cache(python_path):
    """清除 pip 缓存 — 兼容新旧 pip，并手动删除缓存目录"""
    pip_dir = os.path.join(os.path.dirname(python_path), "..", "Lib", "site-packages") if IS_WINDOWS else None
    # 先尝试 pip cache purge（pip >= 20.1）
    steps = [
        ("pip cache purge", [python_path, "-m", "pip", "cache", "purge"], os.path.dirname(python_path)),
    ]
    # 备用：手动清 pip 缓存目录
    if IS_WINDOWS:
        localapp = os.environ.get("LOCALAPPDATA", "")
        if localapp:
            pip_dir = os.path.join(localapp, "pip", "cache")
            if os.path.isdir(pip_dir):
                steps.append(("手动清理 %LOCALAPPDATA%\\pip\\cache", ["cmd", "/c", "rd", "/s", "/q", pip_dir], None))
    return _repair_run_steps(steps, "清除 pip 缓存")


def repair_status():
    running = repair_proc is not None and repair_proc.poll() is None
    return {"running": running, "action": repair_action if running else "",
            "pid": repair_proc.pid if running and repair_proc and hasattr(repair_proc, "pid") else None}


def repair_logs_get(since=0):
    with repair_log_lock:
        total = len(repair_logs)
        if since >= total:
            return {"lines": [], "next_index": since, "total": total}
        return {"lines": repair_logs[since:], "next_index": total, "total": total}


# ---- 镜像源 ----
_PIP_MIRRORS = {
    "default": "",                           # 官方源（国外），不加 -i
    "tsinghua": "https://pypi.tuna.tsinghua.edu.cn/simple",
    "aliyun": "https://mirrors.aliyun.com/pypi/simple/",
    "tencent": "https://mirrors.cloud.tencent.com/pypi/simple",
    "douban": "http://pypi.douban.com/simple/",
    "ustc": "https://pypi.mirrors.ustc.edu.cn/simple",
}

def _pip_install_cmd(python_path, packages, cwd=None):
    mirror = settings.get("pip_mirror", "default")
    mirror_url = _PIP_MIRRORS.get(mirror, "")
    cmd = [python_path, "-m", "pip", "install"]
    if isinstance(packages, list):
        cmd += packages
    else:
        cmd.append(packages)
    if mirror_url:
        cmd += ["-i", mirror_url, "--trusted-host", mirror_url.split("/")[2]]
    return cmd




def repair_pip_install(python_path, packages, mirror):
    pkgs = [p.strip() for p in packages.split() if p.strip()]
    if not pkgs:
        return {"ok": False, "msg": "Please fill dependency package names"}
    cmd = _pip_install_cmd(python_path, pkgs)
    mirror_name = settings.get("pip_mirror", "default")
    mirror_url = _PIP_MIRRORS.get(mirror_name, "")
    label = f"pip install {' '.join(pkgs)}" + (f" (mirror: {mirror_name})" if mirror_url else "")
    return _repair_run_steps([(label, cmd, os.path.dirname(python_path))],
                            f"安装依赖 {' '.join(pkgs)}")


def repair_download_file(aria2c_path, url, save_path):
    """使用 aria2c 下载文件到指定路径"""
    if not url or not url.strip():
        return {"ok": False, "msg": "请填写下载链接"}
    if not save_path or not save_path.strip():
        save_path = os.path.join(COMFYUI_DIR, "models")
    os.makedirs(save_path, exist_ok=True)
    url = url.strip()
    save_path = save_path.strip()
    fn = url.rsplit("/", 1)[-1].split("?")[0] or "download"
    return _repair_run_steps(
        [(f"下载 {fn}", [aria2c_path, url, "-d", save_path], save_path)],
        f"下载文件 {fn}"
    )


def repair_single_plugin(git_path, python_path, comfyui_dir, plugin_name, mirror=None):
    """跨平台：修复单个插件 — git pull + pip install -r requirements.txt
    mirror: 可选镜像前缀，如 'https://api.gitproxy.dev/github.com/'，用于 git pull 加速
    """
    cn_dir = os.path.join(comfyui_dir, "custom_nodes")
    plugin_dir = os.path.join(cn_dir, plugin_name)
    if not os.path.isdir(plugin_dir):
        return {"ok": False, "msg": f"插件目录不存在: {plugin_dir}"}
    if not os.path.isdir(os.path.join(plugin_dir, ".git")):
        return {"ok": False, "msg": f"不是 Git 仓库: {plugin_dir}"}
    # git pull 命令（可选镜像加速）
    pull_args = [git_path]
    if mirror:
        pull_args += ["-c", f"url.{mirror}.insteadOf=https://github.com/"]
    pull_args.append("pull")
    pull_label = "git pull" + (" [镜像]" if mirror else "")
    req_file = os.path.join(plugin_dir, "requirements.txt")
    steps = [
        (pull_label, list(pull_args), plugin_dir),
    ]
    if os.path.isfile(req_file):
        steps.append(
            ("安装 requirements.txt", _pip_install_cmd(python_path, ["-r", req_file]), plugin_dir)
        )
    else:
        with repair_log_lock:
            repair_logs.append("未找到 requirements.txt，跳过依赖安装")
    title = f"修复插件 {plugin_name}" + (" [镜像]" if mirror else "")
    return _repair_run_steps(steps, title)


def repair_all_plugins(git_path, python_path, comfyui_dir, mirror=None):
    """跨平台：扫描 custom_nodes，逐个 git pull + pip install
    mirror: 可选镜像前缀，如 'https://api.gitproxy.dev/github.com/'，用于 git pull 时加速
    """
    cn_dir = os.path.join(comfyui_dir, "custom_nodes")
    if not os.path.isdir(cn_dir):
        return {"ok": False, "msg": f"custom_nodes 目录不存在: {cn_dir}"}
    plugins = []
    for name in sorted(os.listdir(cn_dir)):
        p = os.path.join(cn_dir, name)
        if os.path.isdir(p) and os.path.isdir(os.path.join(p, ".git")):
            plugins.append(name)
    if not plugins:
        return {"ok": False, "msg": "未找到任何可修复的 Git 插件"}
    # 加速拉取：-c url.<mirror>.insteadOf=https://github.com/
    pull_args = [git_path]
    if mirror:
        pull_args += ["-c", f"url.{mirror}.insteadOf=https://github.com/"]
    pull_args.append("pull")
    steps = []
    for i, pn in enumerate(plugins, 1):
        pd = os.path.join(cn_dir, pn)
        label = f"[{i}/{len(plugins)}] {pn} — git pull"
        if mirror:
            label += " [镜像]"
        steps.append((label, list(pull_args), pd))
        req = os.path.join(pd, "requirements.txt")
        if os.path.isfile(req):
            steps.append((f"[{i}/{len(plugins)}] {pn} — 安装依赖",
                         _pip_install_cmd(python_path, ["-r", req]), pd))
    title = f"批量修复 {len(plugins)} 个插件"
    if mirror:
        title += " [镜像]"
    return _repair_run_steps(steps, title)


def kill_comfyui_processes():
    """跨平台：强制结束所有 ComfyUI Python 进程及其子进程树，并释放端口"""
    killed = 0
    my_pid = str(os.getpid())
    cf = subprocess.CREATE_NO_WINDOW if IS_WINDOWS else 0
    try:
        if IS_WINDOWS:
            # 1) 先用 wmic 找到所有 ComfyUI 相关进程，/T 杀整棵进程树
            result = subprocess.run(
                ["wmic", "process", "where", "name='python.exe'",
                 "get", "processid,commandline", "/format:csv"],
                capture_output=True, text=True, encoding="gbk", errors="replace", timeout=15, creationflags=cf)
            for line in result.stdout.split("\n"):
                low = line.strip().lower()
                if "comfyui" not in low:
                    continue
                parts = line.split(",")
                if len(parts) >= 2:
                    pid = parts[1].strip()
                    if pid and pid != my_pid and pid.isdigit():
                        try:
                            # /T 强制结束进程树（含子进程）
                            subprocess.run(["taskkill", "/F", "/T", "/PID", pid],
                                         capture_output=True, timeout=10, creationflags=cf)
                            killed += 1
                        except Exception:
                            pass
            # 2) 如果有 ComfyUI 端口配置，用 netstat 查找占用该端口的进程
            port = settings.get("comfyui_port", "") or "8188"
            try:
                nr = subprocess.run(
                    ["netstat", "-ano"],
                    capture_output=True, text=True, encoding="gbk", errors="replace", timeout=10, creationflags=cf)
                for ln in nr.stdout.split("\n"):
                    if f":{port}" in ln and "LISTENING" in ln:
                        parts = ln.strip().split()
                        pid = parts[-1] if parts else ""
                        if pid and pid != my_pid and pid.isdigit():
                            try:
                                subprocess.run(["taskkill", "/F", "/T", "/PID", pid],
                                             capture_output=True, timeout=10, creationflags=cf)
                                killed += 1
                            except Exception:
                                pass
            except Exception:
                pass
        else:
            # Linux / macOS: pgrep -f comfyui + 进程树终止
            result = subprocess.run(["pgrep", "-f", "comfyui"],
                                  capture_output=True, text=True, encoding="gbk", errors="replace", timeout=10)
            for pid in result.stdout.strip().split("\n"):
                pid = pid.strip()
                if pid and pid != my_pid and pid.isdigit():
                    try:
                        # 先杀子进程组，再杀主进程
                        subprocess.run(["kill", "-9", "-" + pid], timeout=5)
                        killed += 1
                    except Exception:
                        try:
                            os.kill(int(pid), 9)
                            killed += 1
                        except Exception:
                            pass
            # 检查端口占用
            port = settings.get("comfyui_port", "") or "8188"
            try:
                lr = subprocess.run(["lsof", "-ti", f":{port}"],
                                  capture_output=True, text=True, encoding="gbk", errors="replace", timeout=5)
                for pid in lr.stdout.strip().split("\n"):
                    pid = pid.strip()
                    if pid and pid.isdigit():
                        try:
                            os.kill(int(pid), 9)
                            killed += 1
                        except Exception:
                            pass
            except Exception:
                pass
        # 终止跟踪的子进程
        global comfyui_proc
        if comfyui_proc and comfyui_proc.poll() is None:
            try:
                comfyui_proc.kill()
                comfyui_proc.wait(timeout=3)
                killed += 1
            except Exception:
                pass
            comfyui_proc = None
    except Exception as e:
        return {"ok": False, "msg": f"结束 ComfyUI 进程失败: {e}"}
    return {"ok": True, "msg": f"已结束 {killed} 个 ComfyUI 进程，端口已释放"}


def kill_aria2_processes():
    """跨平台：强制结束所有 aria2c 进程"""
    killed = 0
    cf = subprocess.CREATE_NO_WINDOW if IS_WINDOWS else 0
    try:
        if IS_WINDOWS:
            subprocess.run(["taskkill", "/F", "/IM", "aria2c.exe"],
                         capture_output=True, timeout=10, creationflags=cf)
            killed += 1
        else:
            subprocess.run(["pkill", "-9", "aria2c"],
                         capture_output=True, timeout=10)
            killed += 1
        # 终止跟踪的子进程
        global aria2_proc
        if aria2_proc and aria2_proc.poll() is None:
            try:
                aria2_proc.kill()
                aria2_proc.wait(timeout=3)
            except Exception:
                pass
        aria2_proc = None
    except Exception as e:
        return {"ok": False, "msg": f"结束 aria2 进程失败: {e}"}
    return {"ok": True, "msg": "已结束所有 aria2c 进程"}


# ============================================================
# 输出管理 — 文件浏览
# ============================================================

# 文件类型识别
_IMG_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg", ".ico"}
_VID_EXTS = {".mp4", ".webm", ".mov", ".avi", ".mkv", ".flv", ".wmv", ".m4v"}
_AUD_EXTS = {".mp3", ".wav", ".flac", ".ogg", ".aac", ".m4a", ".wma", ".opus"}


def _file_type(name):
    ext = os.path.splitext(name)[1].lower()
    if ext in _IMG_EXTS:
        return "image"
    if ext in _VID_EXTS:
        return "video"
    if ext in _AUD_EXTS:
        return "audio"
    return "other"


def _safe_path(root_dir, rel_path):
    """防御路径穿越：将 rel_path 解析到 root_dir 内，越界则返回 None"""
    root = os.path.abspath(root_dir)
    target = os.path.abspath(os.path.join(root, rel_path))
    if os.path.commonpath([root, target]) != root:
        return None
    return target


def _fuzzy_match(query, name):
    """模糊匹配：query 整体是 name 子串，或 query 各字符按顺序出现在 name 中（不区分大小写）"""
    q = query.lower()
    n = name.lower()
    if q in n:
        return True
    idx = 0
    for ch in q:
        idx = n.find(ch, idx)
        if idx == -1:
            return False
        idx += 1
    return True


def _sort_items(items, sort):
    """排序：name 按名称、date 按修改时间；_desc 表示倒序（date_desc = 最新在前）"""
    rev = sort.endswith("_desc")
    if sort.startswith("name"):
        items.sort(key=lambda x: x.get("name", "").lower(), reverse=rev)
    elif sort.startswith("date"):
        items.sort(key=lambda x: x.get("modified", 0), reverse=rev)
    else:
        items.sort(key=lambda x: x.get("name", "").lower())
    # 稳定二次排序：目录在前，文件在后（不改变组内已排好的顺序）
    items.sort(key=lambda x: 0 if x.get("is_dir") else 1)
    return items


def recursive_search(root_dir, query, sort, page, page_size, type_fn):
    """跨文件夹模糊搜索文件名，返回与 browse 相同的分页结构（带 recursive 标记）"""
    items = []
    for dirpath, dirnames, filenames in os.walk(root_dir):
        for name in dirnames:
            if not _fuzzy_match(query, name):
                continue
            full = os.path.join(dirpath, name)
            try:
                st = os.stat(full)
            except OSError:
                continue
            rel = os.path.relpath(full, root_dir).replace("\\", "/")
            items.append({"name": name, "path": rel, "modified": st.st_mtime, "is_dir": True})
        for name in filenames:
            if not _fuzzy_match(query, name):
                continue
            ft = type_fn(name)
            if ft == "other":
                continue
            full = os.path.join(dirpath, name)
            try:
                st = os.stat(full)
            except OSError:
                continue
            rel = os.path.relpath(full, root_dir).replace("\\", "/")
            items.append({"name": name, "path": rel, "type": ft, "size": st.st_size,
                          "modified": st.st_mtime, "is_dir": False})
    _sort_items(items, sort)
    total = len(items)
    ps = max(1, min(100, int(page_size)))
    tp = max(1, (total + ps - 1) // ps) if total else 1
    pg = max(1, min(tp, int(page)))
    start = (pg - 1) * ps
    return {
        "ok": True, "current_path": "", "breadcrumbs": [], "parent_path": None,
        "items": items[start:start + ps], "page": pg, "page_size": ps,
        "total": total, "total_pages": tp, "search": query, "sort": sort,
        "recursive": True,
    }


def browse_output_dir(root_dir, rel_path="", page=1, page_size=20,
                      search="", sort="date_desc"):
    """浏览输出目录：文件夹在前、文件在后，支持分页/搜索/排序。
    带 search 时执行跨文件夹模糊搜索。"""
    if search and search.strip():
        return recursive_search(root_dir, search.strip(), sort, page, page_size, _file_type)
    target = _safe_path(root_dir, rel_path)
    if not target:
        return {"ok": False, "msg": "无效路径"}
    if not os.path.isdir(target):
        return {"ok": False, "msg": "目录不存在"}

    # 面包屑
    breadcrumbs = []
    try:
        rel_norm = os.path.relpath(target, root_dir)
    except ValueError:
        rel_norm = ""
    if rel_norm == ".":
        rel_norm = ""
    parts = rel_norm.replace("\\", "/").split("/") if rel_norm else []
    acc = ""
    for p in parts:
        if not p:
            continue
        acc = os.path.join(acc, p) if acc else p
        breadcrumbs.append({"name": p, "path": acc.replace("\\", "/")})

    # 枚举
    try:
        entries = os.listdir(target)
    except OSError:
        return {"ok": False, "msg": "无法读取目录"}

    folders_raw = []
    files_raw = []
    for name in entries:
        full = os.path.join(target, name)
        if os.path.isdir(full):
            child_rel = os.path.join(rel_path, name) if rel_path else name
            try:
                st = os.stat(full)
                folders_raw.append({
                    "name": name, "path": child_rel.replace("\\", "/"),
                    "modified": st.st_mtime, "is_dir": True,
                })
            except OSError:
                pass
        elif os.path.isfile(full):
            ft = _file_type(name)
            if ft == "other":
                continue
            try:
                st = os.stat(full)
                files_raw.append({
                    "name": name,
                    "path": (os.path.join(rel_path, name).replace("\\", "/") if rel_path else name),
                    "type": ft, "size": st.st_size,
                    "modified": st.st_mtime, "is_dir": False,
                })
            except OSError:
                pass

    # 搜索
    if search:
        q = search.lower()
        folders_raw = [f for f in folders_raw if q in f["name"].lower()]
        files_raw = [f for f in files_raw if q in f["name"].lower()]

    # 排序
    rev = sort.endswith("_desc")
    if sort.startswith("name"):
        folders_raw.sort(key=lambda x: x["name"].lower(), reverse=rev)
        files_raw.sort(key=lambda x: x["name"].lower(), reverse=rev)
    elif sort.startswith("date"):
        folders_raw.sort(key=lambda x: x["modified"], reverse=rev)
        files_raw.sort(key=lambda x: x["modified"], reverse=rev)
    else:
        folders_raw.sort(key=lambda x: x["name"].lower())
        files_raw.sort(key=lambda x: x["name"].lower())

    # 分页
    total = len(folders_raw) + len(files_raw)
    ps = max(1, min(100, int(page_size)))
    tp = max(1, (total + ps - 1) // ps) if total else 1
    pg = max(1, min(tp, int(page)))
    start = (pg - 1) * ps
    end = start + ps
    all_items = folders_raw + files_raw
    paged = all_items[start:end]

    return {
        "ok": True,
        "current_path": rel_norm.replace("\\", "/") if rel_norm else "",
        "breadcrumbs": breadcrumbs,
        "parent_path": os.path.dirname(rel_norm).replace("\\", "/") if rel_norm else None,
        "items": paged,
        "page": pg, "page_size": ps, "total": total, "total_pages": tp,
        "search": search, "sort": sort,
    }


def serve_output_file(root_dir, rel_path):
    """安全返回输出文件绝对路径（供 send_file 使用）"""
    target = _safe_path(root_dir, rel_path)
    if not target or not os.path.isfile(target):
        return None
    return target


def zip_files(root_dir, rel_paths, zip_name="files.zip"):
    """将多个文件/文件夹打包为 zip，返回 BytesIO（供 send_file 下载）"""
    import zipfile as _zipfile
    import io as _io
    buf = _io.BytesIO()
    with _zipfile.ZipFile(buf, 'w', _zipfile.ZIP_DEFLATED) as zf:
        for rp in rel_paths:
            t = _safe_path(root_dir, rp)
            if not t or not os.path.exists(t):
                continue
            if os.path.isdir(t):
                for dirpath, _dirnames, filenames in os.walk(t):
                    for fn in filenames:
                        full = os.path.join(dirpath, fn)
                        zf.write(full, os.path.relpath(full, root_dir).replace("\\", "/"))
            else:
                zf.write(t, os.path.relpath(t, root_dir).replace("\\", "/"))
    buf.seek(0)
    return buf


def delete_output_files(root_dir, rel_paths):
    """批量删除文件/文件夹"""
    results = []
    for rp in rel_paths:
        t = _safe_path(root_dir, rp)
        if not t or not os.path.exists(t):
            results.append({"path": rp, "ok": False, "msg": "路径无效或不存在"})
            continue
        try:
            if os.path.isdir(t):
                shutil.rmtree(t)
            else:
                os.remove(t)
            results.append({"path": rp, "ok": True, "msg": "已删除"})
        except Exception as e:
            results.append({"path": rp, "ok": False, "msg": str(e)})
    return {"ok": True, "results": results}


def rename_output_file(root_dir, rel_path, new_name):
    """重命名文件/文件夹"""
    t = _safe_path(root_dir, rel_path)
    if not t or not os.path.exists(t):
        return {"ok": False, "msg": "路径无效或不存在"}
    safe_name = os.path.basename(new_name)
    if not safe_name or safe_name in (".", ".."):
        return {"ok": False, "msg": "无效的文件名"}
    new_t = os.path.join(os.path.dirname(t), safe_name)
    if os.path.exists(new_t):
        return {"ok": False, "msg": "目标已存在: " + safe_name}
    try:
        os.rename(t, new_t)
        return {"ok": True, "msg": "已重命名为 " + safe_name}
    except Exception as e:
        return {"ok": False, "msg": str(e)}


def move_output_files(root_dir, rel_paths, dest_rel):
    """批量移动文件/文件夹到目标目录"""
    dest = _safe_path(root_dir, dest_rel)
    if not dest or not os.path.isdir(dest):
        return {"ok": False, "msg": "目标目录无效: " + (dest_rel or "(root)")}
    results = []
    for rp in rel_paths:
        t = _safe_path(root_dir, rp)
        if not t or not os.path.exists(t):
            results.append({"path": rp, "ok": False, "msg": "源路径无效"})
            continue
        try:
            shutil.move(t, dest)
            results.append({"path": rp, "ok": True, "msg": "已移动"})
        except Exception as e:
            results.append({"path": rp, "ok": False, "msg": str(e)})
    return {"ok": True, "results": results}


def copy_output_files(root_dir, rel_paths, dest_rel):
    """批量复制文件/文件夹到目标目录"""
    dest = _safe_path(root_dir, dest_rel)
    if not dest:
        return {"ok": False, "msg": "目标目录无效: " + (dest_rel or "(root)")}
    os.makedirs(dest, exist_ok=True)
    results = []
    for rp in rel_paths:
        t = _safe_path(root_dir, rp)
        if not t or not os.path.exists(t):
            results.append({"path": rp, "ok": False, "msg": "源路径无效"})
            continue
        try:
            if os.path.isdir(t):
                d = os.path.join(dest, os.path.basename(t))
                if os.path.exists(d):
                    shutil.rmtree(d)
                shutil.copytree(t, d)
            else:
                shutil.copy2(t, dest)
            results.append({"path": rp, "ok": True, "msg": "已复制"})
        except Exception as e:
            results.append({"path": rp, "ok": False, "msg": str(e)})
    return {"ok": True, "results": results}


def send_to_input_dir(output_root, rel_paths, input_root):
    """将输出目录的文件/文件夹复制到输入目录（跨根目录复制）"""
    if not os.path.isdir(input_root):
        os.makedirs(input_root, exist_ok=True)
    results = []
    for rp in rel_paths:
        src = _safe_path(output_root, rp)
        if not src or not os.path.exists(src):
            results.append({"path": rp, "ok": False, "msg": "源路径无效"})
            continue
        # 目标: input_root / basename
        dst = os.path.join(input_root, os.path.basename(src))
        try:
            if os.path.isdir(src):
                if os.path.exists(dst):
                    shutil.rmtree(dst)
                shutil.copytree(src, dst)
            else:
                os.makedirs(input_root, exist_ok=True)
                shutil.copy2(src, dst)
            results.append({"path": rp, "ok": True, "msg": "已发送"})
        except Exception as e:
            results.append({"path": rp, "ok": False, "msg": str(e)})
    return {"ok": True, "results": results}


# ============================================================
# 工作流管理 — 文件浏览
# ============================================================

# 工作流允许的文件扩展名（JSON 及文本类）
_WF_EXTS = {".json", ".txt", ".md", ".png"}


def _workflow_type(name):
    """工作流文件类型：json / text / image / other"""
    ext = os.path.splitext(name)[1].lower()
    if ext == ".json":
        return "json"
    if ext in (".txt", ".md"):
        return "text"
    if ext in _IMG_EXTS:
        return "image"
    return "other"


def workflows_browse(root_dir, rel_path="", page=1, page_size=20,
                     search="", sort="date_desc"):
    """浏览工作流目录：文件夹在前、文件在后，支持分页/搜索/排序。
    与 browse_output_dir 类似，但不过滤非媒体文件（保留全部类型）。
    带 search 时执行跨文件夹模糊搜索。"""
    if search and search.strip():
        return recursive_search(root_dir, search.strip(), sort, page, page_size, _workflow_type)
    target = _safe_path(root_dir, rel_path)
    if not target:
        return {"ok": False, "msg": "无效路径"}
    if not os.path.isdir(target):
        return {"ok": False, "msg": "目录不存在"}

    # 面包屑
    breadcrumbs = []
    try:
        rel_norm = os.path.relpath(target, root_dir)
    except ValueError:
        rel_norm = ""
    if rel_norm == ".":
        rel_norm = ""
    parts = rel_norm.replace("\\", "/").split("/") if rel_norm else []
    acc = ""
    for p in parts:
        if not p:
            continue
        acc = os.path.join(acc, p) if acc else p
        breadcrumbs.append({"name": p, "path": acc.replace("\\", "/")})

    try:
        entries = os.listdir(target)
    except OSError:
        return {"ok": False, "msg": "无法读取目录"}

    folders_raw = []
    files_raw = []
    for name in entries:
        full = os.path.join(target, name)
        if os.path.isdir(full):
            child_rel = os.path.join(rel_path, name) if rel_path else name
            try:
                st = os.stat(full)
                folders_raw.append({
                    "name": name, "path": child_rel.replace("\\", "/"),
                    "modified": st.st_mtime, "is_dir": True,
                })
            except OSError:
                pass
        elif os.path.isfile(full):
            ft = _workflow_type(name)
            if ft == "other":
                continue
            try:
                st = os.stat(full)
                files_raw.append({
                    "name": name,
                    "path": (os.path.join(rel_path, name).replace("\\", "/") if rel_path else name),
                    "type": ft, "size": st.st_size,
                    "modified": st.st_mtime, "is_dir": False,
                })
            except OSError:
                pass

    # 搜索
    if search:
        q = search.lower()
        folders_raw = [f for f in folders_raw if q in f["name"].lower()]
        files_raw = [f for f in files_raw if q in f["name"].lower()]

    # 排序
    rev = sort.endswith("_desc")
    if sort.startswith("name"):
        folders_raw.sort(key=lambda x: x["name"].lower(), reverse=rev)
        files_raw.sort(key=lambda x: x["name"].lower(), reverse=rev)
    elif sort.startswith("date"):
        folders_raw.sort(key=lambda x: x["modified"], reverse=rev)
        files_raw.sort(key=lambda x: x["modified"], reverse=rev)
    else:
        folders_raw.sort(key=lambda x: x["name"].lower())
        files_raw.sort(key=lambda x: x["name"].lower())

    # 分页
    total = len(folders_raw) + len(files_raw)
    ps = max(1, min(100, int(page_size)))
    tp = max(1, (total + ps - 1) // ps) if total else 1
    pg = max(1, min(tp, int(page)))
    start = (pg - 1) * ps
    end = start + ps
    all_items = folders_raw + files_raw
    paged = all_items[start:end]

    return {
        "ok": True,
        "current_path": rel_norm.replace("\\", "/") if rel_norm else "",
        "breadcrumbs": breadcrumbs,
        "parent_path": os.path.dirname(rel_norm).replace("\\", "/") if rel_norm else None,
        "items": paged,
        "page": pg, "page_size": ps, "total": total, "total_pages": tp,
        "search": search, "sort": sort,
    }


def workflows_get(root_dir, rel_path):
    """读取工作流文件内容（文本形式）"""
    t = _safe_path(root_dir, rel_path)
    if not t or not os.path.isfile(t):
        return {"ok": False, "msg": "文件不存在或路径无效"}
    try:
        with open(t, "r", encoding="utf-8") as f:
            content = f.read()
        return {"ok": True, "name": os.path.basename(t), "path": rel_path, "content": content}
    except Exception as e:
        return {"ok": False, "msg": "读取失败: " + str(e)}


def workflows_save(root_dir, rel_path, content):
    """保存工作流文件（新建或覆盖）。返回 {ok, path, msg}。"""
    t = _safe_path(root_dir, rel_path)
    if not t:
        return {"ok": False, "msg": "无效路径"}
    os.makedirs(os.path.dirname(t), exist_ok=True)
    try:
        with open(t, "w", encoding="utf-8") as f:
            f.write(content)
        return {"ok": True, "path": rel_path.replace("\\", "/"), "msg": "已保存"}
    except Exception as e:
        return {"ok": False, "msg": "保存失败: " + str(e)}


def workflows_new(root_dir, name):
    """在根目录新建空白工作流 JSON 文件"""
    safe_name = os.path.basename(name)
    if not safe_name:
        return {"ok": False, "msg": "无效的文件名"}
    if not safe_name.lower().endswith(".json"):
        safe_name += ".json"
    t = os.path.join(root_dir, safe_name)
    if os.path.exists(t):
        return {"ok": False, "msg": "文件已存在: " + safe_name}
    try:
        with open(t, "w", encoding="utf-8") as f:
            f.write('{\n  "nodes": [],\n  "links": [],\n  "version": 0.4\n}\n')
        return {"ok": True, "name": safe_name, "path": safe_name, "msg": "已创建"}
    except Exception as e:
        return {"ok": False, "msg": "创建失败: " + str(e)}


def workflows_move_files(root_dir, rel_paths, dest_rel):
    """批量移动工作流文件/文件夹到目标目录"""
    dest = _safe_path(root_dir, dest_rel)
    if not dest or not os.path.isdir(dest):
        return {"ok": False, "msg": "目标目录无效: " + (dest_rel or "(root)")}
    results = []
    for rp in rel_paths:
        t = _safe_path(root_dir, rp)
        if not t or not os.path.exists(t):
            results.append({"path": rp, "ok": False, "msg": "源路径无效"})
            continue
        try:
            shutil.move(t, dest)
            results.append({"path": rp, "ok": True, "msg": "已移动"})
        except Exception as e:
            results.append({"path": rp, "ok": False, "msg": str(e)})
    return {"ok": True, "results": results}


def workflows_copy_files(root_dir, rel_paths, dest_rel):
    """批量复制工作流文件/文件夹到目标目录"""
    dest = _safe_path(root_dir, dest_rel)
    if not dest:
        return {"ok": False, "msg": "目标目录无效: " + (dest_rel or "(root)")}
    os.makedirs(dest, exist_ok=True)
    results = []
    for rp in rel_paths:
        t = _safe_path(root_dir, rp)
        if not t or not os.path.exists(t):
            results.append({"path": rp, "ok": False, "msg": "源路径无效"})
            continue
        try:
            if os.path.isdir(t):
                d = os.path.join(dest, os.path.basename(t))
                if os.path.exists(d):
                    shutil.rmtree(d)
                shutil.copytree(t, d)
            else:
                shutil.copy2(t, dest)
            results.append({"path": rp, "ok": True, "msg": "已复制"})
        except Exception as e:
            results.append({"path": rp, "ok": False, "msg": str(e)})
    return {"ok": True, "results": results}


def workflows_backup(root_dir, backup_dir):
    """将整个工作流目录复制到指定备份目录下的「年月日时分秒-备份」文件夹"""
    if not os.path.isdir(root_dir):
        return {"ok": False, "msg": "工作流目录不存在: " + root_dir}
    if not backup_dir or not backup_dir.strip():
        return {"ok": False, "msg": "未配置备份目录"}
    backup_dir = backup_dir.strip()
    # 防御：备份目录不能位于工作流目录内部（否则 copytree 会无限递归）
    try:
        if os.path.commonpath([os.path.abspath(root_dir), os.path.abspath(backup_dir)]) == os.path.abspath(root_dir):
            return {"ok": False, "msg": "备份目录不能位于工作流目录内部，请改用工作流目录之外的路径"}
    except ValueError:
        pass  # 不同盘符等无法比较的情况，交由后续处理
    try:
        os.makedirs(backup_dir, exist_ok=True)
    except Exception as e:
        return {"ok": False, "msg": "无法创建备份目录: " + str(e)}
    # 时间戳：年月日时分秒
    stamp = time.strftime("%Y%m%d%H%M%S")
    dest = os.path.join(backup_dir, stamp + "-备份")
    if os.path.exists(dest):
        # 罕见冲突，追加毫秒
        dest = os.path.join(backup_dir, stamp + "-" + str(int(time.time() * 1000) % 1000) + "-备份")
    try:
        shutil.copytree(root_dir, dest)
        return {"ok": True, "dest": dest, "msg": "备份完成"}
    except Exception as e:
        return {"ok": False, "msg": "备份失败: " + str(e)}


def serve_workflow_file(root_dir, rel_path):
    """安全返回工作流文件绝对路径（供 send_file 下载使用）"""
    target = _safe_path(root_dir, rel_path)
    if not target or not os.path.isfile(target):
        return None
    return target




def workflows_analyze(root_dir, rel_path):
    """解析工作流 JSON，提取模型文件名（.safetensors/.pth/.ckpt/.gguf/.onnx 等）。

    遍历 nodes[].widgets_values 及 nodes[].inputs 中的默认值，
    找出所有看起来像模型文件名的字符串（带常见模型扩展名）。
    返回 {ok, name, models: [{file, ext, node, node_type}]}。
    """
    t = _safe_path(root_dir, rel_path)
    if not t or not os.path.isfile(t):
        return {"ok": False, "msg": "文件不存在或路径无效"}
    try:
        with open(t, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        return {"ok": False, "msg": "读取失败: " + str(e)}

    # 尝试解析 JSON
    try:
        data = json.loads(content)
    except Exception:
        # 非 JSON 文件（.txt/.md/.png）：用正则兜底扫描
        return _workflows_analyze_text(content, rel_path)

    nodes = data.get("nodes") if isinstance(data, dict) else None
    if not isinstance(nodes, list):
        return {"ok": False, "msg": "工作流 JSON 结构无效（缺少 nodes 数组）"}

    # 常见模型扩展名
    model_exts = (".safetensors", ".pth", ".pt", ".ckpt", ".gguf", ".onnx",
                  ".bin", ".sft", ".pt2", ".vae.pt", ".vae.safetensors",
                  ".safetensors.bak", ".pth.tar", ".tar", ".zip", ".json")
    # 按扩展名排序，长的优先匹配（如 .safetensors 在 .safetensors.bak 之前）
    model_exts = tuple(sorted(model_exts, key=len, reverse=True))

    found = []
    seen = set()

    def add_model(fname, node_name, node_type):
        if not fname or not isinstance(fname, str):
            return
        fname = fname.strip()
        if not fname:
            return
        low = fname.lower()
        if not any(low.endswith(e) for e in model_exts):
            return
        if fname in seen:
            return
        seen.add(fname)
        found.append({
            "file": fname,
            "ext": os.path.splitext(fname)[1].lower(),
            "node": node_name or "",
            "node_type": node_type or "",
        })

    for node in nodes:
        if not isinstance(node, dict):
            continue
        ntype = node.get("type", "")
        nname = node.get("title", "") or node.get("type", "")
        # widgets_values：常含 ckpt_name / lora_name 等模型选择值
        wv = node.get("widgets_values")
        if isinstance(wv, list):
            for v in wv:
                if isinstance(v, str):
                    add_model(v, nname, ntype)
                elif isinstance(v, list):
                    for sv in v:
                        if isinstance(sv, str):
                            add_model(sv, nname, ntype)
        # inputs 中的 widget 默认值（部分节点结构）
        for inp in node.get("inputs") or []:
            if not isinstance(inp, dict):
                continue
            w = inp.get("widget")
            if isinstance(w, dict) and w.get("name") and inp.get("value") is not None:
                v = inp["value"]
                if isinstance(v, str):
                    add_model(v, nname, ntype)
            if inp.get("value") and isinstance(inp["value"], str):
                add_model(inp["value"], nname, ntype)

    # 去重后排序（保持出现顺序）
    return {
        "ok": True,
        "name": os.path.basename(t),
        "path": rel_path,
        "models": found,
        "count": len(found),
        "type": "json",
    }


def _workflows_analyze_text(content, rel_path):
    """非 JSON 工作流（.txt/.md/.png）用正则提取模型文件名"""
    model_exts = (".safetensors", ".pth", ".pt", ".ckpt", ".gguf", ".onnx",
                  ".bin", ".sft", ".pt2", ".vae.pt", ".vae.safetensors")
    model_exts = tuple(sorted(model_exts, key=len, reverse=True))
    ext_alt = "|".join(re.escape(e.lstrip(".")) for e in model_exts)
    # 文件名正则：允许中文、字母、数字、_ - . 空格、路径分隔符
    pat = re.compile(
        r"[A-Za-z0-9_\-\u4e00-\u9fff /\\.]+\.(?:" + ext_alt + r")\b",
        re.I,
    )
    found = []
    seen = set()
    for m in pat.finditer(content):
        fname = m.group(0).strip()
        base = fname.replace("\\", "/").split("/")[-1].strip()
        if not base or base in seen:
            continue
        seen.add(base)
        found.append({"file": base, "ext": os.path.splitext(base)[1].lower(),
                      "node": "", "node_type": ""})
    return {
        "ok": True,
        "name": os.path.basename(rel_path),
        "path": rel_path,
        "models": found,
        "count": len(found),
        "type": "text",
    }



# 抖音页面请求通用 headers（_DOUYIN_HEADERS）
_DOUYIN_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://www.douyin.com/",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

def _douyin_extract_links(text):
    return re.findall(r"(https?://[^\s]+)", text)

def _douyin_extract_note_id(url):
    m = re.search(r"https?://(?:www\.)?douyin\.com/note/([^/?#]+)", url, re.I)
    return m.group(1) if m else None

def _douyin_extract_aweme_id(url):
    for p in [
        r"https?://(?:www\.)?douyin\.com/video/([^/?#]+)",
        r"https?://(?:www\.)?douyin\.com/share/video/([^/?#]+)",
        r"https?://(?:www\.)?iesdouyin\.com/share/video/([^/?#]+)",
    ]:
        m = re.search(p, url, re.I)
        if m:
            return m.group(1)
    return None

def _douyin_extract_balanced_arr(text, start):
    import json as _json
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == '\\':
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == '[':
                depth += 1
            elif ch == ']':
                depth -= 1
                if depth == 0:
                    raw = text[start:i+1].replace('\\"', '"')
                    try:
                        return _json.loads(raw)
                    except (ValueError, _json.JSONDecodeError):
                        return None
    return None

def _douyin_dedup_urls(urls):
    seen = set()
    unique = []
    for u in urls:
        m = re.search(r'douyinpic\.com/(tos-cn-[^~]+~[^.?]+)', u)
        key = m.group(1) if m else u
        if key not in seen:
            seen.add(key)
            unique.append(u)
    return unique

def _douyin_extract_urls_by_key(html, keys):
    import json as _json
    for key in keys:
        ek = re.escape(key)
        for pat in [rf'"{ek}"\s*:\s*\[', re.escape(f'\\"{key}\\"\\:\\[').replace('\\\\\\\\', '\\\\')]:
            for m in re.finditer(pat, html):
                arr = _douyin_extract_balanced_arr(html, m.end() - 1)
                if isinstance(arr, list):
                    urls = [u.rstrip('\\') for u in arr if isinstance(u, str) and 'douyinpic.com' in u]
                    if urls:
                        return urls
    return None

def douyin_extract_image_urls(html: str):
    """从抖音页面 HTML 提取图片 URL，返回去重后的列表。"""
    # 1. downloadUrlList
    dl = _douyin_extract_urls_by_key(html, ["downloadUrlList"])
    if dl:
        return _douyin_dedup_urls(dl)
    # 2. urlList
    ul = _douyin_extract_urls_by_key(html, ["urlList"])
    if ul:
        aw = [u for u in ul if 'aweme_images' in u]
        return _douyin_dedup_urls(aw or ul)
    # 3. Regex fallback
    fb = re.findall(
        r'https?://(?:p\d+-)?pc(?:-sign)?\.douyinpic\.com/[^"\s]+?~tplv-dy-aweme-images:q75\.(?:jpe?g|webp)[^"\s]*',
        html, re.I
    )
    if fb:
        fb = [u.rstrip('\\').replace('\\u0026', '&') for u in fb]
        return _douyin_dedup_urls(fb)
    return []

def douyin_load_cookie(cookie_file: str):
    """加载 Cookie 文件（支持 JSON 或原始字符串），返回 requests.Session。"""
    import json as _json, requests as _requests
    with open(cookie_file, 'r', encoding='utf-8') as f:
        raw_content = f.read().strip()
    sess = _requests.Session()
    items = []
    # 尝试 JSON 格式
    try:
        data = _json.loads(raw_content)
    except (ValueError, _json.JSONDecodeError):
        # 非 JSON → 原始 Cookie 字符串
        sess.cookies = _requests.utils.cookiejar_from_dict(
            {k.strip(): v.strip() for k, _, v in (p.partition('=') for p in raw_content.split(';') if '=' in p)})
        return sess
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        if isinstance(data.get("cookie"), str):
            sess.cookies = _requests.utils.cookiejar_from_dict(
                {k.strip(): v.strip() for k, _, v in (p.partition('=') for p in data["cookie"].split(';') if '=' in p)})
            return sess
        elif isinstance(data.get("cookies"), list):
            items = data["cookies"]
    for item in items:
        if not isinstance(item, dict):
            continue
        n = item.get("name")
        v = item.get("value")
        if not n or v is None:
            continue
        sess.cookies.set(n, v,
            domain=item.get("domain", ""),
            path=item.get("path", "/"),
            secure=bool(item.get("secure", False)))
    return sess


def douyin_get_cookie_string(cookie_file: str) -> str:
    """从 cookie 文件提取原始 Cookie: header 字符串（用于 aria2 --header）。"""
    import json as _json
    with open(cookie_file, 'r', encoding='utf-8') as f:
        raw = f.read().strip()
    # 尝试 JSON
    try:
        data = _json.loads(raw)
    except (ValueError, _json.JSONDecodeError):
        return raw
    if isinstance(data, dict):
        if isinstance(data.get("cookie"), str):
            return data["cookie"]
        if isinstance(data.get("cookies"), list):
            return "; ".join(f"{c['name']}={c['value']}" for c in data["cookies"] if c.get("name"))
    if isinstance(data, list):
        return "; ".join(f"{c['name']}={c['value']}" for c in data if c.get("name"))
    return raw


def _douyin_aria2_headers(cookie_file: str = ""):
    """构建 aria2 --header 参数列表：逗号分隔的 name: 值（aria2 JSON-RPC header 格式）。"""
    hdrs = [
        "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Referer: https://www.douyin.com/",
    ]
    if cookie_file:
        try:
            cookie_str = douyin_get_cookie_string(cookie_file)
            if cookie_str:
                hdrs.append(f"Cookie: {cookie_str}")
        except Exception:
            pass
    return hdrs

def douyin_fetch(raw_text_or_url: str, cookie_file: str):
    """抓取抖音页面，支持多个链接并发抓取。
    返回 {ok, results: [{target_url, aweme_id, note_id, desc, type, video_url, cover_url, image_urls, image_count}], msg?}。
    单链接时 results 数组仍然返回一个元素。"""
    import json as _json, requests as _requests
    from concurrent.futures import ThreadPoolExecutor, as_completed
    # Step 1: 先提取所有链接
    links = _douyin_extract_links(raw_text_or_url)
    if not links:
        return {"ok": False, "msg": "未检测到有效链接，请粘贴包含 https:// 的抖音地址"}

    # 加载 session（所有请求复用一个）
    try:
        sess = douyin_load_cookie(cookie_file)
    except Exception as e:
        return {"ok": False, "msg": f"Cookie 加载失败: {e}"}

    def _fetch_one(url: str):
        """抓取单个链接，返回结果字典。"""
        try:
            resp = sess.get(url, headers=_DOUYIN_HEADERS, allow_redirects=True, timeout=(10, 30))
            resp.raise_for_status()
            final_url = resp.url
            html = resp.text
        except Exception as e:
            return {"target_url": url, "type": "error", "msg": f"页面请求失败: {e}"}

        aweme_id = _douyin_extract_aweme_id(final_url)
        if not aweme_id:
            m = re.search(r'"aweme_id":"([^"]+)"', html)
            if m:
                aweme_id = m.group(1)

        note_id = _douyin_extract_note_id(final_url)
        desc = None
        cover_url = None
        video_url = None
        image_urls = []

        # 优先尝试 API
        if aweme_id:
            try:
                api = "https://www.douyin.com/aweme/v1/web/aweme/detail/"
                params = {"aweme_id": aweme_id, "device_platform": "web", "aid": "6383", "channel": "channel_pc_web"}
                hdrs = {**_DOUYIN_HEADERS, "X-Requested-With": "XMLHttpRequest"}
                ar = sess.get(api, params=params, headers=hdrs, timeout=(10, 30))
                ar.raise_for_status()
                ad = ar.json()
                detail = ad.get("aweme_detail") or {}
                desc = detail.get("desc")
                cover_info = detail.get("cover") or {}
                if isinstance(cover_info, dict) and cover_info.get("url_list"):
                    cover_url = cover_info["url_list"][0]
                video_info = (detail.get("video") or {}).get("play_addr") or {}
                vurls = video_info.get("url_list") or []
                if vurls:
                    video_url = vurls[0]
            except Exception:
                pass

        if not video_url:
            image_urls = douyin_extract_image_urls(html)

        return {
            "target_url": url,
            "aweme_id": aweme_id,
            "note_id": note_id,
            "desc": desc,
            "type": "video" if video_url else ("note" if image_urls else "unknown"),
            "video_url": video_url,
            "cover_url": cover_url,
            "image_urls": image_urls,
            "image_count": len(image_urls),
        }

    # 并发抓取所有链接
    results = []
    max_workers = min(len(links), 5)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {executor.submit(_fetch_one, url): url for url in links}
        for future in as_completed(future_map):
            try:
                results.append(future.result())
            except Exception as e:
                results.append({"target_url": future_map[future], "type": "error", "msg": str(e)})

    # 按原始链接顺序排列
    url_order = {url: i for i, url in enumerate(links)}
    results.sort(key=lambda r: url_order.get(r.get("target_url", ""), 999))

    return {"ok": True, "results": results, "total": len(results)}

def douyin_download_images(urls: list, save_dir: str, name_template: str = "{index:02d}.{ext}", cookie_file: str = ""):
    """通过 aria2c 批量下载图片。返回 {ok, gids, save_dir}。"""
    os.makedirs(save_dir, exist_ok=True)
    gids = []
    total = len(urls)
    header = _douyin_aria2_headers(cookie_file)
    for idx, url in enumerate(urls, start=1):
        # 生成文件名
        uri_part = "img"
        m = re.search(r'/([^/?]+?)(?:\.(?:jpe?g|png|webp)[^"\\s]*|$)', url, re.I)
        if m:
            uri_part = m.group(1)[:40]
        name = name_template.replace("{index:02d}", f"{idx:02d}").replace("{index:03d}", f"{idx:03d}")\
            .replace("{index}", str(idx)).replace("{uri}", uri_part).replace("{ext}", "png")\
            .replace("{datetime}", time.strftime("%Y%m%d_%H%M%S")).replace("{date}", time.strftime("%Y%m%d"))\
            .replace("{time}", time.strftime("%H%M%S"))
        out_file = os.path.join(save_dir, name)
        opts = {"dir": save_dir, "out": name, "continue": "true"}
        if header:
            opts["header"] = header
        r, e = aria2_rpc("aria2.addUri", [[url], opts])
        if e:
            print(f"  [WARN] image {idx}/{total} aria2 addUri failed: {e}")
            continue
        gids.append({"idx": idx, "url": url, "gid": r, "out": out_file})
    return {"ok": True, "gids": gids, "total": total, "added": len(gids), "save_dir": save_dir}


def douyin_download_video(video_url: str, save_dir: str, name: str = "video.mp4", cookie_file: str = ""):
    """通过 aria2c 下载视频文件。返回 {ok, gid, out}。"""
    os.makedirs(save_dir, exist_ok=True)
    opts = {"dir": save_dir, "out": name, "continue": "true"}
    header = _douyin_aria2_headers(cookie_file)
    if header:
        opts["header"] = header
    r, e = aria2_rpc("aria2.addUri", [[video_url], opts])
    if e:
        return {"ok": False, "msg": f"aria2 添加视频任务失败: {e}"}
    return {"ok": True, "gid": r, "out": os.path.join(save_dir, name)}


def douyin_download_status(gid):
    """查询 aria2 单个任务的下载进度"""
    r, e = aria2_rpc("aria2.tellStatus", [gid])
    if e or not r:
        return {"status": "error", "msg": e or "未知", "progress": 0, "total": 0, "completed": 0, "speed": 0}
    t = int(r.get("totalLength", 0))
    c = int(r.get("completedLength", 0))
    sp = int(r.get("downloadSpeed", 0))
    pct = round((c / t * 100), 1) if t > 0 else 0
    return {"status": r.get("status", "?"), "progress": pct, "total": t, "completed": c, "speed": sp, "name": r.get("files", [{}])[0].get("path", "") if r.get("files") else ""}

def create_output_dir(root_dir, rel_path):
    """创建新目录"""
    t = _safe_path(root_dir, rel_path)
    if not t:
        return {"ok": False, "msg": "无效路径"}
    if os.path.exists(t):
        return {"ok": False, "msg": "目录已存在"}
    try:
        os.makedirs(t)
        return {"ok": True, "msg": "目录已创建"}
    except Exception as e:
        return {"ok": False, "msg": str(e)}


def upload_output_files(root_dir, dest_rel, files):
    """上传单个或多个文件到输出目录"""
    dest = _safe_path(root_dir, dest_rel)
    if not dest or not os.path.isdir(dest):
        return {"ok": False, "msg": "目标目录无效"}
    results = []
    for f in files:
        if not f.filename:
            continue
        fn = os.path.basename(f.filename)
        tgt = os.path.join(dest, fn)
        try:
            f.save(tgt)
            results.append({"name": fn, "ok": True, "msg": "已上传"})
        except Exception as e:
            results.append({"name": fn, "ok": False, "msg": str(e)})
    return {"ok": True, "results": results}


def cleanup_all():
    """跨平台：退出时清理所有子进程"""
    try:
        global repair_proc, comfyui_proc, aria2_proc
        for p in [repair_proc, comfyui_proc, aria2_proc]:
            if p and hasattr(p, "poll") and p.poll() is None:
                try:
                    if hasattr(p, "_stop_flag"):
                        p._stop_flag.set()  # 多步代理
                    else:
                        p.kill()
                except Exception:
                    pass
        repair_proc = None
        comfyui_proc = None
        aria2_proc = None
    except Exception:
        pass


# ========== 关机功能 ==========
_shutdown_timer = None
_shutdown_lock = threading.Lock()


def schedule_shutdown(delay):
    """定时关机：delay 秒后执行系统关机。返回 {ok, msg}。"""
    global _shutdown_timer
    delay = max(0, min(int(delay), 3600))
    with _shutdown_lock:
        if _shutdown_timer is not None:
            _shutdown_timer.cancel()
            _shutdown_timer = None

    def _do():
        global _shutdown_timer
        with _shutdown_lock:
            _shutdown_timer = None
        try:
            if config.IS_WINDOWS:
                subprocess.run(["shutdown", "/s", "/t", "0"], timeout=10)
            else:
                subprocess.run(["shutdown", "-h", "now"], timeout=10)
        except Exception:
            pass

    t = threading.Timer(delay, _do)
    t.daemon = True
    with _shutdown_lock:
        _shutdown_timer = t
    t.start()
    return {"ok": True, "msg": f"已计划 {delay} 秒后关机"}


def cancel_shutdown():
    """取消已计划的定时关机。返回 {ok, msg}。"""
    global _shutdown_timer
    with _shutdown_lock:
        if _shutdown_timer is None:
            return {"ok": False, "msg": "没有待执行的关机任务"}
        _shutdown_timer.cancel()
        _shutdown_timer = None
    return {"ok": True, "msg": "已取消关机"}
