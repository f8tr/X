import os
import re
import json
import asyncio
import logging
from typing import Optional, Dict, Any

import requests
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    filters,
)

# =========================================================
# 🔐 قراءه المتغيرات من الـ Environment
# =========================================================
BOT_TOKEN = os.getenv("BOT_TOKEN")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN غير موجود")

if not DEEPSEEK_API_KEY:
    raise RuntimeError("DEEPSEEK_API_KEY غير موجود")

# =========================================================
# 🧾 Logging
# =========================================================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# =========================================================
# 🧩 Utilities
# =========================================================

def extract_username(text: str) -> Optional[str]:
    text = text.strip()

    m = re.search(r"(?:https?://)?(?:x\.com|twitter\.com)/([A-Za-z0-9_]{1,30})", text)
    if m: return m.group(1)

    m = re.search(r"@([A-Za-z0-9_]{1,30})", text)
    if m: return m.group(1)

    if re.fullmatch(r"[A-Za-z0-9_]{1,30}", text):
        return text

    return None


def fetch_x_markdown(username: str) -> str:
    url = f"https://r.jina.ai/https://x.com/{username}"

    resp = requests.get(url, timeout=20)
    if resp.status_code != 200:
        raise RuntimeError(f"فشل جلب الصفحة، الكود: {resp.status_code}")

    text = resp.text.strip()
    if len(text) < 200:
        raise RuntimeError("محتوى الصفحة قليل — الحساب خاص أو فاضي")

    return text


def build_prompt(username: str, page_text: str) -> str:
    return f"""
سأعطيك نص صفحة مستخدم X.
حلل فقط المعلومات الموجودة.

أرجع JSON فقط بهذا الشكل:

{{
  "bio": "...",
  "topics": "...",
  "personality": "...",
  "hobbies": "...",
  "security": "...",
  "summary": "..."
}}

النص:
\"\"\"{page_text[:12000]}\"\"\" 
"""


def call_deepseek(prompt: str) -> Dict[str, Any]:
    url = "https://api.deepseek.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": "ارجع JSON فقط."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.4,
    }

    resp = requests.post(url, headers=headers, json=payload, timeout=50)
    resp.raise_for_status()

    content = resp.json()["choices"][0]["message"]["content"].strip()

    try:
        return json.loads(content)
    except:
        return {"summary": content}


def format_report(username: str, data: Dict[str, Any]) -> str:
    g = lambda k, d="غير واضح": str(data.get(k, d))

    return f"""الهدف: @{username}
──────────────
📝 البايو:
{g("bio")}

🧵 المواضيع:
{g("topics")}

🧠 الشخصية:
{g("personality")}

🎭 الهوايات:
{g("hobbies")}

🚨 الملاحظات:
{g("security")}

🤖 ملخص:
{g("summary")}

👁‍🗨 انتهى التقرير.
"""


def build_report(username: str) -> str:
    page = fetch_x_markdown(username)
    prompt = build_prompt(username, page)
    data = call_deepseek(prompt)
    return format_report(username, data)


# =========================================================
# 🧵 Telegram Handlers
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("ارسل يوزر X وسأحلله 🔍")


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message.text.strip()
    username = extract_username(msg)

    if not username:
        await update.message.reply_text("ارسل يوزر صحيح مثل: @elonmusk")
        return

    waiting = await update.message.reply_text("⏳ جاري التحليل...")

    try:
        loop = asyncio.get_running_loop()
        report = await asyncio.to_thread(build_report, username)
        await waiting.edit_text(report)
    except Exception as e:
        await waiting.edit_text(f"❌ خطأ: {e}")


# =========================================================
# 🚀 RUN BOT
# =========================================================

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    print("Bot is running…")
    app.run_polling()


if __name__ == "__main__":
    main()
