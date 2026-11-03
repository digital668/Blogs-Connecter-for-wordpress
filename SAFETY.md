# Keeping your sites safe

The biggest risk to a network of 20+ sites publishing AI-assisted content is **Google's "scaled content abuse" spam policy**: lots of pages produced mainly to rank rather than to help readers. It was introduced in March 2024 and has been the main target of the 2026 spam updates (March, June, August and September 2026). In 2026 Google also extended its spam policies to AI Overviews and AI Mode.

Google does **not** penalise content just because AI helped write it. It penalises volume without value. Everything below is about making sure each post earns its place.

---

## 1. Guardrails built into the platform (automatic)

| Guardrail | What it prevents |
|---|---|
| **One post per site every 2 days, staggered across sites.** Publish times vary within working hours in the site's market. | The sudden mass-publishing pattern typical of content farms. About 15 posts a month per site is a normal blog pace. |
| **Review window:** posts sit as *Scheduled* for 3 days before going live. | Publishing something wrong before a person has looked at it. |
| **YMYL sites always draft** (`ymyl: true`). Use this for the credit/finance sites, and for health sites like physio or fitness advice. | Google holds money and health content to the highest standard (E-E-A-T). A person must approve every post. |
| **Automatic draft instead of schedule** when a post has a Rank Math score under 80, fails the humanize checks, has 4+ unsourced numbers, or fails the duplicate check. | Weak posts going live unreviewed. The dashboard shows the reason. |
| **Duplicate check against all your sites**, plus a **niche group** rule: sites with the same `group` never get the same or a similar topic within 120 days. | Near-duplicate posts across your sister sites (the three US internet sites especially). |
| **No cross-linking between your own sites.** Links to any other domain in `sites.yaml` are removed. | A visible "network" footprint and link-scheme signals. |
| **Outbound links are kept only to trusted sources** (government, education, regulators, major publications; you can add domains with `external_links_ok`). | Links to spammy or non-existent pages. |
| **Traffic-drop auto-pause:** if a site's weekly clicks fall 35%+, writing stops for that site. | Continuing to publish into a site that may have a problem (manual action, update hit, technical fault). |
| **Content-quality gate:** filler, AI-pattern, repetition and information-density scoring based on Google's Quality Rater Guidelines (scaled content abuse). A score under 60 means the post becomes a draft. | Thin, generic posts going live. |
| **Weekly Google index check** of every auto-written post. You're warned if more than half aren't indexed. | Continuing to publish when Google is already ignoring the posts. This is an early warning, before any penalty. |
| **Kill switch:** in GitHub, go to Settings → Variables → `AUTOPILOT_PAUSED = true`. | One switch stops all writing on all sites immediately. |
| **The platform only creates new posts.** It never edits, deletes or noindexes your existing pages. | Accidental damage to pages that already rank. |
| **Competitor pages are read only as statistics** (word counts, headings, common terms). It respects robots.txt and fetches slowly. | Copying, or anything that looks like scraping. |
| **Humanizing by editing, not spinning.** It never swaps synonyms to fool detectors. | "Spun" text, which is itself a spam signal and reads badly. |
| **Licensed images only** (Pexels or CC0/public domain), uploaded to your own media library. | Copyright claims and hot-linking. |

---

## 2. What you should do (one-time setup per site)

1. **Real author:** set `author_id` to a real person's WordPress account with a photo, a short bio of their experience and links to their profiles. Add an **About** page and a **Contact** page.
2. **Make each site different:** give each site its own `niche`, `audience`, `tone` and `seed_keywords` in `sites.yaml`. Sites that read the same are the main footprint risk.
3. **Review the drafts.** Spend about 5 minutes on each scheduled post during the review window. Add one real detail from your own experience: a local price, a customer question, a photo you took. This is the single strongest protection against scaled content abuse.
4. **Connect with the Connector plugin, not an Application Password.** Its key can only create drafts or scheduled posts and upload images. It can't publish instantly, edit, delete or change settings, so even a leaked key can't damage the site. Turn on 2FA for your admin accounts.
5. **Lead-gen forms (US sites):** any form that collects phone numbers needs a privacy policy link and clear consent text for calls and texts. Have this checked for US telemarketing rules (TCPA). If you earn commissions from providers, add a short disclosure to comply with FTC rules.
6. **No city doorway pages:** don't create near-identical "Internet providers in [City]" pages for hundreds of cities. Google calls that doorway abuse. The platform doesn't do it, so don't add it manually either.

---

## 3. Free tools to connect

| Tool | Use it for |
|---|---|
| **Google Search Console** | Manual actions (check **Security & Manual actions** monthly), indexing, Core Web Vitals. Required for the platform. |
| **Bing Webmaster Tools** | Import from Search Console in one click. Covers Bing and ChatGPT search visibility. |
| **Rank Math (free) → Instant Indexing** | Sends new posts to Bing and other engines through IndexNow as soon as they publish. |
| **Wordfence (free)** | Firewall and malware scan. Hacked sites get flagged by Google faster than anything else. |
| **UpdraftPlus (free)** | Automatic weekly backups to Google Drive. |
| **Cloudflare (free)** | DNS, SSL, caching and bot protection. |
| **PageSpeed Insights** | Check each site's speed once a month. |
| **Rich Results Test** | Confirms the FAQ schema is valid. |
| Your plagiarism checker | Run it during the review window, as you do now. |

---

## 4. Monthly 10-minute safety check

- [ ] Search Console → **Manual actions** and **Security issues** show "No issues" on every site
- [ ] Dashboard: no site is **paused** for a traffic drop; if one is, find out why before resuming
- [ ] Dashboard: the share of drafts needing review isn't climbing
- [ ] Spot-read 2 live posts per site: would you be happy if a customer read them?
- [ ] Wordfence scan is clean; backups are running

**If a site does get hit:** set `enabled: false` for it, check Search Console for a manual action, and improve or remove the weakest auto-written posts before you turn it back on.

---

## Rules the platform follows

- Google Search Essentials and spam policies: scaled content abuse, doorway abuse, link spam, cloaking, keyword stuffing, thin affiliation, scraped content, expired domain abuse and site reputation abuse.
- Google's guidance on helpful, reliable, people-first content and E-E-A-T, including its guidance on using generative AI content.
- Rank Math's content-analysis tests, used as a checklist and never as a reason to stuff keywords. Density is capped at 2%.
