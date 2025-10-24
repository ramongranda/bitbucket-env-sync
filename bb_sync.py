#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bitbucket Sync (Cloud & Server/DC)
- .env first (created/updated). If required values are missing: warn and exit.
- INSECURE=true by default; if INSECURE=false requires corporate CA for API/Git.
- REPO_LIST empty => sync ALL; else only listed slugs.
- Auth: use Git Credential Manager prompt for PAT/App Password (Option A).
- INCREMENTAL .env updates per successful repo with:
    REPO_<SLUG>=<URL>
    REPO_<SLUG>_DEFAULT_BRANCH=<remote_default_branch>
    REPO_<SLUG>_LAST_SYNC=<ISO-UTC>
    REPO_<SLUG>_LAST_STATUS=cloned|updated
    REPO_<SLUG>_LAST_COMMIT=<short_hash_local>
    REPO_<SLUG>_ACTIVE_BRANCH=<local_branch_or_short_hash>
Requirements: Python 3.9+, Git in PATH, requests (pip install requests)
"""

import os
import re
import sys
import subprocess
from pathlib import Path
from typing import Iterator, Optional, Tuple, List
from urllib.parse import urlparse
from datetime import datetime, timezone
import requests

API_CLOUD = "https://api.bitbucket.org/2.0"
ENV_FILE = Path(__file__).resolve().parent / ".env"
VERIFY: object = True  # True | False | path-to-PEM


# ---------- .env helpers ----------
def load_env_file() -> dict:
    env = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
                os.environ.setdefault(k.strip(), v.strip())
    return env


def write_env(env_map: dict):
    lines = [
        "# Bitbucket Sync .env",
        "# Fill required values. INSECURE=true by default.",
        "",
    ]
    for k, v in env_map.items():
        lines.append(f"{k}={v}")
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def ensure_env_defaults():
    """Create/complete .env keys and return (env_dict, missing_required_list)."""
    env = load_env_file()
    env.setdefault("INSECURE", "true")
    env.setdefault("BB_BASE_DIR", str(Path(__file__).resolve().parent))
    env.setdefault("REPO_LIST", "")
    env.setdefault("BITBUCKET_CA_BUNDLE", "")
    env.setdefault("GIT_CA_BUNDLE", "")
    env.setdefault("BITBUCKET_USER", "")
    env.setdefault("BITBUCKET_APP_PASSWORD", "")  # not used with GCM, kept for compatibility
    env.setdefault("BITBUCKET_WORKSPACE", "")
    env.setdefault("BITBUCKET_BASE_URL", "")
    env.setdefault("BITBUCKET_PROJECT", "")

    existing = load_env_file()
    if set(env.keys()) - set(existing.keys()) or not ENV_FILE.exists():
        write_env(env)

    missing = []
    if not env["BITBUCKET_USER"]:
        missing.append("BITBUCKET_USER")

    has_workspace = bool(env["BITBUCKET_WORKSPACE"])
    has_server_pair = bool(env["BITBUCKET_BASE_URL"] and env["BITBUCKET_PROJECT"])
    if not (has_workspace or has_server_pair):
        missing.append("BITBUCKET_WORKSPACE (Cloud or URL /projects/KEY)  ||  BITBUCKET_BASE_URL+BITBUCKET_PROJECT")

    insecure = env["INSECURE"].lower() in ("1", "true", "yes", "y")
    if not insecure:
        if not env["BITBUCKET_CA_BUNDLE"] and not os.getenv("REQUESTS_CA_BUNDLE"):
            missing.append("BITBUCKET_CA_BUNDLE (or REQUESTS_CA_BUNDLE env)")
        if not env["GIT_CA_BUNDLE"] and not env["BITBUCKET_CA_BUNDLE"]:
            missing.append("GIT_CA_BUNDLE (or reuse BITBUCKET_CA_BUNDLE)")

    return env, missing


# ---------- Bitbucket helpers ----------
def detect_mode(workspace_or_url: str, base_url: str, project: str) -> Tuple[str, dict]:
    """('cloud', {'workspace':...}) or ('server', {'base_url':..., 'project':...})"""
    if workspace_or_url and workspace_or_url.startswith(("http://", "https://")):
        u = urlparse(workspace_or_url)
        m = re.search(r"/projects/([^/]+)/?", u.path, re.IGNORECASE)
        if not m:
            raise SystemExit("[ERR] BITBUCKET_WORKSPACE looks like URL but is not /projects/KEY")
        return "server", {"base_url": f"{u.scheme}://{u.netloc}", "project": m.group(1)}
    if base_url and project:
        return "server", {"base_url": base_url, "project": project}
    if workspace_or_url:
        return "cloud", {"workspace": workspace_or_url}
    raise SystemExit("[ERR] Incomplete destination config (Cloud or Server/DC).")


def http_get(url: str, auth, params=None) -> requests.Response:
    return requests.get(url, auth=auth, params=params, timeout=60, verify=VERIFY)


def paginate_cloud(url: str, auth, params=None) -> Iterator[dict]:
    params = dict(params or {})
    while url:
        r = http_get(url, auth, params=params)
        if r.status_code == 401:
            raise RuntimeError("401 Cloud: invalid App Password or insufficient permissions.")
        if r.status_code != 200:
            raise RuntimeError(f"Bitbucket Cloud API {r.status_code}: {r.text}")
        data = r.json()
        for item in data.get("values", []):
            yield item
        url = data.get("next")
        params = {}


def paginate_server(base_url: str, project: str, auth, params=None) -> Iterator[dict]:
    limit = 100
    start = 0
    while True:
        url = f"{base_url}/rest/api/1.0/projects/{project}/repos"
        qparams = {"limit": limit, "start": start}
        if params:
            qparams.update(params)
        r = http_get(url, auth, params=qparams)
        if r.status_code == 401:
            raise RuntimeError("401 Server/DC: use Personal Access Token or valid credentials for API (read-only).")
        if r.status_code != 200:
            raise RuntimeError(f"Bitbucket Server API {r.status_code}: {r.text}")
        data = r.json()
        for item in data.get("values", []):
            yield item
        if data.get("isLastPage", True):
            break
        start = data.get("nextPageStart", start + limit)


def get_clone_url(repo: dict) -> Optional[str]:
    for link in repo.get("links", {}).get("clone", []):
        if (link.get("name") or "").lower() in ("https", "http"):
            return link.get("href")
    return None


def get_default_branch(repo: dict, mode: str) -> Optional[str]:
    if mode == "cloud":
        mb = repo.get("mainbranch") or {}
        return mb.get("name")
    db = repo.get("defaultBranch") or {}
    return db.get("displayId") or (db.get("id").split("/")[-1] if db.get("id") else None)


# ---------- git helpers ----------
def run_git(cmd: List[str], cwd: Optional[Path] = None, git_ca_bundle: Optional[str] = None, insecure: bool = False) -> int:
    env = os.environ.copy()
    if git_ca_bundle:
        env["GIT_SSL_CAINFO"] = git_ca_bundle
        env["CURL_CA_BUNDLE"] = git_ca_bundle
    if insecure:
        env["GIT_SSL_NO_VERIFY"] = "1"
    env.pop("GIT_TERMINAL_PROMPT", None)  # allow GCM
    return subprocess.call(["git"] + cmd, cwd=str(cwd) if cwd else None, env=env)


def run_git_capture(cmd: List[str], cwd: Optional[Path] = None) -> str:
    try:
        out = subprocess.check_output(["git"] + cmd, cwd=str(cwd) if cwd else None, stderr=subprocess.DEVNULL)
        return out.decode("utf-8", errors="ignore").strip()
    except Exception:
        return ""


def local_active_branch(repo_dir: Path) -> str:
    branch = run_git_capture(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_dir)
    if branch and branch != "HEAD":
        return branch
    return run_git_capture(["rev-parse", "--short", "HEAD"], cwd=repo_dir)


def local_short_commit(repo_dir: Path) -> str:
    return run_git_capture(["rev-parse", "--short", "HEAD"], cwd=repo_dir)


def now_iso_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def update_env_repo(slug: str, url: str, default_branch: str, status: str, repo_dir: Path):
    existing = load_env_file()
    key_base = slug.replace("-", "_").upper()
    existing[f"REPO_{key_base}"] = url
    if default_branch:
        existing[f"REPO_{key_base}_DEFAULT_BRANCH"] = default_branch
    existing[f"REPO_{key_base}_LAST_SYNC"] = now_iso_utc()
    existing[f"REPO_{key_base}_LAST_STATUS"] = status
    existing[f"REPO_{key_base}_LAST_COMMIT"] = local_short_commit(repo_dir) or ""
    existing[f"REPO_{key_base}_ACTIVE_BRANCH"] = local_active_branch(repo_dir) or ""
    write_env(existing)


def parse_repo_list(text: str) -> List[str]:
    if not text:
        return []
    items = [x.strip() for x in text.split(",")]
    return [x for x in items if x]


def str2bool(s: str) -> bool:
    return str(s).lower() in ("1", "true", "yes", "y")


# ---------- main ----------
def main():
    env, missing = ensure_env_defaults()
    if missing:
        print(f"[INFO] .env at: {ENV_FILE}")
        print("[WARN] Missing required values in .env:")
        for m in missing:
            print(f"  - {m}")
        print("\nFill the .env and run again. Nothing was executed.")
        sys.exit(2)

    insecure = str2bool(env.get("INSECURE", "true"))
    ca_api = env.get("BITBUCKET_CA_BUNDLE") or os.getenv("REQUESTS_CA_BUNDLE") or ""
    git_ca = env.get("GIT_CA_BUNDLE") or ca_api
    global VERIFY
    VERIFY = False if insecure else (ca_api if ca_api else True)

    workspace_or_url = env.get("BITBUCKET_WORKSPACE", "")
    mode, info = detect_mode(workspace_or_url, env.get("BITBUCKET_BASE_URL", ""), env.get("BITBUCKET_PROJECT", ""))

    base_dir = Path(env.get("BB_BASE_DIR", Path(__file__).resolve().parent)).resolve()
    base_dir.mkdir(parents=True, exist_ok=True)
    wanted = parse_repo_list(env.get("REPO_LIST", ""))

    print(f"[INFO] Mode: {mode.upper()}")
    if mode == "cloud":
        print(f"[INFO] Workspace: {info['workspace']}")
    else:
        print(f"[INFO] Base URL: {info['base_url']}  Project: {info['project']}")
    print(f"[INFO] Base directory: {base_dir}")
    print(f"[INFO] TLS: {'INSECURE (no verify)' if insecure else ('CA='+ (ca_api or 'system'))}")

    api_user = env["BITBUCKET_USER"]
    api_pass = env.get("BITBUCKET_APP_PASSWORD", "")
    auth_api = (api_user, api_pass) if api_pass else (api_user, "")  # may fail for private APIs if empty

    total = cloned = updated = skipped = 0

    iterator = (
        paginate_cloud(f"{API_CLOUD}/repositories/{info['workspace']}", auth_api, {"pagelen": 100})
        if mode == "cloud"
        else paginate_server(info["base_url"], info["project"], auth_api)
    )

    try:
        for repo in iterator:
            total += 1
            slug = repo.get("slug") or re.sub(r"\s+", "-", (repo.get("name") or "").lower())
            if wanted and slug not in wanted:
                continue

            url = get_clone_url(repo)
            if not url:
                print(f"[WARN] missing HTTPS URL: {slug}")
                skipped += 1
                continue

            default_branch = get_default_branch(repo, mode) or ""
            dest = base_dir / slug
            action = "clone" if not dest.exists() else "pull" if (dest / ".git").exists() else None
            if not action:
                print(f"[WARN] {dest} exists but is not a git repo")
                skipped += 1
                continue

            print(f"[{action.upper()}] {slug} -> {dest}")
            if action == "clone":
                rc = run_git(["clone", url, str(dest)], git_ca_bundle=git_ca, insecure=insecure)
                if rc == 0:
                    cloned += 1
                    update_env_repo(slug, url, default_branch, "cloned", dest)
                else:
                    skipped += 1
                    print(f"[ERR] clone ({rc}) in {slug}")
            else:
                # ensure clean remote URL (no embedded creds)
                run_git(["remote", "set-url", "origin", url], cwd=dest, git_ca_bundle=git_ca, insecure=insecure)
                rc = run_git(["pull", "--ff-only"], cwd=dest, git_ca_bundle=git_ca, insecure=insecure)
                if rc == 0:
                    updated += 1
                    update_env_repo(slug, url, default_branch, "updated", dest)
                else:
                    skipped += 1
                    print(f"[ERR] pull ({rc}) in {slug}")

        print(f"\n[SUMMARY] total={total} cloned={cloned} updated={updated} skipped={skipped}")

    except requests.exceptions.SSLError as e:
        print(f"[FATAL] SSL error (API): {e}\n       Set INSECURE=false and provide PEM paths.", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[INTERRUPTED]")
        sys.exit(130)
    except Exception as e:
        print(f"[FATAL] {e}", file=sys.stderr)
        sys.exit(1)
