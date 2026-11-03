"""Tiny GitHub REST helper (issues, comments, labels) using the workflow's GITHUB_TOKEN."""
from __future__ import annotations

import os

import requests


def repo() -> str | None:
    return os.environ.get("GITHUB_REPOSITORY")


def repo_url() -> str | None:
    r = repo()
    return f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/{r}" if r else None


def available() -> bool:
    return bool(os.environ.get("GITHUB_TOKEN") and repo())


def _api(method: str, path: str, quiet: bool = False, **kw) -> dict | list | None:
    if not available():
        return None
    url = f"{os.environ.get('GITHUB_API_URL', 'https://api.github.com')}/repos/{repo()}{path}"
    r = requests.request(method, url, timeout=20, headers={
        "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
        "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}, **kw)
    if r.status_code >= 400:
        if quiet:
            return None
        print(f"::warning title=GitHub API::{method} {path} → {r.status_code} {r.text[:200]}")
        return None
    return r.json() if r.text else {}


def comment(issue: int, body: str) -> None:
    _api("POST", f"/issues/{issue}/comments", json={"body": body[:65000]})


def add_labels(issue: int, labels: list[str]) -> None:
    for name, color in ((lb, "1f5f4a") for lb in labels):
        _api("POST", "/labels", quiet=True, json={"name": name, "color": color})   # 422 if it already exists
    _api("POST", f"/issues/{issue}/labels", json={"labels": labels})


def set_title(issue: int, title: str) -> None:
    _api("PATCH", f"/issues/{issue}", json={"title": title})


def close(issue: int) -> None:
    _api("PATCH", f"/issues/{issue}", json={"state": "closed"})


def new_secret_url() -> str:
    return f"{repo_url()}/settings/secrets/actions/new" if repo_url() else "Settings → Secrets and variables → Actions → New repository secret"


def plugin_url() -> str:
    return f"{repo_url()}/raw/main/wordpress-plugin/seo-autopilot-connector.zip" if repo_url() \
        else "wordpress-plugin/seo-autopilot-connector.zip in this repo"
