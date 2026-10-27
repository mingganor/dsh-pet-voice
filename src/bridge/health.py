#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""桌宠语音体检：一眼看出「听不见 / 说不出」是哪一环断了

用法：
  health.py --check     只看状态
  health.py --repair    壳丢了就自动补，然后提示重启
  health.py --restart   重启 DSH 桌面端并等接口起来（会关掉桌宠窗口）
"""

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import install as patcher  # noqa: E402

HOME = os.path.expanduser("~")
VOICE_LOG = os.path.join(HOME, ".dsh/pet-voice.log")
EAR_LOG = "/tmp/pet-listen.log"
VOICEPRINT = os.path.join(HOME, ".dsh/pet-voiceprint.json")


def ping(timeout=1.5):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:3099/ping", timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


def ear_alive():
    try:
        p = subprocess.run(["pgrep", "-f", "pet-listen.py"], capture_output=True, text=True)
        return bool(p.stdout.strip())
    except Exception:
        return False


def tail(path, n=6):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()[-n:]
        return "".join(lines).rstrip()
    except Exception:
        return ""


def run_check():
    print("🐋 桌宠语音体检\n")
    ok = True

    # 1) 插件补丁
    print("① 插件里的语音桥壳")
    rc = patcher.do_check(patcher.TARGET)
    if rc != 0:
        ok = False

    # 2) 3099
    print("\n② 对话接口 127.0.0.1:3099")
    info = ping()
    if info:
        print(f"✓ 通：{json.dumps(info, ensure_ascii=False)}")
    else:
        ok = False
        print("✗ 没人应答（她能听但说不出，或整只没起来）")
        print("  → 先重启桌面端：双击总控台选 4；还不行跑 health.py --repair")

    # 3) 耳朵
    print("\n③ 耳朵（麦克风监听）")
    if ear_alive():
        print("✓ 进程在")
    else:
        ok = False
        print("✗ 没在跑 → 双击 00-总控台.command 选 1")

    # 4) 声纹
    print("\n④ 声纹")
    if os.path.exists(VOICEPRINT):
        try:
            d = json.load(open(VOICEPRINT, encoding="utf-8"))
            print(f"✓ 已登记（阈值 {d.get('threshold')}，{d.get('created')}）")
        except Exception as e:
            print(f"⚠ 文件读不了：{e}")
    else:
        print("· 还没登记（谁说话她都接）。双击 03-声纹.command")

    # 5) 日志尾巴
    print("\n⑤ 接口日志（最近几行）")
    print(tail(VOICE_LOG) or "· 还没有日志（接口从没起过？）")
    last = tail(EAR_LOG, 3)
    if last:
        print("\n⑥ 耳朵日志（最近几行）")
        print(last)

    print("\n" + ("✓ 全部正常" if ok else "✗ 有环节不通，按上面提示处理"))
    return 0 if ok else 1


def run_repair():
    print("→ 检查补丁…")
    rc = patcher.do_check(patcher.TARGET)
    if rc == 0:
        print("补丁完整，不需要修。")
    else:
        print("\n→ 重新打补丁（会先备份）…")
        patcher.do_apply(patcher.TARGET)
    print("\n→ 补丁改过就需要重启桌面端才生效：")
    print("   health.py --restart   （或双击总控台选 4）")
    return 0


APP_LOCK = os.path.join(HOME, "Library/Application Support/@deepseek-ai/dsh-desktop/SingletonLock")


def run_restart():
    print("→ 关闭 DSH 桌面端…")
    subprocess.run(["pkill", "-f", "DeepSeek Harness"], capture_output=True)
    # 等旧实例真的退干净，否则 open 会把事件丢给正在退出的那个
    for _ in range(40):
        if not os.path.exists(APP_LOCK):
            break
        time.sleep(0.5)
    time.sleep(1)
    print("→ 重新打开…")
    for attempt in range(6):
        subprocess.run(["open", "-a", "DeepSeek Harness"], capture_output=True)
        for _ in range(15):
            time.sleep(1)
            if ping(1.0):
                print(f"✓ 接口就绪（第 {attempt+1} 次拉起）")
                return 0
        print(f"  （{15*(attempt+1)}s 还没起来，再拉一次）")
    print("✗ 90s 内接口没起来，看 ~/.dsh/pet-voice.log")
    return 1


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--check", action="store_true")
    g.add_argument("--repair", action="store_true")
    g.add_argument("--restart", action="store_true")
    args = ap.parse_args()
    if args.repair:
        return run_repair()
    if args.restart:
        return run_restart()
    return run_check()


if __name__ == "__main__":
    sys.exit(main())
