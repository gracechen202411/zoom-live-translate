#!/usr/bin/env python3
"""
Zoom 聊天实时翻译：读取 zoom-chat-translate-reader.swift 输出的聊天行（stdin），
去重后批量交给 Gemini 翻成中文，打印到终端，并追加到聊天记录 md。

用法（一般由 zoom-chat-translate.sh 调用）：
    swift zoom-chat-translate-reader.swift | python3 zoom-chat-translate.py --out 聊天.md
"""

import argparse
import importlib.util
import json
import queue
import re
import sys
import threading
import time
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")  # 屏蔽 Python / urllib3 版本提示，别刷屏

from google import genai

# 复用字幕翻译器的鉴权、模型和领域配置，改一处两边生效
_spec = importlib.util.spec_from_file_location(
    "zoom_live_translate", Path(__file__).with_name("zoom-live-translate.py")
)
live = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(live)

# 「Minh发给所有人, cho em hỏi, giá bao nhiêu ạ, 17:25」；消息本身可能含逗号，所以时间从末尾取
ROW_RE = re.compile(r"^(?P<sender>.+?)(?:发给| to )(?P<target>[^,]+), (?P<text>.*), (?P<time>\d{1,2}:\d{2})$", re.S)
# 新消息攒这么久再一起翻，一次请求翻多条，省钱也不刷屏
BATCH_WAIT_SEC = 1.0

DIM, GREEN, CYAN, BOLD, RESET = "\033[2m", "\033[92m", "\033[96m", "\033[1m", "\033[0m"

SYSTEM_PROMPT = (
    f"你是翻译。下面是{live.TRANSLATE_CONTEXT}的观众在聊天区发的消息（口语、缩写、表情如 =)) 很多）。"
    "把每条翻成简洁自然的中文，保留英文专有名词和术语；"
    "纯表情或无意义的原样输出。输入是 JSON 数组，只输出等长的 JSON 字符串数组，不要解释。"
)


def translate_batch(client: genai.Client, texts: "list[str]") -> "list[str]":
    """一次翻译多条；主模型失败降级备用模型，全失败就原样返回，聊天不能卡住。"""
    for attempt, model in enumerate((live.GEMINI_MODEL, live.FALLBACK_MODEL, live.FALLBACK_MODEL)):
        try:
            resp = client.models.generate_content(
                model=model,
                contents=json.dumps(texts, ensure_ascii=False),
                config=genai.types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    temperature=0.2,
                    response_mime_type="application/json",
                ),
            )
            parts = resp.candidates[0].content.parts if resp.candidates else []
            result = json.loads("".join(p.text for p in parts or [] if p.text and not p.thought))
            if isinstance(result, list) and len(result) == len(texts):
                return [str(x) for x in result]
        except Exception:
            pass
        time.sleep(0.8 * (attempt + 1))
    return ["（翻译失败）"] * len(texts)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, help="聊天记录 markdown 路径")
    args = parser.parse_args()

    client = live.build_gemini_client()
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_file = out_path.open("a", encoding="utf-8")
    if out_path.stat().st_size == 0:
        out_file.write(f"# Zoom 聊天记录 {time.strftime('%Y-%m-%d %H:%M')}\n\n")
        out_file.flush()

    pending: "queue.Queue[dict | None]" = queue.Queue()

    def worker() -> None:
        while True:
            first = pending.get()
            if first is None:
                return
            batch = [first]
            deadline = time.time() + BATCH_WAIT_SEC
            while (left := deadline - time.time()) > 0:
                try:
                    item = pending.get(timeout=left)
                except queue.Empty:
                    break
                if item is None:
                    pending.put(None)
                    break
                batch.append(item)
            translations = translate_batch(client, [m["text"] for m in batch])
            for msg, zh in zip(batch, translations):
                sys.stdout.write(
                    f"{DIM}{msg['time']}{RESET}  {CYAN}{msg['sender']}{RESET}  {BOLD}{GREEN}{zh}{RESET}\n"
                    f"       {DIM}{msg['text']}{RESET}\n\n"
                )
                out_file.write(f"**{msg['time']} {msg['sender']}**：{zh}\n> {msg['text']}\n\n")
            sys.stdout.flush()
            out_file.flush()

    worker_thread = threading.Thread(target=worker)
    worker_thread.start()
    sys.stdout.write(f"{GREEN}▶ 聊天翻译已就绪{RESET}  记录：{out_path}\n\n")
    sys.stdout.flush()

    seen: "set[str]" = set()
    for line in sys.stdin:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "error" in payload or "status" in payload:
            sys.stdout.write(f"{DIM}ℹ️ {payload.get('error') or payload.get('status')}{RESET}\n")
            sys.stdout.flush()
            continue
        row = payload.get("row", "")
        # 虚拟滚动会反复读到同一行；同人同分钟同内容视为同一条
        if row in seen:
            continue
        seen.add(row)
        match = ROW_RE.match(row)
        if match:
            pending.put(match.groupdict())

    pending.put(None)
    worker_thread.join()
    out_file.close()


if __name__ == "__main__":
    main()
