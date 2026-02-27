from typing import Dict, List

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup

from bot.constants import LIST_PAGE_SIZE


def kb_cancel() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([["لغو"]], resize_keyboard=True)


def kb_preview() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ تایید", callback_data="wizard:confirm"),
                InlineKeyboardButton("✏️ ویرایش", callback_data="wizard:edit"),
            ],
            [InlineKeyboardButton("لغو", callback_data="wizard:cancel")],
        ]
    )


def kb_low_balance() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("شارژ کیف پول", callback_data="menu:wallet")],
            [InlineKeyboardButton("ثبت درخواست شارژ", callback_data="menu:topup")],
            [InlineKeyboardButton("پشتیبانی", callback_data="menu:support")],
            [InlineKeyboardButton("ادامه", callback_data="menu:home")],
        ]
    )


def kb_broadcast_target() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("همه کاربران", callback_data="broadcast:target:all"),
                InlineKeyboardButton("فقط نمایندگان", callback_data="broadcast:target:agents"),
            ]
        ]
    )


def kb_broadcast_confirm() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ تایید", callback_data="broadcast:confirm"),
                InlineKeyboardButton("✏️ ویرایش", callback_data="broadcast:edit"),
            ],
            [InlineKeyboardButton("❌ لغو", callback_data="broadcast:cancel")],
        ]
    )


def kb_pagination(total_items: int, current_page: int, items_per_page: int, callback_prefix: str) -> InlineKeyboardMarkup:
    total_pages = max((total_items - 1) // items_per_page + 1, 1)
    page = max(1, min(current_page, total_pages))
    buttons = []

    if page > 1:
        buttons.append(InlineKeyboardButton("«", callback_data=f"{callback_prefix}:1"))
        buttons.append(InlineKeyboardButton("‹", callback_data=f"{callback_prefix}:{page - 1}"))
    else:
        buttons.append(InlineKeyboardButton("«", callback_data=f"{callback_prefix}:1"))
        buttons.append(InlineKeyboardButton("‹", callback_data=f"{callback_prefix}:1"))

    start = max(1, page - 1)
    end = min(total_pages, page + 1)
    for p in range(start, end + 1):
        label = f"- {p} -" if p == page else str(p)
        buttons.append(InlineKeyboardButton(label, callback_data=f"{callback_prefix}:{p}"))

    if page < total_pages:
        buttons.append(InlineKeyboardButton("›", callback_data=f"{callback_prefix}:{page + 1}"))
        buttons.append(InlineKeyboardButton("»", callback_data=f"{callback_prefix}:{total_pages}"))
    else:
        buttons.append(InlineKeyboardButton("›", callback_data=f"{callback_prefix}:{total_pages}"))
        buttons.append(InlineKeyboardButton("»", callback_data=f"{callback_prefix}:{total_pages}"))

    return InlineKeyboardMarkup([buttons])


def kb_client_actions(rows: List[Dict], total_items: int, page: int) -> InlineKeyboardMarkup:
    buttons: List[List[InlineKeyboardButton]] = []
    for c in rows:
        cid = c["id"]
        buttons.append(
            [
                InlineKeyboardButton("نمایش کانفیگ", callback_data=f"client_action:{cid}:config"),
                InlineKeyboardButton("QR کد", callback_data=f"client_action:{cid}:qr"),
            ]
        )
        buttons.append(
            [
                InlineKeyboardButton("جزئیات", callback_data=f"client_action:{cid}:details"),
                InlineKeyboardButton("تمدید خودکار", callback_data=f"client_action:{cid}:renew"),
            ]
        )
    if total_items > LIST_PAGE_SIZE:
        buttons.extend(kb_pagination(total_items, page, LIST_PAGE_SIZE, "page:clients").inline_keyboard)
    return InlineKeyboardMarkup(buttons)


def kb_main_menu(role: str) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("📊 داشبورد", callback_data="menu:dashboard")],
        [InlineKeyboardButton("👤 کلاینت‌های من", callback_data="menu:my_clients")],
        [InlineKeyboardButton("➕ ساخت کلاینت", callback_data="menu:create_client")],
        [InlineKeyboardButton("🌐 لیست اینباندها", callback_data="menu:inbounds")],
        [InlineKeyboardButton("📦 پلن‌های پیشنهادی", callback_data="menu:suggested_plans")],
        [InlineKeyboardButton("💰 کیف پول / موجودی", callback_data="menu:wallet")],
        [InlineKeyboardButton("📄 تاریخچه تراکنش", callback_data="menu:tx")],
        [InlineKeyboardButton("🆘 پشتیبانی", callback_data="menu:support")],
    ]
    if role in {"reseller", "agent"}:
        rows.append([InlineKeyboardButton("🎁 معرفی دوستان", callback_data="menu:referral")])
    rows.append([InlineKeyboardButton("⚙️ تنظیمات", callback_data="menu:settings")])
    return InlineKeyboardMarkup(rows)


def kb_create_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🛒 کلاینت تکی", callback_data="create:single")],
            [InlineKeyboardButton("📦 ساخت گروهی", callback_data="create:bulk")],
            [InlineKeyboardButton("🧩 کلاینت چند اینباند", callback_data="create:multi")],
            [InlineKeyboardButton("⬅️ بازگشت", callback_data="menu:home")],
        ]
    )


def kb_settings_menu(admin: bool) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("📍 تنظیم اینباند پیش‌فرض", callback_data="settings:set_default_inbound")],
        [InlineKeyboardButton("🎟 اعمال کد تخفیف", callback_data="settings:promo")],
    ]
    if admin:
        rows.extend(
            [
                [InlineKeyboardButton("🛠 ادمین: ساخت اینباند", callback_data="admin:create_inbound")],
                [InlineKeyboardButton("💵 ادمین: قیمت‌گذاری سراسری", callback_data="admin:set_global_price")],
                [InlineKeyboardButton("🌐 ادمین: قانون اینباند", callback_data="admin:set_inbound_rule")],
                [InlineKeyboardButton("👥 ادمین: نمایندگان", callback_data="admin:resellers")],
                [InlineKeyboardButton("💳 ادمین: شارژ کیف پول", callback_data="admin:charge_wallet")],
            ]
        )
    rows.append([InlineKeyboardButton("⬅️ بازگشت", callback_data="menu:home")])
    return InlineKeyboardMarkup(rows)


def kb_topup_request() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("ثبت درخواست شارژ", callback_data="menu:topup")]])
