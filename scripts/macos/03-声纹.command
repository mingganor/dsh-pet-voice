#!/bin/bash
# 🐋 声纹：只认你的声音
#   双击        = 录入（读 5 句）
#   --test      = 说一句，看相似度
#   --reset     = 删掉登记
set -u

PY="${PET_PYTHON:-}"
if [ -z "$PY" ] && [ -f "$HOME/.dsh/pet-python" ]; then
    PY="$(cat "$HOME/.dsh/pet-python")"
fi
if [ -z "$PY" ] || [ ! -x "$PY" ]; then
    PY="python3"
fi
cd "$HOME/.dsh" || exit 1

if [ ! -x "$PY" ]; then
    echo "找不到 Python：$PY"
    read -r -p "按回车关闭…" _
    exit 1
fi

case "${1:-}" in
    --test)  "$PY" pet-voiceid.py --test ;;
    --reset) "$PY" pet-voiceid.py --reset ;;
    *)       "$PY" pet-voiceid.py --enroll ;;
esac

echo
echo "──────────────────────────────────────"
echo "测试：\"$0\" --test"
echo "重录：\"$0\""
echo "删掉：\"$0\" --reset"
echo "──────────────────────────────────────"
read -r -p "按回车关闭这个窗口…" _
