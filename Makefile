# Simple build & release helpers
PY ?= python
PIP ?= pip
NAME ?= bb_sync

.PHONY: help install clean build-win build-linux build-all zip

help:
	@echo "targets: install | build-win | build-linux | build-all | clean | zip"

install:
	$(PIP) install --upgrade pip
	$(PIP) install requests pyinstaller

build-win:
	pyinstaller --onefile --name $(NAME)_windows bb_sync.py

build-linux:
	pyinstaller --onefile --name $(NAME)_linux bb_sync.py

build-all: install build-win build-linux

clean:
	- rm -rf build dist __pycache__ *.spec

zip:
	@mkdir -p package
	@cp -f bb_sync.py README.md package/ 2>/dev/null || true
	@cp -f dist/* package/ 2>/dev/null || true
	@cd package && zip -r ../$(NAME)_bundle.zip .
	@rm -rf package
	@echo "=> $(NAME)_bundle.zip ready"
