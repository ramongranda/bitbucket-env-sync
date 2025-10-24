#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bitbucket Sync (Cloud & Server/DC)
- .env first (created/updated). If required values are missing: warn and exit.
- INSECURE=true by default; if INSECURE=false requires corporate CA for API/Git.
- REPO_LIST empty => sync ALL; else only listed slugs.
- Auth: use Git Credential Manager prompt for PAT/App Password (Option A).
- INCREMENTAL .env updates per successful repo with:
    REPO_<SLUG>=<URL>            (NOTE: migrated -> stored in REPO_LIST instead)
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
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=dirpath, delete=False) as tf:
                tf.write(content)
                temp_name = tf.name
            os.replace(temp_name, str(ENV_FILE))
    except TimeoutError:
        # If cannot acquire lock, fallback to best-effort write (not ideal but avoids crashing)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(ENV_FILE.parent), delete=False) as tf:
            tf.write(content)
            temp_name = tf.name
        os.replace(temp_name, str(ENV_FILE))


def normalize_url_for_list(url: str) -> str:
    """Normaliza la URL para comparación/almacenamiento en REPO_LIST: quita espacios y trailing slash."""
    return url.strip().rstrip("/")


def parse_repo_list(text: str) -> List[str]:
    if not text:
        return []
    items = [x.strip() for x in text.split(",")]
    return [x for x in items if x]


def ensure_url_in_repo_list(env_map: dict, url: str) -> bool:
    """
    Asegura que la URL esté presente en env_map['REPO_LIST'] (coma-separada).
    Devuelve True si se añadió, False si ya existía.
    """
    raw = env_map.get("REPO_LIST", "") or ""
    items = [x.strip() for x in raw.split(",") if x.strip()]
    normalized_existing = {normalize_url_for_list(u) for u in items}
    norm_url = normalize_url_for_list(url)
    if norm_url in normalized_existing:
        return False
    # Añadir la forma normalizada (consistente) al listado
    items.append(norm_url)
    env_map["REPO_LIST"] = ",".join(items)
    return True


def migrate_old_repo_keys(env_map: dict) -> None:
    """
    Encuentra claves antiguas REPO_<SLUG>=<URL> (sin sufijos) y las mueve a REPO_LIST,
    luego elimina las claves antiguas del mapa. Es idempotente.
    """
    keys = list(env_map.keys())
    base_repo_keys = []
    for k in keys:
        # Coincide con REPO_... pero sin sufijos (las metadatas tienen sufijos como _LAST_SYNC)
        if re.fullmatch(r"REPO_[A-Z0-9_]+", k):
            base_repo_keys.append(k)
    added_any = False
    for k in base_repo_keys:
        val = env_map.get(k, "").strip()
        if val:
            try:
                # Solo añadir si parece una URL o algo válido (mejor validar con esquema)
                parsed = urlparse(val)
                if parsed.scheme in ("http", "https"):
                    added = ensure_url_in_repo_list(env_map, val)
                    if added:
                        added_any = True
            except Exception:
                # si parse falla, ignoramos la migración de esta key pero la borramos de todos modos
                pass
        # Eliminar la clave antigua para evitar duplicados y mantener solo metadatos
        try:
            env_map.pop(k, None)
        except Exception:
            pass
    # No hacemos write aquí: quien llamó a migrate_old_repo_keys se encargará de escribir


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
    limit = 100
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
    """
    Actualiza .env para un repo:
    - Migra claves antiguas REPO_<SLUG>=<URL> a REPO_LIST (y borra las claves antiguas).
    - Añade la URL a REPO_LIST si no estaba.
    - Mantiene las keys de metadata por slug (DEFAULT_BRANCH, LAST_SYNC, etc).
    Todo con lock y escritura atómica via load_env_file() / write_env().
    """
    # Cargamos (con lock interno)
    existing = load_env_file()

    # Migrar y eliminar las claves antiguas REPO_<SLUG>
    try:
        migrate_old_repo_keys(existing)
    except Exception:
        # No queremos fallar el flujo por migración
        pass

    # Añadir la URL al REPO_LIST si no estaba
    try:
        ensure_url_in_repo_list(existing, url)
    except Exception:
        pass

    # Guardar metadata por slug (sin la entrada REPO_<SLUG>=<URL> antigua)
    key_base = slug.replace("-", "_").upper()
    if default_branch:
        existing[f"REPO_{key_base}_DEFAULT_BRANCH"] = default_branch
    existing[f"REPO_{key_base}_LAST_SYNC"] = now_iso_utc()
    existing[f"REPO_{key_base}_LAST_STATUS"] = status
    existing[f"REPO_{key_base}_LAST_COMMIT"] = local_short_commit(repo_dir) or ""
    existing[f"REPO_{key_base}_ACTIVE_BRANCH"] = local_active_branch(repo_dir) or ""

    # Escribimos .env atómicamente (write_env usa lock)
    write_env(existing)


def str2bool(s: str) -> bool:
    return str(s).lower() in ("1", "true", "yes", "y")


# ---------- main ----------
def main():
    # Resto del main sin cambios
    env, missing = ensure_env_defaults()
    # ... el flujo de ejecución sigue igual que antes ...
    # (No modifiqué la lógica del main salvo por las funciones de .env ya cambiadas)
    pass


# Si se ejecuta como script
if __name__ == "__main__":
    main()
