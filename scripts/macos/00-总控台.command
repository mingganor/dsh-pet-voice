#!/bin/bash
# 🐋 桌宠语音总控台 —— 双击运行
# 桌宠（DSH 桌面端）/ 耳朵（麦克风）/ 声纹 / 体检，全在这一个菜单里
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
BRIDGE="$HOME/.dsh/pet-bridge"
VP="$HOME/.dsh/pet-voiceprint.json"
VOICE_CFG="$HOME/.dsh/pet-voice.json"

if [ ! -x "$PY" ]; then
    echo "找不到 Python：$PY"
    echo "WorkBuddy 的 Python 环境可能换了，改本文件第 6 行的 PY 即可。"
    read -r -p "按回车关闭…" _
    exit 1
fi

# ---------- 状态 ----------
dsh_up() { pgrep -f "$APP" >/dev/null 2>&1; }
pet_up() { pgrep -f "dsh-pet/runtime/electron-helper" >/dev/null 2>&1; }
ear_up() { pgrep -f "pet-listen.py" >/dev/null 2>&1; }
api_up() { curl -s --max-time 2 http://127.0.0.1:3099/ping >/dev/null 2>&1; }
mark()   { if "$@"; then printf "✅"; else printf "❌"; fi; }

vp_text() {
    if [ -f "$VP" ]; then
        "$PY" -c "import json;d=json.load(open('$VP'));print(f\"已登记（阈值 {d.get('threshold')}）\")" 2>/dev/null || echo "已登记"
    else
        echo "未登记（谁说话她都接）"
    fi
}

mode_text() {
    "$PY" -c "import json;print(json.load(open('$HOME/.dsh/pet-listen.json')).get('mode','always'))" 2>/dev/null || echo unknown
}

show_status() {
    echo
    echo "──────────────────────────────────────"
    echo "  $(mark dsh_up)  DSH 桌面端（宿主）"
    echo "  $(mark pet_up)  桌宠小窗（她本鲸）"
    echo "  $(mark ear_up)  耳朵（麦克风监听）"
    echo "  $(mark api_up)  对话接口 :3099"
    echo "  声纹：$(vp_text)"
    echo "  监听模式：$(mode_text)"
    echo "──────────────────────────────────────"
    echo
}

wait_api() {
    for _ in $(seq 1 40); do
        sleep 2
        api_up && return 0
    done
    return 1
}

wait_ear_ready() {
    for _ in $(seq 1 60); do
        grep -q "就绪，开始监听" "$LOG" 2>/dev/null && return 0
        sleep 2
    done
    return 1
}

# ---------- 动作 ----------
start_ear() {
    if ear_up; then
        echo "→ 耳朵已在听"
        return
    fi
    echo "→ 开启耳朵…"
    nohup "$PY" "$HOME/.dsh/pet-listen.py" > "$LOG" 2>&1 &
    disown 2>/dev/null || true
    echo "  ⏳ 加载模型（首次约 1 分钟）…"
    if wait_ear_ready; then
        echo "  ✓ 她能听见你了"
    else
        echo "  ⚠ 还没就绪，看 $LOG 或做一次体检"
    fi
}

start_all() {
    if dsh_up; then
        echo "→ 桌面端已在运行"
    else
        echo "→ 启动桌面端…"
        open -a "$APP"
    fi
    echo "→ 等对话接口…"
    if wait_api; then
        echo "  ✓ 接口就绪"
    else
        echo "  ⚠ 接口没起来（按 8 体检）"
    fi
    start_ear
}

stop_ear() {
    pkill -f "pet-listen.py" 2>/dev/null
    sleep 1
    if ear_up; then echo "✗ 没关掉"; else echo "✓ 耳朵已关（她还看得见，只是听不见）"; fi
}

stop_all() {
    stop_ear
    echo "→ 退出桌面端…"
    pkill -f "$APP" 2>/dev/null
    pkill -f "dsh-pet/runtime" 2>/dev/null
    sleep 3
    if dsh_up; then echo "⚠ 可能没退干净，再按一次 3"; else echo "✓ 她彻底下班了"; fi
}

restart_dsh() {
    echo "→ 重启桌面端（桌宠窗口会关掉再开）…"
    if [ -f "$BRIDGE/health.py" ]; then
        "$PY" "$BRIDGE/health.py" --restart
    else
        pkill -f "$APP" 2>/dev/null
        sleep 5
        open -a "$APP"
        if wait_api; then echo "✓ 重启完成"; else echo "⚠ 接口未起"; fi
    fi
}

say_test() {
    if ! api_up; then
        echo "✗ 接口没起，先按 1，或按 8 体检"
        return
    fi
    echo "→ 发一句测试话…"
    curl -s --max-time 90 -X POST http://127.0.0.1:3099/say \
        -H "Content-Type: application/json" \
        -d '{"text":"喂，听得到吗？","pet":"main"}'
    echo
    if "$PY" -c "import json;print(json.load(open('$VOICE_CFG')).get('enabled',True))" 2>/dev/null | grep -qi false; then
        echo "（她没出声：$VOICE_CFG 里 enabled=false）"
    else
        echo "（她应该说话了；没声音就按 8 体检）"
    fi
}

toggle_mode() {
    cur=$(mode_text)
    if [ "$cur" = "always" ]; then new="wakeword"; else new="always"; fi
    "$PY" - <<EOF
import json
p = "$HOME/.dsh/pet-listen.json"
d = json.load(open(p))
d["mode"] = "$new"
json.dump(d, open(p, "w"), ensure_ascii=False, indent=2)
EOF
    echo "→ 监听模式：$cur ⇒ $new"
    if [ "$new" = "wakeword" ]; then
        echo "  现在要先喊唤醒词她才理你（wakewords 在 ~/.dsh/pet-listen.json 里配）"
    else
        echo "  现在你说什么她都接（安静环境用）"
    fi
    if ear_up; then
        echo "→ 重启耳朵生效…"
        stop_ear
        start_ear
    fi
}

health() { "$PY" "$BRIDGE/health.py" --check; }

enroll() {
    ( cd "$HOME/.dsh" && "$PY" pet-voiceid.py --enroll )
    echo
    read -r -p "按回车回到菜单…" _
}

vptest() {
    ( cd "$HOME/.dsh" && "$PY" pet-voiceid.py --test )
    echo
    read -r -p "按回车回到菜单…" _
}

# 非交互：./00-总控台.command --status
if [ "${1:-}" = "--status" ]; then
    show_status
    exit 0
fi

clear
echo "🐋 桌宠语音总控台"
show_status

while true; do
    echo "  [1] 全部开启（桌宠 + 耳朵）"
    echo "  [2] 只关耳朵（还看得见她）"
    echo "  [3] 全部关闭"
    echo "  [4] 重启桌面端（改插件/配置后用）"
    echo "  [5] 发一句测试话（验证她能说）"
    echo "  [6] 刷新状态"
    echo "  [7] 切换监听模式（always / wakeword）"
    echo "  [8] 体检（听/说哪一环断了）"
    echo "  [9] 录入声纹（只认你的声音）"
    echo " [10] 测试声纹（看相似度）"
    echo "  [0] 退出"
    echo
    read -r -p "选一个: " c
    echo
    case "$c" in
        1) start_all ;;
        2) stop_ear ;;
        3) stop_all ;;
        4) restart_dsh ;;
        5) say_test ;;
        6) : ;;
        7) toggle_mode ;;
        8) health ;;
        9) enroll ;;
        10) vptest ;;
        0) echo "拜拜 🌾"; exit 0 ;;
        *) echo "没这个选项" ;;
    esac
    show_status
done
