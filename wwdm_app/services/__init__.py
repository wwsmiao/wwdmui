import os, re, sys, io, time, json, shutil, tempfile, threading, subprocess, ctypes, configparser, zipfile
import logging
from ..config import (settings, DEFAULT_SETTINGS, IS_WINDOWS, IS_LINUX, BASE_DIR,
                     ARIA2_RPC_PORT, ARIA2_RPC_SECRET, _DEFAULT_ARIA2C, COMFYUI_DIR,
                     get_comfyui_dir, get_git_path)   # 访问器实现在 core/config.py
from ..database import db_get, db_get_many, plugin_get, plugin_get_many
from ..core import tasks as task_bus        # 任务总线：后台活儿统一上报

# ---- 日志 ----
# 里程碑（开始 / 完成 / 失败 / 清理）走 logging：控制台和 logs/wwdm.log 都能看到；
# 每秒刷一次的下载进度条走 _cli()：只打控制台，免得把日志文件刷爆。
log = logging.getLogger("wwdm.services")

# ---- 状态变量 ----
aria2_proc = None
download_gids = {}
download_mirrors = {}
console_threads = {}
download_queue = []
queue_lock = threading.Lock()

BAR_WIDTH = 28


def _cli(msg="", end="\n", flush=False):
    """只往控制台输出（进度条这类高频内容不进日志文件）"""
    try:
        print(msg, end=end, flush=flush)
    except Exception:
        pass


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


# get_comfyui_dir() / get_git_path() 已上移到 core/config.py（单一实现，
# 见文件顶部 import）；sqlite 层 core/db.py 也要用，而 core 不能反向依赖 services。


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


# ---- GitHub 镜像（全局设置） ----
# group: 官方 / 镜像仓库(替换 github.com 域名) / 加速网站(前缀加速)
# prefix: 用于替换 "https://github.com/" 的字符串，空串 = 官方直连
GITHUB_MIRRORS = [
    {"key": "official",   "label": "GitHub 官方",         "group": "官方",     "prefix": ""},
    {"key": "gitclone",   "label": "gitclone.com",        "group": "镜像仓库", "prefix": "https://gitclone.com/github.com/"},
    {"key": "ur1",        "label": "github.ur1.fun",      "group": "镜像仓库", "prefix": "https://github.ur1.fun/"},
    {"key": "ghproxy",    "label": "gh-proxy.com",        "group": "加速网站", "prefix": "https://gh-proxy.com/https://github.com/"},
    {"key": "ghproxynet", "label": "ghproxy.net",         "group": "加速网站", "prefix": "https://ghproxy.net/https://github.com/"},
    {"key": "homeboyc",   "label": "ghproxy.homeboyc.cn", "group": "加速网站", "prefix": "https://ghproxy.homeboyc.cn/https://github.com/"},
    {"key": "akams",      "label": "github.akams.cn",     "group": "加速网站", "prefix": "https://github.akams.cn/https://github.com/"},
]
_GITHUB_MIRROR_MAP = {m["key"]: m for m in GITHUB_MIRRORS}
# 连通性探测目标：极小仓库的 git smart-http 端点（等价于 git clone 的第一步）
GITHUB_PROBE_URL = "https://github.com/octocat/Hello-World.git/info/refs?service=git-upload-pack"


def github_mirror_prefix(key=None):
    """当前 GitHub 镜像用于替换 "https://github.com/" 的前缀；官方/未知返回空串"""
    if key is None:
        key = settings.get("github_mirror", "official")
    m = _GITHUB_MIRROR_MAP.get(key)
    return (m or {}).get("prefix", "") or ""


def github_mirror_label(key=None):
    if key is None:
        key = settings.get("github_mirror", "official")
    m = _GITHUB_MIRROR_MAP.get(key)
    return m["label"] if m else "GitHub 官方"


def apply_github_mirror(url, key=None):
    """按 GitHub 镜像设置改写单个 URL（仅处理 github.com 链接）"""
    p = github_mirror_prefix(key)
    if not p or "https://github.com/" not in url:
        return url
    return url.replace("https://github.com/", p, 1)


def convert_git_mirror(url):
    """插件克隆用：按全局 GitHub 镜像设置转换仓库地址"""
    if "github.com" not in url:
        return url
    url = url.rstrip("/")
    if not url.endswith(".git"):
        url += ".git"
    return apply_github_mirror(url)


def test_github_mirrors(timeout=8):
    """并发探测各 GitHub 镜像的 git 可用性（git-upload-pack 端点），返回连通性与延迟"""
    import requests
    from concurrent.futures import ThreadPoolExecutor
    try:
        requests.packages.urllib3.disable_warnings()
    except Exception:
        pass

    def probe(m):
        url = apply_github_mirror(GITHUB_PROBE_URL, m["key"])
        t0 = time.time()
        item = {"key": m["key"], "label": m["label"], "group": m["group"],
                "prefix": m["prefix"], "url": url, "ms": 0,
                "http": 0, "status": "fail", "msg": ""}
        try:
            # 诊断用途的只读探测：部分镜像站点证书不规范，故不做证书校验
            r = requests.get(url, timeout=(4, timeout), allow_redirects=True,
                             verify=False, headers={"User-Agent": "git/2.43.0"})
            item["ms"] = int((time.time() - t0) * 1000)
            item["http"] = r.status_code
            if r.status_code == 200 and b"git-upload-pack" in r.content[:600]:
                if item["ms"] <= 3000:
                    item["status"], item["msg"] = "ok", "可用"
                else:
                    item["status"], item["msg"] = "slow", "可用（较慢）"
            else:
                item["msg"] = "HTTP %d" % r.status_code
        except requests.exceptions.Timeout:
            item["ms"] = int((time.time() - t0) * 1000)
            item["msg"] = "连接超时"
        except requests.exceptions.SSLError:
            item["ms"] = int((time.time() - t0) * 1000)
            item["msg"] = "SSL 证书错误"
        except Exception as e:
            item["ms"] = int((time.time() - t0) * 1000)
            item["msg"] = ("连接失败: " + str(e))[:90]
        return item

    try:
        with ThreadPoolExecutor(max_workers=len(GITHUB_MIRRORS)) as ex:
            results = list(ex.map(probe, GITHUB_MIRRORS))
    except Exception as e:
        return {"ok": False, "msg": "测试失败: " + str(e), "results": []}
    return {"ok": True, "current": settings.get("github_mirror", "official"),
            "results": results}


# ---- 下载状态缓存（RPC 失败时返回最后已知状态） ----
_dl_cache = {}       # rid → {status, progress, total, completed, speed}
_dl_cache_lock = threading.Lock()


# 本机回环调用一律绕过代理：用户机器上常设 HTTP_PROXY/HTTPS_PROXY，
# 而 requests 默认会连 127.0.0.1 也送去代理 —— 实测那样 /object_info 直接 502、
# aria2 RPC 也连不上。显式把该 scheme 的代理置 None 才是直连。
_NO_PROXY = {"http": None, "https": None}


# ---- aria2c RPC（含重试 + 指数退避） ----
def aria2_rpc(method, params=None, retries=3, base_delay=0.5):
    import requests
    url = "http://127.0.0.1:" + str(ARIA2_RPC_PORT) + "/jsonrpc"
    payload = {"jsonrpc": "2.0", "id": "1", "method": method,
               "params": ["token:" + ARIA2_RPC_SECRET] + (params or [])}
    last_err = None
    for attempt in range(retries):
        try:
            r = requests.post(url, json=payload, timeout=5,
                               proxies=_NO_PROXY)
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
            log.info("[aria2c] 已启动 PID=%s v%s", aria2_proc.pid, vstr)
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
        log.error("[下载失败] %s: %s", m["name"], e)
        return False, e
    download_gids[rid] = r
    download_mirrors[rid] = use_mirror
    t = threading.Thread(target=_console_progress_monitor, args=(rid, r, m["name"]), daemon=True)
    console_threads[rid] = t
    t.start()
    tag = " [镜像]" if use_mirror else ""
    log.info("[下载%s] %s gid=%s", tag, m["name"], r)
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
                log.error("[下载中断] %s：aria2 RPC 连续出错", name)
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
        _cli(msg, end="", flush=True)
        if s in ("complete", "error", "removed"):
            _cli("")
            if s == "complete":
                log.info("[下载完成] %s (%s)", name, fmt_size(t))
            elif s == "error":
                log.error("[下载出错] %s", name)
            elif s == "removed":
                log.warning("[下载已取消] %s", name)
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
    log.info("[下载排队] %s (位置%d)", m["name"], pos)
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
    # 前端每 2s 轮询一次本接口：原来每个任务查一次库、每个队列项还查两次，
    # 改成一次 IN 查询取回全部名字，N 个任务从 2N 次建连降到 1 次
    rid_list = list(download_gids.keys())
    names = db_get_many(rid_list + list(download_queue))
    al = []
    for rid in rid_list:
        st = get_download_status(rid)
        if st:
            m = names.get(rid) or {}
            al.append({"id": rid, "name": m.get("name") or "#" + str(rid),
                       "save_path": m.get("save_path", ""), **st})
    qu = []
    for i, rid in enumerate(download_queue):
        m = names.get(rid) or {}
        qu.append({"id": rid, "name": m.get("name") or "#" + str(rid),
                   "save_path": m.get("save_path", ""),
                   "mirror": download_mirrors.get(rid, False),
                   "queue_position": i + 1})
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
    from .. import config
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
repair_child_proc = None   # 当前正在执行的子进程（供「停止操作」强制结束进程树）
repair_logs = []
repair_log_lock = threading.Lock()
repair_action = ""


def _kill_tree(proc):
    """强制结束进程及其全部子进程（Windows 用 taskkill /F /T）"""
    if proc is None:
        return
    try:
        if IS_WINDOWS:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            proc.terminate()
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass



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


def _start_repair_proc(cmd, cwd, action_name, header_lines=None):
    global repair_proc, repair_logs, repair_action
    if repair_proc and repair_proc.poll() is None:
        return {"ok": False, "msg": "已有操作" + repair_action + " 正在运行，请先停止"}
    with repair_log_lock:
        repair_logs = list(header_lines) if header_lines else []
        repair_logs.append("$ " + _fmt_cmd(cmd))
    repair_action = action_name
    cf = subprocess.CREATE_NO_WINDOW if IS_WINDOWS else 0
    repair_proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
        text=True, encoding="gbk", errors="replace", creationflags=cf)
    threading.Thread(target=_repair_reader, args=(repair_proc,), daemon=True).start()
    return {"ok": True, "msg": action_name + " 已启动", "pid": repair_proc.pid}


def _fmt_cmd(cmd):
    """把命令列表还原成可读命令行写入日志，便于核对「显示的就是执行的」；URL 里的凭据打码"""
    try:
        parts = []
        for a in cmd:
            a = str(a)
            parts.append(('"%s"' % a) if (" " in a or "\t" in a) else a)
        s = " ".join(parts)
    except Exception:
        return "(命令解析失败)"
    return re.sub(r"://[^/@\s]+@", "://***@", s)


def _repair_run_steps(steps, action_name, header_lines=None):
    """跨平台多步修复：在后台线程中依次执行命令，输出写入 repair_logs
    steps: [(label, cmd_list, cwd), ...]
    header_lines: 可选，写入日志开头的说明行（如使用的镜像）
    完成后自动置 repair_proc=None"""
    global repair_proc, repair_logs, repair_action
    if repair_proc and repair_proc.poll() is None:
        return {"ok": False, "msg": "已有操作" + repair_action + " 正在运行，请先停止"}
    with repair_log_lock:
        repair_logs = list(header_lines) if header_lines else []
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
        global repair_child_proc, repair_proc, repair_action
        cf = subprocess.CREATE_NO_WINDOW if IS_WINDOWS else 0
        stopped = False
        for label, cmd, cwd in steps:
            if stop_flag.is_set():
                stopped = True
                break
            with repair_log_lock:
                repair_logs.append("")
                repair_logs.append(f"--- {label} ---")
                repair_logs.append("$ " + _fmt_cmd(cmd))
            try:
                proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                    text=True, encoding="gbk", errors="replace", creationflags=cf)
                repair_child_proc = proc
                for line in proc.stdout:
                    if stop_flag.is_set():
                        stopped = True
                        _kill_tree(proc)
                        break
                    with repair_log_lock:
                        repair_logs.append(_strip_ansi(line.rstrip()))
                proc.wait()
                repair_child_proc = None
                rc = proc.returncode
                with repair_log_lock:
                    repair_logs.append(f"--- {label} 完成 (exit={rc}) ---")
            except Exception as e:
                repair_child_proc = None
                with repair_log_lock:
                    repair_logs.append(f"!!! {label} 异常: {e}")
            if stopped:
                break
        repair_child_proc = None
        with repair_log_lock:
            repair_logs.append("")
            if stopped:
                repair_logs.append("!!! 操作已被用户停止")
                repair_logs.append(f"===== {action_name} 已停止 =====")
            else:
                repair_logs.append(f"===== {action_name} 全部完成 =====")
        if repair_proc is proxy:
            repair_proc = None
            repair_action = ""

    threading.Thread(target=_run, daemon=True).start()
    return {"ok": True, "msg": action_name + " 已启动", "pid": 0}


def repair_clone(git_path, target_dir, mirror=None):
    """克隆 ComfyUI。git 镜像统一取自「全局设置 → GitHub 镜像」
    （mirror 参数已废弃，仅为兼容保留，传什么都以全局设置为准）
    """
    if os.path.exists(os.path.join(target_dir, "main.py")):
        return {"ok": False, "msg": "ComfyUI 已存在于 " + target_dir + "，无需克隆"}
    _base = "https://github.com/Comfy-Org/ComfyUI"
    info = repair_mirror_info()
    if info["github_prefix"]:
        url = apply_github_mirror(_base + ".git")
        tag = " [GitHub镜像: %s]" % info["github_label"]
    else:
        url = _base
        tag = " [GitHub: 官方直连]"
    os.makedirs(target_dir, exist_ok=True)
    header = _op_header("克隆 ComfyUI", git_path=git_path,
                        extra=["仓库地址    : %s" % url, "目标目录    : %s" % target_dir])
    return _start_repair_proc([git_path, "clone", url, target_dir],
                              os.path.dirname(target_dir), "克隆 ComfyUI" + tag,
                              header_lines=header)


# ---- ComfyUI 仓库分支 / tag（v4.1：本地查询同步出，git fetch 挪后台）----
# 为什么改：git fetch --all --tags 在慢网络下要几十秒（timeout=45），原来是**在请求
# 线程里**跑的 —— 用户一切到「修复安装」页就挂着一个转很久的下拉框，而且慢请求会一直
# 占着连接。现在拆成两半：
#   * 本地 ref（HEAD / branch -r / tag）照旧同步读，毫秒~1 秒级 → 下拉框立刻有内容
#   * 远端刷新丢给后台线程；响应体带 refreshing=True，前端轮询到 False 再定稿
# 对老前端零影响：ok / current / branches / tags 四个键语义不变，新增键纯附加。
_branches_cache = {}                 # comfyui_dir -> 快照 dict
_branches_fetching = set()           # 正在后台 fetch 的 comfyui_dir
_branches_lock = threading.Lock()
_repo_git_lock = threading.Lock()    # 同一仓库的 git 写操作串行
BRANCHES_TTL = 600.0                 # 快照新鲜期（秒）：新鲜就直接返回，不惊动网络
BRANCHES_RETRY_AFTER = 20.0          # 刷新失败后的重试间隔（秒），免得反复打网络
BRANCHES_FETCH_TIMEOUT = 45          # 单次 git fetch 上限（秒）
_BRANCHES_SKIP_MSG = "修复任务正在运行，本次跳过远端刷新"


def _git_env():
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "Never"
    return env


def _git_run(git_path, argv, cwd, timeout=45):
    """跑一条 git 命令，返回 (stdout, returncode)。永不抛异常。"""
    cf = subprocess.CREATE_NO_WINDOW if IS_WINDOWS else 0
    try:
        p = subprocess.run([git_path] + list(argv), cwd=cwd, capture_output=True,
                           text=True, encoding="gbk", errors="replace",
                           creationflags=cf, env=_git_env(), timeout=timeout)
        return (p.stdout or "").strip(), p.returncode
    except Exception as e:
        return str(e), 1


def _branches_snapshot(git_path, comfyui_dir):
    """只读本地仓库（不联网）：当前分支 / 远端分支 / tag

    远端分支与 tag 用**一次** ``for-each-ref`` 一起拿：原来 ``branch -r`` +
    ``tag`` 是两条命令、两次 spawn，Windows 上每次 spawn 上百毫秒 —— 冷缓存首次
    进「修复安装」页时这一段是同步跑的，能省一半就省一半。
    ``--sort=-creatordate`` 保证 tag 仍是「最新在前」；分支另外按名字排序，与旧实现
    ``git branch -r`` 的字母序输出保持一致（下拉框顺序不变）。
    """
    cur_out, _ = _git_run(git_path, ["rev-parse", "--abbrev-ref", "HEAD"],
                          comfyui_dir, timeout=15)
    current = cur_out if cur_out else "unknown"
    ref_out, _ = _git_run(git_path, ["for-each-ref", "--sort=-creatordate",
                                     "--format=%(refname)",
                                     "refs/remotes/origin", "refs/tags"],
                          comfyui_dir, timeout=15)
    branches, tags = [], []
    for ref in ref_out.split("\n"):
        ref = ref.strip()
        if ref.startswith("refs/remotes/origin/"):
            name = ref[len("refs/remotes/origin/"):]
            if name and name != "HEAD" and name not in branches:
                branches.append(name)
        elif ref.startswith("refs/tags/"):
            name = ref[len("refs/tags/"):]
            if name and len(tags) < 50:
                tags.append(name)
    branches.sort()
    return {"ok": True, "current": current, "branches": branches, "tags": tags,
            "fetched_at": 0.0, "error": "", "retry_after": 0.0}


def _repair_busy():
    """修复流程（git pull / checkout）是否正在跑"""
    try:
        return repair_proc is not None and repair_proc.poll() is None
    except Exception:
        return False


def _branches_fetch_worker(git_path, comfyui_dir):
    """后台线程：git fetch --all --tags，完了重新读一遍本地 ref。

    ⚠️ 这是裸线程，拿不到 Flask 上下文 —— 不要去碰 request / g / trace_id。
    只做 subprocess + 模块级字典。
    """
    # 任务总线上报（v5.0 试点：先接这一条，验证总线设计）。
    # ⚠️ 这一步必须包 try：万一抛出去，下面的 _branches_fetching 就永远不会被
    # discard，/api/repair/comfyui_branches 会一直显示 refreshing=True 卡住。
    tid = task_bus.make_id(task_bus.KIND_BRANCHES, comfyui_dir)
    try:
        task_bus.upsert(tid, task_bus.KIND_BRANCHES, title="刷新 ComfyUI 远端分支",
                        status=task_bus.RUNNING, message=comfyui_dir,
                        detail={"dir": comfyui_dir})
    except Exception:
        tid = ""

    err = ""
    skipped = False
    try:
        if _repair_busy():
            # 修复流程正在 git pull / checkout，这时再 fetch 会两边抢 ref 锁
            skipped = True
            err = _BRANCHES_SKIP_MSG
        else:
            with _repo_git_lock:
                if _repair_busy():
                    skipped = True
                    err = _BRANCHES_SKIP_MSG
                else:
                    out, code = _git_run(git_path, ["fetch", "--all", "--tags"],
                                         comfyui_dir, timeout=BRANCHES_FETCH_TIMEOUT)
                    if code != 0:
                        lines = [x for x in (out or "").splitlines() if x.strip()]
                        err = (lines[-1] if lines else "git fetch 失败")[:300]
    except Exception as e:
        err = "%s: %s" % (type(e).__name__, e)

    try:
        snap = _branches_snapshot(git_path, comfyui_dir)
    except Exception as e:
        snap = None
        err = err or ("读取本地 ref 失败: %s" % e)

    with _branches_lock:
        _branches_fetching.discard(comfyui_dir)
        if snap is not None:
            old = _branches_cache.get(comfyui_dir) or {}
            if err:
                # 没拉成：保留上次成功时间，排一个重试时间，别让前端无限催
                snap["fetched_at"] = old.get("fetched_at", 0.0)
                snap["retry_after"] = time.time() + BRANCHES_RETRY_AFTER
            else:
                snap["fetched_at"] = time.time()
                snap["retry_after"] = 0.0
            snap["error"] = err
            _branches_cache[comfyui_dir] = snap
    if err:
        log.info("[分支] 远端刷新未完成(%s): %s", comfyui_dir, err)

    # 任务总线收尾（被修复任务挡住算「取消」，不算失败）
    if tid:
        try:
            if skipped:
                task_bus.cancel(tid, err)
            elif err:
                task_bus.fail(tid, err)
            else:
                task_bus.finish(tid, "已刷新远端分支", detail={
                    "dir": comfyui_dir,
                    "branches": len((snap or {}).get("branches") or []),
                    "tags": len((snap or {}).get("tags") or []),
                })
        except Exception:
            pass


def _branches_payload(snap, key):
    with _branches_lock:
        refreshing = key in _branches_fetching
    fetched = float((snap or {}).get("fetched_at") or 0.0)
    return {
        "ok": True,
        "current": (snap or {}).get("current") or "unknown",
        "branches": list((snap or {}).get("branches") or []),
        "tags": list((snap or {}).get("tags") or []),
        "refreshing": refreshing,
        "cached": fetched > 0,
        "fetched_at": fetched,
        "stale": (time.time() - fetched) >= BRANCHES_TTL,
        "error": (snap or {}).get("error") or "",
    }


def _branches_kick(git_path, comfyui_dir):
    """起后台线程拉远端 ref；同一目录同时只允许一个"""
    with _branches_lock:
        if comfyui_dir in _branches_fetching:
            return False
        _branches_fetching.add(comfyui_dir)
    try:
        threading.Thread(target=_branches_fetch_worker, args=(git_path, comfyui_dir),
                         name="wwdm-branches-fetch", daemon=True).start()
    except Exception as e:
        with _branches_lock:
            _branches_fetching.discard(comfyui_dir)
        log.warning("[分支] 后台刷新线程起不来: %s", e)
        return False
    return True


def get_comfyui_branches(git_path, comfyui_dir, wait=0.0, force=False):
    """获取 ComfyUI 仓库的分支 / tag。

    **立即返回**：本地 ref 同步读一次（第一次调用时会建快照），远端刷新在后台跑。
    响应体里 refreshing=True 表示后台还在拉，前端继续轮询同一个地址即可。

    :param wait:  调用方愿意就地等后台刷新多少秒（默认 0 = 完全不等）
    :param force: 忽略 TTL，强制提交一次远端刷新
    """
    if not os.path.isdir(os.path.join(comfyui_dir, ".git")):
        return {"ok": False, "msg": "ComfyUI 目录不是 Git 仓库: " + comfyui_dir}
    key = os.path.abspath(comfyui_dir)
    now = time.time()

    with _branches_lock:
        snap = _branches_cache.get(key)
        fetching = key in _branches_fetching
    fetched = float((snap or {}).get("fetched_at") or 0.0)
    fresh = fetched > 0 and (now - fetched) < BRANCHES_TTL

    if snap is None:
        # 第一次：先同步读一遍本地 ref，让下拉框立刻有东西，不用等网络
        try:
            snap = _branches_snapshot(git_path, key)
        except Exception as e:
            return {"ok": False, "msg": "读取分支失败: %s" % e}
        with _branches_lock:
            _branches_cache[key] = snap

    retry_ok = now >= float((snap or {}).get("retry_after") or 0.0)
    if (force or not fresh) and retry_ok and not fetching and not _repair_busy():
        _branches_kick(git_path, key)
        if wait:
            deadline = time.time() + max(0.0, float(wait))
            while time.time() < deadline:
                with _branches_lock:
                    if key not in _branches_fetching:
                        break
                    snap = _branches_cache.get(key) or snap
                time.sleep(0.2)

    with _branches_lock:
        snap = _branches_cache.get(key) or snap
    return _branches_payload(snap, key)


def comfyui_git_idle(timeout=15.0):
    """等后台的分支刷新整个收工（最多 timeout 秒）。

    用户点「更新到最新版」前先调它：后台的 git fetch 与 git pull 会同时写 refs，
    与其让用户在日志里看到 "cannot lock ref"，不如等几秒。

    判据是 ``_branches_fetching`` 清空，而不是「拿到 _repo_git_lock」—— 后者只能
    证明 fetch 那一段结束了，之后后台线程还要读一遍本地 ref 才会收尾；用前者才是
    「这个仓库上已经没有任何后台 git 活动」。
    返回 True = 已经空闲；False = 等到超时还在忙（调用方可照常继续，只是有极小的
    抢锁概率，用户会从日志里看到 git 自己的报错）。
    """
    deadline = time.time() + max(0.0, float(timeout))
    while True:
        with _branches_lock:
            busy = bool(_branches_fetching)
        if not busy:
            return True
        if time.time() >= deadline:
            return False
        time.sleep(0.2)


def repair_mirror_info():
    """当前生效的 GitHub / pip 镜像信息（供前端与日志展示）"""
    gk = settings.get("github_mirror", "official")
    pk = settings.get("pip_mirror", "default")
    eff_url = _PIP_MIRRORS.get(pk) or _PIP_MIRRORS["default"]
    amb = _detect_ambient_pip_index()
    conflict = bool(amb["index"]) and _norm_index_url(amb["index"]) != _norm_index_url(eff_url)
    return {
        "ok": True,
        "github_mirror": gk,
        "github_label": github_mirror_label(gk),
        "github_prefix": github_mirror_prefix(gk),
        "pip_mirror": pk,
        "pip_label": PIP_MIRROR_LABELS.get(pk) or (pk or "default"),
        "pip_url": eff_url,
        "pip_mirrors": dict(_PIP_MIRRORS),
        "pip_ambient_url": amb["index"],
        "pip_ambient_extra": amb["extra"],
        "pip_ambient_source": amb["source"],
        "pip_env_conflict": conflict,
        "git_path": settings.get("git_path", "git") or "git",
        "python_path": settings.get("python_path") or DEFAULT_SETTINGS["python_path"],
        "comfyui_dir": settings.get("comfyui_dir") or COMFYUI_DIR,
        "aria2c_path": settings.get("aria2c_path") or _DEFAULT_ARIA2C,
    }


def _op_header(title, git_path=None, python_path=None, extra=None):
    """构造修复操作日志头部：明确打印本次使用的 git / pip 路径与镜像来源"""
    info = repair_mirror_info()
    lines = ["===== %s =====" % title]
    if git_path:
        lines.append("git 路径    : %s" % (git_path or "git"))
        lines.append("GitHub 镜像 : %s%s" % (
            info["github_label"],
            ("  (" + info["github_prefix"] + ")") if info["github_prefix"] else "  (直连 github.com)"))
    if python_path:
        lines.append("pip 路径    : %s" % (python_path or ""))
        lines.append("pip 镜像源  : %s%s" % (
            info["pip_label"],
            ("  (" + info["pip_url"] + ")") if info["pip_url"] else "  (官方 pypi.org)"))
        if info.get("pip_env_conflict"):
            lines.append("提示        : 本机已有 pip 镜像配置（%s）指向 %s，实际执行时已被上面的设置覆盖" % (
                info.get("pip_ambient_source") or "?", info.get("pip_ambient_url") or "?"))
    if extra:
        lines.extend(extra)
    lines.append("")
    return lines


def _gh_git_args(git_path, info=None):
    """返回 (命令前缀, 标签)：带 GitHub 镜像时附加 git insteadOf 重写"""
    info = info or repair_mirror_info()
    prefix = info["github_prefix"]
    cmd = [git_path]
    if prefix:
        cmd += ["-c", "url.%s.insteadOf=https://github.com/" % prefix]
        return cmd, (" [GitHub镜像: %s]" % info["github_label"])
    return cmd, " [GitHub: 官方直连]"


def repair_comfyui_update(git_path, comfyui_dir, target):
    """更新 ComfyUI：
    - git 更新（pull / fetch+checkout）走「全局设置 → GitHub 镜像」
    - 更新完成后用「全局设置 → pip 镜像源」安装 ComfyUI 根目录的 requirements.txt
    - target 为空则只 pull 当前分支；否则 fetch 后 checkout target 再 pull
    """
    if not os.path.isdir(os.path.join(comfyui_dir, ".git")):
        return {"ok": False, "msg": "ComfyUI 目录不是 Git 仓库: " + comfyui_dir}
    target = (target or "").strip()

    info = repair_mirror_info()
    gh_prefix, gh_label = info["github_prefix"], info["github_label"]
    pip_label, pip_url = info["pip_label"], info["pip_url"]

    # 镜像通过 git insteadOf 重写 https://github.com/ 前缀（镜像仓库 / 加速网站都适用）
    git_cmd = [git_path]
    if gh_prefix:
        git_cmd += ["-c", "url.%s.insteadOf=https://github.com/" % gh_prefix]
    gh_tag = (" [GitHub镜像: %s]" % gh_label) if gh_prefix else " [GitHub: 官方直连]"

    base_label = "更新 ComfyUI 到最新版" if not target else ("更新 ComfyUI → %s" % target)
    label = base_label + gh_tag

    header = _op_header(base_label, git_path=git_path, python_path=settings.get("python_path"),
                        extra=["ComfyUI目录: %s" % comfyui_dir])
    if not gh_prefix:
        header.append("提示: 当前为 GitHub 官方直连；如需加速，可在「全局设置 → GitHub 镜像」中切换。")
        header.append("")

    steps = []
    if target:
        steps.append(("获取远端分支/标签 (git fetch)" + gh_tag,
                      git_cmd + ["fetch", "--all", "--tags"], comfyui_dir))
        steps.append(("切换到 %s (git checkout)" % target, git_cmd + ["checkout", target], comfyui_dir))
    steps.append(("拉取最新代码 (git pull)" + gh_tag, git_cmd + ["pull"], comfyui_dir))

    req = os.path.join(comfyui_dir, "requirements.txt")
    if os.path.isfile(req):
        python_path = settings.get("python_path") or DEFAULT_SETTINGS["python_path"]
        steps.append(("安装 requirements.txt 依赖 (pip install -r) [pip: %s]" % pip_label,
                      _pip_install_cmd(python_path, ["-r", req]), comfyui_dir))
    else:
        header.append("未找到 requirements.txt，跳过依赖安装: " + req)

    return _repair_run_steps(steps, label, header_lines=header)


def repair_requirements(python_path, comfyui_dir):
    """按全局 pip 镜像安装 ComfyUI 根目录下的 requirements.txt"""
    req = os.path.join(comfyui_dir, "requirements.txt")
    if not os.path.exists(req):
        return {"ok": False, "msg": "未找到 requirements.txt: " + req}
    info = repair_mirror_info()
    tag = " [pip: %s]" % info["pip_label"]
    header = _op_header("安装 ComfyUI requirements.txt", python_path=python_path,
                        extra=["依赖文件    : %s" % req])
    return _repair_run_steps(
        [("pip install -r requirements.txt" + tag,
          _pip_install_cmd(python_path, ["-r", req]), comfyui_dir)],
        "安装 requirements.txt" + tag, header_lines=header)


def repair_pytorch(python_path, cmd_str):
    if not cmd_str or not cmd_str.startswith("pip install"):
        return {"ok": False, "msg": "无效的安装命令"}
    parts = cmd_str.split()
    if parts[0] == "pip":
        parts = [python_path, "-m", "pip"] + parts[1:]
    header = _op_header("安装 PyTorch", python_path=python_path,
                        extra=["安装命令    : %s" % cmd_str,
                               "安装源      : 由上面命令自带的 --index-url 决定（PyTorch 专用源）"])
    return _repair_run_steps([("安装 PyTorch", parts, os.path.dirname(python_path))],
                             "安装 PyTorch", header_lines=header)


def repair_stop():
    """停止当前页面正在运行的所有操作：pip 依赖安装 / git 安装 / aria2c 下载
    多步任务（代理对象）置停止标志并强杀当前子进程树；单步任务直接强杀进程树。
    """
    global repair_proc, repair_action, repair_child_proc
    if not repair_proc or repair_proc.poll() is not None:
        repair_proc = None
        repair_action = ""
        return {"ok": False, "msg": "没有正在运行的操作"}
    a = repair_action or "操作"
    is_multi = hasattr(repair_proc, "_stop_flag")
    if is_multi:
        # 多步代理：置停止标志，执行线程会结束当前子进程并退出
        repair_proc._stop_flag.set()
        _kill_tree(repair_child_proc)
    else:
        # 单步子进程：直接强杀
        _kill_tree(repair_proc)
        try:
            repair_proc.wait(timeout=5)
        except Exception:
            pass
    repair_proc = None
    repair_action = ""
    repair_child_proc = None
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
# pip 镜像中文名（前端展示 / 日志用）
PIP_MIRROR_LABELS = {
    "default": "国外官方 (pypi.org)",
    "tsinghua": "清华大学 (tuna.tsinghua.edu.cn)",
    "aliyun": "阿里云 (mirrors.aliyun.com)",
    "tencent": "腾讯云 (mirrors.cloud.tencent.com)",
    "douban": "豆瓣 (pypi.douban.com)",
    "ustc": "中科大 (pypi.mirrors.ustc.edu.cn)",
}

_PIP_MIRRORS = {
    # 官方源也必须显式给出 URL：旧实现留空串表示"不加 -i"，结果 pip 会回落到
    # pip.ini / PIP_INDEX_URL 里配置的第三方源（本机 %APPDATA%\pip\pip.ini 就指向清华），
    # 造成「界面显示国外官方、实际却走清华」的展示与行为不一致。
    # 已实测：命令行 -i 的优先级高于配置文件与环境变量，显式传 -i 即可完全掌控。
    "default": "https://pypi.org/simple",
    "tsinghua": "https://pypi.tuna.tsinghua.edu.cn/simple",
    "aliyun": "https://mirrors.aliyun.com/pypi/simple/",
    "tencent": "https://mirrors.cloud.tencent.com/pypi/simple",
    "douban": "http://pypi.douban.com/simple/",
    "ustc": "https://pypi.mirrors.ustc.edu.cn/simple",
}


def _norm_index_url(u):
    """归一化 index-url 用于比较（忽略大小写与结尾斜杠）"""
    return (u or "").strip().lower().rstrip("/")


def _pip_config_files():
    """按 pip 的查找顺序返回 [(作用域说明, 路径)]，用于读取系统 pip 配置里的 index-url。

    作用域说明是给界面/日志展示的**通用文案**，故意不含本机绝对路径——
    软件会装在不同人的电脑上，`C:\\Users\\xxx\\...` 这种路径对别人毫无意义。
    路径本身只在本函数内部用于读文件，不对外输出。
    """
    files = []
    py = settings.get("python_path") or DEFAULT_SETTINGS.get("python_path") or ""
    if py:
        d = os.path.dirname(py)
        files.append(("Python 安装目录的 pip 配置", os.path.join(d, "pip.ini")))
        files.append(("Python 安装目录的 pip 配置", os.path.join(d, "pip.conf")))
    home = os.path.expanduser("~")
    if IS_WINDOWS:
        appdata = os.environ.get("APPDATA") or ""
        progdata = os.environ.get("PROGRAMDATA") or ""
        if appdata:
            files.append(("用户级 pip 配置", os.path.join(appdata, "pip", "pip.ini")))
        files.append(("用户级 pip 配置", os.path.join(home, ".pip", "pip.ini")))
        if progdata:
            files.append(("全局 pip 配置", os.path.join(progdata, "pip", "pip.ini")))
    else:
        files.append(("用户级 pip 配置", os.path.join(home, ".config", "pip", "pip.conf")))
        files.append(("用户级 pip 配置", os.path.join(home, ".pip", "pip.conf")))
        files.append(("全局 pip 配置", "/etc/pip.conf"))
        files.append(("全局 pip 配置", "/etc/xdg/pip/pip.conf"))
    return files


def _read_pip_cfg(path):
    """读取 pip 配置文件的 [global] / [install] 段，键名统一小写；失败返回 {}"""
    out = {}
    try:
        cp = configparser.ConfigParser()
        with io.open(path, "r", encoding="utf-8-sig", errors="replace") as f:
            cp.read_file(f)
    except Exception:
        return out
    for sec in cp.sections():
        if sec.strip().lower() not in ("global", "install"):
            continue
        try:
            items = cp.items(sec)
        except Exception:
            continue
        for k, v in items:
            out[(k or "").strip().lower()] = (v or "").strip()
    return out


def _detect_ambient_pip_index():
    """探测「系统 pip」在本工具不传 -i 时会使用的 index-url（环境变量 > 配置文件）。
    仅用于提示本工具设置与系统 pip 配置是否一致；返回 {index, extra, source}。"""
    env_idx = (os.environ.get("PIP_INDEX_URL") or "").strip()
    env_extra = (os.environ.get("PIP_EXTRA_INDEX_URL") or "").strip()
    if env_idx:
        return {"index": env_idx, "extra": env_extra, "source": "环境变量 PIP_INDEX_URL"}
    for scope, p in _pip_config_files():
        if not p or not os.path.isfile(p):
            continue
        cfg = _read_pip_cfg(p)
        idx = cfg.get("index-url", "")
        if idx:
            # source 只回传通用作用域文案，不回传本机绝对路径
            return {"index": idx, "extra": cfg.get("extra-index-url", ""), "source": scope}
    return {"index": "", "extra": "", "source": ""}


def _pip_install_cmd(python_path, packages, cwd=None):
    """构造 pip install 命令：镜像取自全局设置并用 -i 显式指定，
    避免 pip 回落到 pip.ini / PIP_INDEX_URL 里的其它源，保证「显示的就是执行的」。"""
    mirror = settings.get("pip_mirror", "default")
    mirror_url = _PIP_MIRRORS.get(mirror) or _PIP_MIRRORS["default"]
    cmd = [python_path, "-m", "pip", "install", "--disable-pip-version-check"]
    if isinstance(packages, list):
        cmd += packages
    else:
        cmd.append(packages)
    if mirror_url:
        cmd += ["-i", mirror_url, "--trusted-host", mirror_url.split("/")[2]]
    return cmd




def repair_pip_install(python_path, packages, mirror=None):
    """安装 pip 包。镜像统一取自「全局设置 → pip 镜像源」（mirror 参数已废弃，仅为兼容保留）"""
    pkgs = [p.strip() for p in packages.split() if p.strip()]
    if not pkgs:
        return {"ok": False, "msg": "Please fill dependency package names"}
    cmd = _pip_install_cmd(python_path, pkgs)
    info = repair_mirror_info()
    tag = " [pip: %s]" % info["pip_label"]
    label = "pip install " + " ".join(pkgs) + tag
    header = _op_header("安装 pip 依赖", python_path=python_path,
                        extra=["安装包      : %s" % " ".join(pkgs)])
    return _repair_run_steps([(label, cmd, os.path.dirname(python_path))],
                            "安装依赖 " + " ".join(pkgs), header_lines=header)


def repair_download_file(aria2c_path, url, save_path, filename=None):
    """使用 aria2c 下载文件。文件名称 / 下载链接 / 保存路径 三项都必须手动填写，
    不再使用默认保存路径。
    """
    url = (url or "").strip()
    save_path = (save_path or "").strip()
    filename = (filename or "").strip()
    if not filename:
        return {"ok": False, "msg": "请填写文件名称"}
    if not url:
        return {"ok": False, "msg": "请填写下载链接"}
    if not save_path:
        return {"ok": False, "msg": "请填写保存路径"}
    if os.sep in filename or "/" in filename or "\\" in filename:
        return {"ok": False, "msg": "文件名称不能包含路径分隔符"}
    try:
        os.makedirs(save_path, exist_ok=True)
    except Exception as e:
        return {"ok": False, "msg": "保存路径无法创建：%s" % e}
    header = ["===== 下载文件 %s =====" % filename,
              "文件名称    : %s" % filename,
              "下载链接    : %s" % url,
              "保存路径    : %s" % save_path,
              "aria2c 路径 : %s" % aria2c_path,
              ""]
    return _repair_run_steps(
        [("下载 " + filename,
          [aria2c_path, url, "-d", save_path, "-o", filename], save_path)],
        "下载文件 " + filename, header_lines=header
    )


def repair_single_plugin(git_path, python_path, comfyui_dir, plugin_name, mirror=None):
    """跨平台：修复单个插件 — git pull + pip install -r requirements.txt
    镜像统一取自「全局设置」：git 用 GitHub 镜像，pip 用 pip 镜像源
    （mirror 参数已废弃，仅为兼容保留，传什么都以全局设置为准）
    """
    cn_dir = os.path.join(comfyui_dir, "custom_nodes")
    plugin_dir = os.path.join(cn_dir, plugin_name)
    if not os.path.isdir(plugin_dir):
        return {"ok": False, "msg": f"插件目录不存在: {plugin_dir}"}
    if not os.path.isdir(os.path.join(plugin_dir, ".git")):
        return {"ok": False, "msg": f"不是 Git 仓库: {plugin_dir}"}

    info = repair_mirror_info()
    git_base, gh_tag = _gh_git_args(git_path, info)
    pip_tag = "[pip: %s]" % info["pip_label"]

    req_file = os.path.join(plugin_dir, "requirements.txt")
    steps = [("git pull" + gh_tag, git_base + ["pull"], plugin_dir)]
    if os.path.isfile(req_file):
        steps.append(("安装 requirements.txt (pip install -r) " + pip_tag,
                      _pip_install_cmd(python_path, ["-r", req_file]), plugin_dir))

    title = "修复插件 %s" % plugin_name
    header = _op_header(title, git_path=git_path, python_path=python_path,
                        extra=["插件目录    : %s" % plugin_dir])
    if not os.path.isfile(req_file):
        header.append("未找到 requirements.txt，跳过依赖安装")
        header.append("")
    return _repair_run_steps(steps, title + gh_tag, header_lines=header)


def repair_all_plugins(git_path, python_path, comfyui_dir, mirror=None):
    """跨平台：扫描 custom_nodes，逐个 git pull + pip install
    镜像统一取自「全局设置」：git 用 GitHub 镜像，pip 用 pip 镜像源
    （mirror 参数已废弃，仅为兼容保留，传什么都以全局设置为准）
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

    info = repair_mirror_info()
    git_base, gh_tag = _gh_git_args(git_path, info)
    pip_tag = "[pip: %s]" % info["pip_label"]

    steps = []
    for i, pn in enumerate(plugins, 1):
        pd = os.path.join(cn_dir, pn)
        steps.append((("[%d/%d] %s — git pull" % (i, len(plugins), pn)) + gh_tag,
                      git_base + ["pull"], pd))
        req = os.path.join(pd, "requirements.txt")
        if os.path.isfile(req):
            steps.append(("[%d/%d] %s — 安装依赖 (pip install -r) %s" % (i, len(plugins), pn, pip_tag),
                          _pip_install_cmd(python_path, ["-r", req]), pd))

    title = "批量修复 %d 个插件" % len(plugins)
    shown = ", ".join(plugins[:12]) + (" ..." if len(plugins) > 12 else "")
    header = _op_header(title, git_path=git_path, python_path=python_path,
                        extra=["插件数量    : %d" % len(plugins), "插件列表    : %s" % shown])
    return _repair_run_steps(steps, title + gh_tag, header_lines=header)


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


def _safe_rel(rel_path):
    """把前端传来的相对路径规范化成安全形式；非法一律返回 None。

    比「先拼好再 commonpath」更早、更明确地拦下这些：
      * ``..`` 段（路径穿越）—— 含 Windows 会当成 ``..`` 处理的 ``.. `` / ``.. .``
      * 前导 ``/`` 或 ``\\``（绝对路径 / UNC）
      * 任意段里出现 ``:``（盘符 / 驱动器相对路径 / NTFS 数据流 ``a.txt:ads``）
      * NUL 字节（Windows 的 abspath 不拒绝它，会一路漏到 open() 才炸成 500）

    合法时返回以 ``/`` 分隔的相对路径；空串表示「根本身」。
    """
    r = str(rel_path or "").replace("\\", "/")
    if not r:
        return ""
    if "\x00" in r:
        return None
    if r.startswith("/"):
        return None
    parts = []
    for seg in r.split("/"):
        if seg in ("", "."):
            continue
        # Windows 的文件名规则里，分量末尾的空格与点会被忽略，所以先按该口径归一：
        #   ".." / ".. " / ".. ." / "... " 全部归一成空串（= 不表达任何层级），
        #   而 "a." → "a"、"a..b" 不受影响。
        # 归一后剩下空串的分量（纯点/纯空格）内部会整个丢掉它，正常文件名也不可能
        # 长成这样，所以直接判非法 —— 比「先放行、指望后面 commonpath 兜住」清楚。
        if not seg.rstrip(" ."):
            return None
        if ":" in seg:
            return None
        parts.append(seg)
    return "/".join(parts)


def _is_inside(root, target):
    """target 是否落在 root 内。

    跨盘符 / UNC 与本地盘混用时 ``os.path.commonpath`` 会抛
    ``ValueError: Paths don't have the same drive``。旧实现让它直接冒到全局
    错误处理器，于是 ``GET /api/output/browse?path=D:\\`` 这种请求变成 500
    —— 局域网里谁都打得出来。这里统一吞成 False（= 越界）。
    """
    try:
        return os.path.commonpath([root, target]) == root
    except ValueError:
        return False


def _safe_path(root_dir, rel_path):
    """防御路径穿越：把 rel_path 解析到 root_dir 内；越界 / 非法一律返回 None。

    这是全项目**唯一**的相对路径校验入口 —— ``_local_abs``（本地模型/插件那套）、
    ``_save_upload_batch``（上传落盘）都走它，所以新增路由只要调用带路径参数的
    服务函数就自动受保护，不必再各写一份 commonpath。

    关于软链接（特意不处理）
    ------------------------
    这里**刻意不做 realpath**：「模型目录用目录联接（junction）指到别的盘」是本
    项目常见且受支持的部署方式，realpath 之后父目录与目标的盘符会不一致，会把正常
    目录误判成越界。而「在受管目录里放一个指向外部的链接」这条绕行，前提是攻击者
    已经能在受管目录里创建链接 —— 那已经等价于本机文件系统写权限，再拦这一层没有
    额外收益，却会实打实地弄坏正常部署。
    """
    root = os.path.abspath(root_dir)
    r = _safe_rel(rel_path)
    if r is None:
        return None
    target = os.path.abspath(os.path.join(root, r.replace("/", os.sep)))
    if not _is_inside(root, target):
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


class _ZipSink:
    """zipfile 的输出漏斗：把写进来的字节攒成块，交给生成器逐块 yield。

    故意不实现 seek/tell：zipfile 会自动降级成「不可 seek」写法（用数据描述符记录
    每个条目的长度），这正是流式 zip 需要的行为。
    """

    def __init__(self):
        self.chunks = []

    def write(self, b):
        if b:
            self.chunks.append(bytes(b))
        return len(b)

    def flush(self):
        pass

    def drain(self):
        out = self.chunks
        self.chunks = []
        return out


def zip_collect(root_dir, rel_paths):
    """收集要打包的 (绝对路径, zip 内相对路径) 列表"""
    items = []
    for rp in rel_paths:
        t = _safe_path(root_dir, rp)
        if not t or not os.path.exists(t):
            continue
        if os.path.isdir(t):
            for dirpath, _dirnames, filenames in os.walk(t):
                for fn in filenames:
                    full = os.path.join(dirpath, fn)
                    items.append((full, os.path.relpath(full, root_dir).replace("\\", "/")))
        else:
            items.append((t, os.path.relpath(t, root_dir).replace("\\", "/")))
    return items


_ZIP_CHUNK = 1 << 20          # 每次读写 1 MB：内存占用与文件大小无关


def _zip_one(zf, sink, srcf, arc, chunk=_ZIP_CHUNK):
    """（生成器）把一个文件写进 zip，边写边把攒下的字节交出去。

    ⚠️ 这里刻意不用 ``zf.write()``：它会**把整个文件压完才返回**，于是 _ZipSink
    期间就攒下了一整个文件的字节 —— 实测 40MB 文件堆峰值 40.4MB，等于没改善，
    单个 10GB 模型照样顶爆内存。自己分块读写、每块 drain 一次，内存只与 chunk 有关。
    """
    st = os.fstat(srcf.fileno())
    zi = zipfile.ZipInfo(arc, date_time=time.localtime(st.st_mtime)[:6])
    zi.compress_type = zipfile.ZIP_DEFLATED
    zi.external_attr = (0o100000 | (st.st_mode & 0o777)) << 16      # 保留权限位
    with zf.open(zi, "w") as dst:
        while True:
            data = srcf.read(chunk)
            if not data:
                break
            dst.write(data)
            for c in sink.drain():
                yield c
    for c in sink.drain():
        yield c


def zip_files_stream(root_dir, rel_paths, zip_name="files.zip"):
    """流式打包：边压缩边吐，不再把整个 zip 先攒进内存。

    原实现用 BytesIO 全量入内存，用户选中一个几百 GB 的输出目录再点下载就会把内存顶爆。
    返回 (items, generator)：
      * ``items`` 先算好，调用方用它判断「有没有可下载的内容」（空则回 404），
        同时避免「开始传输后没法再改状态码」的问题；
      * ``generator`` 逐块产出 zip 字节，交给 Flask 分块传输。
    压缩方式与原来一致（ZIP_DEFLATED）。
    """
    items = zip_collect(root_dir, rel_paths)

    def _gen():
        sink = _ZipSink()
        done, skipped = 0, 0
        try:
            with zipfile.ZipFile(sink, "w", zipfile.ZIP_DEFLATED) as zf:
                for full, arc in items:
                    try:
                        srcf = open(full, "rb")
                    except OSError as e:
                        # 还没往 zip 里写过任何东西，跳过是安全的
                        log.warning("[打包] 跳过 %s: %s", full, e)
                        skipped += 1
                        continue
                    try:
                        for ch in _zip_one(zf, sink, srcf, arc):
                            yield ch
                        done += 1
                    finally:
                        srcf.close()
            # with 退出时会写入中央目录，必须再吐一次
            for ch in sink.drain():
                yield ch
        except Exception as e:
            # 已经开始传输了，状态码改不了；如实记日志（客户端会看到下载中断）
            log.error("[打包] 生成中断: %s", e)
        finally:
            log.info("[打包] %s 结束：写出 %d 个，跳过 %d 个，共 %d 个",
                     zip_name, done, skipped, len(items))

    return items, _gen()


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
    t = _safe_path(root_dir, safe_name)
    if not t:
        return {"ok": False, "msg": "无效的文件名"}
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




# ==================== 工作流解析：模型 + 插件 ====================
# 复刻 ComfyUI-Manager 的「缺失插件检测」思路：
#     工作流里的节点类名  −  本机已注册的节点类名  =  缺失节点
#     缺失节点再经 extension-node-map.json 反查 → 该装哪个插件仓库
# 「本机已注册节点」有两个来源：
#     ① ComfyUI 在运行 → GET /object_info（带 python_module，最准，与 Manager 前端同源）
#     ② ComfyUI 未运行 → 各插件 .git/config 的 origin URL 去映射表反查（覆盖不全，判定偏保守）
# 改这里要注意：_wf_object_info 的端口取自设置里的 comfyui_port，必须与 wwdmui 启动的那份一致。

_WF_BUILTIN_MODULE_ROOTS = ("nodes", "comfy_extras", "comfy_api_nodes", "comfy_api")
_WF_CORE_REPO = "https://github.com/comfyanonymous/ComfyUI"
# 非插件类节点（纯前端/占位），不参与插件判定
_WF_SKIP_NODE_TYPES = ("Note", "MarkdownNote", "Reroute", "PrimitiveNode",
                       "ReroutePrimitive", "Anything Everywhere")

_wf_lock = threading.Lock()
_wf_objinfo_cache = {"ts": 0.0, "port": "", "map": None}
_wf_packmap_cache = {"ts": 0.0, "root": "", "rev": None, "titles": None, "byurl": None}
_wf_modelidx_cache = {"ts": 0.0, "key": "", "index": None}


def _wf_port():
    """ComfyUI 服务端口（设置里没填就用 8188）。允许写成 host:port。"""
    try:
        p = (settings.get("comfyui_port") or "").strip()
    except Exception:
        p = ""
    if not p:
        return "8188"
    if ":" in p:
        p = p.rsplit(":", 1)[1]
    return p.strip() or "8188"


def _wf_url_key(u):
    """仓库 URL 归一化。统一走 database._norm_plugin_url ——
    它会顺带剥掉 gh-proxy / gitclone 这类加速前缀，而 .git/config 里的 origin
    经常带前缀，两边各写一套归一化必然对不上。"""
    from .. import database as _db
    return _db._norm_plugin_url(u)

def _wf_object_info(ttl=30.0):
    """读运行中 ComfyUI 的节点注册表。返回 (map{类名: python_module}, online)。"""
    port = _wf_port()
    now = time.time()
    with _wf_lock:
        c = _wf_objinfo_cache
        if c["map"] is not None and c["port"] == port and now - c["ts"] < ttl:
            return c["map"], True
    mp = {}
    ok = False
    try:
        import requests
        r = requests.get("http://127.0.0.1:" + port + "/object_info",
                         timeout=(3, 10), proxies=_NO_PROXY)
        if r.status_code == 200:
            raw = r.json()
            if isinstance(raw, dict):
                for k, v in raw.items():
                    mp[k] = (v.get("python_module") or "") if isinstance(v, dict) else ""
                ok = True
    except Exception:
        ok = False
    if not ok:
        return {}, False
    with _wf_lock:
        _wf_objinfo_cache.update({"ts": now, "port": port, "map": mp})
    return mp, True


def _wf_pack_map(ttl=300.0):
    """节点类名 ↔ 插件仓库 映射，直接读**内置的** models.db（node_map + nodes 表）。

    数据随包分发，不读 ComfyUI-Manager 的目录；过期了在插件管理页点「更新插件库」刷新。
    返回 (rev, titles, byurl)：
        rev    {节点类名: [仓库URL, ...]}      原始 URL，界面可直接当链接
        titles {url_key: 插件名}
        byurl  {url_key: set(节点类名)}
    """
    now = time.time()
    with _wf_lock:
        c = _wf_packmap_cache
        if c["rev"] is not None and now - c["ts"] < ttl:
            return c["rev"], c["titles"], c["byurl"]
    from .. import database as _db
    try:
        rev, byurl = _db.node_map_load()
        titles = _db.plugin_url_titles()
    except Exception:
        rev, byurl, titles = {}, {}, {}
    with _wf_lock:
        _wf_packmap_cache.update({"ts": now, "root": "", "rev": rev,
                                  "titles": titles, "byurl": byurl})
    return rev, titles, byurl


def _wf_invalidate_pack_map():
    """更新插件库后强制下一次重新读表。"""
    with _wf_lock:
        _wf_packmap_cache.update({"ts": 0.0, "root": "", "rev": None,
                                  "titles": None, "byurl": None})


def _wf_installed_keys(byurl):
    """已装插件对应的规范化 URL 集合（离线判定用）。

    两条路：① `.git/config` 里的 origin URL；② 目录名与映射表里仓库 basename 相同。
    ② 覆盖那些没有 .git 的包（本机 168 个插件目录里只有 107 个带 .git）。
    返回 (keys:set, pack_urls:dict{目录名: origin URL})
    """
    pack_urls = _wf_installed_pack_urls()
    keys = set()
    for u in pack_urls.values():
        k = _wf_url_key(u)
        if k:
            keys.add(k)
    base2key = {}
    for k in byurl:
        b = k.rstrip("/").split("/")[-1]
        if b:
            base2key.setdefault(b, k)
    try:
        names = os.listdir(plugins_root())
    except Exception:
        names = []
    for nm in names:
        k = _wf_url_key(pack_urls.get(nm, "")) or base2key.get(nm.lower(), "")
        if k:
            keys.add(k)
    return keys, pack_urls

def _wf_installed_pack_urls():
    """扫 custom_nodes 各插件的 .git/config 取 origin URL → {包目录名: URL}。"""
    out = {}
    root = plugins_root()
    try:
        names = os.listdir(root)
    except Exception:
        return out
    for nm in names:
        p = os.path.join(root, nm)
        if not os.path.isdir(p):
            continue
        cfg = os.path.join(p, ".git", "config")
        if not os.path.isfile(cfg):
            continue
        try:
            with open(cfg, "r", encoding="utf-8", errors="replace") as f:
                s = f.read()
        except Exception:
            continue
        m = re.search(r"url\s*=\s*(\S+)", s)
        if m:
            out[nm] = m.group(1)
    return out


def _wf_model_index(ttl=60.0):
    """本机模型文件名索引 {小写文件名: 相对路径}，用于判断工作流引用的模型在不在。"""
    root = models_root()
    now = time.time()
    try:
        key = str(int(os.stat(root).st_mtime_ns))
    except Exception:
        key = ""
    with _wf_lock:
        c = _wf_modelidx_cache
        if c["index"] is not None and c["key"] == key and now - c["ts"] < ttl:
            return c["index"]
    idx = {}
    try:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            for fn in filenames:
                rel = os.path.relpath(os.path.join(dirpath, fn), root).replace(os.sep, "/")
                idx.setdefault(fn.lower(), rel)
    except Exception:
        pass
    with _wf_lock:
        _wf_modelidx_cache.update({"ts": now, "key": key, "index": idx})
    return idx


def _wf_parse_api_nodes(data):
    """兼容 ComfyUI「API 格式」工作流（{id: {class_type, inputs}}）。

    转成与 UI 格式等价的 node 列表；不是 API 格式则返回 None。
    """
    if not isinstance(data, dict) or not data:
        return None
    out = []
    for v in data.values():
        if not isinstance(v, dict) or "class_type" not in v:
            return None
        ntype = str(v.get("class_type") or "")
        title = ""
        meta = v.get("_meta")
        if isinstance(meta, dict):
            title = str(meta.get("title") or "")
        vals = []
        inputs = v.get("inputs")
        if isinstance(inputs, dict):
            for iv in inputs.values():
                if isinstance(iv, str):
                    vals.append(iv)
                elif isinstance(iv, list):
                    for x in iv:
                        if isinstance(x, str):
                            vals.append(x)
        out.append({"type": ntype, "title": title, "widgets_values": vals})
    return out


def _wf_classify(ntype, info, online, rev, titles, byurl, installed_keys, pack_urls):
    """判定单个节点类型：内置 / 已装插件 / 缺失 / 未知。"""
    core_key = _wf_url_key(_WF_CORE_REPO)
    all_repos = rev.get(ntype) or []
    repos = [u for u in all_repos if _wf_url_key(u) != core_key]
    repo = repos[0] if repos else ""
    title = titles.get(_wf_url_key(repo), "") if repo else ""

    if online:
        if ntype not in info:
            # ComfyUI 在跑但没注册这个类名 → 确实缺
            return {"type": ntype, "state": "missing" if repo else "unknown",
                    "pack": "", "repo": repo, "title": title}
        mod = info.get(ntype) or ""
        if mod.startswith("custom_nodes."):
            pack = mod.split(".")[1] if "." in mod else mod
            # 已装插件的仓库只认 .git/config 里读到的真实地址，不用映射表猜（会张冠李戴）
            u = pack_urls.get(pack, "")
            return {"type": ntype, "state": "installed", "pack": pack, "repo": u,
                    "title": titles.get(_wf_url_key(u), "") if u else ""}
        return {"type": ntype, "state": "builtin", "pack": "", "repo": "", "title": ""}

    # 离线：靠「已装插件的 URL 集合」与映射表比对
    for u in all_repos:
        k = _wf_url_key(u)
        if k and k in installed_keys and k != core_key:
            return {"type": ntype, "state": "installed",
                    "pack": u.rstrip("/").split("/")[-1],
                    "repo": u, "title": titles.get(k, "")}
    if core_key in [_wf_url_key(u) for u in all_repos]:
        return {"type": ntype, "state": "builtin", "pack": "", "repo": "", "title": ""}
    if repo:
        return {"type": ntype, "state": "missing", "pack": "", "repo": repo, "title": title}
    return {"type": ntype, "state": "unknown", "pack": "", "repo": "", "title": ""}

def _wf_repo_url(u):
    """把「指向仓库里某个文件」的地址收敛成可 git clone 的仓库根地址。

    上游 extension-node-map.json 里混着 raw.githubusercontent.com/<o>/<r>/.../x.py
    这类单文件地址，还有个别 gist。前者能还原成仓库，后者根本不是仓库 ——
    直接丢掉，免得界面把 .py 地址当「仓库」给用户去 clone。
    """
    u = (u or "").strip().rstrip("/")
    if not u:
        return ""
    m = re.match(r"https?://raw\.githubusercontent\.com/([^/]+)/([^/]+)/", u, re.I)
    if m:
        return "https://github.com/" + m.group(1) + "/" + m.group(2)
    if re.search(r"gist\.", u, re.I):
        return ""
    p = re.match(r"(https?://[^/]+)/([^/]+)/([^/]+)", u, re.I)
    if not p:
        return ""
    host, owner, repo = p.group(1), p.group(2), p.group(3)
    if "." not in host.split("//", 1)[-1]:
        return ""
    repo = re.sub(r"\.git$", "", repo, flags=re.I)
    if re.search(r"\.(py|json|ya?ml|txt|md|csv|toml|ini|safetensors)$", repo, re.I):
        return ""
    return host + "/" + owner + "/" + repo


def _wf_bind_model_lib(found):
    """给解析出的模型补 exists / at / id 三个字段。

    返回 (缺失数, 其中模型库里查得到下载地址的数量)。

    * exists / at —— 来自本机 models 目录的文件名索引；
    * id —— 来自 models.db（只收录带 download_url 的记录），界面上的「下载」按钮
      复用它走既有的按 id 下载流程（队列、并发上限、镜像改写、进度都现成）。

    JSON 工作流与非 JSON 兜底（.txt/.md/.png）都走这里，两条路径口径一致。
    """
    from .. import database as _db

    midx = _wf_model_index()
    missing = 0
    for m in found:
        norm = (m.get("file") or "").replace("\\", "/").strip().lower()
        base = norm.split("/")[-1]
        at = midx.get(norm) or midx.get(base) or ""
        m["exists"] = bool(at)
        m["at"] = at
        m["id"] = 0          # 0 = 模型库里没有 -> 前端不给下载按钮，显示「库中无」
        if not at:
            missing += 1

    try:
        idmap = _db.model_ids_by_file()
    except Exception as e:
        log.warning("[解析] 读取模型库索引失败: %s", e)
        idmap = {}
    in_lib = 0
    for m in found:
        if m.get("exists"):
            continue
        key = (m.get("file") or "").replace("\\", "/").strip().lower().split("/")[-1]
        hit = idmap.get(key)
        if hit:
            m["id"] = hit[0]
            in_lib += 1
    return missing, in_lib


def workflows_analyze(root_dir, rel_path):
    """解析工作流：列出所需【模型】与所需【插件】，并判断本机是否已有。

    插件部分复刻 ComfyUI-Manager 的缺失检测：
        工作流节点类名 − 本机已注册节点类名 = 缺失节点 → 映射表反查所属插件仓库
    返回 {ok, name, path, type, models[], plugins[], missing_*}
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
        # 非 JSON 文件（.txt/.md/.png）：只能正则兜底捞模型名
        res = _workflows_analyze_text(content, rel_path)
        # 非 JSON 也要判存在性、也要能一键下载（模型库里有地址就行），
        # 否则前端会把每个模型都显示成「未找到」、徽章还会说「模型已齐全」。
        _mm, _mml = _wf_bind_model_lib(res.get("models") or [])
        res.update({"plugins": [], "plugin_count": 0, "missing_plugin_count": 0,
                    "missing_model_count": _mm, "missing_model_in_lib": _mml,
                    "builtin_node_count": 0, "unknown_nodes": [],
                    "comfyui_online": False, "source": "text"})
        return res

    nodes = data.get("nodes") if isinstance(data, dict) else None
    if isinstance(nodes, list):
        fmt = "ui"
    else:
        nodes = _wf_parse_api_nodes(data)
        fmt = "api"
    if nodes is None:
        return {"ok": False, "msg": "工作流 JSON 结构无效（既没有 nodes 数组，也不是 API 格式）"}

    # 常见模型扩展名（长的优先匹配，如 .safetensors 在 .safetensors.bak 之前）
    model_exts = (".safetensors", ".pth", ".pt", ".ckpt", ".gguf", ".onnx",
                  ".bin", ".sft", ".pt2", ".vae.pt", ".vae.safetensors",
                  ".safetensors.bak", ".pth.tar", ".tar", ".zip", ".json")
    model_exts = tuple(sorted(model_exts, key=len, reverse=True))

    found = []
    seen = set()
    node_types = []
    seen_types = set()

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

    def add_type(ntype):
        if not ntype or not isinstance(ntype, str):
            return
        if ntype in seen_types or ntype in _WF_SKIP_NODE_TYPES:
            return
        seen_types.add(ntype)
        node_types.append(ntype)

    for node in nodes:
        if not isinstance(node, dict):
            continue
        ntype = node.get("type", "")
        nname = node.get("title", "") or node.get("type", "")
        add_type(ntype)
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

    # ---- 模型：对照本机 models 目录标出缺失，并绑上模型库里的下载入口 ----
    missing_models, missing_model_in_lib = _wf_bind_model_lib(found)

    # ---- 插件：节点类名差集 + 映射表反查 ----
    from .. import database as _db

    info, online = _wf_object_info()
    rev, titles, byurl = _wf_pack_map()
    installed_keys, pack_urls = _wf_installed_keys(byurl)

    plugins_map = {}
    miss_map = {}
    builtin_nodes = []
    unknown_nodes = []
    for ntype in node_types:
        r = _wf_classify(ntype, info, online, rev, titles, byurl, installed_keys, pack_urls)
        st = r["state"]
        if st == "builtin":
            builtin_nodes.append(ntype)
        elif st == "installed":
            key = r["pack"] or r["type"]
            b = plugins_map.setdefault(key, {"pack": r["pack"] or r["type"], "installed": True,
                                             "repo": r["repo"], "title": r["title"], "nodes": []})
            b["nodes"].append(r["type"])
            if r["repo"] and not b["repo"]:
                b["repo"] = r["repo"]
            if r["title"] and not b["title"]:
                b["title"] = r["title"]
        elif st == "missing":
            key = r["pack"] or (r["repo"].rstrip("/").split("/")[-1] if r["repo"] else r["type"])
            b = miss_map.setdefault(key, {"pack": key, "installed": False,
                                          "repo": r["repo"], "title": r["title"], "nodes": []})
            b["nodes"].append(r["type"])
            if r["title"] and not b["title"]:
                b["title"] = r["title"]
        else:
            unknown_nodes.append(ntype)

    # 缺失的排前面（用户最需要看到），同组内按节点数降序
    plugins = sorted(list(miss_map.values()) + list(plugins_map.values()),
                     key=lambda x: (x["installed"], -len(x["nodes"]), x["pack"].lower()))

    # 绑上插件库里的条目 id —— 界面上的「下载」按钮直接复用它，
    # 走既有的按 id 克隆流程（自带队列、并发上限、GitHub 镜像改写与进度）。
    try:
        _idmap = _db.plugin_ids_by_url()
    except Exception:
        _idmap = {}
    for p in plugins:
        hit = _idmap.get(_wf_url_key(p.get("repo") or ""))
        p["id"] = hit[0] if hit else 0
        if not p.get("title") and hit and hit[1]:
            p["title"] = hit[1]

    return {
        "ok": True,
        "name": os.path.basename(t),
        "path": rel_path,
        "type": fmt,
        "source": "comfyui" if online else "local",
        "comfyui_online": online,
        "models": found,
        "count": len(found),
        "model_count": len(found),
        "missing_model_count": missing_models,
        "missing_model_in_lib": missing_model_in_lib,   # 其中能在模型库里找到下载地址的
        "plugins": plugins,
        "plugin_count": len(plugins),
        "missing_plugin_count": len(miss_map),
        "builtin_node_count": len(builtin_nodes),
        "unknown_nodes": unknown_nodes,
        # 内置插件库规模（models.db 的 node_map 表）：为空说明从没更新过库
        "lib_nodes": len(rev),
        "lib_repos": len(byurl),
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


def _safe_upload_rel(rel):
    """把浏览器的 webkitRelativePath 规范化成安全相对路径；非法返回 None

    - 忽略空段与 "."
    - ".." / 盘符 / 绝对路径一律判非法
    """
    parts = []
    for seg in str(rel or "").replace("\\", "/").split("/"):
        seg = seg.strip()
        if not seg or seg == ".":
            continue
        if seg == ".." or ":" in seg:
            return None
        parts.append(seg)
    return "/".join(parts)


def _save_upload_batch(dest_abs, files, rel_paths=None):
    """统一落盘：支持按 webkitRelativePath 还原目录结构，同名不覆盖（自动加序号）

    返回 {"saved","renamed","failed","results"}；调用方自行组装 msg。
    """
    base = os.path.abspath(dest_abs)
    results = []
    saved = renamed = failed = 0
    for i, f in enumerate(files or []):
        raw = os.path.basename((getattr(f, "filename", "") or "").replace("\\", "/").strip())
        if not raw or raw in (".", ".."):
            continue
        rel = ""
        if rel_paths and i < len(rel_paths):
            rel = _safe_upload_rel(rel_paths[i])
            if rel is None:
                failed += 1
                results.append({"name": raw, "ok": False, "msg": "路径不合法，已跳过"})
                continue
        if rel:
            tgt = _safe_path(base, rel)
            if not tgt:
                failed += 1
                results.append({"name": rel, "ok": False, "msg": "路径越界，已跳过"})
                continue
        else:
            # 原来这里直接 join：filename 是 "C:x" 时 join 会得到驱动器相对路径，
            # 落到 C 盘当前目录去。统一过一遍 _safe_path。
            tgt = _safe_path(base, raw)
            if not tgt:
                failed += 1
                results.append({"name": raw, "ok": False, "msg": "文件名不合法，已跳过"})
                continue
        tgt = _unique_path(tgt)
        try:
            sub = os.path.dirname(tgt)
            if sub and not os.path.isdir(sub):
                os.makedirs(sub, exist_ok=True)
            f.save(tgt)
        except Exception as e:
            failed += 1
            results.append({"name": raw, "ok": False, "msg": str(e)})
            continue
        saved += 1
        show = os.path.relpath(tgt, base).replace(os.sep, "/")
        if os.path.basename(tgt) != raw:
            renamed += 1
            results.append({"name": show, "ok": True, "msg": "已上传（同名，自动改名）"})
        else:
            results.append({"name": show, "ok": True, "msg": "已上传"})
    return {"saved": saved, "renamed": renamed, "failed": failed, "results": results}


def upload_output_files(root_dir, dest_rel, files, rel_paths=None):
    """上传单个/多个文件到指定根目录（可选按 webkitRelativePath 还原目录结构）"""
    dest = _safe_path(root_dir, dest_rel)
    if not dest or not os.path.isdir(dest):
        return {"ok": False, "msg": "目标目录无效"}
    r = _save_upload_batch(dest, files, rel_paths)
    if not r["saved"] and not r["failed"]:
        return {"ok": False, "msg": "未选择文件"}
    parts = ["已上传 %d 个文件" % r["saved"]]
    if r["renamed"]:
        parts.append("%d 个同名文件已自动加序号" % r["renamed"])
    if r["failed"]:
        parts.append("%d 个失败" % r["failed"])
    return {"ok": True, "saved": r["saved"], "renamed": r["renamed"],
            "failed": r["failed"], "results": r["results"], "msg": "，".join(parts)}


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


# ============================================================
# 域拆分再导出（v5.0 第 6 步）
# ------------------------------------------------------------
# 子域模块把定义**原文搬走**后，在这里并回本命名空间，保证 `wwdm_app.services.X`
# 与拆分前完全一致：同一对象（`is` 判等）、可打桩、可 `from .services import X`。
# ⚠️ 子域新增/改名符号时必须同步加这里；验证脚本用 AST 比对 HEAD 的符号集兜底。
# ============================================================
from .douyin import (                                     # noqa: E402,F401
    douyin_fetch, douyin_download_images, douyin_download_video,
    douyin_download_status, douyin_extract_image_urls,
    douyin_load_cookie, douyin_get_cookie_string,
    _DOUYIN_HEADERS,
    _douyin_aria2_headers, _douyin_dedup_urls, _douyin_extract_aweme_id,
    _douyin_extract_balanced_arr, _douyin_extract_links,
    _douyin_extract_note_id, _douyin_extract_urls_by_key,
)

from .shutdown import (                                   # noqa: E402,F401
    shutdown_status, schedule_shutdown, cancel_shutdown,
    _SHUTDOWN_STATE,
    _persist_shutdown_state, _clear_shutdown_state, _restore_shutdown_state,
    _shutdown_cmd, _reap_shutdown_state, _shutdown_lock,
)

# ---- 域 4/10：plugins ----
from .plugins import (                                    # noqa: E402,F401
    plugins_update_library, plugins_ensure_row, plugins_clone_by_url,
    _PLUGIN_LIB_BASES, _fetch_lib_file,
)

# ---- 域 5/10：clone ----
from .clone import (                                           # noqa: E402,F401
    start_clone, start_clone_batch, get_clone_status, get_all_clone_status,
    cancel_clone, cancel_clone_with_cleanup, stop_clone_and_cleanup,
    clone_procs, clone_infos, clone_mirrors, clone_queue, clone_lock,
    clone_session_started, clone_session_folder, clone_session_done, clone_cleanup_lock,
    _git_clone_thread, _get_clone_count, _process_clone_queue, _rmtree_force,
)

# ---- 域 6/11：files（本地模型/插件目录）----
from .files import (                                              # noqa: E402,F401
    models_root, plugins_root, LOCAL_SCOPES, MODEL_LOCAL_EXTS,
    _local_scope, _local_root, _local_root_name, _local_rel, _local_abs, _local_crumbs,
    _dir_stats_cache, _dir_stats_lock,
    invalidate_local_usage, _scan_tree, local_dir_stats,
    local_models_list, local_models_search,
    _ps_quote, _ps_run, _SHFILEOPSTRUCTW, _shfo_recycle, _ps_recycle_stage2,
    _send_to_trash, _unique_path, open_path_in_explorer,
    local_models_delete, local_models_rename, local_models_upload,
    local_models_mkdir, local_models_move, local_models_folder_tree, local_models_open,
)
