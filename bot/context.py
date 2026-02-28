from dataclasses import dataclass
from typing import Any, Dict


@dataclass
class BotContext:
    config: Dict[str, Any]
