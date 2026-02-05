import json
import uuid
import time
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE_URL = "https://141.11.107.4:57796/Z3byxHGOjyxwa4Xekm"
USERNAME = "admin"
PASSWORD = "admin"
SERVER_HOST = "141.11.107.4"

START_FROM_USE = {
    7: -604800000,
    30: -2592000000,
    90: -7776000000
}


class XUI:
    def __init__(self):
        self.s = requests.Session()
        self.s.verify = False

    def login(self):
        r = self.s.post(
            f"{BASE_URL}/login",
            data={"username": USERNAME, "password": PASSWORD}
        )
        if r.status_code != 200:
            raise Exception("Login failed")
        print("✅ Logged in")

    def get_inbound(self, inbound_id):
        r = self.s.get(f"{BASE_URL}/panel/api/inbounds/get/{inbound_id}")
        data = r.json()
        if not data.get("success"):
            raise Exception("Failed to fetch inbound")

        obj = data["obj"]
        stream = json.loads(obj.get("streamSettings", "{}"))

        return {
            "port": obj["port"],
            "protocol": obj["protocol"],
            "network": stream.get("network", "tcp"),
            "security": stream.get("security", "none"),
            "reality": stream.get("realitySettings", {})
        }

    def add_clients(self, inbound_id, clients):
        payload = {
            "id": inbound_id,
            "settings": json.dumps({"clients": clients})
        }
        r = self.s.post(
            f"{BASE_URL}/panel/api/inbounds/addClient",
            data=payload
        )
        if not r.json().get("success"):
            raise Exception(f"Client creation failed: {r.text}")


def make_vless(uuid_, inbound, remark):
    if inbound["security"] == "reality":
        r = inbound["reality"]
        return (
            f"vless://{uuid_}@{SERVER_HOST}:{inbound['port']}"
            f"?type=tcp"
            f"&security=reality"
            f"&encryption=none"
            f"&pbk={r['settings']['publicKey']}"
            f"&fp={r['settings'].get('fingerprint', 'chrome')}"
            f"&sni={r['serverNames'][0]}"
            f"&sid={r['shortIds'][0]}"
            f"#{remark}"
        )

    return (
        f"vless://{uuid_}@{SERVER_HOST}:{inbound['port']}"
        f"?type={inbound['network']}"
        f"&security={inbound['security']}"
        f"&encryption=none"
        f"#{remark}"
    )


def main():
    print("=== 3x-UI Client Creator ===\n")

    mode = input("Create (S)ingle or (B)ulk? [S/B]: ").strip().lower()
    if mode not in ["s", "b"]:
        print("Invalid choice")
        return

    inbound_id = int(input("Inbound ID: "))
    days = int(input("Plan days (7/30/90): "))
    traffic = int(input("Traffic (GB): "))
    start_use = input("Start from first use? (y/N): ").lower() == "y"

    expiry = START_FROM_USE[days] if start_use else int((time.time() + days * 86400) * 1000)

    xui = XUI()
    xui.login()
    inbound = xui.get_inbound(inbound_id)

    clients = []

    if mode == "s":
        remark = input("Client remark/email: ")
        uid = str(uuid.uuid4())
        clients.append({
            "id": uid,
            "email": remark,
            "enable": True,
            "expiryTime": expiry,
            "totalGB": traffic * 1024**3,
            "flow": "",
            "limitIp": 0,
            "tgId": "",
            "subId": "",
            "comment": "",
            "reset": 0
        })

    else:
        base = input("Base remark (e.g. agent1): ")
        count = int(input("How many clients?: "))

        for i in range(count):
            uid = str(uuid.uuid4())
            clients.append({
                "id": uid,
                "email": f"{base}_{i+1}",
                "enable": True,
                "expiryTime": expiry,
                "totalGB": traffic * 1024**3,
                "flow": "",
                "limitIp": 0,
                "tgId": "",
                "subId": "",
                "comment": "",
                "reset": 0
            })

    xui.add_clients(inbound_id, clients)

    print("\n✅ Created successfully!\n")
    for c in clients:
        print(make_vless(c["id"], inbound, c["email"]))


if __name__ == "__main__":
    main()