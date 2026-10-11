#!/bin/bash
# Zoom 聊天区实时翻译：读 Zoom 聊天窗口的新消息 → Gemini 翻成中文
#
# 用法：./zoom-chat-translate.sh       停止：Ctrl+C
# 前置：Zoom 里把聊天打开并停在最底部；运行它的终端 App 需要「辅助功能」权限
# 聊天记录存在 $OUT_DIR（默认 ./transcripts）。鉴权、模型和字幕翻译共用同一套环境变量。

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
PYTHON="${PYTHON:-python3}"
OUT_DIR="${OUT_DIR:-$HERE/transcripts}"
OUT_FILE="$OUT_DIR/$(date +%Y-%m-%d-%H%M)-zoom聊天记录.md"

swift "$HERE/zoom-chat-translate-reader.swift" \
| "$PYTHON" -u "$HERE/zoom-chat-translate.py" --out "$OUT_FILE"
