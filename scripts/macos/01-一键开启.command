#!/bin/bash
# 🐋 一键开启：桌面端 + 耳朵，不用进菜单
set -u

PY="${PET_PYTHON:-}"
if [ -z "$PY" ] && [ -f "$HOME/.dsh/pet-python" ]; then
    PY="$(cat "$HOME/.dsh/pet-python")"
fi
if [ -z "$PY" ] || [ ! -x "$PY" ]; then
    PY="python3"
fi
LOG="/tmp/pet-listen.log"
APP="DeepSeek Harness"

if [ ! -x "$PY" ]; then
    echo "找不到 Python：$PY"
    read -r -p "按回车关闭…" _
    exit 1
fi

dsh_up() { pgrep -f "$APP" >/dev/null 2>&1; }
ear_up() { pgrep -f "pet-listen.py" >/dev/null 2>&1; }
api_up() { curl -s --max-time 2 http://127.0.0.1:3099/ping >/dev/null 2>&1; }

if dsh_up; then
    echo "✅ 桌面端已在运行"
else
    echo "→ 启动桌面端…"
    open -a "$APP"
fi

echo "→ 等对话接口…"
ok=0
for _ in $(seq 1 40); do
    sleep 2
    if api_up; then ok=1; break; fi
done
if [ "$ok" = 1 ]; then
    echo "✅ 接口就绪（:3099）"
else
    echo "⚠ 接口没起来：先跑 02-体检.command"
fi

if ear_up; then
    echo "✅ 耳朵已在听"
else
    echo "→ 开启耳朵…"
    nohup "$PY" "$HOME/.dsh/pet-listen.py" > "$LOG" 2>&1 &
    disown 2>/dev/null || true
    for _ in $(seq 1 60); do
        sleep 2
        grep -q "就绪，开始监听" "$LOG" 2>/dev/null && break
    done
    if grep -q "就绪，开始监听" "$LOG" 2>/dev/null; then
        echo "✅ 耳朵就绪"
    else
        echo "⚠ 耳朵还没就绪，看 $LOG"
    fi
fi

echo
echo "她上线了。要关的话跑 00-总控台.command 选 3。"
read -r -p "按回车关闭这个窗口…" _
