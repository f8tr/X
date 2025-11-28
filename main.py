import os
import re
import html
import json
import asyncio
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
# 🔐 قراءة المفاتيح من Environment
# =========================================
BOT_TOKEN = os.getenv("BOT_TOKEN")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

# لو واحد منهم ناقص خلي البرنامج يطيح بدري
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN مهو موجود في Environment")
if not DEEPSEEK_API_KEY:
    raise RuntimeError("DEEPSEEK_API_KEY مهو موجود في Environment")

# طابور الطلبات
request_queue = asyncio.Queue()

# =========================================
# 🌐 Nitter Instances (بدائل تويتر بدون لوجن)
# =========================================
NITTER_INSTANCES = [
    "https://nitter.poast.org",
    "https://nitter.fdn.fr",
    "https://nitter.lacontrevoie.fr",
    "https://nitter.cz",
    "https://n.opnxng.com",
    "https://nitter.esmailelbob.xyz",
]


def clean_text(text: str) -> str:
    if not text:
        return "غير معروف"
    return html.escape(str(text))


def strip_tags(s: str) -> str:
    return re.sub(r"<.*?>", "", s or "")


def fetch_from_nitter(path: str):
    """
    يلف على اكثر من سيرفر Nitter لين يلقى واحد يرد
    يرجع (النص, الدومين) او (None, None)
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; TelegramBot/1.0)"
    }
    for base in NITTER_INSTANCES:
        url = base.rstrip("/") + path
        try:
            r = requests.get(url, headers=headers, timeout=15)
            if r.status_code == 200 and r.text.strip():
                return r.text, base
        except Exception:
            continue
    return None, None


# =========================================
# 🐦 سحب بيانات الحساب + التغريدات من Nitter
# =========================================
def get_profile_and_tweets(username: str):
    """
    يرجع:
      profile: dict فيه (name, username, bio, loc, joined)
      tweets: list فيه عناصر {text, date}
      sources_counter: Counter لمصادر التغريدات (iPhone, Web...)
    """

    # 1) صفحة البروفايل HTML
    html_page, used_base = fetch_from_nitter(f"/{username}")
    if not html_page:
        return None, [], Counter()

    # الاسم الظاهر
    name = username
    m_name = re.search(
        r'class="profile-card-fullname"[^>]*>(.*?)</', html_page, re.S
    )
    if m_name:
        name = strip_tags(m_name.group(1)).strip()

    # البايو
    bio = "لا يوجد"
    m_bio = re.search(
        r'class="profile-bio"[^>]*>(.*?)</(div|p)>', html_page, re.S
    )
    if m_bio:
        bio = strip_tags(m_bio.group(1)).strip()
        if not bio:
            bio = "لا يوجد"

    # الموقع / الدولة من خانة اللوكيشن
    loc = "غير معروف"
    m_loc = re.search(
        r'class="profile-location"[^>]*>.*?<span[^>]*>(.*?)</span>',
        html_page,
        re.S,
    )
    if m_loc:
        loc = strip_tags(m_loc.group(1)).strip() or "غير معروف"

    # تاريخ الانضمام
    joined = "غير معروف"
    # مثال النص: Joined May 2015
    m_join = re.search(r"Joined\s+([^<\n]+)", html_page)
    if m_join:
        joined = m_join.group(1).strip()

    profile = {
        "name": name,
        "username": username,
        "bio": bio,
        "loc": loc,
        "joined": joined,
    }

    # 2) RSS للتغريدات
    rss_text, _ = fetch_from_nitter(f"/{username}/rss")
    tweets = []
    sources_counter = Counter()

    if rss_text:
        items = re.findall(r"<item>(.*?)</item>", rss_text, re.S)
        for it in items[:120]:  # ناخذ 120 تغريدة تكفي
            # العنوان = نص التغريدة
            m_title = re.search(r"<title>(.*?)</title>", it, re.S)
            if not m_title:
                continue
            t_html = m_title.group(1)
            t_txt = strip_tags(html.unescape(t_html)).strip()
            if not t_txt:
                continue

            # التاريخ
            m_date = re.search(r"<pubDate>(.*?)</pubDate>", it, re.S)
            date_str = m_date.group(1).strip() if m_date else None
            # نحاول نحول التاريخ لصيغة معروفة
            norm_date = None
            if date_str:
                try:
                    dt = datetime.strptime(
                        date_str, "%a, %d %b %Y %H:%M:%S %z"
                    )
                    norm_date = dt.isoformat()
                except Exception:
                    norm_date = date_str

            # المصدر (Twitter for iPhone / Web .. الخ) من description
            m_desc = re.search(
                r"<description>(.*?)</description>", it, re.S
            )
            src_txt = ""
            if m_desc:
                desc_clean = html.unescape(
                    strip_tags(m_desc.group(1))
                )
                # مثال: "RT @user: النص ... · Twitter for iPhone"
                m_src = re.search(
                    r"Twitter for ([A-Za-z0-9 ]+)", desc_clean
                )
                if m_src:
                    src_txt = m_src.group(1).strip()
                    sources_counter[src_txt] += 1

            tweets.append({"text": t_txt, "date": norm_date})

    return profile, tweets, sources_counter


# =========================================
# 📱 تحديد الجهاز من المصادر
# =========================================
def detect_device_from_sources(src_counter: Counter) -> str:
    if not src_counter:
        return "غير معروف"

    top_src, _ = src_counter.most_common(1)[0]
    if "iPhone" in top_src:
        return "iPhone"
    if "Android" in top_src:
        return "Android"
    if "Web" in top_src or "web" in top_src:
        return "Web"
    return top_src


# =========================================
# 🎂 يوم الميلاد من التغريدات
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
        low = txt.lower()
        if any(kw.lower() in low for kw in keywords):
            d = tw["date"]
            if d:
                try:
                    dt = datetime.fromisoformat(d)
                    d_str = dt.strftime("%Y-%m-%d")
                except Exception:
                    d_str = d
            else:
                d_str = "غير معروف"
            snippet = txt[:80].replace("\n", " ")
            return (
                "🎂 يوم ميلاده (بالدليل):\n"
                "✅ لقيناه!\n"
                f"التاريخ: {d_str}\n"
                f'الدليل تغريدة يقول: "{html.escape(snippet)}..."'
            )

    return "🎂 يوم ميلاده (بالدليل):\nما لقيت شي واضح عن يوم ميلاده من تغريداته."


# =========================================
# 📍 موقعه من سوالفه
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
        "القصيم",
        "أبها",
        "ابها",
        "تبوك",
        "حائل",
        "جازان",
        "الخبر",
        "الكويت",
        "دبي",
        "الشرقية",
        "الشرقيه",
    ]

    for tw in tweets:
        txt = tw["text"]
        for city in cities:
            if city in txt:
                d = tw["date"]
                if d:
                    try:
                        dt = datetime.fromisoformat(d)
                        d_str = dt.strftime("%Y-%m-%d")
                    except Exception:
                        d_str = d
                else:
                    d_str = "غير معروف"
                snippet = txt[:100].replace("\n", " ")
                return (
                    "📍 موقعه (من سوالفه):\n"
                    f'قفطناه يقول: "{html.escape(snippet)}..."\n'
                    f"بتاريخ: {d_str}"
                )

    return "📍 موقعه (من سوالفه):\nما وضح من سوالفه وين ساكن بالضبط."


# =========================================
# 👥 اخوياه (من المنشن)
# =========================================
def detect_friends_from_tweets(tweets):
    counter = Counter()
    for tw in tweets:
        for m in re.findall(r"@([A-Za-z0-9_]+)", tw["text"]):
            counter[m.lower()] += 1

    # كم استبعاد بسيط
    ignore = {"twitter", "support", "x", "elonmusk"}
    for ig in ignore:
        counter.pop(ig, None)

    return counter.most_common(5)


# =========================================
# 🧠 تحليل شخصية (Rules)
# =========================================
def analyze_personality_rule_based(tweets):
    if not tweets:
        return "ما في تغريدات كفاية اقدر احكم منها."

    text = " ".join(t["text"] for t in tweets).lower()

    aggro = len(
        re.findall(
            r"(غبي|تافه|مرض|صياح|بزر|كريه|ياخي|تخلف|قذر|حيوان|كلب|زق|تفجير|حرب|قتل)",
            text,
        )
    )
    emo = len(
        re.findall(
            r"(احبكم|احبك|حب|قلب|قلبي|سعيد|مبسوط|شاكر|شكرا|جميل|جمال|روعة|لطيف|ودود)",
            text,
        )
    )
    ego = len(
        re.findall(
            r"\b(انا|أنا|عن نفسي|رايي|رأيي|شخصيا|تجربتي|me|my|i )\b",
            text,
        )
    )
    intellect = len(
        re.findall(
            r"(تحليل|منطق|واقعي|السبب|مستقبل|مشروع|تطوير|تقنية|سياسة|اقتصاد|بحث)",
            text,
        )
    )

    traits = []

    if aggro > emo:
        traits.append(
            "⚠️ يميل للحدة شوي، اسلوبه فيه نبرة هجوم او تنمر في بعض التغريدات."
        )
    elif emo > aggro:
        traits.append(
            "💖 يميل للكلام اللطيف والدعم اكثر من الصدام، جوه هادي نوعا ما."
        )

    if ego > 4:
        traits.append(
            "😎 واثق من نفسه، يحب يذكر رايه وتجربته بشكل واضح ومتكرر."
        )

    if intellect > 3:
        traits.append(
            "🧠 يحب يحلل ويتفلسف على الاحداث، مو بس يتابعها بشكل سطحي."
        )

    if not traits:
        traits.append(
            "⚖️ شخصيته متوازنة، تغريداته عادية غالبا، لا هو راعي دراما ولا راعي مديح زايد."
        )

    return "\n".join(traits)


# =========================================
# 🎭 تحليل الهوايات (Rules)
# =========================================
def analyze_hobbies_rule_based(tweets):
    if not tweets:
        return "هواياته مو واضحة، ما في محتوى كفاية عن جوه."

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

        desc = "🎮 جيمر غالبا، واضح انه راعي قيمز وقطع."
        if games:
            desc += f"\nالالعاب اللي تبينت من تغريداته: {', '.join(games)}."
        sections.append(desc)

    # كوره
    if re.search(r"(هلال|نصر|اتحاد|اهلي|أهلي|دوري|مباراة|هدف|messi|ronaldo)", text):
        club = "متابع للدوري والكورة بشكل عام"
        if "هلال" in text:
            club = "الهلال 💙"
        elif "نصر" in text:
            club = "النصر 💛"
        elif "اتحاد" in text:
            club = "الاتحاد 🐆"
        elif "اهلي" in text or "أهلي" in text:
            club = "الاهلي 💚"

        sections.append(
            f"⚽ الكورة:\nيظهر انه يشجع ({club}) ويتابع المباريات والاخبار الرياضية."
        )

    # تقنية / امن سيبراني
    if re.search(
        r"(linux|لينكس|ubuntu|arch|manjaro|kali|whonix|python|بايثون|code|coding|cyber|security|hack|هكر|برمجة|أمن|سيرفر)",
        text,
    ):
        sections.append(
            "💻 تقني او راعي امن سيبراني، واضح يحب انظمة لينكس او البرمجة او مجال السيكيورتي."
        )

    # ترفيه / انمي
    if re.search(
        r"(anime|انمي|one piece|ون بيس|naruto|ناروتو|attack on titan|aot|netflix|نتفلكس|فلم|فيلم|مسلسل)",
        text,
    ):
        sections.append(
            "📺 جوه ترفيهي، يتابع انمي او مسلسلات وافلام، غالبا جوه سهر ونتفلكس."
        )

    # سيارات
    if re.search(
        r"(موتر|سيارة|سياره|تفحيط|درفت|تيربو|تزويد|ميكانيكا|بنزين|سرعة)",
        text,
    ):
        sections.append(
            "🚗 راعي مواتر او سيارات، مهتم بالتزويد او السواقه او محتوى السيارات بشكل عام."
        )

    if not sections:
        return "هواياته مو واضحه من تغريداته، يا انه ما يتكلم عنها او حسابه عام جدا."

    return "\n".join(sections)


# =========================================
# 🚨 الفحص الامني (الفاظ)
# =========================================
def security_check(tweets):
    bad_words = [
        "لعن",
        "كسم",
        "قذر",
        "زبالة",
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
            d = tw["date"]
            year = "غير معروف"
            if d:
                try:
                    dt = datetime.fromisoformat(d)
                    year = dt.year
                except Exception:
                    year = d
            snippet = txt[:100].replace("\n", " ")
            return (
                "🚨 الفحص الامني (الولاء والماضي):\n"
                "⚠️ فيه تغريدات فيها الفاظ او نبرة عدوانية.\n"
                f"مثال (سنة {year}):\n\"{html.escape(snippet)}...\""
            )

    return "🚨 الفحص الامني (الولاء والماضي):\n✅ ما شفت شي مقلق من ناحية الفاظ او عدوانية واضحة."


# =========================================
# 🤖 DeepSeek – ملخص AI
# =========================================
def deepseek_summary(profile, tweets, personality, hobbies, security_txt):
    joined_tweets = "\n".join(t["text"] for t in tweets[:40])

    prompt = f"""
انت محلل اجتماعي ونفسي سعودي، ابيك تحلل صاحب هذا الحساب وتحط رايك بشكل مرتب وبلهجة سعودية خفيفة بدون تنوين.

معلومات الحساب:
الاسم: {profile['name']}
اليوزر: @{profile['username']}
البايو: {profile['bio']}
الموقع الرسمي: {profile['loc']}
تاريخ انشاء الحساب: {profile['joined']}

تحليل الشخصية (من نظام قواعد سابق):
{personality}

تحليل الهوايات:
{hobbies}

الفحص الامني:
{security_txt}

بعض من تغريداته:
\"\"\" 
{joined_tweets}
\"\"\"


المطلوب منك:
- تعطيني نظرة عامة عن هالشخصية (رايق، متوتر، هجومي، منطقي، نرجسي، حساس.. الخ).
- جوه العام: ايجابي، سلبي، سوداوي، ساخر.. الخ.
- طريقة تعامله مع الناس اونلاين: محترم، هجومي، دفاعي، يمزح بزيادة.. الخ.
- اذا فيه نقاط ملفته او تحذير بسيط (بدون قذف او مبالغة).

اكتب الاجابة بنقاط واضحة وبالعربي فقط، وباسلوب مفهوم.
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
        return "ما قدرت اطلع ملخص من الذكاء الاصطناعي، غالبا فيه مشكلة في المفتاح او الاتصال."


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
                text=f"ثواني بس @{username} قاعد انبش في تاريخه 👀",
                parse_mode="HTML",
            )

            profile, tweets, src_counter = get_profile_and_tweets(username)
            if not profile:
                await app.bot.send_message(
                    chat_id=chat_id,
                    text="❌ ما قدرت اجيب معلومات الحساب، يمكن اليوزر غلط او الحساب محجوب بقوة.",
                )
                continue

            device = detect_device_from_sources(src_counter)
            birthday_block = detect_birthday_from_tweets(tweets)
            location_block = detect_location_from_tweets(tweets)
            friends = detect_friends_from_tweets(tweets)
            personality = analyze_personality_rule_based(tweets)
            hobbies = analyze_hobbies_rule_based(tweets)
            security_txt = security_check(tweets)
            ai_summary = deepseek_summary(
                profile, tweets, personality, hobbies, security_txt
            )

            # بناء الاوت بوت النهائي بالشكل اللي تبيه
            msg = f"""الهدف: @{profile['username']}
──────────────
📝 البايو:
{clean_text(profile['bio'])}

📍 الدولة (الرسمية): {clean_text(profile['loc'])}
📱 يدخل من: {clean_text(device)}
📅 موجود من: {clean_text(profile['joined'])}
──────────────
{birthday_block}
──────────────
{location_block}
──────────────
👥 اخوياه (اكثر ناس يرد عليهم / يذكرهم):
"""

            if friends:
                for i, (u, c) in enumerate(friends, 1):
                    msg += f"{i}. @{u} (تكرر {c} مرة)\n"
            else:
                msg += "ما فيه اسماء واضحة تتكرر كثير.\n"

            msg += f"""
──────────────
🧠 وش وضعه؟ (تحليل شخصيته):
{personality}
──────────────
🎭 وش جوه؟ (تحليل الهوايات):
{hobbies}
──────────────
{security_txt}
──────────────
🤖 ملخص الذكاء الاصطناعي:
{ai_summary}

👁‍🗨 انتهى التقرير.
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
        "هات اليوزر و اسرد لك تفاصيله 🔍",
        parse_mode="HTML",
    )


async def handle_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_txt = (update.message.text or "").strip()

    # تنظيف اليوزر من روابط
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
            f"قدامك {q_size} في الطابور.\n"
            "اقضي وقتك بالاستغفار ❤️",
            parse_mode="HTML",
        )
    else:
        await update.message.reply_text(
            f"ثواني بس @{user_txt} قاعد انبش في تاريخه 👀",
            parse_mode="HTML",
        )

    await request_queue.put((chat_id, user_txt))


async def post_init(application: Application):
    asyncio.create_task(process_queue_worker(application))


# =========================================
# 🚀 تشغيل البوت
# =========================================
if __name__ == "__main__":
    print("🤖 Bot is running (Nitter Hybrid Analyzer)…")
    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(
        MessageHandler(filters.TEXT & (~filters.COMMAND), handle_username)
    )

    app.run_polling()
