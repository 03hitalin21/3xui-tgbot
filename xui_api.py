import json
import os
from typing import Any, Dict, List

import requests

BASE_URL = os.getenv("XUI_BASE_URL", "")
USERNAME = os.getenv("XUI_USERNAME", "")
PASSWORD = os.getenv("XUI_PASSWORD", "")
SERVER_HOST = os.getenv("XUI_SERVER_HOST", "")
SUBSCRIPTION_PORT = int(os.getenv("XUI_SUBSCRIPTION_PORT", "2096"))


class XUIApi:
    def __init__(self) -> None:
        self.s = requests.Session()
        self.s.verify = False

    def login(self) -> None:
        r = self.s.post(f"{BASE_URL}/login", data={"username": USERNAME, "password": PASSWORD}, timeout=20)
        if r.status_code != 200:
            raise RuntimeError("x-ui login failed")

    def list_inbounds(self) -> List[Dict[str, Any]]:
        endpoints = [
            f"{BASE_URL}/panel/api/inbounds/list",
            f"{BASE_URL}/panel/api/inbounds/get/all",
        ]
        for ep in endpoints:
            try:
                r = self.s.get(ep, timeout=20)
                data = r.json()
                if data.get("success") and isinstance(data.get("obj"), list):
                    return data["obj"]
            except Exception:
                continue
        return []

    def get_inbound(self, inbound_id: int) -> Dict[str, Any]:
        r = self.s.get(f"{BASE_URL}/panel/api/inbounds/get/{inbound_id}", timeout=20)
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
        r = self.s.post(f"{BASE_URL}/panel/api/inbounds/addClient", data=payload, timeout=30)
        if not r.json().get("success"):
            raise RuntimeError(f"Client creation failed: {r.text}")

    def update_clients(self, inbound_id: int, clients: List[dict]) -> None:
        payload = {"id": inbound_id, "settings": json.dumps({"clients": clients})}
        r = self.s.post(f"{BASE_URL}/panel/api/inbounds/updateClient", data=payload, timeout=30)
        if not r.json().get("success"):
            raise RuntimeError(f"Client update failed: {r.text}")

    def set_client_enabled(self, inbound_id: int, client_payload: dict, enabled: bool) -> None:
        client_payload["enable"] = enabled
        self.update_clients(inbound_id, [client_payload])

    def delete_client(self, inbound_id: int, client_uuid: str) -> None:
        r = self.s.post(f"{BASE_URL}/panel/api/inbounds/{inbound_id}/delClient/{client_uuid}", timeout=30)
        data = r.json()
        if not data.get("success"):
            raise RuntimeError(f"Client delete failed: {r.text}")

    def last_online(self) -> Dict[str, int]:
        r = self.s.post(f"{BASE_URL}/panel/api/inbounds/lastOnline", timeout=20)
        data = r.json()
        if not data.get("success"):
            raise RuntimeError("Failed to fetch lastOnline")
        return data.get("obj", {})

    def onlines(self) -> List[str]:
        r = self.s.post(f"{BASE_URL}/panel/api/inbounds/onlines", timeout=20)
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
        r = self.s.post(f"{BASE_URL}/panel/api/inbounds/add", data=payload, timeout=30)
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
