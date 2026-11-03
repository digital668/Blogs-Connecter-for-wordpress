"""Sends finished articles to WordPress as scheduled posts (or drafts)."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from . import formats
from .config import Site
from .scheduler import publish_time

RECORD_KEYS = ("title", "slug", "focus_keyword", "meta_title", "meta_description", "word_count",
               "rankmath", "humanize", "originality", "review_reasons", "unsourced_claims",
               "links_removed", "builder", "image", "warnings", "competitors_analysed", "geo", "quality")


def publish(site: Site, article: dict, wp, mode: str, builder: str, allow_scripts: bool) -> dict:
    record = {k: article.get(k) for k in RECORD_KEYS}
    record.update(fingerprint=article.get("fingerprint", []),
                  written_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    content, extra_meta = formats.render(builder, article, allow_scripts)

    if mode in ("schedule", "draft") and wp is not None:
        when = publish_time(site) if mode == "schedule" else None
        post = wp.create_post(
            title=article["title"], content=content, slug=article["slug"],
            excerpt=article["excerpt"], status="future" if mode == "schedule" else "draft",
            date_gmt=when.strftime("%Y-%m-%dT%H:%M:%S") if when else None,
            categories=[article.get("category") or site.default_category],
            tags=article.get("tags", [])[:6], meta_title=article["meta_title"],
            meta_description=article["meta_description"], focus_keyword=article["focus_keyword"],
            featured_media=article.get("featured_media"), extra_meta=extra_meta)
        record.update(post_id=post.get("id"), link=post.get("link"), status=post.get("status"),
                      scheduled_for=when.isoformat() if when else None,
                      edit_url=f"{site.base}/wp-admin/post.php?post={post.get('id')}&action=edit")
    else:
        record.update(status="saved-only")

    adir = site.data_dir / "articles"
    adir.mkdir(exist_ok=True)
    (adir / f"{article['slug']}.json").write_text(json.dumps(record, indent=1), encoding="utf-8")
    (adir / f"{article['slug']}.html").write_text(
        f"<!-- SEO title: {article['meta_title']} | Meta: {article['meta_description']} -->\n"
        f"<h1>{article['title']}</h1>\n{content}", encoding="utf-8")
    return record
