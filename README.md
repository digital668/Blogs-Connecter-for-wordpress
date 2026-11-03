# SEO Autopilot

Connects all your WordPress sites to one private GitHub repo. For each site it researches keywords and competitors, writes an original, humanized blog **every 2 days**, makes it pass Rank Math's checks, formats it for that site's builder (Divi, Elementor or Gutenberg), and saves it as a draft or schedules it. Everything runs free on GitHub Actions, so you don't need a server.

You manage it from **GitHub Issues**: add a website with a form, then type commands like `/write` or `/pause` on that website's issue.

> Read **[SAFETY.md](SAFETY.md)** once. It explains how the platform keeps your sites safe with Google.

---

## One-time setup (about 15 minutes)

**1. Create the repo.** On GitHub, go to **New repository**, choose **Private** and click **Create**. Click **uploading an existing file**, drag in **everything** from this zip (including the `.github` folder) and click **Commit changes**.

**2. Let the robot save its work.** Go to **Settings → Actions → General → Workflow permissions → Read and write permissions → Save**.

**3. Add the Claude API key.** Go to **Settings → Secrets and variables → Actions → New repository secret**. Name it `ANTHROPIC_API_KEY` and paste the key from console.anthropic.com as the value (add credit there first).

Optional, can be done later:
- `PEXELS_API_KEY`: a free key from pexels.com/api for better stock photos. Without it, free public-domain images are used.
- `GSC_SERVICE_ACCOUNT_JSON`: Search Console data. See [Search Console](#search-console-optional) below.

**4. Check it.** Go to **Actions → SEO Autopilot → Run workflow → `test` → Run**. The run page shows a checklist of what's set up and what's missing.

---

## Add a website (about 3 minutes per site)

1. Go to **Issues → New issue → 🌐 Add a website**. Paste the address, pick the country, publishing mode and frequency, and click **Submit**.
2. Within about a minute, the issue replies with what it worked out from the site: niche, audience, keywords, competitors, page builder, contact page and service pages to link. The reply also gives you the exact secret name for this site.
3. Follow the 3 steps in that reply:
   - In WordPress, upload `wordpress-plugin/seo-autopilot-connector.zip` (**Plugins → Add New → Upload → Activate**).
   - Go to **Settings → SEO Autopilot → Create connection key → Copy**.
   - Add a GitHub secret with the name from the reply (for example `WP_PASS_GLOBEL_CONNECT`) and paste the key.
4. Comment **`/test`** on the issue. When it's green, comment **`/write`** to get your first draft.

From then on, the daily schedule writes for that site automatically. Every new blog is posted as a comment on the site's issue, and GitHub emails you, so you know when something needs review.

**Kaizen Klub and Globel Connect are already set up** in `sites.yaml` with their tuned settings. Open **Add a website** for each anyway: the platform recognises them and turns that issue into their control panel without changing their settings.

---

## Commands (type as a comment on a website's issue)

| Command | What it does |
|---|---|
| `/test` | Checks the connection (plugin, page builder, Search Console) |
| `/plan` | Shows the next blog topic |
| `/write` | Writes one blog now |
| `/audit` | Crawls the site and lists the top SEO issues |
| `/status` | Shows settings, the last blog and any problems |
| `/pause` · `/resume` | Stops or restarts automatic blogs for this site |
| `/mode draft` · `/mode schedule` | Drafts you publish yourself, or auto-scheduled after a 3-day review window |
| `/every 3` | One blog every 3 days |
| `/set keywords a, b, c` | Changes a setting: `name`, `niche`, `audience`, `tone`, `country`, `keywords`, `competitors`, `category`, `rules`, `review_days` |
| `/remove` | Removes the site from the platform (its WordPress posts are kept) |
| `/help` | Shows this list |

**To stop everything at once:** go to **Settings → Secrets and variables → Actions → Variables → New variable**, name it `AUTOPILOT_PAUSED` and set it to `true`. Each site's WordPress plugin also has its own **Switch OFF** button.

---

## What happens every day (6:47 am IST)

- **Monday:** a site audit (broken links, missing meta, thin pages, orphans) plus a scan of competitor sitemaps for topic gaps.
- **Monday and Thursday:** keyword research from Search Console and Google Autocomplete.
- **Thursday:** an index check asks Google whether each blog that went live 5+ days ago is indexed, and warns you if most aren't.
- **Every day:** each site that's due gets **one blog**:

> pick a fresh topic → research what ranks on Google → analyse competitors (free) → write → human-style edit and AI-phrase cleanup → remove unsafe links → add a free licensed photo with keyword alt text → Rank Math check and auto-fix (21 tests) → AI-search check → content-quality check → duplicate check across all your sites → safety gate → format for Divi, Elementor or Gutenberg → save as draft or schedule.

Anything that fails a check is saved as a **draft** with the reason, so it's never published.

## Where to see results

- **Each website's issue:** every new blog, with its scores and an **Edit in WordPress** link.
- **Each run's page in Actions:** a summary table of what was written and what needs review.
- **The full dashboard:** open a run, go to **Artifacts → dashboard**, download, unzip and open `dashboard.html`. It shows health, Search Console trends, every blog's Rank Math, AI-search and quality scores, Google index status, issues, competitor gaps and link ideas. The **＋ Add website** button opens the form.

---

## Search Console (optional)

1. At console.cloud.google.com, create a project, then go to **APIs & Services → Library → Google Search Console API → Enable**.
2. Go to **IAM & Admin → Service Accounts → Create**, then **Keys → Add key → JSON**. Paste the whole file into the `GSC_SERVICE_ACCOUNT_JSON` secret.
3. For each site, in Search Console, go to **Settings → Users and permissions → Add user**. Paste the service account's email and choose **Restricted**. This works even if your sites are in different Google accounts.
4. New websites get `sc-domain:yourdomain.com` automatically. If yours is a URL-prefix property instead, comment `/set gsc https://www.yourdomain.com/` on the site's issue. Then comment `/test`.

**Free extra in each WordPress site:** go to **Rank Math → Dashboard → Instant Indexing → On**. New posts are then sent to Bing (which also feeds ChatGPT search) the moment they go live.

---

## Costs

| | |
|---|---|
| GitHub private repo + Actions | Free (about 800–1,000 of the 2,000 free minutes a month for 20 sites) |
| Search Console, Autocomplete, competitor analysis, photos | Free |
| Claude API | About **$0.40–0.60 per blog**, plus about $0.05 to set up each new website |

At 20 sites × 1 blog every 2 days, that's roughly **$120–180 a month**. Use `/every 3` to cut it by about a third.

---

## Files

```
sites.yaml                 your websites (managed for you by the issue commands)
SAFETY.md                  how the platform keeps your sites safe + monthly checklist
wordpress-plugin/          seo-autopilot-connector.zip: install this on each WordPress site
.github/workflows/         main.yml (daily schedule) · site-manager.yml (issue form + commands)
.github/ISSUE_TEMPLATE/    the "Add a website" form
seo_platform/              the platform (Python)
docs/index.html            dashboard
tests/                     automated tests (fake WordPress, fake Claude)
THIRD_PARTY_NOTICES.md     credits for code adapted from AgriciDaniel/claude-seo (MIT)
```
