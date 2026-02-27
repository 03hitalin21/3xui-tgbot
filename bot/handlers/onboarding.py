from telegram import ReplyKeyboardRemove, Update
from telegram.ext import ContextTypes

import db
from bot import utils
from bot.utils import *

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ctx = context.application.bot_data["ctx"]
    u = update.effective_user
    role = "admin" if is_admin(u.id) else get_user_role(u.id)
    db.ensure_agent(u.id, u.username or "", u.full_name or "", role=role)
    if context.args:
        code = context.args[0].strip()
        referrer = db.get_agent_by_referral_code(code)
        if referrer and int(referrer["tg_id"]) != u.id:
            db.set_referred_by(u.id, int(referrer["tg_id"]))
    reset_flow(context)
    logger.info("user_start | user=%s | role=%s", u.id, role)
    await update.message.reply_text("به پنل فروش خوش آمدید 👋", reply_markup=main_menu(role))

async def menu_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    role = get_user_role(update.effective_user.id)
    await update.message.reply_text("منوی اصلی", reply_markup=main_menu(role))

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reset_flow(context)
    await update.message.reply_text(
        "عملیات لغو شد. به منوی اصلی بازگشتید.",
        reply_markup=ReplyKeyboardRemove(),
    )
    role = get_user_role(update.effective_user.id)
    await update.message.reply_text("منوی اصلی", reply_markup=main_menu(role))

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "راهنما:\n"
        "/start - شروع\n/menu - منو\n/cancel - لغو\n"
        "ثبت درخواست شارژ از داخل منو (بدون نیاز به دستور)\n/registeragent - ثبت به عنوان نماینده"
    )

def ensure_referral_code(tg_id: int) -> str:
    code = db.get_referral_code(tg_id)
    if code:
        return code
    while True:
        code = generate_referral_code()
        if not db.get_agent_by_referral_code(code):
            db.set_referral_code(tg_id, code)
            return code

async def referral_info(message, context: ContextTypes.DEFAULT_TYPE, tg_id: int, role: str) -> None:
    if not is_referral_agent(role):
        await message.reply_text("برنامه معرفی فقط برای نمایندگان فعال است.")
        return
    code = ensure_referral_code(tg_id)
    stats = db.get_referral_stats(tg_id)
    username = context.bot.username or "your_bot"
    link = f"https://t.me/{username}?start={code}"
    await message.reply_text(
        "🎁 برنامه معرفی\n"
        f"لینک معرفی شما:\n{link}\n\n"
        f"تعداد کاربران معرفی‌شده: {stats['referred_count']}\n"
        f"مجموع کمیسیون دریافتی: {toman(stats['commission_total'])}"
    )

async def referral_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    role = get_user_role(uid)
    await referral_info(update.effective_message, context, uid, role)
