"""@rdchbonus_bot — Rodychi lead-magnet bot.

Users click CTAs on rodychi.media/rodychi-fit-score/ → opens
  https://t.me/rdchbonus_bot?start=expert
  https://t.me/rdchbonus_bot?start=business
  https://t.me/rdchbonus_bot?start=school
Bot sends the matching playbook PDF (hosted on GitHub Pages).
"""
import os
import asyncio
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

logging.basicConfig(
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("rdchbonus")

TOKEN = os.environ["BOT_TOKEN"]
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID", "").strip()

BASE_URL = "https://rodychi.media/rodychi-fit-score/guides"

GUIDES = {
    "expert": {
        "url":  f"{BASE_URL}/Rodychi_Expert_TG_Guide_v7.pdf",
        "filename": "Rodychi_Expert_Playbook.pdf",
        "title": "📘 Playbook для експерта",
        "intro": (
            "Привіт! 👋\n\n"
            "Тримайте playbook — як експерту заробляти $10-25K/міс у Telegram.\n\n"
            "16 сторінок: план міграції з Instagram, прайсинг преміум-програм, "
            "бонус-шаблони і кейс психолога з $14K за 4 місяці.\n\n"
            "Будуть питання — пишіть @rodychi_mng."
        ),
    },
    "business": {
        "url":  f"{BASE_URL}/Rodychi_Business_TG_Meta_v7.pdf",
        "filename": "Rodychi_Business_Playbook.pdf",
        "title": "🏢 Playbook для бізнесу",
        "intro": (
            "Привіт! 👋\n\n"
            "Тримайте playbook — як знизити CAC у 5 разів через Telegram + Meta.\n\n"
            "Для салонів, клінік, ресторанів, фітнес-студій. Готовий план на 60 днів, "
            "кейс б'юті-салону +307% виручки.\n\n"
            "Будуть питання — пишіть @rodychi_mng."
        ),
    },
    "school": {
        "url":  f"{BASE_URL}/Rodychi_School_Second_Funnel_v7.pdf",
        "filename": "Rodychi_School_Playbook.pdf",
        "title": "🎓 Playbook для онлайн-школи",
        "intro": (
            "Привіт! 👋\n\n"
            "Тримайте playbook — друга воронка для онлайн-школи, +30–50% доходу за 4-6 місяців.\n\n"
            "4 способи заробити з тих самих учнів, кейс школи з +35% доходу і готова архітектура.\n\n"
            "Будуть питання — пишіть @rodychi_mng."
        ),
    },
}


LEADS_WEBHOOK = os.environ.get("LEADS_WEBHOOK", "http://127.0.0.1:10001/quiz")

async def notify_admin(context, text: str):
    """Legacy no-op: kept for backward compat. Notifications now routed via send_lead_to_backend."""
    return

async def send_lead_to_backend(user, param: str):
    """POST lead data to leads bot webhook so it appears in /leads CRM."""
    import aiohttp, uuid
    payload = {
        "event": "magnet_claim",
        "quizId": "mag_" + uuid.uuid4().hex[:8],
        "source": "rdchbonus_bot",
        "name": (user.first_name or "") + ((" " + user.last_name) if user.last_name else ""),
        "contact": ("@" + user.username) if user.username else f"tg://user?id={user.id}",
        "company": "",
        "tier": "magnet",
        "score": 0,
        "answers": {
            "playbook": param,
            "telegram_user_id": user.id,
            "telegram_username": user.username or "",
        },
    }
    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.post(LEADS_WEBHOOK, json=payload) as r:
                await r.text()
                log.info("Lead posted to backend: %s", payload["quizId"])
    except Exception as e:
        log.warning("backend lead post fail: %s", e)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    param = (context.args[0] if context.args else "").lower().strip()
    log.info("User %s (@%s) started with param=%s", user.id, user.username, param)

    if param in GUIDES:
        g = GUIDES[param]
        await update.message.reply_text(g["intro"])
        try:
            await update.message.reply_document(
                document=g["url"],
                filename=g["filename"],
                caption=g["title"],
            )
            await send_lead_to_backend(user, param)
        except Exception as e:
            log.exception("send document failed")
            await update.message.reply_text(
                f"Вибачте, щось пішло не так зі відправкою файлу: {e}\n"
                f"Напишіть @rodychi_mng — ми надішлемо вам PDF вручну."
            )
    else:
        await update.message.reply_text(
            "👋 Привіт! Я бот Rodychi media — агенція повного циклу у Telegram.\n\n"
            "Оберіть playbook під вашу ситуацію:\n\n"
            "/start expert — для експертів (психологи, коучі, нутриціологи)\n"
            "/start business — для бізнесу (б'юті, клініки, фітнес, ресторани)\n"
            "/start school — для онлайн-шкіл і продюсерів\n\n"
            "Або пройдіть повний Fit Score (5 хв, безкоштовно):\n"
            "https://rodychi.media/rodychi-fit-score"
        )


async def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    log.info("✅ @rdchbonus_bot started")
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
