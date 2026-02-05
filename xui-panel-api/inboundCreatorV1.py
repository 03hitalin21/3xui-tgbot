import json
import uuid
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE_URL = "https://141.11.107.4:57796/Z3byxHGOjyxwa4Xekm"
USERNAME = "admin"
PASSWORD = "admin"


def create_inbound_interactive():
    print("=== Create a new Inbound ===")
    
    # General inbound info
    port = int(input("Enter port number: "))
    remark = input("Enter remark/name: ").strip() or f"Inbound-{port}"
    protocol = input("Protocol (vless/trojan/etc.) [vless]: ").strip() or "vless"
    network = input("Network (tcp/ws/etc.) [tcp]: ").strip() or "tcp"
    security = input("Security (none/tls/reality) [none]: ").strip().lower() or "none"

    # External Proxy
    use_proxy = input("Use external proxy? (y/N): ").strip().lower() == "y"
    external_proxy = []
    if use_proxy:
        proxy_dest = input("External Proxy dest (IP/domain): ").strip()
        proxy_port = int(input(f"External Proxy port [{port}]: ").strip() or port)
        proxy_remark = input("External Proxy remark: ").strip() or f"proxy-{proxy_dest}"
        force_tls = input("Force TLS for proxy? (same/true/false) [same]: ").strip() or "same"
        external_proxy.append({
            "dest": proxy_dest,
            "port": proxy_port,
            "remark": proxy_remark,
            "forceTls": force_tls
        })

    # Reality settings
    reality_settings = {}
    if security == "reality":
        target = input("Reality TARGET (domain:port) [www.example.com:443]: ").strip() or "www.example.com:443"
        sni = input("Reality SNI (server name) [www.example.com]: ").strip() or "www.example.com"
        short_id = str(uuid.uuid4())[:8]
        public_key = str(uuid.uuid4())
        reality_settings = {
            "show": False,
            "xver": 0,
            "target": target,
            "serverNames": [sni],
            "privateKey": str(uuid.uuid4()),
            "shortIds": [short_id],
            "settings": {"publicKey": public_key, "fingerprint": "chrome"}
        }

    session = requests.Session()
    session.verify = False

    # Login
    resp = session.post(f"{BASE_URL}/login", data={"username": USERNAME, "password": PASSWORD})
    if resp.status_code != 200:
        print("❌ Login failed")
        return

    # Build payload
    stream_settings = {"network": network, "security": security}
    if security == "reality":
        stream_settings["realitySettings"] = reality_settings
    if use_proxy:
        stream_settings["externalProxy"] = external_proxy
    stream_settings["tcpSettings"] = {"acceptProxyProtocol": False, "header": {"type": "none"}}

    inbound_payload = {
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
        "settings": json.dumps({
            "clients": [{
                "id": str(uuid.uuid4()),
                "flow": "",
                "email": remark,
                "limitIp": 0,
                "totalGB": 0,
                "expiryTime": 0,
                "enable": True,
                "tgId": "",
                "subId": str(uuid.uuid4()),
                "comment": "",
                "reset": 0
            }],
            "decryption": "none",
            "encryption": "none"
        }),
        "streamSettings": json.dumps(stream_settings),
        "sniffing": json.dumps({
            "enabled": False,
            "destOverride": ["http", "tls", "quic", "fakedns"],
            "metadataOnly": False,
            "routeOnly": False
        })
    }

    # Create inbound
    r = session.post(f"{BASE_URL}/panel/api/inbounds/add", data=inbound_payload)
    result = r.json()
    if not result.get("success"):
        print("❌ Failed to create inbound:", r.text)
        return

    inbound_id = result.get("obj", {}).get("id", "unknown")
    print(f"✅ Inbound created! ID: {inbound_id}, Port: {port}, Remark: {remark}")


if __name__ == "__main__":
    create_inbound_interactive()