# -*- coding: utf-8 -*-
"""抖音图文 / 视频解析与下载（v5.0 第 6 步 · 域 1/10）。

**从 ``services/__init__.py`` 整段搬来，函数体逐字未改**，只补了模块头与一处延迟导入：

* ``aria2_rpc`` 仍住在 ``services/__init__.py``（它属于下载域，本轮未拆），
  所以在 :func:`douyin_download_status` 里用函数内 ``from . import aria2_rpc`` 取。
  这样既不会在包初始化期形成循环导入，又保留了「打桩 ``services.aria2_rpc``
  能立刻影响这里」的行为（拆前是同一个模块全局，行为一致）。

⚠️ ``services/__init__.py`` 末尾有一段**显式再导出**，把本模块的 15 个符号并回
``wwdm_app.services`` 命名空间 —— 新增/改名符号时那边要同步加。
"""
import logging
import os
import re
import time

log = logging.getLogger("wwdm.services")   # 与拆前同一个 logger，日志输出不变


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

        if video_url:
            typ, msg = "video", ""
        elif image_urls:
            typ, msg = "note", ""
        elif aweme_id:
            # 抖音详情接口现在按请求签名（a_bogus），服务端直取会被拦；
            # 保留链接让用户在浏览器里打开查看。
            typ = "video_blocked"
            msg = "视频直链受抖音风控限制，可点「用浏览器打开链接」查看"
        else:
            typ, msg = "unknown", ""
        return {
            "target_url": url,
            "aweme_id": aweme_id,
            "note_id": note_id,
            "desc": desc,
            "type": typ,
            "msg": msg,
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
    from . import aria2_rpc   # 延迟导入：符号仍住在 services/__init__.py
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
            log.warning("[抖音] 第 %s/%s 张加入 aria2 失败: %s", idx, total, e)
            continue
        gids.append({"idx": idx, "url": url, "gid": r, "out": out_file})
    return {"ok": True, "gids": gids, "total": total, "added": len(gids), "save_dir": save_dir}


def douyin_download_video(video_url: str, save_dir: str, name: str = "video.mp4", cookie_file: str = ""):
    """通过 aria2c 下载视频文件。返回 {ok, gid, out}。"""
    from . import aria2_rpc   # 延迟导入：符号仍住在 services/__init__.py
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
    from . import aria2_rpc   # 延迟导入：aria2_rpc 仍住在 services/__init__.py
    r, e = aria2_rpc("aria2.tellStatus", [gid])
    if e or not r:
        return {"status": "error", "msg": e or "未知", "progress": 0, "total": 0, "completed": 0, "speed": 0}
    t = int(r.get("totalLength", 0))
    c = int(r.get("completedLength", 0))
    sp = int(r.get("downloadSpeed", 0))
    pct = round((c / t * 100), 1) if t > 0 else 0
    return {"status": r.get("status", "?"), "progress": pct, "total": t, "completed": c, "speed": sp, "name": r.get("files", [{}])[0].get("path", "") if r.get("files") else ""}

