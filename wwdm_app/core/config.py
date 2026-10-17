import os, json, secrets, platform as _platform

# ---- 平台 ----
IS_WINDOWS = _platform.system() == "Windows"
IS_LINUX = _platform.system() == "Linux"

# ---- 路径 ----
# ⚠️ 本文件在 wwdm_app/core/ 下：PACKAGE_DIR 必须**多退一级**才是包目录，否则
#    BASE_DIR / DB_PATH / NOTES_DB_PATH / SETTINGS_PATH 会全部指到 wwdm_app/core/...
#    （v5.0 拆 core/ 时最容易踩的一脚：路径算错一级，库和日志会凭空“消失”）
PACKAGE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE_DIR = os.path.dirname(PACKAGE_DIR)  # 项目根目录 C:\ComfyUI\wwdmui
COMFYUI_DIR = os.path.join(os.path.dirname(BASE_DIR), "ComfyUI")
DB_PATH = os.path.join(PACKAGE_DIR, "static", "info", "models.db")
NOTES_DB_PATH = os.path.join(PACKAGE_DIR, "static", "info", "notes.db")
SETTINGS_PATH = os.path.join(BASE_DIR, "settings.json")
ARIA2_RPC_PORT = 6800
ARIA2_RPC_SECRET = "comfyui_manager"

if IS_WINDOWS:
    _DEFAULT_ARIA2C = os.path.join(BASE_DIR, "aria2-137-win64", "aria2c.exe")
else:
    _DEFAULT_ARIA2C = "aria2c"

# ---- 默认设置 ----
DEFAULT_SETTINGS = {
    "python_path": os.path.join(os.path.dirname(os.path.dirname(BASE_DIR)), "python312", "python.exe") if IS_WINDOWS else "python3",
    "aria2c_path": _DEFAULT_ARIA2C,
    "comfyui_dir": COMFYUI_DIR,
    "git_path": "git",
    "max_concurrent_downloads": 3,
    "max_concurrent_clones": 2,
    "max_connection_per_server": 8,
    "split": 8,
    "min_split_size": "10M",
    "connect_timeout": 60,
    "timeout": 60,
    "lowest_speed_limit": "1K",
    "retry_wait": 3,
    "max_tries": 0,
    "file_allocation": "none",
    "check_certificate": False,
    "auto_start_aria2": True,
    # ComfyUI 启动参数
    "comfyui_listen": "",
    "comfyui_port": "",
    "comfyui_auto_launch": True,
    "comfyui_vram": "default",
    "comfyui_preview_method": "default",
    "comfyui_cors_origin": "",
    "comfyui_extra_args": "",
    # pip 镜像
    "pip_mirror": "default",  # default/tsinghua/aliyun/tencent/douban/ustc
    # GitHub 镜像（克隆 ComfyUI 插件 / 更新 ComfyUI 时使用）
    "github_mirror": "official",  # official/ghproxy/yuntao/ghproxycn/ghproxynet/gitclone/ur1/homeboyc/akams
    # HuggingFace 渠道（模型下载 URL 改写 + 注入给 ComfyUI 的 HF_ENDPOINT）
    # hf-mirror=国内公益镜像（默认，保持既有体验）/ official=官方直连
    "hf_channel": "hf-mirror",    # hf-mirror/official
    # ComfyUI 环境变量预设（启动前注入）
    "comfyui_presets": [],  # [{"key":"TORCH_COMPILE_OFF","value":"1"},...]
    # 输出管理
    "output_dir": "",  # 空值表示用 comfyui_dir + output
    "input_dir": "",  # 空值表示用 comfyui_dir + input
    # 工作流管理
    "workflow_dir": "",  # 空值表示用 comfyui_dir + user/default/workflows
    "workflow_backup_dir": "",  # 空值表示不启用备份功能
    # 网页背景壁纸
    "wallpaper_path": "",  # 背景壁纸路径（图片或 mp4），空值表示使用默认水彩背景
    "wallpaper_blur": 0,  # 壁纸毛玻璃效果：0=无玻璃，1-10=毛玻璃像素
    # 抖音提取
    "douyin_cookie_file": "",    # dy_cookie.json 文件路径
    "douyin_save_dir": "",       # 图片/视频保存目录
    "douyin_format": "png",      # 图片格式: png / jpg
    "douyin_name_template": "{index:02d}.{ext}",  # 命名模板
    # 关机功能
    "shutdown_delay": 60,  # 默认关机延迟秒数
    # ---- 管理器访问 ----
    # web_host / web_port 重启后生效；access_token / token_required 立即生效
    "web_host": "0.0.0.0",    # 0.0.0.0=局域网可访问 / 127.0.0.1=仅本机
    "web_port": 7860,         # 管理器监听端口，1024-65535
    "access_token": "",       # 空值 = 首次启动自动生成
    "token_required": True,   # 非回环地址访问是否必须携带访问令牌
    "max_upload_gb": 8        # 单次上传体积上限（GB）
}


# ---- 运行态信息：由 run.py 在启动时写入「本进程真实监听」的值 ----
# 设置页靠它和 settings 里的值对比，才能显示「有改动待重启生效」。
RUNTIME_INFO = {
    "host": "",
    "port": 0,
    "lan_ips": [],
    "started_at": 0.0,
    "token_enabled": False,
}


def new_access_token():
    """生成一条 32 字符的访问令牌"""
    return secrets.token_urlsafe(24)


def load_settings():
    merged = dict(DEFAULT_SETTINGS)
    repaired = False
    if os.path.exists(SETTINGS_PATH):
        try:
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                saved = json.load(f)
            merged.update(saved)
        except Exception:
            pass
    for key in ("aria2c_path", "comfyui_dir", "python_path"):
        val = merged.get(key, "")
        if val and not os.path.exists(val):
            merged[key] = DEFAULT_SETTINGS[key]
            repaired = True
    # 二次探测: 如果默认 python_path 也无效，搜索常见位置
    py = merged.get("python_path", "")
    if not py or not os.path.exists(py):
        candidates = [
            os.path.join(os.path.dirname(os.path.dirname(BASE_DIR)), "python312", "python.exe"),
            os.path.join(os.path.dirname(os.path.dirname(BASE_DIR)), "python3", "python.exe"),
            os.path.join(os.path.dirname(BASE_DIR), "python312", "python.exe"),
            os.path.join(os.path.dirname(BASE_DIR), "python3", "python.exe"),
        ]
        for c in candidates:
            if os.path.exists(c):
                merged["python_path"] = c
                repaired = True
                break
    # ---- 管理器访问设置归一化 ----
    wh = str(merged.get("web_host") or "").strip()
    if wh not in ("0.0.0.0", "127.0.0.1"):
        wh = "0.0.0.0"
        repaired = True
    merged["web_host"] = wh
    try:
        wp = int(merged.get("web_port") or 7860)
    except (ValueError, TypeError):
        wp = 7860
    if not (1024 <= wp <= 65535):
        wp = 7860
    if merged.get("web_port") != wp:
        merged["web_port"] = wp
        repaired = True
    merged["token_required"] = bool(merged.get("token_required", True))
    # 访问令牌：首次启动（或重置后）自动生成一条，外部访问的凭据
    if not str(merged.get("access_token") or "").strip():
        merged["access_token"] = new_access_token()
        repaired = True
    try:
        mu = float(merged.get("max_upload_gb", 8) or 8)
    except (ValueError, TypeError):
        mu = 8.0
    merged["max_upload_gb"] = max(0.1, min(64.0, mu))
    if repaired:
        _save_settings(merged)
    return merged


def _save_settings(s):
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=2)


def save_settings(s):
    _save_settings(s)


settings = load_settings()


# ---- 派生访问器 ----
# 放在 core 而不是 services，是为了让 sqlite 层（core/db.py）也能直接用：
# db.py 要拼 custom_nodes 路径，但它**不能反向 import 业务模块 services**
# （core 只许被依赖，不许依赖业务）。
# ⚠️ 只此一份实现：services.py / routes.py 都从这里取，别再抄第二份。
def get_comfyui_dir():
    """当前生效的 ComfyUI 目录（设置优先，空值回落默认）"""
    return settings.get("comfyui_dir", COMFYUI_DIR)


def get_git_path():
    """当前生效的 git 可执行程序（设置优先）"""
    return settings.get("git_path", "git")
