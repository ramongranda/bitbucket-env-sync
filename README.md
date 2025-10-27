# Bitbucket Workspace Sync Tool

[![Build](https://github.com/ramongranda/bitbucket-env-sync/actions/workflows/build.yml/badge.svg)](https://github.com/ramongranda/bitbucket-env-sync/actions/workflows/build.yml)
[![Release](https://github.com/ramongranda/bitbucket-env-sync/actions/workflows/release.yml/badge.svg)](https://github.com/ramongranda/bitbucket-env-sync/actions/workflows/release.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A simple cross-platform CLI to clone and update repositories from a Bitbucket
workspace (Cloud) or project (Server/Data Center). It stores configuration and
incremental sync metadata in a single `.env` file and is designed for simple,
incremental repository synchronization.

## Features

* Supports Bitbucket Cloud and Bitbucket Server / Data Center.
* Auth via Git Credential Manager (App Password / PAT prompt on first use).
* Creates or updates `.env` automatically; exits if required values are missing.
* `INSECURE=true` by default (skips TLS verification) for frictionless setup.
* Supports corporate CA PEMs when `INSECURE=false`.
* Incremental metadata written to `.env` per-repo: DEFAULT_BRANCH, LAST_SYNC,
  LAST_STATUS, LAST_COMMIT, ACTIVE_BRANCH.

## REPO_LIST format

Repository URLs are stored in `REPO_LIST` as one URL per line (not comma-separated).
Example:

```env
REPO_LIST=https://bitbucket.org/workspace/repo1
https://bitbucket.org/workspace/repo2
```

## Requirements

* Python 3.9+
* Git installed and available in PATH
* Python packages: `requests` (runtime) and `pyinstaller` (optional, for builds)

Install runtime deps:

```bash
pip install requests
```

## Usage

1. First run to create a `.env` template:

```bash
python bb_sync.py
```

2. Fill the `.env` with required fields (examples below). If `REPO_LIST` is empty
   the tool will attempt to sync all repositories in the workspace/project.

Example `.env` (Server/DC auto-detection):

```dotenv
BITBUCKET_USER=your.user
BITBUCKET_WORKSPACE=https://bitbucket.example.com/projects/PROJ
BB_BASE_DIR=/home/you/workspaces
REPO_LIST=
INSECURE=true
BITBUCKET_CA_BUNDLE=
GIT_CA_BUNDLE=
# Bitbucket Workspace Sync Tool

[![Build](https://github.com/ramongranda/bitbucket-env-sync/actions/workflows/build.yml/badge.svg)](https://github.com/ramongranda/bitbucket-env-sync/actions/workflows/build.yml)
[![Release](https://github.com/ramongranda/bitbucket-env-sync/actions/workflows/release.yml/badge.svg)](https://github.com/ramongranda/bitbucket-env-sync/actions/workflows/release.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A simple cross-platform CLI to clone and update repositories from a Bitbucket
workspace (Cloud) or project (Server/Data Center). It stores configuration and
incremental sync metadata in a single `.env` file and is designed for simple,
incremental repository synchronization.

## Features

* Supports Bitbucket Cloud and Bitbucket Server / Data Center.
* Auth via Git Credential Manager (App Password / PAT prompt on first use).
* Creates or updates `.env` automatically; exits if required values are missing.
* `INSECURE=true` by default (skips TLS verification) for frictionless setup.
* Supports corporate CA PEMs when `INSECURE=false`.
* Incremental metadata written to `.env` per-repo: DEFAULT_BRANCH, LAST_SYNC,
  LAST_STATUS, LAST_COMMIT, ACTIVE_BRANCH.

## REPO_LIST format

Repository URLs are stored in `REPO_LIST` as one URL per line (not comma-separated).
Example:

```env
REPO_LIST=https://bitbucket.org/workspace/repo1
https://bitbucket.org/workspace/repo2
```

## Requirements

* Python 3.9+
* Git installed and available in PATH
* Python packages: `requests` (runtime) and `pyinstaller` (optional, for builds)

Install runtime deps:

```bash
pip install requests
```

## Usage

1. First run to create a `.env` template:

```bash
python bb_sync.py
```

2. Fill the `.env` with required fields (examples below). If `REPO_LIST` is empty
   the tool will attempt to sync all repositories in the workspace/project.

Example `.env` (Server/DC auto-detection):

```dotenv
BITBUCKET_USER=your.user
BITBUCKET_WORKSPACE=https://bitbucket.example.com/projects/PROJ
BB_BASE_DIR=/home/you/workspaces
REPO_LIST=
INSECURE=true
BITBUCKET_CA_BUNDLE=
GIT_CA_BUNDLE=
```

Example `.env` (Cloud):

```dotenv
BITBUCKET_USER=myuser
BITBUCKET_WORKSPACE=my-workspace
BB_BASE_DIR=/home/myuser/workspaces
REPO_LIST=
INSECURE=true
```

## Optional: automatic `.env` commits

Set `AUTO_COMMIT_ENV=true` in `.env` to opt in. When enabled the tool will
attempt to `git add .env` and `git commit -m "env: update <slug> <timestamp>"`
in the repository root after each successful per-repo update. Failures are
logged but do not stop the sync.

**WARNING:** do NOT store secrets, PATs or credentials in `.env` if you enable
this option. Prefer a separate non-sensitive metadata file if you need
versioning.

## Builds

Windows:

```powershell
pyinstaller --onefile --name bb_sync bb_sync.py
```

Linux:

```bash
pyinstaller --onefile --name bb_sync_linux bb_sync.py
```

Artifacts will appear in `dist/`.

## CI / GitHub Actions

The repository includes GitHub Actions workflows to build Windows and Linux
binaries and to attach them to releases. The build workflow triggers on pushes
to the `master` branch and on pull requests. Releases are produced from git tags
(`v*`).

## Development

Install developer tools:

```bash
pip install pre-commit black isort flake8
pre-commit install
```

Run checks locally:

```bash
pre-commit run --all-files
black . && isort .
flake8
```

## Makefile shortcuts

| Command            | Description                  |
| ------------------ | ---------------------------- |
| `make install`     | Install dependencies         |
| `make build-win`   | Build Windows binary         |
| `make build-linux` | Build Linux binary           |
| `make build-all`   | Build both                   |
| `make zip`         | Create deployable zip bundle |

## 🚀 Download

Get the latest binaries:

* [Windows](https://github.com/ramongranda/bitbucket-env-sync/releases/latest/download/bb_sync_Windows.exe)
* [Linux](https://github.com/ramongranda/bitbucket-env-sync/releases/latest/download/bb_sync_Linux)
* [All platforms (ZIP)](https://github.com/ramongranda/bitbucket-env-sync/releases/latest/download/release_assets.zip)

## License

This project is licensed under the MIT License. See the `LICENSE` file for the
full text.

## Author

Ramón Granda — Zoomiit · Asturias, Spain
