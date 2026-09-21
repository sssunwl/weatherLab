"""推一則訊息到 Discord #n-weather。

Webhook 網址來源(依序):
  1. 環境變數 DISCORD_WEBHOOK_URL(GitHub Actions 從 Secrets 帶入)
  2. ~/.config/weatherlab/discord_webhook(本機測試用)

用法:
  python scrapers/notify_discord.py "訊息內容"
  echo "訊息內容" | python scrapers/notify_discord.py
"""
import os
import sys
from pathlib import Path

import requests

LIMIT = 2000  # Discord 單則上限
LOCAL_FILE = Path.home() / ".config" / "weatherlab" / "discord_webhook"


def webhook_url() -> str:
    url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not url and LOCAL_FILE.exists():
        url = LOCAL_FILE.read_text().strip()
    if not url:
        raise SystemExit("找不到 DISCORD_WEBHOOK_URL")
    return url


def chunks(text: str):
    """超過 2000 字時按行切,不在行中間斷開。"""
    buf = ""
    for line in text.splitlines(keepends=True):
        if len(buf) + len(line) > LIMIT:
            if buf:
                yield buf
            buf = ""
            while len(line) > LIMIT:
                yield line[:LIMIT]
                line = line[LIMIT:]
        buf += line
    if buf:
        yield buf


def send(text: str) -> None:
    url = webhook_url()
    for part in chunks(text):
        r = requests.post(
            url,
            json={"content": part, "allowed_mentions": {"parse": []}},
            timeout=15,
        )
        r.raise_for_status()


if __name__ == "__main__":
    msg = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else sys.stdin.read()
    if not msg.strip():
        raise SystemExit("沒有訊息內容")
    send(msg)
