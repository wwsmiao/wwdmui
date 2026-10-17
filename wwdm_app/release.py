"""发布包 ``settings.json`` 的白名单生成（v4.1 / S6）。

为什么必须是白名单
------------------
发布包里那份 ``settings.json`` 是要**发给别人**的（闲鱼卖出后不可撤回）。它一旦
漏进本机路径或访问令牌，就随 zip 一起出去了 —— 对方的杀毒软件、网盘、备份都可能
留痕，事后改代码也追不回来。

旧做法是排除法：拿 ``DEFAULT_SETTINGS`` 全量，再减掉几个敏感键。问题在于
``config.py`` 每次新增设置项都会**默认进包** —— 漏一次就出事（``access_token``
正是这么被点名要排除的）。

这里改成白名单：``RELEASE_KEYS`` 里没列到的键**一律不进包**。将来新增设置项，
默认行为是「不进包」；忘了加白名单的最坏后果，是买家那边少一份默认值，
``config.load_settings()`` 会用 ``DEFAULT_SETTINGS`` 补齐，功能完全不受影响。
—— 安全侧失败（fail-closed），而不是泄漏侧失败。

另有两道保险
------------
* ``RELEASE_NEVER``：显式「永不入包」清单，与白名单求交集必须为空 —— 防止以后
  有人手滑把 ``access_token`` 之流写进白名单。
* ``scan_release_settings()``：对**生成结果**做值级体检，命中绝对路径 / 本机
  用户名 / 令牌样式就报错并拒绝写盘。

本模块只依赖标准库，**不 import config**（因此不会被 ``wwdm_app/__init__.py``
那条 Flask 导入链带上），默认值由调用方以 ``defaults`` 参数注入 —— 纯函数，好测。
"""

import json
import re

# --------------------------------------------------------------------------
# 白名单：只有列在这里的键才可能进发布包
#
# 刻意不收的键（见 RELEASE_NEVER）：
#   python_path / aria2c_path / comfyui_dir  —— 本机绝对路径；买家那边由
#       config.load_settings() 自己探测，反而更准（按买家的 BASE_DIR 算）
#   access_token                             —— 凭据，绝不外发；买家首启自动生成
# --------------------------------------------------------------------------
RELEASE_KEYS = (
    # —— 下载引擎（aria2c）——
    "git_path",
    "max_concurrent_downloads",
    "max_concurrent_clones",
    "max_connection_per_server",
    "split",
    "min_split_size",
    "connect_timeout",
    "timeout",
    "lowest_speed_limit",
    "retry_wait",
    "max_tries",
    "file_allocation",
    "check_certificate",
    "auto_start_aria2",
    # —— ComfyUI 启动参数 ——
    "comfyui_listen",
    "comfyui_port",
    "comfyui_auto_launch",
    "comfyui_vram",
    "comfyui_preview_method",
    "comfyui_cors_origin",
    "comfyui_extra_args",
    "comfyui_presets",
    # —— 镜像 ——
    "pip_mirror",
    "github_mirror",
    # —— 目录（默认全空，由买家的 comfyui_dir 派生）——
    "output_dir",
    "input_dir",
    "workflow_dir",
    "workflow_backup_dir",
    # —— 界面 ——
    "wallpaper_path",
    "wallpaper_blur",
    "theme_preset",
    # —— 抖音素材提取 ——
    "douyin_cookie_file",
    "douyin_save_dir",
    "douyin_format",
    "douyin_name_template",
    # —— 关机 ——
    "shutdown_delay",
    # —— 管理器访问（**不含 access_token**）——
    "web_host",
    "web_port",
    "token_required",
    "max_upload_gb",
)

# 永不入包。与 RELEASE_KEYS 求交集必须为空（build_release_settings 会断言）。
RELEASE_NEVER = (
    "python_path",
    "aria2c_path",
    "comfyui_dir",
    "access_token",
)

# 语义上就是「买家本机路径」的键：即使在白名单里，值也强制留空，绝不照抄默认值。
# （默认值现在是空串，但万一哪天 config.py 把它们改成硬编码路径，这里兜住。）
RELEASE_BLANK = (
    "output_dir",
    "input_dir",
    "workflow_dir",
    "workflow_backup_dir",
    "wallpaper_path",
    "douyin_cookie_file",
    "douyin_save_dir",
)

# --------------------------------------------------------------------------
# 值级体检用的模式
# --------------------------------------------------------------------------

# 绝对路径：盘符（C:\ / C:/）、UNC（\\host\share 或 //host/share）、常见家目录根
# 注意 UNC 那支加了 (?<![A-Za-z0-9:]) —— 否则 https://host/x 里的 //host/ 会被当成
# UNC 误报（URL 不是本机路径，不该拦）。
_ABS_PATH_RE = re.compile(
    r"(?i)(?:^|[^A-Za-z0-9])"
    r"(?:[A-Za-z]:[\\/]"
    r"|(?<![A-Za-z0-9:])[\\/]{2}[^\\/\s]+[\\/]"
    r"|/(?:users|home|root|tmp|var|opt|mnt|media|etc)/)"
)

# 本机/开发者环境的痕迹
_LOCAL_TRACE_RE = re.compile(
    r"(?i)(administrator|\\users\\|/users/|appdata|\.workbuddy|\.cache|anaconda|venv\\|venv/)"
)

# 令牌样式：一串纯 urlsafe 字符（secrets.token_urlsafe 的产物就长这样）
_TOKENY_RE = re.compile(r"^[A-Za-z0-9_\-]{24,}={0,2}$")


def build_release_settings(defaults):
    """按白名单从 ``defaults`` 取默认值，返回可写进发布包的 dict。

    只认 ``defaults``（= ``config.DEFAULT_SETTINGS`` 的值），**绝不读真实
    settings.json** —— 开发者自己的 comfyui_dir / 令牌 / 壁纸路径根本没有机会进包。

    ``defaults`` 里缺某个白名单键会抛 ``KeyError``：宁可打包时当场报错，
    也不要静默生成一份少键的配置。
    """
    overlap = set(RELEASE_KEYS) & set(RELEASE_NEVER)
    if overlap:
        raise ValueError("RELEASE_KEYS 与 RELEASE_NEVER 冲突：%s" % sorted(overlap))

    out = {}
    for key in RELEASE_KEYS:
        out[key] = "" if key in RELEASE_BLANK else defaults[key]
    return out


def scan_release_settings(data):
    """体检生成结果，返回问题描述列表（空列表 = 通过）。

    最后一道保险：万一白名单规则被改坏，或者 ``DEFAULT_SETTINGS`` 里某个默认值
    本身就带了本机信息（例如有人把 comfyui_dir 的默认值从「运行时派生」改成硬编码），
    这里会拦下来 —— 宁可不生成，也不发出一份带路径的发布包。
    """
    issues = []
    for key in sorted(data):
        if key in RELEASE_NEVER:
            issues.append("%s：属于「永不入包」键，却出现在结果里" % key)
            continue
        val = data[key]
        if not isinstance(val, str) or not val.strip():
            continue
        if _ABS_PATH_RE.search(val):
            issues.append("%s：值像本机绝对路径 -> %r" % (key, val))
        elif _LOCAL_TRACE_RE.search(val):
            issues.append("%s：值疑似含本机环境信息 -> %r" % (key, val))
        elif _TOKENY_RE.match(val.strip()):
            issues.append("%s：值疑似访问令牌/随机串 -> %r" % (key, val))
    return issues


def dump_release_settings(data):
    """序列化为 settings.json 文本（UTF-8、两空格缩进、键序 = 写入顺序）。"""
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"
