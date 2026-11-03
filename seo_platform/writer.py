"""Researches a topic and writes a WordPress-ready blog post with Claude."""
from __future__ import annotations

import json
import re

from . import llm
from .config import Site
from .planner import existing_titles
from .textutil import html_to_text, slugify, words

BRIEF_SCHEMA = {
    "type": "object",
    "properties": {
        "search_intent": {"type": "string"},
        "top_ranking_urls": {"type": "array", "items": {"type": "string"},
                             "description": "URLs of the pages currently ranking on Google for the keyword, best first"},
        "what_top_results_cover": {"type": "array", "items": {"type": "string"}},
        "gaps_to_beat_them": {"type": "array", "items": {"type": "string"}},
        "people_also_ask": {"type": "array", "items": {"type": "string"}},
        "key_facts": {"type": "array", "items": {"type": "object", "properties": {
            "fact": {"type": "string"}, "source_url": {"type": "string"}}, "required": ["fact"]}},
        "outline": {"type": "array", "items": {"type": "string"}, "description": "H2/H3 headings in order"},
        "recommended_words": {"type": "integer"},
    },
    "required": ["search_intent", "outline", "people_also_ask", "gaps_to_beat_them", "recommended_words"],
}

ARTICLE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "Post title (H1)"},
        "slug": {"type": "string", "description": "Short URL slug containing the focus keyword"},
        "meta_title": {"type": "string", "description": "SEO title ≤ 60 chars: starts with the focus keyword, has a number, a power word and a sentiment word"},
        "meta_description": {"type": "string", "description": "140–155 characters, MUST include the focus keyword"},
        "excerpt": {"type": "string"},
        "content_html": {"type": "string", "description": "Body HTML only: h2, h3, p, ul, ol, li, table, a, strong, em. No h1, no FAQ, no CTA."},
        "faq": {"type": "array", "items": {"type": "object", "properties": {
            "question": {"type": "string"}, "answer": {"type": "string"}}, "required": ["question", "answer"]}},
        "cta": {"type": "object", "properties": {
            "title": {"type": "string"}, "text": {"type": "string"}, "button": {"type": "string"},
            "url": {"type": "string", "description": "One of OUR page URLs (contact, service, check-availability…)"}},
            "required": ["title", "text", "button", "url"]},
        "tags": {"type": "array", "items": {"type": "string"}},
        "category": {"type": "string"},
    },
    "required": ["title", "slug", "meta_title", "meta_description", "excerpt", "content_html", "faq", "tags", "cta"],
}

RANKMATH_RULES = """Rank Math checklist (every item must pass):
- SEO title (meta_title): STARTS with the focus keyword, ≤ 60 chars, contains a number, one power word
  (e.g. proven, simple, essential, complete, quick, expert, ultimate, easy, smart, avoid, mistakes) and a
  positive or negative sentiment word (e.g. best, easy, worth, save, reliable / avoid, mistakes, costly, risk).
- Focus keyword in the slug; slug short (2–5 words).
- Focus keyword in the first 2 sentences, in at least two H2/H3 headings, and spread through the body at
  roughly 1–1.3% density (about once per 90 words) — natural, never stuffed.
- 3–5 internal links to OUR pages listed below; 1–3 external links to authoritative sources
  (government, universities, regulators, major publications) — only real, existing URLs you are confident of.
- Paragraphs under 100 words. Use H2 sections with H3 sub-points.

AI-search readiness (Google AI Overviews, AI Mode, ChatGPT search):
- Answer the reader's main question directly in the first 2–3 sentences of the article.
- Start every H2 section with a 1–3 sentence direct answer (under 60 words) before the detail.
- Phrase at least 2–3 H2/H3 headings as the questions people actually search.
- Use specifics — numbers, names, steps, price ranges with sources — rather than generic statements.
- Where a comparison helps, use a table; for processes, a numbered list."""


def research(site: Site, topic: dict) -> tuple[dict, dict]:
    search_note = ("First use web search to see what currently ranks on Google for the focus keyword "
                   f"in {site.country} (top results, People Also Ask, related searches). Then"
                   if site.use_web_research else "Using your own knowledge,")
    prompt = f"""{search_note} build a content brief for a blog post on {site.name} ({site.url}).

Focus keyword: {topic['focus_keyword']}
Working title: {topic['working_title']}
Angle: {topic.get('angle', '')}
Secondary keywords: {topic.get('secondary_keywords', [])}
Audience: {site.audience}

List the top ranking URLs you found (excluding {site.domain}). Show how to be MORE useful than them
(gaps they miss), include real People-Also-Ask style questions, and list key facts with the source URL
where you found each one (prefer official / authoritative sources)."""
    return llm.structured(system="You are an SEO research analyst.", prompt=prompt,
                          tool_name="submit_brief", schema=BRIEF_SCHEMA,
                          web_search=site.use_web_research, max_searches=4,
                          country=site.country, max_tokens=4000)


def target_words(site: Site, brief: dict, comp: dict) -> int:
    median = (comp.get("summary") or {}).get("median_words") or 0
    return min(3000, max(site.min_words, int(brief.get("recommended_words") or 0), int(median * 1.1)))


def write(site: Site, topic: dict, brief: dict, comp: dict) -> tuple[dict, dict]:
    links = existing_titles(site)[:80]
    target = target_words(site, brief, comp)
    comp_summary = comp.get("summary") or {"note": "no competitor data"}
    prompt = f"""Write a complete blog post for {site.name} ({site.url}).

Niche: {site.niche}
Audience: {site.audience}
Tone: {site.tone}
Language: {site.language}  (country: {site.country})

Focus keyword: {topic['focus_keyword']}
Secondary keywords: {topic.get('secondary_keywords', [])}
Working title: {topic['working_title']}
Angle: {topic.get('angle', '')}

CONTENT BRIEF:
{json.dumps(brief, indent=1)}

COMPETITOR ANALYSIS (statistics from the pages ranking now — use it to cover the topic more completely,
NEVER to copy wording or structure from any one page):
{json.dumps(comp_summary, indent=1)[:6000]}

OUR EXISTING PAGES (for internal links and the CTA url):
{json.dumps(links, indent=0)}

{RANKMATH_RULES}

Writing requirements:
- {target}–{target + 500} words of genuinely useful content: specific steps, real examples, comparisons,
  and a table if most competitors use one. Cover the competitor terms that fit naturally.
- 100% original wording written from scratch. No generic filler, no "In today's world", "delve",
  "In conclusion", "it's important to note".
- Write like an experienced practitioner talking to {site.audience}: contractions, "you", varied sentence length.
- Only state facts you're confident in. Any statistic, price or study must link to its source.
  If unsure, describe it qualitatively instead of inventing a number.
- FAQ: 4–6 real questions from the brief → `faq` field only (not in content_html).
- CTA: short, relevant call-to-action → `cta` field, url must be one of OUR pages.
- content_html: body only, no <h1>, no markdown.
{("SITE RULES (must follow, they override anything above):" + chr(10) + site.writing_rules) if site.writing_rules else ""}"""
    article, usage = llm.structured(system="You are an expert SEO copywriter who writes original, genuinely helpful articles.",
                                    prompt=prompt, tool_name="submit_article", schema=ARTICLE_SCHEMA,
                                    max_tokens=16000)
    return finalize(site, topic, article), usage


REVISE_SCHEMA = {"type": "object", "properties": {
    "meta_title": {"type": "string"}, "content_html": {"type": "string"}}, "required": ["meta_title", "content_html"]}


def revise(site: Site, article: dict, failed: dict) -> tuple[dict, dict]:
    prompt = f"""This blog post for {site.name} fails these Rank Math / AI-search checks:
{json.dumps(list(failed.values()), indent=1)}

Focus keyword: "{article['focus_keyword']}"
Current SEO title: {article['meta_title']}

Fix ONLY what is needed to pass them, with minimal edits. Keep everything else (links, tables, headings,
facts, tone) unchanged. Keep keyword use natural. Return the SEO title and the full body HTML.

{RANKMATH_RULES}

BODY HTML:
{article['content_html']}"""
    out, usage = llm.structured(system="You are a careful SEO editor.", prompt=prompt, tool_name="submit_fix",
                                schema=REVISE_SCHEMA, max_tokens=16000)
    new = out.get("content_html") or ""
    if len(html_to_text(new)) >= 0.85 * len(html_to_text(article["content_html"])):
        article["content_html"] = strip_h1(new)
    mt = (out.get("meta_title") or "").strip()
    if mt and article["focus_keyword"].lower() in mt.lower() and len(mt) <= 65:
        article["meta_title"] = mt
    return article, usage


def strip_h1(html: str) -> str:
    """The theme prints the post title as H1 — remove any H1 the model added (tag AND text)."""
    return re.sub(r"<h1[^>]*>.*?</h1>", "", html, flags=re.S | re.I).strip()


def finalize(site: Site, topic: dict, a: dict) -> dict:
    kw = topic["focus_keyword"].strip()
    a["focus_keyword"] = kw
    a["slug"] = slugify(a.get("slug") or kw)
    a["content_html"] = strip_h1(a["content_html"])
    a["meta_title"] = a["meta_title"].strip()[:65]
    md = a["meta_description"].strip()
    if kw.lower() not in md.lower():
        md = f"{kw[0].upper() + kw[1:]}: {md}"
    if len(md) > 160:
        md = md[:157].rsplit(" ", 1)[0] + "…"
    a["meta_description"] = md
    a["category"] = a.get("category") or site.default_category
    if site.cta:
        a["cta"] = dict(site.cta)
    cta = a.get("cta")
    if cta and not site.cta and not site.owns(cta.get("url") or ""):
        cta["url"] = site.base + "/"
    a["word_count"] = len(words(html_to_text(a["content_html"])))
    return a
