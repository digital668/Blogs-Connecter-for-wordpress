=== SEO Autopilot Connector ===
Requires at least: 6.0
Requires PHP: 7.4
Stable tag: 1.0.0
License: GPLv2 or later

Connects this WordPress site to your SEO Autopilot GitHub repo with one key.

== Description ==
The connection key can ONLY:
* read posts, pages, categories, tags and media
* upload images (and set alt text on images it uploaded)
* create DRAFT or SCHEDULED posts (at least 1 hour ahead), with Rank Math / Yoast fields and the Divi Builder flag
* create a missing category or tag

It can NOT publish instantly, edit or delete existing content, see users, change settings, or install plugins.
Wrong keys are locked out after 10 tries per 15 minutes. "Create new key" stops the old key immediately.
"Switch OFF" pauses the repo without uninstalling. Only a hash of the key is stored.

== Setup ==
1. Plugins → Add New → Upload Plugin → choose seo-autopilot-connector.zip → Install → Activate.
2. Settings → SEO Autopilot → Create connection key → Copy key.
3. GitHub repo → Settings → Secrets and variables → Actions → New repository secret:
   name WP_PASS_<YOUR SITE KEY> (e.g. WP_PASS_KAIZEN_KLUB), value = the key.
4. Choose "Create posts as" (a real author with a bio).
