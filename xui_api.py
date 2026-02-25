import json
import os
from typing import Any, Dict, List

import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

BASE_URL = os.getenv("XUI_BASE_URL", "")
USERNAME = os.getenv("XUI_USERNAME", "")
PASSWORD = os.getenv("XUI_PASSWORD", "")
SERVER_HOST = os.getenv("XUI_SERVER_HOST", "")
SUBSCRIPTION_PORT = int(os.getenv("XUI_SUBSCRIPTION_PORT", "2096"))
CONNECT_TIMEOUT = float(os.getenv("XUI_CONNECT_TIMEOUT", "30"))
READ_TIMEOUT = float(os.getenv("XUI_READ_TIMEOUT", "30"))
REQUEST_RETRIES = int(os.getenv("XUI_REQUEST_RETRIES", "2"))


class XUIApi:
    def __init__(self) -> None:
        self.s = requests.Session()
        self.s.verify = False
        retries = Retry(
            total=REQUEST_RETRIES,
            connect=REQUEST_RETRIES,
            read=REQUEST_RETRIES,
            backoff_factor=0.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset(["GET", "POST"]),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retries)
        self.s.mount("http://", adapter)
        self.s.mount("https://", adapter)

    def _request(self, method: str, url: str, **kwargs):
        timeout = kwargs.pop("timeout", (CONNECT_TIMEOUT, READ_TIMEOUT))
        return self.s.request(method, url, timeout=timeout, **kwargs)

    def login(self) -> None:
        r = self._request("POST", f"{BASE_URL}/login", data={"username": USERNAME, "password": PASSWORD})
        if r.status_code != 200:
            raise RuntimeError("x-ui login failed")

    def list_inbounds(self) -> List[Dict[str, Any]]:
        endpoints = [
            ("get", f"{BASE_URL}/panel/api/inbounds/list"),
            ("post", f"{BASE_URL}/panel/api/inbounds/list"),
            ("get", f"{BASE_URL}/panel/api/inbounds/get/all"),
            ("post", f"{BASE_URL}/panel/api/inbounds/get/all"),
            ("get", f"{BASE_URL}/xui/API/inbounds/"),
        ]
        for method, ep in endpoints:
            try:
                r = self._request(method.upper(), ep)
                data = r.json()
                if isinstance(data, list):
                    return data
                if isinstance(data, dict):
                    if data.get("success") and isinstance(data.get("obj"), list):
                        return data["obj"]
                    if isinstance(data.get("obj"), dict) and isinstance(data["obj"].get("inbounds"), list):
                        return data["obj"]["inbounds"]
            except Exception:
                continue
        return []

    def get_inbound(self, inbound_id: int) -> Dict[str, Any]:
        r = self._request("GET", f"{BASE_URL}/panel/api/inbounds/get/{inbound_id}")
        data = r.json()
        if not data.get("success"):
            raise RuntimeError("Failed to fetch inbound")
        obj = data["obj"]
        stream = json.loads(obj.get("streamSettings", "{}"))
        return {
            "port": obj["port"],
            "network": stream.get("network", "tcp"),
            "security": stream.get("security", "none"),
            "reality": stream.get("realitySettings", {}),
            "remark": obj.get("remark", ""),
        }

    def add_clients(self, inbound_id: int, clients: List[dict]) -> None:
        payload = {"id": inbound_id, "settings": json.dumps({"clients": clients})}
        r = self._request("POST", f"{BASE_URL}/panel/api/inbounds/addClient", data=payload)
        if not r.json().get("success"):
            raise RuntimeError(f"Client creation failed: {r.text}")

    def update_clients(self, inbound_id: int, clients: List[dict]) -> None:
        payload = {"id": inbound_id, "settings": json.dumps({"clients": clients})}
        r = self._request("POST", f"{BASE_URL}/panel/api/inbounds/updateClient", data=payload)
        if not r.json().get("success"):
            raise RuntimeError(f"Client update failed: {r.text}")

    def set_client_enabled(self, inbound_id: int, client_payload: dict, enabled: bool) -> None:
        client_payload["enable"] = enabled
        self.update_clients(inbound_id, [client_payload])

    def delete_client(self, inbound_id: int, client_uuid: str) -> None:
        r = self._request("POST", f"{BASE_URL}/panel/api/inbounds/{inbound_id}/delClient/{client_uuid}")
        data = r.json()
        if not data.get("success"):
            raise RuntimeError(f"Client delete failed: {r.text}")

    def last_online(self) -> Dict[str, int]:
        r = self._request("POST", f"{BASE_URL}/panel/api/inbounds/lastOnline")
        data = r.json()
        if not data.get("success"):
            raise RuntimeError("Failed to fetch lastOnline")
        return data.get("obj", {})

    def onlines(self) -> List[str]:
        r = self._request("POST", f"{BASE_URL}/panel/api/inbounds/onlines")
        data = r.json()
        if not data.get("success"):
            raise RuntimeError("Failed to fetch onlines")
        return data.get("obj", [])

    def create_inbound(self, port: int, remark: str, protocol: str = "vless", network: str = "tcp") -> int:
        payload = {
            "up": 0,
            "down": 0,
            "total": 0,
            "remark": remark,
            "enable": True,
            "expiryTime": 0,
            "trafficReset": "never",
            "lastTrafficResetTime": 0,
            "listen": "",
            "port": port,
            "protocol": protocol,
            "settings": json.dumps({"clients": [], "decryption": "none", "encryption": "none"}),
            "streamSettings": json.dumps({"network": network, "security": "none"}),
            "sniffing": json.dumps({"enabled": False, "destOverride": ["http", "tls"]}),
        }
        r = self._request("POST", f"{BASE_URL}/panel/api/inbounds/add", data=payload)
        data = r.json()
        if not data.get("success"):
            raise RuntimeError(f"Failed to create inbound: {r.text}")
        return int(data.get("obj", {}).get("id"))


def vless_link(uid: str, inbound: dict, remark: str) -> str:
    if inbound["security"] == "reality":
        r = inbound["reality"]
        return (
            f"vless://{uid}@{SERVER_HOST}:{inbound['port']}?type=tcp&security=reality&encryption=none"
            f"&pbk={r['settings']['publicKey']}&fp={r['settings'].get('fingerprint', 'chrome')}"
            f"&sni={r['serverNames'][0]}&sid={r['shortIds'][0]}#{remark}"
        )
    return f"vless://{uid}@{SERVER_HOST}:{inbound['port']}?type={inbound['network']}&security={inbound['security']}&encryption=none#{remark}"


def subscription_link(sub_id: str) -> str:
    return f"https://{SERVER_HOST}:{SUBSCRIPTION_PORT}/sub/{sub_id}"


def build_client_payload(
    client_id: str,
    email: str,
    expiry_time_ms: int,
    gb: int,
    sub_id: str,
    tg_id: str,
    *,
    enable: bool = True,
    flow: str = "",
    comment: str = "tg",
    reset: int = 0,
    limit_ip: int | None = None,
) -> dict:
    total_gb_val = max(int(gb), 0)
    if total_gb_val == 0:
        # Unlimited traffic: totalGB must be 0 and limitIp defaults to 1 (unless admin/user picked 2 or 3).
        effective_limit_ip = limit_ip if limit_ip in {1, 2, 3} else 1
    else:
        effective_limit_ip = limit_ip if limit_ip in {1, 2, 3} else 2

    return {
        "id": client_id,
        "email": email,
        "enable": bool(enable),
        "expiryTime": int(expiry_time_ms),
        "totalGB": int(total_gb_val) * 1024**3,
        "flow": flow,
        "limitIp": effective_limit_ip,
        "tgId": str(tg_id),
        "subId": sub_id,
        "comment": comment,
        "reset": int(reset),
    }

