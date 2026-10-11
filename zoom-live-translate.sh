#!/bin/bash
# Zoom 直播实时翻译：BlackHole 抓 Zoom 声音 → whisper-stream 本地识别 → Gemini 翻成中文
#
# 用法：
#   ./zoom-live-translate.sh          # 默认讲者说越南语
#   ./zoom-live-translate.sh en       # 讲者说英语（任何 whisper 支持的语言代码都行）
# 停止：Ctrl+C。逐字稿存在 $OUT_DIR（默认 ./transcripts）。
#
# 前置（只做一次）：swift zoom-live-translate-audio-setup.swift
# 会创建「Zoom+字幕」多输出设备（扬声器 + BlackHole 2ch）并设为系统输出。

set -euo pipefail

LANG_CODE="${1:-vi}"
HERE="$(cd "$(dirname "$0")" && pwd)"
MODEL="${WHISPER_MODEL:-$HOME/.cache/whisper-models/ggml-large-v3-turbo.bin}"
PYTHON="${PYTHON:-python3}"
OUT_DIR="${OUT_DIR:-$HERE/transcripts}"
OUT_FILE="$OUT_DIR/$(date +%Y-%m-%d-%H%M)-zoom逐字稿.md"

[ -f "$MODEL" ] || { echo "缺 whisper 模型：$MODEL（见 README 下载步骤）"; exit 1; }
command -v whisper-stream >/dev/null || { echo "缺 whisper-stream：brew install whisper-cpp"; exit 1; }

# 自动找 BlackHole 在 whisper-stream 里的设备编号（编号会随插拔耳机变化）
DEVICE_ID="$( (whisper-stream -m "$MODEL" -c 99 2>&1 & p=$!; sleep 12; kill $p 2>/dev/null) \
  | grep -o "Capture device #[0-9]*: 'BlackHole 2ch'" | grep -o '[0-9]*' | head -1 || true)"
[ -n "$DEVICE_ID" ] || { echo "找不到 BlackHole 2ch 采集设备，请确认已安装并重启过"; exit 1; }

mkdir -p "$OUT_DIR"
echo "🎧 采集设备 #$DEVICE_ID BlackHole 2ch ｜ 语言 $LANG_CODE ｜ 逐字稿 $OUT_FILE"

# script 包一层伪终端，强制 whisper-stream 行缓冲，否则管道里会攒成一坨才出来
# --step 0 = VAD 模式：讲者说完一句停顿时整句识别，比固定切片准确
script -q /dev/null whisper-stream \
  -m "$MODEL" -l "$LANG_CODE" -c "$DEVICE_ID" \
  --step 0 --length 12000 -t 8 -vth 0.6 \
  2>/dev/null \
| "$PYTHON" "$HERE/zoom-live-translate.py" --out "$OUT_FILE" --lang "$LANG_CODE"
