---
name: quant_knowledge_base_builder
description: 一套用于将在线技术论坛转化为本地/云端专属知识库的全自动工具链。
---

# 量化知识库构建器 (Quant Knowledge Base Builder)

本 Skill 封装了一套完整的“论坛 -> 私有智库”自动化采集与处理流水线。特别针对量化投资社区（如宽邦）设计，能够将包含大量代码、K线图、回测曲线的复杂网页，无损转化为 AI (NotebookLM) 友好的 PDF 合集。

## 核心能力

1.  **所见即所得抓取 (Visual Scraper)**
    *   **Anti-Headless**: 使用有头浏览器 + 模拟人类操作（随机休眠、滚动）绕过反爬。
    *   **Full-Page Screenshot**: 采用长截图转 PDF 方案，完美保留图表、公式和代码高亮。
    *   **Smart Layout**: 自动注入 CSS 移除侧边栏、广告，自动修复宽表格被截断问题（Viewport 1600px）。

2.  **流式自动合集 (Stream Merger)**
    *   **流水线作业**: 后台监听下载目录，每积攒 50 篇自动合并为一本电子书。
    *   **智能书签**: 自动为合并后的 PDF 生成目录（Outline），方便 AI 检索。
    *   **Cloud Sync**: 支持直接输出到 Google Drive / OneDrive 同步目录，实现“采集即上云”。

## 目录结构

```text
quant_knowledge_base_builder/
├── pdf_scraper.py            # [核心] 爬虫主程序
├── auto_merger.py            # [核心] 自动合并/监控机器人
├── util_stealth.py           # 反爬隐身工具库
├── config_example.json       # 配置文件模板
├── SKILL.md                  # 本文档
└── utils/
    └── priority_queue_injector.py # 强插队工具（用于优先抓取特定URL）
```

## 快速开始

### 1. 安装依赖
```bash
pip install playwright pillow pikepdf
playwright install chromium
```

### 2. 配置
复制 `config_example.json` 为 `config.json`，并填入目标论坛的基础 URL。
需准备 `state.json` (Playwright 登录态) 和 `links.json` (待爬取链接列表)。

### 3. 启动流水线
**终端 A: 启动爬虫**
```bash
python3 pdf_scraper.py
```
*爬虫将以有头模式启动浏览器，开始抓取并生成 PDF。*

**终端 B: 启动合并机器人**
```bash
python3 auto_merger.py
```
*机器人将监控下载目录，自动将单篇 PDF 合并为带目录的合集，并同步到云端。*

## 进阶技巧
*   **Google Drive 同步**: 修改 `auto_merger.py` 中的 `OUTPUT_DIR` 为您的 Google Drive 挂载路径（如 `/Volumes/GoogleDrive/...`）。
*   **插队抓取**: 如果需要立即查看某篇帖子，运行 `python3 utils/priority_queue_injector.py` 修改 `progress.json`。

## 适用场景
*   构建基于 NotebookLM / RAG 的私有研报库。
*   离线备份高价值的技术论坛内容。
*   需要保留复杂网页排版（表格、图表）的归档任务。
