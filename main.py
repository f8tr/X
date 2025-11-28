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
    Application,
)

# ============================================================
# 🔐 قراءة المفاتيح من Environment
# ============================================================
BOT_TOKEN = os.getenv("BOT_TOKEN")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

# مسار كروم (لينكس / استضافة) – تقدر تعدله حسب بيئتك
CHROME_PATH = os.getenv("CHROME_PATH", "/usr/bin/google-chrome")
USER_DATA = "/tmp/ChromeBot"

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
# 🧠 تحليل عن طريق DeepSeek
# ============================================================
def analyze_with_deepseek(tweets_list, bio):
    if not tweets_list:
        return "ما لقيت تغريدات كفاية اقدر احلل منها."

    full_text = "\n".join(tweets_list[:40])

    prompt = f"""
انت محلل اجتماعي و نفسي سعودي، عندك خبرة طويلة في قراءة الشخصيات من تغريداتهم.

ابي منك تحليل للمستخدم هذا باللهجة السعودية العامية بدون تنوين:
- لا تكتب سطر واحد طويل، اكتب كنقاط مرتبة.
- خلك واقعي، لا تطبيل ولا جلد زايد.

المعلومات:

البايو:
\"\"\"{bio}\"\"\"

بعض تغريداته:
\"\"\"{full_text}\"\"\"

ابي منك الرد بهالتنسيق:

1) شخصيته:
- وصف عام: هادي، عصبي، مهايطي، مثقف، نفسية.. الخ
- اسلوبه بالكلام: رسمي، شوارعي، مزوح، ثقيل دم.. الخ

2) اهتماماته:
- اهتمامات واضحة: تقنية، امن سيبراني، كورة، انمي، سيارات، تداول.. الخ
- هل يغرد عن يومياته ولا بس ريتويت؟

3) توقع عن واقعه:
- ممكن يكون يدرس ايش او يشتغل في اي مجال؟
- شكل نمط حياته: سهران، موظف، طالب جامعة، عاطل.. الخ

4) ملاحظات وتحذيرات:
- اذا فيه عدوانية، تنمر، تشخيص، مشاكل نفسية.. اذكرها
- اذا شخص متزن ورايق، اذكرها بعد

اكتب كل شي بالعربي وبعامية سعودية خفيفة، بدون انقليزي الا اذا اضطرّيت.
"""

    try:
        headers = {
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": "deepseek-chat",
            "messages": [{"role": "user", "content": prompt}],
        }
        r = requests.post(
            "https://api.deepseek.com/chat/completions",
            headers=headers,
            data=json.dumps(payload),
            timeout=60,
        )
        data = r.json()
        return data["choices"][0]["message"]["content"]
    except Exception as e:
        return f"تعذر التحليل عن طريق الذكاء الاصطناعي، ممكن يكون فيه مشكلة بالمفتاح او الاتصال."

# ============================================================
# 🐦 دوال تويتر – نفس لوجيكك القديم مع تحسينات بسيطة
# ============================================================
def get_info_brute_force(driver, target):
    info = {
        "loc": "غير معروف",
        "device": "غير معروف",
        "joined": "غير معروف",
        "bio": "لا يوجد",
        "name": target,
    }

    driver.get(f"https://twitter.com/{target}")
    time.sleep(3)

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

    try:
        body_text = driver.find_element(By.TAG_NAME, "body").text
        join_match = re.search(
            r"(Joined|انضم في)\s+([A-Za-z]+\s+\d{4})", body_text
        )
        if join_match:
            info["joined"] = join_match.group(2)

        try:
            loc = driver.find_element(
                By.XPATH, '//span[@data-testid="UserLocation"]'
            ).text
            if loc:
                info["loc"] = clean_text(loc)
        except:
            pass
    except:
        pass

    try:
        driver.get(f"https://twitter.com/{target}/about")
        time.sleep(3)
        dialog_text = driver.find_element(By.TAG_NAME, "body").text

        if "Account based in" in dialog_text:
            match = re.search(r"Account based in\n(.+)", dialog_text)
            if match:
                info["loc"] = f"{match.group(1)} (موثق)"

        if "Connected via" in dialog_text:
            match = re.search(r"Connected via\n(.+)", dialog_text)
            if match:
                info["device"] = match.group(1)
    except:
        pass

    return info

def analyze_friends_strict(driver, target_user):
    driver.get(f"https://twitter.com/{target_user}/with_replies")
    time.sleep(4)
    target_clean = target_user.lower()
    valid_contacts = []
    ignore_list = ["twitter", "support", "ads", "promote", "business", target_clean]

    try:
        for _ in range(6):
            driver.execute_script("window.scrollBy(0, 2500);")
            time.sleep(1.5)
            try:
                body_text = driver.find_element(By.TAG_NAME, "body").text
                matches = re.findall(
                    r"(?:Replying to|ردًا على)\s+@([\w_]+)",
                    body_text,
                    re.IGNORECASE,
                )
                for user in matches:
                    u_lower = user.lower()
                    if u_lower not in ignore_list and len(u_lower) > 2:
                        valid_contacts.append(u_lower)
            except:
                pass
    except:
        pass

    return Counter(valid_contacts).most_common(5)

def analyze_identity(driver, username, display_name):
    report_lines = []
    report_lines.append(f"👤 <b>الاسم الظاهر:</b> {clean_text(display_name)}")

    tribe_match = re.findall(r"\bال[ا-ي]+ي\b", display_name)
    tribe_potential = tribe_match[0] if tribe_match else None

    query = f'from:{username} ("اسمي" OR "انا" OR "قبيلتي" OR "ربعي" OR "عزوتي" OR "ونعم")'
    driver.get(
        f"https://twitter.com/search?q={urllib.parse.quote(query)}&src=typed_query&f=live"
    )
    time.sleep(2)
    found_proof = False

    try:
        articles = driver.find_elements(By.TAG_NAME, "article")
        for art in articles[:3]:
            text = art.text
            if tribe_potential and tribe_potential in text:
                report_lines.append(
                    f"✅ <b>القبيلة مؤكدة:</b> {tribe_potential}\n   الدليل: تغريدة يقول <i>'{clean_text(text[:40])}...'</i>"
                )
                found_proof = True
                break
            if "اسمي" in text:
                report_lines.append(
                    f"🔎 <b>اعتراف بالاسم:</b>\n   <i>'{clean_text(text[:50])}...'</i>"
                )
                found_proof = True
                break
    except:
        pass

    if not found_proof and tribe_potential:
        report_lines.append(
            f"⚠️ <b>توقع القبيلة:</b> {tribe_potential} (مذكورة بالاسم الظاهر فقط)"
        )
    elif not found_proof:
        report_lines.append("🔒 <b>الهوية الحقيقية:</b> ما صرح باسمه الواضح.")

    return "\n".join(report_lines)

def hunt_birthday_proof(driver, username):
    query1 = (
        f'from:{username} ("عيد ميلادي" OR "يوم ميلادي" OR "كبرت سنة" OR "Birthday")'
    )
    driver.get(
        f"https://twitter.com/search?q={urllib.parse.quote(query1)}&src=typed_query&f=live"
    )
    time.sleep(2)

    try:
        tweet = driver.find_element(
            By.XPATH, '//article//div[@data-testid="tweetText"]'
        )
        time_el = driver.find_element(By.TAG_NAME, "time")
        if tweet:
            t_date = time_el.get_attribute("datetime").split("T")[0]
            return f"🎂 <b>يوم ميلاده (بالدليل):</b>\n✅ لقيناه!\nالتاريخ: {t_date}\nالدليل: <i>\"{clean_text(tweet.text[:60])}...\"</i>"
    except:
        pass

    query2 = (
        f'to:{username} ("كل عام وانت بخير" OR "عيد ميلاد سعيد" OR "Happy Birthday")'
    )
    driver.get(
        f"https://twitter.com/search?q={urllib.parse.quote(query2)}&src=typed_query&f=live"
    )
    time.sleep(2)

    try:
        times = driver.find_elements(By.TAG_NAME, "time")
        dates = [t.get_attribute("datetime").split("T")[0][5:] for t in times[:10]]
        if dates:
            common = Counter(dates).most_common(1)[0][0]
            return f"🎂 <b>يوم ميلاده (توقع قوي):</b>\nيوافق تقريباً: {common} (من تبريكات الناس)"
    except:
        pass

    return "🎂 <b>يوم ميلاده (بالدليل):</b> للحين ما لقينا شي واضح."

def hunt_location_text(driver, username):
    cities = "الرياض OR جدة OR الدمام OR مكة OR المدينة OR القصيم OR أبها OR تبوك OR حائل OR جازان OR الطائف OR الخبر OR الشرقية OR الكويت OR دبي"
    query = f"from:{username} ({cities})"
    driver.get(
        f"https://twitter.com/search?q={urllib.parse.quote(query)}&src=typed_query&f=live"
    )
    time.sleep(2)

    try:
        tweet = driver.find_element(
            By.XPATH, '//article//div[@data-testid="tweetText"]'
        )
        time_el = driver.find_element(By.TAG_NAME, "time")
        t_text = tweet.text
        t_date = time_el.get_attribute("datetime").split("T")[0]
        return f"📍 <b>موقعه (من سوالفه):</b>\nقفطناه يقول: <i>\"{clean_text(t_text[:80])}...\"</i>\nبتاريخ: {t_date}"
    except:
        pass

    return "📍 <b>موقعه (من سوالفه):</b> ما فيه شي واضح عن مكان سكنه."

def analyze_hobbies_structured(tweets_list):
    text = " ".join(tweets_list).lower()
    sections = []

    # قيمز / جيمر
    if re.search(
        r"(pc|بي سي|تجميعة|كرت شاشة|steam|overwatch|valorant|cod|فيفا|قيمز|لعب|elden|قراند|gta)",
        text,
    ):
        games = []
        if "overwatch" in text:
            games.append("Overwatch")
        if "valorant" in text:
            games.append("Valorant")
        if "fifa" in text:
            games.append("FIFA")
        if "cod" in text:
            games.append("Call of Duty")
        if "elden" in text:
            games.append("Elden Ring")
        if "gta" in text or "قراند" in text:
            games.append("GTA / قراند")

        desc = "🎮 <b>جيمر (PC Master Race):</b>\nواضح انه راعي قطع وتجميعات واهتمامه بالالعاب."
        if games:
            desc += f"\nالألعاب اللي ظهرت بتغريداته: {', '.join(games)}."
        sections.append(desc)

    # كورة
    if re.search(r"(هلال|نصر|اتحاد|اهلي|أهلي|دوري|مباراة|هدف|messi|ronaldo)", text):
        club = "متابع عام"
        if "هلال" in text:
            club = "الهلال 💙"
        elif "نصر" in text:
            club = "النصر 💛"
        elif "اتحاد" in text:
            club = "الاتحاد 🐆"
        elif "اهلي" in text or "أهلي" in text:
            club = "الأهلي 💚"

        sections.append(
            f"⚽ <b>الكورة:</b>\nيشجع ({club})، ويبين انه يتابع المباريات والاخبار الرياضية."
        )

    # تقنية
    if re.search(
        r"(linux|لينكس|ubuntu|arch|manjaro|python|بايثون|code|coding|cyber|security|hack|هكر|برمجة|أمن|سيرفر|kali)",
        text,
    ):
        sections.append(
            "💻 <b>تقني / جييك:</b>\nمهتم بالتقنية، وبرمجة او امن سيبراني او لينكس (kali / arch / whonix)."
        )

    # انمي / ترفيه
    if re.search(
        r"(anime|انمي|one piece|ون بيس|naruto|ناروتو|attack on titan|netflix|فلم|فيلم|مسلسل)",
        text,
    ):
        sections.append(
            "📺 <b>ترفيه:</b>\nيتابع انمي/مسلسلات وافلام، واضح انه راعي سهر ونتفلكس."
        )

    # سيارات
    if re.search(
        r"(موتر|سيارة|سياره|تفحيط|درفت|تيربو|تزويد|ميكانيكا|بنزين)",
        text,
    ):
        sections.append(
            "🚗 <b>سيارات:</b>\nيحب المواتر والتزويد والسوالف اللي حولها، ممكن يكون راعي تفحيط او تعديل."
        )

    if not sections:
        return "🤷‍♂️ <b>هواياته مو واضحة:</b> ما يوضح كثير عن جوه وهواياته من تغريداته."

    return "\n".join(sections)

def analyze_personality(tweets_list):
    if not tweets_list:
        return "ما لقيت تغريدات كفاية اقدر احكم منها."

    text = " ".join(tweets_list).lower()

    aggro = len(
        re.findall(
            r"(غبي|تافه|مرض|صياح|بزر|كريه|ياخي|تخلف|قذر|يا حيوان|يا كلب|زق)",
            text,
        )
    )
    emo = len(
        re.findall(
            r"(احبكم|حب|قلب|قلبي|سعيد|مبسوط|شاكر|شكرا|شكراً|جميل|جمال|روعة|حلوين)",
            text,
        )
    )
    ego = len(
        re.findall(
            r"\b(انا|أنا|عن نفسي|رأيي|شخصياً|تجربتي|me|my|i )\b",
            text,
        )
    )
    intellect = len(
        re.findall(
            r"(تحليل|منطق|واقعي|السبب|مستقبل|مشروع|تطوير|تقنية|بحث)",
            text,
        )
    )

    traits = []

    if aggro > emo:
        traits.append(
            "⚠️ <b>راعي مشاكل شوي:</b> اسلوبه فيه حدة وتنمر احياناً، يحب يفصفص الناس وما يجامل كثير."
        )
    elif emo > aggro:
        traits.append(
            "💖 <b>راعي مشاعر:</b> يميل للكلام اللطيف والدعم، جوه اخف من الناس الحادة."
        )

    if ego > 4:
        traits.append(
            "😎 <b>واثق من نفسه:</b> يتكلم عن نفسه وتجربته كثير، واضح مهتم براحته ونظرته للامور."
        )

    if intellect > 3:
        traits.append(
            "🧠 <b>مفكر:</b> عنده ميل للتحليل والمنطق، ما ياخذ الاشياء بسسطحية."
        )

    if not traits:
        traits.append(
            "⚖️ <b>شخصية متزنة:</b> تغريداته هادية غالباً، ما فيها تطرف واضح لا بالمشاكل ولا بالعواطف."
        )

    return "\n".join(traits)

def check_bad_words(driver, username):
    bad_words = [
        "لعن",
        "كسم",
        "كس",
        "قذر",
        "زبالة",
        "منحط",
        "كلب",
        "حيوان",
        "واطي",
        "زق",
    ]
    search_query = " OR ".join(bad_words)
    query = f"from:{username} ({search_query})"

    driver.get(
        f"https://twitter.com/search?q={urllib.parse.quote(query)}&src=typed_query&f=live"
    )
    time.sleep(2)

    try:
        tweet = driver.find_element(
            By.XPATH, '//article//div[@data-testid="tweetText"]'
        )
        time_el = driver.find_element(By.TAG_NAME, "time")

        if tweet:
            t_text = tweet.text
            t_year = time_el.get_attribute("datetime").split("-")[0]
            clean_t = clean_text(t_text[:100])

            return (
                "🚨 <b>الفحص الأمني (الألفاظ والعدوانية):</b>\n"
                "⚠️ رصدت تغريدة او اكثر فيها الفاظ او هجوم واضح.\n"
                f"<b>مثال (سنة {t_year}):</b>\n<i>\"{clean_t}...\"</i>"
            )
    except:
        pass

    return "✅ <b>الفحص الأمني (الألفاظ والعدوانية):</b>\nواضح انه ما يستخدم الفاظ بذيئة كثيرة، سجله نظيف نسبياً."

# ============================================================
# 👷‍♂️ العامل الخلفي (الطابور)
# ============================================================
async def process_queue_worker(app: Application):
    global global_driver
    print("🚀 Background Worker Started...")

    while True:
        chat_id, user_input = await request_queue.get()

        try:
            # تشغيل كروم مرة وحدة
            if global_driver is None:
                # قتل اي كروم قديم
                os.system("pkill chrome || true")
                time.sleep(1)

                opts = Options()
                opts.binary_location = CHROME_PATH
                opts.add_argument(f"--user-data-dir={USER_DATA}")
                opts.add_argument("--profile-directory=Default")
                opts.add_argument("--headless=new")
                opts.add_argument("--no-sandbox")
                opts.add_argument("--disable-dev-shm-usage")
                opts.add_argument("--window-size=1920,1080")

                service = Service(ChromeDriverManager().install())
                global_driver = webdriver.Chrome(service=service, options=opts)
                global_driver.execute_script(
                    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
                )

            await app.bot.send_message(
                chat_id=chat_id,
                text=f"ثواني بس @{user_input} ، قاعد انبش في تاريخه 👀",
                parse_mode="HTML",
            )

            # ===== تنفيذ التحليل =====
            info = get_info_brute_force(global_driver, user_input)
            identity = analyze_identity(global_driver, user_input, info["name"])
            birthday = hunt_birthday_proof(global_driver, user_input)
            location = hunt_location_text(global_driver, user_input)
            friends = analyze_friends_strict(global_driver, user_input)
            security = check_bad_words(global_driver, user_input)

            # سحب تغريدات للتحليل
            global_driver.get(f"https://twitter.com/{user_input}")
            time.sleep(2)
            tweets = []

            try:
                for _ in range(6):
                    global_driver.execute_script("window.scrollBy(0, 2000);")
                    time.sleep(1)
                    arts = global_driver.find_elements(By.TAG_NAME, "article")
                    for a in arts:
                        txt = a.text.strip()
                        if txt and txt not in tweets:
                            tweets.append(txt)
            except:
                pass

            personality = analyze_personality(tweets)
            hobbies = analyze_hobbies_structured(tweets)
            ai_summary = analyze_with_deepseek(tweets, info["bio"])

            # ===== بناء الاوتبوت =====
            msg = f"""الهدف: <code>@{user_input}</code>
──────────────
{identity}
──────────────
📝 <b>البايو:</b>
{info['bio']}

📍 <b>الدولة (الرسمية):</b> {info['loc']}
📱 <b>يدخل من:</b> {info['device']}
📅 <b>موجود من:</b> {info['joined']}
──────────────
{birthday}
──────────────
{location}
──────────────
👥 <b>أخوياه (أكثر ناس يرد عليهم):</b>
"""

            if friends:
                for i, (u, c) in enumerate(friends, 1):
                    msg += f"{i}. <code>@{u}</code> (تكرر {c} مرة)\n"
            else:
                msg += "ما فيه اسم معين يتكرر كثير.\n"

            msg += f"""
──────────────
🧠 <b>وش وضعه؟ (تحليل شخصيته):</b>
{personality}
──────────────
🎭 <b>وش جوّه؟ (تحليل الهوايات):</b>
{hobbies}
──────────────
🚨 <b>الفحص الأمني (الولاء والماضي):</b>
{security}
──────────────
🤖 <b>ملخص الذكاء الاصطناعي (نظرة عامة عليه):</b>
{ai_summary}

👁‍🗨 <b>انتهى التقرير.</b>
"""

            await app.bot.send_message(
                chat_id=chat_id,
                text=msg,
                parse_mode="HTML",
            )

        except Exception as e:
            await app.bot.send_message(
                chat_id=chat_id, text=f"❌ صار خطأ فني داخل التحليل: {str(e)}"
            )
            try:
                if global_driver:
                    global_driver.quit()
                    global_driver = None
            except:
                pass
        finally:
            request_queue.task_done()

# ============================================================
# 🧵 تليجرام – أوامر و رسائل
# ============================================================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    name = user.first_name or "حبيبي"

    await update.message.reply_text(
        f"👋 هلا والله {name}!\n\n"
        "هات اليوزر حق تويتر (بدون روابط)، وانا اسرد لك تفاصيله تقرير كامل.",
        parse_mode="HTML",
    )

async def handle_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_txt = (update.message.text or "").strip()

    # تنظيف اليوزر
    user_txt = user_txt.replace("https://", "").replace("http://", "")
    user_txt = user_txt.replace("www.", "")
    user_txt = user_txt.replace("x.com/", "").replace("twitter.com/", "")
    user_txt = user_txt.replace("@", "").split("/")[0].strip()

    if not user_txt or " " in user_txt:
        await update.message.reply_text(
            "اكتب لي يوزر واحد بس، بدون مسافات وبدون روابط كاملة 🙏",
            parse_mode="HTML",
        )
        return

    chat_id = update.effective_chat.id
    q_size = request_queue.qsize()

    if q_size > 0:
        await update.message.reply_text(
            f"انتظر لين يجي دورك 🙏\n"
            f"قدامك <b>{q_size}</b> في الطابور.\n"
            "اقضي وقتك بالاستغفار ❤️",
            parse_mode="HTML",
        )
    else:
        await update.message.reply_text(
            f"ثواني بس @{user_txt} .. قاعد انبش في تاريخه 👀",
            parse_mode="HTML",
        )

    await request_queue.put((chat_id, user_txt))

async def post_init(application: Application):
    asyncio.create_task(process_queue_worker(application))

# ============================================================
# 🚀 تشغيل البوت
# ============================================================
if __name__ == "__main__":
    print("🤖 Bot is running (Twitter Analyzer)…")

    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_username))

    app.run_polling()
