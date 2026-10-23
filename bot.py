"""Telegram bot for the AI curator: the same request handler and SQLite log as the site.

    uv run python bot.py

Environment (on the server from /opt/ai-curator/.env via systemd, locally from ~/.secrets/common.env):
    CURATOR_BOT_TOKEN       bot token from BotFather
    OPENROUTER_API_KEY      key with a spending limit
    CURATOR_ADMIN_CHAT_ID   only this chat may run /stats and /export
    CURATOR_LOG_SALT        secret salt for hashing user ids in the log
"""

import asyncio
import logging
import os
from datetime import datetime
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from curator import config, kb, lms, service
from curator.db_logger import DatabaseLogger

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)  # every getUpdates call would be logged otherwise
log = logging.getLogger("curator-bot")

TG_LIMIT = 4096
HISTORY_MESSAGES = 4


def secret(name: str) -> str:
    if value := os.environ.get(name):
        return value
    env_file = Path.home() / ".secrets" / "common.env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() == name and value.strip():
                return value.strip().strip("'\"")
    return ""


STUDENTS = {s["student_id"]: s for s in lms.get_students()}
ADMIN = secret("CURATOR_ADMIN_CHAT_ID") or secret("ADMIN_CHAT_ID")
INDEX = kb.load_index()
LOGGER = DatabaseLogger()
STATE: dict[int, dict] = {}  # per chat: demo student, level, dialog history (in memory, reset on restart)


def state(chat_id: int) -> dict:
    if chat_id not in STATE:
        STATE[chat_id] = {"student": "s001", "level": STUDENTS["s001"]["level"], "history": []}
    return STATE[chat_id]


def students_keyboard() -> InlineKeyboardMarkup:
    buttons = [InlineKeyboardButton(f"{s['name']} · {s['level']}", callback_data=f"student:{sid}")
               for sid, s in STUDENTS.items()]
    return InlineKeyboardMarkup([buttons[i:i + 2] for i in range(0, len(buttons), 2)])


def level_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(lv, callback_data=f"level:{lv}")
                                  for lv in ("начинающий", "продвинутый")]])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    st = state(update.effective_chat.id)
    st["history"] = []
    await update.message.reply_text(
        "Я ИИ-куратор курса «Промпт-инжиниринг: от первого запроса до агента». Отвечаю про дедлайны, "
        "материалы курса и что почитать дальше, всегда со ссылкой на источник.\n\n"
        "Это демо: курс и студенты вымышленные, дата в LMS 23.10.2026. Выберите, за кого спрашиваете:",
        reply_markup=students_keyboard())


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Просто напишите вопрос, например:\n• Когда дедлайн по ДЗ-2?\n• Что такое эмбеддинги?\n"
        "• Что почитать по RAG?\n\n/student сменить студента\n/level уровень ответа\n/new начать диалог заново\n\n"
        f"Лимит демо: {config.MAX_QUESTIONS_PER_DAY_TELEGRAM} вопросов в день.")


async def student_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("За кого спрашиваете?", reply_markup=students_keyboard())


async def level_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Как объяснять?", reply_markup=level_keyboard())


async def new_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state(update.effective_chat.id)["history"] = []
    await update.message.reply_text("Начали заново. Спрашивайте.")


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    st = state(query.message.chat.id)
    kind, value = query.data.split(":", 1)
    if kind == "student":
        st.update(student=value, level=STUDENTS[value]["level"], history=[])
        text = f"Спрашиваете как {STUDENTS[value]['name']} ({value}), уровень «{st['level']}». Задавайте вопрос."
    else:
        st.update(level=value, history=[])
        text = f"Уровень ответа: {value}."
    await query.edit_message_text(text)


def is_admin(update: Update) -> bool:
    return bool(ADMIN) and str(update.effective_chat.id) == ADMIN


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        await update.message.reply_text("Статистика доступна только администратору.")
        return
    s = LOGGER.get_stats()
    rejected = ", ".join(f"{k}: {v}" for k, v in s["rejected"].items()) or "нет"
    categories = ", ".join(f"{k}: {v}" for k, v in s["by_category"].items()) or "нет"
    answered = s["answered"] or 1
    await update.message.reply_text(
        f"📊 Статистика куратора\n\n"
        f"Запросов: {s['total']} (ответов {s['answered']}, ошибок {s['errors']})\n"
        f"Отклонено: {rejected}\n"
        f"Пользователей: {s['users']}, каналы: {s['by_channel']}\n"
        f"Из кэша: {s['from_cache']} ({s['from_cache'] / answered:.0%})\n"
        f"Время ответа без кэша: среднее {s['avg_ms'] / 1000:.1f} с, 95% быстрее {s['p95_ms'] / 1000:.1f} с\n"
        f"Передано человеку: {s['escalated']}, без источника: {s['no_source']}\n"
        f"Типы вопросов: {categories}\n"
        f"Токены: {s['tokens_in']:,} / {s['tokens_out']:,}, примерно ${s['cost_usd']}")


async def export_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        await update.message.reply_text("Выгрузка доступна только администратору.")
        return
    path = LOGGER.export_csv(config.DB_FILE.parent / f"logs_{datetime.now():%Y%m%d_%H%M}.csv")
    with path.open("rb") as f:
        await update.message.reply_document(f, filename=path.name, caption="Лог запросов, пользователи обезличены")
    path.unlink()


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    st = state(chat_id)
    await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
    try:
        # the chain is synchronous (LangChain + FAISS), keep the event loop free for other chats
        r = await asyncio.to_thread(
            service.handle, update.message.text, student_id=st["student"], level=st["level"], channel="telegram",
            user_ref=str(chat_id), index=INDEX, logger=LOGGER, history=st["history"],
            daily_limit=config.MAX_QUESTIONS_PER_DAY_TELEGRAM)
    except Exception:
        log.exception("answer failed")
        await update.message.reply_text("Не получилось ответить, попробуйте ещё раз через минуту.")
        return
    text = r.text
    if r.status == "answered":
        if r.escalate:
            text += "\n\n👤 Этот вопрос решает куратор-человек: форма «Заявка куратору»."
        if r.sources:
            text += "\n\n📎 " + "; ".join(s["source"] for s in r.sources)
        st["history"] = (st["history"] + [{"role": "user", "content": update.message.text},
                                          {"role": "assistant", "content": r.text}])[-HISTORY_MESSAGES:]
    await update.message.reply_text(text[:TG_LIMIT])


def main() -> None:
    token = secret("CURATOR_BOT_TOKEN")
    if not token:
        raise SystemExit("Нет CURATOR_BOT_TOKEN")
    app = Application.builder().token(token).build()
    for name, fn in [("start", start), ("help", help_cmd), ("student", student_cmd), ("level", level_cmd),
                     ("new", new_cmd), ("stats", stats_cmd), ("export", export_cmd)]:
        app.add_handler(CommandHandler(name, fn))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    log.info("bot started, model %s, db %s", config.CHAT_MODEL, config.DB_FILE)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
