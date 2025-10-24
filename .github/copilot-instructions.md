
# Copilot Coding Agent Instructions for Bitbucket Env Sync

## 🏗️ General Architecture
- Python CLI project to synchronize Bitbucket Cloud and Server/DC repositories.
- Main file is `bb_sync.py`, handling sync logic, authentication, and incremental `.env` updates.
- Configuration and sync state are stored in `.env` (created/updated automatically).
- Main flow:
  1. Checks/creates `.env` and prompts for required fields if missing.
  2. Detects Cloud/Server mode based on `.env` variables.
  3. Syncs all repositories or only those listed in `REPO_LIST`.
  4. Updates per-repo metadata in `.env` after each successful operation.

## ⚙️ Development Workflows
- **Install dependencies:**
  - `make install` or `pip install requests pyinstaller`
- **Sync:**
  - `python bb_sync.py` (creates `.env` if missing)
- **Build binary:**
  - Windows: `make build-win` or `pyinstaller --onefile --name bb_sync bb_sync.py`
  - Linux: `make build-linux` or `pyinstaller --onefile --name bb_sync_linux bb_sync.py`
- **Format and lint:**
  - `black . && isort .`
  - `pre-commit run --all-files`
- **Tests:**
  - `pytest`

## 🗂️ Key Conventions and Patterns
- `.env` is the only persistent state; never modify manually during execution.
- Per-repo metadata keys follow the pattern: `REPO_<SLUG>_DEFAULT_BRANCH`, `REPO_<SLUG>_LAST_SYNC`, etc.
- Repository URLs migrate from individual keys to the comma-separated `REPO_LIST`.
- The script uses file locks for safe concurrent `.env` read/write.
- Recommended authentication: Git Credential Manager (PAT/App Password).
- Default is `INSECURE=true` for easy setup; use corporate CA and `INSECURE=false` for production.

## 🔗 Integrations and Dependencies
- Requires Python 3.9+, Git in PATH, and the `requests` package.
- Uses `pyinstaller` for local multiplatform builds.
- CI/CD via GitHub Actions (`.github/workflows/build.yml` and `release.yml`).
- Supports corporate CA integration via `BITBUCKET_CA_BUNDLE` and `GIT_CA_BUNDLE` variables.

## 📁 Key Files
- `bb_sync.py`: main logic and helpers.
- `.env`: configuration and sync state.
- `Makefile`: build and packaging commands.
- `pyproject.toml`, `setup.cfg`: dependencies and tool configuration.
- `README.md`: configuration and usage examples.

## 📝 Example of `.env` Metadata Pattern
```
REPO_LIST=https://bitbucket.org/workspace/repo1,https://bitbucket.org/workspace/repo2
REPO_REPO1_DEFAULT_BRANCH=main
REPO_REPO1_LAST_SYNC=2025-10-24T12:34:56Z
REPO_REPO1_LAST_STATUS=updated
REPO_REPO1_LAST_COMMIT=abc123
REPO_REPO1_ACTIVE_BRANCH=main
```

## 🚩 Agent Notes
- Do not modify `.env` without using the lock and atomic write helpers.
- Follow build and test flows as described in Makefile and README.
- Maintain cross-platform compatibility (Windows/Linux).
- Always validate required `.env` fields before running sync.
