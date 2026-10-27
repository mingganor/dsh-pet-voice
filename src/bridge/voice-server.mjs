// 桌宠语音桥（宿主侧逻辑，全部在这里；dsh-pet 里只留一个 10 行的壳）
//
// 职责：
//   1. speak(text)          —— 把回复交给 ~/.dsh/pet-speak.py 出声（串行、限流、超时）
//   2. startVoiceServer()   —— 127.0.0.1:3099 的 /say、/ping、/health
//
// 设计要点：
//   - 端口占用不再静默：EADDRINUSE 会重试并在 ~/.dsh/pet-voice.log 里留痕
//   - 所有请求/异常都落日志，出问题能查
//   - 插件升级覆盖了壳？跑 ~/.dsh/pet-bridge/install.py --apply 重新打补丁

import { spawn } from "node:child_process";
import { appendFileSync, existsSync, readFileSync } from "node:fs";
import http from "node:http";
import os from "node:os";

const HOME = process.env.HOME || os.homedir();
const PORT = Number(process.env.PET_VOICE_PORT || 3099);
const LOG = HOME + "/.dsh/pet-voice.log";
const SPEAK_PY = HOME + "/.dsh/pet-speak.py";
const VOICE_CFG = HOME + "/.dsh/pet-voice.json";
/** 找装了依赖的 Python：环境变量 > ~/.dsh/pet-python（安装器写入）> python3 */
function resolvePython() {
  if (process.env.PET_PYTHON) return process.env.PET_PYTHON;
  try {
    const p = readFileSync(HOME + "/.dsh/pet-python", "utf8").trim();
    if (p && existsSync(p)) return p;
  } catch { }
  return "python3";
}
const SPEAK_BIN = resolvePython();

export function log(msg) {
  const line = `[${new Date().toISOString()}] ${msg}\n`;
  try { appendFileSync(LOG, line); } catch { /* 日志写不了也不能让主流程挂 */ }
  try { console.log("[pet-voice] " + msg); } catch { }
}

/** 把台词洗成适合朗读的纯文本（保留中英文数字和基本标点） */
function clean(raw) {
  let t = String(raw || "");
  t = t.replace(/```[\s\S]*?```/g, " ").replace(/`[^`]*`/g, " ")
    .replace(/!?\[[^\]]*\]\([^)]*\)/g, " ").replace(/https?:\/\/\S+/g, " ")
    .replace(/<[^>]{1,40}>/g, " ")
    .replace(/[\[\(（【][^\]\)）】]{0,24}[\]\)）】]/g, "")
    .replace(/\s+/g, " ").trim();
  return t;
}

let chain = Promise.resolve();
let pending = 0;

export async function speak(raw) {
  try {
    let cfg = { enabled: true };
    try {
      cfg = Object.assign(cfg, JSON.parse(readFileSync(VOICE_CFG, "utf8")));
    } catch { }
    if (cfg.enabled === false) return;
    const text = clean(raw);
    if (!text) return;
    if (pending > 2) { log("说话积压超过 2 句，丢弃这一句"); return; }
    pending++;
    chain = chain.then(() => new Promise((resolve) => {
      let done = false;
      const fin = () => { if (!done) { done = true; pending--; resolve(); } };
      try {
        const p = spawn(SPEAK_BIN, [SPEAK_PY, text], { stdio: "ignore" });
        p.on("close", fin);
        p.on("error", (e) => { log("TTS 进程启动失败：" + e.message); fin(); });
        setTimeout(() => { try { p.kill(); } catch { } fin(); }, 20000);
      } catch (e) {
        log("TTS 启动异常：" + e.message);
        fin();
      }
    })).catch((e) => log("TTS 队列异常：" + e));
  } catch (e) {
    log("speak 异常：" + e);
  }
}

function listenWithRetry(srv, triesLeft) {
  const onErr = (e) => {
    const code = e && e.code || String(e);
    if (code === "EADDRINUSE" && triesLeft > 1) {
      log(`3099 被占用（EADDRINUSE），1.5s 后重试，还剩 ${triesLeft - 1} 次`);
      setTimeout(() => listenWithRetry(srv, triesLeft - 1), 1500);
    } else {
      log(`3099 监听失败：${code}。跑 02-体检.command（或 health.py --check）看处理建议`);
    }
  };
  srv.once("error", onErr);
  srv.listen(PORT, "127.0.0.1", () => {
    srv.removeListener("error", onErr);
    srv.on("error", (e) => log("服务运行中出错：" + (e && e.code || e)));
    log(`语音接口就绪 http://127.0.0.1:${PORT} pid=${process.pid}`);
  });
}

/**
 * 启动 3099。
 * @param {{chat:(petId:string,text:string)=>Promise<any>, pets:()=>any[]}} opts
 */
export function startVoiceServer(opts = {}) {
  if (globalThis.__petVoiceStarted) { log("已在运行，跳过重复启动"); return null; }
  globalThis.__petVoiceStarted = true;

  const chat = typeof opts.chat === "function" ? opts.chat : null;
  const pets = typeof opts.pets === "function" ? opts.pets : () => [];
  if (!chat) { log("缺少 chat 回调，接口不启动"); return null; }

  const safePets = () => {
    try {
      return (pets() || []).map((p) => p && (p.id ?? p)).filter(Boolean);
    } catch (e) { log("取宠物列表失败：" + e); return []; }
  };

  const srv = http.createServer(async (req, res) => {
    const send = (code, obj) => {
      try {
        res.writeHead(code, { "content-type": "application/json; charset=utf-8" });
        res.end(JSON.stringify(obj));
      } catch { }
    };
    try {
      const url = req.url || "/";
      if (req.method === "POST" && url.startsWith("/say")) {
        let body = "";
        for await (const c of req) {
          body += c;
          if (body.length > 1e6) { req.destroy(); return; }
        }
        let parsed;
        try { parsed = JSON.parse(body || "{}"); } catch { return send(400, { ok: false, reason: "bad-json" }); }
        const text = String(parsed.text || "").trim();
        if (!text) return send(400, { ok: false, reason: "empty" });
        if (text.length > 2000) return send(400, { ok: false, reason: "too-long" });
        let petId = String(parsed.pet || "");
        if (!petId) petId = String(safePets()[0] || "main");
        log(`/say pet=${petId} len=${text.length}`);
        const r = await chat(petId, text);
        log(`/say -> ${JSON.stringify(r)}`);
        return send(200, { ok: true, pet: petId, result: r });
      }
      if (req.method === "GET" && (url.startsWith("/ping") || url.startsWith("/health"))) {
        return send(200, {
          ok: true,
          pid: process.pid,
          uptime: Math.round(process.uptime()),
          pets: safePets(),
          version: "bridge-1.0",
        });
      }
      return send(404, { ok: false, reason: "not-found" });
    } catch (e) {
      log("请求失败：" + (e && e.stack || e));
      return send(500, { ok: false, error: String(e) });
    }
  });

  srv.on("clientError", (e, socket) => { try { socket.destroy(); } catch { } });
  listenWithRetry(srv, 5);
  return srv;
}
