#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dsh-pet 语音桥补丁管理器

把「注入逻辑」从 node_modules 里搬出来：
  - 真正的实现：~/.dsh/pet-bridge/voice-server.mjs（随你改，插件升级不受影响）
  - 插件里只留 10 行壳：转发到上面那个文件

用法：
  install.py --check       体检：壳在不在、语法对不对
  install.py --apply       打补丁（幂等，可反复跑；升级覆盖后重跑这个）
  install.py --uninstall   还原成没打补丁的样子（优先用备份）
  install.py --file X.js   对指定文件操作（测试用）

注意：改完必须重启 DSH 桌面端才生效。
"""

import argparse
import glob
import os
import re
import shutil
import subprocess
import sys
import time

HOME = os.path.expanduser("~")
TARGET = os.path.join(HOME, ".dsh/profiles/desktop/node_modules/dsh-pet/lib/index.js")
ENTRY = "file://" + os.path.join(HOME, ".dsh/pet-bridge/voice-server.mjs")

NEW_START = "// ===== dsh-pet 语音桥（managed by ~/.dsh/pet-bridge/install.py，勿手改）====="
NEW_END = "// ===== 语音桥外壳结束 ====="
# 旧版本注入（本仓库 v1 用过的标记）；保留是为了升级时能清干净
LEGACY = []

SPEAK_STUB = f"""// ===== dsh-pet 语音桥（managed by ~/.dsh/pet-bridge/install.py，勿手改）=====
// 实现都在 ~/.dsh/pet-bridge/voice-server.mjs；这里只是转发壳。
// 插件升级覆盖了这段就重跑：python3 ~/.dsh/pet-bridge/install.py --apply
const __PET_VOICE_ENTRY = "{ENTRY}";
let __petVoiceMod = null;
function __petVoice() {{
	if (!__petVoiceMod) __petVoiceMod = import(__PET_VOICE_ENTRY);
	return __petVoiceMod;
}}
function speakText(__raw) {{
	__petVoice().then((m) => m.speak(__raw)).catch(() => {{ }});
}}
// ===== 语音桥外壳结束 =====
"""

SERVER_STUB = f"""	// ===== dsh-pet 语音桥（managed by ~/.dsh/pet-bridge/install.py，勿手改）=====
	__petVoice().then((m) => m.startVoiceServer({{
		chat: (petId, text) => chatWithPet(petId, text),
		pets: () => effectivePetList()
	}})).catch((e) => {{
		const msg = "[dsh-pet-voice] 语音桥加载失败：" + (e && e.message || e);
		try {{ ctx.logger?.warn?.(msg); }} catch (e2) {{ }}
		try {{ console.error(msg); }} catch (e3) {{ }}
	}});
	// ===== 语音桥外壳结束 =====
"""


class PatchError(Exception):
    pass


def read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def write_atomic(path, text):
    tmp = path + ".tmp-petvoice"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def strip_blocks(src):
    removed = []
    for start_m, end_m in [(NEW_START, NEW_END)] + LEGACY:
        while True:
            i = src.find(start_m)
            if i < 0:
                break
            j = src.find(end_m, i)
            if j < 0:
                raise PatchError(f"找到开始标记但找不到结束标记：{start_m}")
            ls = src.rfind("\n", 0, i) + 1
            # 块前面若有空行，连它一起删——否则反复 apply 会越积越多空行
            if ls >= 1 and src[ls - 1] == "\n":
                prev_ls = src.rfind("\n", 0, ls - 1) + 1
                if src[prev_ls:ls].strip() == "":
                    ls = prev_ls
            le = src.find("\n", j)
            le = len(src) if le < 0 else le + 1
            src = src[:ls] + src[le:]
            removed.append(start_m)
    return src, removed


def insert_before(src, anchor, text, where="first"):
    idx = src.find(anchor) if where == "first" else src.rfind(anchor)
    if idx < 0:
        raise PatchError(f"找不到锚点：{anchor!r}")
    return src[:idx] + text + src[idx:]


def ensure_call(src, anchor, snippet, window=160, where="after"):
    """anchor 后（或前）插入一行；附近已有 speakText( 就跳过。"""
    idx = src.find(anchor)
    if idx < 0:
        raise PatchError(f"找不到锚点：{anchor!r}")
    if where == "after":
        seg = src[idx:idx + len(anchor) + window]
        if "speakText(" in seg:
            return src, False
        at = idx + len(anchor)
        return src[:at] + snippet + src[at:], True
    seg = src[max(0, idx - window):idx + len(anchor)]
    if "speakText(" in seg:
        return src, False
    return src[:idx] + snippet + src[idx:], True


def patch_text(src):
    src, removed = strip_blocks(src)

    # 1) 出声壳 + 模块加载器（模块作用域，插在 whisper 区块之前）
    anchor = "//#region src/host/whisper.ts"
    if src.find(anchor) < 0:
        raise PatchError("找不到 //#region src/host/whisper.ts，插件版本可能变了")
    src = insert_before(src, anchor, SPEAK_STUB + "\n", "first")

    # 2) 两处 speakText 调用：
    #    - 对话：chatWithPet 里生成成功后说一次（不碰 generateWith 的分支结构，跨版本更稳）
    #    - 碎碎念：generateWhisper 里说一次
    #    先清掉历史注入留下的分支内调用，避免重复出声
    for legacy_line in ("\t\tspeakText(text);\n", "\tspeakText(picked.text || text);\n"):
        src = src.replace(legacy_line, "")
    added = []
    src, a = ensure_call(src, "\t\tif (!generated.ok) return generated;\n",
                         "\t\tspeakText(generated.text);\n")
    added.append(a)
    src, b = ensure_call(src, "\treturn meme ? {\n", "\tspeakText(text);\n", where="before")
    added.append(b)

    # 3) 3099 服务壳（apply 内，插在 startHelper() 之前）
    at = src.rfind("\n\tstartHelper();")
    if at < 0:
        raise PatchError("找不到 apply 末尾的 startHelper();")
    at += 1
    src = src[:at] + SERVER_STUB + src[at:]

    return src, removed, added


def node_check(path):
    if not shutil.which("node"):
        return True, "找不到 node，跳过语法检查"
    p = subprocess.run(["node", "--check", path], capture_output=True, text=True)
    if p.returncode == 0:
        return True, "node --check 通过"
    return False, (p.stderr or p.stdout).strip()[:800]


def do_check(path):
    issues = []
    if not os.path.exists(path):
        print(f"✗ 插件入口不存在：{path}")
        return 1
    src = read(path)
    n_start, n_end = src.count(NEW_START), src.count(NEW_END)
    print(f"· 插件入口：{path}")
    print(f"· 语音桥壳：开始 {n_start} 处 / 结束 {n_end} 处（应为 2/2：出声壳 + 接口壳）")
    if n_start != 2 or n_end != 2:
        issues.append("语音桥壳缺失或被重复插入")
    calls = src.count("speakText(")
    print(f"· speakText 调用点：{calls} 处（应为 3：定义 1 + 对话 1 + 碎碎念 1）")
    if calls < 3:
        issues.append("speakText 调用点不全（插件升级可能覆盖了）")
    ok, msg = node_check(path)
    print(f"· 语法：{msg}")
    if not ok:
        issues.append("语法检查不通过")
    if issues:
        print("\n✗ 需要修复：")
        for x in issues:
            print("   - " + x)
        print("   跑：python3 ~/.dsh/pet-bridge/install.py --apply")
        return 1
    print("\n✓ 壳完整")
    return 0


def remember_python():
    """记下装了依赖的 Python，供宿主侧的 voice-server.mjs 调用 TTS"""
    try:
        os.makedirs(os.path.join(HOME, ".dsh"), exist_ok=True)
        with open(os.path.join(HOME, ".dsh", "pet-python"), "w", encoding="utf-8") as f:
            f.write(sys.executable + "\n")
    except Exception as e:
        print(f"（记 Python 路径失败，不影响补丁：{e}）")


def do_apply(path):
    remember_python()
    src = read(path)
    backup = f"{path}.bak-petvoice-{time.strftime('%Y%m%d-%H%M%S')}"
    shutil.copy2(path, backup)
    try:
        out, removed, added = patch_text(src)
    except PatchError as e:
        print(f"✗ 没打补丁：{e}")
        print(f"  备份：{backup}")
        return 1
    if out == src:
        print("✓ 已经是最新，无需改动")
        os.remove(backup)
        return 0
    write_atomic(path, out)
    ok, msg = node_check(path)
    if not ok:
        shutil.copy2(backup, path)
        print("✗ 打完后语法不过，已回滚")
        print(msg)
        return 1
    print(f"✓ 补丁已应用：{path}")
    if removed:
        print("  清掉了旧注入：" + "、".join(sorted(set(removed))))
    if any(added):
        print("  补了 speakText 调用点：" + "、".join(str(i + 1) for i, x in enumerate(added) if x))
    print(f"  备份：{backup}")
    print("  → 重启 DSH 桌面端（双击总控台选 4）后生效")
    return 0


def do_uninstall(path):
    backups = sorted(glob.glob(path + ".bak-petvoice-*"))
    if backups:
        shutil.copy2(backups[-1], path)
        print(f"✓ 已用备份还原：{backups[-1]}")
        print("  → 重启 DSH 桌面端后生效")
        return 0
    src, removed = strip_blocks(read(path))
    if not removed:
        print("· 没有找到注入，无需还原")
        return 0
    src = insert_before(src, "//#region src/host/whisper.ts",
                        "function speakText() { }\n\n", "first")
    write_atomic(path, src)
    ok, msg = node_check(path)
    if not ok:
        print("✗ 还原后语法不过：" + msg)
        return 1
    print("✓ 已移除注入（保留空 speakText，调用点不会报错）")
    return 0


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--check", action="store_true")
    g.add_argument("--apply", action="store_true")
    g.add_argument("--uninstall", action="store_true")
    ap.add_argument("--file", default=TARGET)
    args = ap.parse_args()

    if args.apply:
        return do_apply(args.file)
    if args.uninstall:
        return do_uninstall(args.file)
    return do_check(args.file)


if __name__ == "__main__":
    sys.exit(main())
