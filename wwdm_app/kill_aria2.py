#!/usr/bin/env python3
"""强制关闭所有 aria2c 进程"""
import subprocess, sys, platform

is_win = platform.system() == "Windows"

if is_win:
    killed = 0
    # 按进程名杀
    for name in ("aria2c.exe", "aria2c"):
        try:
            # taskkill/tasklist 在中文 Windows 输出 GBK，必须显式指定编码，
            # 否则 text=True 会按 UTF-8 解码而抛 UnicodeDecodeError。
            # 也正因输出是中文，不能靠匹配 "SUCCESS" 计数 —— 改为「先查在位再杀」。
            q = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq " + name, "/FO", "CSV", "/NH"],
                capture_output=True, text=True, encoding="gbk", errors="replace"
            )
            if name.lower() not in (q.stdout or "").lower():
                continue  # 本来就没在跑
            subprocess.run(
                ["taskkill", "/F", "/IM", name, "/T"],
                capture_output=True, text=True, encoding="gbk", errors="replace"
            )
            killed += 1
        except Exception:
            pass
else:
    try:
        r = subprocess.run(
            ["pkill", "-f", "aria2c"],
            capture_output=True, text=True
        )
        killed = 1 if r.returncode == 0 else 0
    except Exception:
        try:
            subprocess.run(["killall", "aria2c"], capture_output=True)
            killed = 1
        except Exception:
            killed = 0

print(f"aria2c: 已关闭 {killed} 个进程" if killed else "aria2c: 未找到运行中的进程")
sys.exit(0)
