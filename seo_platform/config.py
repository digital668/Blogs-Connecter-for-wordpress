"""Loads sites.yaml and secrets from environment variables."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"            # raw per-site state (committed by the workflow)
DASHBOARD_DATA = ROOT / "docs" / "data"  # JSON the dashboard reads
SITES_FILE = ROOT / "sites.yaml"

CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL") or "claude-sonnet-5-5"


def _all_secrets() -> dict:
    """GitHub Actions passes every repo secret as one JSON blob (ALL_SECRETS), so adding
    a new site only needs a new secret — no workflow edits."""
    import json
    try:
        return json.loads(os.environ.get("ALL_SECRETS") or "{}")
    except ValueError:
        return {}


@dataclass
class Site:
    key: str
    name: str
    url: str
    wp_user: str = ""               # only needed for Application Passwords (not with the connector plugin)
    gsc_property: str | None = None
    niche: str = ""
    audience: str = ""
    tone: str = "Helpful and clear"
    language: str = "English"
    country: str = "US"
    seed_keywords: list[str] = field(default_factory=list)
    competitors: list[str] = field(default_factory=list)
    interval_days: int = 2          # one new blog every N days per site
    group: str | None = None        # sites sharing a niche never get the same topic
    builder: str = "auto"           # auto | gutenberg | elementor | divi
    ymyl: bool = False              # money/health/legal site → always draft, human review
    toc_plugin: bool = False        # true if a TOC plugin (Easy TOC, LuckyWP, Rank Math TOC block) auto-inserts one
    images: bool = True             # add free stock images (Pexels / Openverse) with keyword alt text
    external_links_ok: list[str] = field(default_factory=list)  # extra domains allowed as outbound links
    pause_on_traffic_drop: float = 0.35   # auto-pause writing if 7-day clicks fall by this share
    writing_rules: str = ""         # site-specific instructions the writer must follow
    cta: dict | None = None         # fixed call-to-action {title, text, button, url}; else the writer picks one
    issue: int | None = None        # GitHub issue that acts as this site's control panel
    review_days: int = 3
    min_words: int = 1200
    use_web_research: bool = True
    publish_mode: str = "schedule"
    author_id: int | None = None
    default_category: str = "Blog"
    enabled: bool = True

    @property
    def secret_name(self) -> str:
        return "WP_PASS_" + re.sub(r"[^A-Z0-9]", "_", self.key.upper())

    @property
    def wp_password(self) -> str | None:
        return os.environ.get(self.secret_name) or _all_secrets().get(self.secret_name)

    @property
    def domain(self) -> str:
        return re.sub(r"^https?://(www\.)?", "", self.base).split("/")[0].lower()

    @property
    def root_domain(self) -> str:
        """kaizenklub.in for blogs.kaizenklub.in; handles co.in / com.au style suffixes."""
        parts = self.domain.split(".")
        n = 3 if len(parts) >= 3 and parts[-2] in ("co", "com", "org", "net", "gov", "ac", "edu") else 2
        return ".".join(parts[-n:])

    def owns(self, url_or_host: str) -> bool:
        """True for links to this site or any subdomain of the same root (main site ↔ blog)."""
        h = re.sub(r"^https?://", "", url_or_host).split("/")[0].lower()
        h = h[4:] if h.startswith("www.") else h
        return h == self.root_domain or h.endswith("." + self.root_domain)

    @property
    def base(self) -> str:
        return self.url.rstrip("/")

    @property
    def data_dir(self) -> Path:
        d = DATA_DIR / self.key
        d.mkdir(parents=True, exist_ok=True)
        return d


def all_domains() -> set[str]:
    """Every domain in sites.yaml (enabled or not) — used to block cross-linking your own network."""
    return {s.domain for s in load_sites(include_disabled=True)}


def paused() -> bool:
    return os.environ.get("AUTOPILOT_PAUSED", "").strip().lower() in ("1", "true", "yes", "on")


SITES_HEADER = """\
# ─────────────────────────────────────────────────────────────────────────────
#  SEO Autopilot — websites
#  You normally don't edit this by hand: add a website with GitHub → Issues →
#  New issue → "Add a website", and manage it with commands on that issue
#  (/test /write /pause /resume /mode /every /remove /help).
#  Each site's Connector-plugin key lives in a GitHub secret WP_PASS_<KEY>
#  (key upper-cased, dashes → underscores), e.g. kaizen-klub → WP_PASS_KAIZEN_KLUB
# ─────────────────────────────────────────────────────────────────────────────
"""


class _Dumper(yaml.SafeDumper):
    pass


def _str_rep(dumper, data):
    style = "|" if "\n" in data else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


_Dumper.add_representer(str, _str_rep)


def read_raw(path: Path | None = None) -> dict:
    path = path or SITES_FILE
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else None
    raw = raw or {}
    raw.setdefault("defaults", {})
    raw["sites"] = raw.get("sites") or []
    return raw


def save_raw(raw: dict, path: Path | None = None) -> None:
    path = path or SITES_FILE
    body = yaml.dump(raw, Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=110, default_flow_style=False)
    path.write_text(SITES_HEADER + "\n" + body, encoding="utf-8")


def load_sites(path: Path | None = None, only: str | None = None,
               include_disabled: bool = False) -> list[Site]:
    raw = read_raw(path)
    defaults = raw.get("defaults", {}) or {}
    known = set(Site.__dataclass_fields__)
    sites: list[Site] = []
    for entry in raw.get("sites", []) or []:
        merged = {**defaults, **entry}
        unknown = set(merged) - known
        if unknown:
            raise ValueError(f"sites.yaml: unknown field(s) {sorted(unknown)} in site {entry.get('key')}")
        site = Site(**merged)
        if only and site.key != only:
            continue
        if not site.enabled and not include_disabled and not only:
            continue
        sites.append(site)
    for s in sites:
        if s.builder not in ("auto", "gutenberg", "elementor", "divi"):
            raise ValueError(f"sites.yaml: builder for {s.key} must be auto/gutenberg/elementor/divi")
        if s.interval_days < 1:
            raise ValueError(f"sites.yaml: interval_days for {s.key} must be ≥ 1")
    keys = [s.key for s in sites]
    if len(keys) != len(set(keys)):
        raise ValueError("sites.yaml: duplicate site keys")
    return sites
