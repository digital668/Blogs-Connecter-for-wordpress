"""End-to-end tests against fake WordPress sites, fake competitors and a mocked Claude API.

Run:  pip install pytest responses && pytest -q
"""
import json
import re
from datetime import date, timedelta

import pytest
import responses

from seo_platform import cli, competitors, config, formats, humanize, llm, originality, planner, rankmath, report, safety, scheduler
from seo_platform.config import Site

BASE = "https://example-isp.com"
DIVI = "https://divi-site.com"
LOREM = ("Fiber internet sends data as light through glass strands which keeps speeds steady even "
         "when the whole street is online in the evening and that is why upload speed matters ")


def page_html(title, h1, links=(), desc="A helpful description of this page for searchers in the US.", alt=True):
    a = "".join(f'<a href="{l}">link</a>' for l in links)
    img = '<img src="x.jpg" alt="pic">' if alt else '<img src="x.jpg">'
    return (f"<html><head><title>{title}</title><meta name='description' content='{desc}'>"
            f"<link rel='canonical' href='{BASE}/'></head><body><h1>{h1}</h1>{img}<p>{LOREM * 20}</p>{a}</body></html>")


def posts_for(base):
    return [
        {"id": 1, "link": f"{base}/fiber-vs-cable/", "slug": "fiber-vs-cable", "title": {"rendered": "Fiber vs Cable Internet Compared"},
         "content": {"rendered": "<p>" + LOREM * 30 + "</p>"}, "date": "2026-01-01", "modified": "2026-01-02"},
        {"id": 2, "link": f"{base}/cheap-plans/", "slug": "cheap-plans", "title": {"rendered": "Cheap Internet Plans for Students"},
         "content": {"rendered": "<p>Short post about cheap plans.</p>"}, "date": "2026-02-01", "modified": "2026-02-01"},
    ]


@pytest.fixture
def env(tmp_path, monkeypatch):
    data = tmp_path / "data"
    for mod in (config, originality, report, cli):
        if hasattr(mod, "DATA_DIR"):
            monkeypatch.setattr(mod, "DATA_DIR", data)
    monkeypatch.setattr(report, "DASHBOARD_DATA", tmp_path / "docs" / "data")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "index.html").write_text("<script>window.EMBED = null;</script>")
    monkeypatch.setenv("WP_PASS_TEST_SITE", "abcd efgh ijkl")
    monkeypatch.setenv("WP_PASS_DIVI_SITE", "abcd efgh ijkl")
    monkeypatch.setenv("PEXELS_API_KEY", "test")
    monkeypatch.delenv("AUTOPILOT_PAUSED", raising=False)
    common = dict(wp_user="bot", niche="US home internet", audience="US households", seed_keywords=["fiber internet"],
                  competitors=["comp1.com"], interval_days=2, review_days=3, min_words=300, group="us-internet")
    site = Site(key="test-site", name="Example ISP", url=BASE, **common)
    divi = Site(key="divi-site", name="Divi ISP", url=DIVI, **common)
    sites = [site, divi]
    monkeypatch.setattr(cli, "load_sites", lambda **kw: sites)
    monkeypatch.setattr(config, "all_domains", lambda: {"example-isp.com", "divi-site.com", "kaizenklub.in"})
    monkeypatch.setattr(safety, "all_domains", lambda: {"example-isp.com", "divi-site.com", "kaizenklub.in"})
    return sites, tmp_path


def fake_wp(rs, base, created, divi=False):
    api = base + "/wp-json/wp/v2"
    rs.add(responses.GET, f"{api}/users/me", json={"name": "bot", "roles": ["administrator"],
           "capabilities": {"publish_posts": True, "upload_files": True, "unfiltered_html": not divi}})
    rs.add(responses.GET, re.compile(rf"{re.escape(api)}/posts\?.*status=publish%2Cfuture.*"),
           json=[{"id": 9, "meta": {"rank_math_focus_keyword": "old keyword"}}], headers={"X-WP-TotalPages": "1"})
    rs.add(responses.GET, f"{api}/posts", json=posts_for(base), headers={"X-WP-TotalPages": "1"})
    rs.add(responses.GET, f"{api}/pages", json=[{"id": 10, "link": f"{base}/", "slug": "home", "title": {"rendered": "Home"},
           "content": {"rendered": "<p>Welcome</p>"}}], headers={"X-WP-TotalPages": "1"})
    home = '<link rel="stylesheet" href="/wp-content/themes/Divi/style.css">' if divi else '<div class="elementor-section"></div>'
    rs.add(responses.GET, f"{base}/", body=page_html("Example ISP — Compare Home Internet Providers", "Home",
           links=[f"{base}/fiber-vs-cable/"]) + home, content_type="text/html")
    rs.add(responses.GET, f"{base}/wp-json/", json={"namespaces": ["wp/v2"] + ([] if divi else ["elementor/v1"])})
    rs.add(responses.GET, f"{base}/fiber-vs-cable/", body=page_html("Fiber vs Cable Internet Compared: Which Is Better?", "Fiber vs Cable",
           links=[f"{base}/cheap-plans/", f"{base}/missing-page/"]), content_type="text/html")
    rs.add(responses.GET, f"{base}/cheap-plans/", body=page_html("Cheap", "Cheap plans", alt=False, desc=""), content_type="text/html")
    rs.add(responses.GET, f"{base}/missing-page/", status=404)
    rs.add(responses.GET, f"{base}/robots.txt", body="User-agent: *")
    rs.add(responses.GET, f"{base}/wp-sitemap.xml", body="<xml/>")
    rs.add(responses.GET, f"{base}/sitemap_index.xml", status=404)
    rs.add(responses.GET, re.compile(rf"{re.escape(api)}/(categories|tags)\?.*"), json=[])
    rs.add_callback(responses.POST, re.compile(rf"{re.escape(api)}/(categories|tags)$"),
                    callback=lambda r: (201, {}, json.dumps({"id": 99})))
    rs.add_callback(responses.POST, f"{api}/media",
                    callback=lambda r: (201, {}, json.dumps({"id": 77, "source_url": f"{base}/wp-content/uploads/fiber.jpg",
                                                             "media_details": {"width": 1200, "height": 800}})))
    rs.add(responses.POST, f"{api}/media/77", json={"id": 77})

    def create_post(req):
        body = json.loads(req.body)
        body["_site"] = base
        created.append(body)
        pid = 500 + len(created)
        return 201, {}, json.dumps({"id": pid, "link": f"{base}/?p={pid}", "status": body["status"]})
    rs.add_callback(responses.POST, f"{api}/posts", callback=create_post)


def fake_world(rs):
    rs.add(responses.GET, re.compile(r"https://suggestqueries\.google\.com/.*"),
           json=["q", ["fiber internet installation cost", "is fiber internet worth it", "fiber internet vs 5g home internet"]])
    rs.add(responses.GET, re.compile(r"https://api\.pexels\.com/.*"), json={"photos": [
        {"src": {"large": "https://images.pexels.com/photo1.jpg"}, "photographer": "Ann", "url": "https://pexels.com/p/1"},
        {"src": {"large": "https://images.pexels.com/photo2.jpg"}, "photographer": "Bo", "url": "https://pexels.com/p/2"}]})
    rs.add(responses.GET, re.compile(r"https://images\.pexels\.com/.*"), body=b"\xff\xd8jpegdata", content_type="image/jpeg")
    for c in ("comp1.com", "comp2.com"):
        rs.add(responses.GET, f"https://{c}/robots.txt", body="User-agent: *\nDisallow: /private/")
        comp_page = ("<html><head><title>Fiber cost guide</title><script type='application/ld+json'>{\"@type\":\"FAQPage\"}</script></head>"
                     "<body><article><h2>Installation fees</h2><h2>Monthly price</h2><table><tr><td>x</td></tr></table>"
                     "<p>" + ("installation fee router rental data caps contract length " * 60) + "</p></article></body></html>")
        rs.add(responses.GET, f"https://{c}/fiber-cost", body=comp_page, content_type="text/html")
    rs.add(responses.GET, "https://comp1.com/sitemap_index.xml",
           body="<sitemapindex><sitemap><loc>https://comp1.com/post-sitemap.xml</loc></sitemap></sitemapindex>")
    rs.add(responses.GET, "https://comp1.com/post-sitemap.xml",
           body="<urlset><url><loc>https://comp1.com/fiber-cost</loc></url><url><loc>https://comp1.com/how-to-set-up-mesh-wifi-at-home</loc></url></urlset>")
    for path in ("/wp-sitemap.xml", "/sitemap.xml"):
        rs.add(responses.GET, "https://comp1.com" + path, status=404)


DIVI_PARA = [
    "Wondering whether {kw}? It usually is for busy homes, though a few situations make cable the smarter call.",
    "Begin by listing who uses the connection and when. Evenings are the real test for any line.",
    "Gamers notice lower lag first. Remote workers notice uploads, since big files and calls go both ways all day.",
    "The national <a href='https://broadbandmap.fcc.gov/home'>FCC coverage map</a> shows which networks reach each address.",
    "Price matters less than people think. Reliability during storms and outages matters more over a year.",
    "See how <a href='{base}/fiber-vs-cable/'>fiber stacks up against cable</a> on raw speed.",
    "Ask neighbours too. Their real bills and outage stories beat any advert you'll read online.",
    "Our partner <a href='https://kaizenklub.in/gym'>training page</a> has nothing to do with this.",
]
DIVI_FILL = ["Keep notes as you go. They help at renewal time.",
             "When deciding if {kw}, count every phone, laptop, television and camera that connects at home.",
             "A smart doorbell or two security cameras can quietly upload video around the clock without anyone noticing.",
             "Switching takes an afternoon. Staying on a slow plan costs you every single evening."]


def human_body(kw, base, alt_para=None, alt_fill=None):
    """A realistic draft: keyword placement, varied sentences, links (incl. one to a sister site + one untrusted)."""
    para = [
        f"Thinking about {kw}? Here's the short answer. Most homes pay less than they expect, but a few fees catch people out every year.",
        "Start with the quote. Ask for the full price in writing, including the router, the setup visit and any activation charge.",
        "Then compare it with the plan you have today. A cheaper monthly bill can hide a bigger upfront payment, so look at the total over two years.",
        "Check the official <a href='https://broadbandmap.fcc.gov/home'>FCC broadband map</a> to see who actually serves your street.",
        "Short contracts cost more each month. Longer ones lock you in. Pick the one that matches how long you'll stay.",
        f"Our guide to <a href='{base}/fiber-vs-cable/'>fiber vs cable internet</a> explains the speed side of the decision.",
        "Some providers waive the visit when you order online. Others don't. Ask before you book, because the answer changes the maths.",
        "Read our sister site <a href='https://kaizenklub.in/gym'>gym page</a> and <a href='https://random-blog.biz/x'>this blog</a>.",
    ]
    filler = ["Write the numbers down. You'll want them later.",
              f"When you weigh {kw}, think about the whole household rather than one person's habits and screens.",
              "Kids streaming, parents on video calls and a games console all pull on the same line at once in the evening.",
              "Small things add up. A rented router at ten dollars a month is a hundred and twenty a year."]
    if alt_para:
        para = [x.format(kw=kw, base=base) for x in alt_para]
        filler = [x.format(kw=kw) for x in alt_fill]
    html = f"<p>{para[0]} {para[1]}</p>"
    for i, h in enumerate([f"What drives {kw}", "Fees to ask about before you sign", f"Is {kw} worth paying for your home?", "Quick checklist"]):
        html += f"<h2>{h}</h2>"
        for j in range(4):
            html += f"<p>{para[(i * 2 + j) % len(para)]} {filler[(i + j) % len(filler)]}</p>"
    return html


def fake_llm(*, tool_name, prompt, **kw):
    usage = {"input_tokens": 1000, "output_tokens": 2000, "searches": 2 if kw.get("web_search") else 0}
    if tool_name == "submit_plan":
        if "Divi ISP" in prompt:
            return {"topics": [{"focus_keyword": "is fiber internet worth it", "working_title": "Is Fiber Internet Worth It?",
                                "angle": "x", "search_intent": "informational", "secondary_keywords": []}]}, usage
        return {"topics": [{"focus_keyword": "fiber internet installation cost", "working_title": "Fiber Internet Installation Cost",
                            "angle": "Real fees", "search_intent": "commercial", "secondary_keywords": []}]}, usage
    if tool_name == "submit_brief":
        return {"search_intent": "commercial", "outline": ["What you pay"], "people_also_ask": ["Is install free?"],
                "gaps_to_beat_them": ["fees by provider"], "recommended_words": 300,
                "top_ranking_urls": ["https://comp1.com/fiber-cost", "https://comp2.com/fiber-cost"],
                "key_facts": [{"fact": "Coverage", "source_url": "https://www.fcc.gov/consumers"}]}, usage
    if tool_name == "submit_article":
        kwd = re.search(r"Focus keyword: (.+)", prompt).group(1).strip()
        base = DIVI if "Divi ISP" in prompt else BASE
        body = human_body(kwd, base) if base == BASE else human_body(kwd, base, DIVI_PARA, DIVI_FILL)
        return {"title": f"{kwd.title()}: What You'll Really Pay", "slug": kwd,
                "meta_title": f"{kwd.title()}: 7 Proven Ways to Save",
                "meta_description": "What it really costs, which fees you can skip, and how to plan the switch without surprises.",
                "excerpt": "Short summary.", "content_html": "<h1>Dup</h1>" + body,
                "faq": [{"question": "Is install free?", "answer": "Sometimes, when you order online."}],
                "cta": {"title": "Check plans", "text": "See what's available at your address.", "button": "Check now", "url": base + "/"},
                "tags": ["fiber"], "category": "Guides"}, usage
    if tool_name == "submit_edit":
        body = re.search(r"DRAFT:\n(.*)", prompt, re.S).group(1)
        return {"content_html": body.replace("Here's the short answer.", "Moreover, here's the short answer.")}, usage
    if tool_name == "submit_fix":
        return {"meta_title": "unchanged", "content_html": re.search(r"BODY HTML:\n(.*)", prompt, re.S).group(1)}, usage
    raise AssertionError(tool_name)


class A:
    site = None


@responses.activate
def test_daily_pipeline_end_to_end(env, monkeypatch):
    sites, root = env
    created = []
    fake_wp(responses, BASE, created)
    fake_wp(responses, DIVI, created, divi=True)
    fake_world(responses)
    monkeypatch.setattr(llm, "structured", fake_llm)
    monkeypatch.setattr(cli, "WORKERS", 1)

    assert cli.cmd_test(sites, A) == {}
    assert cli.cmd_audit(sites, A) == {}
    gaps = json.loads((sites[0].data_dir / "competitor_gaps.json").read_text())
    assert any("mesh wifi" in g["keyword"] for g in gaps)
    assert cli.cmd_research(sites, A) == {}
    assert cli.cmd_write(sites, A) == {}

    assert len(created) == 2
    by_site = {p["_site"]: p for p in created}
    gb, dv = by_site[BASE], by_site[DIVI]

    # Elementor/Gutenberg site → clean HTML, scheduled, featured image, FAQ schema (admin user)
    assert gb["status"] == "future" and gb["featured_media"] == 77
    assert "[et_pb_" not in gb["content"] and "FAQPage" in gb["content"] and "<h1" not in gb["content"]
    assert "kaizenklub.in" not in gb["content"] and "random-blog.biz" not in gb["content"]   # network + untrusted links removed
    assert "broadbandmap.fcc.gov" in gb["content"]
    assert "Moreover" not in gb["content"] and "Dup" not in gb["content"]
    assert "fiber internet installation cost" in gb["meta"]["rank_math_title"].lower()   # bad revised title rejected                     # AI phrase scrubbed after the humanize pass
    assert "table-of-contents" in gb["content"]                # TOC added for Rank Math
    assert 'alt="Fiber internet installation cost' in gb["content"]
    # Divi site → native Divi modules, builder flag on, no <script> (user can't post scripts)
    assert dv["content"].startswith("[et_pb_section") and "[et_pb_toggle" in dv["content"] and "[et_pb_cta" in dv["content"]
    assert dv["meta"]["_et_pb_use_builder"] == "on" and "<script" not in dv["content"]
    assert "[et_pb_image src=\"https://divi-site.com/wp-content/uploads/fiber.jpg\"" in dv["content"] and "<figure" not in dv["content"]

    for s in sites:
        h = json.loads((s.data_dir / "history.json").read_text())[-1]
        assert h["rankmath_score"] >= 80, h["rankmath_failed"]
        assert h["humanize"]["ai_phrases_left"] == []
        assert h["originality"]["ok"]
        assert h["geo_score"] >= 80, h["geo_failed"]
    # the repetitive, number-light Divi test article is held back by the quality gate
    hd = json.loads((sites[1].data_dir / "history.json").read_text())[-1]
    ht = json.loads((sites[0].data_dir / "history.json").read_text())[-1]
    assert ht["status"] == "future" and ht["quality"]["overall"] >= 60
    assert hd["status"] == "draft" and any("Content quality" in r for r in hd["review_reasons"])
    # sister sites in the same group got different keywords
    kws = [json.loads((s.data_dir / "history.json").read_text())[-1]["keyword"] for s in sites]
    assert len(set(kws)) == 2

    report.build(sites)
    out = json.loads((root / "docs" / "data" / "all.json").read_text())
    assert {s["builder"] for s in out["sites"]} == {"elementor", "divi"}


def test_scheduler_one_blog_every_two_days(env):
    sites, _ = env
    s = sites[0]
    start = date(2026, 11, 2)
    due_days = [d for d in range(10) if scheduler.is_due(s, start + timedelta(days=d))[0]]
    assert len(due_days) == 5 and all(b - a == 2 for a, b in zip(due_days, due_days[1:]))
    # after writing today, not due tomorrow
    planner.save_history(s, [{"written_at": f"{start}T05:00:00+00:00", "keyword": "x", "title": "x"}])
    assert not scheduler.is_due(s, start + timedelta(days=1))[0]
    assert scheduler.is_due(s, start + timedelta(days=2))[0]
    # different sites are staggered across days
    offs = {scheduler.offset(Site(key=f"site-{i}", name="", url="https://x.com", wp_user="", interval_days=2)) for i in range(10)}
    assert offs == {0, 1}


def test_traffic_drop_pauses_site(env):
    sites, _ = env
    s = sites[0]
    days = [{"date": f"2026-10-{d:02d}", "clicks": 100 if d <= 14 else 40, "impressions": 1000} for d in range(1, 22)]
    (s.data_dir / "gsc.json").write_text(json.dumps({"daily": days, "queries": []}))
    assert safety.traffic_drop(s)[0]
    s.pause_on_traffic_drop = 0
    assert not safety.traffic_drop(s)[0]


def test_ymyl_always_draft(env):
    sites, _ = env
    s = sites[0]
    s.ymyl = True
    mode, reasons = safety.gate(s, {"rankmath": {"score": 100, "failed": {}}, "humanize": {"ok": True}})
    assert mode == "draft" and "YMYL" in reasons[0]


def test_rankmath_checker_and_autofix(env):
    sites, _ = env
    s = sites[0]
    a = {"focus_keyword": "fiber internet", "title": "Guide", "meta_title": "Everything a busy household should know about fiber internet",
         "meta_description": "fiber internet guide", "slug": "a-very-long-slug-that-does-not-have-the-keyword-in-it-at-all-really",
         "content_html": "<p>" + "word " * 200 + "</p>"}
    r = rankmath.analyse(s, a, set())
    assert {"kw_in_url", "kw_at_start", "short_paragraphs", "media", "title_number"} <= set(r["failed"])
    a = rankmath.autofix(s, a, ["https://www.fcc.gov/x"], set())
    f = a["rankmath"]["failed"]
    assert "kw_in_url" not in f and "short_paragraphs" not in f and "external_links" not in f and "title_number" not in f
    assert a["meta_title"].lower().startswith("fiber internet")


def test_humanize_scrub_keeps_links():
    html, n = humanize.scrub('<p>In today\'s fast-paced world, it is crucial to <a href="https://x.gov">leverage</a> fiber. Moreover, delve into it.</p>')
    assert n >= 4 and 'href="https://x.gov"' in html and "crucial" not in html and html.startswith("<p>It is key")


def test_divi_escapes_brackets():
    out = formats.to_divi("<p>Price [promo] here</p>", [{"question": 'Is "it" free?', "answer": "[yes]"}], None)
    assert "[promo]" not in out and "&#91;yes&#93;" in out and 'title="Is &quot;it&quot; free?"' in out


def test_originality_flags_copied_text(env):
    from seo_platform.textutil import fingerprint, html_to_text
    src = "<p>" + " ".join(f"token{i}" for i in range(300)) + "</p>"
    res = originality.check(src, extra=[("https://other-site.com/post", set(fingerprint(html_to_text(src))))])
    assert not res["ok"] and res["closest"] == "https://other-site.com/post"


def test_competitor_robots_respected():
    with responses.RequestsMock() as rs:
        rs.add(responses.GET, "https://blocked.com/robots.txt", body="User-agent: *\nDisallow: /")
        assert competitors._get("https://blocked.com/page") == ""


def test_sites_yaml_loads():
    sites = config.load_sites(include_disabled=True)
    assert sites and all(s.secret_name.startswith("WP_PASS_") for s in sites)


# ── features adapted from AgriciDaniel/claude-seo ──────────────
def test_invisible_characters_stripped():
    from seo_platform.quality import strip_invisible
    txt, n = strip_invisible("Fiber​ internet costs﻿ less \U000E0041here 👨‍👩")
    assert n == 4 and txt == "Fiber internet costs less here 👨‍👩"   # emoji joiner kept


def test_content_quality_flags_filler():
    from seo_platform.quality import content_quality
    filler = "<p>" + ("In today's fast-paced world, it's important to note that when it comes to internet, "
                      "at the end of the day, moreover, needless to say, let's dive in. ") * 15 + "</p>"
    good = "<p>" + " ".join(f"Comcast charged $89 in 2025 for install {i}, while AT&T Fiber waived it in Dallas." for i in range(40)) + "</p>"
    assert content_quality(filler)["overall"] < 60 and "filler-heavy" in content_quality(filler)["flags"]
    assert content_quality(good)["overall"] > content_quality(filler)["overall"]


def test_geo_readiness():
    from seo_platform.quality import geo_readiness
    weak = "<h2>Background</h2><p>" + "word " * 150 + "</p><h2>More</h2><p>" + "word " * 150 + "</p>"
    strong = ("<p>Fiber install usually costs $0–$100.</p><h2>How much does fiber installation cost?</h2>"
              "<p>Most providers charge $0 to $100; many waive it online.</p><ul><li>a</li></ul>"
              "<h2>Is it worth paying for?</h2><p>Yes for homes with 3+ heavy users, per <a href='https://fcc.gov'>FCC</a> data 2025, 10, 20.</p>")
    assert geo_readiness(weak, "fiber install")["score"] < geo_readiness(strong, "fiber install")["score"] == 100


def test_indexcheck_marks_indexed(env, monkeypatch):
    sites, _ = env
    s = sites[0]
    s.gsc_property = "sc-domain:example-isp.com"
    monkeypatch.delenv("WP_PASS_TEST_SITE")
    planner.save_history(s, [{"post_id": 5, "link": BASE + "/a/", "scheduled_for": "2026-10-01T10:00:00+00:00", "keyword": "a", "title": "a"},
                             {"post_id": 6, "link": BASE + "/b/", "scheduled_for": "2099-01-01T10:00:00+00:00", "keyword": "b", "title": "b"}])
    from seo_platform import gsc
    monkeypatch.setattr(gsc, "inspect", lambda site, url: {"verdict": "PASS", "coverage": "Submitted and indexed"})
    assert cli.cmd_indexcheck([s], A) == {}
    h = planner.history(s)
    assert h[0]["index"]["verdict"] == "PASS" and "index" not in h[1]


def test_connector_key_uses_header_not_password(monkeypatch):
    from seo_platform.wordpress import WordPress
    monkeypatch.setenv("WP_PASS_CONN", "sap_abc123")
    wp = WordPress(Site(key="conn", name="c", url="https://x.com"))
    assert wp.uses_connector and wp.s.headers["X-SEO-Autopilot-Key"] == "sap_abc123" and wp.s.auth is None
    monkeypatch.setenv("WP_PASS_APP", "abcd efgh")
    wp2 = WordPress(Site(key="app", name="a", url="https://x.com", wp_user="bob"))
    assert not wp2.uses_connector and wp2.s.auth == ("bob", "abcdefgh")


# ── add websites from an issue form + control-panel commands ─────────
FORM = """### Website address

https://www.globelconnect.in/

### Target country

US — United States

### Publishing

Drafts — I review and publish them myself (recommended to start)

### How often

Every 3 days

### Sensitive topics

- [ ] This site gives health, medical, finance or legal advice (every post will wait for my approval)

### Competitors (optional)

leadgenco.com, https://www.bpo-rival.com/

### Special instructions (optional)

Never promise lead volumes.
"""


@pytest.fixture
def sitesfile(tmp_path, monkeypatch):
    f = tmp_path / "sites.yaml"
    f.write_text("defaults:\n  language: English\nsites:\n")
    monkeypatch.setattr(config, "SITES_FILE", f)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    sent = []
    from seo_platform import github_ops as gh
    monkeypatch.setattr(gh, "comment", lambda n, b: sent.append((n, b)))
    monkeypatch.setattr(gh, "add_labels", lambda n, l: None)
    monkeypatch.setattr(gh, "set_title", lambda n, t: None)
    monkeypatch.setattr(gh, "close", lambda n: sent.append((n, "CLOSED")))
    return f, sent


def test_parse_issue_form():
    from seo_platform.issues import parse_form
    f = parse_form(FORM)
    assert f["url"] == "https://www.globelconnect.in/" and f["country"] == "US" and f["publish_mode"] == "draft"
    assert f["interval_days"] == 3 and f["ymyl"] is False and "leadgenco.com" in f["competitors"]
    assert "Never promise" in f["notes"]


def test_add_website_from_issue(sitesfile, monkeypatch):
    f, sent = sitesfile
    from seo_platform import onboard, issues
    monkeypatch.setattr(onboard, "gather", lambda url: {"title": "Globel Connect", "wordpress": True, "builder": "divi",
                                                       "categories": [{"name": "Uncategorized", "posts": 1}, {"name": "Blogs", "posts": 5}],
                                                       "pages": [{"url": url + "/contact-us/", "title": "Contact"}]})
    monkeypatch.setattr(onboard, "profile", lambda url, info, form: ({
        "name": "Globel Connect", "niche": "Lead generation and BPO", "audience": "US SMB owners", "tone": "B2B",
        "country": "IN", "seed_keywords": ["healthcare lead generation", "data entry outsourcing"],
        "competitors": ["www.rival-two.com", "globelconnect.in"], "ymyl": False,
        "service_pages": ["https://globelconnect.in/data-entry-services/", "https://evil.com/x"],
        "contact_url": "https://globelconnect.in/contact-us/", "cta_title": "Talk to us", "cta_text": "Get a plan",
        "cta_button": "Contact", "writing_rules": ["No invented results"]}, {}))
    issues.handle_new_issue(7, FORM)
    sites = config.load_sites(include_disabled=True)
    s = sites[0]
    assert s.key == "globelconnect" and s.url == "https://www.globelconnect.in" and s.issue == 7
    assert s.country == "US" and s.interval_days == 3 and s.builder == "divi" and s.default_category == "Blogs"
    assert s.competitors == ["leadgenco.com", "bpo-rival.com", "rival-two.com"]     # own domain dropped
    assert "evil.com" not in s.writing_rules and "data-entry-services" in s.writing_rules and "Never promise" in s.writing_rules
    reply = sent[-1][1]
    assert "WP_PASS_GLOBELCONNECT" in reply and "/test" in reply and "Lead generation and BPO" in reply
    # adding the same site again links instead of duplicating
    issues.handle_new_issue(9, FORM.replace("https://www.globelconnect.in/", "globelconnect.in"))
    assert len(config.load_sites(include_disabled=True)) == 1 and config.load_sites()[0].issue == 9
    assert "already set up" in sent[-1][1]


def test_issue_commands(sitesfile):
    f, sent = sitesfile
    from seo_platform import issues
    raw = config.read_raw()
    raw["sites"].append({"key": "kk", "name": "KK", "url": "https://kk.in", "issue": 3, "seed_keywords": ["a"]})
    config.save_raw(raw)
    issues.handle_command(3, "/pause")
    assert config.load_sites(include_disabled=True)[0].enabled is False and "Paused" in sent[-1][1]
    issues.handle_command(3, "/resume")
    issues.handle_command(3, "/mode schedule")
    issues.handle_command(3, "/every 4")
    issues.handle_command(3, "/set keywords gym in koramangala, mma classes")
    issues.handle_command(3, "/set rules - line one\n- line two")
    s = config.load_sites()[0]
    assert s.enabled and s.publish_mode == "schedule" and s.interval_days == 4
    assert s.seed_keywords == ["gym in koramangala", "mma classes"]
    issues.handle_command(3, "/every 99")
    assert "1–30" in sent[-1][1]
    issues.handle_command(5, "/test")
    assert "isn't linked" in sent[-1][1]
    issues.handle_command(3, "/help")
    assert "/write" in sent[-1][1]
    issues.handle_command(3, "/remove")
    assert config.load_sites(include_disabled=True) == [] and sent[-1][1] == "CLOSED"
    # file stays valid YAML with the header
    assert f.read_text().startswith("# ─") and "sites:" in f.read_text()
