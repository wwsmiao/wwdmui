"""路由兼容层 —— v5.0 第 6 步拆完之后，本模块只剩「首页 + 公共辅助」。

134 条 API 路由已分别搬进 ``wwdm_app/blueprints/`` 下的蓝图
（models / download / plugins / clone / files / input / output / workflows /
  notes / douyin / shutdown / settings / tools / comfyui / repair）。

留在这里的三样东西是**多个蓝图共用**的：
  _zip_response         流式 zip 响应（files/output/input/workflows 用）
  _tool_version_cached  git/python/aria2c 版本探测缓存（tools/download 用）
  _get_input_dir        输入目录解析（input 蓝图用）

蓝图统一 ``from ..routes import ...``。将来这些东西也可以下沉到 core/，
但先保留 1~2 个版本，避免一次性改动面过大。
"""
import os, time, threading, subprocess
from flask import render_template, Response
from . import config


def _zip_response(gen, name):
    """流式 zip 响应：边压缩边分块发送，不再先把整包读进内存。

    注意这里**不能用 stream_with_context**：生成器内部不碰 request/g，
    只用到模块级的 settings 与日志，所以请求上下文结束也能继续迭代。
    """
    return Response(gen, mimetype="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="%s"' % name})


# ---- 外部工具版本探测缓存 ----
# git / python / aria2c 的 --version 每次都要 fork/exec 一个进程（Windows 上 50~300ms），
# 而 checkGit()/detectPy() 在每次进插件页、每次点克隆前都会调一次，属纯重复劳动。
# 命中缓存后这些接口从「几十~几百 ms」降到「<1ms」。失败结果只留 10s，便于装完工具后立刻生效。
_tool_ver_cache = {}
_tool_ver_lock = threading.Lock()


def _tool_version_cached(key, argv, ttl=300.0):
    """带 TTL 的命令版本探测；返回版本首行，失败返回 None"""
    now = time.time()
    with _tool_ver_lock:
        c = _tool_ver_cache.get(key)
        if c and now - c[0] < c[2]:
            return c[1]
    out = None
    try:
        r = subprocess.run(argv, capture_output=True, text=True,
                           encoding="gbk", errors="replace", timeout=5)
        if r.returncode == 0:
            out = (r.stdout or r.stderr).split("\n")[0].strip() or None
    except Exception:
        out = None
    with _tool_ver_lock:
        _tool_ver_cache[key] = (now, out, ttl if out else 10.0)
    return out


# ---- 目录解析辅助（模块级，供蓝图导入）----
# 原先 _get_output_dir / _get_input_dir 都是 register() 内的嵌套函数，蓝图访问不到；
# 拆域时把这类辅助提到模块级，蓝图统一 `from ..routes import _get_input_dir`。
def _get_input_dir():
    d = config.settings.get("input_dir", "").strip()
    if d:
        return d
    comfy = config.settings.get("comfyui_dir", config.DEFAULT_SETTINGS["comfyui_dir"])
    return os.path.join(comfy, "input")


def register(app):
    """将所有路由注册到 Flask app"""

    @app.route("/")
    def index():
        # theme_preset 服务端直出到 <html data-preset>：配色是首屏视觉的一部分，
        # 等前端 fetch 完 /api/settings 再套色会先闪一下默认水彩
        return render_template("index.html",
                               theme_preset=config.settings.get("theme_preset", "watercolor"),
                               ui_theme=config.settings.get("ui_theme", "light"))

