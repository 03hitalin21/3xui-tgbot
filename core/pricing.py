from typing import Callable, Dict, List, Optional


def validate_duration(days: int, max_days: int) -> bool:
    return days > 0 and days <= max_days


def validate_gb(gb: int, max_gb: int) -> bool:
    return gb >= 0 and gb <= max_gb


def compute_agent_price(
    tg_id: int,
    inbound_id: int,
    days: int,
    gb: int,
    db_module,
    limit_ip: Optional[int] = None,
    unlimited_default_limit_ip: int = 1,
) -> float:
    rule = db_module.inbound_rule(inbound_id)
    if rule and int(rule["enabled"]) == 0:
        raise ValueError("Selected inbound is disabled by admin")
    if gb == 0:
        limit = limit_ip if limit_ip in {1, 2, 3} else unlimited_default_limit_ip
        return float(db_module.get_setting_float(f"price_unlimited_ip{limit}"))
    ppgb_default = db_module.get_setting_float("price_per_gb")
    ppday_default = db_module.get_setting_float("price_per_day")
    ppgb = float(rule["price_per_gb"]) if rule and rule["price_per_gb"] is not None else ppgb_default
    ppday = float(rule["price_per_day"]) if rule and rule["price_per_day"] is not None else ppday_default
    ppgb_eff = db_module.get_effective_price_per_gb(tg_id, ppgb)
    ppday_eff = db_module.get_effective_price_per_day(tg_id, ppday)
    return round(gb * ppgb_eff + days * ppday_eff, 2)


def calculate_price(
    order_draft: Dict,
    db_module,
    unlimited_default_limit_ip: int = 1,
) -> float:
    tg_id = int(order_draft.get("tg_id", 0))
    count = order_count(order_draft)
    if order_draft["kind"] == "multi":
        total = sum(
            compute_agent_price(
                tg_id,
                inbound_id,
                order_draft["days"],
                order_draft["gb"],
                db_module,
                order_draft.get("limit_ip"),
                unlimited_default_limit_ip,
            )
            for inbound_id in (order_draft.get("inbound_ids") or [])
        )
        return round(total, 2)
    unit = compute_agent_price(
        tg_id,
        order_draft["inbound_id"],
        order_draft["days"],
        order_draft["gb"],
        db_module,
        order_draft.get("limit_ip"),
        unlimited_default_limit_ip,
    )
    return round(unit * count, 2)


def order_count(order_draft: Dict) -> int:
    if order_draft["kind"] == "bulk":
        return int(order_draft["count"])
    if order_draft["kind"] == "multi":
        return len(order_draft.get("inbound_ids") or [])
    return 1


def apply_discount(gross: float, discount: float) -> float:
    return round(gross * (1 - discount / 100), 2)


def inbound_pricing_text(inbound_id: int, db_module) -> str:
    rule = db_module.inbound_rule(inbound_id)
    ppgb = float(rule["price_per_gb"]) if rule and rule["price_per_gb"] is not None else db_module.get_setting_float("price_per_gb")
    ppday = float(rule["price_per_day"]) if rule and rule["price_per_day"] is not None else db_module.get_setting_float("price_per_day")
    return f"{ppgb} برای هر GB + {ppday} برای هر روز"


def inbound_pricing_text_list(inbound_ids: List[int], db_module) -> str:
    return " | ".join(f"{inbound_id}: {inbound_pricing_text(inbound_id, db_module)}" for inbound_id in inbound_ids)
