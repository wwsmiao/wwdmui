#!/usr/bin/env python3
"""强制关闭所有 aria2c 进程"""
import subprocess, sys, platform

is_win = platform.system() == "Windows"

if is_win:
    killed = 0
    # 按进程名杀
    for name in ("aria2c.exe", "aria2c"):
        try:
            r = subprocess.run(
                ["taskkill", "/F", "/IM", name, "/T"],
                capture_output=True, text=True
            )
            if r.returncode == 0:
                # 统计 killed 行数
                for line in r.stdout.splitlines():
                    if "SUCCESS" in line:
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
