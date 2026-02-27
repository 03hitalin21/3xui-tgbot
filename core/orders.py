from typing import Dict, Optional

from core.pricing import apply_discount, calculate_price, order_count


def validate_order_draft(w: Dict, max_bulk_count: int, max_days: int, max_gb: int) -> Optional[str]:
    kind = w.get("kind")
    if kind == "bulk":
        count = int(w.get("count") or 0)
        if count <= 0 or count > max_bulk_count:
            return "invalid_count"
    days = int(w.get("days") or 0)
    if days <= 0 or days > max_days:
        return "invalid_days"
    gb = int(w.get("gb") if w.get("gb") is not None else -1)
    if gb < 0 or gb > max_gb:
        return "invalid_gb"
    return None


def validate_plan_selection(plan_id: int, plans: list[Dict]) -> Optional[Dict]:
    return next((p for p in plans if int(p["id"]) == int(plan_id)), None)


def validate_inbound_selection(inbound_ids: list[int]) -> bool:
    return bool(inbound_ids) and all(int(i) > 0 for i in inbound_ids)


def finalize_order(order_draft: Dict, promo_discount: float, db_module, unlimited_default_limit_ip: int = 1) -> Dict[str, object]:
    gross = calculate_price(order_draft, db_module, unlimited_default_limit_ip)
    count = order_count(order_draft)
    discount = float(promo_discount)
    net = apply_discount(gross, discount)
    auto_renew = bool(order_draft.get("auto_renew", False))
    reset_days = max(order_draft["days"] - 1, 0) if auto_renew else 0
    inbound_ids = order_draft.get("inbound_ids") or [order_draft["inbound_id"]]
    return {
        "count": count,
        "gross": gross,
        "discount": discount,
        "net": net,
        "auto_renew": auto_renew,
        "reset_days": reset_days,
        "inbound_ids": inbound_ids,
    }


def build_order_summary(
    w: Dict,
    gross: float,
    discount: float,
    net: float,
    inbound_pricing_text_fn,
    inbound_pricing_text_list_fn,
    toman_fn,
) -> str:
    inbound_ids = w.get("inbound_ids")
    count = len(inbound_ids) if inbound_ids else w.get("count", 1)
    total_gb = w["gb"] * count
    inbound_label = ", ".join(str(i) for i in inbound_ids) if inbound_ids else str(w["inbound_id"])
    pricing_text = inbound_pricing_text_list_fn(inbound_ids) if inbound_ids else inbound_pricing_text_fn(w["inbound_id"])
    return (
        "🧾 <b>پیش\u200cنمایش سفارش</b>\n"
        f"تعداد کلاینت: <b>{count}</b>\n"
        f"مدت: <b>{w['days']} روز</b>\n"
        f"حجم کل: <b>{total_gb} گیگ</b>\n"
        f"هزینه کل: <b>{toman_fn(net)}</b> (قیمت: {pricing_text})\n"
        f"اینباند: <b>{inbound_label}</b>\n"
        f"نام/پیشوند: <b>{w.get('remark') or w.get('base_remark')}</b>\n"
        f"شروع بعد از اولین استفاده: <b>{'بله' if w['start_after_first_use'] else 'خیر'}</b>\n"
        f"تمدید خودکار: <b>{'بله' if w['auto_renew'] else 'خیر'}</b>\n"
        f"تخفیف: <b>{discount}%</b> | مبلغ ناخالص: <b>{toman_fn(gross)}</b>"
    )
