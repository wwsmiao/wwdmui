import os, json, platform as _platform

# ---- 平台 ----
IS_WINDOWS = _platform.system() == "Windows"
IS_LINUX = _platform.system() == "Linux"

# ---- 路径 ----
PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(PACKAGE_DIR)  # 项目根目录 C:\ComfyUI\wwdmui
COMFYUI_DIR = os.path.join(os.path.dirname(BASE_DIR), "ComfyUI")
DB_PATH = os.path.join(PACKAGE_DIR, "static", "info", "models.db")
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
    # 输出管理
    "output_dir": ""  # 空值表示用 comfyui_dir + output
}


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
    if repaired:
        _save_settings(merged)
    return merged


def _save_settings(s):
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=2)


def save_settings(s):
    _save_settings(s)


settings = load_settings()
