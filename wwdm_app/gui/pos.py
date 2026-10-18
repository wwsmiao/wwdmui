# -*- coding: utf-8 -*-
"""悬浮球位置持久化（纯函数，不 import tkinter，可单测）。

位置以 ``"x,y"`` 字符串存进 settings.json 的 ``fab_pos``：
  * 解析容忍脏数据（空串 / 非数字 / 负数 / 天文数字）→ 一律 ``None``，
    由调用方回退默认位置（右下角）；
  * 落盘前必须 clamp：用户上次在 4K 屏上拖的坐标，这次在 1080p 笔记本上
    启动会跑到屏幕外，不钳回去球就「丢了」；
  * 底部给任务栏预留 TASKBAR_RESERVE，默认位置不会压在任务栏上。

约定 screen_wh = (屏幕宽, 屏幕高)，球为边长 size 的正方形 hitbox。
"""
FAB_SIZE = 64           # 悬浮球直径（px），与 app.ico 图标 1:1；fab.py 画布一致
FAB_MARGIN = 8          # 拖拽后距屏幕边缘的最小留白
TASKBAR_RESERVE = 48    # 默认位置距屏幕底部的预留（任务栏粗略高度）


def parse_pos(text, screen_wh=None):
    """解析 ``"x,y"`` → ``(x, y)``；脏数据一律 ``None``。

    screen_wh 只用于合理性上界检查（超过 5 位数直接拒），不做可见性判定 ——
    跨会话分辨率变小是正常场景，越界交给 clamp_pos 修，不该在这里拒绝。
    """
    if not text or not isinstance(text, str):
        return None
    parts = text.split(",")
    if len(parts) != 2:
        return None
    try:
        x, y = int(parts[0].strip()), int(parts[1].strip())
    except (ValueError, AttributeError):
        return None
    if x < 0 or y < 0 or x > 99999 or y > 99999:
        return None
    return (x, y)


def fmt_pos(x, y):
    """``(x, y)`` → ``"x,y"``（落盘用，取整防浮点尾巴）。"""
    return "%d,%d" % (int(x), int(y))


def clamp_pos(x, y, screen_wh, size=FAB_SIZE, margin=FAB_MARGIN):
    """把坐标钳回 ``[margin, 屏幕 - size - margin]``，保证球永远够得着。"""
    sw, sh = int(screen_wh[0]), int(screen_wh[1])
    lo = max(0, int(margin))
    hi_x = max(lo, sw - int(size) - lo)
    hi_y = max(lo, sh - int(size) - lo)
    return (min(max(int(x), lo), hi_x), min(max(int(y), lo), hi_y))


def default_pos(screen_wh, size=FAB_SIZE, margin=FAB_MARGIN):
    """默认位置：右下角，扣掉任务栏预留。"""
    sw, sh = int(screen_wh[0]), int(screen_wh[1])
    x = sw - int(size) - max(0, int(margin))
    y = sh - int(size) - TASKBAR_RESERVE
    return (max(0, x), max(0, y))


def resolve_pos(saved_text, screen_wh, size=FAB_SIZE, margin=FAB_MARGIN):
    """一步到位：存的字符串 → 可用的屏幕坐标。

    saved_text 为空/脏 → 默认右下角；合法但越界 → clamp 后的位置。
    """
    p = parse_pos(saved_text, screen_wh)
    if p is None:
        return default_pos(screen_wh, size=size, margin=margin)
    return clamp_pos(p[0], p[1], screen_wh, size=size, margin=margin)
