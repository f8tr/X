import os
import re
import html
import json
import asyncio
import subprocess
from collections import Counter
from datetime import datetime

import requests
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
    Application,
)

# =========================================
# 🔐 قرائة المتغيرات من Environment
# =========================================
BOT_TOKEN = os.getenv("BOT_TOKEN")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

request_queue = asyncio.Queue()


# =========================================
# 🔧 دوال مساعدة بسيطة
# =========================================
def clean_text(text):
    if not text:
        return "غير معروف"
    return html.escape(str(text))


def run_snscrape(args):
    """
    تشغيل snscrape عن طريق subprocess
    """
    try:
        result = subprocess.run(
            ["snscrape", "--jsonl"] + args,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return []
        return result.stdout.splitlines()
    except Exception:
        return []


# =========================================
# 🐦 سحب بيانات حساب تويتر بدون تسجيل دخول
# =========================================
def get_user_profile(username):
    """
    يرجع معلومات اساسية عن المستخدم باستخدام snscrape twitter-user
    """
    lines = run_snscrape([f"twitter-user {username}"])
    if not lines:
        return None, []

    # اول سطر فيه تغريدة + بيانات يوزر
    first = json.loads(lines[0])
    user = first.get("user", first)

    bio = user.get("description") or "لا يوجد"
    loc = user.get("location") or "غير معروف"
    created = user.get("created") or user.get("created_at")
    if created:
        try:
            # snscrape يرجع ISO datetime
            dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
            joined = dt.strftime("%B %Y")
        except Exception:
            joined = str(created)
    else:
        joined = "غير معروف"

    profile = {
        "name": user.get("displayname") or username,
        "username": user.get("username") or username,
        "bio": bio,
        "loc": loc,
        "joined": joined,
        "followers": user.get("followersCount", 0),
        "friends": user.get("friendsCount", 0),
    }

    # نجمع مجموعة تغريدات من نفس الخرج
    tweets = []
    for ln in lines[:120]:  # 120 تغريدة تكفي للتحليل
        try:
            t = json.loads(ln)
            content = t.get("content") or t.get("renderedContent") or ""
            content = content.strip()
            if not content:
                continue
            date_str = t.get("date") or t.get("created")
            tweets.append(
                {
                    "text": content,
                    "date": date_str,
                    "raw": t,
                }
            )
        except Exception:
            continue

    return profile, tweets


# =========================================
# 🎂 استخراج يوم الميلاد من التغريدات
# =========================================
def detect_birthday_from_tweets(tweets):
    keywords = [
        "عيد ميلادي",
        "يوم ميلادي",
        "كبرت سنة",
        "عيد ميلاد",
        "birthday",
        "my birthday",
    ]

    for tw in tweets:
        txt = tw["text"]
        if any(kw.lower() in txt.lower() for kw in keywords):
            date = tw["date"]
            if date:
                try:
                    dt = datetime.fromisoformat(date.replace("Z", "+00:00"))
                    d_str = dt.strftime("%Y-%m-%d")
                except Exception:
                    d_str = str(date)
            else:
                d_str = "غير معروف"

            snippet = txt[:80].replace("\n", " ")
            return (
                f"🎂 <b>يوم ميلاده (بالدليل):</b>\n"
                f"✅ لقيناه!\n"
                f"التاريخ: {d_str}\n"
                f'الدليل تغريدة يقول: "<i>{html.escape(snippet)}...</i>"'
            )

    # لو ما لقينا شي واضح
    return "🎂 <b>يوم ميلاده (بالدليل):</b> للحين ما لقينا شي واضح من تغريداته."


# =========================================
# 📍 تحديد الموقع من سوالفه
# =========================================
def detect_location_from_tweets(tweets):
    cities = [
        "الرياض",
        "جدة",
        "جده",
        "الدمام",
        "مكة",
        "مكه",
        "المدينة",
        "المدينه",
        "الشرقية",
        "الشرقيه",
        "القصيم",
        "أبها",
        "ابها",
        "تبوك",
        "حائل",
        "جازان",
        "الخبر",
        "الكويت",
        "دبي",
    ]

    for tw in tweets:
        txt = tw["text"]
        for city in cities:
            if city in txt:
                date = tw["date"]
                if date:
                    try:
                        dt = datetime.fromisoformat(date.replace("Z", "+00:00"))
                        d_str = dt.strftime("%Y-%m-%d")
                    except Exception:
                        d_str = str(date)
                else:
                    d_str = "غير معروف"
                snippet = txt[:100].replace("\n", " ")
                return (
                    "📍 <b>موقعه (من سوالفه):</b>\n"
                    f'قفطناه يقول: "<i>{html.escape(snippet)}...</i>"\n'
                    f"بتاريخ: {d_str}"
                )

    return "📍 <b>موقعه (من سوالفه):</b> ما وضح من سوالفه وين ساكن بالضبط."


# =========================================
# 👥 اخوياه (اكثر ناس يرد عليهم / يذكرهم)
# =========================================
def detect_friends_from_tweets(tweets):
    mentions_counter = Counter()

    for tw in tweets:
        raw = tw["raw"]
        mentioned = raw.get("mentionedUsers") or []
        for m in mentioned:
            uname = m.get("username")
            if uname:
                mentions_counter[uname.lower()] += 1

        # احتياط بالـ regex
        for m in re.findall(r"@([A-Za-z0-9_]+)", tw["text"]):
            mentions_counter[m.lower()] += 1

    # استبعاد بعض الاشياء لو حبيت
    ignore = {"twitter", "support", "x", "elonmusk"}
    for ig in ignore:
        if ig in mentions_counter:
            mentions_counter.pop(ig, None)

    top = mentions_counter.most_common(5)
    return top


# =========================================
# 🧠 تحليل الشخصية (Rule-Based)
# =========================================
def analyze_personality_rule_based(tweets):
    if not tweets:
        return "ما لقيت تغريدات كفاية اقدر احكم منها."

    text = " ".join(t["text"] for t in tweets).lower()

    aggro = len(
        re.findall(
            r"(غبي|تافه|مرض|صياح|بزر|كريه|ياخي|تخلف|قذر|يا حيوان|يا كلب|زق|تهديد|حرب)",
            text,
        )
    )
    emo = len(
        re.findall(
            r"(احبكم|حب|قلب|قلبي|سعيد|مبسوط|شاكر|شكرا|جميل|جمال|روعة|حلوين|لطيف)",
            text,
        )
    )
    ego = len(
        re.findall(r"\b(انا|أنا|عن نفسي|رأيي|شخصياً|تجربتي|me|my|i )\b", text)
    )
    intellect = len(
        re.findall(r"(تحليل|منطق|واقعي|السبب|مستقبل|مشروع|تطوير|تقنية|بحث)", text)
    )

    traits = []

    if aggro > emo:
        traits.append(
            "⚠️ <b>يميل للحدة شوي:</b> اسلوبه فيه نبرة هجوم او تنمر ببعض التغريدات."
        )
    elif emo > aggro:
        traits.append(
            "💖 <b>راعي مشاعر:</b> يميل للكلام اللطيف والدعم اكثر من الصدام."
        )

    if ego > 4:
        traits.append(
            "😎 <b>واثق من نفسه:</b> يتكلم عن نفسه وتجربته وآراءه بشكل واضح ومتكرر."
        )

    if intellect > 3:
        traits.append(
            "🧠 <b>مفكر:</b> ماياخذ الامور بسطحية، يحاول يحلل ويتفلسف على الواقع والاحداث."
        )

    if not traits:
        traits.append(
            "⚖️ <b>شخصية متزنة:</b> تغريداته عادية غالباً، لا هو راعي مشاكل ولا مبالغ بالعاطفة."
        )

    return "\n".join(traits)


# =========================================
# 🎭 تحليل الهوايات (Rule-Based)
# =========================================
def analyze_hobbies_rule_based(tweets):
    if not tweets:
        return "🤷‍♂️ <b>هواياته مو واضحة:</b> ما في محتوى كافي عن جوه."

    text = " ".join(t["text"] for t in tweets).lower()
    sections = []

    # قيمز
    if re.search(
        r"(pc|بي سي|تجميعة|كرت شاشة|steam|overwatch|valorant|cod|فيفا|قيمز|لعب|elden|قراند|gta|fortnite|فورتنايت)",
        text,
    ):
        games = []
        if "overwatch" in text:
            games.append("Overwatch")
        if "valorant" in text:
            games.append("Valorant")
        if "fifa" in text:
            games.append("FIFA")
        if "elden" in text:
            games.append("Elden Ring")
        if "gta" in text or "قراند" in text:
            games.append("GTA / قراند")
        if "fortnite" in text or "فورتنايت" in text:
            games.append("Fortnite")

        desc = "🎮 <b>جيمر (غالباً PC):</b>\nواضح يحب القيمز ومواتر البي سي والقطع."
        if games:
            desc += f"\nالألعاب اللي بينت من تغريداته: {', '.join(games)}."
        sections.append(desc)

    # كورة
    if re.search(r"(هلال|نصر|اتحاد|اهلي|أهلي|دوري|مباراة|هدف|messi|ronaldo)", text):
        club = "متابع كورة عام"
        if "هلال" in text:
            club = "الهلال 💙"
        elif "نصر" in text:
            club = "النصر 💛"
        elif "اتحاد" in text:
            club = "الاتحاد 🐆"
        elif "اهلي" in text or "أهلي" in text:
            club = "الاهلي 💚"

        sections.append(
            f"⚽ <b>الكورة:</b>\nشكله يشجع ({club}) ويتابع المباريات ونتايج الدوريات."
        )

    # تقنية / امن سيبراني
    if re.search(
        r"(linux|لينكس|ubuntu|arch|manjaro|kali|whonix|python|بايثون|code|coding|cyber|security|hack|هكر|برمجة|أمن|سيرفر)",
        text,
    ):
        sections.append(
            "💻 <b>تقني / امن سيبراني:</b>\nواضح مهتم بالتقنية، لينكس، او امن المعلومات والبرمجة."
        )

    # انمي / ترفيه
    if re.search(
        r"(anime|انمي|one piece|ون بيس|naruto|ناروتو|attack on titan|aot|netflix|نتفلكس|فلم|فيلم|مسلسل)",
        text,
    ):
        sections.append(
            "📺 <b>ترفيه:</b>\nيتابع انمي او مسلسلات وافلام، جوه سهر ونتفلكس غالباً."
        )

    # سيارات
    if re.search(
        r"(موتر|سيارة|سياره|تفحيط|درفت|تيربو|تزويد|ميكانيكا|بنزين|سرعة)",
        text,
    ):
        sections.append(
            "🚗 <b>مواتر وسيارات:</b>\nعنده اهتمام بالسيارات، التزويد او التفحيط او السواقه عموماً."
        )

    if not sections:
        return "🤷‍♂️ <b>هواياته مو واضحة:</b> محتواه ما يعطينا صورة واضحة عن جوه."

    return "\n".join(sections)


# =========================================
# 🚨 الفحص الأمني (الفاظ / عدوانية)
# =========================================
def security_check(tweets):
    bad_words = [
        "لعن",
        "كسم",
        "كس ",
        "قذر",
        "زبالة",
        "زبااله",
        "منحط",
        "كلب",
        "حيوان",
        "واطي",
        "زق",
        "قحبة",
        "قحبه",
        "يا عاهره",
    ]

    for tw in tweets:
        txt = tw["text"]
        if any(bw in txt for bw in bad_words):
            date = tw["date"]
            if date:
                try:
                    dt = datetime.fromisoformat(date.replace("Z", "+00:00"))
                    year = dt.year
                except Exception:
                    year = date
            else:
                year = "غير معروف"
            snippet = txt[:100].replace("\n", " ")
            return (
                "🚨 <b>الفحص الأمني (الولاء والماضي):</b>\n"
                "⚠️ فيه تغريدات فيها الفاظ او نبرة عدوانية.\n"
                f"<b>مثال (سنة {year}):</b>\n<i>\"{html.escape(snippet)}...\"</i>"
            )

    return "✅ <b>الفحص الأمني (الولاء والماضي):</b>\nما ظهر عندي شي خطير من ناحية الفاظ او عدوانية واضحة."


# =========================================
# 🤖 DeepSeek – ملخص AI
# =========================================
def deepseek_summary(profile, tweets, personality, hobbies, security_txt):
    # ناخذ نص مختصر نرسله لـ AI
    joined_tweets = "\n".join(t["text"] for t in tweets[:40])

    prompt = f"""
انت محلل اجتماعي ونفسي سعودي، ابيك تحلل صاحب هذا الحساب وتحط رايك بشكل مرتب وعامي، 
بدون تنوين وبلهجة سعودية خفيفة بس تبقى محترم ومفهوم.

معلومات الحساب:
الاسم: {profile['name']}
اليوزر: @{profile['username']}
البايو: {profile['bio']}
الموقع الرسمي: {profile['loc']}
تاريخ انشاء الحساب: {profile['joined']}
المتابعين: {profile['followers']}
الي يتابعهم: {profile['friends']}

تحليل الشخصية (من عندي كقواعد جاهزة):
{personality}

تحليل الهوايات (من عندي):
{hobbies}

الفحص الأمني:
{security_txt}

بعض من تغريداته:
\"\"\" 
{joined_tweets}
\"\"\"

ابي منك ترد لي بنقاط مختصرة توضح:
- نظرة عامة عن الشخص: رايق، متشنج، نرجسي، منطقي.. الخ
- جوه العام: سوداوي، ايجابي، ساخر.. الخ
- كيف ممكن يتعامل مع الناس (اونلاين): محترم، هجومي، يستفز.. الخ
- اذا في شي ملفت او تحذير (بدون مبالغة او قذف)

لا تعيد نفس الكلام اللي فوق، عطنا خلاصتك انت.
استخدم عربي فقط.
"""

    try:
        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": "deepseek-chat",
            "messages": [{"role": "user", "content": prompt}],
        }
        r = requests.post(url, headers=headers, json=payload, timeout=60)
        data = r.json()
        return data["choices"][0]["message"]["content"]
    except Exception:
        return "تعذر استخراج ملخص من الذكاء الاصطناعي، يمكن فيه مشكلة بالمفتاح او الاتصال."


# =========================================
# 👷‍♂️ العامل الخلفي (الطابور)
# =========================================
async def process_queue_worker(app: Application):
    print("🚀 Background worker started...")
    while True:
        chat_id, username = await request_queue.get()

        try:
            await app.bot.send_message(
                chat_id=chat_id,
                text=f"ثواني بس @{username} ، قاعد انبش في تاريخه 👀",
                parse_mode="HTML",
            )

            profile, tweets = get_user_profile(username)
            if not profile:
                await app.bot.send_message(
                    chat_id=chat_id,
                    text="❌ ما قدرت اجيب معلومات الحساب، يمكن اليوزر غلط او الحساب مخفي بقوة.",
                )
                continue

            # birthday
            birthday_block = detect_birthday_from_tweets(tweets)

            # location from talk
            location_block = detect_location_from_tweets(tweets)

            # friends
            friends = detect_friends_from_tweets(tweets)

            # rule-based personality + hobbies + security
            personality = analyze_personality_rule_based(tweets)
            hobbies = analyze_hobbies_rule_based(tweets)
            security_txt = security_check(tweets)

            # AI summary
            ai_summary = deepseek_summary(
                profile, tweets, personality, hobbies, security_txt
            )

            # بناء الاوتبوت النهائي
            msg = f"""الهدف: @{profile['username']}
──────────────
📝 <b>البايو:</b>
{clean_text(profile['bio'])}

📍 <b>الدولة (الرسمية):</b> {clean_text(profile['loc'])}
📱 <b>يدخل من:</b> غير معروف
📅 <b>موجود من:</b> {clean_text(profile['joined'])}
──────────────
{birthday_block}
──────────────
{location_block}
──────────────
👥 <b>أخوياه (أكثر ناس يرد عليهم / يذكرهم):</b>
"""

            if friends:
                for i, (u, c) in enumerate(friends, 1):
                    msg += f"{i}. <code>@{u}</code> (تكرر {c} مرة)\n"
            else:
                msg += "ما فيه اسماء واضحة تتكرر كثير.\n"

            msg += f"""
──────────────
🧠 <b>وش وضعه؟ (تحليل شخصيته – قواعد):</b>
{personality}
──────────────
🎭 <b>وش جوّه؟ (تحليل الهوايات – قواعد):</b>
{hobbies}
──────────────
{security_txt}
──────────────
🤖 <b>ملخص الذكاء الاصطناعي عنه:</b>
{ai_summary}

👁‍🗨 <b>انتهى التقرير.</b>
"""

            await app.bot.send_message(
                chat_id=chat_id,
                text=msg,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )

        except Exception as e:
            await app.bot.send_message(
                chat_id=chat_id,
                text=f"❌ صار خطأ فني داخل التحليل: {e}",
            )
        finally:
            request_queue.task_done()


# =========================================
# 🧵 تليجرام – الأوامر
# =========================================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    name = user.first_name or "حبيبي"
    await update.message.reply_text(
        f"👋 هلا والله {name}!\n\n"
        "هات يوزر تويتر (بدون روابط) وانا اسرد لك تفاصيله بتقرير كامل 🔍",
        parse_mode="HTML",
    )


async def handle_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_txt = (update.message.text or "").strip()

    # تنظيف اليوزر من روابط الخ
    user_txt = user_txt.replace("https://", "").replace("http://", "")
    user_txt = user_txt.replace("www.", "")
    user_txt = user_txt.replace("x.com/", "").replace("twitter.com/", "")
    user_txt = user_txt.replace("@", "").split("/")[0].strip()

    if not user_txt or " " in user_txt:
        await update.message.reply_text(
            "اكتب يوزر واحد بس، بدون مسافات وبدون رابط كامل 🙏",
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
            f"ثواني بس @{user_txt} ، قاعد انبش في تاريخه 👀",
            parse_mode="HTML",
        )

    await request_queue.put((chat_id, user_txt))


async def post_init(application: Application):
    asyncio.create_task(process_queue_worker(application))


# =========================================
# 🚀 تشغيل البوت
# =========================================
if __name__ == "__main__":
    print("🤖 Bot is running (Hybrid Twitter Analyzer)…")
    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(
        MessageHandler(filters.TEXT & (~filters.COMMAND), handle_username)
    )

    app.run_polling()
