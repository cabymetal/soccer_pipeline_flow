"""
Telegram entry point (long polling). Run with: python -m soccer_bot.main
"""
import asyncio
import logging
from zoneinfo import ZoneInfo

import anthropic
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from soccer_bot import formatters as fmt
from soccer_bot.commands import COMMANDS, CommandError
from soccer_bot.config import Settings, load_settings
from soccer_bot.data import build_source
from soccer_bot.data.base import DataSource
from soccer_bot.usage import UsageStore

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)  # httpx logs the URL, which contains the bot token
logger = logging.getLogger(__name__)

DISPLAY_TZ = ZoneInfo("America/Bogota")


class Bot:
    def __init__(self, settings: Settings, source: DataSource):
        self.settings = settings
        self.source = source
        self.store = UsageStore(settings.usage_db_path, settings.nl_max_questions, settings.nl_window_hours)
        self.router = None
        if settings.nl_enabled:
            from soccer_bot.nl_router import NLRouter
            self.router = NLRouter(source)

    def is_admin(self, chat_id: int) -> bool:
        return chat_id in self.settings.admin_chat_ids

    def is_allowed(self, chat_id: int) -> bool:
        s = self.settings
        return s.access_mode == "public" or chat_id in s.allowed_chat_ids or self.is_admin(chat_id)

    async def reject(self, update: Update) -> None:
        chat_id = update.effective_chat.id
        logger.warning("Rejected chat_id=%s", chat_id)
        await update.message.reply_text(
            f"⛔ No autorizado. Tu chat_id es <code>{chat_id}</code>. "
            "Agrégalo a ALLOWED_CHAT_IDS para usar el bot.",
            parse_mode=ParseMode.HTML,
        )

    def command_handler(self, name: str):
        command = COMMANDS[name]

        async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            if not self.is_allowed(update.effective_chat.id):
                return await self.reject(update)

            pending = await update.message.reply_text("⏳ Consultando...")
            try:
                # data access is blocking (Databricks SQL), keep it off the event loop
                text = await asyncio.to_thread(command, self.source, context.args or [])
            except CommandError as e:
                text = f"⚠️ {e}"
            except Exception:
                logger.exception("Command /%s failed", name)
                text = "💥 Error consultando los datos. Intenta de nuevo en un momento."
            await pending.edit_text(text, parse_mode=ParseMode.HTML)

        return handler

    async def cupo(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        if not self.is_allowed(chat_id):
            return await self.reject(update)
        if self.is_admin(chat_id):
            text = "👑 Eres administrador: preguntas ilimitadas."
        else:
            status = await asyncio.to_thread(self.store.check, chat_id)
            text = fmt.format_quota(status.remaining, self.settings.nl_max_questions, status.resets_at, DISPLAY_TZ)
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)

    def admin_handler(self, render):
        async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            if not self.is_admin(update.effective_chat.id):
                return await update.message.reply_text("Comando solo para administradores.")
            text = await asyncio.to_thread(render)
            await update.message.reply_text(text, parse_mode=ParseMode.HTML)

        return handler

    async def question(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Free-text message: route through Claude, subject to the quota."""
        chat_id = update.effective_chat.id
        if not self.is_allowed(chat_id):
            return await self.reject(update)
        if self.router is None:
            return await update.message.reply_text("Usa /ayuda para ver los comandos disponibles.")

        text = update.message.text.strip()
        s = self.settings
        if len(text) > s.nl_max_chars:
            return await update.message.reply_text(
                f"✂️ Pregunta muy larga (máx. {s.nl_max_chars} caracteres). No se descontó de tu cupo."
            )

        exempt = self.is_admin(chat_id)
        status = await asyncio.to_thread(self.store.reserve, chat_id, text, exempt)
        if not status.allowed:
            return await update.message.reply_text(
                "⏳ Ya usaste tus preguntas libres. "
                + fmt.format_quota(0, s.nl_max_questions, status.resets_at, DISPLAY_TZ)
                + "\n\nLos comandos siguen siendo ilimitados: /ayuda",
                parse_mode=ParseMode.HTML,
            )

        pending = await update.message.reply_text("🤔 Analizando tu pregunta...")
        try:
            result = await asyncio.to_thread(self.router.route, text)
        except anthropic.APIError:
            logger.exception("Claude API call failed")
            await asyncio.to_thread(self.store.refund, status.question_id)
            return await pending.edit_text("💥 No pude procesar la pregunta. No se descontó de tu cupo.")
        except Exception:
            logger.exception("Question routing failed")
            await asyncio.to_thread(self.store.refund, status.question_id)
            return await pending.edit_text("💥 Error consultando los datos. No se descontó de tu cupo.")

        await asyncio.to_thread(
            self.store.complete, status.question_id, result.status, result.tool, result.params,
            result.category, result.summary, result.input_tokens, result.output_tokens,
        )
        reply = result.reply
        if not exempt:
            reply += "\n\n" + fmt.format_quota(status.remaining, s.nl_max_questions, status.resets_at, DISPLAY_TZ)
        await pending.edit_text(reply, parse_mode=ParseMode.HTML)


def main() -> None:
    settings = load_settings()
    source = build_source(settings)
    bot = Bot(settings, source)
    logger.info("Starting bot: data=%s access=%s nl=%s", settings.data_source, settings.access_mode,
                settings.nl_enabled)
    if settings.access_mode == "allowlist" and not (settings.allowed_chat_ids or settings.admin_chat_ids):
        logger.warning("ALLOWED_CHAT_IDS is empty: the bot will only reply with each sender's chat_id")

    app = Application.builder().token(settings.telegram_token).build()
    for name in COMMANDS:
        app.add_handler(CommandHandler(name, bot.command_handler(name)))
    app.add_handler(CommandHandler("cupo", bot.cupo))
    app.add_handler(CommandHandler("uso", bot.admin_handler(lambda: fmt.format_usage(bot.store.usage_since(30)))))
    app.add_handler(CommandHandler("sugerencias", bot.admin_handler(lambda: fmt.format_suggestions(bot.store.suggestions()))))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot.question))
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
