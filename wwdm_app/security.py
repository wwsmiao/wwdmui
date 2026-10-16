# -*- coding: utf-8 -*-
"""访问控制：访问令牌 + 同源(CSRF)校验 + 上传体积上限。

三层防护（见《wwdmui 改进与优化方案 v2》§1.4）：

    层            挡什么                          生效时机
    ----------------------------------------------------------------
    监听地址       默认只对本机可见                 重启后
    访问令牌       0.0.0.0 下的未授权访问           立即
    同源校验       恶意网页跨站伪造写请求           立即

设计要点
--------
* **回环地址一律放行**（127.0.0.1 / ::1）：本机用户不该被自己拦下。
* 令牌只从「非回环 + token_required=true + 令牌非空」三者同时成立时才校验；
  任一不成立就直接放行（用户可在设置里关掉，或把监听地址收回 127.0.0.1）。
* 同源校验只作用于写请求（非 GET/HEAD/OPTIONS），做法是：Origin/Referer **若存在**
  就必须与本站一致。浏览器对跨站写请求一定会带 Origin 且无法伪造，而脚本/curl
  不带这两个头，所以「不存在则放行」既能挡 CSRF 又不影响命令行调用。
* 全部拒绝路径都会写一条 WARNING 日志，便于事后排查是不是有人乱扫。
"""
import hmac
import logging
from urllib.parse import urlencode, urlsplit

from flask import after_this_request, jsonify, make_response, redirect, request

from . import config

_LOG = logging.getLogger("wwdm.security")

COOKIE_NAME = "wwdm_token"
COOKIE_MAX_AGE = 365 * 24 * 3600


# ============================================================
# 基础判定
# ============================================================
def is_loopback(ip):
    """回环地址判定（含 IPv4 / IPv6 / IPv4-mapped IPv6）"""
    if not ip:
        return False
    ip = str(ip).strip().lower()
    if ip in ("127.0.0.1", "::1", "localhost"):
        return True
    if ip.startswith("127."):
        return True
    if ip.startswith("::ffff:127."):
        return True
    return False


def token_enabled():
    """令牌校验是否真正生效：开关打开 **且** 令牌非空。"""
    if not config.settings.get("token_required", True):
        return False
    return bool((config.settings.get("access_token") or "").strip())


def _given_token():
    """按优先级取令牌：?t= > Cookie > 请求头"""
    return (request.args.get("t")
            or request.cookies.get(COOKIE_NAME)
            or request.headers.get("X-Access-Token")
            or "")


def _same_origin_ok():
    """写请求的同源校验。Origin/Referer 存在就必须与本站 host 一致。"""
    for hdr in ("Origin", "Referer"):
        val = request.headers.get(hdr)
        if not val:
            continue
        try:
            netloc = urlsplit(val).netloc or ""
        except Exception:
            netloc = ""
        if not netloc:
            return False                     # 例如 "Origin: null"，一律拒绝
        if netloc.lower() != (request.host or "").lower():
            return False
    return True


def _wants_json():
    """这个请求期望 JSON 响应吗（决定拒绝时返回 JSON 还是 HTML 页）"""
    if (request.path or "").startswith("/api/"):
        return True
    if request.is_json:
        return True
    accept = request.headers.get("Accept", "")
    return ("application/json" in accept) and ("text/html" not in accept)


def _clean_url():
    """去掉 query 里的 t=，让令牌不留在浏览器历史里"""
    args = [(k, v) for k, v in request.args.items(multi=True) if k != "t"]
    q = urlencode(args)
    return (request.path or "/") + ("?" + q if q else "")


# ============================================================
# 令牌输入页（自包含，不依赖任何外部资源）
# ============================================================
_LOGIN_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>需要访问令牌 · ComfyUI 模型管理器</title>
<style>
  html,body{height:100%}
  body{margin:0;display:flex;align-items:center;justify-content:center;
       background:#0b0f14;color:#e6edf3;
       font:14px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
  .box{width:min(92vw,384px);padding:28px 26px;border-radius:14px;
       background:#111821;border:1px solid #1f2b38;box-shadow:0 18px 50px rgba(0,0,0,.55)}
  h1{margin:0 0 6px;font-size:17px;letter-spacing:.3px}
  p{margin:0 0 16px;color:#8b9bab;font-size:13px}
  input{width:100%;box-sizing:border-box;padding:11px 12px;border-radius:9px;
        border:1px solid #24323f;background:#0b0f14;color:#e6edf3;font-size:14px;outline:none}
  input:focus{border-color:#00d4ff;box-shadow:0 0 0 3px rgba(0,212,255,.14)}
  button{margin-top:12px;width:100%;padding:11px;border:0;border-radius:9px;cursor:pointer;
         background:linear-gradient(135deg,#00d4ff,#0090c8);color:#04121a;
         font-weight:700;font-size:14px}
  .hint{margin:14px 0 0;font-size:12px;color:#6c7c8c}
  .err{margin:12px 0 0;font-size:12.5px;color:#ff6b6b}
</style></head>
<body><div class="box">
  <h1>需要访问令牌</h1>
  <p>本管理器开启了访问令牌保护，请输入令牌后继续。</p>
  <form method="get" autocomplete="off">
    <input name="t" type="password" autofocus placeholder="粘贴访问令牌">
    <button type="submit">进入</button>
  </form>
  <!--MSG-->
  <p class="hint">令牌可以在运行管理器的这台电脑上打开「设置 → 管理器访问」复制，
  或查看启动窗口控制台输出里的「访问令牌」。</p>
</div></body></html>
"""


def _login_response(bad):
    msg = '<p class="err">令牌不正确，请重新输入。</p>' if bad else ""
    body = _LOGIN_HTML.replace("<!--MSG-->", msg)
    resp = make_response(body, 401)
    resp.headers["Content-Type"] = "text/html; charset=utf-8"
    resp.headers["Cache-Control"] = "no-store"
    return resp


# ============================================================
# 注册
# ============================================================
def register(app):
    # ---- 上传体积上限：挡「一个请求把内存/磁盘打满」 ----
    try:
        gb = float(config.settings.get("max_upload_gb", 8) or 8)
    except (ValueError, TypeError):
        gb = 8.0
    gb = max(0.1, min(64.0, gb))
    app.config["MAX_CONTENT_LENGTH"] = int(gb * 1024 * 1024 * 1024)

    @app.errorhandler(413)
    def _payload_too_large(e):                # noqa: ARG001
        limit_gb = app.config.get("MAX_CONTENT_LENGTH", 0) / 1024.0 / 1024.0 / 1024.0
        msg = ("上传内容超过上限（当前 %.1f GB）。可在「设置 → 管理器访问」里调整上限，"
               "或分批上传。" % limit_gb)
        _LOG.warning("拒绝超大请求: %s %s (%s)", request.method, request.path, request.remote_addr)
        if _wants_json():
            return jsonify({"ok": False, "msg": msg}), 413
        return ("<meta charset='utf-8'>"
                "<div style=\"font:14px/1.8 -apple-system,'Microsoft YaHei',sans-serif;"
                "padding:32px;color:#3a3a3a\">%s</div>" % msg), 413

    @app.before_request
    def _gate():
        # ① 同源校验（写请求）
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            if not _same_origin_ok():
                _LOG.warning("拒绝跨站写请求: %s %s origin=%s referer=%s ip=%s",
                             request.method, request.path,
                             request.headers.get("Origin"),
                             request.headers.get("Referer"),
                             request.remote_addr)
                msg = "请求来源校验失败（跨站请求已被拒绝），请从管理器页面内操作。"
                if _wants_json():
                    return jsonify({"ok": False, "msg": msg}), 403
                return ("<meta charset='utf-8'>"
                        "<div style=\"font:14px/1.8 -apple-system,'Microsoft YaHei',sans-serif;"
                        "padding:32px;color:#3a3a3a\">%s</div>" % msg), 403

        # ② 回环地址免校验
        if is_loopback(request.remote_addr):
            return None

        # ③ 访问令牌
        if not token_enabled():
            return None
        want = (config.settings.get("access_token") or "").strip()
        given = str(_given_token() or "")
        if given and hmac.compare_digest(given, want):
            if request.args.get("t"):
                if _wants_json():
                    # API 请求不能 302（fetch/XHR 会自己追随重定向，语义会乱）。
                    # 直接放行，顺手把 Cookie 挂到响应上，后续请求就不必再带 ?t=。
                    @after_this_request
                    def _set_cookie(resp):
                        resp.set_cookie(COOKIE_NAME, want, max_age=COOKIE_MAX_AGE,
                                        httponly=True, samesite="Lax")
                        return resp
                    return None
                # 页面请求：写 Cookie 后 302 回干净 URL，令牌不留在浏览器历史里
                resp = redirect(_clean_url(), code=302)
                resp.set_cookie(COOKIE_NAME, want, max_age=COOKIE_MAX_AGE,
                                httponly=True, samesite="Lax")
                return resp
            return None

        _LOG.warning("未授权访问被拒: %s %s ip=%s", request.method, request.path, request.remote_addr)
        if _wants_json():
            return jsonify({"ok": False, "need_token": True,
                            "msg": "未授权：需要访问令牌"}), 401
        return _login_response(bad=bool(request.args.get("t") or
                                        request.cookies.get(COOKIE_NAME)))
