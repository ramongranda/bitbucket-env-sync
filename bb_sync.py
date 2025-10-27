from __future__ import annotations

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bitbucket Env Sync CLI – desde cero

Funciones clave:
- Lee/crea .env con bloqueo de archivo y escritura atómica.
- Pide campos obligatorios si faltan (solo la primera vez).
- Pre-carga credenciales en ~/.git-credentials (git credential.helper=store).
- Si REPO_LIST está vacío: descubre repos por API (Server/DC o Cloud) y rellena .env.
- Valida acceso contra el primer repo antes de clonar.
- Clona/actualiza todos los repos en BB_BASE_DIR con spinner.
- Actualiza metadatos por repo en .env (DEFAULT_BRANCH, LAST_SYNC, etc.).

Requisitos: Python 3.9+, git, requests.
"""

import contextlib
import getpass
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple
from urllib.parse import urlparse

import requests

API_CLOUD = "https://api.bitbucket.org/2.0"
ENV_FILE = Path(__file__).resolve().parent / ".env"
VERIFY: object = True  # True | False | path-to-PEM
DEBUG = str(os.environ.get("BB_SYNC_DEBUG", "0")).lower() in ("1", "true", "yes", "y")

# =========================================================
# util / logging
# =========================================================

def write_repo_audit(url: str, sync_date: str, branch: str) -> None:
    audit_file = Path(__file__).resolve().parent / ".repo_audit"
    line = f"{url} | {sync_date} | {branch}\n"
    with open(audit_file, "a", encoding="utf-8") as f:
        f.write(line)


def log_debug(msg: str) -> None:
    if DEBUG:
        sys.stderr.write(f"[debug] {msg}\n")
        sys.stderr.flush()


def now_iso_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def str2bool(s: str) -> bool:
    return str(s).lower() in ("1", "true", "yes", "y")

# =========================================================
# spinner
# =========================================================

class Spinner:
    def __init__(self, text: str = "Procesando"):
        self.text = text
        self._stop = threading.Event()
        self._th: Optional[threading.Thread] = None

    def start(self) -> None:
        def run():
            i = 0
            frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
            while not self._stop.is_set():
                sys.stdout.write(f"\r{self.text} {frames[i % len(frames)]}")
                sys.stdout.flush()
                time.sleep(0.08)
                i += 1
        self._th = threading.Thread(target=run, daemon=True)
        self._th.start()

    def stop(self, suffix: str = " listo") -> None:
        self._stop.set()
        if self._th:
            self._th.join(timeout=1)
        sys.stdout.write(f"\r{self.text}{suffix}\n")
        sys.stdout.flush()

@contextlib.contextmanager
def spinning(text: str):
    sp = Spinner(text)
    sp.start()
    try:
        yield sp
    finally:
        sp.stop()

# =========================================================
# .env helpers (lock + atomic write)
# =========================================================

@contextlib.contextmanager
def file_lock(target_path: Path, timeout: float = 10.0):
    lock_path = Path(str(target_path) + ".lock")
    f = open(lock_path, "w")
    try:
        start = time.time()
        while True:
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except Exception:
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
        try:
            lock_path.unlink()
        except Exception:
            pass

def ensure_env_parent() -> None:
    ENV_FILE.parent.mkdir(parents=True, exist_ok=True)


def load_env_file() -> dict:
    env = {}
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
        if ENV_FILE.exists():
            for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip()
                    os.environ.setdefault(k.strip(), v.strip())
    return env


def write_env(env_map: dict) -> None:
    ensure_env_parent()
    lines = [
        "# Bitbucket Sync .env",
        "# Fill required values. INSECURE=true by default.",
        "",
    ]
    for k, v in env_map.items():
        lines.append(f"{k}={v}")
    content = "\n".join(lines) + "\n"

    try:
        with file_lock(ENV_FILE):
            dirpath = str(ENV_FILE.parent)
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=dirpath, delete=False) as tf:
                tf.write(content)
                temp_name = tf.name
            os.replace(temp_name, str(ENV_FILE))
    except TimeoutError:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(ENV_FILE.parent), delete=False) as tf:
            tf.write(content)
            temp_name = tf.name
        os.replace(temp_name, str(ENV_FILE))


def normalize_url_for_list(url: str) -> str:
    return url.strip().rstrip("/")


def parse_repo_list(text: str) -> List[str]:
    if not text:
        return []
    items: List[str] = []
    for line in text.splitlines():
        for part in line.split(","):
            p = part.strip()
            if p:
                items.append(p)
    return items


def ensure_url_in_repo_list(env_map: dict, url: str) -> bool:
    raw = env_map.get("REPO_LIST", "") or ""
    items = [x.strip() for x in raw.splitlines() if x.strip()]
    normalized_existing = {normalize_url_for_list(u) for u in items}
    norm_url = normalize_url_for_list(url)
    if norm_url in normalized_existing:
        return False
    items.append(norm_url)
    env_map["REPO_LIST"] = "\n".join(items)
    return True


def migrate_old_repo_keys(env_map: dict) -> None:
    keys = list(env_map.keys())
    for k in keys:
        if re.fullmatch(r"REPO_[A-Z0-9_]+", k):
            env_map.pop(k, None)

# =========================================================
# Bitbucket helpers (Cloud / Server)
# =========================================================

@dataclass
class Repo:
    url: str
    host: str
    kind: str  # "cloud" | "server"
    workspace: Optional[str] = None
    project: Optional[str] = None
    slug: Optional[str] = None


def parse_repo_url(url: str) -> Repo:
    u = urlparse(url)
    host = u.netloc.lower()
    parts = [p for p in u.path.split("/") if p]
    if host.endswith("bitbucket.org") and len(parts) >= 2:
        # Cloud: https://bitbucket.org/<workspace>/<repo>
        return Repo(url=url, host=host, kind="cloud", workspace=parts[0], slug=parts[1].replace(".git", ""))
    # Server/DC: https://host/scm/PROJ/repo(.git)  o  https://host/projects/PROJ/repos/repo
    proj = None
    slug = None
    if len(parts) >= 3 and parts[0].lower() == "scm":
        proj = parts[1]
        slug = parts[2].replace(".git", "")
    elif len(parts) >= 4 and parts[0].lower() == "projects" and parts[2].lower() == "repos":
        proj = parts[1]
        slug = parts[3].replace(".git", "")
    return Repo(url=url, host=host, kind="server", project=proj, slug=slug)


def http_get(url: str, auth, params=None) -> requests.Response:
    return requests.get(url, auth=auth, params=params, timeout=60, verify=VERIFY)


def list_repo_clone_urls_server(base_url: str, project: str, auth, cred_host: str) -> List[str]:
    """Devuelve HTTPS clone URLs de todos los repos del proyecto (Server/DC)."""
    urls, start = [], 0
    while True:
        r = http_get(
            f"{base_url}/rest/api/1.0/projects/{project}/repos",
            auth,
            params={"limit": 100, "start": start},
        )
        if r.status_code in (401, 403):
            remove_git_credentials(cred_host)
            raise SystemExit("[AUTH] Credenciales inválidas para Server/DC")
        if r.status_code != 200:
            raise SystemExit(f"[ERR] Server API {r.status_code}: {r.text[:200]}")
        data = r.json()
        for repo in data.get("values", []):
            clones = repo.get("links", {}).get("clone", [])
            href = next((c.get("href") for c in clones if c.get("name", "").lower() in ("http", "https")), None)
            if href:
                urls.append(href.rstrip("/"))
        if data.get("isLastPage", True):
            break
        start = data.get("nextPageStart", 0)
    return urls


def list_repo_clone_urls_cloud(workspace: str, auth, cred_host: str) -> List[str]:
    """Devuelve HTTPS clone URLs de todos los repos del workspace (Cloud)."""
    urls = []
    url = f"{API_CLOUD}/repositories/{workspace}"
    params = {"pagelen": 100}
    while True:
        r = http_get(url, auth, params=params)
        if r.status_code in (401, 403):
            remove_git_credentials(cred_host)
            raise SystemExit("[AUTH] Credenciales inválidas para Cloud")
        if r.status_code != 200:
            raise SystemExit(f"[ERR] Cloud API {r.status_code}: {r.text[:200]}")
        data = r.json()
        for repo in data.get("values", []):
            clones = repo.get("links", {}).get("clone", [])
            href = next((c.get("href") for c in clones if c.get("name", "").lower() == "https"), None)
            if href:
                urls.append(href.rstrip("/"))
        url = data.get("next")
        if not url:
            break
        params = {}
    return urls


def validate_first_repo(repo: Repo, auth, cred_host: str) -> None:
    """Valida credenciales contra un repo concreto antes de clonar."""
    if repo.kind == "cloud" and repo.workspace and repo.slug:
        api = f"{API_CLOUD}/repositories/{repo.workspace}/{repo.slug}"
        with spinning(f"Validando acceso a {repo.workspace}/{repo.slug}"):
            r = http_get(api, auth)
        if r.status_code == 200:
            return
        if r.status_code in (401, 403):
            remove_git_credentials(cred_host)
            raise SystemExit("[AUTH] Credenciales inválidas para Bitbucket Cloud (401/403)")
        raise SystemExit(f"[ERR] Cloud API {r.status_code}: {r.text[:200]}")
    # Server/DC
    u = urlparse(repo.url)
    base = f"{u.scheme}://{u.netloc}"
    if repo.project and repo.slug:
        api = f"{base}/rest/api/1.0/projects/{repo.project}/repos/{repo.slug}"
    else:
        api = f"{base}/rest/api/1.0/projects"
    with spinning(f"Validando acceso a {u.netloc}"):
        r = http_get(api, auth)
    if r.status_code == 200:
        return
    if r.status_code in (401, 403):
        remove_git_credentials(cred_host)
        raise SystemExit("[AUTH] Credenciales inválidas para Bitbucket Server/DC (401/403)")
    raise SystemExit(f"[ERR] Server API {r.status_code}: {r.text[:200]}")

# =========================================================
# git helpers
# =========================================================

def run_git(
    cmd: List[str],
    cwd: Optional[Path] = None,
    git_ca_bundle: Optional[str] = None,
    insecure: bool = False,
    stream_output: bool = False,
) -> int:
    env = os.environ.copy()
    if git_ca_bundle:
        env["GIT_SSL_CAINFO"] = git_ca_bundle
        env["CURL_CA_BUNDLE"] = git_ca_bundle
    if insecure:
        env["GIT_SSL_NO_VERIFY"] = "1"
    env.pop("GIT_TERMINAL_PROMPT", None)  # permitir prompts de credenciales
    env["GIT_PROGRESS"] = "1"
    if not stream_output:
        return subprocess.call(["git"] + cmd, cwd=str(cwd) if cwd else None, env=env)

    process = subprocess.Popen(
        ["git"] + cmd,
        cwd=str(cwd) if cwd else None,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert process.stdout is not None
    try:
        for line in process.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
    finally:
        process.stdout.close()
    return process.wait()


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


def default_branch(repo_dir: Path) -> str:
    ref = run_git_capture(["symbolic-ref", "refs/remotes/origin/HEAD"], cwd=repo_dir)
    if ref and "/" in ref:
        return ref.split("/")[-1]
    b = run_git_capture(["rev-parse", "--abbrev-ref", "origin/HEAD"], cwd=repo_dir)
    if b and "/" in b:
        return b.split("/")[-1]
    return ""

# =========================================================
# pre-carga de credenciales (git store)
# =========================================================

def _match_credential_host(line: str, host: str) -> bool:
    try:
        parsed = urlparse(line.strip())
        netloc = parsed.netloc or ""
        host_part = netloc.rsplit("@", 1)[-1]
        return host_part.lower() == host.lower()
    except Exception:
        return False


def ensure_git_credentials_store(host: str, user: str, password: str) -> None:
    """Garantiza que ~/.git-credentials contiene https://user:password@host y helper=store."""
    cred_file = Path.home() / ".git-credentials"
    cred_file.parent.mkdir(parents=True, exist_ok=True)
    entry = f"https://{user}:{password}@{host}\n"
    existing_lines: list[str] = []
    if cred_file.exists():
        try:
            existing_lines = [
                line for line in cred_file.read_text(encoding="utf-8", errors="ignore").splitlines() if line.strip()
            ]
        except Exception:
            existing_lines = []

    filtered = [line for line in existing_lines if not _match_credential_host(line, host)]
    filtered.append(entry.strip())

    with cred_file.open("w", encoding="utf-8") as f:
        f.write("\n".join(filtered) + "\n")
    try:
        cred_file.chmod(0o600)
    except Exception:
        pass
    subprocess.run(["git", "config", "--global", "credential.helper", "store"], check=False)


def ensure_store_has_credentials(host: str, user: str, password: str) -> None:
    """Verifica si las credenciales actuales están en git-store; si no, las guarda."""
    cred_file = Path.home() / ".git-credentials"
    if cred_file.exists():
        try:
            for line in cred_file.read_text(encoding="utf-8", errors="ignore").splitlines():
                if _match_credential_host(line, host) and f"{user}:{password}@" in line:
                    return
        except Exception:
            pass
    ensure_git_credentials_store(host, user, password)


def get_env_credentials(env_map: dict) -> tuple[str, str]:
    user = (env_map.get("BITBUCKET_USERNAME") or "").strip()
    password = (env_map.get("BITBUCKET_PASSWORD") or "").strip()
    if not user or not password:
        raise SystemExit(
            "Faltan credenciales en .env (BITBUCKET_USERNAME/BITBUCKET_PASSWORD). Actualiza el archivo y reintenta."
        )
    host = resolve_bitbucket_host(env_map)
    ensure_store_has_credentials(host, user, password)
    return user, password


def remove_git_credentials(host: str) -> None:
    """Elimina credenciales almacenadas para un host dado."""
    cred_file = Path.home() / ".git-credentials"
    if not cred_file.exists():
        return
    try:
        lines = [line for line in cred_file.read_text(encoding="utf-8", errors="ignore").splitlines()]
    except Exception:
        return
    filtered = [line for line in lines if not _match_credential_host(line, host)]
    if len(filtered) == len(lines):
        return
    with cred_file.open("w", encoding="utf-8") as f:
        f.write(("\n".join(filtered) + "\n") if filtered else "")
    print(f"[INFO] Se borraron credenciales almacenadas para {host}. Actualiza BITBUCKET_USERNAME/BITBUCKET_PASSWORD si es necesario.")


def resolve_bitbucket_host(env_map: dict) -> str:
    if env_map.get("BITBUCKET_BASE_URL"):
        return urlparse(env_map["BITBUCKET_BASE_URL"]).netloc
    if env_map.get("BITBUCKET_WORKSPACE"):
        return "bitbucket.org"
    return "bitbucket.mova.indra.es"


def first_auth(env_map: dict) -> tuple[str, str]:
    """Obtiene (usuario, password) desde git-store; si no existen, los solicita y guarda."""
    return get_env_credentials(env_map)

# =========================================================
# per-repo metadata in .env
# =========================================================

def update_env_repo(slug: str, url: str, def_branch: str, status: str, repo_dir: Path) -> None:
    pass  # Eliminado: ahora el registro se hará en .repo_audit

# =========================================================
# core env + discovery
# =========================================================

def ensure_env_defaults() -> Tuple[dict, List[str]]:
    env_map = load_env_file()
    changed = False
    if "INSECURE" not in env_map:
        env_map["INSECURE"] = "true"
        changed = True
    if "REPO_LIST" not in env_map:
        env_map["REPO_LIST"] = ""
        changed = True
    if "BITBUCKET_USERNAME" not in env_map:
        env_map["BITBUCKET_USERNAME"] = ""
        changed = True
    if "BITBUCKET_PASSWORD" not in env_map:
        env_map["BITBUCKET_PASSWORD"] = ""
        changed = True
    if changed:
        write_env(env_map)

    missing: List[str] = []
    if not env_map.get("BB_BASE_DIR"):
        missing.append("BB_BASE_DIR")
    if not env_map.get("BITBUCKET_WORKSPACE") and not (env_map.get("BITBUCKET_BASE_URL") and env_map.get("BITBUCKET_PROJECT")):
        missing.append("BITBUCKET_WORKSPACE o (BITBUCKET_BASE_URL y BITBUCKET_PROJECT)")
    if not env_map.get("BITBUCKET_USERNAME"):
        missing.append("BITBUCKET_USERNAME")
    if not env_map.get("BITBUCKET_PASSWORD"):
        missing.append("BITBUCKET_PASSWORD")

    return env_map, missing


def prompt_missing(env_map: dict, missing_keys: List[str]) -> dict:
    print("Config .env incompleta. Te pido los datos mínimos:")
    # Se solicitan datos de ruta y destino Bitbucket; credenciales se piden aparte.
    if "BB_BASE_DIR" in missing_keys:
        base = input("BB_BASE_DIR (ruta donde clonar): ").strip()
        env_map["BB_BASE_DIR"] = base or str((Path.home() / "bitbucket").resolve())
    if "BITBUCKET_WORKSPACE o (BITBUCKET_BASE_URL y BITBUCKET_PROJECT)" in missing_keys:
        ws = input("BITBUCKET_WORKSPACE (Cloud) [deja vacío si usas Server]: ").strip()
        if ws:
            env_map["BITBUCKET_WORKSPACE"] = ws
        else:
            base = input("BITBUCKET_BASE_URL (Server, ej https://bitbucket.miempresa.com): ").strip()
            proj = input("BITBUCKET_PROJECT (clave del proyecto, ej MIPROY): ").strip()
            env_map["BITBUCKET_BASE_URL"] = base
            env_map["BITBUCKET_PROJECT"] = proj
    if "BITBUCKET_USERNAME" in missing_keys:
        env_map["BITBUCKET_USERNAME"] = input("BITBUCKET_USERNAME: ").strip()
    if "BITBUCKET_PASSWORD" in missing_keys:
        env_map["BITBUCKET_PASSWORD"] = getpass.getpass("BITBUCKET_PASSWORD: ").strip()
    write_env(env_map)
    return env_map


def ensure_repo_list(env_map: dict) -> List[str]:
    """Lee REPO_LIST; si está vacío, descubre por API y guarda en .env."""
    urls = parse_repo_list(env_map.get("REPO_LIST", ""))
    if urls:
        return [normalize_url_for_list(u) for u in urls]

    auth = first_auth(env_map)
    cred_host = resolve_bitbucket_host(env_map)
    workspace = (env_map.get("BITBUCKET_WORKSPACE") or "").strip()
    base_url = (env_map.get("BITBUCKET_BASE_URL") or "").strip()
    project = (env_map.get("BITBUCKET_PROJECT") or "").strip()

    if base_url and project:
        print(f"[INFO] Descubriendo repos en {base_url} proyecto {project} …")
        discovered = list_repo_clone_urls_server(base_url, project, auth, cred_host)
    elif workspace:
        print(f"[INFO] Descubriendo repos en workspace Cloud {workspace} …")
        discovered = list_repo_clone_urls_cloud(workspace, auth, cred_host)
    else:
        raise SystemExit("[ERR] Falta destino: define BITBUCKET_WORKSPACE (Cloud) o BITBUCKET_BASE_URL+BITBUCKET_PROJECT (Server).")

    if not discovered:
        raise SystemExit("[ERR] No se encontraron repos en el workspace/proyecto.")

    existing = load_env_file()
    for u in discovered:
        ensure_url_in_repo_list(existing, u)
    write_env(existing)

    return [normalize_url_for_list(u) for u in discovered]

# =========================================================
# core clone/update
# =========================================================

def ensure_basedir(path_str: str) -> Path:
    # evita errores si por accidente ponen rutas Windows dentro de WSL
    p_str = path_str.replace("\\", "/")
    # Convertir rutas de Windows a WSL si estamos en WSL y la ruta parece de Windows
    if os.name != "nt" and (p_str.startswith("C:/") or p_str.startswith("D:/") or p_str.startswith("E:/")):
        drive = p_str[0].lower()
        rest = p_str[3:]
        p_str = f"/mnt/{drive}{rest}"
    p = Path(p_str).expanduser().resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def clone_or_update(repo: Repo, base_dir: Path, insecure: bool, ca_bundle: Optional[str], shallow: bool) -> Tuple[str, Path]:
    name = repo.slug or Path(urlparse(repo.url).path).name.replace(".git", "")
    dest = base_dir / name
    if dest.exists() and (dest / ".git").exists():
        print(f"\nActualizando {name} …")
        rc = run_git(
            ["fetch", "--all"],
            cwd=dest,
            insecure=insecure,
            git_ca_bundle=ca_bundle,
            stream_output=True,
        )
        if rc == 0:
            rc_pull = run_git(
                ["pull", "--ff-only"],
                cwd=dest,
                insecure=insecure,
                git_ca_bundle=ca_bundle,
                stream_output=True,
            )
            if rc_pull == 0:
                print(f"[OK] {name} actualizado.")
                return ("updated", dest)
        print(f"[ERR] No se pudo actualizar {name}.")
        return ("error", dest)
    else:
        print(f"\nClonando {name} desde {repo.url} …")
        clone_cmd = ["clone"]
        if shallow:
            clone_cmd.extend(["--depth", "1"])
        clone_cmd.extend([repo.url, str(dest)])
        rc = run_git(
            clone_cmd,
            insecure=insecure,
            git_ca_bundle=ca_bundle,
            stream_output=True,
        )
        if rc == 0:
            print(f"[OK] {name} clonado en {dest}.")
            return ("cloned", dest)
        print(f"[ERR] No se pudo clonar {name}.")
        return ("error", dest)

# =========================================================
# main
# =========================================================

def main() -> int:
    print("BB_SYNC starting…")
    log_debug(f"python={sys.version.split()[0]} cwd={os.getcwd()} env_file={ENV_FILE}")

    env, missing = ensure_env_defaults()
    if missing:
        env = prompt_missing(env, missing)

    # TLS/verify desde .env
    global VERIFY
    insecure = str2bool(env.get("INSECURE", "true"))
    ca_bundle = env.get("CA_BUNDLE") or env.get("BITBUCKET_CA_BUNDLE") or env.get("GIT_CA_BUNDLE") or None
    VERIFY = False if insecure else (ca_bundle if ca_bundle else True)
    shallow = str2bool(env.get("SHALLOW_CLONE", "false"))

    # Descubre/lee la lista (también asegura credenciales)
    cred_host = resolve_bitbucket_host(env)
    # Descubre/lee la lista (también asegura credenciales)
    cred_host = resolve_bitbucket_host(env)
    urls = ensure_repo_list(env)
    if not urls:
        print("[ERR] No se encontraron repositorios para sincronizar.")
        return 2

    base_dir = ensure_basedir(env.get("BB_BASE_DIR", "./repos"))

    # Valida contra el primer repo
    first_repo = parse_repo_url(urls[0])
    user, pw = first_auth(env)  # asegura que creds existen
    validate_first_repo(first_repo, (user, pw), cred_host)

    # Procesa todos
    for url in urls:
        repo = parse_repo_url(url)
        # No modificar REPO_LIST durante sincronización; solo auditar
        _, repo_dir = clone_or_update(repo, base_dir, insecure, ca_bundle, shallow)
        try:
            dbranch = default_branch(repo_dir)
        except Exception:
            dbranch = ""
        try:
            sync_date = now_iso_utc()
            branch = dbranch or local_active_branch(repo_dir)
            write_repo_audit(url, sync_date, branch)
        except Exception as e:
            print(f"[WARN] No se pudo registrar auditoría para {url}: {e}")

    print("\nTodo listo. Repos sincronizados/actualizados.")
    return 0


if __name__ == "__main__":
    try:
        rc = main()
        sys.exit(rc)
    except KeyboardInterrupt:
        print("\nInterrumpido por el usuario.")
        sys.exit(130)
    except SystemExit:
        raise
    except Exception as e:
        print(f"[ERR] {e}")
        if DEBUG:
            import traceback
            traceback.print_exc()
        sys.exit(1)
