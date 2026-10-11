#!/usr/bin/env python3
"""
Zoom 实时翻译器：读取 whisper-stream 的越南语转录（stdin），用 Gemini 翻成中文，
实时打印到终端，并把「原文 + 译文」逐条追加到逐字稿文件。

用法（一般由 zoom-live-translate.sh 调用）：
    whisper-stream ... | python3 zoom-live-translate.py --out 逐字稿.md [--lang vi]
"""

import argparse
import json
import os
import warnings

warnings.filterwarnings("ignore")  # 屏蔽 Python / urllib3 版本提示，别刷屏

import queue
from collections import deque
from difflib import SequenceMatcher
import re
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from google import genai

# 可用环境变量覆盖；备用模型在主模型偶发 429 限流时顶上
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")
FALLBACK_MODEL = os.environ.get("GEMINI_FALLBACK_MODEL", "gemini-3.5-flash-lite")
# 告诉翻译模型讲的是什么领域，术语会翻得更准，例如「跨境电商 / 广告投放课程」
TRANSLATE_CONTEXT = os.environ.get("TRANSLATE_CONTEXT", "在线课程 / 直播讲座")

# whisper-stream VAD 模式输出格式：[00:00:00.000 --> 00:00:05.000]  文本
SEGMENT_RE = re.compile(r"^\[\d{2}:\d{2}:\d{2}\.\d{3} --> \d{2}:\d{2}:\d{2}\.\d{3}\]\s*(.*)$")

# whisper 在静音段常见的幻觉句，直接丢弃
HALLUCINATION_PATTERNS = (
    "subscribe", "đăng ký kênh", "cảm ơn các bạn đã xem", "hẹn gặp lại",
    "[âm nhạc]", "(nhạc)", "♪",
)

LANG_NAMES = {"vi": "越南语", "en": "英语"}

# 终端颜色；上游 script(1) 把终端置为 raw 模式，必须显式 \r\n 才能回到行首
DIM, GREEN, BOLD, RESET = "\033[2m", "\033[92m", "\033[1m", "\033[0m"
EOL = "\r\n"
# 少于这个词数的碎片先攒着，最多等 MERGE_WAIT_SEC 秒并入下一句再翻
MERGE_MIN_WORDS = 6
MERGE_WAIT_SEC = 2.5
# 与最近几句的字符相似度达到这个值就当重复识别丢掉
REPEAT_SIMILARITY = 0.6


def build_gemini_client() -> genai.Client:
    """两种鉴权二选一：
    - GEMINI_API_KEY：Google AI Studio 的 key，最简单
    - GOOGLE_APPLICATION_CREDENTIALS：Vertex 服务账号 JSON，project 取自文件本身
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    if api_key:
        return genai.Client(api_key=api_key)
    sa_file = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if not sa_file or not Path(sa_file).exists():
        sys.exit("ERROR: 请设置 GEMINI_API_KEY，或把 GOOGLE_APPLICATION_CREDENTIALS 指向 Vertex 服务账号 JSON")
    project = json.loads(Path(sa_file).read_text()).get("project_id") or os.environ["GOOGLE_CLOUD_PROJECT"]
    return genai.Client(vertexai=True, project=project, location="global")


def normalize(text: str) -> str:
    """去标点、小写、压空格，让 'add on' 与 'addon'、'BB' 与 'bê bê' 这类差异不影响比对。"""
    return re.sub(r"[^\w]+", "", text.lower())


def is_repeat(text: str, recent: "deque[str]") -> bool:
    """whisper VAD 模式每次重识别最近 12 秒，上一句尾巴会再出现一次且用词略有出入；
    与最近几句字符级相似度 ≥ 0.6，或整体被包含，都视为重复。"""
    new = normalize(text)
    if not new:
        return True
    for old in recent:
        old_n = normalize(old)
        if new in old_n or old_n in new:
            return True
        if SequenceMatcher(None, new, old_n).ratio() >= REPEAT_SIMILARITY:
            return True
    return False


def is_hallucination(text: str) -> bool:
    lowered = text.lower()
    return len(text) < 2 or any(p in lowered for p in HALLUCINATION_PATTERNS)


def build_translator(client: genai.Client, src_lang: str):
    src_name = LANG_NAMES.get(src_lang, src_lang)
    system_prompt = (
        f"你是同声传译。把老师口语化的{src_name}讲课内容翻成简洁自然的中文。"
        f"背景：{TRANSLATE_CONTEXT}。"
        "只输出中文译文，不解释，不加引号；英文专有名词（如 Shopify、TikTok、CPA）保留英文；"
        "听不清或无意义的片段输出「……」。"
    )

    def translate(text: str) -> str:
        last_error = ""
        # 主模型两次 + 备用模型两次，间隔退避；Vertex global 端点偶发 429，不能让直播翻译中断
        for attempt, model in enumerate((GEMINI_MODEL, GEMINI_MODEL, FALLBACK_MODEL, FALLBACK_MODEL)):
            try:
                resp = client.models.generate_content(
                    model=model,
                    contents=text,
                    config=genai.types.GenerateContentConfig(
                        system_instruction=system_prompt,
                        temperature=0.2,
                        # 关掉思考：默认会先「想」3~5 秒，比讲者说一句还慢，队列越积越多；关掉后约 0.9 秒
                        thinking_config=genai.types.ThinkingConfig(thinking_budget=0),
                    ),
                )
                # 不用 resp.text：Gemini 3.x 回包带 thought_signature，resp.text 会打警告刷屏挤乱字幕
                parts = resp.candidates[0].content.parts if resp.candidates else []
                return "".join(p.text for p in parts or [] if p.text and not p.thought).strip()
            except Exception as exc:
                last_error = f"{exc.__class__.__name__}: {str(exc)[:60]}"
                time.sleep(0.8 * (attempt + 1))
        return f"（翻译失败：{last_error}）"

    return translate


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, help="逐字稿 markdown 路径")
    parser.add_argument("--lang", default="vi", help="老师说的语言代码，默认 vi")
    args = parser.parse_args()

    client = build_gemini_client()
    translate = build_translator(client, args.lang)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_file = out_path.open("a", encoding="utf-8")
    if out_path.stat().st_size == 0:
        out_file.write(f"# Zoom 直播逐字稿 {datetime.now():%Y-%m-%d %H:%M}\n\n")
        out_file.flush()

    # 翻译放单独线程，避免 API 延迟堵住转录读取
    pending: "queue.Queue[str | None]" = queue.Queue()

    def next_sentence() -> "str | None":
        """从队列取一句；碎片过短时最多等 MERGE_WAIT_SEC 秒把后续碎片拼进来。"""
        src = pending.get()
        if src is None:
            return None
        while len(src.split()) < MERGE_MIN_WORDS:
            try:
                more = pending.get(timeout=MERGE_WAIT_SEC)
            except queue.Empty:
                break
            if more is None:
                pending.put(None)  # 结束信号留给下一轮
                break
            src = f"{src} {more}"
        return src

    def worker() -> None:
        while True:
            src = next_sentence()
            if src is None:
                break
            zh = translate(src)
            stamp = datetime.now().strftime("%H:%M:%S")
            sys.stdout.write(
                f"{DIM}{stamp}{RESET}  {BOLD}{GREEN}{zh}{RESET}{EOL}"
                f"          {DIM}{src}{RESET}{EOL}{EOL}"
            )
            sys.stdout.flush()
            out_file.write(f"**{stamp}** {zh}\n> {src}\n\n")
            out_file.flush()

    worker_thread = threading.Thread(target=worker)
    worker_thread.start()
    sys.stdout.write(f"{GREEN}▶ 实时翻译已就绪，等待老师说话……{RESET}  逐字稿：{out_path}{EOL}{EOL}")
    sys.stdout.flush()

    recent: "deque[str]" = deque(maxlen=6)
    for raw in sys.stdin:
        match = SEGMENT_RE.match(raw.strip())
        if not match:
            continue
        text = match.group(1).strip()
        if not text or is_hallucination(text) or is_repeat(text, recent):
            continue
        recent.append(text)
        pending.put(text)

    pending.put(None)
    worker_thread.join()  # 等最后一句翻完再退出
    out_file.close()


if __name__ == "__main__":
    main()
