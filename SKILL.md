# arXiv文献监控技能

每日推送以仓库根目录的 `arxiv_daily_report.py`、`config.yaml` 和 README 为准。下面是早期草稿，不再描述当前的 GitHub Action。

## 概述
自动从arXiv.org搜索磁电耦合和量子自旋液体相关的最新文献，支持定时推送。

## 关键词
- 磁电耦合 (magnetoelectric coupling)
- 量子自旋液体 (quantum spin liquid)
- 凝聚态物理 (condensed matter physics)
- 拓扑材料 (topological materials)

## 核心脚本

### 1. arxiv_search.py
搜索arXiv最新文献，支持关键词过滤和日期范围。

### 2. arxiv_daily_report.py
生成每日文献报告，包含摘要和链接。

### 3. setup_arxiv_monitor.py
设置定时任务和配置。

## 使用方法

```bash
# 搜索最新文献
python arxiv_search.py --keywords "magnetoelectric coupling quantum spin liquid" --days 1

# 生成日报
python arxiv_daily_report.py --output markdown

# 设置定时任务
python setup_arxiv_monitor.py --schedule "daily 09:00"
```

## 配置
编辑 `config.yaml` 设置：
- 搜索关键词
- 推送时间
- 输出格式
- 文献数量限制