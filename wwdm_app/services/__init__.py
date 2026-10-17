import os, re, sys, io, time, json, shutil, tempfile, threading, subprocess, ctypes, configparser, zipfile
import logging
from ..config import (settings, DEFAULT_SETTINGS, IS_WINDOWS, IS_LINUX, BASE_DIR,
                     ARIA2_RPC_PORT, ARIA2_RPC_SECRET, _DEFAULT_ARIA2C, COMFYUI_DIR,
                     get_comfyui_dir, get_git_path)   # 访问器实现在 core/config.py
from ..database import db_get, db_get_many, plugin_get, plugin_get_many
from ..core import tasks as task_bus        # 任务总线：后台活儿统一上报
from ..core.tasks import KIND_DOWNLOAD, KIND_REPAIR
from . import bus                           # 上报薄封装：整体 try/except，绝不反噬业务

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
        bus.fail(KIND_DOWNLOAD, rid, "启动失败: " + str(e))
        return False, e
    download_gids[rid] = r
    download_mirrors[rid] = use_mirror
    bus.start(KIND_DOWNLOAD, rid, title=m["name"], progress=0.0, message="下载中",
              detail={"save_path": m["save_path"], "mirror": bool(use_mirror)})
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
                bus.fail(KIND_DOWNLOAD, rid, "aria2 RPC 连续出错，任务中断")
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
                bus.done(KIND_DOWNLOAD, rid, "下载完成 " + fmt_size(t),
                         detail={"total": t, "mirror": bool(download_mirrors.get(rid, False))})
            elif s == "error":
                log.error("[下载出错] %s", name)
                bus.fail(KIND_DOWNLOAD, rid, "下载出错")
            elif s == "removed":
                log.warning("[下载已取消] %s", name)
                bus.cancel(KIND_DOWNLOAD, rid, "下载已取消")
            last_status = s
            break
        bus.step(KIND_DOWNLOAD, rid, progress=round(pct, 1),
                 message=(fmt_size(c) + "/" + fmt_size(t) + "  " + fmt_speed(sp)).strip(),
                 detail={"total": t, "completed": c, "speed": sp})
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
    bus.queued(KIND_DOWNLOAD, rid, title=m["name"], position=pos,
               detail={"save_path": m["save_path"], "mirror": bool(use_mirror)})
    return True, "加入队列: " + m["name"] + " (位置" + str(pos) + ")"


def start_batch(rids, use_mirror=False):
    return [{"id": r, "ok": ok, "msg": m} for r in rids for ok, m in [start_download(r, use_mirror)]]


def pause_download(rid):
    g = download_gids.get(rid)
    if not g:
        return False, "未找到任务"
    _, e = aria2_rpc("aria2.pause", [g])
    if not e:
        bus.step(KIND_DOWNLOAD, rid, message="已暂停")
    return (True, "已暂停") if not e else (False, str(e))


def resume_download(rid):
    g = download_gids.get(rid)
    if not g:
        return False, "未找到任务"
    _, e = aria2_rpc("aria2.unpause", [g])
    if not e:
        bus.step(KIND_DOWNLOAD, rid, message="下载中")
    return (True, "已恢复") if not e else (False, str(e))


def cancel_download(rid):
    with queue_lock:
        if rid in download_queue:
            download_queue.remove(rid)
            download_mirrors.pop(rid, None)
            bus.cancel(KIND_DOWNLOAD, rid, "已从队列移除")
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
    if not e:
        # 监控线程通常也会收到 removed 再报一次，同 id 幂等，不会重复建行
        bus.cancel(KIND_DOWNLOAD, rid, "已取消")
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


def _repair_key(action_name):
    """任务中心的键：去掉「[GitHub镜像: xx]」这类标签，同一动作复用同一条任务。"""
    return str(action_name or "").split(" [")[0].strip() or "repair"


def _repair_reader(proc, task_key=None):
    global repair_logs
    try:
        for line in proc.stdout:
            with repair_log_lock:
                repair_logs.append(_strip_ansi(line.rstrip()))
    except Exception:
        pass
    # 单步任务的收尾：读完 stdout 基本就等于进程结束了，这里补一条终态上报
    if task_key:
        try:
            rc = proc.wait()
        except Exception:
            rc = None
        if rc == 0:
            bus.done(KIND_REPAIR, task_key, "完成")
        else:
            bus.fail(KIND_REPAIR, task_key, "退出码 %s" % rc)


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
    _rk = _repair_key(action_name)
    bus.start(KIND_REPAIR, _rk, title=action_name, progress=task_bus.PROGRESS_UNKNOWN,
              message="执行中", detail={"cmd": _fmt_cmd(cmd)})
    threading.Thread(target=_repair_reader, args=(repair_proc, _rk), daemon=True).start()
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
        _rk = _repair_key(action_name)
        _total = max(1, len(steps))
        _failed = None
        bus.start(KIND_REPAIR, _rk, title=action_name, progress=0.0,
                  message="准备执行 %d 个步骤" % _total)
        for _idx, (label, cmd, cwd) in enumerate(steps):
            if stop_flag.is_set():
                stopped = True
                break
            with repair_log_lock:
                repair_logs.append("")
                repair_logs.append(f"--- {label} ---")
                repair_logs.append("$ " + _fmt_cmd(cmd))
            bus.step(KIND_REPAIR, _rk, progress=round(_idx * 100.0 / _total, 1),
                     message="%s (%d/%d)" % (label, _idx + 1, _total))
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
                if rc != 0 and _failed is None:
                    _failed = (label, rc)
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
        # 任务中心收尾：停止 → 取消；有步骤非 0 退出 → 失败；否则完成
        if stopped:
            bus.cancel(KIND_REPAIR, _rk, "操作已被用户停止")
        elif _failed:
            bus.fail(KIND_REPAIR, _rk, "%s 失败 (exit=%s)" % (_failed[0], _failed[1]))
        else:
            bus.done(KIND_REPAIR, _rk, action_name + " 全部完成")
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
    _rk = _repair_key(repair_action)
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
    bus.cancel(KIND_REPAIR, _rk, a + " 已停止")
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

# ---- 域 7/11：workflows ----
from .workflows import (                                              # noqa: E402,F401
    workflows_browse, workflows_get, workflows_save, workflows_new,
    workflows_move_files, workflows_copy_files, workflows_backup,
    serve_workflow_file, workflows_analyze,
    _WF_EXTS, _WF_BUILTIN_MODULE_ROOTS, _WF_CORE_REPO, _WF_SKIP_NODE_TYPES,
    _wf_lock, _wf_objinfo_cache, _wf_packmap_cache, _wf_modelidx_cache,
    _workflow_type, _wf_port, _wf_url_key, _wf_object_info, _wf_pack_map,
    _wf_invalidate_pack_map, _wf_installed_keys, _wf_installed_pack_urls,
    _wf_model_index, _wf_parse_api_nodes, _wf_classify, _wf_repo_url,
    _wf_bind_model_lib, _workflows_analyze_text,
)

# ---- 域 8/11：output（输出/输入目录浏览 + zip + 上传）----
from .output import (                                               # noqa: E402,F401
    _IMG_EXTS, _VID_EXTS, _AUD_EXTS, _ZIP_CHUNK, _ZipSink, _file_type,
    _safe_rel, _is_inside, _safe_path, _fuzzy_match, _sort_items, recursive_search,
    browse_output_dir, serve_output_file,
    zip_collect, _zip_one, zip_files_stream,
    delete_output_files, rename_output_file, move_output_files, copy_output_files,
    send_to_input_dir, create_output_dir,
    _safe_upload_rel, _save_upload_batch, upload_output_files,
)
