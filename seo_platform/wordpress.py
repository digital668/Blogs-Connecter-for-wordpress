"""Minimal WordPress REST API client. Authenticates with the SEO Autopilot Connector plugin key
(sap_…), or a WordPress Application Password as a fallback."""
from __future__ import annotations

import time
from typing import Any, Iterator

import requests

from .config import Site

UA = "SEO-Autopilot/1.0 (+https://github.com)"


class WordPressError(RuntimeError):
    pass


class WordPress:
    def __init__(self, site: Site, timeout: int = 30):
        self.site = site
        self.api = f"{site.base}/wp-json/wp/v2"
        self.s = requests.Session()
        self.s.headers["User-Agent"] = UA
        pw = (site.wp_password or "").strip()
        self.uses_connector = pw.startswith("sap_")
        if self.uses_connector:
            # SEO Autopilot Connector plugin key — limited permissions, no WordPress user password involved
            self.s.headers["X-SEO-Autopilot-Key"] = pw
        elif pw:
            # WordPress Application Password (fallback)
            self.s.auth = (site.wp_user, pw.replace(" ", ""))
        self.timeout = timeout

    # ── low level ──────────────────────────────────────────
    def _req(self, method: str, path: str, **kw) -> requests.Response:
        url = path if path.startswith("http") else f"{self.api}{path}"
        for attempt in range(3):
            r = self.s.request(method, url, timeout=self.timeout, **kw)
            if r.status_code in (429, 502, 503, 504) and attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue
            if r.status_code >= 400:
                msg = r.text[:300]
                try:
                    msg = r.json().get("message", msg)
                except ValueError:
                    pass
                raise WordPressError(f"{self.site.key}: {method} {url} → {r.status_code}: {msg}")
            return r
        raise WordPressError(f"{self.site.key}: {method} {url} failed after retries")

    def _paged(self, path: str, params: dict | None = None) -> Iterator[dict]:
        params = {"per_page": 100, **(params or {})}
        page = 1
        while True:
            r = self._req("GET", path, params={**params, "page": page})
            items = r.json()
            yield from items
            total_pages = int(r.headers.get("X-WP-TotalPages", 1))
            if page >= total_pages or not items:
                break
            page += 1

    # ── checks ────────────────────────────────────────────
    def test_connection(self) -> dict:
        me = self._req("GET", "/users/me", params={"context": "edit"}).json()
        caps = me.get("capabilities", {}) or {}
        return {
            "via": "connector plugin" if self.uses_connector else "application password",
            "user": me.get("name"),
            "can_publish": bool(caps.get("publish_posts")),
            "can_upload": bool(caps.get("upload_files")),
            "unfiltered_html": bool(caps.get("unfiltered_html")),
            "roles": me.get("roles", []),
        }

    # ── content ───────────────────────────────────────────
    def posts(self, status: str = "publish") -> list[dict]:
        fields = "id,link,slug,date,modified,status,title,content,excerpt,categories,tags"
        params = {"status": status, "_fields": fields}
        if status != "publish":
            params["context"] = "edit"
        return list(self._paged("/posts", params))

    def pages(self) -> list[dict]:
        return list(self._paged("/pages", {"_fields": "id,link,slug,date,modified,title,content"}))

    def post(self, post_id: int) -> dict:
        return self._req("GET", f"/posts/{post_id}", params={"context": "edit", "_fields": "id,link,status"}).json()

    def scheduled_posts(self) -> list[dict]:
        return list(self._paged("/posts", {"status": "future", "context": "edit",
                                           "_fields": "id,link,slug,date,title,status"}))

    def term_id(self, taxonomy: str, name: str) -> int:
        """Return the ID of a category/tag, creating it if missing."""
        found = self._req("GET", f"/{taxonomy}", params={"search": name, "per_page": 100}).json()
        for t in found:
            if t["name"].strip().lower() == name.strip().lower():
                return t["id"]
        return self._req("POST", f"/{taxonomy}", json={"name": name}).json()["id"]

    def upload_media(self, data: bytes, filename: str, ctype: str, *, alt: str,
                     caption: str, title: str) -> dict:
        r = self._req("POST", "/media", data=data, headers={
            "Content-Type": ctype, "Content-Disposition": f'attachment; filename="{filename}"'})
        media = r.json()
        self._req("POST", f"/media/{media['id']}", json={"alt_text": alt, "caption": caption, "title": title})
        return media

    def homepage_html(self) -> str:
        try:
            return self.s.get(self.site.base + "/", timeout=self.timeout).text
        except requests.RequestException:
            return ""

    def namespaces(self) -> list[str]:
        try:
            return self.s.get(f"{self.site.base}/wp-json/", timeout=self.timeout).json().get("namespaces", [])
        except (requests.RequestException, ValueError):
            return []

    def used_focus_keywords(self) -> set[str]:
        """Rank Math focus keywords already used on the site (needs the helper mu-plugin)."""
        out = set()
        try:
            for p in self._paged("/posts", {"status": "publish,future,draft", "context": "edit",
                                            "_fields": "id,meta"}):
                kw = ((p.get("meta") or {}).get("rank_math_focus_keyword") or "").split(",")[0].strip().lower()
                if kw:
                    out.add(kw)
        except WordPressError:
            pass
        return out

    def create_post(self, *, title: str, content: str, slug: str, excerpt: str,
                    status: str, date_gmt: str | None, categories: list[str],
                    tags: list[str], meta_title: str, meta_description: str,
                    focus_keyword: str, featured_media: int | None = None,
                    extra_meta: dict | None = None) -> dict:
        payload: dict[str, Any] = {
            "title": title,
            "content": content,
            "slug": slug,
            "excerpt": excerpt,
            "status": status,
            "categories": [self.term_id("categories", c) for c in categories if c],
            "tags": [self.term_id("tags", t) for t in tags if t],
        }
        if date_gmt:
            payload["date_gmt"] = date_gmt
        if self.site.author_id:
            payload["author"] = self.site.author_id
        if featured_media:
            payload["featured_media"] = featured_media
        # Yoast + Rank Math meta. These only stick if the plugin exposes them to the
        # REST API (see README → "SEO plugin fields"). Unknown keys are ignored by WP.
        payload["meta"] = {
            "_yoast_wpseo_title": meta_title,
            "_yoast_wpseo_metadesc": meta_description,
            "_yoast_wpseo_focuskw": focus_keyword,
            "rank_math_title": meta_title,
            "rank_math_description": meta_description,
            "rank_math_focus_keyword": focus_keyword,
            **(extra_meta or {}),
        }
        try:
            return self._req("POST", "/posts", json=payload).json()
        except WordPressError as e:
            if "meta" in str(e).lower():
                payload.pop("meta")
                return self._req("POST", "/posts", json=payload).json()
            raise
