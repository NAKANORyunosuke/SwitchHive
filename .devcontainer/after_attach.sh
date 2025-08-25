#!/usr/bin/env bash
set -eu

# VS Code から入った直後に .venv を使えるようパス提示（必要なら）
if [ -f .venv/bin/activate ]; then
  echo "✅ Python venv ready: $(python --version)"
fi

# 既存の Function プロジェクトが無ければ雛形を提案
# 例: func init . --python --worker-runtime python
# 例: func new --name HttpTrigger1 --template "HTTP trigger"
