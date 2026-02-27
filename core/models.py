from dataclasses import dataclass
from typing import Optional


@dataclass
class Agent:
    tg_id: int
    username: str
    full_name: str
    role: str
    balance: float = 0.0
    is_active: int = 1
    preferred_inbound: Optional[int] = None


@dataclass
class Plan:
    id: int
    title: str
    days: int
    gb: int
    limit_ip: int


@dataclass
class OrderDraft:
    kind: str
    tg_id: int
    inbound_id: Optional[int] = None
    inbound_ids: Optional[list[int]] = None
    days: int = 0
    gb: int = 0
    count: int = 1
    remark: Optional[str] = None
    base_remark: Optional[str] = None
    limit_ip: Optional[int] = None
    start_after_first_use: bool = False
    auto_renew: bool = False


@dataclass
class Client:
    inbound_id: int
    email: str
    uuid: str
    sub_id: str
    subscription_url: str
    config_url: str
    days: int
    gb: int
    start_after_first_use: bool
    auto_renew: bool


@dataclass
class WalletSummary:
    tg_id: int
    balance: float
    low_balance_threshold: float
    is_low_balance: bool
