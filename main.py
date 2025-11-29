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
    raise RuntimeError("BOT_TOKEN غير موجود في الـ Environment")

if not DEEPSEEK_API_KEY:
    raise RuntimeError("DEEPSEEK_API_KEY غير موجود في الـ Environment")

# =========================================================
# 🧾 إعداد اللوق
# =========================================================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# =========================================================
# 🧩 دوال مساعدة
# =========================================================

def extract_username(text: str) -> Optional[str]:
    """
    يستخرج يوزر X من:
    - @username
    - https://x.com/username
    - https://twitter.com/username
    - او كلمة عاديه بدون @
    """
    text = text.strip()

    # لو فيه URL
    m = re.search(r"(?:https?://)?(?:x\.com|twitter\.com)/([A-Za-z0-9_]{1,30})", text)
    if m:
        return m.group(1)

    # لو فيه @
    m = re.search(r"@([A-Za-z0-9_]{1,30})", text)
    if m:
        return m.group(1)

    # لو بس كلمة بدون مسافات ونفس شروط اليوزر
    if re.fullmatch(r"[A-Za-z0-9_]{1,30}", text):
        return text

    return None


def fetch_x_profile_markdown(username: str) -> str:
    """
    نستخدم خدمة Jina Reader:
    تاخذ صفحة X وترجعها نص/ماركداون جاهز للـ NLP.
    ما تحتاج تسجيل دخول، بس الحساب لازم يكون عام.
    """
    # لو تحب تغيّر الدومين لاحقاً خله هنا بس
    url = f"https://r.jina.ai/https://x.com/{username}"

    resp = requests.get(url, timeout=25)
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code} من خدمة قراءة الصفحة")

    text = resp.text.strip()

    # حماية بسيطة لو رجّع لنا صفحة غريبة
    if not text or len(text) < 200:
        raise RuntimeError("ما لقيت محتوى كفاية من صفحة X (يمكن الحساب مقفل او شبه فاضي).")

    return text


def build_deepseek_prompt(username: str, page_text: str) -> str:
    """
    برومبت مخصص لــ DeepSeek عشان يرجّع لنا JSON مرتب.
    نخليه آمن: ما يخترع عمر/دولة/موقع من رأسه.
    """
    prompt = f"""
انت محلل محتوى لتويتر/X.
سأعطيك نصاً طويلاً يمثل محتوى صفحة مستخدم في X (بايو + تغريدات حديثة + معلومات أخرى).

مهم جداً:
- اعتمد فقط على المعلومات الموجودة في النص.
- لا تخترع عمر، مدينة، دولة، او معلومات شخصية إذا لم تكن مكتوبة بشكل صريح وواضح.
- إذا ما قدرت تعرف شيء، اكتب "غير واضح" أو "ما يظهر من النص".

أريد منك أن ترجع **فقط** JSON بهذا الشكل (بدون أي نص خارجه):

{{
  "bio": "نص مختصر للبايو إذا كان موجوداً، وإلا اشرح انه غير موجود.",
  "main_topics": "ما هي المواضيع اللي يتكلم عنها غالباً؟ (تداول، كريبتو، برمجة، ألعاب، حياة يومية ...الخ).",
  "personality": "انطباع عام محترم عن شخصيته من اسلوب تغريداته (هادئ، عصبي، يمزح كثير، رسمي ...الخ).",
  "hobbies": "أي اهتمامات او هوايات واضحة من كلامه (إن وجدت).",
  "security_note": "هل فيه أشياء ممكن تعتبر حساسة أو عدوانية او ألفاظ سيئة او لا؟ اذكرها بشكل عام بدون مبالغة.",
  "short_summary": "ملخص عام عن صاحب الحساب بجملتين او ثلاث."
}}

مرة ثانية:
- أرجع JSON صالح مباشرة، بدون أسطر توضيحية، بدون ``` ، بدون تعليقات.
- لا تذكر اسم المستخدم في الحقول، لأن الكود يعرفه أصلاً.

النص الكامل لصفحة المستخدم @{username} هو:

\"\"\"{page_text[:12000]}\"\"\"  # قصينا لو كان طويل جداً
"""
    return prompt


def call_deepseek_api(prompt: str) -> Dict[str, Any]:
    """
    استدعاء DeepSeek بصيغة متوافقة مع OpenAI API.
    يرجع dict فيه الحقول المطلوبة، او يرفع خطأ لو فشل.
    """
    url = "https://api.deepseek.com/v1/chat/completions"

    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": "انت مساعد تحليلات، تلتزم بالحقائق فقط."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.5,
        "max_tokens": 900,
    }

    resp = requests.post(url, headers=headers, json=payload, timeout=60)
    resp.raise_for_status()
    data = resp.json()

    try:
        content = data["choices"][0]["message"]["content"]
    except Exception as e:
        raise RuntimeError(f"رد غير متوقع من DeepSeek: {e}")

    content = content.strip()

    # نحاول نفك JSON
    try:
        parsed = json.loads(content)
        if not isinstance(parsed, dict):
            raise ValueError("JSON مو object")
        return parsed
    except Exception:
        # لو خبص المودل، نخليه يرجع كل النص في حقل واحد
        return {
            "bio": "غير واضح",
            "main_topics": "غير واضح",
            "personality": "غير واضح",
            "hobbies": "غير واضح",
            "security_note": "غير واضح",
            "short_summary": content[:1000],
        }


def format_report(username: str, ai_data: Dict[str, Any]) -> str:
    """
    نحول JSON من DeepSeek إلى رسالة شكلها نفس الأوتبوت اللي تحبه،
    لكن بدون تخمين معلومات شخصية قوية.
    """

    def g(key: str, default: str = "غير واضح") -> str:
        val = ai_data.get(key)
        if not val:
            return default
        return str(val).strip()

    bio = g("bio", "ما كتب بايو او البايو غير واضح.")
    topics = g("main_topics")
    personality = g("personality")
    hobbies = g("hobbies")
    security_note = g("security_note")
    summary = g("short_summary")

    report = f"""الهدف: @{username}
──────────────
📝 البايو:
{bio}

🧵 المواضيع اللي يتكلم عنها كثير:
{topics}

🧠 الانطباع العام عن شخصيته:
{personality}

🎭 جوه واهتماماته:
{hobbies}

🚨 ملاحظات عامة (ألفاظ / عدوانية / أشياء حساسة):
{security_note}

🤖 ملخص الذكاء الاصطناعي:
{summary}

👁‍🗨 انتهى التقرير."""
    return report


def build_full_report(username: str) -> str:
    """
    دالة سنكرونس نجمع فيها كل شي:
    - نقرأ صفحة X عن طريق Jina
    - نرسل النص لـ DeepSeek
    - نجهز التقرير النهائي
    """
    page_text = fetch_x_profile_markdown(username)
    prompt = build_deepseek_prompt(username, page_text)
    ai_data = call_deepseek_api(prompt)
    report = format_report(username, ai_data)
    return report


# =========================================================
# 🧵 هاندلرات التليقرام
# =========================================================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "هلو حبيبي 👋\n\n"
        "ارسل لي يوزر X / تويتر بأي شكل من هذي الطرق:\n"
        "- @username\n"
        "- الرابط كامل: https://x.com/username\n"
        "- او بس username بدون @\n\n"
        "وانا ارجع لك تقرير تحليلي عن الحساب بناء على البايو والتغريدات العامة فقط.\n"
        "الحساب لازم يكون عام، وما نقدر نقرأ شي مخفي او برا الصفحة."
    )
    await update.message.reply_text(text)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return

    raw = update.message.text.strip()

    # نتجاهل الاوامر الثانية
    if raw.startswith("/"):
        await update.message.reply_text("استخدم /start عشان تشوف طريقة الاستخدام 🌝")
        return

    username = extract_username(raw)
    if not username:
        await update.message.reply_text(
            "ما عرفت أطلع اليوزر 😅\n"
            "ارسلها كذا مثلاً:\n"
            "@elonmusk او رابط حسابه على X."
        )
        return

    waiting_msg = await update.message.reply_text(
        f"🔍 قاعد أحلل حساب @{username}...\n"
        "استنى ثواني لين أرجع لك التقرير."
    )

    try:
        # نشغل التحليل في ثريد منفصل عشان ما نعلق البوت
        loop = asyncio.get_running_loop()
        report = await asyncio.to_thread(build_full_report, username)
        await waiting_msg.edit_text(report)
    except Exception as e:
        logger.exception("Error while analyzing account")
        await waiting_msg.edit_text(
            f"❌ صار خطأ أثناء التحليل:\n{e}\n\n"
            "جرب حساب ثاني، أو بعد شوي لو المشكلة من الخدمة الخارجية."
        )


# =========================================================
# 🚀 تشغيل البوت
# =========================================================

def main() -> None:
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", start_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print("✅ Bot is running (Twitter Analyzer v2, no-login)...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
