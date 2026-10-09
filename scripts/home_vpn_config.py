#!/usr/bin/env python3
"""Shared HOME-VPN configuration, version and topology contracts.

This module intentionally contains no host mutations.  Installers, runtime
services and validation tests import it as the single source of truth.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import tempfile
from urllib.parse import urlparse


SOURCE_ROOT = pathlib.Path(__file__).resolve().parents[1]
INSTALLED_SHARE = pathlib.Path("/usr/local/share/home-vpn")


def _first_existing(*paths: pathlib.Path) -> pathlib.Path:
    return next((path for path in paths if path.is_file()), paths[0])


VERSION_FILE = _first_existing(SOURCE_ROOT / "VERSION", INSTALLED_SHARE / "VERSION")
DEFAULT_CONFIG_FILE = _first_existing(
    SOURCE_ROOT / "config/config.example.json",
    INSTALLED_SHARE / "config.example.json",
)
DEFAULT_MENU_FILE = _first_existing(
    SOURCE_ROOT / "config/vpn-bot-menu.example.json",
    INSTALLED_SHARE / "vpn-bot-menu.example.json",
)
PROJECT_VERSION = VERSION_FILE.read_text(encoding="utf-8").strip()

TOPOLOGY_MODES = {
    1: {
        "name": "central-direct",
        "control_role": "central",
        "edge_placeholder": True,
        "cascade": False,
    },
    2: {
        "name": "central-cascade",
        "control_role": "central",
        "edge_placeholder": True,
        "cascade": True,
    },
    3: {
        "name": "single-foreign",
        "control_role": "single-foreign",
        "edge_placeholder": False,
        "cascade": False,
    },
}

# Every variable consumed by the three Python services belongs here.  Empty
# values are valid for optional integrations; secrets are never supplied with
# a usable default.
ENV_SCHEMAS = {
    "site": {
        "required": {
            "VPN_SHOP_DB", "HOME_VPN_CONFIG", "APP_ENV", "PUBLIC_URL",
            "APP_BIND", "APP_PORT", "TRUST_PROXY", "SESSION_SECRET",
            "API_SECRET", "MAIL_SETTINGS_KEY", "ADMIN_USER",
            "ADMIN_PASSWORD", "ADMIN_ENTRY_PATH",
        },
        "optional": {
            "ASSET_ROOT", "DOWNLOAD_ROOT", "TLS_CERT", "TLS_KEY",
            "TELEGRAM_ADMIN_ID", "TELEGRAM_BOT_URL", "BOT_ADMIN_URL",
            "SMTP_HOST", "SMTP_PORT", "SMTP_USERNAME", "SMTP_PASSWORD",
            "SMTP_STARTTLS", "SMTP_SSL", "IMAP_HOST", "IMAP_PORT",
            "IMAP_USERNAME", "IMAP_PASSWORD", "IMAP_SSL", "MAIL_FROM",
            "MAIL_REPLY_TO", "PAYMENT_URL", "YOOKASSA_SHOP_ID",
            "YOOKASSA_SECRET_KEY", "YOOKASSA_API_URL",
            "YOOKASSA_RETURN_URL", "MTPROTO_INBOUND_ID",
            "MTPROTO_PUBLIC_HOST", "MTPROTO_PUBLIC_PORT",
            "MTPROTO_XUI_URL", "MTPROTO_XUI_TOKEN_ENV",
        },
        "secret": {
            "SESSION_SECRET", "API_SECRET", "MAIL_SETTINGS_KEY",
            "ADMIN_PASSWORD", "SMTP_PASSWORD", "IMAP_PASSWORD",
            "YOOKASSA_SECRET_KEY",
        },
    },
    "bot": {
        "required": {
            "TELEGRAM_BOT_TOKEN", "SHOP_API_URL", "PUBLIC_BASE_URL",
            "SHOP_API_SECRET", "TELEGRAM_ADMIN_ID", "BOT_STATE_FILE",
            "PAYMENT_TIMERS_STATE", "BOT_RUNTIME_CONFIG", "BOT_MENU_CONFIG",
        },
        "optional": {"TELEGRAM_CHANNEL", "TELEGRAM_PROXY_URL", "ADMIN_PANEL_URL"},
        "secret": {"TELEGRAM_BOT_TOKEN", "SHOP_API_SECRET"},
    },
    "bot_admin": {
        "required": {
            "BOT_PANEL_BIND", "BOT_PANEL_PORT", "BOT_PANEL_USER",
            "BOT_PANEL_PASSWORD", "BOT_PANEL_SECRET", "BOT_PANEL_ENTRY_PATH",
            "BOT_PANEL_SECURE_COOKIE",
        },
        "optional": set(),
        "secret": {"BOT_PANEL_PASSWORD", "BOT_PANEL_SECRET"},
    },
}


def load_json(path: pathlib.Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ValueError(f"Не удалось прочитать {path}: {error}") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"Некорректный JSON {path}: строка {error.lineno}, столбец {error.colno}") from error
    if not isinstance(data, dict):
        raise ValueError(f"{path}: верхний уровень должен быть JSON-объектом")
    return data


def default_config() -> dict:
    return load_json(DEFAULT_CONFIG_FILE)


def default_menu() -> dict:
    return load_json(DEFAULT_MENU_FILE)


def validate_project_config(data: dict) -> list[str]:
    errors: list[str] = []
    branding = data.get("branding") or {}
    pricing = data.get("pricing") or {}
    devices = pricing.get("device_monthly") or {}
    nodes = data.get("nodes") or []
    if not str(branding.get("name") or "").strip():
        errors.append("branding.name: обязательное непустое значение")
    if not isinstance(devices, dict) or not devices:
        errors.append("pricing.device_monthly: нужна минимум одна цена")
    else:
        for key, value in devices.items():
            try:
                if int(key) < 1 or int(value) < 0:
                    raise ValueError
            except (TypeError, ValueError):
                errors.append(f"pricing.device_monthly.{key}: ожидается неотрицательное целое число")
    if not isinstance(nodes, list) or not nodes:
        errors.append("nodes: нужна минимум одна нода")
        return errors
    if sum(bool(node.get("primary")) for node in nodes if isinstance(node, dict)) != 1:
        errors.append("nodes: должна быть ровно одна primary-нода")
    seen: set[str] = set()
    for index, node in enumerate(nodes):
        prefix = f"nodes[{index}]"
        if not isinstance(node, dict):
            errors.append(f"{prefix}: ожидается объект")
            continue
        node_id = str(node.get("id") or "")
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", node_id):
            errors.append(f"{prefix}.id: недопустимый идентификатор")
        elif node_id in seen:
            errors.append(f"{prefix}.id: дубликат {node_id}")
        seen.add(node_id)
        if not str(node.get("name") or "").strip():
            errors.append(f"{prefix}.name: обязательное значение")
        panel_url = str(node.get("panel_url") or "")
        if panel_url and urlparse(panel_url).scheme not in ("http", "https"):
            errors.append(f"{prefix}.panel_url: ожидается полный http(s)-URL")
        if node.get("primary") and not node.get("inbound_ids"):
            errors.append(f"{prefix}.inbound_ids: primary-нода должна содержать inbound ID")
    return errors


def validate_menu(data: dict) -> list[str]:
    errors: list[str] = []
    items = data.get("items")
    order = data.get("order")
    if not isinstance(items, dict) or not items:
        return ["items: нужен непустой объект кнопок"]
    if not isinstance(order, list) or set(order) != set(items):
        errors.append("order: должен один раз перечислять все ключи items")
    seen: set[str] = set()
    for key, item in items.items():
        if not isinstance(item, dict):
            errors.append(f"items.{key}: ожидается объект")
            continue
        if not key or key in seen:
            errors.append(f"items.{key}: пустой или повторяющийся ключ")
        seen.add(key)
        if not str(item.get("label") or "").strip():
            errors.append(f"items.{key}.label: обязательное значение")
    return errors


def parse_env(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"строка {number}: ожидается KEY=VALUE")
        key, value = line.split("=", 1)
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            raise ValueError(f"строка {number}: недопустимое имя переменной {key!r}")
        values[key] = value
    return values


def validate_env(component: str, values: dict[str, str], *, strict_unknown: bool = True) -> list[str]:
    if component not in ENV_SCHEMAS:
        return [f"неизвестная env-схема: {component}"]
    schema = ENV_SCHEMAS[component]
    errors = [f"{key}: обязательная переменная отсутствует" for key in sorted(schema["required"] - values.keys())]
    allowed = schema["required"] | schema["optional"]
    if strict_unknown:
        errors.extend(f"{key}: переменная не используется компонентом {component}" for key in sorted(values.keys() - allowed))
    for key in ("APP_PORT", "SMTP_PORT", "IMAP_PORT", "MTPROTO_PUBLIC_PORT", "BOT_PANEL_PORT") & values.keys():
        if values[key] and (not values[key].isdigit() or not 0 <= int(values[key]) <= 65535):
            errors.append(f"{key}: ожидается порт 0..65535")
    if component == "bot_admin" and values.get("BOT_PANEL_BIND") not in ("127.0.0.1", "::1"):
        errors.append("BOT_PANEL_BIND: панель должна слушать loopback")
    if component == "bot" and values.get("SHOP_API_URL"):
        host = urlparse(values["SHOP_API_URL"]).hostname
        if host not in ("127.0.0.1", "::1", "localhost"):
            errors.append("SHOP_API_URL: внутренний API бота должен использовать loopback")
    return errors


def merge_env(
    path: pathlib.Path,
    updates: dict[str, object],
    *,
    component: str | None = None,
    preserve_secrets: set[str] | None = None,
    overwrite_secrets: bool = False,
    mode: int = 0o600,
) -> dict[str, str]:
    """Atomically merge env values, preserving installed secrets by default."""
    current = parse_env(path.read_text(encoding="utf-8")) if path.exists() else {}
    protected = preserve_secrets or set()
    for key, value in updates.items():
        rendered = str(value)
        if key in protected and current.get(key) and not overwrite_secrets:
            continue
        current[key] = rendered
    if component:
        errors = validate_env(component, current, strict_unknown=False)
        if errors:
            raise ValueError("Некорректная env-конфигурация:\n- " + "\n- ".join(errors))
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent, text=True)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as output:
            for key, value in current.items():
                if "\n" in value or "\r" in value:
                    raise ValueError(f"{key}: перевод строки запрещён в env-значении")
                output.write(f"{key}={value}\n")
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return current
