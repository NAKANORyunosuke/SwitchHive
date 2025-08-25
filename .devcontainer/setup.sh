#!/usr/bin/env bash
set -euxo pipefail

# Python 仮想環境
if [ ! -d .venv ]; then
  python -m venv .venv
fi
. .venv/bin/activate
python -m pip install --upgrade pip

# 依存（任意のマネージャに対応）
if [ -f requirements.txt ]; then
  pip install -r requirements.txt
elif command -v uv >/dev/null 2>&1 && [ -f pyproject.toml ]; then
  uv pip install -r pyproject.toml
elif [ -f pyproject.toml ] && grep -q '\[tool.poetry\]' pyproject.toml; then
  pip install poetry && poetry config virtualenvs.in-project true && poetry install --no-interaction
fi

# Azure Functions Core Tools v4 と Azurite（npm 経由）
# ※ --unsafe-perm はコンテナ内グローバルインストール時の権限対策
npm install -g azure-functions-core-tools@4 --unsafe-perm true
npm install -g azurite

# 便利 CLI（必要ならコメントアウトを外す）
# pip install azure-functions
# pip install azure-identity azure-storage-blob
