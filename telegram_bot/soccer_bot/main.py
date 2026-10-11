"""
Telegram entry point (long polling). Run with: python -m soccer_bot.main
"""
import asyncio
import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from soccer_bot.commands import COMMANDS, CommandError
from soccer_bot.config import Settings, load_settings
from soccer_bot.data import build_source
from soccer_bot.data.base import DataSource

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)  # httpx logs the URL, which contains the bot token
logger = logging.getLogger(__name__)


def _is_allowed(settings: Settings, chat_id: int) -> bool:
    return chat_id in settings.allowed_chat_ids


def make_handler(name: str, settings: Settings, source: DataSource):
    command = COMMANDS[name]

    async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        if not _is_allowed(settings, chat_id):
            logger.warning("Rejected chat_id=%s", chat_id)
            await update.message.reply_text(
                f"⛔ No autorizado. Tu chat_id es <code>{chat_id}</code>. "
                "Agrégalo a ALLOWED_CHAT_IDS para usar el bot.",
                parse_mode=ParseMode.HTML,
            )
            return

        pending = await update.message.reply_text("⏳ Consultando...")
        try:
            # data access is blocking (Databricks SQL), keep it off the event loop
            text = await asyncio.to_thread(command, source, context.args or [])
        except CommandError as e:
            text = f"⚠️ {e}"
        except Exception:
            logger.exception("Command /%s failed", name)
            text = "💥 Error consultando los datos. Intenta de nuevo en un momento."
        await pending.edit_text(text, parse_mode=ParseMode.HTML)

    return handler


async def unknown_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Usa /ayuda para ver los comandos disponibles.")


def main() -> None:
    settings = load_settings()
    source = build_source(settings)
    logger.info("Starting bot with data source: %s", settings.data_source)
    if not settings.allowed_chat_ids:
        logger.warning("ALLOWED_CHAT_IDS is empty: the bot will only reply with each sender's chat_id")

    app = Application.builder().token(settings.telegram_token).build()
    for name in COMMANDS:
        app.add_handler(CommandHandler(name, make_handler(name, settings, source)))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, unknown_text))
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
