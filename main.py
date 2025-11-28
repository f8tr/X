import time
import os
import re
import urllib.parse
import html
import asyncio
import json
import requests
from collections import Counter

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    filters,
    Application
)

# ============================================================
# 🔐 قراءة المفاتيح من Environment
# ============================================================
BOT_TOKEN = os.getenv("8590131508:AAEQHi77AEzlaoRpN5LYixPrc7_aOUP5osY")
DEEPSEEK_API_KEY = os.getenv("sk-8215110c094649bfbbe3aaae2842bf65")

# ============================================================
# إعداد Chrome لمسار Render
# ============================================================
CHROME_PATH = "/usr/bin/google-chrome"
USER_DATA = r"/tmp/chrome"

request_queue = asyncio.Queue()
global_driver = None

# ============================================================
# دوال مساعدة
# ============================================================
def clean_text(text):
    if not text:
        return "غير معروف"
    return html.escape(str(text))


def smart_wait(driver, xpath, timeout=5):
    try:
        return WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((By.XPATH, xpath))
        )
    except:
        return None

# ============================================================
# 🔥 DeepSeek AI — تحليل بالذكاء الاصطناعي
# ============================================================
def analyze_with_ai(tweets_list, bio):

    if not tweets_list:
        return "ما لقيت تغريدات كفاية للتحليل."

    content = "\n".join(tweets_list[:30])

    prompt = f"""
سولّف لي عن صاحب الحساب بأسلوب سعودي بدون تنوين.

حلل شخصيته وهواياته واهتماماته ونمطه بالكلام.
اعتمد على البايو وهذا المحتوى:

البايو:
{bio}

أبرز تغريداته:
{content}

ابغى التحليل كنقاط بس:
- شخصيته
- ميوله
- اهتماماته
- نبرة كلامه
- هل هو اجتماعي او لا
- نصيحة عنه
"""

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}"
    }

    data = {
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 450,
        "temperature": 0.2
    }

    try:
        r = requests.post(
            "https://api.deepseek.com/chat/completions",
            headers=headers,
            data=json.dumps(data)
        )
        res = r.json()
        return res["choices"][0]["message"]["content"]
    except Exception as e:
        return f"تحليل الذكاء اصطناعي تعذر: {str(e)}"


# ============================================================
# دوال السحب والتحليل اليدوي من تويتر
# ============================================================
def get_info_brute_force(driver, target):
    info = {
        "loc": "غير معروف",
        "device": "غير معروف",
        "joined": "غير معروف",
        "bio": "لا يوجد",
        "name": target,
        "vpn": False
    }

    driver.get(f"https://twitter.com/{target}")
    time.sleep(3)

    # الاسم والبايو
    try:
        info["name"] = driver.find_element(
            By.XPATH, '//div[@data-testid="UserName"]//span[1]//span[1]'
        ).text

        info["bio"] = clean_text(
            driver.find_element(
                By.XPATH, '//div[@data-testid="UserDescription"]'
            ).text.replace("\n", " ")
        )
    except:
        pass

    # الدخول على /about
    try:
        driver.get(f"https://twitter.com/{target}/about")
        time.sleep(3)

        body = driver.find_element(By.TAG_NAME, "body").text

        # الدولة الرسمية
        if "Account based in" in body:
            m = re.search(r"Account based in\n(.+)", body)
            if m:
                info["loc"] = f"{m.group(1)} (موثق)"

        # الجهاز
        if "Connected via" in body:
            m = re.search(r"Connected via\n(.+)", body)
            if m:
                info["device"] = m.group(1)

        # Joined
        m = re.search(r"Joined\s+([A-Za-z]+\s+\d{4})", body)
        if m:
            info["joined"] = m.group(1)

        # فحص VPN لو فيه علامة "!"
        if "!" in body:
            info["vpn"] = True

    except:
        pass

    return info


def hunt_location_text(driver, username):
    keywords = "الرياض OR جدة OR الدمام OR مكة OR المدينة OR الشرقية OR تبوك OR حائل"

    query = f'from:{username} ({keywords})'

    driver.get(
        f"https://twitter.com/search?q={urllib.parse.quote(query)}&src=typed_query&f=live"
    )
    time.sleep(2)

    try:
        tw = driver.find_element(By.XPATH, '//article//div[@data-testid="tweetText"]')
        t = driver.find_element(By.TAG_NAME, "time")
        txt = tw.text
        dt = t.get_attribute("datetime").split("T")[0]

        return f"قفطناه يقول: '{clean_text(txt[:60])}...'\nبتاريخ: {dt}"

    except:
        return "ما لقيت موقع واضح من سوالفه."


def analyze_friends(driver, username):
    driver.get(f"https://twitter.com/{username}/with_replies")
    time.sleep(3)

    found = []
    ignore = ["twitter", "support", "ads", username.lower()]

    for _ in range(6):
        driver.execute_script("window.scrollBy(0, 2000);")
        time.sleep(1.2)
        try:
            body = driver.find_element(By.TAG_NAME, "body").text
            matches = re.findall(r"(?:Replying to|ردًا على)\s+@(\w+)", body)
            for u in matches:
                if u.lower() not in ignore:
                    found.append(u.lower())
        except:
            pass

    return Counter(found).most_common(5)


def check_bad_words(driver, username):
    bad = ["لعن", "سب", "قذر", "زبالة", "كسم", "واطي", "كلب", "حيوان"]

    query = f'from:{username} ({" OR ".join(bad)})'

    driver.get(
        f"https://twitter.com/search?q={urllib.parse.quote(query)}&src=typed_query&f=live"
    )
    time.sleep(2)

    try:
        tw = driver.find_element(By.XPATH, '//article//div[@data-testid="tweetText"]')
        yr = driver.find_element(By.TAG_NAME, "time").get_attribute("datetime").split("-")[0]

        return f"⚠️ فيه كلام بذي عام {yr}:\n'{clean_text(tw.text[:80])}...'"
    except:
        return "✅ سليم: ما فيه ألفاظ بذيئة."

# ============================================================
# 🌪️ الطابور + العامل الخلفي
# ============================================================
async def process_queue_worker(app: Application):
    global global_driver

    while True:
        chat_id, username = await request_queue.get()

        try:
            # المتصفح
            if global_driver is None:
                opts = Options()
                opts.binary_location = CHROME_PATH
                opts.add_argument("--headless=new")
                opts.add_argument("--no-sandbox")
                opts.add_argument("--disable-dev-shm-usage")
                opts.add_argument("--window-size=1920,1080")

                service = Service(ChromeDriverManager().install())
                global_driver = webdriver.Chrome(service=service, options=opts)

            # سحب معلومات الحساب
            info = get_info_brute_force(global_driver, username)
            location = hunt_location_text(global_driver, username)
            friends = analyze_friends(global_driver, username)
            badwords = check_bad_words(global_driver, username)

            # سحب التغريدات للتحليل
            global_driver.get(f"https://twitter.com/{username}")
            time.sleep(2)

            tweets = []
            for _ in range(3):
                global_driver.execute_script("window.scrollBy(0,1500)")
                time.sleep(1)
                for a in global_driver.find_elements(By.TAG_NAME, "article"):
                    tweets.append(a.text)

            ai_summary = analyze_with_ai(tweets, info["bio"])

            # ============================================================
            # 📝 بناء التقرير النهائي
            # ============================================================
            msg = f"""
الهدف: {username}
──────────────
📝 البايو:
{info['bio']}

📍 الدولة (الرسمية): {info['loc']}
📱 يدخل من: {info['device']}
📅 موجود من: {info['joined']}
"""

            if info["vpn"]:
                msg += "🛡 احتمال يستخدم VPN او بروكسي\n"

            msg += f"""
──────────────
📍 موقعه (من سوالفه):
{location}
──────────────
👥 اخوياه:
"""

            if friends:
                for i, (u, c) in enumerate(friends, 1):
                    msg += f"{i}) @{u} (تكرر {c} مره)\n"
            else:
                msg += "ما ظهر له تفاعل واضح.\n"

            msg += f"""
──────────────
🚨 الفحص الامني:
{badwords}
──────────────
🤖 تحليل الذكاء الاصطناعي:
{ai_summary}
──────────────
👁‍🗨 انتهى التقرير.
"""

            await app.bot.send_message(chat_id, msg)

        except Exception as e:
            await app.bot.send_message(chat_id, f"❌ خطأ: {str(e)}")

        finally:
            request_queue.task_done()

# ============================================================
# رد /start + استقبال اليوزر
# ============================================================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg = update.effective_user.username
    name = f"@{tg}" if tg else update.effective_user.first_name

    await update.message.reply_text(
        f"👋 هلا والله يا {name}!\n\nهات اليوزر و اسرد لك تفاصيله 🔍🔥"
    )


async def handle_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = clean_text(update.message.text.replace("@", "").strip())
    chat_id = update.effective_chat.id

    q = request_queue.qsize()

    if q > 0:
        await update.message.reply_text(
            f"انتظر لين يجي دورك ✋\nقدامك {q}\nاقضي وقتك بالاستغفار ❤️"
        )
    else:
        await update.message.reply_text(
            f"ثواني بس يا {user}…\n(قاعد انبش في تاريخه) 🔎👀"
        )

    await request_queue.put((chat_id, user))


# ============================================================
# 🚀 تشغيل البوت
# ============================================================
async def post_init(app: Application):
    asyncio.create_task(process_queue_worker(app))


if __name__ == "__main__":
    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_username))
    app.run_polling()
