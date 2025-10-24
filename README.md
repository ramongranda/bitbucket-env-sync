# Bitbucket Workspace Sync Tool

[![Build](https://github.com/ramongranda/bitbucket-env-sync/actions/workflows/build.yml/badge.svg)](https://github.com/ramongranda/bitbucket-env-sync/actions/workflows/build.yml)
[![Release](https://github.com/ramongranda/bitbucket-env-sync/actions/workflows/release.yml/badge.svg)](https://github.com/ramongranda/bitbucket-env-sync/actions/workflows/release.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A simple cross-platform CLI to **clone and update all repositories** from a Bitbucket
workspace (Cloud) or project (Server/Data Center).
It maintains a `.env` file with configuration and **incremental sync metadata** per repository.

---

## ✨ Features

* Supports **Bitbucket Cloud** and **Bitbucket Server / Data Center**.
* Auth via **Git Credential Manager** (App Password / PAT prompt on first use).
* Creates or updates `.env` automatically; exits if required values are missing.
* `INSECURE=true` by default (skips TLS verification) for frictionless setup.
* Supports corporate CA PEMs when `INSECURE=false`.
* Incrementally updates `.env` after each successful repo:

  * `REPO_<SLUG>=<URL>`
  * `REPO_<SLUG>_DEFAULT_BRANCH`
  * `REPO_<SLUG>_LAST_SYNC` (UTC)
  * `REPO_<SLUG>_LAST_STATUS` (`cloned|updated`)
  * `REPO_<SLUG>_LAST_COMMIT`
  * `REPO_<SLUG>_ACTIVE_BRANCH`

---

## 🚀 Requirements

* Python **3.9+**
* Git installed and available in PATH
* Packages:

  ```bash
  pip install requests pyinstaller
  ```

---

## ⚙️ Usage

### 1. First run

Run once to create a `.env` template:

```bash
python bb_sync.py
```

If any required fields are missing, the script prints them and exits.

---

### 2. Fill the `.env`

#### Example for Bitbucket Server/DC (project URL auto-detection)

```dotenv
BITBUCKET_USER=rgranda.INDRA
BITBUCKET_WORKSPACE=https://bitbucket.mova.indra.es/projects/PRECTICNTA
BB_BASE_DIR=C:\Users\rgranda.INDRA\workspaces\NTA
REPO_LIST=
INSECURE=true
BITBUCKET_CA_BUNDLE=
GIT_CA_BUNDLE=
```

#### Example for Bitbucket Cloud

```dotenv
BITBUCKET_USER=myuser
BITBUCKET_WORKSPACE=my-workspace
BB_BASE_DIR=/home/myuser/workspaces/NTA
REPO_LIST=
INSECURE=true
BITBUCKET_CA_BUNDLE=
GIT_CA_BUNDLE=
```

#### Example for explicit Server/DC configuration

```dotenv
BITBUCKET_USER=myuser
BITBUCKET_BASE_URL=https://bitbucket.example.com
BITBUCKET_PROJECT=PROJ
BB_BASE_DIR=/home/myuser/workspaces/NTA
REPO_LIST=repo-a,repo-b
INSECURE=false
BITBUCKET_CA_BUNDLE=/path/to/corporate.pem
GIT_CA_BUNDLE=/path/to/corporate.pem
```

---

### 3. Run synchronization

```bash
python bb_sync.py
```

* If `REPO_LIST` is empty → syncs **all** repos.
* If not → syncs only the listed ones.
* Each successful operation **immediately updates `.env`** with metadata.

---

## 🔑 Authentication (Option A — recommended)

* **Server/DC** → Create a **Personal Access Token (PAT)** with read permissions.
* **Cloud** → Create an **App Password**.
* On the first run, Git Credential Manager will prompt:

  * Username → your Bitbucket username
  * Password → PAT/App Password
* To cache credentials permanently:

  ```bash
  git config --global credential.helper manager-core
  git config --global credential.interactive never
  ```

---

## 🔒 TLS / Corporate CA

* Quick start: `INSECURE=true` (disables verification)
* Production setup:

  ```dotenv
  INSECURE=false
  BITBUCKET_CA_BUNDLE=/path/to/corporate.pem
  GIT_CA_BUNDLE=/path/to/corporate.pem
  ```

---

## 🧱 Local builds

### Windows

```powershell
pyinstaller --onefile --name bb_sync bb_sync.py
```

### Linux

```bash
pyinstaller --onefile --name bb_sync_linux bb_sync.py
```

Artifacts will appear in `dist/`.

---

## 🧹 Project layout

```
bitbucket-env-sync/
├─ bb_sync.py
├─ README.md
├─ setup.cfg
├─ pyproject.toml
├─ Makefile
├─ bb_sync.spec
├─ .pre-commit-config.yaml
├─ .gitignore
└─ .github/workflows/
   ├─ build.yml
   └─ release.yml
```

---

## 🥪 Development

Install tools:

```bash
pip install pre-commit black isort pytest
pre-commit install
```

Run all checks manually:

```bash
pre-commit run --all-files
black . && isort .
pytest
```

---

## 🚚 CI/CD (GitHub Actions)

* **build.yml** → builds Windows & Linux binaries on push/PR
* **release.yml** → builds and attaches binaries to tagged releases

Create a release:

```bash
git tag v0.1.0
git push origin v0.1.0
```

---

## 🧮 Makefile shortcuts

| Command            | Description                  |
| ------------------ | ---------------------------- |
| `make install`     | Install dependencies         |
| `make build-win`   | Build Windows binary         |
| `make build-linux` | Build Linux binary           |
| `make build-all`   | Build both                   |
| `make zip`         | Create deployable zip bundle |

---

## 🗾 License (MIT)

```
MIT License

Copyright (c) 2025 Ramón Granda

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
```

---

## 👨‍💻 Author

**Ramón Granda**
Indra Mobility · Asturias, Spain
