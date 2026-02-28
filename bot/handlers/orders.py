import json
import time
import uuid
from datetime import datetime
from typing import Dict, List

from telegram import ReplyKeyboardRemove, Update
from telegram.ext import ContextTypes

from core import pricing as core_pricing
from bot.constants import DEFAULT_FLOW, DEFAULT_LIMIT_IP, LIST_PAGE_SIZE, UNLIMITED_DEFAULT_LIMIT_IP
import db
from xui_api import XUIApi, build_client_payload, subscription_link, vless_link
from bot import ui
from bot import utils
from bot.utils import *
from bot.handlers.onboarding import referral_info

async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ctx = context.application.bot_data["ctx"]
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    role = "admin" if is_admin(uid) else get_user_role(uid)
    db.ensure_agent(uid, q.from_user.username or "", q.from_user.full_name or "", role=role)

    data = q.data
    if data == "menu:home":
        await q.message.reply_text("منوی اصلی", reply_markup=main_menu(role))
        return

    if data == "menu:dashboard":
        s = db.agent_stats(uid)
        await q.message.reply_text(
            f"📊 داشبورد\nموجودی: {toman(s['balance'])}\nتعداد کلاینت: {s['clients']}\nفروش امروز: {toman(s['today_sales'])}\nمجموع هزینه: {toman(s['spent'])}"
        )
        return

    if data == "menu:referral":
        await referral_info(q.message, context, uid, role)
        return

    if data == "menu:my_clients":
        total = db.count_clients(uid)
        if total == 0:
            await q.message.reply_text("هنوز کلاینتی ثبت نشده است.")
            return
        page, offset, total_pages = page_bounds(total, 1, LIST_PAGE_SIZE)
        rows = db.list_clients_paged(uid, LIST_PAGE_SIZE, offset)
        lines = [f"👤 Your clients (page {page}/{total_pages}):"]
        for c in rows:
            lines.append(f"• {c['email']} | inbound {c['inbound_id']} | {c['days']}d/{c['gb']}GB")
        await q.message.reply_text(
            "\n".join(lines),
            reply_markup=client_actions_keyboard(rows, total, page),
        )
        return

    if data == "menu:create_client":
        await q.message.reply_text("نوع ساخت را انتخاب کنید:", reply_markup=create_menu())
        return

    if data == "menu:suggested_plans":
        plans = db.list_plan_templates(get_user_role(uid))
        if not plans:
            await q.message.reply_text("هنوز پلن پیشنهادی ثبت نشده است.")
            return
        lines = ["📦 پلن‌های پیشنهادی:"]
        for p in plans[:20]:
            gb_txt = "نامحدود" if int(p["gb"]) == 0 else f"{p['gb']} گیگ"
            lines.append(f"• /useplan {p['id']} - {p['title']} ({p['days']} روز | {gb_txt} | {p['limit_ip']} کاربر)")
        await q.message.reply_text("\n".join(lines))
        return

    if data == "menu:inbounds":
        api = XUIApi()
        try:
            api.login()
            ins = api.list_inbounds()
        except Exception as exc:
            await q.message.reply_text(f"خطا از پنل: {exc}")
            return
        if not ins:
            await q.message.reply_text("اینباندی پیدا نشد.")
            return
        total = len(ins)
        if total == 0:
            await q.message.reply_text("اینباندی پیدا نشد.")
            return
        page, offset, total_pages = page_bounds(total, 1, LIST_PAGE_SIZE)
        lines = [f"🌐 اینباندها (صفحه {page}/{total_pages}):"]
        for i in ins[offset:offset + LIST_PAGE_SIZE]:
            rid = i.get("id")
            remark = i.get("remark", "-")
            port = i.get("port", "-")
            lines.append(f"• ID {rid} | {remark} | port {port}")
        await q.message.reply_text("\n".join(lines), reply_markup=build_pagination(total, page, LIST_PAGE_SIZE, "page:inbounds"))
        return

    if data == "menu:wallet":
        a = db.get_agent(uid)
        msg = [f"💰 موجودی: {toman(a['balance'] if a else 0)}"]
        payment_details = manual_payment_text()
        if payment_details:
            msg.append("")
            msg.append(payment_details)
            msg.append("برای شارژ روی «ثبت درخواست شارژ» بزنید.")
        await q.message.reply_text(
            "\n".join(msg),
            reply_markup=ui.kb_topup_request(),
        )
        return

    if data == "menu:topup":
        context.user_data["flow"] = "topup_amount"
        await q.message.reply_text(
            "مبلغ شارژ را وارد کنید (فقط عدد). مثال: 50000",
            reply_markup=cancel_keyboard(),
        )
        return

    if data == "menu:tx":
        total = db.count_transactions(uid)
        if total == 0:
            await q.message.reply_text("هنوز تراکنشی ثبت نشده است.")
            return
        page, offset, total_pages = page_bounds(total, 1, LIST_PAGE_SIZE)
        tx = db.list_transactions_paged(uid, LIST_PAGE_SIZE, offset)
        lines = [f"📄 تراکنش‌ها (صفحه {page}/{total_pages}):"]
        for t in tx:
            lines.append(f"• {toman(t['amount'])} | {t['reason']} | {time.strftime('%Y-%m-%d %H:%M', time.localtime(t['created_at']))}")
        await q.message.reply_text("\n".join(lines), reply_markup=build_pagination(total, page, LIST_PAGE_SIZE, "page:tx"))
        return

    if data == "menu:support":
        await q.message.reply_text("🆘 پشتیبانی\n" + db.get_setting_text("support_text"))
        return

    if data == "menu:settings":
        await q.message.reply_text("تنظیمات", reply_markup=settings_menu(is_admin(uid)))
        return

    if data == "settings:set_default_inbound":
        context.user_data["flow"] = "set_default_inbound"
        await q.message.reply_text("شناسه اینباند را برای ذخیره به‌عنوان پیش‌فرض ارسال کنید.")
        return

    if data == "settings:promo":
        context.user_data["flow"] = "promo_apply"
        await q.message.reply_text("الان کد تخفیف را ارسال کنید.")
        return

    if data == "create:single":
        if not can_start_wizard(uid):
            await q.message.reply_text("⏳ لطفاً کمی بعد دوباره تلاش کنید.")
            return
        context.user_data["flow"] = "wizard_inbound"
        context.user_data["wizard"] = {"kind": "single", "tg_id": uid}
        logger.info("wizard_start | user=%s | kind=single", uid)
        await q.message.reply_text(
            "➕ ساخت کلاینت تکی\nمرحله ۱/۷: شناسه اینباند را ارسال کنید (یا default).",
            reply_markup=cancel_keyboard(),
        )
        return

    if data == "create:bulk":
        if not can_start_wizard(uid):
            await q.message.reply_text("⏳ لطفاً کمی بعد دوباره تلاش کنید.")
            return
        context.user_data["flow"] = "wizard_inbound"
        context.user_data["wizard"] = {"kind": "bulk", "tg_id": uid}
        logger.info("wizard_start | user=%s | kind=bulk", uid)
        await q.message.reply_text(
            "➕ Bulk client wizard\nStep 1/8: send inbound ID (or type: default).",
            reply_markup=cancel_keyboard(),
        )
        return

    if data == "create:multi":
        if not can_start_wizard(uid):
            await q.message.reply_text("⏳ لطفاً کمی بعد دوباره تلاش کنید.")
            return
        context.user_data["flow"] = "wizard_inbounds"
        context.user_data["wizard"] = {"kind": "multi", "tg_id": uid}
        logger.info("wizard_start | user=%s | kind=multi", uid)
        await q.message.reply_text(
            "➕ Multi-inbound client wizard\nStep 1/7: send inbound IDs separated by comma. Example: 1,2,3",
            reply_markup=cancel_keyboard(),
        )
        return

    if data.startswith("admin:"):
        if not is_admin(uid):
            await q.message.reply_text("این گزینه فقط برای ادمین است.")
            return
        if data == "admin:create_inbound":
            context.user_data["flow"] = "admin_create_inbound"
            await q.message.reply_text("ارسال کنید: <port> <remark> [protocol] [network]")
        elif data == "admin:set_global_price":
            context.user_data["flow"] = "admin_set_global_price"
            await q.message.reply_text("ارسال کنید: <price_per_gb> <price_per_day>\nنمونه: 2000 100")
        elif data == "admin:set_inbound_rule":
            context.user_data["flow"] = "admin_set_inbound_rule"
            await q.message.reply_text("ارسال کنید: <inbound_id> <enabled 1/0> <price_per_gb or -> <price_per_day or ->")
        elif data == "admin:resellers":
            rows = db.list_resellers(limit=50)
            if not rows:
                await q.message.reply_text("نماینده‌ای یافت نشد.")
            else:
                txt = ["👥 Resellers:"]
                for r in rows:
                    txt.append(f"• {r['tg_id']} | {r['username'] or '-'} | bal={r['balance']} | active={r['is_active']}")
                await q.message.reply_text("\n".join(txt[:60]))
        elif data == "admin:charge_wallet":
            context.user_data["flow"] = "admin_charge_wallet"
            await q.message.reply_text("ارسال کنید: <tg_id> <amount>\nنمونه: 123456 50000")
        return

    if data.startswith("wizard:"):
        action = data.split(":", 1)[1]
        if action == "cancel":
            reset_flow(context)
            context.user_data.pop("promo_discount", None)
            await q.message.reply_text(
                "عملیات لغو شد. به منوی اصلی بازگشتید.",
                reply_markup=ReplyKeyboardRemove(),
            )
            await q.message.reply_text("منوی اصلی", reply_markup=main_menu(role))
            return
        if action == "edit":
            context.user_data["flow"] = "wizard_days"
            await q.message.reply_text("مرحله ویرایش: تعداد روزها را ارسال کنید.", reply_markup=cancel_keyboard())
            return
        if action == "confirm":
            await finalize_order(update, context, context.user_data.get("wizard", {}))
            return

    if data.startswith("client_action:"):
        parts = data.split(":")
        if len(parts) != 3:
            await q.message.reply_text("عملیات کلاینت نامعتبر است.")
            return
        client_id = as_int(parts[1])
        action = parts[2]
        if not client_id:
            await q.message.reply_text("کلاینت نامعتبر است.")
            return
        client = db.get_client(uid, client_id)
        if not client:
            await q.message.reply_text("کلاینت پیدا نشد.")
            return
        if action == "config":
            await q.message.reply_text(f"🔐 کانفیگ:\n{client['vless_link']}")
            return
        if action == "qr":
            qr = f"https://api.qrserver.com/v1/create-qr-code/?size=300x300&data={client['vless_link']}"
            await q.message.reply_photo(qr)
            return
        if action == "details":
            created_at = datetime.fromtimestamp(client["created_at"]).strftime("%Y-%m-%d %H:%M")
            if client["start_after_first_use"]:
                expiry_text = "شروع بعد از اولین استفاده"
            else:
                expiry_ts = client["created_at"] + client["days"] * 86400
                expiry_text = datetime.fromtimestamp(expiry_ts).strftime("%Y-%m-%d")
            await q.message.reply_text(
                "ℹ️ جزئیات کلاینت\n"
                f"Remark: {client['email']}\n"
                f"اینباند: {client['inbound_id']}\n"
                f"Subscription: {client['subscription_link']}\n"
                f"مدت: {client['days']} روز | حجم: {client['gb']} گیگ\n"
                f"تاریخ ایجاد: {created_at}\n"
                f"انقضا: {expiry_text}\n"
                f"تمدید خودکار: {'فعال' if client['auto_renew'] else 'غیرفعال'}"
            )
            return
        if action == "renew":
            new_value = not bool(client["auto_renew"])
            db.update_client_auto_renew(uid, client_id, new_value)
            logger.info("client_auto_renew_toggle | user=%s | client=%s | enabled=%s", uid, client_id, new_value)
            await q.message.reply_text(f"✅ تمدید خودکار {'فعال شد' if new_value else 'غیرفعال شد'}.")
            return

    if data.startswith("page:"):
        parts = data.split(":")
        if len(parts) < 3:
            await q.message.reply_text("درخواست صفحه نامعتبر است.")
            return
        page_type = parts[1]
        page_num = as_int(parts[2]) or 1

        if page_type == "clients":
            total = db.count_clients(uid)
            if total == 0:
                await q.message.edit_message_text("هنوز کلاینتی ثبت نشده است.")
                return
            page, offset, total_pages = page_bounds(total, page_num, LIST_PAGE_SIZE)
            rows = db.list_clients_paged(uid, LIST_PAGE_SIZE, offset)
            lines = [f"👤 Your clients (page {page}/{total_pages}):"]
            for c in rows:
                lines.append(f"• {c['email']} | inbound {c['inbound_id']} | {c['days']}d/{c['gb']}GB")
            await q.message.edit_message_text("\n".join(lines))
            await q.message.edit_message_reply_markup(client_actions_keyboard(rows, total, page))
            return

        if page_type == "tx":
            total = db.count_transactions(uid)
            if total == 0:
                await q.message.edit_message_text("هنوز تراکنشی ثبت نشده است.")
                return
            page, offset, total_pages = page_bounds(total, page_num, LIST_PAGE_SIZE)
            rows = db.list_transactions_paged(uid, LIST_PAGE_SIZE, offset)
            lines = [f"📄 تراکنش‌ها (صفحه {page}/{total_pages}):"]
            for t in rows:
                lines.append(f"• {toman(t['amount'])} | {t['reason']} | {time.strftime('%Y-%m-%d %H:%M', time.localtime(t['created_at']))}")
            await q.message.edit_message_text("\n".join(lines))
            await q.message.edit_message_reply_markup(build_pagination(total, page, LIST_PAGE_SIZE, "page:tx"))
            return

        if page_type == "inbounds":
            api = XUIApi()
            try:
                api.login()
                ins = api.list_inbounds()
            except Exception as exc:
                await q.message.edit_message_text(f"خطا از پنل: {exc}")
                return
            total = len(ins)
            if total == 0:
                await q.message.edit_message_text("اینباندی پیدا نشد.")
                return
            page, offset, total_pages = page_bounds(total, page_num, LIST_PAGE_SIZE)
            lines = [f"🌐 اینباندها (صفحه {page}/{total_pages}):"]
            for i in ins[offset:offset + LIST_PAGE_SIZE]:
                rid = i.get("id")
                remark = i.get("remark", "-")
                port = i.get("port", "-")
                lines.append(f"• ID {rid} | {remark} | port {port}")
            await q.message.edit_message_text("\n".join(lines))
            await q.message.edit_message_reply_markup(build_pagination(total, page, LIST_PAGE_SIZE, "page:inbounds"))
            return

    await q.message.reply_text("عملیات ناشناخته است.")

async def text_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ctx = context.application.bot_data["ctx"]
    txt = (update.message.text or "").strip()
    uid = update.effective_user.id
    agent = db.get_agent(uid)
    flow = context.user_data.get("flow")
    w = context.user_data.get("wizard", {})

    if is_cancel(txt):
        reset_flow(context)
        context.user_data.pop("promo_discount", None)
        await update.message.reply_text(
            "عملیات لغو شد. به منوی اصلی بازگشتید.",
            reply_markup=ReplyKeyboardRemove(),
        )
        role = agent["role"] if agent else "buyer"
        await update.message.reply_text("منوی اصلی", reply_markup=main_menu(role))
        return

    if flow == "set_default_inbound":
        iid = as_int(txt)
        if not iid or iid <= 0:
            await update.message.reply_text("شناسه اینباند نامعتبر است")
            return
        db.set_preferred_inbound(uid, iid)
        reset_flow(context)
        await update.message.reply_text(f"اینباند پیش‌فرض روی {iid} تنظیم شد")
        return

    if flow == "promo_apply":
        try:
            disc = db.apply_promo(txt, uid)
        except ValueError as exc:
            await update.message.reply_text(str(exc))
            return
        context.user_data["promo_discount"] = disc
        reset_flow(context)
        await update.message.reply_text(f"کد تخفیف اعمال شد: {disc}% برای سفارش بعدی")
        return

    if flow == "register_agent_experience":
        exp = parse_positive_int(txt)
        if exp is None or exp > 50:
            await update.message.reply_text("سابقه نامعتبر است. یک عدد بین 0 تا 50 ارسال کنید.", reply_markup=cancel_keyboard())
            return
        context.user_data["register_agent_experience"] = exp
        context.user_data["flow"] = "register_agent_history"
        await update.message.reply_text(
            "لطفاً خلاصه سوابق کاری خود را ارسال کنید (حداقل 10 کاراکتر).",
            reply_markup=cancel_keyboard(),
        )
        return

    if flow == "register_agent_history":
        history = txt.strip()
        if len(history) < 10:
            await update.message.reply_text("لطفاً توضیحات کامل‌تری از سابقه کاری خود ارسال کنید.", reply_markup=cancel_keyboard())
            return
        exp = context.user_data.pop("register_agent_experience", 0)
        user = update.effective_user
        db.ensure_agent(user.id, user.username or "", user.full_name or "", role="agent")
        db.set_agent_registration(user.id, True)
        db.set_agent_profile(user.id, exp, history)
        reset_flow(context)
        await update.message.reply_text(
            "✅ ثبت‌نام نماینده انجام شد. اطلاعات شما برای تعیین قیمت اختصاصی ثبت شد.",
            reply_markup=ReplyKeyboardRemove(),
        )
        await update.message.reply_text("منوی اصلی", reply_markup=main_menu(get_user_role(user.id)))
        try:
            await context.bot.send_message(
                chat_id=ADMIN_TELEGRAM_ID,
                text=(
                    "📥 ثبت‌نام نماینده جدید\n"
                    f"ID: {user.id}\n"
                    f"نام کاربری: @{user.username if user.username else '-'}\n"
                    f"نام: {user.full_name or '-'}\n"
                    f"سابقه: {exp} سال\n"
                    f"سوابق: {history}\n"
                    "برای قیمت اختصاصی: /admin/users"
                ),
            )
        except Exception:
            pass
        return

    if flow == "topup_amount":
        try:
            amt = float(txt)
        except ValueError:
            await update.message.reply_text("مبلغ باید عدد باشد", reply_markup=cancel_keyboard())
            return
        if amt <= 0:
            await update.message.reply_text("مبلغ باید بیشتر از صفر باشد", reply_markup=cancel_keyboard())
            return
        req_id = db.create_topup_request(uid, amt)
        context.user_data["flow"] = "topup_receipt"
        context.user_data["topup_request_id"] = req_id
        details = manual_payment_text()
        msg = [f"درخواست #{req_id} ثبت شد."]
        if details:
            msg.append(details)
        msg.append("پس از انتقال، لطفاً رسید پرداخت را به صورت عکس همینجا ارسال کنید.")
        await update.message.reply_text("\n\n".join(msg), reply_markup=ReplyKeyboardRemove())
        return

    # Admin flows
    if flow == "admin_create_inbound":
        if not is_admin(uid):
            await update.message.reply_text("دسترسی ندارید")
            return
        parts = txt.split()
        if len(parts) < 2:
            await update.message.reply_text("فرمت: <port> <remark> [protocol] [network]")
            return
        port = as_int(parts[0])
        if not port:
            await update.message.reply_text("پورت نامعتبر است")
            return
        api = XUIApi()
        try:
            api.login()
            inbound_id = api.create_inbound(port, parts[1], parts[2] if len(parts) > 2 else "vless", parts[3] if len(parts) > 3 else "tcp")
        except Exception as exc:
            await update.message.reply_text(f"ناموفق: {exc}")
            return
        reset_flow(context)
        logger.info("admin_create_inbound | admin=%s | inbound=%s", uid, inbound_id)
        await update.message.reply_text(f"اینباند با شناسه {inbound_id} ساخته شد")
        return

    if flow == "admin_set_global_price":
        if not is_admin(uid):
            await update.message.reply_text("دسترسی ندارید")
            return
        parts = txt.split()
        if len(parts) != 2:
            await update.message.reply_text("فرمت: <price_per_gb> <price_per_day>")
            return
        try:
            pgb = float(parts[0]); pday = float(parts[1])
        except ValueError:
            await update.message.reply_text("مقادیر قیمت باید عددی باشند")
            return
        db.set_setting("price_per_gb", str(pgb))
        db.set_setting("price_per_day", str(pday))
        reset_flow(context)
        logger.info("admin_set_global_price | admin=%s | ppgb=%s | ppday=%s", uid, pgb, pday)
        await update.message.reply_text("قیمت‌گذاری سراسری به‌روزرسانی شد.")
        return

    if flow == "admin_set_inbound_rule":
        if not is_admin(uid):
            await update.message.reply_text("دسترسی ندارید")
            return
        parts = txt.split()
        if len(parts) != 4:
            await update.message.reply_text("فرمت: <inbound_id> <enabled 1/0> <price_per_gb or -> <price_per_day or ->")
            return
        iid = as_int(parts[0]); en = as_int(parts[1])
        if not iid or en not in [0, 1]:
            await update.message.reply_text("inbound_id یا enabled نامعتبر است")
            return
        pgb = None if parts[2] == "-" else float(parts[2])
        pday = None if parts[3] == "-" else float(parts[3])
        db.set_inbound_rule(iid, bool(en), pgb, pday)
        reset_flow(context)
        logger.info("admin_set_inbound_rule | admin=%s | inbound=%s | enabled=%s", uid, iid, en)
        await update.message.reply_text("قانون قیمت/فعال‌سازی اینباند ذخیره شد.")
        return

    if flow == "admin_charge_wallet":
        if not is_admin(uid):
            await update.message.reply_text("دسترسی ندارید")
            return
        parts = txt.split()
        if len(parts) != 2:
            await update.message.reply_text("فرمت: <tg_id> <amount>")
            return
        tid = as_int(parts[0])
        try:
            amount = float(parts[1])
        except ValueError:
            await update.message.reply_text("مبلغ باید عددی باشد")
            return
        if not tid:
            await update.message.reply_text("شناسه کاربر نامعتبر است")
            return
        db.ensure_agent(tid, "", "", role="buyer")
        bal = db.add_balance(tid, amount, "topup.admin", meta=f"by:{uid}")
        reset_flow(context)
        logger.info("admin_charge_wallet | admin=%s | target=%s | amount=%s", uid, tid, amount)
        await update.message.reply_text(f"کیف پول به‌روزرسانی شد. موجودی جدید: {toman(bal)}")
        return

    # Wizard flows
    if flow == "wizard_inbounds":
        inbound_ids = parse_inbound_ids(txt)
        if not inbound_ids:
            await update.message.reply_text(
                "لیست اینباند نامعتبر است. شناسه‌ها را با کاما بفرستید، مثل: 1,2,3",
                reply_markup=cancel_keyboard(),
            )
            return
        w["inbound_ids"] = inbound_ids
        context.user_data["wizard"] = w
        context.user_data["flow"] = "wizard_remark"
        await update.message.reply_text(
            "Step 2/7: send client remark/email. Hint: user123",
            reply_markup=cancel_keyboard(),
        )
        return

    if flow == "wizard_inbound":
        if txt.lower() == "default":
            if not agent or not agent["preferred_inbound"]:
                await update.message.reply_text(
                    "No default inbound set. Send numeric inbound ID.",
                    reply_markup=cancel_keyboard(),
                )
                return
            w["inbound_id"] = int(agent["preferred_inbound"])
        else:
            iid = parse_positive_int(txt)
            if not iid:
                await update.message.reply_text("شناسه اینباند نامعتبر است. فقط عدد ارسال کنید.", reply_markup=cancel_keyboard())
                return
            w["inbound_id"] = iid
        context.user_data["wizard"] = w
        if w["kind"] == "single":
            context.user_data["flow"] = "wizard_remark"
            await update.message.reply_text(
                "Step 2/7: send client remark/email. Hint: user123",
                reply_markup=cancel_keyboard(),
            )
        else:
            context.user_data["flow"] = "wizard_base"
            await update.message.reply_text(
                "Step 2/8: send base remark for bulk. Hint: teamA",
                reply_markup=cancel_keyboard(),
            )
        return

    if flow == "wizard_remark":
        remark = normalize_remark(txt)
        if not remark:
            await update.message.reply_text(
                "Remark must be 2-64 chars using letters, numbers, underscore, or dash only.",
                reply_markup=cancel_keyboard(),
            )
            return
        w["remark"] = remark
        context.user_data["flow"] = "wizard_days"
        await update.message.reply_text("مرحله ۳/۷: تعداد روز را ارسال کنید. مثال: 30", reply_markup=cancel_keyboard())
        return

    if flow == "wizard_base":
        base_remark = normalize_remark(txt)
        if not base_remark:
            await update.message.reply_text(
                "Base remark must be 2-64 chars using letters, numbers, underscore, or dash only.",
                reply_markup=cancel_keyboard(),
            )
            return
        w["base_remark"] = base_remark
        context.user_data["flow"] = "wizard_count"
        await update.message.reply_text("مرحله ۳/۸: تعداد کلاینت را ارسال کنید. مثال: 5", reply_markup=cancel_keyboard())
        return

    if flow == "wizard_count":
        c = parse_positive_int(txt)
        if not c or c > MAX_BULK_COUNT:
            await update.message.reply_text(
                f"تعداد نامعتبر است. عددی بین 1 تا {MAX_BULK_COUNT} وارد کنید.",
                reply_markup=cancel_keyboard(),
            )
            return
        w["count"] = c
        context.user_data["flow"] = "wizard_days"
        await update.message.reply_text("مرحله ۴/۸: تعداد روز را ارسال کنید. مثال: 30", reply_markup=cancel_keyboard())
        return

    if flow == "wizard_days":
        d = parse_positive_int(txt)
        if not d or not core_pricing.validate_duration(d, MAX_DAYS):
            await update.message.reply_text(
                f"روز نامعتبر است. عددی بین 1 تا {MAX_DAYS} وارد کنید.",
                reply_markup=cancel_keyboard(),
            )
            return
        w["days"] = d
        context.user_data["flow"] = "wizard_gb"
        step = "Step 4/7" if w["kind"] in {"single", "multi"} else "Step 5/8"
        await update.message.reply_text(f"{step}: حجم کل (گیگ) را ارسال کنید. مثال: 50", reply_markup=cancel_keyboard())
        return

    if flow == "wizard_gb":
        if txt.strip() in {"0", "نامحدود", "unlimited"}:
            g = 0
        else:
            g = parse_positive_int(txt)
        if g is None or not core_pricing.validate_gb(g, MAX_GB):
            await update.message.reply_text(
                f"حجم نامعتبر است. عددی بین 0 تا {MAX_GB} وارد کنید. (0 = نامحدود)",
                reply_markup=cancel_keyboard(),
            )
            return
        w["gb"] = g
        if g == 0:
            context.user_data["flow"] = "wizard_limit_ip"
            await update.message.reply_text("تعداد کاربر همزمان را انتخاب کنید (1/2/3). پیش‌فرض 1.", reply_markup=cancel_keyboard())
            return
        context.user_data["flow"] = "wizard_start_after_first_use"
        step = "Step 5/7" if w["kind"] in {"single", "multi"} else "Step 6/8"
        await update.message.reply_text(f"{step}: شروع پس از اولین استفاده؟ (y/n)", reply_markup=cancel_keyboard())
        return

    if flow == "wizard_limit_ip":
        v = txt.strip() or "1"
        if v not in {"1", "2", "3"}:
            await update.message.reply_text("فقط یکی از مقادیر 1 یا 2 یا 3 را ارسال کنید.", reply_markup=cancel_keyboard())
            return
        w["limit_ip"] = int(v)
        context.user_data["flow"] = "wizard_start_after_first_use"
        await update.message.reply_text("بعد از اولین استفاده شروع شود؟ (y/n)", reply_markup=cancel_keyboard())
        return

    if flow == "wizard_start_after_first_use":
        v = txt.lower()
        if v not in ["y", "n", "yes", "no"]:
            await update.message.reply_text("لطفاً فقط y یا n ارسال کنید", reply_markup=cancel_keyboard())
            return
        w["start_after_first_use"] = v in ["y", "yes"]
        context.user_data["flow"] = "wizard_auto_renew"
        step = "Step 6/7" if w["kind"] in {"single", "multi"} else "Step 7/8"
        await update.message.reply_text(
            f"{step}: Enable auto-renew? (y/n)\nHint: auto-renew resets one day before expiry.",
            reply_markup=cancel_keyboard(),
        )
        return

    if flow == "wizard_auto_renew":
        v = txt.lower()
        if v not in ["y", "n", "yes", "no"]:
            await update.message.reply_text("لطفاً فقط y یا n ارسال کنید", reply_markup=cancel_keyboard())
            return
        w["auto_renew"] = v in ["y", "yes"]

        try:
            gross = order_total_price(w)
        except ValueError as exc:
            reset_flow(context)
            await update.message.reply_text(str(exc))
            return

        discount = float(context.user_data.get("promo_discount", 0.0))
        net = core_pricing.apply_discount(gross, discount)
        context.user_data["flow"] = "wizard_preview"
        await update.message.reply_text(
            wizard_summary(w, gross, discount, net),
            parse_mode="HTML",
            reply_markup=preview_keyboard(),
        )
        return

    if flow == "wizard_preview":
        v = txt.lower()
        if v in ["n", "no"]:
            reset_flow(context)
            context.user_data.pop("promo_discount", None)
            await update.message.reply_text(
                "عملیات لغو شد. به منوی اصلی بازگشتید.",
                reply_markup=ReplyKeyboardRemove(),
            )
            role = agent["role"] if agent else "buyer"
            await update.message.reply_text("منوی اصلی", reply_markup=main_menu(role))
            return
        if v not in ["y", "yes"]:
            await update.message.reply_text("لطفاً فقط بله یا خیر (yes/no) ارسال کنید", reply_markup=cancel_keyboard())
            return
        await finalize_order(update, context, w)
        return

    await update.message.reply_text("دستور /start را بزنید و از دکمه‌های منو انتخاب کنید.")

async def finalize_order(update: Update, context: ContextTypes.DEFAULT_TYPE, w: Dict):
    effective_message = update.effective_message
    uid = update.effective_user.id
    draft_state = core_orders.finalize_order(
        w,
        float(context.user_data.pop("promo_discount", 0.0)),
        db,
        UNLIMITED_DEFAULT_LIMIT_IP,
    )
    count = int(draft_state["count"])
    gross = float(draft_state["gross"])
    disc = float(draft_state["discount"])
    net = float(draft_state["net"])
    auto_renew = bool(draft_state["auto_renew"])
    reset_days = int(draft_state["reset_days"])
    inbound_ids = list(draft_state["inbound_ids"])

    ag = db.get_agent(uid)
    if not ag or int(ag["is_active"]) != 1:
        reset_flow(context)
        await effective_message.reply_text(
            "Your reseller account is disabled. Contact admin.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    try:
        db.deduct_balance(uid, net, "order.charge", json.dumps({"kind": w["kind"], "inbound": w["inbound_id"]}))
        logger.info("order_deduct | user=%s | amount=%s | kind=%s", uid, net, w["kind"])
    except ValueError:
        reset_flow(context)
        await effective_message.reply_text(
            f"موجودی کافی نیست. مبلغ موردنیاز: {toman(net)}",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    api = XUIApi()
    links: List[str] = []
    subscription_links: List[str] = []
    expiry = expiry_value(w["days"], w["start_after_first_use"])

    try:
        api.login()
        logger.info("api_login | user=%s", uid)
        if w["kind"] == "single":
            inbound = api.get_inbound(w["inbound_id"])
            clients = []
            uidc = str(uuid.uuid4())
            email = w["remark"]
            sub_id = generate_sub_id()
            sub_link = subscription_link(sub_id)
            limit_ip = clamp_limit_ip(int(w.get("limit_ip") or (UNLIMITED_DEFAULT_LIMIT_IP if int(w.get("gb", 0)) == 0 else DEFAULT_LIMIT_IP)))
            clients.append(build_client_payload(
                uidc,
                email,
                expiry,
                int(w["gb"]),
                sub_id,
                str(uid),
                flow=DEFAULT_FLOW,
                reset=reset_days,
                limit_ip=limit_ip,
            ))
            link = vless_link(uidc, inbound, email)
            links.append(link)
            subscription_links.append(sub_link)
            db.save_created_client(
                uid,
                w["inbound_id"],
                email,
                uidc,
                link,
                sub_id,
                sub_link,
                w["days"],
                w["gb"],
                w["start_after_first_use"],
                auto_renew,
            )
            api.add_clients(w["inbound_id"], clients)
        elif w["kind"] == "bulk":
            inbound = api.get_inbound(w["inbound_id"])
            clients = []
            limit_ip = clamp_limit_ip(int(w.get("limit_ip") or (UNLIMITED_DEFAULT_LIMIT_IP if int(w.get("gb", 0)) == 0 else DEFAULT_LIMIT_IP)))
            for i in range(w["count"]):
                uidc = str(uuid.uuid4())
                email = f"{w['base_remark']}_{i+1}"
                sub_id = generate_sub_id()
                sub_link = subscription_link(sub_id)
                clients.append(build_client_payload(
                    uidc,
                    email,
                    expiry,
                    int(w["gb"]),
                    sub_id,
                    str(uid),
                    flow=DEFAULT_FLOW,
                    reset=reset_days,
                    limit_ip=limit_ip,
                ))
                link = vless_link(uidc, inbound, email)
                links.append(link)
                subscription_links.append(sub_link)
                db.save_created_client(
                    uid,
                    w["inbound_id"],
                    email,
                    uidc,
                    link,
                    sub_id,
                    sub_link,
                    w["days"],
                    w["gb"],
                    w["start_after_first_use"],
                    auto_renew,
                )
            api.add_clients(w["inbound_id"], clients)
        else:
            sub_id = generate_sub_id()
            sub_link = subscription_link(sub_id)
            subscription_links.append(sub_link)
            limit_ip = clamp_limit_ip(int(w.get("limit_ip") or (UNLIMITED_DEFAULT_LIMIT_IP if int(w.get("gb", 0)) == 0 else DEFAULT_LIMIT_IP)))
            for inbound_id in inbound_ids:
                inbound = api.get_inbound(inbound_id)
                uidc = str(uuid.uuid4())
                email = w["remark"]
                client = build_client_payload(
                    uidc,
                    email,
                    expiry,
                    int(w["gb"]),
                    sub_id,
                    str(uid),
                    flow=DEFAULT_FLOW,
                    reset=reset_days,
                    limit_ip=limit_ip,
                )
                api.add_clients(inbound_id, [client])
                link = vless_link(uidc, inbound, email)
                links.append(link)
                db.save_created_client(
                    uid,
                    inbound_id,
                    email,
                    uidc,
                    link,
                    sub_id,
                    sub_link,
                    w["days"],
                    w["gb"],
                    w["start_after_first_use"],
                    auto_renew,
                )

        db.create_order(uid, inbound_ids[0], w["kind"], w["days"], w["gb"], count, gross, disc, net, "success")
        logger.info("order_success | user=%s | inbound=%s | count=%s", uid, inbound_ids[0], count)
    except Exception as exc:
        db.add_balance(uid, net, "order.refund", str(exc))
        db.create_order(uid, inbound_ids[0], w["kind"], w["days"], w["gb"], count, gross, disc, net, "failed")
        logger.error("order_failed | user=%s | error=%s", uid, exc)
        reset_flow(context)
        await effective_message.reply_text(
            "⚠️ در حال حاضر امکان ساخت کلاینت وجود ندارد. مبلغ به کیف پول شما برگشت داده شد. لطفاً بعداً دوباره تلاش کنید.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    bal = db.get_agent(uid)["balance"]
    inbound_label = ", ".join(str(i) for i in inbound_ids)
    summary = (
        f"✅ کلاینت(ها) با موفقیت ساخته شد\nنوع: {w['kind']}\nاینباند: {inbound_label}\n"
        f"مدت: {w['days']} روز | حجم: {w['gb']} گیگ | تعداد: {count}\n"
        f"شروع پس از اولین استفاده: {'بله' if w['start_after_first_use'] else 'خیر'} | تمدید خودکار: {'بله' if auto_renew else 'خیر'}\n"
        f"مبلغ ناخالص: {toman(gross)}\nتخفیف: {disc}%\nکسرشده: {toman(net)}\nموجودی: {toman(bal)}"
    )
    configs = "\n".join(links)
    subs = "\n".join(subscription_links)
    sections = [summary]
    if configs:
        sections.append(f"کانفیگ‌ها:\n{configs}")
    if subs:
        sections.append(f"لینک‌های اشتراک:\n{subs}")
    message_text = "\n\n".join(sections)
    if len(message_text) <= 4000:
        await update.effective_message.reply_text(message_text, reply_markup=ReplyKeyboardRemove())
    else:
        await update.effective_message.reply_text(summary, reply_markup=ReplyKeyboardRemove())
        await send_links(update, links)

    # QR preview for single client
    if len(links) == 1:
        qr = f"https://api.qrserver.com/v1/create-qr-code/?size=300x300&data={links[0]}"
        await update.effective_message.reply_photo(qr)

    wallet_summary = core_wallet.get_wallet_summary(uid, LOW_BALANCE_THRESHOLD, db)
    if wallet_summary.is_low_balance:
        await update.effective_message.reply_text(
            "⚠️ موجودی شما کم است. برای جلوگیری از اختلال، کیف پول را شارژ کنید.",
            reply_markup=low_balance_keyboard(),
        )

    reset_flow(context)
