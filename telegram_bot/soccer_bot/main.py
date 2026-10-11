"""
Telegram entry point (long polling). Run with: python -m soccer_bot.main
"""
import asyncio
import logging
from datetime import datetime, timedelta
from html import escape
from zoneinfo import ZoneInfo

import anthropic
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler, ContextTypes,
                          MessageHandler, filters)

from soccer_bot import formatters as fmt
from soccer_bot.commands import COMMANDS, CommandError
from soccer_bot.config import Settings, load_settings
from soccer_bot.data import build_source
from soccer_bot.data.base import DataSource
from soccer_bot.plans import DEFAULT_PATH, Catalog, load_catalog
from soccer_bot.store import Store

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)  # httpx logs the URL, which contains the bot token
logger = logging.getLogger(__name__)

DISPLAY_TZ = ZoneInfo("America/Bogota")
REMINDER_INTERVAL = timedelta(hours=6)
FOUNDER_ID = "fundador"


class Bot:
    def __init__(self, settings: Settings, source: DataSource, catalog: Catalog):
        self.settings = settings
        self.source = source
        self.catalog = catalog
        self.store = Store(settings.db_path, catalog)
        self.router = None
        if settings.nl_enabled:
            from soccer_bot.nl_router import NLRouter
            self.router = NLRouter(source)

    # --- access ---

    def is_admin(self, chat_id: int) -> bool:
        return chat_id in self.settings.admin_chat_ids

    def is_allowed(self, chat_id: int) -> bool:
        s = self.settings
        return s.access_mode == "public" or chat_id in s.allowed_chat_ids or self.is_admin(chat_id)

    def can_use(self, chat_id: int, command: str) -> bool:
        return self.is_admin(chat_id) or self.catalog.can_use(self.store.tier(chat_id), command)

    async def _enter(self, update: Update) -> bool:
        """Register the user and check access. Returns False if the update must be ignored."""
        chat = update.effective_chat
        user = update.effective_user
        await asyncio.to_thread(self.store.touch_user, chat.id, user.username if user else None,
                                user.first_name if user else None)
        if self.is_allowed(chat.id):
            return True
        logger.warning("Rejected chat_id=%s", chat.id)
        await update.effective_message.reply_text(
            f"⛔ No autorizado. Tu chat_id es <code>{chat.id}</code>. "
            "Agrégalo a ALLOWED_CHAT_IDS para usar el bot.",
            parse_mode=ParseMode.HTML,
        )
        return False

    async def _reply(self, update: Update, text: str, **kwargs) -> None:
        await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML, **kwargs)

    # --- data commands ---

    def command_handler(self, name: str):
        command = COMMANDS[name]

        async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            if not await self._enter(update):
                return
            chat_id = update.effective_chat.id
            if not await asyncio.to_thread(self.can_use, chat_id, name):
                return await self._reply(update, fmt.format_locked(name, self.catalog.cheapest_plan_with(name)))

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

    # --- natural language ---

    async def question(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Free-text message: route through Claude, charged to the user's plan or free allowance."""
        if not await self._enter(update):
            return
        if self.router is None:
            return await self._reply(update, "Usa /ayuda para ver los comandos disponibles.")

        chat_id = update.effective_chat.id
        text = update.message.text.strip()
        if len(text) > self.settings.nl_max_chars:
            return await self._reply(
                update, f"✂️ Pregunta muy larga (máx. {self.settings.nl_max_chars} caracteres). No se descontó.")

        exempt = self.is_admin(chat_id)
        reservation = await asyncio.to_thread(self.store.reserve, chat_id, text, exempt)
        if not reservation.allowed:
            return await self._reply(
                update, fmt.format_denied(reservation.reason, reservation.retry_at, DISPLAY_TZ, self.catalog))

        pending = await update.message.reply_text("🤔 Analizando tu pregunta...")
        try:
            result = await asyncio.to_thread(self.router.route, text, lambda tool: self.can_use(chat_id, tool))
        except anthropic.APIError:
            logger.exception("Claude API call failed")
            await asyncio.to_thread(self.store.refund, reservation.question_id)
            return await pending.edit_text("💥 No pude procesar la pregunta. No se descontó.")
        except Exception:
            logger.exception("Question routing failed")
            await asyncio.to_thread(self.store.refund, reservation.question_id)
            return await pending.edit_text("💥 Error consultando los datos. No se descontó.")

        await asyncio.to_thread(
            self.store.complete, reservation.question_id, result.status, result.tool, result.params,
            result.category, result.summary, result.input_tokens, result.output_tokens,
        )
        reply = result.reply
        if result.status == "locked":
            reply = fmt.format_locked(result.tool, self.catalog.cheapest_plan_with(result.tool))
        if not exempt:
            account = await asyncio.to_thread(self.store.account, chat_id)
            reply += "\n\n" + fmt.format_question_footer(account, self.catalog)
        await pending.edit_text(reply, parse_mode=ParseMode.HTML)

    # --- plans & account ---

    def _founder_left(self) -> int:
        offer = self.catalog.founder_offer
        return max(0, offer.slots - self.store.founder_slots_used()) if offer else 0

    def _founder_available(self, chat_id: int) -> bool:
        """Open slots left, or the user already holds the founder price (renewal)."""
        if not self.catalog.founder_offer:
            return False
        current = self.store.active_subscription(chat_id)
        return self._founder_left() > 0 or bool(current and current["founder"])

    def _offer_name(self, offer_id: str) -> str | None:
        if offer_id == FOUNDER_ID and self.catalog.founder_offer:
            plan = self.catalog.plan(self.catalog.founder_offer.plan)
            return f"{plan.name} fundador ({fmt.money(self.catalog.founder_offer.price, self.catalog.currency)})"
        if plan := self.catalog.plan(offer_id):
            return f"{plan.name} ({fmt.money(plan.price, self.catalog.currency)})"
        if pack := self.catalog.packs.get(offer_id):
            return f"{pack.name} ({fmt.money(pack.price, self.catalog.currency)})"
        return None

    async def planes(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._enter(update):
            return
        founder_left = await asyncio.to_thread(self._founder_left)
        founder_ok = await asyncio.to_thread(self._founder_available, update.effective_chat.id)
        c = self.catalog
        buttons = [[InlineKeyboardButton(f"⭐ {p.name} · {fmt.money(p.price, c.currency)}", callback_data=f"buy:{p.id}")]
                   for p in c.plans.values()]
        if founder_ok:
            buttons.insert(0, [InlineKeyboardButton(
                f"🚀 Fundador · {fmt.money(c.founder_offer.price, c.currency)}", callback_data=f"buy:{FOUNDER_ID}")])
        buttons += [[InlineKeyboardButton(f"🔋 {k.name} · {fmt.money(k.price, c.currency)}", callback_data=f"buy:{k.id}")]
                    for k in c.packs.values()]
        await self._reply(update, fmt.format_plans(c, founder_left), reply_markup=InlineKeyboardMarkup(buttons))

    async def buy(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()
        if not await self._enter(update):
            return
        offer_id = query.data.removeprefix("buy:")
        name = self._offer_name(offer_id)
        if name is None:
            return
        if offer_id == FOUNDER_ID and not await asyncio.to_thread(self._founder_available, update.effective_chat.id):
            return await self._reply(update, "Se agotaron los cupos de fundador. Mira /planes")

        user = update.effective_user
        chat_id = update.effective_chat.id
        await self._reply(update, f"🧾 Elegiste <b>{escape(name)}</b>.\n\n{escape(self.settings.payment_instructions)}")

        if offer_id in self.catalog.packs:
            activation = f"/recargar {chat_id} {offer_id}"
        elif offer_id == FOUNDER_ID:
            activation = f"/activar {chat_id} {self.catalog.founder_offer.plan} 1 fundador"
        else:
            activation = f"/activar {chat_id} {offer_id}"
        who = f"@{user.username}" if user and user.username else (user.first_name if user else "")
        for admin_id in self.settings.admin_chat_ids:
            await context.bot.send_message(
                admin_id,
                f"🔔 <b>Solicitud de compra</b>\n{escape(who)} (<code>{chat_id}</code>) quiere "
                f"<b>{escape(name)}</b>.\n\nAl confirmar el pago:\n<code>{activation}</code>",
                parse_mode=ParseMode.HTML,
            )

    async def micuenta(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._enter(update):
            return
        chat_id = update.effective_chat.id
        if self.is_admin(chat_id):
            return await self._reply(update, "👑 Eres administrador: acceso y preguntas ilimitadas.")
        info = await asyncio.to_thread(self.store.account, chat_id)
        await self._reply(update, fmt.format_account(info, self.catalog, DISPLAY_TZ))

    # --- admin ---

    def admin_command(self, func):
        async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            if not self.is_admin(update.effective_chat.id):
                return await update.effective_message.reply_text("Comando solo para administradores.")
            try:
                await func(update, context, context.args or [])
            except ValueError as e:
                await self._reply(update, f"⚠️ {escape(str(e))}")

        return handler

    async def activar(self, update: Update, context: ContextTypes.DEFAULT_TYPE, args: list[str]) -> None:
        if len(args) < 2:
            raise ValueError("Uso: /activar &lt;chat_id&gt; &lt;plan&gt; [periodos] [fundador]")
        chat_id, plan_id = int(args[0]), args[1].lower()
        periods = int(args[2]) if len(args) > 2 and args[2].isdigit() else 1
        founder = FOUNDER_ID in [a.lower() for a in args[2:]]
        if founder:
            offer = self.catalog.founder_offer
            if not offer or offer.plan != plan_id:
                raise ValueError("La oferta de fundador no aplica a ese plan")
            if not await asyncio.to_thread(self._founder_available, chat_id):
                raise ValueError("No quedan cupos de fundador")

        sub = await asyncio.to_thread(self.store.activate, chat_id, plan_id, periods, founder)
        ends = fmt.format_date(datetime.fromisoformat(sub["ends_at_final"]), DISPLAY_TZ)
        plan = self.catalog.plan(plan_id)
        await self._reply(update, f"✅ {escape(plan.name)} activado para <code>{chat_id}</code> hasta {ends}.")
        await context.bot.send_message(
            chat_id, f"✅ ¡Tu plan <b>{escape(plan.name)}</b> está activo hasta el {ends}! Revisa /micuenta",
            parse_mode=ParseMode.HTML,
        )

    async def recargar(self, update: Update, context: ContextTypes.DEFAULT_TYPE, args: list[str]) -> None:
        if len(args) != 2:
            raise ValueError("Uso: /recargar &lt;chat_id&gt; &lt;pack&gt;")
        chat_id, pack_id = int(args[0]), args[1]
        balance = await asyncio.to_thread(self.store.add_pack, chat_id, pack_id)
        await self._reply(update, f"✅ Recarga aplicada a <code>{chat_id}</code>. Saldo: {balance} preguntas.")
        await context.bot.send_message(chat_id, f"🔋 ¡Recarga aplicada! Tienes {balance} preguntas de recarga.")

    async def desactivar(self, update: Update, context: ContextTypes.DEFAULT_TYPE, args: list[str]) -> None:
        if len(args) != 1:
            raise ValueError("Uso: /desactivar &lt;chat_id&gt;")
        affected = await asyncio.to_thread(self.store.deactivate, int(args[0]))
        await self._reply(update, f"Plan desactivado ({affected} periodos afectados).")

    async def suscriptores(self, update: Update, context: ContextTypes.DEFAULT_TYPE, args: list[str]) -> None:
        rows = await asyncio.to_thread(self.store.active_subscribers)
        await self._reply(update, fmt.format_subscribers(rows, self.catalog, DISPLAY_TZ))

    async def uso(self, update: Update, context: ContextTypes.DEFAULT_TYPE, args: list[str]) -> None:
        stats = await asyncio.to_thread(self.store.usage_since, 30)
        await self._reply(update, fmt.format_usage(stats, self.catalog))

    async def sugerencias(self, update: Update, context: ContextTypes.DEFAULT_TYPE, args: list[str]) -> None:
        data = await asyncio.to_thread(self.store.suggestions)
        await self._reply(update, fmt.format_suggestions(data))

    # --- jobs ---

    async def send_reminders(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        due = await asyncio.to_thread(self.store.due_for_reminder, self.catalog.reminder_days_before)
        for sub in due:
            plan = self.catalog.plan(sub["plan"])
            ends = fmt.format_date(datetime.fromisoformat(sub["ends_at"]), DISPLAY_TZ)
            try:
                await context.bot.send_message(
                    sub["chat_id"],
                    f"⏰ Tu plan <b>{escape(plan.name if plan else sub['plan'])}</b> vence el {ends}. "
                    "Renuévalo en /planes para no perder el acceso.",
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                logger.exception("Reminder failed for chat_id=%s", sub["chat_id"])
                continue
            await asyncio.to_thread(self.store.mark_reminded, sub["id"])


def main() -> None:
    settings = load_settings()
    catalog = load_catalog(settings.plans_path or DEFAULT_PATH)
    source = build_source(settings)
    bot = Bot(settings, source, catalog)
    logger.info("Starting bot: data=%s access=%s nl=%s plans=%s", settings.data_source, settings.access_mode,
                settings.nl_enabled, ",".join(catalog.plans))
    if settings.access_mode == "allowlist" and not (settings.allowed_chat_ids or settings.admin_chat_ids):
        logger.warning("ALLOWED_CHAT_IDS is empty: the bot will only reply with each sender's chat_id")
    if not settings.admin_chat_ids:
        logger.warning("ADMIN_CHAT_IDS is empty: nobody will receive purchase requests")

    app = Application.builder().token(settings.telegram_token).build()
    for name in COMMANDS:
        app.add_handler(CommandHandler(name, bot.command_handler(name)))
    app.add_handler(CommandHandler("planes", bot.planes))
    app.add_handler(CommandHandler("micuenta", bot.micuenta))
    app.add_handler(CallbackQueryHandler(bot.buy, pattern=r"^buy:"))
    for name, func in [("activar", bot.activar), ("recargar", bot.recargar), ("desactivar", bot.desactivar),
                       ("suscriptores", bot.suscriptores), ("uso", bot.uso), ("sugerencias", bot.sugerencias)]:
        app.add_handler(CommandHandler(name, bot.admin_command(func)))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot.question))
    app.job_queue.run_repeating(bot.send_reminders, interval=REMINDER_INTERVAL, first=60)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
