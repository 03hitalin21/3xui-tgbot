from typing import Optional, Tuple

from core.models import WalletSummary


def load_low_balance_threshold(low_balance_threshold_env: Optional[str], db_module) -> float:
    if low_balance_threshold_env is not None:
        return float(low_balance_threshold_env)
    return float(db_module.get_setting_float("low_balance_threshold"))


def can_afford(balance: float, amount: float) -> bool:
    return float(balance) >= float(amount)


def get_wallet_summary(tg_id: int, low_balance_threshold: float, db_module) -> WalletSummary:
    agent = db_module.get_agent(tg_id)
    balance = float(agent["balance"]) if agent else 0.0
    return WalletSummary(
        tg_id=tg_id,
        balance=balance,
        low_balance_threshold=float(low_balance_threshold),
        is_low_balance=balance < float(low_balance_threshold),
    )


def validate_topup_request(amount_text: str) -> Tuple[Optional[float], Optional[str]]:
    try:
        amt = float(amount_text)
    except ValueError:
        return None, "مبلغ باید عدد باشد"
    if amt <= 0:
        return None, "مبلغ باید بیشتر از صفر باشد"
    return amt, None


def apply_topup(req_id: int, admin_tg_id: int, db_module) -> float:
    return float(db_module.approve_topup_request(req_id, admin_tg_id))
