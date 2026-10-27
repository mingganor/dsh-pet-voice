#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
桌宠的耳朵：麦克风 → VAD → 本地 Whisper → 声纹 → 对话接口

常驻监听麦克风 -> Silero VAD 判断是否人声 -> (可选)声纹校验 -> 本地 Whisper 转文字
  -> 同音词表纠正 -> POST 给桌宠对话接口（127.0.0.1:3099/say）-> 她开口回答

A 档（识别质量）：
  - 判停 1.3s（原来 1.0s，尾字常被吃掉）
  - 超过 maxSeconds 不再整段丢弃，照常识别（靠人声占比/长度过滤兜底）
  - 关闭 condition_on_previous_text，减少"复读机"式幻听
  - 支持 hotwords（默认关：always 模式下提示词可能被念出来，需要时再开）
  - 内置 + 可配置的同音词表（示例是「鲸鱼娘/小满/DeepSeek」，换成你的桌宠名即可）
B 档（只理主人）：
  - CAM++ 声纹校验，只有登记过的声音才会被送去回答
  - 没登记声纹时自动跳过，不会把她弄哑

用法:
  pet-listen.py                常驻监听
  pet-listen.py --test         测麦克风：静音 3 秒 + 说话 4 秒
  pet-listen.py --wav a.wav    只识别一个 16k wav（不麦克风、不发送）
  pet-listen.py --no-vp        本次不校验声纹（调试用）
"""

import sys
import os
import re
import json
import time
import urllib.request

import numpy as np
import sounddevice as sd

HOME = os.path.expanduser("~")
CFG_PATH = os.path.join(HOME, ".dsh/pet-listen.json")
VOICEPRINT_PATH = os.environ.get("PET_VOICEPRINT") or os.path.join(HOME, ".dsh/pet-voiceprint.json")
VP_MODEL = os.environ.get("PET_VP_MODEL") or os.path.join(HOME, ".dsh/models/campplus.onnx")
SPEAKING_FLAG = os.path.join(HOME, ".dsh/pet-speaking")   # pet-speak.py 播放时立牌
PORT = 3099

DEFAULT_CFG = {
    "mode": "always",              # always=听到就说 | wakeword=先喊唤醒词
    # 示例：按「鲸鱼娘」这只桌宠配的，换成你自己的名字/唤醒词
    "wakewords": ["鲸鱼", "小鲸", "鲸鱼娘", "小满", "你好"],
    "vadThreshold": 0.5,           # Silero VAD 灵敏度 0~1，越大越苛刻
    "minRms": 0.003,               # 低于此音量直接跳过 VAD（省 CPU）
    "silence": 1.3,                # 静音多久算说完（秒）
    "maxSeconds": 20,
    "minSeconds": 0.4,
    "model": "small",              # tiny/base/small/medium
    "hotwords": "",                # 逗号分隔；空 = 不启用（默认，避免提示词泄漏）
    "lexicon": [],                 # 额外的 [["错","对"], ...]
    "voiceprint": {"enabled": True, "threshold": 0.55, "minSeconds": 1.0},
    "pet": "",
    "device": None,
}

HALLUCINATIONS = [
    "请不吝点赞", "订阅", "转发", "打赏", "明镜与点点栏目",
    "谢谢观看", "感谢观看", "谢谢大家", "字幕", "字幕由", "字幕志愿者",
    "未完待续", "请点赞", "点个赞", "关注我", "以上",
]

# 内置同音词表：只放高置信的专名，宁少勿滥（错换对会改坏原意）
# 示例词表：左边是常见误识别，右边是正确写法。换成你桌宠的名字和常用词。
LEXICON_DEFAULT = [
    ("金余娘", "鲸鱼娘"), ("金鱼娘", "鲸鱼娘"), ("鲸鱼年", "鲸鱼娘"),
    ("鲸鱼凉", "鲸鱼娘"), ("鲸鱼鸟", "鲸鱼娘"), ("京余娘", "鲸鱼娘"),
    ("小蛮", "小满"), ("小曼", "小满"), ("小瞒", "小满"),
    ("Deepseak", "DeepSeek"), ("deepseak", "DeepSeek"),
    ("DeepSick", "DeepSeek"), ("迪普希克", "DeepSeek"),
    ("王易云", "网易云"), ("网意云", "网易云"),
    ("豆包儿", "豆包"),
]

SAMPLE_RATE = 16000
CHUNK = 1024
PRE_ROLL = 0.5          # 开始录制时回补的音频（秒），避免吃掉开头

KANA = re.compile(r"[\u3040-\u30ff]")     # 日文假名：中文识别里冒出来多半是幻听
LEAK = ["请用简体中文", "简体中文输出", "以下是普通话"]


def load_cfg():
    cfg = dict(DEFAULT_CFG)
    try:
        with open(CFG_PATH, "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    except Exception:
        pass
    vp = dict(DEFAULT_CFG["voiceprint"])
    vp.update(cfg.get("voiceprint") or {})
    cfg["voiceprint"] = vp
    return cfg


def send_to_pet(text, pet=""):
    payload = json.dumps({"text": text, "pet": pet}).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:{PORT}/say",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        raise RuntimeError(f"接口调用失败：{e}。跑 02-体检.command（或 health.py --check）查原因")


def bridge_info(timeout=2.0):
    """检查 3099 是否在；不在就早一点告诉她（免得用户以为她聋了）"""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/ping", timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def noisy(text):
    t = (text or "").strip()
    if len(t) < 2:
        return True
    for h in HALLUCINATIONS:
        if h in t:
            return True
    for h in LEAK:
        if h in t:
            return True
    if len(KANA.findall(t)) >= 2:      # 中文里不该出现假名，出现即幻听
        return True
    if re.fullmatch(r"(.)\1{4,}", t):
        return True
    return False


def strip_wakeword(text, words):
    for w in words:
        i = text.find(w)
        if i >= 0:
            rest = text[i + len(w):].strip(" ,，。.!！?？、~～")
            if rest:
                return rest
    return text


def fix_lexicon(text, extra):
    for wrong, right in list(LEXICON_DEFAULT) + list(extra or []):
        if wrong and right:
            text = text.replace(str(wrong), str(right))
    return text


def rms_of(a):
    return float(np.sqrt(np.mean(a ** 2))) if len(a) else 0.0


# ---------------- 声纹（B 档） ----------------

_VP = {"ext": None, "model": "", "enrolled": None}


def load_voiceprint():
    """返回 (向量, 文件里建议的阈值或 None)。"""
    try:
        with open(VOICEPRINT_PATH, "r", encoding="utf-8") as f:
            d = json.load(f)
        v = np.asarray(d.get("vector") or [], dtype="float32")
        if v.size:
            return v, d.get("threshold")
    except Exception:
        pass
    return None, None


def vp_extractor(model_path=VP_MODEL):
    """懒加载 CAM++；模型/依赖缺失时返回 None，调用方直接放行。"""
    if _VP["ext"] is not None and _VP["model"] == model_path:
        return _VP["ext"]
    try:
        import sherpa_onnx
        cfg = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=model_path, num_threads=2, debug=False, provider="cpu")
        _VP["ext"] = sherpa_onnx.SpeakerEmbeddingExtractor(cfg)
        _VP["model"] = model_path
        return _VP["ext"]
    except Exception as e:
        print(f"  声纹模型不可用（{e}），本次不校验", flush=True)
        return None


def vp_embed(audio, model_path=VP_MODEL):
    ext = vp_extractor(model_path)
    if ext is None:
        return None
    s = ext.create_stream()
    s.accept_waveform(SAMPLE_RATE, audio)
    s.input_finished()
    v = np.asarray(ext.compute(s), dtype="float32")
    return v


def cosine(a, b):
    na, nb = float(np.linalg.norm(a)), float(np.linalg.norm(b))
    if na == 0 or nb == 0:
        return 0.0
    return float(a @ b / (na * nb))


# ---------------- 麦克风自检 ----------------

def test_mic():
    print("① 请保持安静 3 秒（测环境底噪）…", flush=True)
    sd.sleep(100)
    quiet = sd.rec(int(3 * SAMPLE_RATE), samplerate=SAMPLE_RATE,
                   channels=1, dtype="float32")
    sd.wait()
    q = rms_of(quiet.flatten())
    print(f"   底噪 RMS = {q:.5f}")

    print("\n② 接着请说一句话（4 秒）…", flush=True)
    talk = sd.rec(int(4 * SAMPLE_RATE), samplerate=SAMPLE_RATE,
                  channels=1, dtype="float32")
    sd.wait()
    t = rms_of(talk.flatten())
    print(f"   说话 RMS = {t:.5f}")
    print(f"\n建议 minRms 设为底噪的 1.5 倍左右: {max(q*1.5, 0.002):.5f}")
    if t > q * 2:
        print("✓ 麦克风工作正常（说话时音量明显高于底噪）")
    else:
        print("⚠ 说话与底噪差异不大，检查麦克风权限或离远了")
    return q, t


def transcribe_wav(path):
    """不经过麦克风，直接识别一个 16k 单声道 wav（自检/调参用）。"""
    import wave
    with wave.open(path, "rb") as w:
        sr = w.getframerate()
        a = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype("float32") / 32768.0
    if sr != SAMPLE_RATE:
        print(f"⚠ 采样率 {sr}，请先转成 16000")
    cfg = load_cfg()
    from faster_whisper import WhisperModel
    from opencc import OpenCC
    m = WhisperModel(str(cfg.get("model", "small")), device="cpu", compute_type="int8")
    segs, _ = m.transcribe(a, language="zh", beam_size=5, vad_filter=True,
                           vad_parameters=dict(threshold=0.5),
                           condition_on_previous_text=False,
                           hotwords=(cfg.get("hotwords") or None))
    if cfg["voiceprint"].get("enabled", True) and os.path.exists(VOICEPRINT_PATH):
        vec, saved_thr = load_voiceprint()
        emb = vp_embed(a) if vec is not None else None
        if emb is not None:
            sc = cosine(emb, vec)
            thr = float(saved_thr or cfg["voiceprint"].get("threshold", 0.55))
            print(f"[声纹] {sc:.3f} vs 阈值 {thr:.2f} -> {'通过' if sc >= thr else '会被忽略'}",
                  flush=True)
    text = OpenCC("t2s").convert("".join(s.text for s in segs).strip())
    print(fix_lexicon(text, cfg.get("lexicon")) or "(空)")


# ---------------- 主循环 ----------------

def main():
    cfg = load_cfg()
    if "--test" in sys.argv:
        test_mic()
        return 0
    if "--wav" in sys.argv:
        i = sys.argv.index("--wav")
        transcribe_wav(sys.argv[i + 1])
        return 0

    info = bridge_info()
    if info:
        pets = "、".join(str(p) for p in (info.get("pets") or []))
        print(f"✓ 对话接口就绪（宠物 {pets}）", flush=True)
    else:
        print("⚠ 对话接口 :3099 没起：她能听见，但回答不出去。", flush=True)
        print("  双击 02-体检.command 看是哪一环（多半是桌面端没开，或插件升级覆盖了桥）。",
              flush=True)

    print("加载模型…（首次较慢，之后几秒）", flush=True)
    from faster_whisper import WhisperModel
    from faster_whisper.vad import get_speech_timestamps, VadOptions
    from opencc import OpenCC
    model = WhisperModel(str(cfg.get("model", "small")),
                         device="cpu", compute_type="int8")
    cc = OpenCC("t2s")

    vad_threshold = float(cfg["vadThreshold"])
    min_rms = float(cfg["minRms"])
    silence_need = float(cfg["silence"])
    max_sec = float(cfg["maxSeconds"])
    min_sec = float(cfg["minSeconds"])
    mode = str(cfg.get("mode", "always"))
    wakewords = [str(w) for w in cfg.get("wakewords", [])]
    hotwords = str(cfg.get("hotwords") or "").strip()
    lexicon = cfg.get("lexicon") or []
    pet = str(cfg.get("pet") or "")

    # 声纹
    vp_cfg = cfg["voiceprint"]
    vp_on = bool(vp_cfg.get("enabled", True)) and ("--no-vp" not in sys.argv)
    vp_thr = float(vp_cfg.get("threshold", 0.55))
    vp_min = float(vp_cfg.get("minSeconds", 1.0))
    vp_vec, vp_saved_thr = load_voiceprint() if vp_on else (None, None)
    if vp_saved_thr:
        vp_thr = float(vp_saved_thr)
    if vp_on and vp_vec is None:
        print("⚠ 还没录声纹：谁说话她都会接（跑 pet-voiceid.py --enroll 录一次）", flush=True)
    elif vp_on:
        print(f"✓ 声纹已加载（阈值 {vp_thr:.2f}），只认主人", flush=True)

    print(f"✓ 就绪，开始监听（模式={mode}）", flush=True)
    print(f"  判停 {silence_need:.1f}s / 上限 {max_sec:.0f}s / 热词 {'开' if hotwords else '关'}\n", flush=True)

    ring = np.zeros(SAMPLE_RATE * 8, dtype="float32")
    ring_pos = [0]
    rec_buf = []
    state = {"recording": False, "last_voice": 0.0, "started": 0.0}

    def callback(indata, frames_n, time_info, status):
        data = indata[:, 0].astype("float32")
        n = len(data)
        p = ring_pos[0]
        if p + n <= len(ring):
            ring[p:p + n] = data
        else:
            ring[p:] = data[:len(ring) - p]
            ring[:n - (len(ring) - p)] = data[len(ring) - p:]
        ring_pos[0] = (p + n) % len(ring)
        if state["recording"]:
            rec_buf.append(data.copy())

    def recent(seconds):
        n = int(SAMPLE_RATE * seconds)
        p = ring_pos[0]
        if p >= n:
            return ring[p - n:p].copy()
        return np.concatenate([ring[len(ring) - (n - p):], ring[:p]]).copy()

    def she_speaking():
        try:
            st = os.stat(SPEAKING_FLAG)
            return (time.time() - st.st_mtime) < 30
        except OSError:
            return False

    def is_speaking():
        if she_speaking():
            return False
        seg = recent(1.2)
        if rms_of(seg) < min_rms:
            return False
        try:
            ts = get_speech_timestamps(
                seg,
                VadOptions(threshold=vad_threshold, min_speech_duration_ms=120),
                sampling_rate=SAMPLE_RATE,
            )
            return len(ts) > 0
        except Exception:
            return rms_of(seg) >= min_rms * 3

    def speech_ratio(chunk):
        try:
            ts = get_speech_timestamps(
                chunk, VadOptions(threshold=vad_threshold), sampling_rate=SAMPLE_RATE)
            if not ts:
                return 0.0
            total = sum(t["end"] - t["start"] for t in ts)
            return total / max(len(chunk), 1)
        except Exception:
            return 1.0

    def transcribe(chunk):
        segs, _ = model.transcribe(
            chunk, language="zh", beam_size=5, vad_filter=True,
            vad_parameters=dict(threshold=0.5),
            condition_on_previous_text=False,     # A: 防复读/幻听
            hotwords=hotwords or None,            # A: 可选热词
        )
        segs = list(segs)
        if not segs:
            return "", 1.0
        text = "".join(s.text for s in segs).strip()
        nsp = sum(getattr(s, "no_speech_prob", 0.0) for s in segs) / len(segs)
        return text, nsp

    def handle(chunk):
        try:
            if she_speaking():
                print("  忽略: 她自己在说话（避免自激）", flush=True)
                return
            dur = len(chunk) / SAMPLE_RATE
            if dur < min_sec:
                return

            ratio = speech_ratio(chunk)
            if ratio < 0.25:
                print(f"  忽略({dur:.1f}s): 人声占比仅 {ratio:.0%}", flush=True)
                return

            # B: 声纹校验（短音频 embedding 不稳，跳过）
            if vp_on and vp_vec is not None and dur >= vp_min:
                emb = vp_embed(chunk)
                if emb is not None:
                    score = cosine(emb, vp_vec)
                    if score < vp_thr:
                        print(f"  忽略({dur:.1f}s): 声纹不符 {score:.2f} < {vp_thr:.2f}", flush=True)
                        return
                    print(f"  声纹 {score:.2f} ✓", flush=True)

            t0 = time.time()
            raw, nsp = transcribe(chunk)
            text = cc.convert(raw).strip()
            text = fix_lexicon(text, lexicon)
            print(f"  听到({dur:.1f}s / 用时{time.time()-t0:.1f}s, 人声{ratio:.0%}): {text}",
                  flush=True)

            if nsp > 0.5:
                print(f"    -> 不像人话(no_speech={nsp:.2f})，忽略", flush=True)
                return
            if noisy(text):
                print("    -> 判为噪声，忽略", flush=True)
                return
            if len(text) > 80:
                print("    -> 太长，多半是背景音，忽略", flush=True)
                return
            if mode == "wakeword":
                if not any(w in text for w in wakewords):
                    print("    -> 无唤醒词，忽略", flush=True)
                    return
                text = strip_wakeword(text, wakewords)

            print(f"    -> 发送给桌宠: {text}", flush=True)
            res = send_to_pet(text, pet)
            print(f"    -> {res}", flush=True)
        except Exception as e:
            print(f"    !! 处理失败: {e}", flush=True)

    def finish():
        state["recording"] = False
        if not rec_buf:
            return
        chunk = np.concatenate(rec_buf).flatten()
        rec_buf.clear()
        handle(chunk)

    try:
        vp_mtime = os.path.getmtime(VOICEPRINT_PATH)
    except OSError:
        vp_mtime = 0.0

    stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1,
                            dtype="float32", blocksize=CHUNK,
                            callback=callback,
                            device=cfg.get("device") or None)
    stream.start()

    try:
        while True:
            time.sleep(0.25)
            now = time.time()
            # 声纹文件变了就热加载：录完不用重启耳朵
            if vp_on:
                try:
                    mt = os.path.getmtime(VOICEPRINT_PATH)
                except OSError:
                    mt = 0.0
                if mt != vp_mtime:
                    vp_mtime = mt
                    vp_vec, vp_saved_thr = load_voiceprint() if mt else (None, None)
                    if vp_saved_thr:
                        vp_thr = float(vp_saved_thr)
                    if vp_vec is not None:
                        print(f"✓ 声纹已更新（阈值 {vp_thr:.2f}）", flush=True)
                    else:
                        print("⚠ 声纹文件不在了：谁说话她都会接", flush=True)
            speaking = is_speaking()
            if speaking:
                if not state["recording"]:
                    state["recording"] = True
                    state["started"] = now
                    rec_buf.clear()
                    rec_buf.append(recent(PRE_ROLL))
                elif (now - state["started"]) >= max_sec:
                    # A: 到点不再丢弃，先识别这一段（靠人声占比/长度兜底）
                    print(f"  到 {max_sec:.0f}s 上限，先收这一段", flush=True)
                    finish()
                state["last_voice"] = now
            elif state["recording"]:
                if (now - state["last_voice"]) >= silence_need:
                    finish()
                elif (now - state["started"]) >= max_sec:
                    finish()
    except KeyboardInterrupt:
        print("\n停止监听", flush=True)
    finally:
        stream.stop()
        stream.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
