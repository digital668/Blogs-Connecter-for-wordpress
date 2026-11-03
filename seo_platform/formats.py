"""Shapes the article for each site's page builder.

- Gutenberg / Elementor sites: clean semantic HTML. WordPress shows it in
  the theme's (or Elementor Pro Theme Builder's) Single Post template, so
  the blog automatically picks up each site's own fonts, colours and
  layout. The post is never flagged as "built with Elementor" — that
  avoids the well-known blank-post problem when Elementor data is empty.
- Divi sites: a native Divi Builder layout (sections, rows, Text modules,
  Toggle modules for the FAQ, a Call-To-Action module) with the builder
  switched on, so the post can be edited visually in Divi and uses the
  site's Divi presets.

FAQ schema (JSON-LD) is only added when the WordPress user is allowed to
post scripts (Administrator). Otherwise WordPress would strip the tags
and show raw code on the page.
"""
from __future__ import annotations

import html as htmllib
import json

from bs4 import BeautifulSoup

from .config import Site


def detect(site: Site, wp) -> str:
    if site.builder != "auto":
        return site.builder
    page = wp.homepage_html().lower()
    if "/themes/divi" in page or "et_pb_" in page or "et-boc" in page or "/themes/extra/" in page:
        return "divi"
    if "elementor" in page or "elementor/v1" in wp.namespaces():
        return "elementor"
    return "gutenberg"


def faq_schema(faq: list[dict]) -> str:
    data = {"@context": "https://schema.org", "@type": "FAQPage", "mainEntity": [
        {"@type": "Question", "name": f["question"],
         "acceptedAnswer": {"@type": "Answer", "text": f["answer"]}} for f in faq]}
    return f'<script type="application/ld+json">{json.dumps(data)}</script>'


def faq_html(faq: list[dict]) -> str:
    if not faq:
        return ""
    parts = ['<h2 id="faq">Frequently Asked Questions</h2>']
    for f in faq:
        parts.append(f"<h3>{htmllib.escape(f['question'])}</h3>\n<p>{htmllib.escape(f['answer'])}</p>")
    return "\n".join(parts)


def _attr(s: str) -> str:
    """Safe value for a Divi shortcode attribute."""
    return htmllib.escape(s, quote=True).replace("[", "&#91;").replace("]", "&#93;")


def _sc_text(s: str) -> str:
    """Square brackets inside module content would be read as shortcodes."""
    return s.replace("[", "&#91;").replace("]", "&#93;")


def _sections(content_html: str) -> list[str]:
    """Split body HTML into chunks that each start at an <h2>."""
    soup = BeautifulSoup(content_html, "lxml")
    nodes = list(soup.body.contents) if soup.body else []
    chunks, cur = [], []
    for n in nodes:
        if getattr(n, "name", None) == "h2" and cur:
            chunks.append("".join(str(x) for x in cur))
            cur = []
        cur.append(n)
    if cur:
        chunks.append("".join(str(x) for x in cur))
    return [c for c in chunks if c.strip()]


def to_divi(content_html: str, faq: list[dict], cta: dict | None) -> str:
    out = ['[et_pb_section fb_built="1" _builder_version="4.27.0"][et_pb_row _builder_version="4.27.0"]'
           '[et_pb_column type="4_4" _builder_version="4.27.0"]']
    for chunk in _sections(content_html):
        if "<figure" in chunk:
            fig_soup = BeautifulSoup(chunk, "lxml")
            fig = fig_soup.find("figure")
            img = fig.find("img") if fig else None
            if img and img.get("src"):
                before, rest = chunk.split("<figure", 1)
                after = rest.split("</figure>", 1)[1] if "</figure>" in rest else ""
                if before.strip():
                    out.append(f'[et_pb_text _builder_version="4.27.0"]{_sc_text(before)}[/et_pb_text]')
                alt = img.get("alt", "")
                out.append(f'[et_pb_image src="{_attr(img["src"])}" alt="{_attr(alt)}" '
                           f'title_text="{_attr(alt)}" _builder_version="4.27.0"][/et_pb_image]')
                chunk = after
        if chunk.strip():
            out.append(f'[et_pb_text _builder_version="4.27.0"]{_sc_text(chunk)}[/et_pb_text]')
    if cta:
        out.append(f'[et_pb_cta title="{_attr(cta["title"])}" button_text="{_attr(cta["button"])}" '
                   f'button_url="{_attr(cta["url"])}" _builder_version="4.27.0"]{_sc_text(cta["text"])}[/et_pb_cta]')
    if faq:
        out.append('[et_pb_text _builder_version="4.27.0"]<h2 id="faq">Frequently Asked Questions</h2>[/et_pb_text]')
        for i, f in enumerate(faq):
            out.append(f'[et_pb_toggle title="{_attr(f["question"])}" open="{"on" if i == 0 else "off"}" '
                       f'_builder_version="4.27.0"]<p>{_sc_text(htmllib.escape(f["answer"]))}</p>[/et_pb_toggle]')
    out.append("[/et_pb_column][/et_pb_row][/et_pb_section]")
    return "\n".join(out)


def cta_html(cta: dict | None) -> str:
    if not cta:
        return ""
    return (f'<div class="seo-autopilot-cta"><h3>{htmllib.escape(cta["title"])}</h3>'
            f'<p>{htmllib.escape(cta["text"])}</p>'
            f'<p><a href="{htmllib.escape(cta["url"])}"><strong>{htmllib.escape(cta["button"])}</strong></a></p></div>')


def render(builder: str, article: dict, allow_scripts: bool) -> tuple[str, dict]:
    """Return (post_content, extra_post_meta) for this builder."""
    faq = article.get("faq", [])
    cta = article.get("cta")
    schema = faq_schema(faq) if (faq and allow_scripts) else ""
    if builder == "divi":
        body = to_divi(article["content_html"], faq, cta)
        if schema:
            body += f'\n[et_pb_section][et_pb_row][et_pb_column type="4_4"][et_pb_code]{schema}[/et_pb_code][/et_pb_column][/et_pb_row][/et_pb_section]'
        return body, {"_et_pb_use_builder": "on", "_et_pb_page_layout": "et_right_sidebar",
                      "_et_pb_old_content": article["content_html"]}
    body = article["content_html"] + "\n" + cta_html(cta) + "\n" + faq_html(faq) + ("\n" + schema if schema else "")
    return body, {}
