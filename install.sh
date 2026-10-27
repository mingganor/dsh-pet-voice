#!/usr/bin/env bash
# 一键安装：把耳朵 / 嘴 / 语音桥装到 ~/.dsh，并给 dsh-pet 打上语音壳
# 用法：
#   bash install.sh
#   PET_PYTHON=/path/to/python bash install.sh   # 指定装了依赖的解释器
set -euo pipefail
cd "$(dirname "$0")"

PY="${PET_PYTHON:-python3}"
DSH="$HOME/.dsh"
PLUGIN="$DSH/profiles/desktop/node_modules/dsh-pet/lib/index.js"
MODEL="$DSH/models/campplus.onnx"

echo "→ Python: $PY"
if ! "$PY" -c "import faster_whisper, sounddevice, numpy, sherpa_onnx, edge_tts, opencc" 2>/dev/null; then
    echo "✗ 缺依赖。先装："
    echo "    $PY -m pip install -r requirements.txt"
    exit 1
fi

mkdir -p "$DSH/pet-bridge" "$DSH/models"
install -m 755 src/pet-listen.py src/pet-speak.py src/pet-voiceid.py "$DSH/"
install -m 644 src/bridge/voice-server.mjs "$DSH/pet-bridge/"
install -m 755 src/bridge/install.py src/bridge/health.py "$DSH/pet-bridge/"
printf '%s\n' "$PY" > "$DSH/pet-python"
echo "→ 已复制到 $DSH（并记下 Python 路径）"

if [ ! -s "$MODEL" ]; then
    echo "→ 下载声纹模型（约 28 MB）…"
    URL="${PET_VP_MODEL_URL:-https://huggingface.co/csukuangfj/speaker-embedding-models/resolve/main/3dspeaker_speech_campplus_sv_zh-cn_16k-common.onnx}"
    curl -fL --retry 3 -o "$MODEL" "$URL" ||
        curl -fL --retry 3 -o "$MODEL" "https://hf-mirror.com/csukuangfj/speaker-embedding-models/resolve/main/3dspeaker_speech_campplus_sv_zh-cn_16k-common.onnx"
fi

if [ -f "$PLUGIN" ]; then
    echo "→ 给插件打语音壳…"
    "$PY" "$DSH/pet-bridge/install.py" --apply
else
    echo "⚠ 没找到 dsh-pet：$PLUGIN"
    echo "  先装插件（dsh plugin --profile desktop add dsh-pet），再重跑本脚本"
    exit 1
fi

echo
echo "✓ 装好了。重启 DSH 桌面端后生效。"
echo "  录声纹：$PY $DSH/pet-voiceid.py --enroll"
echo "  体检：  $PY $DSH/pet-bridge/health.py --check"
