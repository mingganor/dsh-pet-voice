#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""桌宠声纹：只认你的声音

用法：
  pet-voiceid.py --enroll            录 5 句，登记你的声音
  pet-voiceid.py --test              说一句，看像不像你（打印得分）
  pet-voiceid.py --show              看当前登记信息
  pet-voiceid.py --reset             删掉登记
  pet-voiceid.py --threshold 0.5     改阈值（越低越宽松）

模型：~/.dsh/models/campplus.onnx（3D-Speaker CAM++，中文 16k）
"""

import argparse
import json
import os
import sys
import time
import wave

import numpy as np
import sounddevice as sd

HOME = os.path.expanduser("~")
VP_PATH = os.environ.get("PET_VOICEPRINT") or os.path.join(HOME, ".dsh/pet-voiceprint.json")
MODEL = os.environ.get("PET_VP_MODEL") or os.path.join(HOME, ".dsh/models/campplus.onnx")
SR = 16000

PROMPTS = [
    "你好呀，今天过得怎么样",
    "帮我把明天的日程念一遍",
    "我一会儿要出去买点东西",
    "现在几点了，外面下雨了吗",
    "好了，先这样吧，我去忙了",
]

REC_SEC = 3.0
MIN_RMS = 0.004


def embed(audio, ext):
    s = ext.create_stream()
    s.accept_waveform(SR, audio)
    s.input_finished()
    v = np.asarray(ext.compute(s), dtype="float32")
    n = np.linalg.norm(v)
    return v / n if n else v


def cosine(a, b):
    return float(a @ b / ((np.linalg.norm(a) * np.linalg.norm(b)) + 1e-9))


def load_extractor(path=MODEL):
    import sherpa_onnx
    if not os.path.exists(path):
        raise SystemExit(f"找不到声纹模型：{path}")
    cfg = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
        model=path, num_threads=2, debug=False, provider="cpu")
    return sherpa_onnx.SpeakerEmbeddingExtractor(cfg)


def read_wav(path):
    with wave.open(path, "rb") as w:
        sr = w.getframerate()
        ch = w.getnchannels()
        a = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype("float32") / 32768.0
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1)
    if sr != SR:
        x = np.linspace(0, 1, len(a), endpoint=False)
        y = np.linspace(0, 1, int(len(a) * SR / sr), endpoint=False)
        a = np.interp(y, x, a).astype("float32")
    return a


def rec(seconds, tag=""):
    print(f"  ● 录音 {seconds:.0f}s {tag}", end="", flush=True)
    a = sd.rec(int(seconds * SR), samplerate=SR, channels=1, dtype="float32")
    sd.wait()
    a = a.flatten()
    print(f"  音量 {float(np.sqrt(np.mean(a**2))):.4f}")
    return a


def save(vec, threshold, source):
    os.makedirs(os.path.dirname(VP_PATH), exist_ok=True)
    json.dump({
        "vector": [round(float(x), 6) for x in vec],
        "dim": int(vec.size),
        "threshold": round(float(threshold), 3),
        "source": source,
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
    }, open(VP_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def load_vp():
    try:
        d = json.load(open(VP_PATH, encoding="utf-8"))
        return np.asarray(d.get("vector") or [], dtype="float32"), d
    except Exception:
        return None, None


def enroll_from_files(ext, paths):
    embs = []
    for p in paths:
        a = read_wav(p)
        embs.append(embed(a, ext))
        print(f"  {p}: {len(a)/SR:.1f}s")
    return embs


def do_enroll(args):
    ext = load_extractor(args.model)
    if args.files:
        embs = enroll_from_files(ext, args.files)
    else:
        print("登记声纹：接下来 5 句，每句 3 秒，请用平时对她说活的音量正常读。\n")
        embs = []
        for i, p in enumerate(PROMPTS, 1):
            print(f"[{i}/{len(PROMPTS)}] 请读：{p}")
            input("      准备好按回车…")
            a = rec(REC_SEC)
            if float(np.sqrt(np.mean(a ** 2))) < MIN_RMS:
                print("      ⚠ 音量太小，这句不算，请重来")
                return 1
            embs.append(embed(a, ext))
            print()
    c = np.mean(np.stack(embs), axis=0)
    c /= (np.linalg.norm(c) + 1e-9)
    scores = [cosine(e, c) for e in embs]
    thr = float(np.clip(min(scores) - 0.15, 0.45, 0.70))
    save(c, thr, "files" if args.files else "mic")
    print(f"各次与中心相似度：{', '.join(f'{s:.2f}' for s in scores)}")
    print("  （同一人不同次一般 0.7~0.9，不同人一般 0.2~0.5；阈值取最低分再留 0.15 余量）")
    print(f"✓ 已登记：{VP_PATH}")
    print(f"  建议阈值 {thr:.2f}（已写入；她总是不理你就调低：--threshold 0.5）")


def do_test(args):
    vec, meta = load_vp()
    if vec is None:
        raise SystemExit("还没登记，先跑 --enroll")
    ext = load_extractor(args.model)
    if args.files:
        embs = [embed(read_wav(p), ext) for p in args.files]
    else:
        print("说一句话（3 秒）…")
        embs = [embed(rec(REC_SEC), ext)]
    thr = float(meta.get("threshold", 0.55))
    for e in embs:
        s = cosine(e, vec)
        print(f"相似度 {s:.3f}  阈值 {thr:.2f}  -> {'通过 ✓' if s >= thr else '不通过 ✗'}")


def do_show(_args):
    vec, meta = load_vp()
    if vec is None:
        print("还没登记")
        return
    print(json.dumps({k: v for k, v in meta.items() if k != "vector"},
                     ensure_ascii=False, indent=2))


def do_reset(_args):
    if os.path.exists(VP_PATH):
        os.remove(VP_PATH)
        print("✓ 已删除声纹登记（她暂时谁说话都接）")
    else:
        print("本来就没有登记")


def do_threshold(args):
    vec, meta = load_vp()
    if vec is None:
        raise SystemExit("还没登记，先跑 --enroll")
    meta["threshold"] = float(args.threshold)
    json.dump(meta, open(VP_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"✓ 阈值改为 {args.threshold:.2f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--enroll", action="store_true")
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--threshold", type=float)
    ap.add_argument("--files", nargs="*", help="用 wav 代替麦克风（调试用）")
    ap.add_argument("--model", default=MODEL)
    args = ap.parse_args()

    if args.reset:
        return do_reset(args)
    if args.threshold is not None:
        return do_threshold(args)
    if args.enroll:
        return do_enroll(args)
    if args.test:
        return do_test(args)
    if args.show:
        return do_show(args)
    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main() or 0)
