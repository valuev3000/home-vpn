#!/usr/bin/env python3
"""Inventory and safely import existing 3x-ui clients into the RU primary node.

Dry-run is the default. Applying changes requires both --apply and the literal
confirmation phrase IMPORT_EXISTING_CLIENTS. Secrets are read only from the
environment variable names declared in nodes.json and are never written to the
report.
"""
import argparse
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ALLOWED = (
    "email", "subId", "password", "auth", "flow", "security", "privateKey",
    "publicKey", "allowedIPs", "preSharedKey", "keepAlive", "forwardedPorts",
    "secret", "adTag", "limitIp", "limitHwid", "totalGB", "expiryTime",
    "enable", "tgId", "group", "comment", "reset", "resetDay", "resetMax",
    "trafficReset", "trafficResetDay", "reverse",
)


def request_json(base, token, method, path, body=None, verify_tls=True, api_key=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    if api_key:
        headers["X-API-Key"] = api_key
    request = urllib.request.Request(
        base.rstrip("/") + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers=headers,
        method=method,
    )
    context = None if verify_tls else ssl._create_unverified_context()
    with urllib.request.urlopen(request, context=context, timeout=30) as response:
        return json.loads(response.read())


def secret(config, key):
    env_name = config.get(key, "")
    value = os.getenv(env_name) if env_name else ""
    if not value:
        raise RuntimeError(f"Не задана переменная окружения {env_name or key}")
    return value


def list_clients(config):
    token = secret(config, "xui_token_env")
    result = request_json(
        config["xui_api_url"], token, "GET", "/panel/api/clients/list",
        verify_tls=config.get("verify_tls", True),
    )
    rows = result.get("obj") or []
    return [row for row in rows if isinstance(row, dict) and row.get("email")]


def detailed_client(config, summary):
    token = secret(config, "xui_token_env")
    email = urllib.parse.quote(summary["email"], safe="")
    result = request_json(
        config["xui_api_url"], token, "GET", "/panel/api/clients/get/" + email,
        verify_tls=config.get("verify_tls", True),
    )
    return (result.get("obj") or {}).get("client") or summary


def identity(client):
    return {
        "email": str(client.get("email") or ""),
        "uuid": str(client.get("uuid") or client.get("id") or ""),
        "subId": str(client.get("subId") or ""),
    }


def same_identity(left, right):
    left, right = identity(left), identity(right)
    if left["uuid"] and right["uuid"] and left["uuid"] != right["uuid"]:
        return False
    if left["subId"] and right["subId"] and left["subId"] != right["subId"]:
        return False
    return True


def import_payload(client, inbound_ids):
    payload = {key: client[key] for key in ALLOWED if key in client}
    if isinstance(payload.get("allowedIPs"), str):
        payload["allowedIPs"] = [x.strip() for x in payload["allowedIPs"].split(",") if x.strip()]
    payload["id"] = str(client.get("uuid") or client.get("id") or "")
    if not payload.get("email") or not payload["id"] or not payload.get("subId"):
        raise RuntimeError("Для импорта обязательны email, UUID/id и subId")
    return {"client": payload, "inboundIds": [int(x) for x in inbound_ids]}


def legacy_url(node, client):
    values = {key: urllib.parse.quote(str(value or ""), safe="") for key, value in identity(client).items()}
    return node["subscription_url_template"].format(**values)


def register_alias(central, email, url, source):
    return request_json(
        central["shop_api_url"], "", "POST", "/api/migration/subscription-alias",
        {"email": email, "subscription_url": url, "source_node": source, "active": True},
        verify_tls=central.get("verify_tls", True),
        api_key=secret(central, "shop_api_secret_env"),
    )


def main():
    parser = argparse.ArgumentParser(description="Безопасная инвентаризация и импорт клиентов 3x-ui")
    parser.add_argument("config", type=Path)
    parser.add_argument("--report", type=Path, default=Path("migration-report.json"))
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    if args.apply and args.confirm != "IMPORT_EXISTING_CLIENTS":
        parser.error("для применения укажите --confirm IMPORT_EXISTING_CLIENTS")

    config = json.loads(args.config.read_text())
    central = config["central"]
    central_token = secret(central, "xui_token_env")
    central_clients = {row["email"]: detailed_client(central, row) for row in list_clients(central)}
    report = {
        "created_at": int(time.time()),
        "mode": "apply" if args.apply else "dry-run",
        "central": central.get("name", "RU-1"),
        "actions": [],
        "summary": {"existing": 0, "imported": 0, "planned_import": 0, "conflict": 0, "failed": 0, "aliases": 0},
    }

    seen = dict(central_clients)
    for node in config.get("nodes", []):
        try:
            remote_rows = list_clients(node)
        except Exception as error:
            report["actions"].append({"node": node.get("name"), "action": "inventory_failed", "error": str(error)})
            report["summary"]["failed"] += 1
            continue
        for row in remote_rows:
            email = row["email"]
            try:
                client = detailed_client(node, row)
                old_url = legacy_url(node, client)
                current = seen.get(email)
                if current and not same_identity(current, client):
                    report["actions"].append({"node": node["name"], "email": email, "action": "conflict", "remote": identity(client), "central": identity(current)})
                    report["summary"]["conflict"] += 1
                    continue
                action = "existing" if current else ("imported" if args.apply else "planned_import")
                if not current and args.apply:
                    result = request_json(
                        central["xui_api_url"], central_token, "POST", "/panel/api/clients/add",
                        import_payload(client, central["inbound_ids"]),
                        verify_tls=central.get("verify_tls", True),
                    )
                    if not result.get("success"):
                        raise RuntimeError(result.get("msg") or "3x-ui отклонил импорт")
                    seen[email] = client
                if node.get("preferred_legacy_link") and (current or args.apply):
                    register_alias(central, email, old_url, node["name"])
                    report["summary"]["aliases"] += 1
                report["actions"].append({"node": node["name"], "email": email, "action": action, "identity": identity(client), "legacy_subscription_url": old_url})
                report["summary"][action] += 1
            except Exception as error:
                report["actions"].append({"node": node.get("name"), "email": email, "action": "failed", "error": str(error)})
                report["summary"]["failed"] += 1

    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report["summary"], ensure_ascii=False))
    print(f"Отчёт: {args.report.resolve()}")
    if report["summary"]["conflict"] or report["summary"]["failed"]:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
