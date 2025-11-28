import os
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
)

# ============================================================
# 🔐 قراءة المفاتيح من Environment
# ============================================================
BOT_TOKEN = os.getenv("BOT_TOKEN")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

if not BOT_TOKEN:
    print("❌ ERROR: BOT_TOKEN not found!")
if not DEEPSEEK_API_KEY:
    print("❌ ERROR: DEEPSEEK_API_KEY not found!")


# ============================================================
# أوامر البوت
# ============================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🚀 البوت شغال يالغالي!")


# ============================================================
# تشغيل البوت
# ============================================================
def main():
    print("🚀 Starting bot...")

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))

    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
