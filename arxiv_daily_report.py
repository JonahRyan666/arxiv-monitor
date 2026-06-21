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
import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus
from bs4 import BeautifulSoup
import re

JPSJ_ISSNS = ("0031-9015", "1347-4073")
JPSJ_RELEVANCE_TERMS = (
    "magnetoelectric",
    "multiferroic",
    "quantum spin liquid",
    "spin liquid",
    "kitaev",
    "kagome",
    "triangular lattice",
    "frustrated magnet",
    "frustrated magnetism",
    "neutron scattering",
    "single-crystal",
    "single crystal",
    "single crystal growth",
    "floating zone",
    "flux growth",
    "chemical vapor transport",
)

# ==================== 环境变量配置 ====================
FEISHU_WEBHOOK_URL = os.getenv("FEISHU_WEBHOOK_URL")
FEISHU_SECRET = os.getenv("FEISHU_SECRET")
SILICONFLOW_API_KEY = os.getenv("SILICONFLOW_API_KEY")
SILICONFLOW_MODEL = os.getenv("SILICONFLOW_MODEL", "deepseek-ai/DeepSeek-V4-Pro")
SILICONFLOW_TIMEOUT = int(os.getenv("SILICONFLOW_TIMEOUT", "30"))
SILICONFLOW_RETRIES = int(os.getenv("SILICONFLOW_RETRIES", "1"))
TRANSLATION_VALIDATION_RETRIES = int(os.getenv("TRANSLATION_VALIDATION_RETRIES", "2"))
MAX_PAPERS_PER_RUN = int(os.getenv("MAX_PAPERS_PER_RUN", "8"))
JPSJ_TARGET_PER_RUN = int(os.getenv("JPSJ_TARGET_PER_RUN", "3"))
JPSJ_BROWSER_COOKIE_FETCH = os.getenv("JPSJ_BROWSER_COOKIE_FETCH", "0") == "1"

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

# JPSJ / Journal of the Physical Society of Japan 搜索词
JPSJ_SEARCH_TERMS = [
    "single crystal magnetoelectric",
    "single crystal multiferroic",
    "single crystal growth magnetoelectric",
    "floating zone magnetoelectric",
    "flux growth frustrated magnet",
    "chemical vapor transport quantum spin liquid",
    "quantum spin liquid single crystal",
    "Kitaev single crystal",
    "kagome quantum spin liquid",
    "triangular lattice quantum spin liquid",
    "frustrated magnetism neutron scattering",
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

def reached_run_limit(papers):
    return len(papers) >= MAX_PAPERS_PER_RUN

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

def clean_title_text(text):
    text = BeautifulSoup(text or "", "html.parser").get_text(" ", strip=True)
    text = re.sub(r"\s+", " ", text)
    return text.strip()

def title_words(text):
    stop_words = {
        "the", "and", "for", "with", "from", "into", "onto", "of", "in", "on",
        "a", "an", "to", "by", "at", "as", "is", "are", "via", "using",
    }
    words = re.findall(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)?", clean_title_text(text))
    return [w for w in words if len(w) > 1 and w.lower() not in stop_words]

def title_overlap_score(a, b):
    a_words = {w.lower() for w in title_words(a)}
    b_words = {w.lower() for w in title_words(b)}
    if not a_words or not b_words:
        return 0.0
    return len(a_words & b_words) / max(len(a_words), 1)

def parse_arxiv_entries(xml_text):
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    entries = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return entries
    for entry in root.findall("atom:entry", ns):
        title_el = entry.find("atom:title", ns)
        summary_el = entry.find("atom:summary", ns)
        id_el = entry.find("atom:id", ns)
        if title_el is None or summary_el is None or id_el is None:
            continue
        entries.append({
            "title": clean_title_text("".join(title_el.itertext())),
            "summary": " ".join("".join(summary_el.itertext()).split()),
            "link": id_el.text,
        })
    return entries

def arxiv_queries_for_title(title):
    words = title_words(title)
    if len(words) < 2:
        return []
    clean_title = clean_title_text(title)
    queries = [f'ti:"{clean_title}"']
    first_phrase = " ".join(words[:2])
    rare_phrase = next((w for w in words[2:] if "-" in w), None)
    if not rare_phrase and len(words) >= 4:
        rare_phrase = " ".join(words[2:4])
    if rare_phrase:
        queries.append(f'all:"{first_phrase}" AND all:"{rare_phrase}"')
    if len(words) >= 4:
        queries.append(f'all:"{" ".join(words[:4])}"')
    return queries

def fetch_arxiv_abstract_by_title(title):
    best_entry = None
    best_score = 0.0
    for query in arxiv_queries_for_title(title):
        try:
            xml = query_arxiv_raw(query, max_results=5, timeout=20)
            for entry in parse_arxiv_entries(xml):
                score = title_overlap_score(title, entry["title"])
                if score > best_score:
                    best_entry = entry
                    best_score = score
        except Exception as e:
            print(f"⚠️ arXiv 摘要兜底查询失败 ({query}): {e}")
    if best_entry and best_score >= 0.45:
        print(f"    🔁 使用 arXiv 摘要兜底: {best_entry['title'][:60]}...")
        return best_entry["summary"]
    return ""

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

# --- JPSJ 抓取 ---
def is_relevant_jpsj_hit(title, summary=""):
    text = f"{title} {summary}".lower()
    return any(term in text for term in JPSJ_RELEVANCE_TERMS)

def is_placeholder_summary(summary):
    summary = (summary or "").strip()
    if not summary:
        return True
    return (
        summary.startswith("JPSJ/Crossref search hit for:")
        or summary.startswith("JPSJ search hit for:")
        or summary == "无摘要。请仅根据题名判断。"
    )

def fetch_browser_cookies_for_jpsj():
    try:
        import browser_cookie3
    except Exception as e:
        print(f"⚠️ 未启用浏览器 cookie 读取: {e}")
        return None
    for loader_name in ("edge", "chrome"):
        loader = getattr(browser_cookie3, loader_name, None)
        if not loader:
            continue
        try:
            return loader(domain_name="journals.jps.jp")
        except Exception as e:
            print(f"⚠️ 读取 {loader_name} cookie 失败: {e}")
    return None

def extract_jpsj_abstract_from_html(html):
    if not html or "Just a moment..." in html or "security verification" in html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for selector in (
        "meta[name='description']",
        ".hlFld-Abstract",
        ".abstractSection",
        ".abstract",
        "#abstract",
        "section.abstract",
        "div.NLM_abstract",
    ):
        node = soup.select_one(selector)
        if not node:
            continue
        text = node.get("content", "") if node.name == "meta" else node.get_text(" ", strip=True)
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) > 80 and "cookie" not in text.lower():
            return text

    text = soup.get_text(" ", strip=True)
    text = re.sub(r"\s+", " ", text)
    match = re.search(
        r"(We\s+(?:report|study|investigate|present|show|measure|demonstrate).+?)(?:©\d{4}|https://doi\.org|1\.\s*Introduction|References)",
        text,
        flags=re.I,
    )
    if match:
        abstract = match.group(1).strip()
        if len(abstract) > 80:
            return abstract
    return ""

def fetch_jpsj_article_abstract(link):
    if not JPSJ_BROWSER_COOKIE_FETCH:
        return ""
    cookies = fetch_browser_cookies_for_jpsj()
    if cookies is None:
        return ""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        response = requests.get(link, headers=headers, cookies=cookies, timeout=30)
        response.raise_for_status()
    except Exception as e:
        print(f"⚠️ JPSJ DOI 页面摘要抓取失败 ({link}): {e}")
        return ""
    abstract = extract_jpsj_abstract_from_html(response.text)
    if abstract:
        print(f"    📄 使用 JPSJ 网页摘要: {abstract[:60]}...")
    return abstract

def enrich_jpsj_summary(title, summary, link=""):
    if not is_placeholder_summary(summary):
        return summary
    page_summary = fetch_jpsj_article_abstract(link) if link else ""
    if page_summary:
        return page_summary
    arxiv_summary = fetch_arxiv_abstract_by_title(title)
    return arxiv_summary or summary

def parse_crossref_date(item):
    date_obj = item.get("published-print") or item.get("published-online") or item.get("published")
    if not date_obj:
        return None
    parts = date_obj.get("date-parts", [[]])[0]
    if not parts:
        return None
    year = parts[0]
    month = parts[1] if len(parts) > 1 else 1
    day = parts[2] if len(parts) > 2 else 1
    return datetime(year, month, day, tzinfo=timezone.utc)

def fetch_jpsj_crossref_papers(keywords, since_dt):
    papers = []
    seen_ids = set()
    headers = {
        "User-Agent": "arxiv-monitor/1.0 (mailto:research@example.com)",
    }
    for issn in JPSJ_ISSNS:
        params = {
            "query.bibliographic": keywords,
            "filter": f"issn:{issn},from-pub-date:{since_dt.date().isoformat()}",
            "sort": "published",
            "order": "desc",
            "rows": 20,
        }
        try:
            response = requests.get("https://api.crossref.org/works", params=params, headers=headers, timeout=20)
            response.raise_for_status()
            items = response.json().get("message", {}).get("items", [])
            for item in items:
                doi = item.get("DOI")
                titles = item.get("title") or []
                title = " ".join(BeautifulSoup(titles[0], "html.parser").get_text(" ", strip=True).split()) if titles else ""
                if not doi or not title:
                    continue
                pub_date = parse_crossref_date(item)
                if pub_date and pub_date < since_dt:
                    continue
                abstract = item.get("abstract", "")
                clean_abstract = BeautifulSoup(abstract, "html.parser").get_text(" ", strip=True) if abstract else ""
                summary = clean_abstract or f"JPSJ/Crossref search hit for: {keywords}"
                if not is_relevant_jpsj_hit(title, clean_abstract):
                    continue
                link = f"https://journals.jps.jp/doi/{doi}"
                paper_id = "jpsj:" + doi.lower()
                if paper_id in seen_ids:
                    continue
                seen_ids.add(paper_id)
                papers.append({
                    "id": paper_id,
                    "title": title,
                    "summary": enrich_jpsj_summary(title, summary, link),
                    "link": link,
                })
        except Exception as e:
            print(f"⚠️ JPSJ Crossref 抓取失败 ({keywords}, {issn}): {e}")
    return papers

def fetch_jpsj_papers(keywords, since_dt):
    base_url = "https://journals.jps.jp/action/doSearch"
    params = {
        "AllField": keywords,
        "SeriesKey": "jpsj",
        "sortBy": "Earliest",
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        response = requests.get(base_url, params=params, headers=headers, timeout=20)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        papers = []
        for item in soup.select(".issue-item, .search__item, .articleEntry, li.searchResultItem"):
            try:
                title_tag = item.select_one("a[href*='/doi/']")
                if not title_tag:
                    continue
                title = " ".join(title_tag.get_text(" ", strip=True).split())
                href = title_tag.get("href", "")
                if not title or "/doi/" not in href:
                    continue
                link = href if href.startswith("http") else "https://journals.jps.jp" + href
                doi_match = re.search(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", link, re.I)
                paper_id = "jpsj:" + (doi_match.group(0).lower() if doi_match else link.rstrip("/").split("/")[-1])

                item_text = item.get_text(" ", strip=True)
                date_match = re.search(
                    r"(\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}|[A-Za-z]{3,9}\s+\d{1,2},\s+\d{4}|\d{4})",
                    item_text,
                )
                if date_match:
                    date_text = date_match.group(1)
                    pub_date = None
                    for fmt in ("%d %B %Y", "%d %b %Y", "%B %d, %Y", "%b %d, %Y", "%Y"):
                        try:
                            pub_date = datetime.strptime(date_text, fmt).replace(tzinfo=timezone.utc)
                            break
                        except ValueError:
                            continue
                    if pub_date and pub_date < since_dt:
                        continue

                summary_tag = item.select_one(".hlFld-Abstract, .abstract, .issue-item__abstract")
                summary = summary_tag.get_text(" ", strip=True) if summary_tag else f"JPSJ search hit for: {keywords}"
                papers.append({
                    "id": paper_id,
                    "title": title,
                    "summary": enrich_jpsj_summary(title, summary, link),
                    "link": link,
                })
            except Exception:
                continue
        if papers:
            return papers
        return fetch_jpsj_crossref_papers(keywords, since_dt)
    except Exception as e:
        print(f"⚠️ JPSJ 官网抓取失败 ({keywords}): {e}，改用 Crossref 兜底")
        return fetch_jpsj_crossref_papers(keywords, since_dt)

# --- SiliconFlow 摘要翻译 ---
def has_chinese(text):
    return bool(re.search(r"[\u4e00-\u9fff]", text or ""))

def is_usable_chinese_summary(summary):
    title_match = re.search(r"【中文题名】[ \t]*(.+)", summary or "")
    abstract_match = re.search(r"【摘要翻译】[ \t]*(.+)", summary or "", flags=re.S)
    if not title_match or not abstract_match:
        return False
    title_text = title_match.group(1).strip()
    abstract_text = abstract_match.group(1).strip()
    return has_chinese(title_text) and has_chinese(abstract_text)

def summarize_with_siliconflow(title, text, tag=""):
    title = (title or "").strip()
    text = (text or "").strip()
    if is_placeholder_summary(text):
        return f"【中文题名】未获取到摘要\n【英文题名】{title}\n【摘要翻译】未能从可访问的数据源获取该文摘要，跳过自动摘要翻译。"
    source_text = text
    if SILICONFLOW_API_KEY:
        prompt = (
            "你是一位凝聚态物理和中子散射方向的科研助手。"
            "请只做两件事：1）把英文题名翻译成中文；2）把英文摘要忠实翻译成中文。"
            "不要写相关性判断，不要写推荐语，不要补充英文摘要之外的信息。"
            "必须把英文题名翻译成中文；材料化学式、Kitaev、kagome、JPSJ 等专有名词可以保留原文，"
            "但不要整句复制英文题名。"
            "\n\n"
            f"来源标签：{tag}\n"
            f"英文题名：{title}\n"
            f"英文摘要：{source_text}\n\n"
            "输出格式：\n"
            "【中文题名】...\n"
            "【摘要翻译】..."
        )
        headers = {"Authorization": f"Bearer {SILICONFLOW_API_KEY}", "Content-Type": "application/json"}
        last_error = None
        max_attempts = SILICONFLOW_RETRIES + TRANSLATION_VALIDATION_RETRIES
        for attempt in range(1, max_attempts + 1):
            retry_instruction = ""
            if attempt > 1:
                retry_instruction = (
                    "\n\n上一次输出未通过中文校验。请重新输出，确保【中文题名】不是英文原题，"
                    "【摘要翻译】必须是中文；不要添加其他字段。"
                )
            data = {
                "model": SILICONFLOW_MODEL,
                "messages": [{"role": "user", "content": prompt + retry_instruction}],
                "max_tokens": 450,
            }
            try:
                resp = requests.post("https://api.siliconflow.cn/v1/chat/completions", headers=headers, json=data, timeout=SILICONFLOW_TIMEOUT)
                if resp.status_code == 200:
                    result = resp.json()["choices"][0]["message"]["content"].strip()
                    if is_usable_chinese_summary(result):
                        return result
                    last_error = "模型返回内容未通过中文题名校验"
                else:
                    last_error = f"HTTP {resp.status_code}: {resp.text[:300]}"
                    if resp.status_code not in (408, 429, 500, 502, 503, 504):
                        break
            except Exception as e:
                last_error = str(e)

            if attempt < max_attempts:
                wait_seconds = attempt * 5
                print(f"⚠️ SiliconFlow 第 {attempt} 次调用失败: {last_error}，{wait_seconds} 秒后重试")
                time.sleep(wait_seconds)

        print(f"⚠️ SiliconFlow API 调用失败: {last_error}，使用原文摘要")
        return f"【中文题名】翻译失败，见英文题名\n【英文题名】{title}\n【摘要翻译】自动翻译失败。英文摘要：{source_text[:600]}..."
    else:
        print("⚠️ 未设置环境变量 SILICONFLOW_API_KEY，使用原文摘要")
        return f"【中文题名】未配置翻译，见英文题名\n【英文题名】{title}\n【摘要翻译】未配置 SiliconFlow API Key。英文摘要：{source_text[:600]}..."

# --- 飞书推送（支持签名）---
def get_display_title(title, summary):
    match = re.search(r"【中文题名】[ \t]*(.+)", summary or "")
    if match:
        return match.group(1).strip()
    return title

def send_to_feishu(title, summary, link, tag):
    display_title = get_display_title(title, summary)
    content = {
        "msg_type": "post",
        "content": {
            "post": {
                "zh_cn": {
                    "title": f"{tag} {display_title}",
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
                return True
            else:
                print(f"❌ 飞书返回错误: {result}")
                return False
        else:
            print(f"❌ 发送失败 HTTP {resp.status_code}")
            return False
    except Exception as e:
        print(f"❌ 发送异常: {e}")
        return False

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

        # 1. 优先抓取 JPSJ，避免被 arXiv 结果挤掉
        print("  📡 优先搜索 JPSJ / Journal of the Physical Society of Japan ...")
        jpsj_collected = 0
        for terms in JPSJ_SEARCH_TERMS:
            jpsj_papers = fetch_jpsj_papers(terms, since_dt)
            for p in jpsj_papers:
                if p["id"] not in sent_ids and p["id"] not in [x["id"] for x in window_papers]:
                    print(f"    🧠 JPSJ: {p['title'][:50]}...")
                    if is_placeholder_summary(p["summary"]):
                        print("      ⚠️ 未获取到真实摘要，跳过该 JPSJ 条目")
                        continue
                    p["tag"] = "【JPSJ】"
                    p["processed_summary"] = summarize_with_siliconflow(p["title"], p["summary"], p["tag"])
                    window_papers.append(p)
                    jpsj_collected += 1
                    if jpsj_collected >= JPSJ_TARGET_PER_RUN or reached_run_limit(window_papers):
                        break
            if jpsj_collected >= JPSJ_TARGET_PER_RUN or reached_run_limit(window_papers):
                break

        # 2. 抓取 arXiv
        if not reached_run_limit(window_papers):
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
                                p["tag"] = topic["name"]
                                p["processed_summary"] = summarize_with_siliconflow(p["title"], p["summary"], p["tag"])
                                window_papers.append(p)
                                collected += 1
                                if collected >= topic["target_count"] or reached_run_limit(window_papers):
                                    break
                        if reached_run_limit(window_papers):
                            break
                    except Exception as e:
                        print(f"    ⚠️ 查询失败: {e}")
                        continue
                if reached_run_limit(window_papers):
                    break

        # 3. 抓取 IOP
        if not reached_run_limit(window_papers):
            print("  📡 搜索 IOP Science (nsearch) ...")
            for terms in IOP_SEARCH_TERMS:
                iop_papers = fetch_iop_nsearch_papers(terms, since_dt)
                for p in iop_papers:
                    if p["id"] not in sent_ids and p["id"] not in [x["id"] for x in window_papers]:
                        print(f"    🧠 IOP: {p['title'][:50]}...")
                        p["tag"] = "【IOP】"
                        p["processed_summary"] = summarize_with_siliconflow(p["title"], p["summary"], p["tag"])
                        window_papers.append(p)
                        if reached_run_limit(window_papers):
                            break
                if reached_run_limit(window_papers):
                    break

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
    print("📚 来源：arXiv + IOP Science (nsearch) + JPSJ")
    print("=" * 60)

    new_papers, used_days, updated_sent_ids = search_papers_with_expanding_window()

    if not new_papers:
        print("\n❌ 所有时间窗口均未找到新论文。")
        # 可选：发送一条提示消息到飞书
        msg = "今日 arXiv & IOP & JPSJ 未找到符合条件的新论文。"
        send_to_feishu("系统通知", msg, "#", "【提示】")
    else:
        print(f"\n📬 共找到 {len(new_papers)} 篇新论文（时间窗口：最近 {used_days} 天）")
        successful_ids = set()
        for p in new_papers:
            if send_to_feishu(p["title"], p["processed_summary"], p["link"], p["tag"]):
                successful_ids.add(p["id"])
        updated_sent_ids.update(successful_ids)

    save_sent_ids(updated_sent_ids)
    print(f"\n✅ 任务完成！已记录论文总数：{len(updated_sent_ids)} 篇。")
