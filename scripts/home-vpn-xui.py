#!/usr/bin/env python3
"""Conservative 3x-ui VLESS inbound wizard for HOME-VPN."""

import argparse
import base64
import getpass
import json
import os
import pathlib
import secrets
import ssl
import tempfile
import urllib.request
import uuid

try:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
except ImportError:
    X25519PrivateKey = None

CONFIG = pathlib.Path("/etc/home-vpn/config.json")


def ask(prompt, default="", secret=False):
    suffix = f" [{default}]" if default and not secret else ""
    value = (getpass.getpass if secret else input)(f"{prompt}{suffix}: ").strip()
    return value or default


def yes(prompt, default=False):
    value = ask(prompt, "Y/n" if default else "y/N").lower()
    return default if value in ("y/n", "y/N") else value in ("y", "yes", "д", "да")


def api(url, token, method, path, body=None):
    request = urllib.request.Request(url.rstrip("/") + path, data=json.dumps(body).encode() if body is not None else None,
        method=method, headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=20, context=ssl._create_unverified_context()) as response: return json.load(response)


def x25519_pair():
    if X25519PrivateKey is None: raise RuntimeError("Установите python3-cryptography")
    private = X25519PrivateKey.generate(); public = private.public_key()
    raw_private = private.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    raw_public = public.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    encode = lambda raw: base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return encode(raw_private), encode(raw_public)


def build_payload(transport, remark, port, domain="", cert="", key="", path="", fallback_port=0, reality_target="", bootstrap=False):
    settings = {"clients": [], "decryption": "none"}
    if fallback_port: settings["fallbacks"] = [{"dest": int(fallback_port), "xver": 0}]
    if transport == "xhttp":
        if not domain or not cert or not key or not path.startswith("/"): raise ValueError("XHTTP требует домен, TLS-сертификат, ключ и путь /...")
        stream = {"network": "xhttp", "security": "tls", "tlsSettings": {"serverName": domain, "minVersion": "1.2",
            "certificates": [{"certificateFile": cert, "keyFile": key}]}, "xhttpSettings": {"path": path, "mode": "auto"}}
        client = {"id": str(uuid.uuid4()), "flow": "", "email": "bootstrap-client", "limitIp": 1, "totalGB": 0, "expiryTime": 0, "enable": True} if bootstrap else None
        public = {"transport": "xhttp", "path": path, "domain": domain}
    elif transport == "reality":
        target = reality_target or "www.cloudflare.com:443"; server_name = target.rsplit(":", 1)[0]
        private, public_key = x25519_pair(); short_id = secrets.token_hex(8)
        stream = {"network": "tcp", "security": "reality", "tcpSettings": {"acceptProxyProtocol": False, "header": {"type": "none"}},
            "realitySettings": {"show": False, "xver": 0, "dest": target, "serverNames": [server_name], "privateKey": private, "shortIds": [short_id]}}
        client = {"id": str(uuid.uuid4()), "flow": "xtls-rprx-vision", "email": "bootstrap-client", "limitIp": 1, "totalGB": 0, "expiryTime": 0, "enable": True} if bootstrap else None
        public = {"transport": "tcp", "server_name": server_name, "public_key": public_key, "short_id": short_id}
    else: raise ValueError("Транспорт должен быть xhttp или reality")
    if client: settings["clients"].append(client)
    payload = {"up": 0, "down": 0, "total": 0, "remark": remark, "enable": True, "expiryTime": 0, "listen": "", "port": int(port),
        "protocol": "vless", "settings": json.dumps(settings, separators=(",", ":")), "streamSettings": json.dumps(stream, separators=(",", ":")),
        "sniffing": json.dumps({"enabled": True, "destOverride": ["http", "tls", "quic"], "metadataOnly": False, "routeOnly": False}, separators=(",", ":"))}
    return payload, client, public


def update_project_config(panel_url, inbound, transport):
    if not CONFIG.exists(): return
    metadata=CONFIG.stat()
    data = json.loads(CONFIG.read_text(encoding="utf-8")); node = next((item for item in data.get("nodes", []) if item.get("primary")), None)
    if not node: return
    inbound_id = int(inbound["id"]); node["panel_url"] = panel_url.rstrip("/")
    node["inbound_ids"] = sorted(set([int(x) for x in node.get("inbound_ids", [])] + [inbound_id]))
    routes = [item for item in node.get("routes", []) if int(item.get("inbound_id", -1)) != inbound_id]
    routes.append({"name": str(inbound.get("remark") or f"inbound-{inbound_id}"), "inbound_id": inbound_id, "protocol": "vless", "transport": transport, "enabled": True}); node["routes"] = routes
    handle, temporary = tempfile.mkstemp(prefix="config.json.", dir=str(CONFIG.parent), text=True)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as output: json.dump(data, output, ensure_ascii=False, indent=2); output.write("\n")
        os.chmod(temporary, 0o660); os.chown(temporary, metadata.st_uid, metadata.st_gid); os.replace(temporary, CONFIG)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def configure(dry_run=False):
    panel = ask("URL панели/API 3x-ui", "https://127.0.0.1:2053/panel-path")
    token = ask("API-токен 3x-ui", secret=True)
    current = api(panel, token, "GET", "/panel/api/inbounds/list")
    if not current.get("success", True): raise RuntimeError(str(current.get("msg") or "3x-ui вернула ошибку"))
    inbounds = current.get("obj") or []; print(f"Найдено inbound: {len(inbounds)}")
    transport = ask("Транспорт: xhttp или reality", "xhttp").lower()
    if transport not in ("xhttp", "reality"): raise SystemExit("Допустимо: xhttp или reality")
    remark = ask("Название inbound", "HOME private network · " + transport.upper())
    port = int(ask("Порт inbound", "443"))
    if any(int(item.get("port") or 0) == port for item in inbounds): raise SystemExit(f"Порт {port} уже занят существующим inbound")
    bootstrap = yes("Создать одного тестового клиента", False)
    if transport == "xhttp":
        domain = ask("Домен", "my.domain.ru"); cert = ask("TLS fullchain", "/etc/x-ui/cert/fullchain.pem"); key = ask("TLS private key", "/etc/x-ui/cert/privatekey.pem")
        if not pathlib.Path(cert).is_file() or not pathlib.Path(key).is_file(): raise SystemExit("TLS-сертификат или ключ не найден")
        path = ask("Секретный XHTTP-путь", "/" + secrets.token_urlsafe(12)); fallback = int(ask("Порт сайта-заглушки, 0 = отключить", "8181"))
        payload, client, public = build_payload("xhttp", remark, port, domain, cert, key, path, fallback, bootstrap=bootstrap)
    else:
        target = ask("Внешний TLS-target REALITY", "www.cloudflare.com:443")
        payload, client, public = build_payload("reality", remark, port, reality_target=target, bootstrap=bootstrap)
    preview = {"remark": remark, "port": port, "protocol": "vless", **public}
    if client: preview["bootstrap_client_id"] = client["id"]
    print("\nБудет создан новый inbound (существующие не меняются):\n" + json.dumps(preview, ensure_ascii=False, indent=2))
    if dry_run or not yes("Создать inbound в 3x-ui", False): print("Изменения не применены."); return
    result = api(panel, token, "POST", "/panel/api/inbounds/add", payload)
    if not result.get("success"): raise RuntimeError(str(result.get("msg") or "3x-ui отклонила inbound"))
    created = result.get("obj") or {}; inbound_id = created.get("id")
    if not inbound_id:
        latest = api(panel, token, "GET", "/panel/api/inbounds/list").get("obj") or []
        created = next((item for item in latest if item.get("remark") == remark and int(item.get("port") or 0) == port), {})
    if not created.get("id"): raise RuntimeError("Inbound создан, но его ID не удалось определить; проверьте панель")
    update_project_config(panel, created, "xhttp" if transport == "xhttp" else "tcp")
    print(f"Создан inbound ID {created['id']}.")
    if client: print("Тестовый клиент: "+client["id"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("configure", nargs="?"); parser.add_argument("--dry-run", action="store_true"); args = parser.parse_args()
    if os.geteuid() != 0: raise SystemExit("Запустите через sudo")
    configure(args.dry_run)
