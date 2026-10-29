"""
Rodychi Leads Webhook Bot (@leadsrodychi_bot)
────────────────────────────────────────────
Приймає POST від Fit Score фронтенда → шле сповіщення адміну + пише в SQLite.
+ CRM-команди в самому Telegram: /leads /lead /stats /find /status
+ Backup в Google Sheets (через Apps Script webhook, опціонально)

Env vars (Render):
  BOT_TOKEN         = 8841660104:AAGjIQefaq_fi5zgvVgeaFFeNaKi3JbZhV0
  ADMIN_CHAT_ID     = (заповнюється автоматично при першому /start від адміна)
  SHEETS_WEBHOOK    = (опціонально — Google Apps Script Web App URL)
  PORT              = 10000  (Render автоматично)
"""

import os
import json
import logging
import sqlite3
import asyncio
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import aiohttp
from aiohttp import web
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
)

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("leads")

# ═══════════ CONFIG ═══════════
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise SystemExit("BOT_TOKEN env var missing")

ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID", "").strip()  # може бути ""; першу /start збережемо
SHEETS_WEBHOOK = os.getenv("SHEETS_WEBHOOK", "").strip()
PORT = int(os.getenv("PORT", "10000"))
DB_PATH = os.getenv("DB_PATH", "leads.db")

KYIV_TZ = timezone(timedelta(hours=3))

# ═══════════ DB ═══════════
def db():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c

def init_db():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS leads (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          quiz_id TEXT,
          event TEXT,
          name TEXT,
          contact TEXT,
          instagram TEXT,
          company TEXT,
          score INTEGER,
          tier TEXT,
          source TEXT,
          business_type TEXT,
          niche TEXT,
          avg_check TEXT,
          budget TEXT,
          ltv TEXT,
          timing TEXT,
          answers_json TEXT,
          magnet_url TEXT,
          status TEXT DEFAULT 'new',
          note TEXT,
          created_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_leads_quiz ON leads(quiz_id);
        CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status);
        CREATE INDEX IF NOT EXISTS idx_leads_created ON leads(created_at);

        CREATE TABLE IF NOT EXISTS admins (
          chat_id INTEGER PRIMARY KEY,
          username TEXT,
          added_at TEXT
        );
        """)
        c.commit()
    log.info("DB initialized at %s", DB_PATH)

def get_admins() -> list[int]:
    with db() as c:
        rows = c.execute("SELECT chat_id FROM admins").fetchall()
    return [r["chat_id"] for r in rows]

def add_admin(chat_id: int, username: str = ""):
    with db() as c:
        c.execute("INSERT OR IGNORE INTO admins (chat_id, username, added_at) VALUES (?, ?, ?)",
                  (chat_id, username, datetime.now(KYIV_TZ).isoformat()))
        c.commit()

# ═══════════ BOT ═══════════
bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
router = Router()
dp.include_router(router)

TIER_EMOJI = {"green": "🟢", "yellow": "🟡", "orange": "🟠", "red": "🔴"}
STATUS_EMOJI = {"new": "🆕", "contacted": "📞", "scheduled": "📅",
                "in_deal": "💼", "won": "✅", "lost": "❌"}
STATUS_ORDER = ["new", "contacted", "scheduled", "in_deal", "won", "lost"]

def format_lead_card(lead: dict) -> str:
    tier_e = TIER_EMOJI.get(lead.get("tier"), "⚪")
    status_e = STATUS_EMOJI.get(lead.get("status", "new"), "🆕")
    lines = [
        f"🎯 <b>Новий лід · Fit Score</b>",
        f"",
        f"👤 <b>{html_esc(lead.get('name') or '—')}</b>",
        f"📞 {html_esc(lead.get('contact') or '—')}",
    ]
    if lead.get("instagram"):
        lines.append(f"📸 {html_esc(lead['instagram'])}")
    if lead.get("company"):
        lines.append(f"🏢 {html_esc(lead['company'])}")
    lines.extend([
        f"",
        f"{tier_e} Score: <b>{lead.get('score', '—')}/100</b> · {(lead.get('tier') or '').upper()}",
        f"🔗 Source: <code>{html_esc(lead.get('source') or 'direct')}</code>",
        f"",
        f"<b>Профіль</b>:",
        f"• Тип: {html_esc(lead.get('business_type') or '—')}",
        f"• Ніша: {html_esc(lead.get('niche') or '—')}",
        f"• Ср. чек: {html_esc(lead.get('avg_check') or '—')}",
        f"• Бюджет: {html_esc(lead.get('budget') or '—')}/міс",
        f"• LTV: {html_esc(lead.get('ltv') or '—')}",
        f"• Готовність: {html_esc(lead.get('timing') or '—')}",
    ])
    if lead.get("magnet_url"):
        lines.append(f"")
        lines.append(f"📥 Playbook: {lead['magnet_url']}")
    lines.append("")
    lines.append(f"ID: <code>{lead.get('quiz_id', '')}</code> · {status_e} {lead.get('status', 'new')}")
    return "\n".join(lines)

def html_esc(s):
    if s is None: return ""
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def lead_keyboard(quiz_id: str, current_status: str = "new") -> InlineKeyboardMarkup:
    """Кнопки для швидкої зміни статусу"""
    rows = [
        [
            InlineKeyboardButton(text="📞 Зв'язався", callback_data=f"st|{quiz_id}|contacted"),
            InlineKeyboardButton(text="📅 Дзвінок", callback_data=f"st|{quiz_id}|scheduled"),
        ],
        [
            InlineKeyboardButton(text="💼 В угоді", callback_data=f"st|{quiz_id}|in_deal"),
            InlineKeyboardButton(text="✅ Продаж", callback_data=f"st|{quiz_id}|won"),
            InlineKeyboardButton(text="❌ Втрачено", callback_data=f"st|{quiz_id}|lost"),
        ],
        [
            InlineKeyboardButton(text="📝 Деталі + всі відповіді", callback_data=f"detail|{quiz_id}"),
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)

# ═══════════ HANDLERS ═══════════

@router.message(Command("start"))
async def cmd_start(m: Message):
    admins = get_admins()
    if not admins:
        add_admin(m.chat.id, m.from_user.username or "")
        await m.answer(
            f"✅ Вас додано як адміна цього бота.\n"
            f"Chat ID: <code>{m.chat.id}</code>\n\n"
            f"Тепер сюди приходитимуть ліди з Fit Score.\n\n"
            f"<b>Команди</b>:\n"
            f"/leads — останні 20 лідів\n"
            f"/stats — статистика по tier і source\n"
            f"/find текст — пошук по імені/ніші\n"
            f"/lead abc123 — деталі конкретного ліда"
        )
        log.info("Admin registered: %s (@%s)", m.chat.id, m.from_user.username)
    elif m.chat.id in admins:
        await m.answer(
            f"Ви вже адмін.\n\n"
            f"<b>Команди</b>:\n"
            f"/leads — останні 20 лідів\n"
            f"/stats — статистика\n"
            f"/find текст — пошук\n"
            f"/lead abc123 — деталі"
        )
    else:
        await m.answer("Цей бот приватний.")

@router.message(Command("leads"))
async def cmd_leads(m: Message):
    if m.chat.id not in get_admins():
        return
    with db() as c:
        rows = c.execute("""
            SELECT quiz_id, name, contact, score, tier, status, source, created_at, business_type
            FROM leads WHERE event='contact_captured'
            ORDER BY id DESC LIMIT 20
        """).fetchall()
    if not rows:
        await m.answer("Ще нема лідів.")
        return
    lines = ["📋 <b>Останні 20 лідів</b>\n"]
    for r in rows:
        te = TIER_EMOJI.get(r["tier"], "⚪")
        se = STATUS_EMOJI.get(r["status"] or "new", "🆕")
        created = r["created_at"][:16].replace("T", " ") if r["created_at"] else "—"
        lines.append(
            f"{te}{se} <code>{r['quiz_id']}</code> · {html_esc(r['name'])} · "
            f"{r['score']}/100 · {html_esc(r['business_type'] or '')} · "
            f"<i>{created}</i> · src:<code>{html_esc(r['source'] or 'direct')}</code>"
        )
    await m.answer("\n".join(lines))

@router.message(Command("stats"))
async def cmd_stats(m: Message):
    if m.chat.id not in get_admins():
        return
    with db() as c:
        total = c.execute("SELECT COUNT(*) c FROM leads WHERE event='contact_captured'").fetchone()["c"]
        by_tier = {r["tier"]: r["c"] for r in c.execute(
            "SELECT tier, COUNT(*) c FROM leads WHERE event='contact_captured' GROUP BY tier"
        ).fetchall()}
        by_status = {r["status"]: r["c"] for r in c.execute(
            "SELECT status, COUNT(*) c FROM leads WHERE event='contact_captured' GROUP BY status"
        ).fetchall()}
        by_source = {r["source"] or "direct": r["c"] for r in c.execute(
            "SELECT source, COUNT(*) c FROM leads WHERE event='contact_captured' GROUP BY source"
        ).fetchall()}
        avg_score = c.execute(
            "SELECT AVG(score) a FROM leads WHERE event='contact_captured'"
        ).fetchone()["a"]
    text = [f"📊 <b>Статистика лідів</b>\n"]
    text.append(f"Всього: <b>{total}</b>")
    text.append(f"Середній Score: <b>{avg_score:.1f if avg_score else 0}</b>\n" if avg_score else "")
    text.append("<b>По tier</b>:")
    for t, n in sorted(by_tier.items(), key=lambda x: -x[1]):
        text.append(f"  {TIER_EMOJI.get(t, '⚪')} {t or '—'}: {n}")
    text.append("\n<b>По статусу</b>:")
    for s, n in sorted(by_status.items(), key=lambda x: -x[1]):
        text.append(f"  {STATUS_EMOJI.get(s, '🆕')} {s or 'new'}: {n}")
    text.append("\n<b>По source</b>:")
    for s, n in sorted(by_source.items(), key=lambda x: -x[1]):
        text.append(f"  <code>{html_esc(s)}</code>: {n}")
    await m.answer("\n".join(text))

@router.message(Command("find"))
async def cmd_find(m: Message, command: CommandObject):
    if m.chat.id not in get_admins():
        return
    q = (command.args or "").strip()
    if not q:
        await m.answer("Приклад: <code>/find психолог</code>")
        return
    with db() as c:
        rows = c.execute("""
            SELECT quiz_id, name, contact, score, tier, business_type, niche, created_at
            FROM leads WHERE event='contact_captured' AND (
              LOWER(name) LIKE ? OR LOWER(niche) LIKE ? OR LOWER(business_type) LIKE ?
              OR LOWER(company) LIKE ? OR LOWER(contact) LIKE ?
            )
            ORDER BY id DESC LIMIT 20
        """, [f"%{q.lower()}%"] * 5).fetchall()
    if not rows:
        await m.answer(f"Нічого не знайдено для «{q}».")
        return
    lines = [f"🔍 Знайдено {len(rows)} лідів по «{q}»:\n"]
    for r in rows:
        te = TIER_EMOJI.get(r["tier"], "⚪")
        created = r["created_at"][:16].replace("T", " ") if r["created_at"] else "—"
        lines.append(
            f"{te} <code>{r['quiz_id']}</code> · {html_esc(r['name'])} · "
            f"{r['score']}/100 · {html_esc(r['niche'] or '')} · <i>{created}</i>"
        )
    await m.answer("\n".join(lines))

@router.message(Command("lead"))
async def cmd_lead(m: Message, command: CommandObject):
    if m.chat.id not in get_admins():
        return
    qid = (command.args or "").strip()
    if not qid:
        await m.answer("Приклад: <code>/lead abc12345</code>")
        return
    with db() as c:
        row = c.execute("SELECT * FROM leads WHERE quiz_id=? ORDER BY id DESC LIMIT 1",
                        (qid,)).fetchone()
    if not row:
        await m.answer(f"Лід <code>{qid}</code> не знайдений.")
        return
    lead = dict(row)
    await m.answer(format_lead_card(lead),
                   reply_markup=lead_keyboard(qid, lead.get("status", "new")))
    if row["note"]:
        await m.answer(f"📝 Нотатка:\n{html_esc(row['note'])}")

@router.callback_query(F.data.startswith("st|"))
async def cb_status(cb: CallbackQuery):
    if cb.message.chat.id not in get_admins():
        await cb.answer("Заборонено.", show_alert=True); return
    _, qid, new_status = cb.data.split("|", 2)
    with db() as c:
        c.execute("UPDATE leads SET status=? WHERE quiz_id=?", (new_status, qid))
        c.commit()
    await cb.answer(f"Статус: {new_status}")
    # Update card
    with db() as c:
        row = c.execute("SELECT * FROM leads WHERE quiz_id=? ORDER BY id DESC LIMIT 1",
                        (qid,)).fetchone()
    if row:
        lead = dict(row)
        try:
            await cb.message.edit_text(format_lead_card(lead),
                                        reply_markup=lead_keyboard(qid, new_status))
        except Exception:
            pass

BRIEF_LABELS = {
    "company_name":      ("1",  "Название компании"),
    "business_info":     ("2",  "Коротко о бизнесе", {"niche":"Ниша / что продают","years":"Лет на рынке","monthly_sales":"Продаж в месяц"}),
    "economics":         ("3",  "Экономика одной продажи", {"avg_check":"Средний чек","margin":"Маржа","repeat":"Повторные покупки"}),
    "current_marketing": ("4",  "Текущее продвижение", {"tg_subs":"Подписчиков в Telegram","other_channels":"Другие каналы","paid_before":"Платная реклама раньше"}),
    "paid_details":      ("5",  "Детали платного опыта"),
    "audience":          ("6",  "Аудитория", {"age":"Основной возраст","gender":"Преобладающий пол","geo":"География"}),
    "uvp":               ("7",  "Почему выбирают именно их (UVP)"),
    "goals":             ("8",  "Цели на 3 месяца", {"main_goal":"Главная цель","target_number":"Целевой показатель","start_when":"Когда начинать"}),
    "budget":            ("9",  "Рекламный бюджет в месяц"),
    "source":            ("10", "Откуда о нас узнали"),
}


def format_brief_details(qid: str, lead: dict, answers: dict) -> str:
    lines = [
        f"📋 <b>Клиентский бриф — {html_esc(lead.get('company') or lead.get('name') or '')}</b>",
        f"ID: <code>{qid}</code>",
        "",
    ]
    for key, meta in BRIEF_LABELS.items():
        num, label = meta[0], meta[1]
        val = answers.get(key)
        empty = (val is None) or (val == "") or (val == []) or (val == {})
        if empty:
            lines.append(f"<b>{num}. {label}</b>")
            lines.append("   —")
            lines.append("")
            continue
        if len(meta) == 3 and isinstance(val, dict):
            lines.append(f"<b>{num}. {label}</b>")
            for subk, sublab in meta[2].items():
                sv = val.get(subk)
                if isinstance(sv, list):
                    sv = ", ".join(sv) if sv else "—"
                lines.append(f"   • {sublab}: <code>{html_esc(sv or '—')}</code>")
            lines.append("")
        else:
            if isinstance(val, list):
                val = ", ".join(val)
            lines.append(f"<b>{num}. {label}</b>")
            lines.append(f"   {html_esc(val)}")
            lines.append("")
    return "\n".join(lines).rstrip()


def brief_keyboard(quiz_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="📝 Все ответы брифа", callback_data=f"detail|{quiz_id}"),
    ]])


def format_brief_card(lead: dict) -> str:
    lines = [
        "📋 <b>Новый бриф</b>",
        "",
        f"🏢 <b>{html_esc(lead.get('company') or lead.get('name') or '—')}</b>",
        f"🧭 Ниша: {html_esc(lead.get('niche') or '—')}",
        f"💰 Бюджет: {html_esc(lead.get('budget') or '—')}/мес",
        f"💳 Средний чек: {html_esc(lead.get('avg_check') or '—')}",
        f"⏰ Готовность: {html_esc(lead.get('timing') or '—')}",
        "",
        f"ID: <code>{lead.get('quiz_id', '')}</code>",
        "",
        "👇 Жми кнопку чтобы увидеть все 10 ответов",
    ]
    return "\n".join(lines)


@router.callback_query(F.data.startswith("detail|"))
async def cb_detail(cb: CallbackQuery):
    if cb.message.chat.id not in get_admins():
        await cb.answer("Запрещено.", show_alert=True); return
    _, qid = cb.data.split("|", 1)
    with db() as c:
        row = c.execute("SELECT * FROM leads WHERE quiz_id=?", (qid,)).fetchone()
    if not row:
        await cb.answer("Не найдено."); return
    lead = dict(row)
    answers = json.loads(row["answers_json"] or "{}")
    if (lead.get("tier") == "brief") or (lead.get("event") == "client_brief"):
        text = format_brief_details(qid, lead, answers)
    else:
        lines = [f"📊 <b>Все ответы · {qid}</b>\n"]
        for k, v in answers.items():
            val = ", ".join(v) if isinstance(v, list) else v
            lines.append(f"<b>{k}</b>: {html_esc(val)}")
        text = "\n".join(lines)
    for i in range(0, len(text), 3800):
        await cb.message.answer(text[i:i+3800])
    await cb.answer()

@router.message(Command("note"))
async def cmd_note(m: Message, command: CommandObject):
    if m.chat.id not in get_admins():
        return
    parts = (command.args or "").split(" ", 1)
    if len(parts) < 2:
        await m.answer("Приклад: <code>/note abc12345 Дзвонив, передзвонить у пʼятницю</code>")
        return
    qid, note = parts
    with db() as c:
        existing = c.execute("SELECT note FROM leads WHERE quiz_id=? ORDER BY id DESC LIMIT 1",
                             (qid,)).fetchone()
        prev = existing["note"] if existing and existing["note"] else ""
        new_note = f"{prev}\n[{datetime.now(KYIV_TZ).strftime('%d.%m %H:%M')}] {note}".strip()
        c.execute("UPDATE leads SET note=? WHERE quiz_id=?", (new_note, qid))
        c.commit()
    await m.answer(f"📝 Нотатка додана до <code>{qid}</code>.")

# ═══════════ WEBHOOK (POST /quiz) ═══════════

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
}

async def quiz_webhook(request: web.Request):
    if request.method == "OPTIONS":
        return web.Response(headers=CORS_HEADERS)
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"ok": False, "err": "bad json"},
                                  status=400, headers=CORS_HEADERS)

    event = data.get("event", "unknown")
    quiz_id = data.get("quizId") or ""
    name = data.get("name") or ""
    contact = data.get("contact") or ""
    instagram = data.get("instagram") or ""
    company = data.get("company") or ""
    score = data.get("score") or 0
    tier = data.get("tier") or ""
    source = data.get("source") or "direct"
    answers = data.get("answers") or {}
    magnet_url = data.get("magnetUrl") or ""

    business_type = answers.get("businessType", "")
    niche = answers.get("niche", "")
    avg_check = answers.get("avgCheck", "")
    budget = answers.get("budget", "")
    ltv = answers.get("ltv", "")
    timing = answers.get("timing", "")

    now = datetime.now(KYIV_TZ).isoformat()

    with db() as c:
        c.execute("""
            INSERT INTO leads
            (quiz_id, event, name, contact, instagram, company, score, tier, source,
             business_type, niche, avg_check, budget, ltv, timing,
             answers_json, magnet_url, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (quiz_id, event, name, contact, instagram, company, score, tier, source,
              business_type, niche, avg_check, budget, ltv, timing,
              json.dumps(answers, ensure_ascii=False), magnet_url, now))
        c.commit()

    log.info("Lead saved: %s / %s / %s (%s)", quiz_id, event, name, source)

    # TG notification (only on contact_captured)
    if event in ("contact_captured", "client_brief"):
        lead = {
            "quiz_id": quiz_id, "name": name, "contact": contact, "instagram": instagram,
            "company": company, "score": score, "tier": tier, "source": source,
            "business_type": business_type, "niche": niche, "avg_check": avg_check,
            "budget": budget, "ltv": ltv, "timing": timing, "magnet_url": magnet_url,
            "status": "new"
        }
        is_brief = (event == "client_brief") or (tier == "brief")
        if is_brief:
            card_text = format_brief_card(lead)
            kb = brief_keyboard(quiz_id)
        else:
            card_text = format_lead_card(lead)
            kb = lead_keyboard(quiz_id, "new")
        for admin_id in get_admins():
            try:
                await bot.send_message(admin_id, card_text, reply_markup=kb)
            except Exception as e:
                log.warning("Failed to notify %s: %s", admin_id, e)

    # Backup to Google Sheets (async, non-blocking)
    if SHEETS_WEBHOOK:
        asyncio.create_task(post_to_sheets({
            "timestamp": now, "quiz_id": quiz_id, "event": event, "source": source,
            "name": name, "contact": contact, "instagram": instagram, "company": company,
            "score": score, "tier": tier, "business_type": business_type, "niche": niche,
            "avg_check": avg_check, "budget": budget, "ltv": ltv, "timing": timing,
            "magnet_url": magnet_url, "answers": json.dumps(answers, ensure_ascii=False)
        }))

    return web.json_response({"ok": True, "id": quiz_id}, headers=CORS_HEADERS)

async def post_to_sheets(payload: dict):
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(SHEETS_WEBHOOK, json=payload, timeout=aiohttp.ClientTimeout(total=8)) as r:
                await r.text()
    except Exception as e:
        log.warning("Sheets webhook failed: %s", e)

async def health(request):
    return web.json_response({"ok": True, "service": "rodychi-leads-webhook",
                              "admins": len(get_admins())})

# ═══════════ RUN ═══════════

async def start_http():
    app = web.Application()
    app.router.add_post("/quiz", quiz_webhook)
    app.router.add_options("/quiz", quiz_webhook)
    app.router.add_get("/health", health)
    app.router.add_get("/", health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    log.info("HTTP on :%s", PORT)

async def start_bot():
    await bot.delete_webhook(drop_pending_updates=True)
    me = await bot.get_me()
    log.info("Bot started: @%s (id=%s)", me.username, me.id)
    # Pre-register admin if ENV set
    if ADMIN_CHAT_ID.isdigit():
        add_admin(int(ADMIN_CHAT_ID))
        log.info("Admin pre-registered from ENV: %s", ADMIN_CHAT_ID)
    await dp.start_polling(bot, handle_signals=False)

async def main():
    init_db()
    await asyncio.gather(start_http(), start_bot())

if __name__ == "__main__":
    asyncio.run(main())
