#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
多源论文监控系统（增强版）
✅ 动态扩大搜索时间窗口，确保每日有推送
✅ 三大主题 + 制备方法组合查询
✅ SiliconFlow 翻译 + 飞书签名推送
"""

import os
import sys
import requests
import json
import time
import hashlib
import base64
import hmac
from pathlib import Path
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus
from bs4 import BeautifulSoup
import re

# ==================== 环境变量配置 ====================
FEISHU_WEBHOOK_URL = os.getenv("FEISHU_WEBHOOK_URL")
FEISHU_SECRET = os.getenv("FEISHU_SECRET")
SILICONFLOW_API_KEY = os.getenv("SILICONFLOW_API_KEY")
SILICONFLOW_MODEL = os.getenv("SILICONFLOW_MODEL", "deepseek-ai/DeepSeek-V4-Pro")

if not FEISHU_WEBHOOK_URL:
    print("❌ 错误：未设置环境变量 FEISHU_WEBHOOK_URL")
    sys.exit(1)

# ==================== 搜索配置 ====================
# 三大主题及其查询（包含制备方法）
ARXIV_TOPICS = [
    {
        "name": "【多铁/磁电 + 制备】",
        "queries": [
            'abs:"multiferroic"',
            'abs:"magnetoelectric"',
            'abs:"multiferroic" abs:"solid state reaction"',
            'abs:"multiferroic" abs:sintering',
            'abs:"multiferroic" abs:"ceramic method"',
            'abs:"multiferroic" abs:"chemical vapor transport"',
            'abs:"multiferroic" abs:"CVT"',
            'abs:"magnetoelectric" abs:"solid state reaction"',
            'abs:"magnetoelectric" abs:sintering',
            'abs:"magnetoelectric" abs:"ceramic method"',
            'abs:"magnetoelectric" abs:"chemical vapor transport"',
            'abs:"magnetoelectric" abs:"CVT"',
        ],
        "target_count": 5
    },
    {
        "name": "【量子自旋液体 + 制备】",
        "queries": [
            'abs:"quantum spin liquid"',
            'abs:"QSL" abs:"frustrated magnet"',
            'abs:"spin liquid" abs:"geometric frustration"',
            'abs:"quantum spin liquid" abs:"solid state reaction"',
            'abs:"quantum spin liquid" abs:sintering',
            'abs:"quantum spin liquid" abs:"chemical vapor transport"',
            'abs:"quantum spin liquid" abs:"CVT"',
            'abs:"frustrated magnet" abs:"solid state reaction"',
            'abs:"frustrated magnet" abs:"single crystal growth"',
        ],
        "target_count": 5
    },
    {
        "name": "【Kagome + 制备】",
        "queries": [
            'abs:"kagome"',
            'abs:"kagome lattice"',
            'abs:"kagome" abs:"solid state reaction"',
            'abs:"kagome" abs:sintering',
            'abs:"kagome" abs:"chemical vapor transport"',
            'abs:"kagome" abs:"CVT"',
            'abs:"kagome" abs:"single crystal"',
        ],
        "target_count": 4
    },
    {
        "name": "【制备方法专题】",
        "queries": [
            'abs:"solid state reaction" abs:"multiferroic"',
            'abs:"solid state reaction" abs:"quantum spin liquid"',
            'abs:"solid state reaction" abs:"kagome"',
            'abs:"chemical vapor transport" abs:"multiferroic"',
            'abs:"chemical vapor transport" abs:"quantum spin liquid"',
            'abs:"chemical vapor transport" abs:"kagome"',
            'abs:"flux growth" abs:"frustrated magnet"',
        ],
        "target_count": 3
    }
]

# IOP 搜索词（同样融入制备方法）
IOP_SEARCH_TERMS = [
    "multiferroic magnetoelectric solid state reaction",
    "multiferroic magnetoelectric ceramic method",
    "multiferroic magnetoelectric CVT",
    "quantum spin liquid frustrated magnet solid state",
    "quantum spin liquid frustrated magnet CVT",
    "kagome lattice solid state reaction",
    "kagome lattice sintering",
    "kagome lattice CVT",
    "solid state reaction multiferroic",
    "chemical vapor transport quantum spin liquid"
]

# 动态时间窗口配置（单位：天）
TIME_WINDOWS = [7, 14, 30, 90]  # 依次扩大
SENT_IDS_FILE = Path(__file__).parent / "sent_papers.json"

# ==================== 工具函数 ====================
def load_sent_ids():
    if SENT_IDS_FILE.exists():
        try:
            return set(json.loads(SENT_IDS_FILE.read_text(encoding="utf-8")))
        except:
            return set()
    return set()

def save_sent_ids(ids):
    SENT_IDS_FILE.write_text(json.dumps(list(ids), indent=2), encoding="utf-8")

# --- arXiv 相关 ---
def query_arxiv_raw(query_str, max_results=30, timeout=30):
    base_url = "https://export.arxiv.org/api/query"
    url = f"{base_url}?search_query={quote_plus(query_str)}&sortBy=submittedDate&sortOrder=descending&start=0&max_results={max_results}"
    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    return response.text

def parse_arxiv_xml(xml_text, since_dt):
    entries = []
    for entry in xml_text.split("<entry>")[1:]:
        try:
            title = entry.split("<title>")[1].split("</title>")[0].strip()
            summary = entry.split("<summary>")[1].split("</summary>")[0].strip()
            link = entry.split('<link href="')[1].split('"')[0]
            paper_id = "arxiv:" + link.split("/abs/")[-1]
            published = entry.split("<published>")[1].split("</published>")[0]
            pub_dt = datetime.strptime(published[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
            if pub_dt >= since_dt:
                entries.append({"id": paper_id, "title": title, "summary": summary, "link": link})
        except:
            continue
    return entries

# --- IOP nsearch 抓取 ---
def fetch_iop_nsearch_papers(keywords, since_dt):
    base_url = "https://iopscience.iop.org/nsearch"
    params = {"terms": keywords, "sort": "publishDate"}
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
    }
    try:
        response = requests.get(base_url, params=params, headers=headers, timeout=20)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        papers = []
        for item in soup.select('div.list-item'):
            try:
                title_tag = item.select_one('h3 a')
                if not title_tag:
                    continue
                title = title_tag.get_text(strip=True)
                link = "https://iopscience.iop.org" + title_tag['href']
                abs_tag = item.select_one('.abstract')
                abstract = abs_tag.get_text(strip=True) if abs_tag else ""
                date_tag = item.select_one('.pub-date')
                if not date_tag:
                    continue
                date_str = date_tag.get_text()
                match = re.search(r'(\d{1,2})\s+(\w+)\s+(\d{4})', date_str)
                if not match:
                    continue
                day, month, year = match.groups()
                pub_date = datetime.strptime(f"{day} {month} {year}", "%d %b %Y").replace(tzinfo=timezone.utc)
                if pub_date >= since_dt:
                    paper_id = f"iop:{link.split('/')[-1]}"
                    papers.append({
                        "id": paper_id,
                        "title": title,
                        "summary": abstract,
                        "link": link
                    })
            except Exception:
                continue
        return papers
    except Exception as e:
        print(f"⚠️ IOP nsearch 抓取失败 ({keywords}): {e}")
        return []

# --- SiliconFlow 摘要翻译 ---
def summarize_with_siliconflow(text):
    if not text.strip():
        return "【摘要】无摘要。"
    if SILICONFLOW_API_KEY:
        prompt = (
            "你是一位顶尖凝聚态物理学家。请将以下英文论文摘要翻译成专业、简洁的中文，并提炼出核心创新点（100字以内）。"
            f"\n\n{text}\n\n"
            "输出格式：【中文摘要】... 【核心创新】..."
        )
        headers = {"Authorization": f"Bearer {SILICONFLOW_API_KEY}", "Content-Type": "application/json"}
        data = {"model": SILICONFLOW_MODEL, "messages": [{"role": "user", "content": prompt}], "max_tokens": 300}
        try:
            resp = requests.post("https://api.siliconflow.cn/v1/chat/completions", headers=headers, json=data, timeout=20)
            if resp.status_code == 200:
                return resp.json()["choices"][0]["message"]["content"].strip()
            else:
                print(f"⚠️ SiliconFlow API 返回错误 {resp.status_code}: {resp.text[:300]}，使用原文摘要")
                return f"【摘要】{text[:200]}..."
        except Exception as e:
            print(f"⚠️ SiliconFlow 调用异常: {e}，使用原文摘要")
            return f"【摘要】{text[:200]}..."
    else:
        print("⚠️ 未设置环境变量 SILICONFLOW_API_KEY，使用原文摘要")
        return f"【摘要】{text[:200]}..."

# --- 飞书推送（支持签名）---
def send_to_feishu(title, summary, link, tag):
    content = {
        "msg_type": "post",
        "content": {
            "post": {
                "zh_cn": {
                    "title": f"{tag} {title}",
                    "content": [
                        [{"tag": "text", "text": summary}],
                        [{"tag": "a", "text": "查看全文", "href": link}]
                    ]
                }
            }
        }
    }
    if FEISHU_SECRET:
        timestamp = str(int(time.time()))
        string_to_sign = timestamp + "\n" + FEISHU_SECRET
        sign = base64.b64encode(
            hmac.new(string_to_sign.encode('utf-8'), digestmod=hashlib.sha256).digest()
        ).decode('utf-8')
        content["timestamp"] = timestamp
        content["sign"] = sign
    try:
        resp = requests.post(FEISHU_WEBHOOK_URL, json=content, timeout=10)
        if resp.status_code == 200:
            result = resp.json()
            if result.get("code") == 0:
                print(f"✅ 已发送到飞书: {title[:30]}...")
            else:
                print(f"❌ 飞书返回错误: {result}")
        else:
            print(f"❌ 发送失败 HTTP {resp.status_code}")
    except Exception as e:
        print(f"❌ 发送异常: {e}")

# ==================== 动态时间窗口搜索 ====================
def search_papers_with_expanding_window():
    sent_ids = load_sent_ids()
    all_new_papers = []
    used_window = None

    for days in TIME_WINDOWS:
        since_dt = datetime.now(timezone.utc) - timedelta(days=days)
        print(f"\n📅 尝试搜索最近 {days} 天...")

        # 临时存储本次窗口找到的论文（用于去重）
        window_papers = []

        # 1. 抓取 arXiv
        for topic in ARXIV_TOPICS:
            print(f"  🔍 检索 arXiv: {topic['name']}")
            collected = 0
            for q in topic["queries"]:
                if collected >= topic["target_count"]:
                    break
                try:
                    xml = query_arxiv_raw(q, max_results=25)
                    papers = parse_arxiv_xml(xml, since_dt)
                    for p in papers:
                        if p["id"] not in sent_ids and p["id"] not in [x["id"] for x in window_papers]:
                            print(f"    🧠 arXiv: {p['title'][:50]}...")
                            p["processed_summary"] = summarize_with_siliconflow(p["summary"])
                            p["tag"] = topic["name"]
                            window_papers.append(p)
                            sent_ids.add(p["id"])
                            collected += 1
                            if collected >= topic["target_count"]:
                                break
                except Exception as e:
                    print(f"    ⚠️ 查询失败: {e}")
                    continue

        # 2. 抓取 IOP
        print("  📡 搜索 IOP Science (nsearch) ...")
        for terms in IOP_SEARCH_TERMS:
            iop_papers = fetch_iop_nsearch_papers(terms, since_dt)
            for p in iop_papers:
                if p["id"] not in sent_ids and p["id"] not in [x["id"] for x in window_papers]:
                    print(f"    🧠 IOP: {p['title'][:50]}...")
                    p["processed_summary"] = summarize_with_siliconflow(p["summary"])
                    p["tag"] = "【IOP】"
                    window_papers.append(p)
                    sent_ids.add(p["id"])

        if window_papers:
            print(f"  ✅ 在 {days} 天内找到 {len(window_papers)} 篇新论文")
            all_new_papers = window_papers
            used_window = days
            break
        else:
            print(f"  ⚠️ 最近 {days} 天无新论文，扩大时间窗口...")

    return all_new_papers, used_window, sent_ids

# ==================== 主程序 ====================
if __name__ == "__main__":
    print("=" * 60)
    print("🚀 启动多源论文监控系统（增强版）")
    print("📚 来源：arXiv + IOP Science (nsearch)")
    print("=" * 60)

    new_papers, used_days, updated_sent_ids = search_papers_with_expanding_window()

    if not new_papers:
        print("\n❌ 所有时间窗口均未找到新论文。")
        # 可选：发送一条提示消息到飞书
        msg = "今日 arXiv & IOP 未找到符合条件的新论文。"
        send_to_feishu("系统通知", msg, "#", "【提示】")
    else:
        print(f"\n📬 共找到 {len(new_papers)} 篇新论文（时间窗口：最近 {used_days} 天）")
        for p in new_papers:
            send_to_feishu(p["title"], p["processed_summary"], p["link"], p["tag"])

    save_sent_ids(updated_sent_ids)
    print(f"\n✅ 任务完成！已记录论文总数：{len(updated_sent_ids)} 篇。")
