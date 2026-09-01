# ComfyUI 模型管理器（薇薇的猫）🐱

一个基于 Flask 的 ComfyUI 模型管理 Web 应用，提供模型浏览、批量导入、下载（aria2c）、输入/输出文件管理、工作流管理、抖音素材提取、定时关机等一站式管理功能。

## ✨ 功能特性

- **模型管理**：浏览 / 搜索 / 筛选模型，支持批量新增（自动提取名称）、删除、打开所在目录
- **输入/输出管理**：浏览输入输出目录，右键菜单支持复制、移动、删除、发送到输入文件夹、新建文件夹
- **工作流管理**：浏览 / JSON 预览 / 新建 / 编辑 / 保存 / 删除 / 重命名 / 复制 / 移动 / 打开目录，一键解析工作流中引用的模型
- **下载引擎**：内置 aria2c 高速下载（支持断点续传、多线程），HuggingFace 直链批量下载
- **抖音素材提取**：输入分享链接即可提取无水印视频 / 图片，支持自定义命名模板
- **笔记功能**：独立 notes.db，支持增删改查
- **插件管理**：一键克隆缺失插件（git）、卸载插件
- **ComfyUI 管理**：一键更新（git pull）、环境变量预设、pip 镜像源切换
- **定时关机**：悬浮按钮可拖拽，单击弹窗确认后按设定延迟关机（0-3600 秒，默认 60 秒）
- **多用户系统**：用户 / 会员 / 管理员分级

## 🚀 快速开始

### 环境要求

- Python 3.10+
- Windows 10/11（Linux/macOS 部分功能受限）
- [aria2c](https://github.com/aria2/aria2/releases)（下载功能需要，可选）

### 安装

```bash
# 1. 克隆仓库
git clone https://github.com/wwsmiao/wwdmui.git
cd wwdmui

# 2. 创建虚拟环境并安装依赖
python -m venv venv
venv\Scripts\activate        # Windows
pip install -r requirements.txt

# 3. 启动
python run.py
```

启动后浏览器自动打开 http://127.0.0.1:7860

### 配置

首次运行会在项目根目录生成 `settings.json`，可配置：

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `comfyui_dir` | ComfyUI 安装目录 | 空（自动探测） |
| `output_dir` | 输出目录 | `comfyui_dir/output` |
| `input_dir` | 输入目录 | `comfyui_dir/input` |
| `workflow_dir` | 工作流目录 | `comfyui_dir/user/default/workflows` |
| `pip_mirror` | pip 镜像源 | `official` |
| `shutdown_delay` | 默认关机延迟（秒） | `60` |
| `auto_start_aria2` | 启动时自动运行 aria2 | `true` |

数据库使用 SQLite（`models.db`），无需额外配置。

## 📦 打包为独立 exe（可选）

使用 Nuitka 打包（内置 torch 等全量依赖，产物约 200MB）：

```bash
python -m nuitka --standalone --onefile --enable-plugin=tk-inter --lto=no ^
  --include-package=wwdm_app --include-data-dir=wwdm_app=templates/static ^
  --windows-icon-from-ico=icons/wwdm_cat.ico run.py
```

## 📁 项目结构

```
wwdmui/
├── run.py                  # 入口（自动打开浏览器）
├── wwdm_app/
│   ├── __init__.py         # Flask 应用工厂
│   ├── config.py           # 配置与默认设置
│   ├── database.py         # SQLite 数据操作
│   ├── routes.py           # 所有 API 路由
│   ├── services.py         # aria2c / git / 抖音 / 关机等业务逻辑
│   ├── kill_aria2.py       # aria2 清理工具
│   ├── templates/index.html # 前端页面
│   └── static/             # 静态资源（CSS/图片）
├── requirements.txt        # Python 依赖
└── README.md
```

## 🔧 API 概览

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/models` | 模型列表（分页/搜索/筛选） |
| POST | `/api/models/batch` | 批量新增模型 |
| GET | `/api/outputs` | 输出文件列表 |
| GET | `/api/inputs` | 输入文件列表 |
| GET | `/api/workflows` | 工作流列表 |
| POST | `/api/shutdown` | 定时关机（`{"delay": 秒}`） |
| POST | `/api/shutdown/cancel` | 取消定时关机 |
| GET/POST | `/api/notes*` | 笔记增删改查 |
| GET | `/api/douyin/*` | 抖音素材提取 |

## 📜 版本历史

- **v3.5**：工作流管理、笔记功能、定时关机悬浮按钮、批量模型导入
- **v3.2**：抖音素材提取、ComfyUI 环境变量预设、pip 镜像源
- **v2.9**：模型管理基础功能、aria2 下载引擎

## 📄 许可证

本项目仅供个人学习使用。
