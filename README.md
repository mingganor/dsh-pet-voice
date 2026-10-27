# dsh-pet-voice

> 给 [dsh-pet](https://github.com/PC2005-cloud/dsh-pet) 桌宠装上**耳朵、声纹和情绪语音**。  
> 全程本地运行：麦克风 → VAD → Whisper → 声纹校验 → 桌宠对话 → TTS，音频不离开你的电脑。

![license](https://img.shields.io/badge/license-MIT-blue.svg)
![python](https://img.shields.io/badge/python-3.10%2B-3776AB.svg)
![platform](https://img.shields.io/badge/platform-macOS%20%7C%20Linux%20%7C%20Windows-lightgrey.svg)

<!-- 演示：把录屏存成 docs/demo.gif 后，取消下面这行注释
![demo](docs/demo.gif)
-->

## 它解决什么

dsh-pet 本身没有音频：不会说话，也听不见你。这个项目补上这一层，并且解决了「装好之后一升级就坏」的问题：

- **听得见**：常驻监听 + Silero VAD 判停，说完约 1.3 秒自动收句
- **听得准**：本地 faster-whisper（CPU int8）+ 同音词表 + 反幻听过滤
- **只认你**：CAM++ 声纹门控，别人说话不接（在识别之前就挡掉，省 CPU）
- **说得好**：edge-tts 按台词情绪切 5 种声线，命中缓存直接播
- **坏不了**：语音逻辑放在插件之外，升级覆盖后一条命令补回，并自带六项体检

## 平台支持

| 平台 | 状态 | 说明 |
|---|---|---|
| macOS | ✅ 实测 | 播放用系统自带 `afplay`；`scripts/macos/*.command` 双击即用；`health.py --restart` 能自动重启桌面端 |
| Linux | ⚠️ 理论可用，未实测 | 播放器自动在 `paplay` / `aplay` / `ffplay` 里挑；没有 `.command`，直接跑命令；重启请手动 |
| Windows | ⚠️ 未实测，建议 WSL | 原生需要 `ffplay`（ffmpeg），且 `install.sh` 是 bash 脚本；WSL 里按 Linux 走，但麦克风要转发（PulseAudio） |

录音、VAD、Whisper、声纹、TTS 都是纯 Python，本身跨平台；差异只在**播放器**、**进程检查**和**快捷方式**三处。

## 架构

```
麦克风 ──► pet-listen.py ─────────────────────────────────────────┐
            │ 16k 单声道 / 环形缓冲 / 0.5s 预录                    │
            │ Silero VAD 判「有人声」+ 1.3s 静音判「说完了」        │
            │ 人声占比 <25% 丢弃                                   │
            │ 声纹（CAM++）< 阈值 → 丢弃                            │
            │ faster-whisper small int8 → 繁转简 → 同音词表          │
            │ 反幻听：no_speech / 假名 / 复读 / 超长                 │
            ▼                                                      │
   POST 127.0.0.1:3099/say ──► voice-server.mjs（宿主进程内）        │
            │ chatWithPet：人设 + 最近 5 轮记忆 + 当前时间           │
            │ LLM 生成                                             │
            ▼                                                      │
     speakText() ──► pet-speak.py（edge-tts + 情绪声线 + 缓存）──────┘
```

> 为什么语音桥必须插在宿主进程里：`chatWithPet` 是 dsh-pet `apply()` 的闭包变量，
> 外部进程拿不到。所以插件里只留约 20 行「壳」，真正的逻辑都在本仓库，升级后重新打壳即可。

## 快速开始

前置：已经装好 **dsh-pet**（桌面 profile），并且有 Python 3.10+。

### macOS / Linux

```bash
git clone https://github.com/mingganor/dsh-pet-voice.git
cd dsh-pet-voice

# 1) 依赖
python3 -m pip install -r requirements.txt

# 2) 安装（复制到 ~/.dsh + 下载声纹模型 + 给插件打壳）
bash install.sh
#   如果默认 python3 没装依赖：PET_PYTHON=/path/to/python bash install.sh
#   报权限问题：chmod +x install.sh

# 3) 重启 DSH 桌面端

# 4) 录一次声纹（读 5 句）
python3 ~/.dsh/pet-voiceid.py --enroll
```

### Windows

原生未实测，推荐 WSL（按 Linux 走）。要在原生 Windows 跑，步骤等价，只是路径换成 `%USERPROFILE%\.dsh`：

1. 装 ffmpeg（提供 `ffplay`，用来播放 TTS 音频）
2. `src\pet-listen.py`、`src\pet-speak.py`、`src\pet-voiceid.py` → `%USERPROFILE%\.dsh\`
3. `src\bridge\` 下三个文件 → `%USERPROFILE%\.dsh\pet-bridge\`
4. 把 Python 解释器路径单写一行到 `%USERPROFILE%\.dsh\pet-python`
5. `campplus.onnx` 下载到 `%USERPROFILE%\.dsh\models\`
6. `python %USERPROFILE%\.dsh\pet-bridge\install.py --apply`

`install.py` / `health.py` 都是纯 Python，路径用 `expanduser("~")`，Windows 下同样成立；只有 `--restart` 是 macOS 专属。

## 日常使用

| 想做什么 | 命令 |
|---|---|
| 录入 / 重录声纹 | `python3 ~/.dsh/pet-voiceid.py --enroll` |
| 测试像不像你 | `python3 ~/.dsh/pet-voiceid.py --test` |
| 调阈值 | `python3 ~/.dsh/pet-voiceid.py --threshold 0.5` |
| 删掉声纹 | `python3 ~/.dsh/pet-voiceid.py --reset` |
| 体检 | `python3 ~/.dsh/pet-bridge/health.py --check` |
| 插件升级后补壳 | `python3 ~/.dsh/pet-bridge/install.py --apply` |

macOS 用户可以把 `scripts/macos/*.command` 拷到桌面，双击即用（总控台 / 一键开启 / 体检 / 声纹）；Linux / Windows 直接用上表命令。

## 配置

`~/.dsh/pet-listen.json`：

| 字段 | 说明 |
|---|---|
| `mode` | `always` 听到就说；`wakeword` 先喊唤醒词 |
| `wakewords` | 唤醒词列表（示例按某只桌宠的名字配的） |
| `silence` | 静音多久算说完（秒），默认 1.3 |
| `maxSeconds` | 单句上限，默认 20（到点照常识别，不丢弃） |
| `vadThreshold` / `minRms` | VAD 灵敏度 / 音量门槛 |
| `hotwords` | Whisper 热词，默认空（always 模式下可能被念出来，谨慎开） |
| `lexicon` | 额外的 `[["错","对"], ...]` 同音词表 |
| `voiceprint` | `{enabled, threshold, minSeconds}` 声纹开关与阈值 |

`~/.dsh/pet-voice.json`：TTS 开关、声线、语速、缓存上限。

## 隐私

- 音频只在内存里流转，**不落盘**
- 声纹只存本地 `~/.dsh/pet-voiceprint.json`（一个 192 维向量），不上传
- 日志 `~/.dsh/pet-voice.log` 与 `/tmp/pet-listen.log` 含**转写文本**，分享日志前先看一眼
- 本仓库的 `.gitignore` 已排除声纹、记忆、缓存、日志

## 已知限制

- 平台：macOS 实测；Linux 理论可用（播放器自动选，未实测）；Windows 建议 WSL
- macOS 专属：`scripts/macos/*.command` 快捷方式、`health.py --restart`（依赖 `open -a` 与 SingletonLock）
- 麦克风设备名/权限各平台不同，必要时在 `pet-listen.json` 里指定 `device`
- 首次加载 Whisper 模型约 30–180 秒（之后 1–2 秒）
- 连续 20 秒的背景人声会整段解码（录了声纹后会在校验阶段被挡掉）
- 声纹阈值需要按人校准：同人通常 0.7–0.95，不同人 0.2–0.5

## 踩过的坑（设计笔记）

1. **3099 端口静默失败**：插件重启的瞬间旧进程还占着端口，`listen` 报 `EADDRINUSE` 被空 `catch` 吞掉，表现是「能听不能说」。现在会重试 5 次并把每一次都写进日志。
2. **插件升级覆盖注入**：原先把 90 行逻辑直接写进 `node_modules` 的构建产物，`dsh plugin add` 一跑就没了。现在只留壳，逻辑外置，并配了补丁管理器（备份 + `node --check` + 失败回滚）。
3. **Whisper 复读 / 幻听**：`condition_on_previous_text` 默认开启时，噪声段会复读同一句；关掉后明显好转，再叠加 `no_speech_prob`、假名、超长过滤。
4. **声纹放在识别之前**：声纹推理只要几十毫秒，比跑一次 Whisper 便宜一个数量级；先挡掉别人，CPU 和误回复都省。
5. **判停太短会吃字**：1.0s → 1.3s，尾字被吞的情况少很多。

## 许可与致谢

- 本仓库代码：**MIT**（见 [LICENSE](LICENSE)）
- 上游 [dsh-pet](https://github.com/PC2005-cloud/dsh-pet)：代码 MIT；**美术素材 CC BY-NC-SA 4.0，禁止商用**。本仓库不包含其代码与素材
- 第三方依赖、模型许可：见 [NOTICE](NOTICE)

这是一个非官方增强项目，与 dsh-pet 作者无隶属关系。
