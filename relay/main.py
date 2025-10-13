import os
import html
from typing import Optional

import requests
from fastapi import FastAPI, Request, Header, HTTPException
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

HOOK_SECRET = os.getenv("HOOK_SECRET")
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
PARSE_MODE = os.getenv("TELEGRAM_PARSE_MODE")

if not (HOOK_SECRET and BOT_TOKEN and CHAT_ID):
    raise RuntimeError("Missing one of HOOK_SECRET, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID env vars.")

app = FastAPI(title="Telegram Relay")

class Msg(BaseModel):
    text: str

def _escape(text: str) -> str:
    if not PARSE_MODE:
        return text
    if PARSE_MODE == "HTML":
        return html.escape(text)
    if PARSE_MODE in ("Markdown", "MarkdownV2"):
        specials = r"_*[]()~`>#+-=|{}.!"
        return "".join("\\" + c if c in specials else c for c in text)
    return text

def _chunk(text: str, limit: int = 4096) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks, start = [], 0
    while start < len(text):
        end = min(start + limit, len(text))
        newline = text.rfind("\n", start, end)
        if newline == -1 or newline <= start + 100:
            newline = end
        chunks.append(text[start:newline])
        start = newline
    return chunks

@app.post("/")
async def root(msg: Msg, request: Request, x_hook_secret: Optional[str] = Header(None)):
    if x_hook_secret != HOOK_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")

    text = _escape(msg.text or "")
    if not text:
        raise HTTPException(status_code=400, detail="Missing text")

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    for part in _chunk(text):
        payload = {"chat_id": CHAT_ID, "text": part, "disable_web_page_preview": True}
        if PARSE_MODE:
            payload["parse_mode"] = PARSE_MODE
        r = requests.post(url, json=payload, timeout=20)
        if r.status_code != 200:
            try:
                details = r.json()
            except Exception:
                details = r.text
            raise HTTPException(status_code=502, detail=f"Telegram error {r.status_code}: {details}")
    return {"ok": True}
