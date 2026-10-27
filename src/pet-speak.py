#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
桌宠语音引擎

用法: pet-speak.py "要念的文本"
特点: 按台词情绪自动切换声线/语调；结果缓存；断网自动回退系统 say
"""

import sys
import os
import re
import json
import time
import asyncio
import shutil
import subprocess

HOME = os.path.expanduser("~")
CFG_PATH = os.path.join(HOME, ".dsh/pet-voice.json")
CACHE_DIR = os.path.join(HOME, ".dsh/pet-voice-cache")
CACHE_LIMIT = 400

DEFAULT_CFG = {
    "enabled": True,
    "engine": "edge",
    "voice": "auto",
    "rate": "auto",
    "pitch": "auto",
    "volume": "+0%",
    "cache": True,
    "maxChars": 70,
    "fallbackVoice": "Tingting",
    "fallbackRate": 190,
}

# 情绪 -> (声线, 语速, 音调)
# 按台词里的关键词切声线：angry / sad / cute / happy / calm
MOODS = {
    "angry":  ("zh-CN-liaoning-XiaobeiNeural", "+6%",  "+15Hz"),   # 东北话, 带点娇嗔
    "sad":    ("zh-CN-XiaoxiaoNeural",          "-10%", "+8Hz"),    # 温柔低落
    "happy":  ("zh-CN-XiaoyiNeural",            "+15%", "+45Hz"),   # 活泼雀跃
    "cute":   ("zh-CN-XiaoyiNeural",            "+8%",  "+50Hz"),   # 撒娇 高音调
    "calm":   ("zh-CN-XiaoxiaoNeural",          "+5%",  "+28Hz"),   # 日常温柔
}
MOOD_ORDER = ["angry", "sad", "cute", "happy"]

MOOD_KEYWORDS = {
    "angry": ["哼", "笨蛋", "讨厌", "生气", "烦死", "别闹", "才不是", "不理你", "坏蛋"],
    "sad":   ["呜", "难过", "好累", "累了", "失败", "出错", "抱歉", "对不起",
              "不开心", "好疼", "好困", "好饿", "寂寞", "想你", "委屈", "呜呜"],
    # 撒娇优先于开心: "诶嘿嘿" 同时含 "嘿嘿", 但那是在撒娇不是在雀跃
    "cute":  ["诶嘿嘿", "摸头", "人家", "陪我", "喜欢", "抱抱", "～", "~"],
    "happy": ["好耶", "太棒", "成功", "完成", "搞定", "嘿嘿", "嘻嘻", "开心",
              "厉害", "加油", "出发", "完美", "通过", "耶"],
}


def load_cfg():
    cfg = dict(DEFAULT_CFG)
    try:
        with open(CFG_PATH, "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    except Exception:
        pass
    return cfg


# 允许出现在朗读文本里的字符白名单（其余一律剔除，避免念出符号名）
KEEP = re.compile(
    r"[^0-9A-Za-z\u4e00-\u9fff\u3040-\u30ff"
    r"\u3002\uff1f\uff01\uff0c\u3001\uff1b\uff1a\u201c\u201d\u2018\u2019"
    r"\uff08\uff09\u300a\u300b\u2014\u2026\uff5e\~,.\?!;:'\"()\- ]"
)


def clean(text):
    """把台词洗成适合朗读的纯文本"""
    t = str(text or "")
    # 去掉代码块 / 行内代码 / 图片语法 / 链接
    t = re.sub(r"```[\s\S]*?```", " ", t)
    t = re.sub(r"`[^`]*`", " ", t)
    t = re.sub(r"!?\[[^\]]*\]\([^)]*\)", " ", t)
    t = re.sub(r"https?://\S+", " ", t)
    t = re.sub(r"<[^>]{1,40}>", " ", t)
    # 去掉 markdown 标记符号
    t = re.sub(r"[*_#>|]", " ", t)
    # 只保留白名单字符（顺带干掉 emoji）
    t = KEEP.sub("", t)
    # 压缩空白
    t = re.sub(r"\s+", " ", t).strip()
    return t


def truncate(t, max_chars):
    """按句末标点断句，别念太长"""
    if len(t) <= max_chars:
        return t
    cut = t[:max_chars]
    for p in ["。", "！", "？", "，", "、", "…", " "]:
        idx = cut.rfind(p)
        if idx > max_chars * 0.5:
            return cut[:idx + 1].strip()
    return cut.strip()


def detect_mood(t):
    """按关键词判断情绪，优先级 angry > sad > happy > cute"""
    for mood in MOOD_ORDER:
        for kw in MOOD_KEYWORDS[mood]:
            if kw in t:
                return mood
    # 没有关键词时，用感叹号当"兴奋"信号
    if "！" in t or "!" in t:
        return "happy"
    return "calm"


def pick_params(t, cfg):
    mood = detect_mood(t)
    v, r, p = MOODS[mood]
    voice = cfg.get("voice", "auto")
    rate = cfg.get("rate", "auto")
    pitch = cfg.get("pitch", "auto")
    return (
        v if voice in ("auto", "", None) else voice,
        r if rate in ("auto", "", None) else rate,
        p if pitch in ("auto", "", None) else pitch,
        mood,
    )


def cache_path(text, voice, rate, pitch):
    key = "|".join([text, voice, rate, pitch])
    h = hashlib_key(key)
    return os.path.join(CACHE_DIR, h + ".mp3")


def hashlib_key(s):
    import hashlib
    return hashlib.md5(s.encode("utf-8")).hexdigest()[:20]


def trim_cache():
    """缓存超量就删最旧的"""
    try:
        files = [os.path.join(CACHE_DIR, f) for f in os.listdir(CACHE_DIR)
                 if f.endswith(".mp3")]
        if len(files) <= CACHE_LIMIT:
            return
        files.sort(key=os.path.getmtime)
        for f in files[:len(files) - CACHE_LIMIT]:
            os.remove(f)
    except Exception:
        pass


async def synth_edge(text, voice, rate, pitch, volume, out):
    import edge_tts
    comm = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch, volume=volume)
    tmp = out + ".part"
    with open(tmp, "wb") as f:
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
    os.replace(tmp, out)


SPEAKING_FLAG = os.path.join(HOME, ".dsh/pet-speaking")


def _player_cmd(path):
    """按平台挑播放器；返回 None 表示一个都没有。

    macOS: afplay（系统自带）
    Linux: paplay / aplay / ffplay（有哪个用哪个）
    Windows: ffplay（需安装 ffmpeg；没有就不出声，不静默假装成功）
    """
    if sys.platform == "darwin":
        return ["afplay", path] if shutil.which("afplay") else None
    if sys.platform.startswith("win"):
        if shutil.which("ffplay"):
            return ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", path]
        return None
    for cmd in (["paplay", path], ["aplay", "-q", path],
                ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", path]):
        if shutil.which(cmd[0]):
            return cmd
    return None


def play(path):
    """播放期间立个牌子，让耳朵别把自己说的话当成人话收进去"""
    try:
        with open(SPEAKING_FLAG, "w") as f:
            f.write(str(os.getpid()))
    except Exception:
        pass
    try:
        cmd = _player_cmd(path)
        if cmd is None:
            print("找不到可用播放器（macOS: afplay；Linux: paplay/aplay/ffplay；"
                  "Windows: 装 ffmpeg 提供 ffplay）", file=sys.stderr)
        else:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    finally:
        try:
            os.remove(SPEAKING_FLAG)
        except Exception:
            pass


def say_fallback(text, cfg):
    """edge-tts 不可用时的兜底：macOS 用 say，Linux 用 espeak-ng，其它平台放弃。"""
    if sys.platform == "darwin":
        subprocess.run(["say", "-v", str(cfg.get("fallbackVoice", "Tingting")),
                        "-r", str(cfg.get("fallbackRate", 190)), text],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    elif sys.platform.startswith("linux") and shutil.which("espeak-ng"):
        subprocess.run(["espeak-ng", "-v", "cmn", text],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main():
    if len(sys.argv) < 2:
        return 0
    cfg = load_cfg()
    if cfg.get("enabled") is False:
        return 0

    raw = " ".join(sys.argv[1:])
    text = truncate(clean(raw), int(cfg.get("maxChars", 70)))
    if not text:
        return 0

    voice, rate, pitch, mood = pick_params(text, cfg)
    volume = str(cfg.get("volume", "+0%"))

    # 缓存命中 -> 直接播
    use_cache = cfg.get("cache", True)
    path = None
    if use_cache:
        os.makedirs(CACHE_DIR, exist_ok=True)
        path = cache_path(text, voice, rate, pitch)
        if os.path.exists(path) and os.path.getsize(path) > 0:
            play(path)
            return 0

    if str(cfg.get("engine", "edge")).lower() == "say":
        say_fallback(text, cfg)
        return 0

    try:
        if path is None:
            os.makedirs(CACHE_DIR, exist_ok=True)
            path = cache_path(text, voice, rate, pitch)
        asyncio.run(synth_edge(text, voice, rate, pitch, volume, path))
        play(path)
        trim_cache()
    except Exception:
        # 断网 / 合成失败 -> 回退系统语音
        try:
            if path and os.path.exists(path):
                os.remove(path)
        except Exception:
            pass
        say_fallback(text, cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
