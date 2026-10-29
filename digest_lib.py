#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pure helpers for the experimental condensed-matter literature digest.

Network I/O lives in arxiv_daily_report.py. This module is safe to import
in unit tests: it does not read secrets or contact external services.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional
from xml.etree import ElementTree as ET

try:
    import yaml
except ImportError:  # pragma: no cover - exercised only on bare installs
    yaml = None


BACKUP_LABEL = "备选"
EMPTY_NOTE = "No qualifying papers today."
EMPTY_NOTE_ZH = "今日没有符合筛选条件的实验论文。"
DISCUSSION_FOOTER = (
    "💬 请大家在群里回复讨论：这几篇能不能追、要不要细读？"
    "回复格式示例：“#2 追 / #4 细读 / #5 跳过”"
)

DEFAULT_CATEGORIES = ("cond-mat.str-el", "cond-mat.mtrl-sci")
DEFAULT_SCREEN_MODEL = "Qwen/Qwen3.6-27B"
DEFAULT_FALLBACK_MODEL = "Qwen/Qwen3.5-9B"
DEFAULT_LEGACY_MODEL = "tencent/Hunyuan-MT-7B"
DEFAULT_MAX_PAPERS = 10
DEFAULT_MIN_PAPERS = 5
DEFAULT_LOOKBACK_DAYS = 3
DEFAULT_BATCH_SIZE = 4
DEFAULT_SCREEN_RETRIES = 2
DEFAULT_SCREEN_POOL = 16
DEFAULT_MAX_AUTHORS = 3
PASS_SCORE = 4.0
TOPUP_LIMIT = 6
CLOSE_SCORE_GAP = 1.0
WINDOW_OVERLAP = timedelta(hours=2)
MAX_PENDING_ATTEMPTS = 3
MAX_STORED_TITLES = 4000

ATOM_NS = "{http://www.w3.org/2005/Atom}"
ARXIV_NS = "{http://arxiv.org/schemas/atom}"

DEFAULT_KEYWORDS = (
    "spin liquid",
    "frustrat*",
    "kagome",
    "pyrochlore",
    "triangular lattice",
    "honeycomb",
    "kitaev",
    "multiferroic",
    "magnetoelectric",
    "ferroelectric",
    "ferroelectric polarization",
    "electric polarization",
    "magnetodielectric",
    "quantum spin ice",
    "spin ice",
    "quantum magnet",
    "helimagnet*",
    "spiral magnet",
    "noncollinear",
    "curie-weiss",
    "spinon",
    "valence bond",
    "spin gap",
    "QSL",
)

DEFAULT_THEORY_MARKERS = (
    "first-principles",
    "first principles",
    "DFT",
    "density functional",
    "ab initio",
    "we propose a model",
    "monte carlo",
    "tensor network",
    "DMRG",
)

DEFAULT_EXPERIMENT_MARKERS = (
    "single crystal",
    "polycrystal",
    "polycrystalline",
    "powder sample",
    "crystal growth",
    "were grown",
    "was grown",
    "grown by",
    "grown from",
    "synthesized",
    "synthesised",
    "chemical vapor transport",
    "CVT",
    "solid-state reaction",
    "solid state reaction",
    "solid-state synthesis",
    "solid state synthesis",
    "high-pressure",
    "high pressure",
    "flux growth",
    "flux-grown",
    "flux method",
    "magnetization",
    "susceptibility",
    "specific heat",
    "heat capacity",
    "neutron",
    "muon",
    "muSR",
    "dielectric",
    "polarization",
    "MPMS",
    "PPMS",
    "SQUID",
    "sintered",
)

# Flexible patterns for the markers the digest cares about. A config keyword
# that matches a key here uses the pattern; any other keyword is a literal
# (with "*" as a wildcard).
_KEYWORD_ALIASES = {
    "spin liquid": r"spin[\s-]liquids?",
    "quantum spin liquid": r"quantum[\s-]spin[\s-]liquids?",
    "quantum spin ice": r"quantum\s+spin\s+ice",
    "spin ice": r"spin\s+ice",
    "magnetoelectric": r"magneto-?electrics?",
    "magnetoelectric coupling": r"magneto-?electric(?:\s+coupling)?",
    "multiferroic": r"multiferroics?",
    "kagome": r"kagom[eé]",
    "kitaev": r"kitaev",
    "pyrochlore": r"pyrochlores?",
    "triangular lattice": r"triangular[\s-]lattices?",
    "honeycomb": r"honeycombs?",
    "ferroelectric": r"ferroelectrics?",
    "ferroelectric polarization": r"ferroelectric\s+polari[sz]ations?",
    "electric polarization": r"electric\s+polari[sz]ations?",
    "magnetodielectric": r"magneto-?dielectrics?",
    "quantum magnet": r"quantum\s+magnets?",
    "spiral magnet": r"spiral[\s-](?:magnet\w*|order|magnetic)",
    "noncollinear": r"non-?collinear",
    "curie-weiss": r"curie[\s\-–—]weiss",
    "spinon": r"spinons?",
    "valence bond": r"valence[\s-]bonds?",
    "spin gap": r"spin[\s-]gaps?",
    "qsl": r"\bQSLs?\b",
    "dft": r"\bDFT\b",
    "dmrg": r"\bDMRG\b",
    "cvt": r"\bCVT\b",
    "mpms": r"\bMPMS\b",
    "ppms": r"\bPPMS\b",
    "squid": r"\bSQUID\b",
    "monte carlo": r"monte[\s-]?carlo",
    "first-principles": r"first[\s-]principles",
    "first principles": r"first[\s-]principles",
    "we propose a model": r"(?:we|this work)\s+propose[sd]?\s+(?:a|an)(?:\s+\w+){0,4}\s+model",
    "tensor network": r"tensor\s+networks?",
    "density functional": r"density[\s-]functional",
    "ab initio": r"ab[\s-]initio",
    "single crystal": r"single[\s-]crystals?",
    "polycrystal": r"polycrystals?|polycrystalline",
    "polycrystalline": r"polycrystalline|polycrystals?",
    "neutron": r"neutrons?",
    "muon": r"muons?",
    "musr": r"(?:μ|µ|mu)\s*SR",
    "μsr": r"(?:μ|µ|mu)\s*SR",
    "magnetization": r"magneti[sz]ations?",
    "susceptibility": r"susceptibilit(?:y|ies)",
    "synthesized": r"synthesi[sz]ed",
    "synthesised": r"synthesi[sz]ed",
    "high-pressure": r"high[\s-]pressure",
    "high pressure": r"high[\s-]pressure",
    "solid-state reaction": r"solid[\s-]state\s+reactions?",
    "solid state reaction": r"solid[\s-]state\s+reactions?",
    "solid-state synthesis": r"solid[\s-]state\s+synthes[ei]s",
    "solid state synthesis": r"solid[\s-]state\s+synthes[ei]s",
    "chemical vapor transport": r"chemical\s+vapor\s+transport",
    "flux growth": r"flux[\s-]growth|flux[\s-]grown|grown\s+from\s+(?:a\s+)?flux",
    "flux-grown": r"flux[\s-]grown",
    "flux method": r"flux\s+method",
    "specific heat": r"specific\s+heats?",
    "heat capacity": r"heat\s+capacit(?:y|ies)",
    "polarization": r"polari[sz]ations?",
    "dielectric": r"dielectrics?",
}

_TOXIC_RE = re.compile(
    r"\b(?:thallium|mercury|beryllium|osmium tetroxide|arsine|phosphine)\b|\bBeO\b|铊|汞|铍",
    re.IGNORECASE,
)
_MBE_RE = re.compile(r"\bMBE\b|molecular beam|分子束外延", re.IGNORECASE)
_MEASUREMENT_HINTS = (
    "susceptib",
    "χ",
    "chi(",
    "specific heat",
    "heat capacity",
    "neutron",
    "μsr",
    "musr",
    "muon",
    "dielectric",
    "polarization",
    "magnetization",
    "magnetisation",
)
_PHYSICS_HINTS = (
    "无长程",
    "没有长程",
    "剩余熵",
    "自旋液体",
    "量子自旋液体",
    "t-linear",
    "t linear",
    "t^2",
    "t²",
    "residual entropy",
    "no long-range",
    "without long-range",
    "absence of long-range",
    "field-tunable",
    "field tunable",
)
_GROWTH_HINTS = re.compile(
    r"flux|chemical vapor transport|\bCVT\b|solid[\s-]state|high[\s-]pressure|"
    r"floating[\s-]zone|助熔|固相|气相传输|高温高压|浮区",
    re.IGNORECASE,
)
_TITLE_STOP = {
    "the", "and", "for", "with", "from", "into", "onto", "of", "in", "on",
    "a", "an", "to", "by", "at", "as", "is", "are", "via", "using", "its",
    "their", "this", "that", "these", "those", "into", "over", "under",
}
_DOI_RE = re.compile(r"(10\.\d{4,9}/[^\s\"'<>]+)", re.IGNORECASE)
_ARXIV_NEW_RE = re.compile(r"(\d{4}\.\d{4,6})(v\d+)?", re.IGNORECASE)
_ARXIV_OLD_RE = re.compile(
    r"([a-z-]+(?:\.[a-z]{2})?/\d{7})(v\d+)?",
    re.IGNORECASE,
)


@dataclass
class Settings:
    categories: list[str] = field(default_factory=lambda: list(DEFAULT_CATEGORIES))
    keywords: list[str] = field(default_factory=lambda: list(DEFAULT_KEYWORDS))
    theory_markers: list[str] = field(default_factory=lambda: list(DEFAULT_THEORY_MARKERS))
    experiment_markers: list[str] = field(default_factory=lambda: list(DEFAULT_EXPERIMENT_MARKERS))
    max_lookback_days: int = DEFAULT_LOOKBACK_DAYS
    max_papers_per_day: int = DEFAULT_MAX_PAPERS
    min_papers_per_day: int = DEFAULT_MIN_PAPERS
    screen_batch_size: int = DEFAULT_BATCH_SIZE
    screen_retries: int = DEFAULT_SCREEN_RETRIES
    screen_pool: int = DEFAULT_SCREEN_POOL
    max_authors: int = DEFAULT_MAX_AUTHORS
    screen_model: str = DEFAULT_SCREEN_MODEL
    fallback_model: str = DEFAULT_FALLBACK_MODEL
    legacy_model: str = DEFAULT_LEGACY_MODEL
    dry_run: bool = False
    state_path: Path = field(default_factory=lambda: Path("digest_state.json"))
    digest_path: Path = field(default_factory=lambda: Path("digest.json"))
    legacy_state_path: Path = field(default_factory=lambda: Path("sent_papers.json"))
    config_path: Optional[Path] = None
    prefilter: Any = None

    def model_chain(self) -> list[str]:
        seen: list[str] = []
        for name in (self.screen_model, self.fallback_model, self.legacy_model):
            cleaned = (name or "").strip()
            if cleaned and cleaned not in seen:
                seen.append(cleaned)
        return seen


class Prefilter:
    def __init__(
        self,
        keywords: Iterable[str],
        theory_markers: Iterable[str],
        experiment_markers: Iterable[str],
    ) -> None:
        self.topics = [_compile_keyword(item) for item in keywords if str(item).strip()]
        self.theory = [_compile_keyword(item) for item in theory_markers if str(item).strip()]
        self.experiment = [
            _compile_keyword(item) for item in experiment_markers if str(item).strip()
        ]

    def status(self, title: str, summary: str = "") -> str:
        text = f"{title or ''}\n{summary or ''}"
        if not any(pattern.search(text) for pattern in self.topics):
            return "offtopic"
        theory = any(pattern.search(text) for pattern in self.theory)
        experiment = any(pattern.search(text) for pattern in self.experiment)
        if theory and not experiment:
            return "theory"
        return "keep"

    def keep(self, title: str, summary: str = "") -> bool:
        return self.status(title, summary) == "keep"


def _compile_keyword(keyword: str) -> re.Pattern[str]:
    key = str(keyword).strip()
    alias = _KEYWORD_ALIASES.get(key.lower())
    if alias:
        return re.compile(alias, re.IGNORECASE)
    wildcard = "\x00"
    raw = key.replace("*", wildcard)
    escaped = re.escape(raw).replace(r"\ ", r"\s+")
    escaped = escaped.replace(wildcard, r"\w*")
    if "*" in key:
        escaped = r"\b" + escaped
    elif " " not in key and len(key) <= 6:
        escaped = rf"\b{escaped}\b"
    return re.compile(escaped, re.IGNORECASE)


def default_prefilter() -> Prefilter:
    return Prefilter(DEFAULT_KEYWORDS, DEFAULT_THEORY_MARKERS, DEFAULT_EXPERIMENT_MARKERS)


def passes_prefilter(title: str, summary: str = "", settings: Optional[Settings] = None) -> bool:
    prefilter = settings.prefilter if settings is not None and settings.prefilter else default_prefilter()
    return prefilter.keep(title, summary)


def prefilter_status(title: str, summary: str = "", settings: Optional[Settings] = None) -> str:
    prefilter = settings.prefilter if settings is not None and settings.prefilter else default_prefilter()
    return prefilter.status(title, summary)


def keyword_match_score(title: str, summary: str = "", settings: Optional[Settings] = None) -> int:
    """How many topic keywords hit. Title matches count more than abstract-only hits."""
    prefilter = settings.prefilter if settings is not None and settings.prefilter else default_prefilter()
    title_text = title or ""
    summary_text = summary or ""
    title_hits = sum(1 for pattern in prefilter.topics if pattern.search(title_text))
    summary_only = sum(
        1
        for pattern in prefilter.topics
        if pattern.search(summary_text) and not pattern.search(title_text)
    )
    return title_hits * 3 + summary_only


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return [str(item).strip() for item in value if str(item).strip()]


def _env_str(environ: Mapping[str, str], key: str) -> Optional[str]:
    raw = environ.get(key)
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def _env_int(environ: Mapping[str, str], key: str) -> Optional[int]:
    raw = _env_str(environ, key)
    if raw is None:
        return None
    return int(raw)


def _env_bool(environ: Mapping[str, str], key: str) -> bool:
    raw = _env_str(environ, key)
    return bool(raw) and raw.lower() in {"1", "true", "yes", "y", "on"}


def _sanitize_category(value: str) -> Optional[str]:
    text = value.strip()
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,40}", text):
        return text
    return None


def _clamp_limits(max_papers: int, min_papers: int) -> tuple[int, int]:
    max_papers = max(1, int(max_papers))
    min_papers = max(0, int(min_papers))
    if min_papers > max_papers:
        min_papers = max_papers
    return max_papers, min_papers


def load_settings(
    config_path: Optional[os.PathLike[str] | str] = None,
    environ: Optional[Mapping[str, str]] = None,
) -> Settings:
    """Load config.yaml, then apply environment overrides.

    Precedence: environment / repository variables > config.yaml > built-in defaults.
    Empty environment values are treated as unset.
    """
    env = os.environ if environ is None else environ
    root = Path(__file__).resolve().parent
    path = Path(config_path) if config_path else Path(
        _env_str(env, "DIGEST_CONFIG") or (root / "config.yaml")
    )
    data: dict[str, Any] = {}
    if path.is_file():
        if yaml is None:
            raise RuntimeError("PyYAML is required to read config.yaml")
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, dict):
            raise ValueError(f"config.yaml must be a mapping, got {type(loaded).__name__}")
        section = loaded.get("arxiv_monitor", loaded)
        if isinstance(section, dict):
            data = section

    categories = [
        cat
        for cat in (
            _sanitize_category(item)
            for item in _as_list(data.get("categories") or list(DEFAULT_CATEGORIES))
        )
        if cat
    ] or list(DEFAULT_CATEGORIES)
    env_categories = _env_str(env, "ARXIV_CATEGORIES")
    if env_categories:
        categories = [
            cat for cat in (_sanitize_category(item) for item in env_categories.split(",")) if cat
        ] or categories

    keywords = _as_list(data.get("keywords")) or list(DEFAULT_KEYWORDS)
    theory = _as_list(data.get("theory_markers")) or list(DEFAULT_THEORY_MARKERS)
    experiment = _as_list(data.get("experiment_markers")) or list(DEFAULT_EXPERIMENT_MARKERS)
    models = data.get("models") if isinstance(data.get("models"), dict) else {}

    max_papers = int(data.get("max_papers_per_day", DEFAULT_MAX_PAPERS))
    min_papers = int(data.get("min_papers_per_day", DEFAULT_MIN_PAPERS))
    lookback = int(data.get("max_lookback_days", DEFAULT_LOOKBACK_DAYS))
    batch = int(data.get("screen_batch_size", DEFAULT_BATCH_SIZE))
    retries = int(data.get("screen_retries", DEFAULT_SCREEN_RETRIES))
    screen_pool = int(data.get("screen_pool", DEFAULT_SCREEN_POOL))
    max_authors = int(data.get("max_authors_display", DEFAULT_MAX_AUTHORS))
    screen_model = str(models.get("screen") or DEFAULT_SCREEN_MODEL)
    fallback_model = str(models.get("fallback") or DEFAULT_FALLBACK_MODEL)
    legacy_model = str(models.get("legacy") or DEFAULT_LEGACY_MODEL)

    env_max = _env_int(env, "MAX_PAPERS_PER_DAY")
    if env_max is None:
        env_max = _env_int(env, "MAX_PAPERS_PER_RUN")
    if env_max is not None:
        max_papers = env_max
    env_min = _env_int(env, "MIN_PAPERS_PER_DAY")
    if env_min is not None:
        min_papers = env_min
    env_lookback = _env_int(env, "ARXIV_MAX_LOOKBACK_DAYS")
    if env_lookback is not None:
        lookback = env_lookback
    env_batch = _env_int(env, "SCREEN_BATCH_SIZE")
    if env_batch is not None:
        batch = env_batch
    env_retries = _env_int(env, "SCREEN_RETRIES")
    if env_retries is not None:
        retries = env_retries
    env_pool = _env_int(env, "SCREEN_POOL")
    if env_pool is not None:
        screen_pool = env_pool
    if _env_str(env, "SILICONFLOW_SCREEN_MODEL"):
        screen_model = _env_str(env, "SILICONFLOW_SCREEN_MODEL") or screen_model
    if _env_str(env, "SILICONFLOW_FALLBACK_MODEL"):
        fallback_model = _env_str(env, "SILICONFLOW_FALLBACK_MODEL") or fallback_model
    if _env_str(env, "SILICONFLOW_MODEL"):
        legacy_model = _env_str(env, "SILICONFLOW_MODEL") or legacy_model

    max_papers, min_papers = _clamp_limits(max_papers, min_papers)
    lookback = max(1, lookback)
    batch = max(1, min(8, batch))
    retries = max(0, retries)
    screen_pool = max(1, screen_pool)
    max_authors = max(1, max_authors)

    state_path = Path(_env_str(env, "DIGEST_STATE_PATH") or (root / "digest_state.json"))
    digest_path = Path(_env_str(env, "DIGEST_OUTPUT_PATH") or (root / "digest.json"))
    legacy_state = Path(_env_str(env, "LEGACY_SENT_PATH") or (root / "sent_papers.json"))

    settings = Settings(
        categories=categories,
        keywords=keywords,
        theory_markers=theory,
        experiment_markers=experiment,
        max_lookback_days=lookback,
        max_papers_per_day=max_papers,
        min_papers_per_day=min_papers,
        screen_batch_size=batch,
        screen_retries=retries,
        screen_pool=screen_pool,
        max_authors=max_authors,
        screen_model=screen_model,
        fallback_model=fallback_model,
        legacy_model=legacy_model,
        dry_run=_env_bool(env, "DRY_RUN"),
        state_path=state_path,
        digest_path=digest_path,
        legacy_state_path=legacy_state,
        config_path=path if path.is_file() else None,
    )
    settings.prefilter = Prefilter(settings.keywords, settings.theory_markers, settings.experiment_markers)
    return settings


def normalize_doi(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    match = _DOI_RE.search(str(raw).strip())
    if not match:
        return None
    doi = match.group(1).rstrip(".,);]>")
    return doi.lower()


def normalize_arxiv_id(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    text = str(raw).strip()
    embedded = re.search(r"arxiv\.org/(?:abs|pdf)/([^?\s#]+)", text, re.IGNORECASE)
    if embedded:
        text = re.sub(r"\.pdf$", "", embedded.group(1), flags=re.IGNORECASE)
    text = re.sub(r"^arxiv:", "", text, flags=re.IGNORECASE).strip()
    # A DOI is not an arXiv id. Old-style ids contain a slash but not "10.".
    if normalize_doi(text) and not re.search(r"arxiv\.org/", str(raw), re.IGNORECASE):
        # Still allow an arXiv id that happens to sit beside a DOI.
        without_doi = _DOI_RE.sub(" ", text)
        text = without_doi.strip() or text
        if normalize_doi(text):
            return None
    new = re.fullmatch(r"(\d{4}\.\d{4,6})(v\d+)?", text, re.IGNORECASE)
    if new:
        return new.group(1)
    old = re.fullmatch(r"([a-z-]+(?:\.[a-z]{2})?/\d{7})(v\d+)?", text, re.IGNORECASE)
    if old:
        return old.group(1).lower()
    found_new = _ARXIV_NEW_RE.search(text)
    if found_new and not normalize_doi(text):
        return found_new.group(1)
    found_old = _ARXIV_OLD_RE.search(text)
    if found_old and not normalize_doi(text):
        return found_old.group(1).lower()
    return None


def normalize_any_id(raw: Optional[str]) -> set[str]:
    keys: set[str] = set()
    doi = normalize_doi(raw)
    if doi:
        keys.add(doi)
    arxiv_id = normalize_arxiv_id(raw)
    if arxiv_id:
        keys.add(arxiv_id)
    return {key for key in keys if key}


def dedup_keys(paper: Mapping[str, Any]) -> set[str]:
    keys: set[str] = set()
    for field_name in ("arxiv_id", "doi", "id", "link"):
        keys |= normalize_any_id(paper.get(field_name))
    return keys


def clean_title(title: str) -> str:
    return re.sub(r"\s+", " ", (title or "").replace("\n", " ")).strip()


def title_tokens(title: str) -> set[str]:
    words = re.findall(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)?", clean_title(title))
    return {word.lower() for word in words if len(word) > 2 and word.lower() not in _TITLE_STOP}


def titles_match(left: str, right: str) -> bool:
    left_tokens = title_tokens(left)
    right_tokens = title_tokens(right)
    if not left_tokens or not right_tokens:
        return False
    if len(left_tokens) < 3 or len(right_tokens) < 3:
        return left_tokens == right_tokens
    overlap = len(left_tokens & right_tokens) / max(len(left_tokens), len(right_tokens))
    return overlap >= 0.85


def parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def compute_window(
    now: datetime,
    last_success: Optional[datetime],
    max_lookback_days: int,
    overlap: timedelta = WINDOW_OVERLAP,
) -> tuple[datetime, datetime]:
    """Return [start, end) covering the time since the last success.

    The start is never older than ``max_lookback_days`` (weekends / missed runs)
    and reaches back a short overlap so clock skew does not drop a paper.
    Dedup keeps the overlap from being sent twice.
    """
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    else:
        now = now.astimezone(timezone.utc)
    lookback_start = now - timedelta(days=max(1, int(max_lookback_days)))
    if last_success is None:
        return lookback_start, now
    if last_success.tzinfo is None:
        last_success = last_success.replace(tzinfo=timezone.utc)
    else:
        last_success = last_success.astimezone(timezone.utc)
    if last_success > now:
        return lookback_start, now
    start = last_success - overlap
    if start < lookback_start:
        start = lookback_start
    if start > now:
        start = lookback_start
    return start, now


def build_arxiv_search_query(
    categories: Iterable[str],
    start: datetime,
    end: datetime,
) -> str:
    cats = [cat for cat in (_sanitize_category(item) for item in categories) if cat]
    if not cats:
        cats = list(DEFAULT_CATEGORIES)
    category_query = " OR ".join(f"cat:{cat}" for cat in cats)
    start_token = start.astimezone(timezone.utc).strftime("%Y%m%d%H%M")
    end_token = end.astimezone(timezone.utc).strftime("%Y%m%d%H%M")
    return f"({category_query}) AND submittedDate:[{start_token} TO {end_token}]"


def _collapse_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def parse_arxiv_feed(xml_text: str) -> list[dict[str, Any]]:
    if not xml_text or not str(xml_text).strip():
        return []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    papers: list[dict[str, Any]] = []
    for entry in root.findall(f"{ATOM_NS}entry"):
        id_text = _collapse_ws("".join((entry.findtext(f"{ATOM_NS}id") or "").split()))
        if not id_text or "api/errors" in id_text or "api/query" in id_text:
            continue
        title = clean_title("".join(entry.find(f"{ATOM_NS}title").itertext())) if entry.find(f"{ATOM_NS}title") is not None else ""
        summary_el = entry.find(f"{ATOM_NS}summary")
        summary = _collapse_ws("".join(summary_el.itertext())) if summary_el is not None else ""
        if not title:
            continue
        arxiv_id = normalize_arxiv_id(id_text)
        if not arxiv_id:
            continue
        authors = []
        for author in entry.findall(f"{ATOM_NS}author"):
            name = _collapse_ws(author.findtext(f"{ATOM_NS}name") or "")
            if name:
                authors.append(name)
        doi = normalize_doi(entry.findtext(f"{ARXIV_NS}doi") or "")
        published_raw = entry.findtext(f"{ATOM_NS}published") or ""
        updated_raw = entry.findtext(f"{ATOM_NS}updated") or ""
        published = parse_iso(updated_raw) or parse_iso(published_raw)
        categories = []
        for category in entry.findall(f"{ATOM_NS}category"):
            term = category.attrib.get("term")
            if term:
                categories.append(term)
        papers.append({
            "id": arxiv_id,
            "arxiv_id": arxiv_id,
            "doi": doi,
            "title": title,
            "summary": summary,
            "authors": authors,
            "link": f"https://arxiv.org/abs/{arxiv_id}",
            "source": "arxiv",
            "published": published.isoformat() if published else None,
            "categories": categories,
        })
    return papers


def empty_state() -> dict[str, Any]:
    return {
        "sent_ids": [],
        "sent_titles": [],
        "pending": [],
        "last_successful_run": None,
    }


def _normalize_state(data: Mapping[str, Any]) -> dict[str, Any]:
    sent: set[str] = set()
    raw_ids = data.get("sent_ids") or []
    if isinstance(raw_ids, dict):
        raw_ids = list(raw_ids.keys())
    for raw in raw_ids:
        sent |= normalize_any_id(str(raw))
    titles: list[str] = []
    seen_titles: set[str] = set()
    for title in data.get("sent_titles") or []:
        cleaned = clean_title(str(title))
        key = cleaned.lower()
        if cleaned and key not in seen_titles:
            seen_titles.add(key)
            titles.append(cleaned)
    pending = []
    for item in data.get("pending") or []:
        if not isinstance(item, dict):
            continue
        record = {
            "arxiv_id": normalize_arxiv_id(item.get("arxiv_id")),
            "doi": normalize_doi(item.get("doi")),
            "source": item.get("source") or "arxiv",
            "attempts": int(item.get("attempts") or 1),
        }
        if record["arxiv_id"] or record["doi"]:
            pending.append(record)
    last_run = data.get("last_successful_run")
    if last_run:
        parsed = parse_iso(str(last_run))
        last_run = parsed.isoformat() if parsed else None
    else:
        last_run = None
    return {
        "sent_ids": sorted(sent),
        "sent_titles": titles[-MAX_STORED_TITLES:],
        "pending": pending,
        "last_successful_run": last_run,
    }


def load_state(path: os.PathLike[str] | str, legacy_path: Optional[os.PathLike[str] | str] = None) -> dict[str, Any]:
    state_path = Path(path)
    if state_path.is_file():
        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"digest state is not valid JSON: {state_path.name}") from exc
        if isinstance(data, list):
            data = {"sent_ids": data}
        if not isinstance(data, dict):
            raise ValueError(f"digest state must be a JSON object: {state_path.name}")
        return _normalize_state(data)
    if legacy_path and Path(legacy_path).is_file():
        try:
            legacy = json.loads(Path(legacy_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            legacy = []
        if isinstance(legacy, dict):
            legacy_ids = legacy.get("sent_ids", [])
        else:
            legacy_ids = legacy
        return _normalize_state({"sent_ids": legacy_ids})
    return empty_state()


def save_state(path: os.PathLike[str] | str, state: Mapping[str, Any]) -> None:
    normalized = _normalize_state(state)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(normalized, ensure_ascii=False, indent=2) + "\n"
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(payload, encoding="utf-8")
    temporary.replace(target)


def is_already_sent(paper: Mapping[str, Any], state: Mapping[str, Any]) -> bool:
    sent_ids = set(state.get("sent_ids") or [])
    if dedup_keys(paper) & sent_ids:
        return True
    title = paper.get("title") or ""
    for previous in state.get("sent_titles") or []:
        if titles_match(title, previous):
            return True
    return False


def remember_sent(state: dict[str, Any], papers: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    sent = set(state.get("sent_ids") or [])
    titles = list(state.get("sent_titles") or [])
    known = {clean_title(title).lower() for title in titles}
    for paper in papers:
        sent |= dedup_keys(paper)
        title = clean_title(paper.get("title") or "")
        if title and title.lower() not in known:
            titles.append(title)
            known.add(title.lower())
    state["sent_ids"] = sorted(sent)
    state["sent_titles"] = titles[-MAX_STORED_TITLES:]
    sent_now = set(state["sent_ids"])
    state["pending"] = [
        item
        for item in state.get("pending") or []
        if not ({item.get("arxiv_id"), item.get("doi")} - {None}) & sent_now
    ]
    return state


def pending_keys(record: Mapping[str, Any]) -> set[str]:
    return {key for key in (record.get("arxiv_id"), record.get("doi")) if key}


def update_pending(
    previous: Iterable[Mapping[str, Any]],
    failed_papers: Iterable[Mapping[str, Any]],
    sent_keys: Iterable[str],
    dropped_keys: Iterable[str] = (),
    max_attempts: int = MAX_PENDING_ATTEMPTS,
) -> list[dict[str, Any]]:
    """Keep papers whose screening failed so the next run can retry them by id."""
    blocked = set(sent_keys) | set(dropped_keys)
    kept: dict[tuple[Optional[str], Optional[str]], dict[str, Any]] = {}
    for item in previous:
        arxiv_id = normalize_arxiv_id(item.get("arxiv_id"))
        doi = normalize_doi(item.get("doi"))
        if not arxiv_id and not doi:
            continue
        if {arxiv_id, doi} & blocked:
            continue
        kept[(arxiv_id, doi)] = {
            "arxiv_id": arxiv_id,
            "doi": doi,
            "source": item.get("source") or ("arxiv" if arxiv_id else "jpsj"),
            "attempts": int(item.get("attempts") or 1),
        }
    for paper in failed_papers:
        arxiv_id = normalize_arxiv_id(paper.get("arxiv_id"))
        doi = normalize_doi(paper.get("doi"))
        if not arxiv_id and not doi:
            continue
        if {arxiv_id, doi} & set(sent_keys):
            continue
        current = kept.get((arxiv_id, doi))
        attempts = (current["attempts"] if current else 0) + 1
        if attempts >= max_attempts:
            kept.pop((arxiv_id, doi), None)
            continue
        kept[(arxiv_id, doi)] = {
            "arxiv_id": arxiv_id,
            "doi": doi,
            "source": paper.get("source") or ("arxiv" if arxiv_id else "jpsj"),
            "attempts": attempts,
        }
    return list(kept.values())


def extract_json(text: str) -> Any:
    if not text:
        return None
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```\s*$", "", cleaned)
    decoder = json.JSONDecoder()

    def _try(blob: str) -> Any:
        for index, char in enumerate(blob):
            if char not in "[{":
                continue
            try:
                value, _end = decoder.raw_decode(blob[index:])
            except json.JSONDecodeError:
                continue
            return value
        return None

    parsed = _try(cleaned)
    if parsed is not None:
        return parsed
    relaxed = re.sub(r",\s*([}\]])", r"\1", cleaned)
    return _try(relaxed)


def _parse_bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "yes", "y", "1"}:
            return True
        if lowered in {"false", "no", "n", "0"}:
            return False
    return None


def _parse_score(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return max(0.0, min(10.0, float(value)))
    match = re.search(r"\d+(?:\.\d+)?", str(value))
    if not match:
        return None
    return max(0.0, min(10.0, float(match.group(0))))


def _parse_optional_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"", "null", "none", "n/a", "na", "unknown", "未给出", "无"}:
            return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"-?\d+(?:\.\d+)?", str(value))
    if not match:
        return None
    return float(match.group(0))


def _optional_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = _collapse_ws(str(value))
    if not text or text.lower() in {"null", "none", "n/a", "na", "unknown"}:
        return None
    return text


def _has_chinese(text: str, minimum: int) -> bool:
    return len(re.findall(r"[\u4e00-\u9fff]", text or "")) >= minimum


def normalize_sample_form(value: Any) -> str:
    text = _collapse_ws(str(value or "")).lower()
    if any(token in text for token in ("单晶", "single crystal", "single-crystal")):
        return "single crystal"
    if any(token in text for token in ("多晶", "polycrystal", "polycrystalline", "ceramic", "powder")):
        return "polycrystal"
    if any(token in text for token in ("薄膜", "thin film", "film")):
        return "film"
    if text in {"", "none", "n/a", "na", "null", "unknown", "无", "无样品"}:
        return "none"
    return "none"


def normalize_measurements(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        parts = re.split(r"[,;、/]| and ", value)
        return [part.strip() for part in parts if part.strip()]
    if isinstance(value, list):
        return [_collapse_ws(str(item)) for item in value if _collapse_ws(str(item))]
    return []


def normalize_screening_object(raw: Mapping[str, Any]) -> Optional[dict[str, Any]]:
    if not isinstance(raw, Mapping):
        return None
    experimental = _parse_bool(raw.get("experimental"))
    score = _parse_score(raw.get("score"))
    highlight = _collapse_ws(str(raw.get("highlight_zh") or ""))
    summary = _collapse_ws(str(raw.get("summary_zh") or ""))
    if experimental is None or score is None:
        return None
    if not _has_chinese(highlight, 4) or not _has_chinese(summary, 8):
        return None
    index = raw.get("index")
    if isinstance(index, str) and index.strip().lstrip("-").isdigit():
        index = int(index.strip())
    elif isinstance(index, float) and index.is_integer():
        index = int(index)
    elif not isinstance(index, int):
        index = None
    growth = _collapse_ws(str(raw.get("growth_method") or "")) or "unknown"
    return {
        "index": index,
        "id": _collapse_ws(str(raw.get("id") or "")) or None,
        "experimental": experimental,
        "sample_form": normalize_sample_form(raw.get("sample_form")),
        "growth_method": growth,
        "measurements": normalize_measurements(raw.get("measurements")),
        "theta_CW": _optional_text(raw.get("theta_CW", raw.get("theta_cw"))),
        "T_order": _optional_text(raw.get("T_order", raw.get("t_order"))),
        "frustration_index": _parse_optional_float(
            raw.get("frustration_index", raw.get("f"))
        ),
        "room_temperature_relevant": bool(_parse_bool(raw.get("room_temperature_relevant"))),
        "meets_measured_data": _parse_bool(raw.get("meets_measured_data")),
        "meets_physics_highlight": _parse_bool(raw.get("meets_physics_highlight")),
        "meets_feasible": _parse_bool(raw.get("meets_feasible")),
        "thin_film_only_mbe": bool(_parse_bool(raw.get("thin_film_only_mbe"))),
        "score": score,
        "highlight_zh": highlight,
        "summary_zh": summary,
    }


def _coerce_screening_items(payload: Any, expected: int) -> Optional[list[Any]]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("papers", "results", "items", "data"):
            if isinstance(payload.get(key), list):
                return payload[key]
        if expected == 1 and "experimental" in payload:
            return [payload]
    return None


def align_screening(raw_items: list[Any], expected: int, ids: Optional[list[str]] = None) -> Optional[list[Optional[dict[str, Any]]]]:
    normalized = [normalize_screening_object(item) if isinstance(item, Mapping) else None for item in raw_items]
    valid = [item for item in normalized if item]
    if not valid:
        return None
    aligned: list[Optional[dict[str, Any]]] = [None] * expected
    ids = ids or []
    used: set[int] = set()
    by_id = {}
    for item in valid:
        if item.get("id"):
            by_id[item["id"]] = item
            arxiv_id = normalize_arxiv_id(item["id"])
            doi = normalize_doi(item["id"])
            if arxiv_id:
                by_id[arxiv_id] = item
            if doi:
                by_id[doi] = item
    if ids and by_id:
        for position, paper_id in enumerate(ids):
            match = by_id.get(paper_id) or by_id.get(normalize_arxiv_id(paper_id) or "") or by_id.get(normalize_doi(paper_id) or "")
            if match is not None:
                aligned[position] = match
                used.add(id(match))
    indexes = [item["index"] for item in valid if item.get("index") is not None]
    index_base = 0
    if indexes and min(indexes) >= 1 and max(indexes) == expected:
        index_base = 1
    for item in valid:
        if id(item) in used or item.get("index") is None:
            continue
        position = item["index"] - index_base
        if 0 <= position < expected and aligned[position] is None:
            aligned[position] = item
            used.add(id(item))
    if all(slot is None for slot in aligned) and len(valid) == expected:
        return valid
    if any(slot is not None for slot in aligned):
        return aligned
    return None


def parse_screening_batch(text: str, expected: int, ids: Optional[list[str]] = None) -> Optional[list[Optional[dict[str, Any]]]]:
    payload = extract_json(text)
    items = _coerce_screening_items(payload, expected)
    if items is None:
        return None
    return align_screening(items, expected, ids)


def _blob(*parts: Any) -> str:
    return " ".join(str(part or "") for part in parts).lower()


def is_thin_film_only_mbe(sample_form: str, growth_method: str, flagged: bool = False) -> bool:
    if flagged:
        return True
    if sample_form != "film":
        return False
    return bool(_MBE_RE.search(growth_method or ""))


def _derive_measured(screening: Mapping[str, Any], paper: Mapping[str, Any]) -> bool:
    explicit = screening.get("meets_measured_data")
    if explicit is not None:
        return bool(explicit)
    if not screening.get("experimental"):
        return False
    text = _blob(screening.get("measurements"), paper.get("summary"), screening.get("summary_zh"))
    return any(hint in text for hint in _MEASUREMENT_HINTS)


def _derive_physics(screening: Mapping[str, Any], paper: Mapping[str, Any]) -> bool:
    explicit = screening.get("meets_physics_highlight")
    if explicit is not None:
        return bool(explicit)
    frustration = screening.get("frustration_index")
    if isinstance(frustration, (int, float)) and abs(float(frustration)) >= 10:
        return True
    if screening.get("room_temperature_relevant"):
        return True
    text = _blob(screening.get("highlight_zh"), screening.get("summary_zh"), paper.get("summary"))
    return any(hint in text for hint in _PHYSICS_HINTS)


def _derive_feasible(screening: Mapping[str, Any], paper: Mapping[str, Any]) -> bool:
    text = _blob(paper.get("title"), paper.get("summary"), screening.get("growth_method"))
    if _TOXIC_RE.search(text):
        return False
    explicit = screening.get("meets_feasible")
    if explicit is not None:
        return bool(explicit)
    return bool(_GROWTH_HINTS.search(text))


def decide_screening(paper: Mapping[str, Any], screening: Mapping[str, Any]) -> dict[str, Any]:
    """Pass experimental papers with real samples and relevant physics.

    Growth feasibility is recorded but does not block a pass: an unclear
    recipe still belongs on the card. Pure theory and thin-film-only MBE
    stay hard-dropped. A score of at least PASS_SCORE counts as relevant
    when the model withheld the physics flag.
    """
    sample_form = screening.get("sample_form") or "none"
    growth = screening.get("growth_method") or "unknown"
    hard_drop = (not screening.get("experimental")) or is_thin_film_only_mbe(
        sample_form,
        growth,
        bool(screening.get("thin_film_only_mbe")),
    )
    meets_measured = _derive_measured(screening, paper)
    meets_physics = _derive_physics(screening, paper)
    meets_feasible = _derive_feasible(screening, paper)
    toxic = bool(_TOXIC_RE.search(_blob(
        paper.get("title"), paper.get("summary"), growth,
    )))
    if toxic:
        meets_feasible = False
    score = float(screening.get("score") or 0)
    has_samples = bool(meets_measured) or bool(screening.get("measurements")) or sample_form in {
        "single crystal",
        "polycrystal",
    }
    relevant = bool(meets_physics) or bool(screening.get("room_temperature_relevant")) or score >= PASS_SCORE
    passes = bool(
        screening.get("experimental")
        and not hard_drop
        and not toxic
        and has_samples
        and relevant
    )
    merged = dict(paper)
    merged.update({
        "experimental": bool(screening.get("experimental")),
        "sample_form": sample_form,
        "growth_method": growth,
        "measurements": list(screening.get("measurements") or []),
        "theta_CW": screening.get("theta_CW"),
        "T_order": screening.get("T_order"),
        "frustration_index": screening.get("frustration_index"),
        "room_temperature_relevant": bool(screening.get("room_temperature_relevant")),
        "meets_measured_data": meets_measured,
        "meets_physics_highlight": meets_physics,
        "meets_feasible": meets_feasible,
        "thin_film_only_mbe": bool(screening.get("thin_film_only_mbe")) or is_thin_film_only_mbe(sample_form, growth),
        "score": float(screening.get("score") or 0),
        "highlight_zh": screening.get("highlight_zh") or "",
        "summary_zh": screening.get("summary_zh") or "",
        "hard_drop": hard_drop,
        "passes": passes,
        "screen_error": False,
    })
    return merged


def _rank_key(paper: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        -float(paper.get("score") or 0),
        0 if paper.get("room_temperature_relevant") else 1,
        clean_title(paper.get("title") or "").lower(),
    )


def _identity(paper: Mapping[str, Any]) -> tuple[str, ...]:
    keys = tuple(sorted(dedup_keys(paper)))
    if keys:
        return keys
    return ("title", clean_title(paper.get("title") or "").lower())


def _mark_selected(paper: Mapping[str, Any], backup: bool) -> dict[str, Any]:
    item = dict(paper)
    item["backup"] = backup
    item["backup_label"] = BACKUP_LABEL if backup else ""
    return item


def _llm_scored(paper: Mapping[str, Any]) -> bool:
    if paper.get("keyword_only"):
        return False
    return bool(paper.get("llm_scored", True))


def _scores_close(previous: Mapping[str, Any], nxt: Mapping[str, Any]) -> bool:
    """Ties and near-ties only compare papers ranked on the same scale."""
    if _llm_scored(previous) != _llm_scored(nxt):
        return False
    if _llm_scored(previous):
        return abs(float(previous.get("score") or 0) - float(nxt.get("score") or 0)) <= CLOSE_SCORE_GAP
    return abs(int(previous.get("keyword_score") or 0) - int(nxt.get("keyword_score") or 0)) <= 1


def select_papers(
    screened: Iterable[Mapping[str, Any]],
    max_papers: int = DEFAULT_MAX_PAPERS,
    min_papers: int = DEFAULT_MIN_PAPERS,
    unscored: Iterable[Mapping[str, Any]] = (),
    topup_limit: int = TOPUP_LIMIT,
) -> tuple[list[dict[str, Any]], bool]:
    """Pick the day's card.

    Papers that pass are kept, capped at ``max_papers``. If fewer than
    ``min_papers`` pass, top up toward that floor, and allow one extra up to
    ``topup_limit`` when the next score is tied or close. Top-up order is
    LLM runner-ups by score, then keyword-prefiltered papers that were not
    scored. Hard-dropped theory and MBE films are never used. Top-ups are
    labeled 备选. Returns ``(selected, send_empty_note)``.
    """
    max_papers, min_papers = _clamp_limits(max_papers, min_papers)
    topup_cap = min(max_papers, max(min_papers, int(topup_limit)))
    eligible = [
        dict(paper)
        for paper in screened
        if not paper.get("hard_drop") and not paper.get("screen_error")
    ]
    qualifying = [paper for paper in eligible if paper.get("passes")]
    runner_ups = [paper for paper in eligible if not paper.get("passes")]
    qualifying.sort(key=_rank_key)
    runner_ups.sort(key=_rank_key)
    keyword_pool = [dict(paper) for paper in unscored if not paper.get("hard_drop")]
    keyword_pool.sort(key=lambda paper: (
        -int(paper.get("keyword_score") or 0),
        clean_title(paper.get("title") or "").lower(),
    ))
    selected: list[dict[str, Any]] = []
    seen: set[tuple[str, ...]] = set()
    for paper in qualifying[:max_papers]:
        selected.append(_mark_selected(paper, backup=False))
        seen.add(_identity(paper))
    if len(selected) >= min_papers:
        if not selected:
            return [], True
        return selected, False

    pool: list[dict[str, Any]] = []
    for paper in [*runner_ups, *keyword_pool]:
        identity = _identity(paper)
        if identity in seen:
            continue
        seen.add(identity)
        pool.append(paper)

    while pool and len(selected) < min_papers:
        selected.append(_mark_selected(pool.pop(0), backup=True))
    while pool and len(selected) < topup_cap and _scores_close(selected[-1], pool[0]):
        selected.append(_mark_selected(pool.pop(0), backup=True))
    if not selected:
        return [], True
    return selected, False


def short_authors(authors: Iterable[str], limit: int = DEFAULT_MAX_AUTHORS) -> str:
    names = [clean_title(author) for author in authors if clean_title(author)]
    if not names:
        return "作者未知"
    limit = max(1, limit)
    if len(names) <= limit:
        return ", ".join(names)
    return ", ".join(names[:limit]) + " et al."


def format_score(score: Any) -> str:
    try:
        value = float(score)
    except (TypeError, ValueError):
        return "?"
    if abs(value - round(value)) < 1e-6:
        return str(int(round(value)))
    return f"{value:.1f}"


def _md_text(text: str) -> str:
    cleaned = _collapse_ws(text)
    return cleaned.replace("[", "［").replace("]", "］")


_SAMPLE_ZH = {
    "single crystal": "单晶",
    "polycrystal": "多晶",
    "film": "薄膜",
    "none": "样品形式未说明",
}


def growth_display(growth_method: Optional[str]) -> str:
    text = _collapse_ws(growth_method or "")
    if not text or text.lower() in {"unknown", "none", "n/a", "未说明"}:
        return "未说明"
    return text


def paper_link(paper: Mapping[str, Any]) -> str:
    arxiv_id = normalize_arxiv_id(paper.get("arxiv_id") or paper.get("link") or paper.get("id"))
    if arxiv_id:
        return f"https://arxiv.org/abs/{arxiv_id}"
    doi = normalize_doi(paper.get("doi") or paper.get("id") or paper.get("link"))
    if doi:
        return f"https://doi.org/{doi}"
    link = paper.get("link") or ""
    if isinstance(link, str) and link.startswith(("http://", "https://")):
        return link
    return ""


def _paper_markdown(paper: Mapping[str, Any], authors_limit: int, number: int) -> str:
    title = _md_text(paper.get("title") or "无题名")
    link = paper_link(paper)
    title_line = f"[{title}]({link})" if link else title
    lines = [f"**#{number}**"]
    scored = _llm_scored(paper) and paper.get("score") is not None
    if paper.get("backup"):
        if scored:
            lines.append(f"**{BACKUP_LABEL}** · 评分 {format_score(paper.get('score'))}/10")
        else:
            lines.append(f"**{BACKUP_LABEL}**")
    meta = []
    if scored:
        meta.append(f"评分 {format_score(paper.get('score'))}/10")
    meta.extend([
        _SAMPLE_ZH.get(paper.get("sample_form") or "", paper.get("sample_form") or ""),
        f"生长：{growth_display(paper.get('growth_method'))}",
    ])
    if paper.get("frustration_index") is not None:
        meta.append(f"f={format_score(paper.get('frustration_index'))}")
    if paper.get("T_order"):
        meta.append(f"T={_md_text(str(paper['T_order']))}")
    if paper.get("room_temperature_relevant"):
        meta.append("室温附近")
    source = "arXiv" if paper.get("source") == "arxiv" else "JPSJ" if paper.get("source") == "jpsj" else (paper.get("source") or "")
    if source:
        meta.append(source)
    lines.append(title_line)
    lines.append(short_authors(paper.get("authors") or [], authors_limit))
    lines.append(" · ".join(part for part in meta if part))
    highlight = _md_text(paper.get("highlight_zh") or "")
    summary = _md_text(paper.get("summary_zh") or "")
    if highlight:
        lines.append(f"**亮点** {highlight}")
    if summary:
        lines.append(summary)
    elif paper.get("keyword_only"):
        lines.append("（未调用模型，仅凭关键词入选）")
    return "\n".join(lines)


def build_feishu_card(
    papers: list[Mapping[str, Any]],
    *,
    generated_at: datetime,
    window_start: datetime,
    window_end: datetime,
    empty_note: bool = False,
    max_authors: int = DEFAULT_MAX_AUTHORS,
    stats: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    if generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=timezone.utc)
    local = generated_at.astimezone(timezone(timedelta(hours=8)))
    date_label = local.strftime("%Y-%m-%d")
    qualifying = [paper for paper in papers if not paper.get("backup")]
    backup_count = len(papers) - len(qualifying)
    if empty_note or not papers:
        title = f"实验凝聚态日报 · {date_label}"
        template = "grey"
        body = f"**{EMPTY_NOTE}**\n{EMPTY_NOTE_ZH}"
        elements: list[dict[str, Any]] = [
            {"tag": "div", "text": {"tag": "lark_md", "content": body}}
        ]
    else:
        title = f"实验凝聚态日报 · {date_label}"
        template = "orange" if qualifying == [] else "blue"
        elements = []
        for index, paper in enumerate(papers, start=1):
            if index > 1:
                elements.append({"tag": "hr"})
            elements.append({
                "tag": "div",
                "text": {"tag": "lark_md", "content": _paper_markdown(paper, max_authors, index)},
            })
        elements.append({"tag": "hr"})
        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": DISCUSSION_FOOTER},
        })
    window_label = (
        f"{window_start.astimezone(timezone(timedelta(hours=8))).strftime('%m-%d %H:%M')}"
        f" – {window_end.astimezone(timezone(timedelta(hours=8))).strftime('%m-%d %H:%M')} 北京时间"
    )
    note = f"达标 {len(qualifying)} 篇，备选 {backup_count} 篇。窗口 {window_label}。"
    if stats and stats.get("prefiltered") is not None:
        note += f" 预筛 {stats['prefiltered']} 篇。"
    elements.append({
        "tag": "note",
        "elements": [{"tag": "plain_text", "content": note}],
    })
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": template,
        },
        "elements": elements,
    }


def feishu_sign(timestamp: str, secret: str) -> str:
    """Feishu/Lark custom-bot signature. The key is ``timestamp + newline + secret``."""
    import base64

    string_to_sign = f"{timestamp}\n{secret}"
    digest = hmac.new(string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def build_feishu_payload(card: Mapping[str, Any], secret: str = "", timestamp: Optional[str] = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"msg_type": "interactive", "card": card}
    if secret:
        stamp = str(timestamp if timestamp is not None else int(datetime.now(timezone.utc).timestamp()))
        payload["timestamp"] = stamp
        payload["sign"] = feishu_sign(stamp, secret)
    return payload


def sign_results_body(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def build_results_request(payload: Mapping[str, Any], secret: str) -> tuple[bytes, dict[str, str]]:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "X-Signature": sign_results_body(body, secret),
    }
    return body, headers


def public_paper(paper: Mapping[str, Any]) -> dict[str, Any]:
    """JSON-safe paper record for digest.json. Drops datetime objects."""
    published = paper.get("published")
    if isinstance(published, datetime):
        published = published.isoformat()
    arxiv_id = normalize_arxiv_id(paper.get("arxiv_id") or paper.get("link") or paper.get("id"))
    doi = normalize_doi(paper.get("doi") or paper.get("id") or paper.get("link"))
    return {
        "id": arxiv_id or doi or paper.get("id"),
        "arxiv_id": arxiv_id,
        "doi": doi,
        "title": paper.get("title"),
        "authors": list(paper.get("authors") or []),
        "link": paper_link(paper),
        "source": paper.get("source"),
        "published": published,
        "score": paper.get("score"),
        "backup": bool(paper.get("backup")),
        "backup_label": paper.get("backup_label") or "",
        "passes": bool(paper.get("passes")),
        "experimental": paper.get("experimental"),
        "sample_form": paper.get("sample_form"),
        "growth_method": growth_display(paper.get("growth_method")) if paper.get("growth_method") else None,
        "measurements": list(paper.get("measurements") or []),
        "theta_CW": paper.get("theta_CW"),
        "T_order": paper.get("T_order"),
        "frustration_index": paper.get("frustration_index"),
        "room_temperature_relevant": bool(paper.get("room_temperature_relevant")),
        "highlight_zh": paper.get("highlight_zh"),
        "summary_zh": paper.get("summary_zh"),
    }


def build_digest(
    papers: list[Mapping[str, Any]],
    *,
    generated_at: datetime,
    window_start: datetime,
    window_end: datetime,
    empty_note: bool,
    dry_run: bool,
    stats: Mapping[str, Any],
) -> dict[str, Any]:
    local = generated_at.astimezone(timezone(timedelta(hours=8)))
    return {
        "generated_at": local.isoformat(),
        "window_start": window_start.astimezone(timezone.utc).isoformat(),
        "window_end": window_end.astimezone(timezone.utc).isoformat(),
        "dry_run": dry_run,
        "empty": empty_note or not papers,
        "note": EMPTY_NOTE if empty_note or not papers else None,
        "stats": dict(stats),
        "papers": [public_paper(paper) for paper in papers],
    }


def dedupe_papers(papers: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Collapse same-day duplicates (arXiv id, DOI, or near-identical title)."""
    kept: list[dict[str, Any]] = []
    for paper in papers:
        current = dict(paper)
        match_at = None
        for index, existing in enumerate(kept):
            same_id = bool(dedup_keys(existing) & dedup_keys(current))
            same_title = titles_match(existing.get("title") or "", current.get("title") or "")
            if same_id or same_title:
                match_at = index
                break
        if match_at is None:
            kept.append(current)
            continue
        kept[match_at] = _merge_paper(kept[match_at], current)
    return kept


def _merge_paper(left: Mapping[str, Any], right: Mapping[str, Any]) -> dict[str, Any]:
    left_summary = left.get("summary") or ""
    right_summary = right.get("summary") or ""
    if len(left_summary) >= len(right_summary):
        primary = dict(left)
        other = right
    else:
        primary = dict(right)
        other = left
    for field_name in ("arxiv_id", "doi"):
        if not primary.get(field_name) and other.get(field_name):
            primary[field_name] = other[field_name]
    if not primary.get("authors") and other.get("authors"):
        primary["authors"] = list(other["authors"])
    if primary.get("arxiv_id"):
        primary["link"] = f"https://arxiv.org/abs/{primary['arxiv_id']}"
        primary["id"] = primary["arxiv_id"]
        primary["source"] = primary.get("source") or "arxiv"
    elif primary.get("doi"):
        primary["link"] = primary.get("link") or f"https://doi.org/{normalize_doi(primary['doi'])}"
    return primary
