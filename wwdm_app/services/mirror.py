# -*- coding: utf-8 -*-
"""镜像域：GitHub 克隆镜像 + HuggingFace 下载渠道。

自 services/__init__.py 拆出（v5.1）。GitHub 部份函数体逐字未改；
HuggingFace 部份是 v5.1 新做的「渠道可切换」—— 原实现 `convert_to_mirror()`
写死把 huggingface.co 换成 hf-mirror.com，用户想用官方渠道时没有开关。

跨域依赖一律函数内延迟导入（`from . import X`）——既避开包初始化期的循环导入，
又保证「在 services 包上打桩后立刻生效」。

⚠️ 本文件内不得出现 `global` 重绑定；`settings` 是**活对象**，只能原地改
   （`settings[k] = v`），不得重新赋值。
"""
import time
from concurrent.futures import ThreadPoolExecutor

from ..config import settings

# ⚠️ save_settings **不在顶层 import**：它是模块内直接名字引用，conftest 打在
#    config.save_settings 上的桩对顶层导入无效（v5.1 实测踩到 —— 测试把真
#    settings.json 的 github_mirror 改成了 ghproxy，被指纹守卫抓出）。
#    改为函数内延迟导入，每次都从 config 模块取最新属性，打桩才能生效。


# ---- HuggingFace 渠道（v5.1）----
# hf-mirror.com 是第三方公益镜像：国内快，但有宕机/限流历史，且 gated repo
# （需要登录的模型）在镜像上拿不到。所以官方渠道必须作为可选项保留，
# 而不是像原来那样写死。默认仍是 hf-mirror，不改变现有用户体验。
HF_CHANNELS = [
    {"key": "hf-mirror", "label": "hf-mirror.com（国内推荐）", "host": "hf-mirror.com"},
    {"key": "official",  "label": "huggingface.co（官方）",   "host": "huggingface.co"},
]
_HF_CHANNEL_MAP = {c["key"]: c for c in HF_CHANNELS}
# ComfyUI / 插件运行时拉模型用的环境变量名（HuggingFace 官方支持的镜像开关）
HF_ENV_KEY = "HF_ENDPOINT"


def hf_channel():
    """当前 HF 渠道 key（official / hf-mirror）"""
    k = settings.get("hf_channel", "hf-mirror")
    return k if k in _HF_CHANNEL_MAP else "hf-mirror"


def hf_endpoint():
    """当前 HF 渠道对应的 HF_ENDPOINT 值；官方渠道返回空串（= 不注入）"""
    c = _HF_CHANNEL_MAP[hf_channel()]
    return "" if c["key"] == "official" else ("https://" + c["host"])


def convert_to_mirror(url):
    """模型下载 URL 按 HF 渠道设置改写（v5.1 前是写死 hf-mirror）"""
    if "huggingface.co" not in url:
        return url
    host = _HF_CHANNEL_MAP[hf_channel()]["host"]
    if host == "huggingface.co":
        return url
    return url.replace("huggingface.co", host)


def hf_preset_entry():
    """给 comfyui_presets 用的 HF_ENDPOINT 条目；官方渠道返回 None（表示不注入）

    复用已有的环境变量注入机制（settings.comfyui_presets），不新造轮子。
    用户在设置页只勾选「给 ComfyUI 注入 HF 镜像」，不手输 key/value ——
    HF_ENDPOINT 拼错一个字母会静默无效。
    """
    ep = hf_endpoint()
    if not ep:
        return None
    return {"key": HF_ENV_KEY, "value": ep}


# ---- GitHub 镜像（全局设置） ----
# group: 官方 / 镜像仓库(替换 github.com 域名) / 加速网站(前缀加速)
# prefix: 用于替换 "https://github.com/" 的字符串，空串 = 官方直连
#
# ⚠️ 2026-10-18 实测（本机无代理，目标 = octocat/Hello-World + ltdrdata/ComfyUI-Manager
#    的 git info/refs，各 3 次重试）：
#      gh-proxy.com        380~718ms  200  ✅ 最快
#      gh-proxy.yuntao.me  980ms      200  ✅
#      ghproxy.cn          878ms      200  ✅
#      ghproxy.net         9.0~9.4s   200  ⚠️ 通但慢到几乎不可用（会被标 slow）
#      gitclone.com        271ms 502 / 超时          ❌
#      github.ur1.fun      402 Payment Required     ❌ 改成收费了
#      ghproxy.homeboyc.cn 403 Forbidden            ❌
#      github.akams.cn     404 Not Found            ❌
#      ghfast.top / github.moeyy.xyz / hub.gitmirror.com / gh-proxy.koyeb.app 均不通 ❌
#      ghproxy.cc 返回 302，git 的 http.followRedirects 默认只跟随首次请求，
#                  行为不确定，故不加入列表
#    上面 4 个失效项**故意保留**：这类公益服务会死也会复活，删掉会让老用户
#    的设置值变成未知值。真正该做的是让 UI 显示实时探测状态（见
#    test_github_mirrors 的 status 字段），而不是替用户做删减。
GITHUB_MIRRORS = [
    {"key": "official",   "label": "GitHub 官方",         "group": "官方",     "prefix": ""},
    {"key": "ghproxy",    "label": "gh-proxy.com",        "group": "加速网站", "prefix": "https://gh-proxy.com/https://github.com/"},
    {"key": "yuntao",     "label": "gh-proxy.yuntao.me",  "group": "加速网站", "prefix": "https://gh-proxy.yuntao.me/https://github.com/"},
    {"key": "ghproxycn",  "label": "ghproxy.cn",          "group": "加速网站", "prefix": "https://ghproxy.cn/https://github.com/"},
    {"key": "ghproxynet", "label": "ghproxy.net",         "group": "加速网站", "prefix": "https://ghproxy.net/https://github.com/"},
    {"key": "gitclone",   "label": "gitclone.com",        "group": "镜像仓库", "prefix": "https://gitclone.com/github.com/"},
    {"key": "ur1",        "label": "github.ur1.fun",      "group": "镜像仓库", "prefix": "https://github.ur1.fun/"},
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


def pick_fastest_mirror(timeout=8):
    """测速并自动选用最快的可用镜像（v5.1 新增）。

    只认 status == "ok"（不含 slow）—— 慢到 3 秒以上的镜像拿来 clone 插件，
    用户体感就是「卡住」。一个可用候选都没有时返回 ok:False，**不改动当前设置**。

    写入前先原地改 settings 再落盘；save_settings 被测试打桩时静默跳过。
    """
    res = test_github_mirrors(timeout)
    if not res.get("ok"):
        return {"ok": False, "msg": res.get("msg") or "测试失败"}
    best = None
    for it in res.get("results") or []:
        if it.get("status") != "ok":
            continue
        if best is None or it["ms"] < best["ms"]:
            best = it
    if best is None:
        return {"ok": False, "msg": "没有可用的镜像（含官方直连）",
                "results": res.get("results") or []}
    # 原地改：settings 是活对象，重新赋值会让 services 包上的别名指向旧 dict
    settings["github_mirror"] = best["key"]
    try:
        from ..config import save_settings     # 延迟导入：让测试的桩能生效
        save_settings(settings)
    except Exception:
        pass
    return {"ok": True, "key": best["key"], "label": best["label"],
            "ms": best["ms"], "msg": "已选用 " + best["label"]}
