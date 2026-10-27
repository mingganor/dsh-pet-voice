#!/bin/bash
# 🐋 体检：看「听 / 说」哪一环断了
set -u

PY="${PET_PYTHON:-}"
if [ -z "$PY" ] && [ -f "$HOME/.dsh/pet-python" ]; then
    PY="$(cat "$HOME/.dsh/pet-python")"
fi
if [ -z "$PY" ] || [ ! -x "$PY" ]; then
    PY="python3"
fi

if [ ! -x "$PY" ]; then
    echo "找不到 Python：$PY"
    read -r -p "按回车关闭…" _
    exit 1
fi
if [ ! -f "$HOME/.dsh/pet-bridge/health.py" ]; then
    echo "找不到体检脚本：$HOME/.dsh/pet-bridge/health.py"
    read -r -p "按回车关闭…" _
    exit 1
fi

"$PY" "$HOME/.dsh/pet-bridge/health.py" --check

echo
echo "──────────────────────────────────────"
echo "补壳：   \"$PY\" \"$HOME/.dsh/pet-bridge/install.py\" --apply"
echo "重启：   \"$PY\" \"$HOME/.dsh/pet-bridge/health.py\" --restart"
echo "接口日志：$HOME/.dsh/pet-voice.log"
echo "──────────────────────────────────────"
read -r -p "按回车关闭这个窗口…" _
