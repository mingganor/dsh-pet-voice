# 架构与设计取舍

## 组件

| 文件 | 跑在哪 | 职责 |
|---|---|---|
| `src/pet-listen.py` | 独立进程 | 麦克风 → VAD → 声纹 → Whisper → 词表 → POST `/say` |
| `src/pet-voiceid.py` | 手动运行 | 声纹登记 / 测试 / 调阈值 / 删除 |
| `src/pet-speak.py` | 独立进程 | edge-tts 合成 + 情绪声线 + 磁盘缓存 + afplay 播放 |
| `src/bridge/voice-server.mjs` | DSH 宿主进程内 | HTTP 3099、`/say` `/ping`、TTS 调用、日志 |
| `src/bridge/install.py` | 手动运行 | 给 dsh-pet 构建产物打壳：`--check/--apply/--uninstall` |
| `src/bridge/health.py` | 手动运行 | 六项体检 + `--repair` / `--restart` |

## 一次对话的时序

```
pet-listen.py                    宿主进程 (DSH)                  pet-speak.py
     │                                 │                               │
     ├─ 录音 / VAD / 声纹 / ASR          │                               │
     ├─ POST /say {text} ──────────────►│                               │
     │                                 ├─ chatWithPet()                │
     │                                 │   ├─ 人设 + 记忆 + 时间        │
     │                                 │   └─ LLM 生成                  │
     │                                 ├─ speakText(text) ────────────►│
     │◄──────────── {ok:true} ─────────┤                               ├─ edge-tts → afplay
```

## 为什么要「壳 + 补丁」

`chatWithPet`、`effectivePetList` 都是 dsh-pet `apply()` 里的局部变量，没有对外导出。
要让 3099 的请求触发一次真实对话，只能在宿主进程里调用它们。因此：

- 插件里保留两段带标记的壳（出声壳、接口壳），合计约 20 行
- 其余逻辑全部在本仓库的 `voice-server.mjs`
- `install.py` 幂等地插入/替换壳：
  - 先删掉旧标记块（含历史版本）
  - 在 `//#region src/host/whisper.ts` 前插出声壳
  - 在 `startHelper()` 前插接口壳
  - 补 `speakText(...)` 调用点
  - 写完必跑 `node --check`，失败自动用备份回滚
- 每次 apply 都会在插件目录留一份 `index.js.bak-petvoice-<时间>`，`--uninstall` 用最新那份还原

## 端口与生命周期

- 只监听 `127.0.0.1:3099`
- `EADDRINUSE` 重试 5 次（间隔 1.5s），每次写 `~/.dsh/pet-voice.log`
- 进程退出随宿主；重启桌面端即重启服务

## 配置来源

| 文件 | 内容 |
|---|---|
| `~/.dsh/pet-listen.json` | 耳朵参数（判停、模式、热词、词表、声纹阈值） |
| `~/.dsh/pet-voice.json` | TTS 参数（开关、声线、语速、缓存） |
| `~/.dsh/pet-voiceprint.json` | 声纹向量 + 阈值（由 `pet-voiceid.py` 写） |
| `~/.dsh/pet-python` | 装了依赖的 Python 路径（安装器写，桥读） |

判停等参数在**启动时**读取；声纹文件是**热加载**的（改完不用重启耳朵）。

## 关键取舍

- **声纹放在 ASR 之前**：CAM++ 几十毫秒，Whisper 数百毫秒到数秒。先挡人，省 CPU 也省误回复。
- **热词默认关**：Whisper 的 `hotwords` 通过提示词注入，always 模式下有概率把提示词本身念出来；词表后处理更安全。
- **20 秒上限不丢弃**：旧实现超时直接扔，长句会凭空消失；现在照常识别，靠人声占比与长度兜底。
- **TTS 串行 + 限流**：多句同时播会叠音；超过 2 句排队就丢弃最新一句。
- **记忆只存修正后的文本**：耳朵先过词表再发送，避免把「金余娘」写进长期记忆。

## 故障与恢复

| 现象 | 原因 | 处理 |
|---|---|---|
| 能听不能说 | 3099 没起 / 壳被升级覆盖 | `install.py --apply` + 重启 |
| 她不理你 | 声纹阈值太高 | `--test` 看分数，`--threshold` 调低 |
| 别人说话也接 | 阈值太低 / 未登记 | 调高或 `--enroll` |
| 反复复读一句 | 噪声段 + 解码设置 | 已默认关 `condition_on_previous_text` |
| 她自问自答 | TTS 外放被麦克风收进去 | `~/.dsh/pet-speaking` 标记 + 30s 窗口 |

## 目录

```
src/            耳朵、嘴、声纹
src/bridge/     宿主侧服务 + 补丁器 + 体检
scripts/macos/  双击即用的 .command
docs/           本文档与演示素材
```
