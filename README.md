# zoom-live-translate

在 Mac 上给 Zoom 会议 / 网络研讨会做**实时中文翻译**：

- **字幕翻译**：抓 Zoom 的声音，本地用 Whisper 识别成原文，再用 Gemini 翻成中文，在终端里滚动显示。
- **聊天翻译**：读取 Zoom 聊天窗口里的新消息，批量翻成中文，带上发送人和时间。

两样都会同时存一份 Markdown 记录（原文 + 译文），课后可以直接拿来整理笔记。

最初是为了听越南语的跨境电商直播课做的，任何 Whisper 支持的语言都能用。

```
Zoom 声音 → 多输出设备（扬声器 + BlackHole）→ whisper-stream（本地识别）→ Gemini → 终端字幕 + 逐字稿.md
Zoom 聊天窗口 → macOS 辅助功能接口读取 → Gemini（批量）→ 终端 + 聊天记录.md
```

## 效果

```
17:15:13  先把利润率跑稳，再慢慢加预算。
          Chạy ổn biên lợi nhuận trước, rồi mới tăng ngân sách từ từ

17:26  Minh  这个广告结构新手也能用吗？
       Cấu trúc ad này newbie dùng được không ạ?
```

## 环境要求

- macOS（用到 CoreAudio 和辅助功能接口，Apple Silicon 上 Whisper 跑得快）
- Xcode 命令行工具（提供 `swift`）：`xcode-select --install`
- [BlackHole 2ch](https://github.com/ExistentialAudio/BlackHole)（虚拟声卡，装完重启一次）：`brew install blackhole-2ch`
- [whisper.cpp](https://github.com/ggml-org/whisper.cpp)（提供 `whisper-stream`）：`brew install whisper-cpp`
- Python 3.9+，以及 `pip install google-genai`
- 一个 Gemini 账号：Google AI Studio 的 API key，或 Vertex AI 服务账号

## 安装

```bash
git clone https://github.com/gracechen202411/zoom-live-translate.git
cd zoom-live-translate
pip install google-genai

# 下载 Whisper 模型（约 1.6 GB）
mkdir -p ~/.cache/whisper-models
curl -L -o ~/.cache/whisper-models/ggml-large-v3-turbo.bin \
  https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo.bin

# 建「Zoom+字幕」多输出设备并设为系统输出（只需一次；以后上课前再跑一次即可切过去）
swift zoom-live-translate-audio-setup.swift
```

鉴权二选一，写进 `~/.zshrc`：

```bash
# 方式一：Google AI Studio key（最简单）
export GEMINI_API_KEY="你的 key"

# 方式二：Vertex AI 服务账号（project 自动从 JSON 里读）
export GOOGLE_APPLICATION_CREDENTIALS="$HOME/path/to/service-account.json"
```

## 使用

**字幕翻译**

```bash
./zoom-live-translate.sh        # 讲者说越南语（默认）
./zoom-live-translate.sh en     # 讲者说英语
```

上课前确认 系统设置 → 声音 → 输出 选的是「Zoom+字幕」，否则抓不到声音。下课后切回扬声器：

```bash
swift zoom-live-translate-audio-setup.swift restore
```

**聊天翻译**

```bash
./zoom-chat-translate.sh
```

- 第一次用要给终端 App 授权：系统设置 → 隐私与安全性 → 辅助功能，打开你用的终端（Terminal / iTerm 等）。
- Zoom 聊天窗口要开着，并**停在最底部**。Zoom 的聊天列表只渲染看得见的消息，往上翻时新消息可能漏掉。

两个可以同时开，各占一个终端窗口。按 Ctrl+C 停止。记录默认存在 `./transcripts/`。

## 配置

| 环境变量 | 默认值 | 作用 |
|---|---|---|
| `GEMINI_API_KEY` | — | AI Studio key，设了就优先用它 |
| `GOOGLE_APPLICATION_CREDENTIALS` | — | Vertex 服务账号 JSON 路径 |
| `GEMINI_MODEL` | `gemini-3.5-flash` | 主翻译模型 |
| `GEMINI_FALLBACK_MODEL` | `gemini-3.5-flash-lite` | 主模型被限流时自动切换 |
| `TRANSLATE_CONTEXT` | `在线课程 / 直播讲座` | 告诉模型讲的是什么领域，术语翻得更准，例如 `跨境电商 / 广告投放课程` |
| `WHISPER_MODEL` | `~/.cache/whisper-models/ggml-large-v3-turbo.bin` | Whisper 模型路径 |
| `OUT_DIR` | `./transcripts` | 逐字稿和聊天记录的保存目录 |
| `PYTHON` | `python3` | 用哪个 Python 跑 |
| `ZOOM_SPEAKER_NAME` | 自动找内置扬声器 | 想让声音从外接音箱 / 耳机出来时，填设备名 |

## 费用和隐私

- **声音不出本机**：语音识别完全在本地跑，不花钱。
- **只有文字会发给 Gemini**：每句原文发一次请求，聊天消息攒一秒批量发。一节一小时的课大约几百次短请求，用 Flash 系列一般只要几美分。
- 所有程序都是前台运行，Ctrl+C 就停，不会在后台常驻。

## 已知限制

- 只支持 macOS。
- 聊天翻译只在中文版和英文版 Zoom 上测过窗口识别；其他语言界面可能要改 `zoom-chat-translate-reader.swift` 里的窗口标题关键词。
- 逐字稿不区分是谁在说话。
- Whisper 在静音时偶尔会编出「谢谢观看」之类的句子，脚本过滤了常见的几种，但不是全部。

## 文件说明

| 文件 | 作用 |
|---|---|
| `zoom-live-translate.sh` | 字幕翻译入口：找 BlackHole 设备、启动 whisper-stream |
| `zoom-live-translate.py` | 去重、合并碎句、调用 Gemini、写逐字稿 |
| `zoom-live-translate-audio-setup.swift` | 创建 / 切换「Zoom+字幕」多输出设备 |
| `zoom-chat-translate.sh` | 聊天翻译入口 |
| `zoom-chat-translate-reader.swift` | 通过辅助功能接口读取 Zoom 聊天窗口 |
| `zoom-chat-translate.py` | 去重、批量翻译、写聊天记录 |

## 许可证

MIT
