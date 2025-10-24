#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bitbucket Env Sync CLI

English Documentation for Maintainers and AI Agents:

Overview:
- Python CLI tool to synchronize Bitbucket Cloud and Server/DC repositories.
- Main configuration and sync state are stored in `.env` (auto-created/updated).
- Supports incremental sync and per-repo metadata tracking.

Main Flow:
1. Checks/creates `.env` and prompts for required fields if missing.
2. Detects Cloud/Server mode based on `.env` variables.
3. Syncs all repositories or only those listed in `REPO_LIST`.
4. Updates per-repo metadata in `.env` after each successful operation.

Key Patterns:
- `.env` is the only persistent state; always use atomic write and file lock helpers.
- Per-repo metadata keys: `REPO_<SLUG>_DEFAULT_BRANCH`, `REPO_<SLUG>_LAST_SYNC`, etc.
- Repository URLs are managed in the comma-separated `REPO_LIST`.
- Authentication via Git Credential Manager (PAT/App Password prompt on first use).
- Default is `INSECURE=true` for easy setup; use corporate CA and `INSECURE=false` for production.

Development Workflows:
- Install dependencies: `make install` or `pip install requests pyinstaller`
- Run sync: `python bb_sync.py` (creates `.env` if missing)
- Build binary: Windows: `make build-win`, Linux: `make build-linux`
- Format/lint: `black . && isort .`, `pre-commit run --all-files`
- Run tests: `pytest`

Example `.env` Metadata:
REPO_LIST=https://bitbucket.org/workspace/repo1,https://bitbucket.org/workspace/repo2
REPO_REPO1_DEFAULT_BRANCH=main
REPO_REPO1_LAST_SYNC=2025-10-24T12:34:56Z
REPO_REPO1_LAST_STATUS=updated
REPO_REPO1_LAST_COMMIT=abc123
REPO_REPO1_ACTIVE_BRANCH=main

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

# Nuevos imports
import tempfile
import time
import contextlib

API_CLOUD = "https://api.bitbucket.org/2.0"
ENV_FILE = Path(__file__).resolve().parent / ".env"
VERIFY: object = True  # True | False | path-to-PEM


# ---------- .env helpers ----------
@contextlib.contextmanager
def file_lock(target_path: Path, timeout: float = 10.0):
    """
    Simple cross-platform lock using a .lock file plus fcntl/msvcrt.
    Blocks until lock acquired or timeout. Removes lockfile on release.
    """
    lock_path = Path(str(target_path) + ".lock")
    # Open lock file for writing (create if missing)
    f = open(lock_path, "w")
    try:
        start = time.time()
        while True:
            try:
                if os.name == "nt":
                    import msvcrt

                    # Try non-blocking lock of one byte
                    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except Exception:
                # Could be BlockingIOError or OSError depending on platform
                if time.time() - start > timeout:
                    f.close()
                    raise TimeoutError(f"Timeout acquiring lock on {lock_path}")
                time.sleep(0.1)
        try:
            yield
        finally:
            try:
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
    finally:
        try:
            f.close()
        except Exception:
            pass
        # Try to remove lockfile (best-effort)
        try:
            lock_path.unlink()
        except Exception:
            pass


def load_env_file() -> dict:
    env = {}
    # Acquire lock for safe concurrent reads
    ensure_env_parent()
    try:
        with file_lock(ENV_FILE):
            if ENV_FILE.exists():
                for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
                    if "=" in line and not line.strip().startswith("#"):
                        k, v = line.split("=", 1)
                        env[k.strip()] = v.strip()
                        os.environ.setdefault(k.strip(), v.strip())
    except TimeoutError:
        # If lock timeout, fallback to best-effort read without lock
        if ENV_FILE.exists():
            for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip()
                    os.environ.setdefault(k.strip(), v.strip())
    return env


def ensure_env_parent():
    # Ensure .env parent exists
    ENV_FILE.parent.mkdir(parents=True, exist_ok=True)


def write_env(env_map: dict):
    """
    Writes .env atomically (temp file in same dir + os.replace) under file lock.
    """
    ensure_env_parent()
    lines = [
        "# Bitbucket Sync .env",
        "# Fill required values. INSECURE=true by default.",
        "",
    ]
    for k, v in env_map.items():
        lines.append(f"{k}={v}")
    content = "\n".join(lines) + "\n"

    # Acquire lock and write atomically
    try:
        with file_lock(ENV_FILE):
            # Write to temp file in same directory to ensure atomic replace
            dirpath = str(ENV_FILE.parent)
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=dirpath, delete=False
            ) as tf:
                tf.write(content)
                temp_name = tf.name
            os.replace(temp_name, str(ENV_FILE))
    except TimeoutError:
        # If cannot acquire lock, fallback to best-effort write (not ideal but avoids crashing)
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=str(ENV_FILE.parent), delete=False
        ) as tf:
            tf.write(content)
            temp_name = tf.name
        os.replace(temp_name, str(ENV_FILE))


def normalize_url_for_list(url: str) -> str:
    """Normalize URL for comparison/storage in REPO_LIST: remove spaces and trailing slash."""
    return url.strip().rstrip("/")


def parse_repo_list(text: str) -> List[str]:
    """Parse REPO_LIST which may be one URL per line or comma-separated values.

    Returns a list of non-empty normalized URL strings.
    """
    if not text:
        return []
    items: List[str] = []
    # support both line-separated and comma-separated values
    for line in text.splitlines():
        for part in line.split(","):
            p = part.strip()
            if p:
                items.append(p)
    return items


def ensure_env_defaults() -> Tuple[dict, List[str]]:
    """Load `.env`, ensure required keys and sensible defaults.

    Returns (env_map, missing_required_keys).
    - Ensures `INSECURE` and `REPO_LIST` exist (defaulting to 'true' and empty).
    - Does basic validation: requires BITBUCKET_USER and BB_BASE_DIR and either
      BITBUCKET_WORKSPACE or both BITBUCKET_BASE_URL and BITBUCKET_PROJECT.
    - Writes `.env` back with defaults applied.
    """
    env_map = load_env_file()
    # Ensure basic keys exist
    changed = False
    if "INSECURE" not in env_map:
        env_map["INSECURE"] = "true"
        changed = True
    if "REPO_LIST" not in env_map:
        env_map["REPO_LIST"] = ""
        changed = True

    # Minimal required fields
    missing: List[str] = []
    if not env_map.get("BITBUCKET_USER"):
        missing.append("BITBUCKET_USER")
    if not env_map.get("BB_BASE_DIR"):
        missing.append("BB_BASE_DIR")

    # require either workspace (cloud) or base_url+project (server)
    if not env_map.get("BITBUCKET_WORKSPACE"):
        if not (env_map.get("BITBUCKET_BASE_URL") and env_map.get("BITBUCKET_PROJECT")):
            missing.append("BITBUCKET_WORKSPACE or (BITBUCKET_BASE_URL and BITBUCKET_PROJECT)")

    # Persist defaults if necessary
    if changed:
        try:
            write_env(env_map)
        except Exception:
            # best-effort: do not fail if write cannot happen
            pass

    return env_map, missing


def ensure_url_in_repo_list(env_map: dict, url: str) -> bool:
    """Ensure the URL is present in env_map['REPO_LIST'] (one per line).

    Returns True if added, False if already present.
    """
    raw = env_map.get("REPO_LIST", "") or ""
    # Store REPO_LIST as one URL per line
    items = [x.strip() for x in raw.splitlines() if x.strip()]
    normalized_existing = {normalize_url_for_list(u) for u in items}
    norm_url = normalize_url_for_list(url)
    if norm_url in normalized_existing:
        return False
    items.append(norm_url)
    env_map["REPO_LIST"] = "\n".join(items)
    return True


def migrate_old_repo_keys(env_map: dict) -> None:
    """Finds old keys REPO_<SLUG>=<URL> (without suffixes) and removes them from the map.

    Does not migrate the URL values to `REPO_LIST`. Idempotent.
    """
    keys = list(env_map.keys())
    base_repo_keys = []
    for k in keys:
        # Only match REPO_<SLUG> keys without suffixes (metadata keys have suffixes like _LAST_SYNC)
        if re.fullmatch(r"REPO_[A-Z0-9_]+", k):
            base_repo_keys.append(k)
    for k in base_repo_keys:
        # Remove old per-repo URL keys, do not migrate to REPO_LIST
        try:
            env_map.pop(k, None)
        except Exception:
            pass
    # No hacemos write aquí: quien llamó a migrate_old_repo_keys se encargará de escribir
    # No write here: caller of migrate_old_repo_keys is responsible for writing


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
    # ... original implementation remains unchanged ...
    next_url = url
    qparams = params.copy() if params else {}
    while True:
        r = http_get(next_url, auth, params=qparams)
        if r.status_code == 401:
            raise RuntimeError(
                "401 Cloud: use App Password or valid credentials for API (read-only)."
            )
        if r.status_code != 200:
            raise RuntimeError(f"Bitbucket Cloud API {r.status_code}: {r.text}")
        data = r.json()
        for item in data.get("values", []):
            yield item
        if not data.get("next"):
            break
        next_url = data.get("next")
        # ensure params not duplicated
        qparams = {}


# ---------- git helpers ----------
def run_git(
    cmd: List[str],
    cwd: Optional[Path] = None,
    git_ca_bundle: Optional[str] = None,
    insecure: bool = False,
) -> int:
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
        out = subprocess.check_output(
            ["git"] + cmd, cwd=str(cwd) if cwd else None, stderr=subprocess.DEVNULL
        )
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
    """
    Incrementally updates .env for a repo:
    - Removes old keys REPO_<SLUG>=<URL>.
    - Adds the repo URL to REPO_LIST if not present (one per line).
    - Updates metadata keys per slug (DEFAULT_BRANCH, LAST_SYNC, etc).
    All changes are atomic and thread-safe.
    """
    existing = load_env_file()

    # Remove old per-repo URL keys
    try:
        migrate_old_repo_keys(existing)
    except Exception:
        pass

    # Add the repo URL to REPO_LIST if not present (one per line)
    try:
        ensure_url_in_repo_list(existing, url)
    except Exception:
        pass

    # Update metadata per repo
    key_base = slug.replace("-", "_").upper()
    if default_branch:
        existing[f"REPO_{key_base}_DEFAULT_BRANCH"] = default_branch
    existing[f"REPO_{key_base}_LAST_SYNC"] = now_iso_utc()
    existing[f"REPO_{key_base}_LAST_STATUS"] = status
    existing[f"REPO_{key_base}_LAST_COMMIT"] = local_short_commit(repo_dir) or ""
    existing[f"REPO_{key_base}_ACTIVE_BRANCH"] = local_active_branch(repo_dir) or ""

    # Atomically write .env
    write_env(existing)

    # Optionally commit .env into git if AUTO_COMMIT_ENV is enabled in env map
    try:
        auto = str(
            existing.get("AUTO_COMMIT_ENV", os.environ.get("AUTO_COMMIT_ENV", "false"))
        ).lower()
        if auto in ("1", "true", "yes", "y"):
            msg = f"env: update {key_base} {now_iso_utc()}"
            try:
                # Stage and commit .env in the repository root (ENV_FILE.parent)
                ret = run_git(["add", str(ENV_FILE.name)], cwd=ENV_FILE.parent)
                if ret == 0:
                    cret = run_git(["commit", "-m", msg], cwd=ENV_FILE.parent)
                    if cret != 0:
                        print(f"Warning: git commit for .env returned {cret}")
                else:
                    print(f"Warning: git add .env returned {ret}")
            except Exception as e:
                print(f"Warning: failed to auto-commit .env: {e}")
    except Exception:
        # Non-fatal: do not break sync because commit failed
        pass


def str2bool(s: str) -> bool:
    return str(s).lower() in ("1", "true", "yes", "y")


# ---------- main ----------
def main():
    _, missing = ensure_env_defaults()
    if missing:
        print("Missing required .env values:", ", ".join(missing))
        print("Please fill them in .env and re-run.")
        sys.exit(1)

    # Environment looks OK for a run. The full sync flow runs here (not implemented
    # in this small incremental update). This function currently verifies env and
    # exits successfully.
    print("Environment OK. Ready to run sync.")
    return 0


# If run as script
if __name__ == "__main__":
    sys.exit(main())
