"""GitHub Issues as a control panel.

- "Add a website" issue form  → onboard the site, reply with setup steps.
- Comments on that issue      → commands: /test /plan /write /audit /status
                                /pause /resume /mode /every /set /remove /help
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import sys

from . import github_ops as gh
from .config import Site, load_sites, read_raw, save_raw

HELP = """**Commands** — type one as a new comment on this issue:

| Command | What it does |
|---|---|
| `/test` | Check the connection (WordPress plugin, page builder, Search Console) |
| `/plan` | Show the next blog topic it would write |
| `/write` | Write one blog now (draft or scheduled, per this site's mode) |
| `/audit` | Crawl the site and list the top SEO issues |
| `/status` | Show this site's settings and last activity |
| `/pause` · `/resume` | Stop / restart automatic blogs for this site |
| `/mode draft` · `/mode schedule` | Drafts for you to publish, or auto-schedule after the review window |
| `/every 3` | One blog every 3 days (any number 1–30) |
| `/set keywords a, b, c` | Change a setting: `name`, `niche`, `audience`, `tone`, `country`, `keywords`, `competitors`, `category`, `rules`, `review_days`, `gsc` (Search Console property) |
| `/remove` | Remove this website from the platform (its WordPress posts are kept) |
"""

SETTABLE = {"name": "name", "niche": "niche", "audience": "audience", "tone": "tone", "country": "country",
            "keywords": "seed_keywords", "competitors": "competitors", "category": "default_category",
            "rules": "writing_rules", "review_days": "review_days", "gsc": "gsc_property"}


# ── helpers ───────────────────────────────────────────────
class _Tee(io.StringIO):
    def write(self, s):
        sys.__stdout__.write(s)
        return super().write(s)


@contextlib.contextmanager
def captured():
    buf = _Tee()
    with contextlib.redirect_stdout(buf):
        yield buf


def parse_form(body: str) -> dict:
    """GitHub issue forms render as '### Label\\n\\nvalue' blocks."""
    fields = {}
    for m in re.finditer(r"^###\s+(.+?)\s*\n+(.*?)(?=^###\s|\Z)", body or "", re.S | re.M):
        val = m.group(2).strip()
        fields[m.group(1).strip().lower()] = "" if val in ("_No response_", "None") else val
    get = lambda *names: next((fields[n] for n in fields for k in names if k in n), "")
    country = get("country")
    mode = get("publish")
    every = re.search(r"\d+", get("how often") or "")
    return {
        "url": get("website", "address", "url"),
        "country": "" if not country or "auto" in country.lower() else country.split()[0][:2].upper(),
        "publish_mode": "schedule" if "schedul" in mode.lower() else "draft",
        "interval_days": int(every.group()) if every else 2,
        "ymyl": "[x]" in get("sensitive").lower(),
        "competitors": get("competitor"),
        "notes": get("instruction", "notes"),
    }


def site_for_issue(num: int) -> dict | None:
    return next((e for e in read_raw()["sites"] if e.get("issue") == num), None)


def secret_name(key: str) -> str:
    return "WP_PASS_" + re.sub(r"[^A-Z0-9]", "_", key.upper())


def summary_table(e: dict) -> str:
    rows = [("Niche", e.get("niche")), ("Audience", e.get("audience")), ("Tone", e.get("tone")),
            ("Market", e.get("country")), ("Keywords", ", ".join(e.get("seed_keywords", []))),
            ("Competitors", ", ".join(e.get("competitors", [])) or "—"),
            ("Page builder", e.get("builder", "auto") + (" (auto-detect on /test)" if e.get("builder", "auto") == "auto" else "")),
            ("Blog category", e.get("default_category", "Blog")),
            ("Publishing", ("drafts for you to review" if e.get("publish_mode", "draft") == "draft" else "auto-scheduled after review window")
             + (" · health/finance site → always drafts" if e.get("ymyl") else "")),
            ("Cadence", f"one blog every {e.get('interval_days', 2)} days"),
            ("Search Console", e.get("gsc_property") or "not set — `/set gsc sc-domain:yourdomain.com`"),
            ("Call-to-action", f"{(e.get('cta') or {}).get('button', '—')} → {(e.get('cta') or {}).get('url', '—')}")]
    return "| Setting | Value |\n|---|---|\n" + "\n".join(f"| {k} | {str(v).replace('|', '/')} |" for k, v in rows)


def setup_steps(e: dict) -> str:
    return f"""**Finish the connection (about 3 minutes):**
1. Download the plugin: [seo-autopilot-connector.zip]({gh.plugin_url()}) → in WordPress: **Plugins → Add New → Upload Plugin → Activate**.
2. In WordPress: **Settings → SEO Autopilot → Create connection key → Copy key**.
3. Add a GitHub secret named **`{secret_name(e['key'])}`** and paste the key → [open the new-secret page]({gh.new_secret_url()}).
4. Comment **`/test`** here. When it's green, try **`/plan`** and **`/write`**.

Want to change anything above? Use `/set`, e.g. `/set keywords lead generation services, call center outsourcing`. All commands: `/help`."""


# ── handlers ──────────────────────────────────────────────
def handle_new_issue(num: int, body: str) -> None:
    form = parse_form(body)
    if not form["url"]:
        gh.comment(num, "I couldn't find a website address in this issue. Please use **New issue → Add a website**.")
        return
    gh.comment(num, f"⏳ Reading **{form['url']}** and researching the business, keywords and competitors… (about a minute)")
    from . import onboard
    try:
        entry, status, info = onboard.add_site(form, issue=num)
    except Exception as e:  # noqa: BLE001
        gh.comment(num, f"❌ Couldn't add the site: `{type(e).__name__}: {e}`\n\nCheck the address and that the "
                        "`ANTHROPIC_API_KEY` secret is set, then comment `/retry`.")
        return
    gh.set_title(num, f"🌐 {entry['name']} — {re.sub(r'^https?://', '', entry['url'])}")
    gh.add_labels(num, ["website", f"site:{entry['key']}"])
    if status == "linked":
        gh.comment(num, f"🔗 **{entry['name']}** was already set up (`{entry['key']}`). This issue is now its control panel.\n\n"
                        f"{summary_table(entry)}\n\nSecret name for this site: **`{secret_name(entry['key'])}`**. Comment `/test` to check it.\n\n{HELP}")
        return
    warn = "" if info.get("wordpress") else "\n\n> ⚠️ I couldn't confirm this is a WordPress site — the Connector plugin needs WordPress.\n"
    gh.comment(num, f"✅ **Added {entry['name']}** as `{entry['key']}`. I filled in these settings from the website:\n\n"
                    f"{summary_table(entry)}{warn}\n\n{setup_steps(entry)}")


def _site(entry: dict) -> Site:
    return load_sites(only=entry["key"], include_disabled=True)[0]


def _last_article(site: Site) -> dict | None:
    from .planner import history
    h = history(site)
    return h[-1] if h else None


def article_line(site: Site, a: dict) -> str:
    rm, q, geo = a.get("rankmath_score"), (a.get("quality") or {}).get("overall"), a.get("geo_score")
    status = {"future": f"scheduled for {a.get('scheduled_for', '')[:16].replace('T', ' ')} UTC", "draft": "saved as **draft**"}.get(
        a.get("status"), a.get("status"))
    s = (f"📝 **{a.get('title')}** — {status}\n"
         f"Focus keyword: `{a.get('focus_keyword')}` · Rank Math {rm}% · AI-search {geo}% · Quality {q} · {a.get('word_count')} words\n")
    if a.get("edit_url"):
        s += f"[Edit in WordPress]({a['edit_url']})"
    if a.get("review_reasons"):
        s += "\n\n**Please review:** " + "; ".join(a["review_reasons"])
    return s


def handle_command(num: int, text: str, author: str = "") -> None:
    cmd_line = text.strip().splitlines()[0].strip()
    if not cmd_line.startswith("/"):
        return
    parts = cmd_line.split(maxsplit=1)
    cmd, arg = parts[0].lower(), (parts[1].strip() if len(parts) > 1 else "")
    raw = read_raw()
    entry = next((e for e in raw["sites"] if e.get("issue") == num), None)

    if cmd == "/help":
        gh.comment(num, HELP)
        return
    if cmd == "/retry" and entry is None:
        from .github_ops import _api
        issue = _api("GET", f"/issues/{num}") or {}
        handle_new_issue(num, issue.get("body", ""))
        return
    if entry is None:
        gh.comment(num, "This issue isn't linked to a website yet. Open **New issue → Add a website** to add one.")
        return

    from . import cli
    name = entry["name"]
    if cmd in ("/pause", "/resume"):
        entry["enabled"] = cmd == "/resume"
        save_raw(raw)
        gh.comment(num, f"{'▶️ Resumed' if entry['enabled'] else '⏸ Paused'} automatic blogs for **{name}**.")
    elif cmd == "/mode":
        if arg not in ("draft", "schedule"):
            gh.comment(num, "Use `/mode draft` or `/mode schedule`.")
            return
        entry["publish_mode"] = arg
        save_raw(raw)
        extra = " (health/finance site — posts still wait as drafts)" if entry.get("ymyl") and arg == "schedule" else ""
        gh.comment(num, f"✅ **{name}** will now {'save drafts for you to publish' if arg == 'draft' else 'schedule posts after the review window'}{extra}.")
    elif cmd == "/every":
        m = re.search(r"\d+", arg)
        if not m or not 1 <= int(m.group()) <= 30:
            gh.comment(num, "Use `/every <days>`, e.g. `/every 3` (1–30).")
            return
        entry["interval_days"] = int(m.group())
        save_raw(raw)
        gh.comment(num, f"✅ **{name}**: one blog every {entry['interval_days']} days.")
    elif cmd == "/set":
        m = re.match(r"(\w+)\s+(.+)", arg, re.S)
        if not m or m.group(1).lower() not in SETTABLE:
            gh.comment(num, "Use `/set <setting> <value>`. Settings: " + ", ".join(f"`{k}`" for k in SETTABLE))
            return
        field, value = SETTABLE[m.group(1).lower()], m.group(2).strip()
        if field in ("seed_keywords", "competitors"):
            value = [v.strip() for v in value.replace("\n", ",").split(",") if v.strip()]
        elif field == "review_days":
            value = max(0, min(30, int(re.search(r"\d+", value).group()))) if re.search(r"\d+", value) else 3
        elif field == "country":
            value = value.upper()[:2]
        entry[field] = value
        save_raw(raw)
        gh.comment(num, f"✅ Updated `{m.group(1).lower()}` for **{name}**.\n\n{summary_table(entry)}")
    elif cmd == "/remove":
        raw["sites"] = [e for e in raw["sites"] if e is not entry]
        save_raw(raw)
        gh.comment(num, f"🗑 Removed **{name}** from the platform. Posts already in WordPress are untouched. "
                        f"You can delete the `{secret_name(entry['key'])}` secret and uninstall the plugin.")
        gh.close(num)
    elif cmd == "/status":
        site = _site(entry)
        st_p = site.data_dir / "status.json"
        st = json.loads(st_p.read_text()) if st_p.exists() else {}
        last = _last_article(site)
        errs = "\n".join(f"- ❌ {k.replace('_error', '')}: {v}" for k, v in st.items() if k.endswith("_error") and v)
        gh.comment(num, f"**{name}** — {'active' if entry.get('enabled', True) else '⏸ paused'}\n\n{summary_table(entry)}\n\n"
                        + (f"**Last blog:**\n{article_line(site, last)}\n\n" if last else "No blogs written yet.\n\n")
                        + (f"**Problems:**\n{errs}" if errs else ""))
    elif cmd in ("/test", "/plan", "/write", "/audit"):
        site = _site(entry)
        gh.comment(num, f"⏳ Running `{cmd}` for **{name}**…")
        fn = {"/test": cli.cmd_test, "/plan": cli.cmd_plan, "/write": cli.cmd_write, "/audit": cli.cmd_audit}[cmd]
        with captured() as buf:
            if cmd == "/write":
                errors = cli._write([site], notify=False)
            else:
                errors = fn([site], type("A", (), {"site": site.key})())
            from . import report
            report.build(load_sites(include_disabled=True))
        log = "\n".join(l for l in buf.getvalue().splitlines() if l.strip() and not l.startswith("::"))[-3500:]
        if cmd == "/write":
            last = _last_article(site)
            body = (f"✅ Done.\n\n{article_line(site, last)}" if last and not errors else f"❌ `/write` failed: {errors.get(site.key, 'see log')}")
        elif cmd == "/audit":
            inv_p = site.data_dir / "inventory.json"
            inv = json.loads(inv_p.read_text()) if inv_p.exists() else {"issues": [], "pages": []}
            top = "\n".join(f"- **{i['severity']}** — {i['message']} ({i['url'].replace(site.base, '') or '/'})" for i in inv["issues"][:15])
            body = f"{'✅' if not errors else '❌'} Audit of {len(inv['pages'])} pages, {len(inv['issues'])} issues.\n\n{top or 'No issues found.'}"
        elif cmd == "/plan" and not errors:
            topics = [l.strip() for l in buf.getvalue().splitlines() if l.strip().startswith("•")]
            body = "🧭 **Next topic:**\n" + ("\n".join(topics) or "No candidates yet — run `/audit` first, or add keywords with `/set keywords …`.")
        else:
            ok = not errors
            body = f"{'✅' if ok else '❌'} `{cmd}` {'passed' if ok else 'found a problem'}."
            if not ok and errors.get(site.key):
                body += f"\n\n**Fix:** {cli.hint(RuntimeError(errors[site.key]))}"
        gh.comment(num, f"{body}\n\n<details><summary>Log</summary>\n\n```\n{log}\n```\n</details>")
    else:
        gh.comment(num, f"Unknown command `{cmd}`.\n\n{HELP}")


def main() -> int:
    path = os.environ.get("GITHUB_EVENT_PATH")
    if not path:
        print("No GitHub event.")
        return 1
    ev = json.load(open(path, encoding="utf-8"))
    issue = ev.get("issue") or {}
    num = issue.get("number")
    if "comment" in ev:
        if issue.get("pull_request"):
            return 0
        handle_command(num, ev["comment"].get("body", ""), ev["comment"].get("user", {}).get("login", ""))
    elif ev.get("action") == "opened":
        labels = {lb["name"] for lb in issue.get("labels", [])}
        if "add-website" in labels or "### Website address" in (issue.get("body") or ""):
            handle_new_issue(num, issue.get("body", ""))
    return 0
