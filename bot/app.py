from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ConversationHandler, MessageHandler, filters

import db
from bot.config import load_config
from bot.constants import BROADCAST_CHOOSE_TARGET, BROADCAST_PREVIEW_CONFIRM, BROADCAST_SEND_MESSAGE
from bot.context import BotContext
from bot import utils


def build_application(config) -> Application:
    return Application.builder().token(utils.BOT_TOKEN).build()


def register_handlers(app) -> None:
    from bot.handlers.onboarding import start, help_cmd, menu_cmd, cancel, referral_cmd
    from bot.handlers.wallet import topup, approve_topup_cmd, photo_flow
    from bot.handlers.admin import (
        register_agent_cmd,
        use_plan_cmd,
        start_broadcast,
        choose_broadcast_target,
        receive_broadcast_message,
        broadcast_preview_action,
        broadcast_cancel,
    )
    from bot.handlers.orders import callback_router, text_flow

    broadcast_conv_handler = ConversationHandler(
        entry_points=[CommandHandler("broadcast", start_broadcast)],
        states={
            BROADCAST_CHOOSE_TARGET: [
                CallbackQueryHandler(choose_broadcast_target, pattern="^broadcast:target:(all|agents)$")
            ],
            BROADCAST_SEND_MESSAGE: [
                MessageHandler(filters.TEXT | filters.PHOTO | filters.Document.ALL, receive_broadcast_message)
            ],
            BROADCAST_PREVIEW_CONFIRM: [
                CallbackQueryHandler(broadcast_preview_action, pattern="^broadcast:(confirm|edit|cancel)$")
            ],
        },
        fallbacks=[CommandHandler("cancel", broadcast_cancel)],
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("menu", menu_cmd))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CommandHandler("topup", topup))
    app.add_handler(CommandHandler("approvetopupid", approve_topup_cmd))
    app.add_handler(CommandHandler("registeragent", register_agent_cmd))
    app.add_handler(CommandHandler("useplan", use_plan_cmd))
    app.add_handler(CommandHandler("referral", referral_cmd))
    app.add_handler(broadcast_conv_handler)
    app.add_handler(CallbackQueryHandler(callback_router))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_flow))
    app.add_handler(MessageHandler(filters.PHOTO, photo_flow))


def main() -> None:
    cfg = load_config()
    utils.apply_runtime_config(cfg)
    db.init_db()
    missing = str(cfg["missing"])
    if missing:
        raise RuntimeError(f"Missing env vars: {missing}")
    utils.LOW_BALANCE_THRESHOLD = utils.load_low_balance_threshold()

    app = build_application(cfg)
    app.bot_data["ctx"] = BotContext(config=cfg)
    register_handlers(app)
    webhook_url = f"{utils.WEBHOOK_BASE_URL}/{utils.WEBHOOK_PATH}"
    app.run_webhook(
        listen=utils.WEBHOOK_LISTEN,
        port=utils.WEBHOOK_PORT,
        url_path=utils.WEBHOOK_PATH,
        webhook_url=webhook_url,
        secret_token=utils.WEBHOOK_SECRET_TOKEN or None,
    )
