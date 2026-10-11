#!/usr/bin/env python3
"""Install one of the supported HOME-VPN topologies on one or more VPS servers.

The script runs either from an administrator workstation with root SSH access
to every VPS, or directly on the central control VPS with remote access only to
foreign nodes. It never writes secrets to the repository: short-lived answer
files on target VPS use mode 0600 and are removed immediately after installation.
"""

import argparse
import base64
import getpass
import hmac
import hashlib
import ipaddress
import json
import os
import pathlib
import re
import shlex
import subprocess
import tempfile
import urllib.parse
import urllib.request
import uuid

from home_vpn_config import TOPOLOGY_MODES, default_config


ROOT = pathlib.Path(__file__).resolve().parents[1]
KIT_BASE_REMARKS = {
    "REALITY", "Hysteria2", "XHTTP", "VLESS-WS", "Trojan-gRPC",
    "VMess-WS", "Shadowsocks", "TUIC", "AmneziaWG", "AmneziaWG-3.1",
}
CONFIRMATION = "INSTALL HOME VPN TOPOLOGY"
REMOVE_CONFIRMATION = "DELETE HOME VPN TOPOLOGY"
DEFAULT_KIT_URL = "https://raw.githubusercontent.com/itsnotkubrick/3X-UI_KIT/v1.2/scripts/3x-ui.sh"
DEFAULT_KIT_SHA256 = "2d7a0f0dcc26ea4f8bab3d60e1900e7046a9673a88fef85c03630502120961f0"


def run(arguments, *, input_text=None, capture=False, timeout=None, check=True):
    return subprocess.run(
        [str(item) for item in arguments], input=input_text, text=True,
        capture_output=capture, timeout=timeout, check=check,
    )


def ssh_command(target, arguments, *, input_text=None, capture=False, timeout=None):
    """Run an argv-safe command through OpenSSH's unavoidable remote shell."""
    if target == "local":
        return run(arguments, input_text=input_text, capture=capture, timeout=timeout)
    command = shlex.join([str(item) for item in arguments])
    return run(
        ["ssh", "--", target, command], input_text=input_text,
        capture=capture, timeout=timeout,
    )


def ssh_script(target, script, *, capture=False, timeout=None):
    return ssh_command(
        target, ["bash", "-s"], input_text=script,
        capture=capture, timeout=timeout,
    )


def ssh_python(target, code, arguments=(), *, capture=False):
    return ssh_command(
        target, ["python3", "-", *arguments],
        input_text=code, capture=capture,
    )


def ask(prompt, default="", secret=False):
    suffix = f" [{default}]" if default and not secret else ""
    value = (getpass.getpass if secret else input)(f"{prompt}{suffix}: ").strip()
    return value or default


def ask_validated(prompt, default, validator, secret=False):
    while True:
        try:return validator(ask(prompt,default,secret))
        except (SystemExit,ValueError) as error:print(error)


def ask_int(prompt, default, minimum=1, maximum=None):
    def validate(value):
        if not str(value).isdigit() or int(value)<minimum or (maximum is not None and int(value)>maximum):
            interval=f"от {minimum} до {maximum}" if maximum is not None else f"не меньше {minimum}"
            raise ValueError(f"{prompt}: требуется число {interval}")
        return int(value)
    return ask_validated(prompt,str(default),validate)


def ask_yes_no(prompt, default=True):
    marker="Y/n" if default else "y/N"
    while True:
        value=ask(f"{prompt} [{marker}]").strip().lower()
        if not value:return default
        if value in ("y","yes"):return True
        if value in ("n","no"):return False
        print("Enter y or n")


def valid_ip(value, label):
    try:
        return str(ipaddress.ip_address(value))
    except ValueError as error:
        raise SystemExit(f"{label}: нужен IP-адрес, получено {value!r}") from error


def valid_country(value, label="Код страны"):
    value = str(value or "").strip().upper()
    if not re.fullmatch(r"[A-Z]{2,3}", value):
        raise SystemExit(
            f"{label}: нужны две или три латинские буквы, например EE или EST"
        )
    return value


def ask_country(prompt, default="DE"):
    while True:
        try:
            return valid_country(ask(prompt, default), prompt)
        except SystemExit as error:
            print(error)


def safe_id(value):
    value = re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-")
    if not value:
        raise SystemExit("ID ноды не может быть пустым")
    return value[:32]


def valid_ssh_target(value, label="SSH target"):
    """Reject values that OpenSSH could interpret as local options."""
    value = str(value or "")
    if not value or value.startswith("-") or not re.fullmatch(r"[A-Za-z0-9_.@:%\[\]-]+", value):
        raise SystemExit(f"{label}: недопустимый SSH-адрес {value!r}")
    return value


def valid_public_url(value, label="public_url"):
    try:parsed=urllib.parse.urlparse(str(value or ""));port=parsed.port
    except ValueError as error:raise SystemExit(f"{label}: некорректный URL") from error
    host=parsed.hostname or ""
    try:ipaddress.ip_address(host);valid_host=True
    except ValueError:valid_host=bool(re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?",host))
    if parsed.scheme not in ("http","https") or not valid_host or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("","/"):
        raise SystemExit(f"{label}: нужен URL без credentials, path, query и fragment")
    if port is not None and not 1 <= port <= 65535:raise SystemExit(f"{label}: недопустимый порт")
    return parsed


def valid_installer_url(value):
    parsed=urllib.parse.urlparse(str(value or ""))
    if parsed.scheme!="https" or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("Нужен полный HTTPS URL без credentials")
    return str(value)


def valid_public_url_value(value, label="public_url"):
    valid_public_url(value,label)
    return str(value).rstrip("/")


def valid_host(value, label, dns_only=False):
    value=str(value or "").strip()
    valid_dns=bool(re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?",value))
    valid_ip_address=False
    if not dns_only:
        try:ipaddress.ip_address(value);valid_ip_address=True
        except ValueError:pass
    if "://" in value or not (valid_dns or valid_ip_address):
        suffix="DNS-имя без схемы и пути" if dns_only else "IP или DNS-имя без схемы и пути"
        raise ValueError(f"{label}: нужен {suffix}")
    return value


def valid_sha256(value):
    value=str(value or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}",value):raise ValueError("SHA-256 должен содержать 64 hex-символа")
    return value


def valid_password(value, label):
    if len(value)<10:raise ValueError(f"{label} должен содержать минимум 10 символов")
    return value


def valid_telegram_token(value):
    if not re.fullmatch(r"[0-9]{6,12}:[A-Za-z0-9_-]{30,}",str(value or "")):
        raise ValueError("Неверный формат Telegram Bot Token")
    return value


def valid_telegram_id(value):
    if not str(value).isdigit() or int(value)<=0:raise ValueError("Telegram ID должен состоять только из цифр")
    return str(value)


def valid_telegram_channel(value):
    if value and not re.fullmatch(r"@[A-Za-z0-9_]{5,32}",value):
        raise ValueError("Укажите канал в формате @channel или оставьте пустым")
    return value


def valid_tls_path(value, label):
    value=str(value or "")
    path=pathlib.PurePosixPath(value)
    if not value.startswith("/") or ".." in path.parts or not re.fullmatch(r"[A-Za-z0-9_./-]+",value):
        raise SystemExit(f"{label}: нужен безопасный абсолютный путь")
    return value


def node_wizard(index, mode, central_address=None, existing_nodes=None, forbidden_ssh=None):
    existing_nodes=existing_nodes or []
    used_ids={node["id"] for node in existing_nodes}
    used_ssh={node["ssh_host"] for node in existing_nodes}|set(forbidden_ssh or ())
    used_addresses={node["address"] for node in existing_nodes}
    used_ingress={node.get("ingress_address") for node in existing_nodes}

    def unique(validator, used, message):
        def validate(value):
            result=validator(value)
            if result in used:raise ValueError(message)
            return result
        return validate

    country = ask_country(f"Нода {index}: код страны")
    node_id = ask_validated(f"Нода {index}: ID","foreign-"+str(index),unique(safe_id,used_ids,"ID ноды уже используется"))
    name = ask(f"Нода {index}: название", f"Зарубежная VPN-нода {index}")
    ssh_host = ask_validated(f"Нода {index}: SSH-логин и адрес (например root@203.0.113.20)",f"root@vpn-node-{index}.example.com",unique(lambda value:valid_ssh_target(value,f"Нода {index}: SSH"),used_ssh,"Эта VPS уже используется в топологии"))
    address = ask_validated(f"Нода {index}: адрес в защищённой межсерверной сети",f"10.10.0.{10+index}",unique(lambda value:valid_ip(value,"Адрес ноды"),used_addresses,"Этот адрес ноды уже используется"))
    if mode == 2:
        ingress = ask_validated(f"Нода {index}: отдельный входной IP на центральной ноде",central_address,unique(lambda value:valid_ip(value,"Central ingress"),used_ingress,"Этот входной IP уже используется"))
        public_host = ask_validated(f"Нода {index}: адрес или домен в клиентских ссылках",ingress,lambda value:valid_host(value,"Адрес в клиентских ссылках"))
    else:
        ingress = ""
        public_host = ask_validated(f"Нода {index}: публичный IP или домен",address,lambda value:valid_host(value,"Публичный адрес ноды"))
    sni = ask_validated(f"Нода {index}: SNI маскировки","dl.google.com",lambda value:valid_host(value,"SNI",True))
    cert = ask_validated(f"Нода {index}: TLS-сертификат на VPS (необязательно)","",lambda value:valid_tls_path(value,"TLS-сертификат ноды") if value else "")
    key = ask_validated(f"Нода {index}: TLS-ключ на VPS (необязательно)","",lambda value:valid_tls_path(value,"TLS-ключ ноды")) if cert else ""
    return {
        "id": node_id, "name": name, "country": country, "flag": "🌐",
        "ssh_host": ssh_host, "address": address, "public_host": public_host,
        "ingress_address": ingress, "sni": sni, "cert": cert, "key": key,
    }


def print_architecture_table():
    print("Выберите архитектуру:")
    print(" 1 — управляющая роль: центральная VPS (сайт и панели); бот — на выбранной VPS")
    print("     VPN: клиент → иностранная VPS; заглушка: только иностранные ноды")
    print(" 2 — управляющая роль: центральная VPS + cascade; бот — на выбранной VPS")
    print("     VPN: клиент → центральная VPS → иностранная VPS; заглушка: только иностранные ноды")
    print(" 3 — управляющая роль: единственная иностранная VPS выполняет все роли")
    print("     VPN: клиент → единственная VPS; заглушка: запрещена")


def wizard(forced_mode=None, local_control_node=False):
    print_architecture_table()
    mode = int(forced_mode) if forced_mode is not None else ask_int("Режим",1,1,3)
    if mode not in TOPOLOGY_MODES:raise SystemExit("Режим должен быть 1, 2 или 3")
    installer_url = ask_validated("HTTPS URL проверенного установщика 11 подключений",DEFAULT_KIT_URL,valid_installer_url)
    installer_sha256 = ask_validated("SHA-256 установщика",DEFAULT_KIT_SHA256,valid_sha256)
    if mode in (1, 2):
        if local_control_node:
            site_ssh = "local"
            print("Управляющая VPS: текущий сервер (SSH к самому себе не требуется)")
        else:
            site_ssh = ask_validated("Root SSH центральной VPS","root@central.example.com",lambda value:valid_ssh_target(value,"SSH центральной VPS"))
        site_address = ask_validated("Адрес центральной VPS в служебной сети","10.10.0.10",lambda value:valid_ip(value,"Адрес центральной VPS"))
        count = ask_int("Количество зарубежных нод", 1)
        nodes = []
        for index in range(1,count+1):
            nodes.append(node_wizard(index,mode,site_address,nodes,{site_ssh}))
        emergency_index = ask_int("Номер ноды для аварийного MTProto",1,1,len(nodes))
    else:
        nodes = [node_wizard(1,mode)]
        site_ssh = nodes[0]["ssh_host"]
        site_address = nodes[0]["address"]
        emergency_index = 1
    while True:
        public_url = ask_validated("Публичный HTTPS URL сайта (домен должен указывать на центральную VPS)","",lambda value:valid_public_url_value(value,"Публичный URL сайта"))
        tls_prompt="TLS-сертификат сайта на VPS (Enter = получить Let's Encrypt автоматически)" if mode in (1,2) else "TLS-сертификат сайта на VPS"
        tls_cert = ask_validated(tls_prompt,"",lambda value:valid_tls_path(value,"TLS-сертификат сайта") if value else "")
        tls_key = ask_validated("TLS-ключ сайта на VPS (необязательно)","",lambda value:valid_tls_path(value,"TLS-ключ сайта")) if tls_cert else ""
        parsed_public=valid_public_url(public_url)
        try:ipaddress.ip_address(parsed_public.hostname or "");public_is_ip=True
        except ValueError:public_is_ip=False
        if mode in (1,2) and parsed_public.scheme=="https" and not tls_cert and public_is_ip:
            print("Для автоматического Let's Encrypt укажите HTTPS URL с доменом, а не IP-адресом")
            continue
        break
    bot_is_enabled=ask_yes_no("Установить Telegram-бота?",True)
    bot_placement="control"
    if bot_is_enabled and mode in (1,2):
        print("Где установить Telegram-бота, bot-admin и Telegram egress:")
        print("  0 — центральная управляющая VPS")
        for index,node in enumerate(nodes,1):print(f"  {index} — {node['name']} ({node['id']}, {node['country']})")
        while True:
            selected=ask("Номер VPS для Telegram-бота","0")
            if selected.isdigit() and 0<=int(selected)<=len(nodes):break
            print(f"Нужно число от 0 до {len(nodes)}")
        if int(selected):
            bot_placement=nodes[int(selected)-1]["id"]
            if nodes[emergency_index-1]["id"]!=bot_placement and ask_yes_no("Использовать эту же VPS для аварийного MTProto?",True):
                emergency_index=int(selected)
    topology = {
        "mode": mode,
        "protocol_installer": {"url": installer_url, "sha256": installer_sha256},
        "telegram_bot": {"enabled": bot_is_enabled,"placement":bot_placement},
        "telegram_egress": {"listen": "127.0.0.1", "port": 18080, "strategy": "leastPing", "probe_interval": "30s"},
        "site": {"ssh_host": site_ssh, "address": site_address, "public_url": public_url,
                 "tls_cert": tls_cert, "tls_key": tls_key},
        "nodes": nodes, "emergency_node": nodes[emergency_index - 1]["id"],
    }
    validate_topology(topology)
    return topology


def validate_topology(topology):
    mode = int(topology.get("mode", 0))
    if mode not in TOPOLOGY_MODES:
        raise SystemExit("topology.mode должен быть 1, 2 или 3")
    xui_mode = topology.get("xui_install_mode", "fresh")
    if xui_mode not in ("fresh", "preserve"):
        raise SystemExit("xui_install_mode должен быть fresh или preserve")
    if xui_mode == "preserve" and mode != 3:
        raise SystemExit("xui_install_mode=preserve поддерживается только для mode 3")
    installer = topology.get("protocol_installer") or {}
    if urllib.parse.urlparse(str(installer.get("url") or "")).scheme != "https":
        raise SystemExit("protocol_installer.url должен быть HTTPS-URL")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", str(installer.get("sha256") or "")):
        raise SystemExit("protocol_installer.sha256 должен содержать 64 hex-символа")
    bot = topology.get("telegram_bot") or {}
    if "enabled" in bot and not isinstance(bot["enabled"], bool):
        raise SystemExit("telegram_bot.enabled должен быть true или false")
    egress = topology.get("telegram_egress") or {}
    if egress.get("listen", "127.0.0.1") not in ("127.0.0.1", "::1"):
        raise SystemExit("telegram_egress.listen должен быть loopback-адресом")
    if not 1024 <= int(egress.get("port", 18080)) <= 65535:
        raise SystemExit("telegram_egress.port должен быть от 1024 до 65535")
    if egress.get("strategy", "roundRobin") not in ("roundRobin", "random", "leastPing", "leastLoad"):
        raise SystemExit("telegram_egress.strategy: допустимы roundRobin, random, leastPing или leastLoad")
    nodes = topology.get("nodes") or []
    if not nodes:
        raise SystemExit("Нужна минимум одна зарубежная нода")
    site = topology.get("site") or {}
    for key in ("ssh_host", "address", "public_url"):
        if not site.get(key):
            raise SystemExit(f"topology.site.{key} обязателен")
    valid_ip(site["address"], "Central/site address")
    valid_public_url(site["public_url"],"topology.site.public_url")
    required_node_keys = ("id", "name", "country", "ssh_host", "address", "public_host", "sni")
    for index, node in enumerate(nodes, 1):
        for key in required_node_keys:
            if not node.get(key):
                raise SystemExit(f"topology.nodes[{index - 1}].{key} обязателен")
        valid_ip(node["address"], f"Адрес ноды {index}")
        valid_country(node["country"], f"Код страны ноды {index}")
        sni_mode=node.get("sni_mode","external")
        if sni_mode not in ("external","local","preserve"):
            raise SystemExit(f"Нода {index}: sni_mode должен быть external, local или preserve")
        valid_host(node["sni"],f"SNI ноды {index}",True)
        api_host = str(node.get("api_host") or "")
        if api_host and not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", api_host):
            raise SystemExit(f"api_host ноды {index}: нужно DNS-имя без схемы и пути")
        if "://" in str(node["public_host"]):
            raise SystemExit(f"public_host ноды {index} указывается без http:// и https://")
        if bool(node.get("cert")) != bool(node.get("key")):
            raise SystemExit(f"Для ноды {index} cert и key указываются только вместе")
    if bool(site.get("tls_cert")) != bool(site.get("tls_key")):
        raise SystemExit("site.tls_cert и site.tls_key указываются только вместе")
    if site.get("tls_cert"):
        valid_tls_path(site["tls_cert"],"site.tls_cert");valid_tls_path(site["tls_key"],"site.tls_key")
    for index,node in enumerate(nodes):
        if node.get("cert"):
            valid_tls_path(node["cert"],f"nodes[{index}].cert");valid_tls_path(node["key"],f"nodes[{index}].key")
    targets = [str(topology["site"]["ssh_host"])] + [str(node["ssh_host"]) for node in nodes]
    valid_ssh_target(targets[0], "topology.site.ssh_host")
    for index, target in enumerate(targets[1:]):
        valid_ssh_target(target, f"topology.nodes[{index}].ssh_host")
    if mode in (1, 2) and len(targets) != len(set(targets)):
        raise SystemExit("Центральная и зарубежные ноды должны быть разными VPS")
    if mode == 3 and (len(nodes) != 1 or nodes[0]["ssh_host"] != topology["site"]["ssh_host"]):
        raise SystemExit("В режиме 3 сайт и VPN должны находиться на одной VPS")
    ids = [str(node["id"]) for node in nodes]
    if len(ids) != len(set(ids)):
        raise SystemExit("ID зарубежных нод должны быть уникальны")
    if topology.get("emergency_node") not in ids:
        raise SystemExit("emergency_node должен ссылаться на существующую ноду")
    placement=str(bot.get("placement") or "control")
    if placement!="control" and placement not in ids:
        raise SystemExit("telegram_bot.placement должен быть control или ID существующей иностранной ноды")
    if bot.get("enabled",True) and placement!="control":
        selected=next(node for node in nodes if node["id"]==placement)
        address=ipaddress.ip_address(selected["address"])
        if site["ssh_host"]!="local" and (not address.is_private or address.is_unspecified or address.is_link_local):
            raise SystemExit("Для удалённого bot-admin адрес выбранной ноды должен быть приватным адресом защищённой сети")
        if urllib.parse.urlparse(site["public_url"]).scheme!="https":
            raise SystemExit("Удалённый Telegram-бот требует HTTPS public_url центрального сайта")
    if mode == 2:
        ingress = [valid_ip(node.get("ingress_address", ""), "Central ingress") for node in nodes]
        if len(ingress) != len(set(ingress)):
            raise SystemExit("Для нескольких каскадных нод нужны уникальные входные IP на центральной VPS")


def show_plan(topology):
    labels = {
        1: "центральная VPS + прямые зарубежные ноды",
        2: "центральная VPS + каскад на зарубежные ноды",
        3: "один зарубежный сервер со всеми компонентами",
    }
    print(f"\nПлан: {labels[topology['mode']]}")
    print(f"Сайт: {topology['site']['ssh_host']} · {topology['site']['public_url']}")
    for node in topology["nodes"]:
        suffix = f" · вход на центральной VPS {node['ingress_address']}" if topology["mode"] == 2 else ""
        roles = " · аварийный MTProto" if node["id"] == topology["emergency_node"] else ""
        print(f"VPN: {node['id']} · {node['ssh_host']} · {node['address']} · 11 подключений{suffix}{roles}")
        masking="локальный сайт-заглушка" if node.get("sni_mode")=="local" else "внешний TLS-сайт"
        if node.get("sni_mode")=="preserve":masking="сохранение существующей настройки"
        print(f"  SNI: {node['sni']} · {masking}")
    print("Заглушка: " + ("на каждой зарубежной ноде" if topology["mode"] in (1, 2) else "не требуется"))
    print("Telegram-бот: " + (bot_location_label(topology) if bot_enabled(topology) else "отключён"))


def ensure_ssh_key_access(target):
    if target=="local":return
    probe=run(["ssh","-o","BatchMode=yes","-o","ConnectTimeout=8","--",target,"id -u"],capture=True,check=False)
    if probe.returncode==0:return
    ssh_dir=pathlib.Path.home()/".ssh";private_key=ssh_dir/"id_ed25519";public_key=ssh_dir/"id_ed25519.pub"
    ssh_dir.mkdir(mode=0o700,parents=True,exist_ok=True);ssh_dir.chmod(0o700)
    if not private_key.exists() or not public_key.exists():
        print(f"Создаётся SSH-ключ для автоматической настройки {target}")
        run(["ssh-keygen","-q","-t","ed25519","-N","","-f",private_key])
    print(f"Введите SSH-пароль для {target}. Он обрабатывается ssh-copy-id и не сохраняется HOME-VPN.")
    run(["ssh-copy-id","-i",public_key,"-o","StrictHostKeyChecking=accept-new",target],timeout=180)
    verified=run(["ssh","-o","BatchMode=yes","-o","ConnectTimeout=8","--",target,"id -u"],capture=True,check=False)
    if verified.returncode or verified.stdout.strip()!="0":raise RuntimeError(f"{target}: не удалось настроить root SSH по ключу")


def check_servers(topology,bootstrap_ssh=False):
    targets = {topology["site"]["ssh_host"]} | {node["ssh_host"] for node in topology["nodes"]}
    for target in sorted(targets):
        if bootstrap_ssh:ensure_ssh_key_access(target)
        result = ssh_command(target, ["id", "-u"], capture=True)
        if result.stdout.strip() != "0":
            raise RuntimeError(f"{target}: для установки нужен root SSH")


def build_source_archive(target):
    tracked = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-z"],
        capture_output=True, check=False,
    )
    if tracked.returncode == 0 and tracked.stdout:
        subprocess.run(
            ["tar", "-C", str(ROOT), "-czf", str(target), "--null", "-T", "-"],
            input=tracked.stdout, check=True,
        )
        return
    # A published source archive has no .git and already contains only release
    # files. Retain defensive exclusions for that installation path.
    run([
        "tar", "--exclude=.git", "--exclude=__pycache__", "--exclude=*.pyc",
        "--exclude=*.db", "--exclude=*.sqlite*", "--exclude=*.env",
        "--exclude=*.log", "--exclude=*.bak*", "--exclude=*.pem",
        "--exclude=*.key", "--exclude=*.crt", "--exclude=.tmp-*",
        "--exclude=./config/config.json", "--exclude=./nodes.json",
        "--exclude=./*-report.json", "--exclude=./payment_timers.json",
        "--exclude=./last_event*", "--exclude=./topology.local.json",
        "-czf", target, "-C", ROOT, ".",
    ])


def deploy_source(target, archive):
    if target == "local":
        run(["install", "-d", "-m", "0750", "/opt/home-vpn-source"])
        run(["tar", "-xzf", archive, "-C", "/opt/home-vpn-source"])
        return
    remote = f"/tmp/home-vpn-source-{os.getpid()}.tar.gz"
    run(["scp", "--", archive, f"{target}:{remote}"])
    try:
        ssh_script(target, f'''set -Eeuo pipefail
install -d -m 0750 /opt/home-vpn-source
tar -xzf {shlex.quote(remote)} -C /opt/home-vpn-source
rm -f -- {shlex.quote(remote)}
''')
    finally:
        ssh_command(target, ["rm", "-f", "--", remote])


def download_protocol_installer(target, topology):
    installer = topology["protocol_installer"]
    with urllib.request.urlopen(installer["url"], timeout=60) as response:
        final_url = urllib.parse.urlparse(response.geturl())
        if final_url.scheme != "https":
            raise RuntimeError("Установщик перенаправил загрузку на адрес без HTTPS")
        payload = response.read(4 * 1024 * 1024 + 1)
    if len(payload) > 4 * 1024 * 1024:
        raise RuntimeError("Установщик протоколов больше допустимых 4 MiB")
    digest = hashlib.sha256(payload).hexdigest()
    if not hmac.compare_digest(digest, installer["sha256"].lower()):
        raise RuntimeError(f"Неожиданный SHA-256 установщика протоколов: {digest}")
    pathlib.Path(target).write_bytes(payload)


def push_file(target, source, destination, mode):
    if target == "local":
        run(["install", "-m", f"{mode:o}", source, destination])
        return
    remote = f"/tmp/home-vpn-{os.getpid()}-{pathlib.Path(source).name}"
    run(["scp", "--", source, f"{target}:{remote}"])
    try:
        ssh_script(target, f"install -m {mode:o} {shlex.quote(remote)} {shlex.quote(destination)}\n")
    finally:
        ssh_command(target, ["rm", "-f", "--", remote])


def existing_kit_state(target):
    code = r'''import json, pathlib, sqlite3
path=pathlib.Path("/etc/x-ui/x-ui.db")
if not path.exists(): print(json.dumps({"installed":False,"remarks":[]})); raise SystemExit
db=sqlite3.connect("file:"+str(path)+"?mode=ro",uri=True)
remarks=[row[0] for row in db.execute("SELECT remark FROM inbounds")]
db.close();print(json.dumps({"installed":pathlib.Path("/usr/local/bin/kit").exists(),"remarks":remarks},ensure_ascii=False))
'''
    return json.loads(ssh_python(target, code, capture=True).stdout)


def install_kit(node, installer):
    target = node["ssh_host"]
    state = existing_kit_state(target)
    remarks=set(state["remarks"])
    compatible=KIT_BASE_REMARKS.issubset(remarks) and len(remarks)==11 and any("MTProto" in item for item in remarks)
    if state["installed"] and compatible:
        print(f"{node['id']}: KIT уже настроен, переустановка не требуется")
    elif state["installed"] or state["remarks"]:
        raise RuntimeError(f"{node['id']}: обнаружена частичная/другая конфигурация 3x-ui; автоматическая очистка запрещена")
    else:
        push_file(target, installer, "/root/3x-ui-protocol-installer.sh", 0o700)
        flags = ["bash","/root/3x-ui-protocol-installer.sh","--host",node["public_host"]]
        if node.get("sni_mode")=="local":flags.extend(("--domain",node["sni"]))
        else:flags.extend(("--sni",node["sni"]))
        flags.extend(("--protocols","all","--user","homevpn-bootstrap","--no-ufw","-y"))
        if node.get("sni_mode")!="local" and node.get("cert") and node.get("key"):
            flags.extend(("--cert", node["cert"], "--key", node["key"]))
        command = " ".join(shlex.quote(item) for item in flags)
        ssh_script(target, f'''set -Eeuo pipefail
timeout --signal=INT 900 {command} >/root/kit-topology-install.log 2>&1 || rc=$?
chmod 600 /root/kit-topology-install.log
if [ "${{rc:-0}}" -ne 0 ] && [ "${{rc:-0}}" -ne 124 ] && [ "${{rc:-0}}" -ne 130 ]; then exit "$rc"; fi
systemctl daemon-reload
systemctl restart x-ui kit-sub
/usr/local/bin/kit user del homevpn-bootstrap >/dev/null 2>&1 || true
''', timeout=960)
    configure_kit_public_certificate(node)
    install_xui_backup(target)


def configure_kit_public_certificate(node):
    """Keep the self-steal certificate, but serve site/panel with our SAN cert."""
    if not node.get("cert") or not node.get("key"):return
    code=r'''import os,pathlib,re,subprocess,sys,tempfile
path=pathlib.Path("/etc/nginx/conf.d/kit.conf")
if not path.is_file():raise SystemExit
cert,key=sys.argv[1:3];text=path.read_text(encoding="utf-8");marker="# Свой домен (self-steal)"
head,separator,tail=text.partition(marker)
head=re.sub(r"(?m)^\s*ssl_certificate\s+[^;]+;",f"    ssl_certificate {cert};",head,count=1)
head=re.sub(r"(?m)^\s*ssl_certificate_key\s+[^;]+;",f"    ssl_certificate_key {key};",head,count=1)
updated=head+(separator+tail if separator else "")
fd,tmp=tempfile.mkstemp(prefix=path.name+".",dir=path.parent,text=True)
with os.fdopen(fd,"w",encoding="utf-8") as output:output.write(updated)
os.chmod(tmp,0o644);os.replace(tmp,path)
result=subprocess.run(["nginx","-t"],text=True,capture_output=True)
if result.returncode:raise SystemExit(result.stderr or result.stdout)
subprocess.run(["systemctl","reload","nginx"],check=True)
'''
    ssh_command(node["ssh_host"],["python3","-c",code,node["cert"],node["key"]])


def install_xui_backup(target):
    for name, destination, mode in (
        ("home-vpn-kit-backup", "/usr/local/sbin/home-vpn-kit-backup", 0o700),
        ("home-vpn-kit-backup.service", "/etc/systemd/system/home-vpn-kit-backup.service", 0o644),
        ("home-vpn-kit-backup.timer", "/etc/systemd/system/home-vpn-kit-backup.timer", 0o644),
    ):
        push_file(target, ROOT / "scripts" / name, destination, mode)
    ssh_script(target, "set -Eeuo pipefail\nsystemctl daemon-reload\nsystemctl enable --now home-vpn-kit-backup.timer\nsystemctl start home-vpn-kit-backup.service\n")


def parse_xui_api_token(output):
    match=re.search(r"^apiToken:\s*(\S+)\s*$",output,re.MULTILINE)
    if not match:raise RuntimeError("3x-ui не вернула API token; обновите панель или создайте токен в Settings → API Tokens")
    return match.group(1)


def existing_xui_token(target):
    """Mint one named token through the panel CLI without reading user credentials."""
    locate = r'''set -Eeuo pipefail
for candidate in /usr/local/x-ui/x-ui /usr/local/bin/x-ui /usr/bin/x-ui; do
    if [ -x "$candidate" ]; then printf '%s\n' "$candidate"; exit; fi
done
exit 1
'''
    binary = ssh_script(target, locate, capture=True).stdout.strip().splitlines()[-1]
    result = ssh_command(
        target, [binary, "setting", "-getApiToken", "-tokenName", "home-vpn"],
        capture=True, timeout=60,
    )
    return parse_xui_api_token(result.stdout)


def parse_env(content):
    values = {}
    for line in content.splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, raw = line.split("=", 1)
        try:
            parsed = shlex.split(raw, posix=True)
            values[key] = parsed[0] if parsed else ""
        except ValueError:
            values[key] = raw.strip()
    return values


def node_inventory(node, token_override=""):
    code = r'''import json, pathlib, sqlite3
db=sqlite3.connect("file:/etc/x-ui/x-ui.db?mode=ro",uri=True);db.row_factory=sqlite3.Row
rows=[dict(row) for row in db.execute("SELECT id,remark,protocol,port,enable,stream_settings FROM inbounds ORDER BY id")]
integrity=db.execute("PRAGMA integrity_check").fetchone()[0];db.close()
settings={}
db=sqlite3.connect("file:/etc/x-ui/x-ui.db?mode=ro",uri=True)
try:settings=dict(db.execute("SELECT key,value FROM settings"))
except sqlite3.Error:pass
db.close()
env=pathlib.Path("/etc/x-ui/install-result.env")
print(json.dumps({"rows":rows,"integrity":integrity,"install_env":env.read_text() if env.is_file() else "","settings":settings},ensure_ascii=False))
'''
    data = json.loads(ssh_python(node["ssh_host"], code, capture=True).stdout)
    remarks = {row["remark"] for row in data["rows"]}
    mtproto_rows=[row for row in data["rows"] if row["protocol"]=="mtproto"]
    preserve=bool(token_override)
    if data["integrity"] != "ok":
        raise RuntimeError(f"{node['id']}: SQLite integrity_check неуспешен")
    if not preserve and (len(data["rows"]) != 11 or not KIT_BASE_REMARKS.issubset(remarks) or len(mtproto_rows)!=1):
        raise RuntimeError(f"{node['id']}: ожидалось ровно 11 подключений KIT, получено {len(data['rows'])}")
    if preserve and not any(bool(row.get("enable")) for row in data["rows"]):
        raise RuntimeError(f"{node['id']}: в существующей 3x-ui нет активных inbounds")
    environment = parse_env(data.pop("install_env"))
    settings = data.pop("settings", {})
    token = token_override or environment.get("XUI_API_TOKEN", "")
    access_values = environment.get("XUI_ACCESS_URL", "").split()
    access = access_values[0] if access_values else ""
    if preserve:
        base_path=str(settings.get("webBasePath") or "").strip("/")
        if not node.get("api_host") or not base_path:
            raise RuntimeError(f"{node['id']}: для существующей панели нужны api_host и webBasePath")
        access="https://"+str(node["api_host"])+"/"+base_path
    if not token or not access:
        raise RuntimeError(f"{node['id']}: KIT не сохранил API token/panel URL")
    parsed = urllib.parse.urlparse(access)
    # The control plane always talks to 3x-ui through the protected server
    # network. Its administrative endpoint is never put on a public route.
    api_address = str(node.get("api_host") or node["address"])
    private_host = "[" + api_address + "]" if ":" in api_address else api_address
    if node.get("api_host"):
        # KIT exposes its loopback-only panel through the authenticated HTTPS
        # path on port 443. Never retain the unreachable local panel port.
        access = urllib.parse.urlunparse(parsed._replace(scheme="https", netloc=private_host))
    else:
        netloc = private_host + ((":" + str(parsed.port)) if parsed.port else "")
        access = urllib.parse.urlunparse(parsed._replace(netloc=netloc))
    routes = []
    for item in data["rows"]:
        try:
            stream = json.loads(item.get("stream_settings") or "{}")
        except json.JSONDecodeError:
            stream = {}
        routes.append({
            "name": item["remark"], "inbound_id": int(item["id"]),
            "protocol": item["protocol"], "transport": stream.get("network") or "tcp",
            "enabled": bool(item["enable"]),
        })
    mtproto = mtproto_rows[0] if mtproto_rows else None
    return {"token": token, "panel_url": access.rstrip("/"), "routes": routes,
            "mtproto_id": int(mtproto["id"]) if mtproto else None, "rows": data["rows"]}


def push_json(target, path, payload, mode=0o600):
    code = r'''import json, os, pathlib, sys, tempfile
target=pathlib.Path(sys.argv[1]);mode=int(sys.argv[2],8);target.parent.mkdir(parents=True,exist_ok=True)
payload=json.load(sys.stdin);fd,tmp=tempfile.mkstemp(prefix=target.name+".",dir=target.parent,text=True)
with os.fdopen(fd,"w",encoding="utf-8") as out:json.dump(payload,out,ensure_ascii=False,indent=2);out.write("\n")
os.chmod(tmp,mode);os.replace(tmp,target)
'''
    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    ssh_command(target, ["python3", "-c", code, path, f"{mode:o}"], input_text=serialized)


def update_remote_envs(target, updates):
    """Atomically merge env values without exposing secrets in process arguments."""
    code = r'''import json, os, pathlib, sys, tempfile
for filename,changes in json.load(sys.stdin).items():
 path=pathlib.Path(filename);values={}
 for line in path.read_text(encoding="utf-8").splitlines():
  if line and not line.lstrip().startswith("#") and "=" in line:
   key,value=line.split("=",1);values[key]=value
 values.update({str(key):str(value) for key,value in changes.items()})
 fd,tmp=tempfile.mkstemp(prefix=path.name+".",dir=path.parent,text=True)
 with os.fdopen(fd,"w",encoding="utf-8") as out:
  for key,value in values.items():out.write(key+"="+value+"\n")
 os.chmod(tmp,0o600);os.replace(tmp,path)
'''
    serialized = json.dumps(updates, ensure_ascii=False, separators=(",", ":"))
    ssh_command(target, ["python3", "-c", code], input_text=serialized)


def remote_file_exists(target, path):
    code="import pathlib,sys;print('1' if pathlib.Path(sys.argv[1]).is_file() else '0')"
    result=ssh_command(target,["python3","-c",code,path],capture=True)
    return result.stdout.strip()=="1"


def read_remote_json(target, path):
    code = "import pathlib,sys;print(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'))"
    return json.loads(ssh_command(
        target, ["python3", "-c", code, path], capture=True,
    ).stdout)


def site_config(nodes, inventories, base=None):
    data = json.loads(json.dumps(base)) if base else default_config()
    configured = []
    for index, node in enumerate(nodes):
        inventory = inventories[node["id"]]
        configured.append({
            "id": node["id"], "name": node["name"], "country": node["country"],
            "flag": node.get("flag", "🌐"), "primary": index == 0, "enabled": True,
            "accept_new": True, "maintenance": False, "weight": 100,
            "panel_url": inventory["panel_url"],
            "token_env": ("XUI_TOKEN" if index == 0 else node["id"].upper().replace("-", "_") + "_XUI_TOKEN"),
            "inbound_ids": [route["inbound_id"] for route in inventory["routes"] if route["enabled"]],
            "subscription_base_url": "", "routes": inventory["routes"],
        })
    data["nodes"] = configured
    return data


def install_site(topology, inventories, credentials):
    site = topology["site"];target = site["ssh_host"]
    already_installed=remote_file_exists(target,"/etc/vpn-shop.env")
    current = read_remote_json(target, "/etc/home-vpn/config.json") if already_installed and remote_file_exists(target,"/etc/home-vpn/config.json") else None
    config = site_config(topology["nodes"], inventories, current)
    tokens = {node["id"]: inventories[node["id"]]["token"] for node in topology["nodes"]}
    answers = {
        "public_url": site["public_url"], "admin_user": credentials["admin_user"],
        "admin_password": credentials["admin_password"], "telegram_token": "",
        "smtp_host": "", "payment_mode": "manual", "node_tokens": tokens,
    }
    if site.get("tls_cert"):
        answers["tls_cert"]=site["tls_cert"];answers["tls_key"]=site["tls_key"]
    push_json(target, "/etc/home-vpn/config.json", config, 0o600)
    if already_installed:
        token_values={item["token_env"]:tokens[item["id"]] for item in config["nodes"]}
        update_remote_envs(target, {"/etc/vpn-shop.env": token_values})
        ssh_script(target,r'''set -Eeuo pipefail
chown root:vpnshop /etc/home-vpn /etc/home-vpn/config.json
chmod 0770 /etc/home-vpn
chmod 0660 /etc/home-vpn/config.json
HOME_VPN_SOURCE_ROOT=/opt/home-vpn-source python3 /opt/home-vpn-source/scripts/home-vpn-manager.py update site
''',timeout=600)
    else:
        push_json(target, "/root/home-vpn-site-answers.json", answers, 0o600)
        try:
            ssh_script(target, r'''set -Eeuo pipefail
HOME_VPN_INSTALL_BOT=0 python3 /opt/home-vpn-source/scripts/home-vpn-setup.py install --config /etc/home-vpn/config.json --answers /root/home-vpn-site-answers.json
''', timeout=1200)
        finally:
            ssh_script(target, "rm -f -- /root/home-vpn-site-answers.json\n")
    site_updates={"TRUST_PROXY":"1"}
    emergency_node = next(node for node in topology["nodes"] if node["id"] == topology["emergency_node"])
    bot_inventory = inventories[emergency_node["id"]]
    if bot_inventory.get("mtproto_id") is not None:
        token_env = next(node["token_env"] for node in config["nodes"] if node["id"] == emergency_node["id"])
        site_updates.update({
            "MTPROTO_INBOUND_ID":str(bot_inventory["mtproto_id"]),
            "MTPROTO_PUBLIC_HOST":emergency_node["public_host"],
            "MTPROTO_PUBLIC_PORT":"443","MTPROTO_XUI_URL":bot_inventory["panel_url"],
            "MTPROTO_XUI_TOKEN_ENV":token_env,
        })
    update_remote_envs(target,{"/etc/vpn-shop.env":site_updates})
    ssh_script(target, "systemctl restart vpn-shop\n")


def ensure_site_tls(topology):
    """Obtain a browser-trusted certificate for a fresh mode 1/2 control VPS."""
    if topology["mode"] not in (1,2):return
    site=topology["site"];parsed=valid_public_url(site["public_url"])
    if parsed.scheme!="https" or site.get("tls_cert"):return
    host=parsed.hostname or ""
    try:ipaddress.ip_address(host)
    except ValueError:pass
    else:raise RuntimeError("Для автоматического Let's Encrypt в URL сайта нужен домен, а не IP-адрес")
    target=site["ssh_host"]
    script=r'''set -Eeuo pipefail
domain=$1
export DEBIAN_FRONTEND=noninteractive
apt-get update >/dev/null
apt-get install -y certbot openssl >/dev/null
cert_dir="/etc/letsencrypt/live/$domain"
if ! test -s "$cert_dir/fullchain.pem" || ! test -s "$cert_dir/privkey.pem" || \
   ! openssl x509 -in "$cert_dir/fullchain.pem" -noout -checkhost "$domain" >/dev/null 2>&1 || \
   ! openssl x509 -in "$cert_dir/fullchain.pem" -noout -checkend 604800 >/dev/null 2>&1; then
  nginx_was_active=0
  if systemctl is-active --quiet nginx; then nginx_was_active=1; systemctl stop nginx; fi
  restore_nginx() { if [ "$nginx_was_active" = 1 ]; then systemctl start nginx; fi; }
  trap restore_nginx EXIT
  certbot certonly --standalone --non-interactive --agree-tos \
    --register-unsafely-without-email --preferred-challenges http -d "$domain"
  restore_nginx
  trap - EXIT
fi
install -d -m 0755 /etc/letsencrypt/renewal-hooks/pre /etc/letsencrypt/renewal-hooks/post /etc/letsencrypt/renewal-hooks/deploy
cat >/etc/letsencrypt/renewal-hooks/pre/home-vpn-nginx <<'HOOK'
#!/bin/sh
systemctl is-active --quiet nginx && systemctl stop nginx || true
HOOK
cat >/etc/letsencrypt/renewal-hooks/post/home-vpn-nginx <<'HOOK'
#!/bin/sh
systemctl start nginx || true
HOOK
cat >/etc/letsencrypt/renewal-hooks/deploy/home-vpn-nginx <<'HOOK'
#!/bin/sh
set -eu
test -n "${RENEWED_LINEAGE:-}" || exit 0
test -d /etc/home-vpn/tls || exit 0
install -m 0640 -o root -g vpnshop "$RENEWED_LINEAGE/fullchain.pem" /etc/home-vpn/tls/site.crt
install -m 0640 -o root -g vpnshop "$RENEWED_LINEAGE/privkey.pem" /etc/home-vpn/tls/site.key
nginx -t && systemctl reload nginx
HOOK
chmod 0755 /etc/letsencrypt/renewal-hooks/pre/home-vpn-nginx /etc/letsencrypt/renewal-hooks/post/home-vpn-nginx /etc/letsencrypt/renewal-hooks/deploy/home-vpn-nginx
systemctl enable --now certbot.timer >/dev/null 2>&1 || true
'''
    ssh_command(target,["bash","-s","--",host],input_text=script,timeout=1200)
    site["tls_cert"]=f"/etc/letsencrypt/live/{host}/fullchain.pem"
    site["tls_key"]=f"/etc/letsencrypt/live/{host}/privkey.pem"
    print(f"TLS Let's Encrypt готов: {host}; автоматическое продление включено")


def site_secrets(target):
    code = r'''import json, pathlib
values={}
for line in pathlib.Path("/etc/vpn-shop.env").read_text(encoding="utf-8").splitlines():
    if line and not line.lstrip().startswith("#") and "=" in line:
        key,value=line.split("=",1);values[key]=value
scheme="https" if values.get("TLS_CERT") else "http"
host="127.0.0.1"
port=values.get("APP_PORT","8080")
print(json.dumps({"api":values.get("API_SECRET",""),"admin_path":values.get("ADMIN_ENTRY_PATH",""),"local_api":scheme+"://"+host+":"+port}))
'''
    return json.loads(ssh_python(target, code, capture=True).stdout)


def panel_api(inventory, method, path, body=None):
    request = urllib.request.Request(
        inventory["panel_url"] + path,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"Authorization": "Bearer " + inventory["token"], "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def ensure_bot_egress_link(node, inventory):
    route = next((item for item in inventory["routes"] if item["enabled"] and item["protocol"] == "vless" and item["transport"] == "xhttp"), None)
    if not route:
        raise RuntimeError(f"{node['id']}: для трафика Telegram нужен активный VLESS/XHTTP inbound")
    email = "homevpn-bot-egress-" + node["id"]
    existing = panel_api(inventory, "GET", "/panel/api/clients/get/" + urllib.parse.quote(email)).get("obj") or {}
    if not existing.get("client"):
        client_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, "home-vpn:bot-egress:" + node["id"]))
        payload = {"client": {"email": email, "id": client_uuid, "uuid": client_uuid, "enable": True, "expiryTime": 0, "totalGB": 0, "limitIp": 0, "tgId": 0, "comment": "HOME-VPN Telegram egress"}, "inboundIds": [route["inbound_id"]]}
        result = panel_api(inventory, "POST", "/panel/api/clients/add", payload)
        if not result.get("success"):
            raise RuntimeError(f"{node['id']}: не удалось создать служебный XHTTP-клиент")
    links = panel_api(inventory, "GET", "/panel/api/clients/links/" + urllib.parse.quote(email)).get("obj") or []
    link = next((item for item in links if item.startswith("vless://") and (urllib.parse.parse_qs(urllib.parse.urlparse(item).query).get("type") or [""])[0].lower() == "xhttp"), "")
    if not link:
        raise RuntimeError(f"{node['id']}: API 3x-ui не вернул VLESS/XHTTP-ссылку служебного клиента")
    return link


def xhttp_outbound(node, link):
    parsed = urllib.parse.urlparse(link);query = {key: values[0] for key, values in urllib.parse.parse_qs(parsed.query).items()}
    if not parsed.username or not parsed.hostname or not parsed.port:
        raise RuntimeError(f"{node['id']}: неполная VLESS/XHTTP-ссылка")
    security = query.get("security", "none")
    stream = {"network": "xhttp", "security": security, "xhttpSettings": {
        "path": urllib.parse.unquote(query.get("path", "/")), "host": query.get("host", ""), "mode": query.get("mode", "auto"),
    }}
    if security == "reality":
        stream["realitySettings"] = {"serverName": query.get("sni", node["sni"]), "fingerprint": query.get("fp", "chrome"), "publicKey": query.get("pbk", ""), "shortId": query.get("sid", ""), "spiderX": urllib.parse.unquote(query.get("spx", "/"))}
    elif security == "tls":
        stream["tlsSettings"] = {"serverName": query.get("sni", node["public_host"]), "allowInsecure": False, "fingerprint": query.get("fp", "chrome")}
    user = {"id": urllib.parse.unquote(parsed.username), "encryption": query.get("encryption", "none")}
    if query.get("flow"):user["flow"] = query["flow"]
    return {"tag": "foreign-" + safe_id(node["id"]), "protocol": "vless", "settings": {"vnext": [{"address": parsed.hostname, "port": parsed.port, "users": [user]}]}, "streamSettings": stream}


def bot_egress_config(outbounds, egress=None):
    """Build a loopback, Telegram-only, health-aware Xray proxy."""
    egress = egress or {}
    listen = egress.get("listen", "127.0.0.1")
    port = int(egress.get("port", 18080))
    strategy = egress.get("strategy", "leastPing")
    if strategy in ("roundRobin","random"):strategy="leastPing"
    return {
        "log": {"loglevel": "warning"},
        "inbounds": [{"tag": "telegram-http", "listen": listen, "port": port, "protocol": "http", "settings": {}}],
        "outbounds": [*outbounds, {"tag": "blocked", "protocol": "blackhole", "settings": {}}],
        "routing": {
            "domainStrategy": "AsIs",
            "rules": [
                {"type": "field", "inboundTag": ["telegram-http"], "domain": ["full:api.telegram.org"], "balancerTag": "foreign-pool"},
                {"type": "field", "inboundTag": ["telegram-http"], "outboundTag": "blocked"},
            ],
            "balancers": [{"tag": "foreign-pool", "selector": ["foreign-"], "fallbackTag": "blocked", "strategy": {"type": strategy}}],
        },
        "observatory": {
            "subjectSelector": ["foreign-"],
            "probeUrl": "https://api.telegram.org",
            "probeInterval": egress.get("probe_interval", "30s"),
            "enableConcurrency": True,
        },
    }


def ensure_xray_on_target(topology,target):
    locate = r'''set -Euo pipefail
if command -v xray >/dev/null 2>&1; then command -v xray; exit; fi
find /usr/local/x-ui/bin /opt -maxdepth 4 -type f -name 'xray*' -perm /111 2>/dev/null | head -n1 || true
'''
    result = ssh_script(target, locate, capture=True)
    if result.stdout.strip():
        return result.stdout.strip().splitlines()[-1]
    source = topology["nodes"][0]["ssh_host"]
    source_arch = ssh_script(source, "uname -m\n", capture=True).stdout.strip()
    target_arch = ssh_script(target, "uname -m\n", capture=True).stdout.strip()
    if source_arch != target_arch:
        raise RuntimeError("Архитектура CPU ноды egress и зарубежной VPS отличается; установите Xray на ноде бота вручную")
    source_binary = ssh_script(source, locate, capture=True).stdout.strip().splitlines()
    if not source_binary:
        raise RuntimeError("На VPN-ноде не найден Xray")
    if source == target:
        return source_binary[-1]
    with tempfile.TemporaryDirectory(prefix="home-vpn-xray-") as directory:
        local = pathlib.Path(directory) / "xray"
        run(["scp", f"{source}:{source_binary[-1]}", local])
        push_file(target, local, "/usr/local/bin/home-vpn-xray", 0o755)
    return "/usr/local/bin/home-vpn-xray"


def install_bot_egress(topology, inventories):
    outbounds=[]
    for node in topology["nodes"]:
        outbounds.append(xhttp_outbound(node, ensure_bot_egress_link(node, inventories[node["id"]])))
    config = bot_egress_config(outbounds, topology.get("telegram_egress"))
    target=bot_target(topology);binary=ensure_xray_on_target(topology,target)
    # Xray determines the config format from the final suffix.
    pending_config = "/etc/home-vpn/bot-egress.new.json"
    push_json(target, pending_config, config, 0o600)
    ownership={"clients":[{"email":"homevpn-bot-egress-"+node["id"],"panel_url":inventories[node["id"]]["panel_url"],"token_env":"XUI_TOKEN" if index==0 else node["id"].upper().replace("-","_")+"_XUI_TOKEN"} for index,node in enumerate(topology["nodes"])]}
    push_json(target,"/etc/home-vpn/bot-egress-ownership.json",ownership,0o600)
    unit=f'''[Unit]
Description=HOME-VPN balanced Telegram egress over VLESS XHTTP
After=network-online.target
Wants=network-online.target
[Service]
Type=simple
User=vpnshopbot
Group=vpnshopbot
SupplementaryGroups=homevpn
ExecStart={binary} run -config /etc/home-vpn/bot-egress.json
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
LockPersonality=true
RestrictRealtime=true
SystemCallArchitectures=native
RestrictAddressFamilies=AF_INET AF_INET6
CapabilityBoundingSet=
AmbientCapabilities=
UMask=0077
[Install]
WantedBy=multi-user.target
'''
    encoded=base64.b64encode(unit.encode()).decode()
    ssh_script(target, f'''set -Eeuo pipefail
getent group vpnshopbot >/dev/null || groupadd --system vpnshopbot
getent passwd vpnshopbot >/dev/null || useradd --system --gid vpnshopbot --home-dir /var/lib/vpn-bot --shell /usr/sbin/nologin vpnshopbot
getent group homevpn >/dev/null || groupadd --system homevpn
getent passwd vpnshop >/dev/null && usermod -a -G homevpn vpnshop
usermod -a -G homevpn vpnshopbot
chown root:homevpn /etc/home-vpn
chmod 0770 /etc/home-vpn
{shlex.quote(binary)} run -test -config {shlex.quote(pending_config)}
install -o root -g vpnshopbot -m 0640 {shlex.quote(pending_config)} /etc/home-vpn/bot-egress.json
rm -f -- {shlex.quote(pending_config)}
printf '%s' {shlex.quote(encoded)} | base64 -d > /etc/systemd/system/home-vpn-bot-egress.service
chmod 0644 /etc/systemd/system/home-vpn-bot-egress.service
systemctl daemon-reload
systemctl enable --now home-vpn-bot-egress.service
''')


def install_bot(topology, credentials):
    site = topology["site"]
    secrets = site_secrets(site["ssh_host"])
    target=bot_target(topology);remote=target!=site["ssh_host"]
    selected=bot_node(topology)
    tunnelled=remote and site["ssh_host"]=="local"
    panel_bind="127.0.0.1" if tunnelled or not remote else selected["address"]
    answers = {
        "public_url": site["public_url"], "shop_api_url": site["public_url"] if remote else secrets["local_api"],
        "shop_api_secret": secrets["api"], "telegram_token": credentials["telegram_token"],
        "telegram_admin_id": credentials["telegram_admin_id"],
        "telegram_channel": credentials["telegram_channel"],
        "admin_panel_url": site["public_url"] + secrets["admin_path"],
        "bot_admin_url": site["public_url"], "bot_panel_bind": panel_bind,
        "bot_panel_port": "8090", "bot_panel_user": credentials["bot_panel_user"],
        "bot_panel_password": credentials["bot_panel_password"],
        "telegram_proxy_url": "http://127.0.0.1:" + str((topology.get("telegram_egress") or {}).get("port",18080)),
    }
    if remote_file_exists(target,"/etc/vpn-bot.env"):
        payload={
            "TELEGRAM_BOT_TOKEN":answers["telegram_token"],"TELEGRAM_ADMIN_ID":answers["telegram_admin_id"],
            "TELEGRAM_CHANNEL":answers["telegram_channel"],"SHOP_API_URL":answers["shop_api_url"],
            "PUBLIC_BASE_URL":answers["public_url"],"SHOP_API_SECRET":answers["shop_api_secret"],
            "ADMIN_PANEL_URL":answers["admin_panel_url"],"TELEGRAM_PROXY_URL":answers["telegram_proxy_url"],
        }
        admin_payload={"BOT_PANEL_BIND":panel_bind,"BOT_PANEL_PORT":"8090","BOT_PANEL_USER":answers["bot_panel_user"],"BOT_PANEL_PASSWORD":answers["bot_panel_password"]}
        update_remote_envs(target, {
            "/etc/vpn-bot.env": payload,
            "/etc/vpn-bot-admin.env": admin_payload,
        })
        ssh_script(target,"set -Eeuo pipefail\nHOME_VPN_SOURCE_ROOT=/opt/home-vpn-source python3 /opt/home-vpn-source/scripts/home-vpn-manager.py update bot\n",timeout=600)
    else:
        push_json(target, "/root/home-vpn-bot-answers.json", answers, 0o600)
        try:
            ssh_script(target, "set -Eeuo pipefail\npython3 /opt/home-vpn-source/scripts/home-vpn-components.py bot --answers /root/home-vpn-bot-answers.json\n", timeout=900)
        finally:
            ssh_script(target, "rm -f -- /root/home-vpn-bot-answers.json\n")
    code = r'''import json, pathlib
values={}
for line in pathlib.Path("/etc/vpn-bot-admin.env").read_text(encoding="utf-8").splitlines():
 if line and not line.lstrip().startswith("#") and "=" in line:
  key,value=line.split("=",1);values[key]=value
print(json.dumps({"bind":values.get("BOT_PANEL_BIND"),"entry":values.get("BOT_PANEL_ENTRY_PATH","")}))
'''
    details=json.loads(ssh_python(target,code,capture=True).stdout)
    if details.get("bind")!=panel_bind or not str(details.get("entry") or "").startswith("/"):
        raise RuntimeError("bot-admin слушает неожиданный адрес или не имеет закрытого URL path")
    public_bot_url=site["public_url"].rstrip("/")+details["entry"]
    upstream=install_bot_admin_tunnel(topology,target) if tunnelled else f"http://{panel_bind}:8090"
    update_remote_envs(site["ssh_host"],{"/etc/vpn-shop.env":{"BOT_ADMIN_URL":public_bot_url,"BOT_ADMIN_UPSTREAM":upstream}})
    ssh_script(target,"systemctl restart vpn-bot vpn-bot-admin\n")
    ssh_script(site["ssh_host"],"systemctl restart vpn-shop\n")
    return {"target":target,"public_url":public_bot_url,"user":answers["bot_panel_user"],"password":answers["bot_panel_password"]}


def install_bot_admin_tunnel(topology,target):
    """Expose a remote loopback bot-admin only to the local control VPS."""
    if topology["site"]["ssh_host"]!="local":raise RuntimeError("SSH bot-admin tunnel поддерживается только при запуске на управляющей VPS")
    port=int((topology.get("telegram_bot") or {}).get("admin_tunnel_port",18090))
    if not 1024<=port<=65535:raise RuntimeError("telegram_bot.admin_tunnel_port должен быть от 1024 до 65535")
    unit=f'''[Unit]
Description=HOME-VPN secure tunnel to remote bot-admin
After=network-online.target
Wants=network-online.target
[Service]
Type=simple
ExecStart=/usr/bin/ssh -N -o BatchMode=yes -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -L 127.0.0.1:{port}:127.0.0.1:8090 {target}
Restart=always
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
[Install]
WantedBy=multi-user.target
'''
    encoded=base64.b64encode(unit.encode()).decode()
    ssh_script("local",f'''set -Eeuo pipefail
printf '%s' {shlex.quote(encoded)} | base64 -d > /etc/systemd/system/home-vpn-bot-admin-tunnel.service
chmod 0644 /etc/systemd/system/home-vpn-bot-admin-tunnel.service
systemctl daemon-reload
systemctl enable --now home-vpn-bot-admin-tunnel.service
''')
    return f"http://127.0.0.1:{port}"


def remove_bot_admin_tunnel(topology):
    if topology["site"]["ssh_host"]!="local":return
    ssh_script("local",'''set -Eeuo pipefail
systemctl disable --now home-vpn-bot-admin-tunnel.service 2>/dev/null || true
rm -f -- /etc/systemd/system/home-vpn-bot-admin-tunnel.service
systemctl daemon-reload
''')


def mode3_fallback_payload(inbound, destination=8080):
    """Return an idempotent Xray fallback payload without changing clients."""
    payload=dict(inbound)
    settings=payload.get("settings") or "{}"
    if isinstance(settings,str):settings=json.loads(settings)
    fallbacks=[]
    for fallback in settings.get("fallbacks") or []:
        # Keep explicit path/SNI routes, but HOME-VPN owns the catch-all route.
        if fallback.get("path") or fallback.get("name") or fallback.get("alpn"):
            fallbacks.append(fallback)
    fallbacks.append({"dest":int(destination),"xver":0})
    settings["fallbacks"]=fallbacks
    payload["settings"]=json.dumps(settings,separators=(",",":"))
    payload.pop("id",None)
    return payload


def mode3_nginx_config(site):
    parsed=valid_public_url(site["public_url"])
    cert=valid_tls_path(site.get("tls_cert"),"site.tls_cert")
    key=valid_tls_path(site.get("tls_key"),"site.tls_key")
    return f'''server {{
    listen 127.0.0.1:10446 ssl http2 proxy_protocol;
    server_name {parsed.hostname};
    ssl_certificate {cert};
    ssl_certificate_key {key};
    ssl_protocols TLSv1.2 TLSv1.3;
    set_real_ip_from 127.0.0.1;
    real_ip_header proxy_protocol;
    server_tokens off;
    client_max_body_size 2m;
    location / {{
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $proxy_protocol_addr;
        proxy_set_header X-Forwarded-For $proxy_protocol_addr;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header Connection "";
        proxy_intercept_errors off;
    }}
}}
'''


def configure_kit_nginx_route(topology):
    target=topology["site"]["ssh_host"]
    snippet='''location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $proxy_protocol_addr;
        proxy_set_header X-Forwarded-For $proxy_protocol_addr;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header Connection "";
        proxy_intercept_errors off;
    }'''
    code=r'''import os,pathlib,re,shutil,subprocess,sys,tempfile
path=pathlib.Path("/etc/nginx/conf.d/kit.conf");replacement=sys.stdin.read().rstrip()
text=path.read_text(encoding="utf-8");original=text
pattern=re.compile(r"\blocation\s+/\s*\{");match=pattern.search(text)
if not match:raise SystemExit("В основном KIT-vhost не найден location /")
opening=text.find("{",match.start(),match.end());depth=0;end=None
for index in range(opening,len(text)):
    if text[index]=="{":depth+=1
    elif text[index]=="}":
        depth-=1
        if depth==0:end=index+1;break
if end is None:raise SystemExit("Незакрытый location / в KIT-vhost")
text=text[:match.start()]+replacement+text[end:]
backup=path.with_name(path.name+".pre-home-vpn")
if not backup.exists():shutil.copy2(path,backup);os.chmod(backup,0o600)
fd,tmp=tempfile.mkstemp(prefix=path.name+".",dir=path.parent,text=True)
with os.fdopen(fd,"w",encoding="utf-8") as output:output.write(text)
os.chmod(tmp,0o644);os.replace(tmp,path)
check=subprocess.run(["nginx","-t"],text=True,capture_output=True)
if check.returncode:
 path.write_text(original,encoding="utf-8");os.chmod(path,0o644)
 raise SystemExit(check.stderr or check.stdout)
subprocess.run(["systemctl","reload","nginx"],check=True)
'''
    ssh_command(target,["python3","-c",code],input_text=snippet)


def configure_preserved_nginx_route(topology):
    site=topology["site"];parsed=valid_public_url(site["public_url"]);target=site["ssh_host"]
    snippet='''location / {
    proxy_pass http://127.0.0.1:8080;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto https;
    proxy_set_header Connection "";
    proxy_intercept_errors off;
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
}
'''
    code=r'''import os,pathlib,re,shutil,subprocess,sys,tempfile
host=sys.argv[1];snippet=sys.stdin.read();marker="include /etc/nginx/snippets/home-vpn-site.conf;"
root_pattern=re.compile(r"\blocation\s+(?:\^~\s+|=\s+)?/\s*\{")
def replace_block(value,match,replacement):
 start=match.start();opening=value.find("{",match.start(),match.end());depth=0
 for index in range(opening,len(value)):
  if value[index]=="{":depth+=1
  elif value[index]=="}":
   depth-=1
   if depth==0:return value[:start]+replacement.rstrip()+value[index+1:]
 raise SystemExit("Незакрытый location / во включаемом nginx-файле")
candidates=[pathlib.Path("/etc/nginx/sites-enabled")/host,pathlib.Path("/etc/nginx/sites-available")/host,pathlib.Path("/etc/nginx/conf.d")/(host+".conf")]
source=next((p.resolve() for p in candidates if p.exists()),None)
if source is None:
 for p in pathlib.Path("/etc/nginx").glob("**/*"):
  if p.is_file():
   try:text=p.read_text(encoding="utf-8")
   except (OSError,UnicodeError):continue
   if re.search(r"\bserver_name\s+"+re.escape(host)+r"(?:\s|;)",text):source=p.resolve();break
if source is None:raise SystemExit("Не найден nginx vhost домена "+host)
text=source.read_text(encoding="utf-8");original_text=text;snippet_path=pathlib.Path("/etc/nginx/snippets/home-vpn-site.conf")
old_snippet=snippet_path.read_bytes() if snippet_path.is_file() else None
rendered=old_snippet.decode("utf-8") if marker in text and old_snippet is not None else snippet
if marker not in text:
 if len(re.findall(r"\bserver\s*\{",text))!=1:raise SystemExit("Vhost содержит несколько server-блоков; автоматическое изменение запрещено")
 if root_pattern.search(text):raise SystemExit("Vhost уже содержит location /; автоматическое изменение запрещено")
 included=[]
 for include in re.findall(r"\binclude\s+([^;*?\[\]]+)\s*;",text):
  path=pathlib.Path(include)
  if path.is_file():
   value=path.read_text(encoding="utf-8");match=root_pattern.search(value)
   if match:included.append((include,value,match))
 if len(included)>1:raise SystemExit("Vhost получает location / из нескольких include; автоматическое изменение запрещено")
 if included:
  include,value,match=included[0];rendered=replace_block(value,match,snippet)
  directive=re.compile(r"\binclude\s+"+re.escape(include)+r"\s*;")
  text=directive.sub(marker,text,count=1)
 else:
  pos=text.rfind("}")
  if pos<0:raise SystemExit("Некорректный nginx vhost")
  text=text[:pos]+"    "+marker+"\n"+text[pos:]
 backup=source.with_name(source.name+".pre-home-vpn")
 if not backup.exists():shutil.copy2(source,backup);os.chmod(backup,0o600)
 fd,tmp=tempfile.mkstemp(prefix=source.name+".",dir=source.parent,text=True)
 with os.fdopen(fd,"w",encoding="utf-8") as out:out.write(text)
 os.chmod(tmp,0o644);os.replace(tmp,source)
snippet_path.parent.mkdir(parents=True,exist_ok=True)
fd,tmp=tempfile.mkstemp(prefix=snippet_path.name+".",dir=snippet_path.parent,text=True)
with os.fdopen(fd,"w",encoding="utf-8") as out:out.write(rendered)
os.chmod(tmp,0o644);os.replace(tmp,snippet_path)
check=subprocess.run(["nginx","-t"],text=True,capture_output=True)
if check.returncode:
 source.write_text(original_text,encoding="utf-8");os.chmod(source,0o644)
 if old_snippet is None:snippet_path.unlink(missing_ok=True)
 else:snippet_path.write_bytes(old_snippet);os.chmod(snippet_path,0o644)
 subprocess.run(["nginx","-t"],check=False)
 raise SystemExit(check.stderr or check.stdout or "nginx -t failed")
'''
    ssh_command(target,["python3","-c",code,parsed.hostname],input_text=snippet)
    ssh_script(target,"set -Eeuo pipefail\nsystemctl reload nginx\n")


def configure_mode3_public_route(topology, inventories):
    if topology["mode"] != 3:return
    if topology.get("xui_install_mode","fresh")=="preserve":
        configure_preserved_nginx_route(topology);return
    target=topology["site"]["ssh_host"]
    if remote_file_exists(target,"/etc/nginx/kit-stream.conf"):
        configure_kit_nginx_route(topology)
        return
    node=topology["nodes"][0];inventory=inventories[node["id"]]
    result=panel_api(inventory,"GET","/panel/api/inbounds/list")
    rows=result.get("obj") or []
    inbound=next((row for row in rows if str(row.get("protocol")).lower()=="vless" and any(route["inbound_id"]==int(row.get("id") or -1) and route["transport"]=="xhttp" for route in inventory["routes"])),None)
    if not inbound:raise RuntimeError("mode 3: не найден VLESS/XHTTP inbound для TLS fallback HOME-VPN")
    response=panel_api(inventory,"POST","/panel/api/inbounds/update/"+str(inbound["id"]),mode3_fallback_payload(inbound))
    if not response.get("success",True):raise RuntimeError("mode 3: 3x-ui отклонила fallback HOME-VPN")
    ssh_script(topology["site"]["ssh_host"],"systemctl restart x-ui vpn-shop\n")


def smoke_public_route(topology):
    url=topology["site"]["public_url"].rstrip("/")
    command="curl --fail --silent --show-error --max-time 20 --retry 8 --retry-delay 2 --retry-all-errors "+shlex.quote(url+"/health")
    result=ssh_script(topology["site"]["ssh_host"],command+"\n",capture=True)
    try:payload=json.loads(result.stdout)
    except json.JSONDecodeError as error:raise RuntimeError("public_url вернул не HOME-VPN /health") from error
    if payload.get("ok") is not True:raise RuntimeError("public_url HOME-VPN health-check неуспешен")


def enforce_primary_site(topology):
    """Remove a stale edge placeholder from the HOME-VPN control node."""
    target = topology["site"]["ssh_host"]
    ssh_script(target, r'''set -Eeuo pipefail
systemctl disable --now home-vpn-placeholder.service 2>/dev/null || true
rm -rf -- /opt/home-vpn-placeholder
rm -f -- /etc/systemd/system/home-vpn-placeholder.service /etc/home-vpn-placeholder.env /etc/home-vpn/placeholder.html
systemctl daemon-reload
systemctl reset-failed home-vpn-placeholder.service 2>/dev/null || true
systemctl is-active --quiet vpn-shop
''')


def placeholder_nodes(topology):
    if topology["mode"] not in (1, 2):
        return []
    primary = topology["site"]["ssh_host"]
    return [node for node in topology["nodes"] if node["ssh_host"] != primary]


def install_placeholders(topology):
    title = "HOME-VPN"
    url = topology["site"]["public_url"]
    for node in placeholder_nodes(topology):
        environment = " ".join((
            "HOME_VPN_PLACEHOLDER_TITLE=" + shlex.quote(title),
            "HOME_VPN_PLACEHOLDER_URL=" + shlex.quote(url),
            "HOME_VPN_PLACEHOLDER_PORT=8181",
        ))
        ssh_script(node["ssh_host"], f"set -Eeuo pipefail\n{environment} python3 /opt/home-vpn-source/scripts/home-vpn-manager.py configure placeholder\n")


def cascade_ports(rows):
    by_remark = {row["remark"]: int(row["port"]) for row in rows}
    return [443, by_remark["Shadowsocks"]], [by_remark[name] for name in ("Hysteria2", "Shadowsocks", "TUIC", "AmneziaWG", "AmneziaWG-3.1")]


def cascade_cleanup_script():
    return r'''set -Eeuo pipefail
systemctl disable --now home-vpn-cascade.service 2>/dev/null || true
nft delete table ip home_vpn_cascade 2>/dev/null || true
if [ -f /var/lib/home-vpn/cascade-ip-forward.previous ]; then
  previous=$(cat /var/lib/home-vpn/cascade-ip-forward.previous)
  case "$previous" in 0|1) sysctl -w "net.ipv4.ip_forward=$previous" >/dev/null ;; esac
fi
rm -f -- /etc/systemd/system/home-vpn-cascade.service /etc/home-vpn/cascade.nft /etc/sysctl.d/90-home-vpn-cascade.conf /var/lib/home-vpn/cascade-ip-forward.previous
systemctl daemon-reload
'''


def configure_cascade(topology, inventories):
    central=topology["site"];ssh_target=central["ssh_host"]
    if topology["mode"] != 2:
        ssh_script(ssh_target,cascade_cleanup_script())
        return
    prerouting=[];forward=[];postrouting=[]
    for node in topology["nodes"]:
        tcp,udp=cascade_ports(inventories[node["id"]]["rows"]);ingress=node["ingress_address"];destination=node["address"]
        for protocol,ports in (("tcp",tcp),("udp",udp)):
            port_set="{ "+", ".join(map(str,sorted(set(ports))))+" }"
            prerouting.append(f"    ip daddr {ingress} {protocol} dport {port_set} dnat to {destination}")
            forward.append(f"    ip daddr {destination} {protocol} dport {port_set} accept")
            postrouting.append(f"    ip daddr {destination} {protocol} dport {port_set} masquerade")
        forward.append(f"    ip saddr {destination} ct state established,related accept")
    nft="table ip home_vpn_cascade {\n  chain prerouting { type nat hook prerouting priority dstnat; policy accept;\n"+"\n".join(prerouting)+"\n  }\n  chain forward { type filter hook forward priority filter; policy accept;\n"+"\n".join(forward)+"\n  }\n  chain postrouting { type nat hook postrouting priority srcnat; policy accept;\n"+"\n".join(postrouting)+"\n  }\n}\n"
    encoded=base64.b64encode(nft.encode()).decode()
    code=f'''set -Eeuo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update >/dev/null
apt-get install -y nftables >/dev/null
install -d -m 0750 /etc/home-vpn
install -d -m 0750 /var/lib/home-vpn
if [ ! -f /var/lib/home-vpn/cascade-ip-forward.previous ]; then
  sysctl -n net.ipv4.ip_forward > /var/lib/home-vpn/cascade-ip-forward.previous
  chmod 0600 /var/lib/home-vpn/cascade-ip-forward.previous
fi
printf '%s' {shlex.quote(encoded)} | base64 -d > /etc/home-vpn/cascade.nft
chmod 0640 /etc/home-vpn/cascade.nft
systemctl disable --now home-vpn-cascade.service 2>/dev/null || true
nft delete table ip home_vpn_cascade 2>/dev/null || true
nft -c -f /etc/home-vpn/cascade.nft
cat > /etc/sysctl.d/90-home-vpn-cascade.conf <<'EOF'
net.ipv4.ip_forward=1
EOF
sysctl -p /etc/sysctl.d/90-home-vpn-cascade.conf >/dev/null
cat > /etc/systemd/system/home-vpn-cascade.service <<'EOF'
[Unit]
Description=HOME-VPN cascade from central ingress addresses to foreign nodes
After=network-online.target
Wants=network-online.target
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/sbin/nft -f /etc/home-vpn/cascade.nft
ExecStop=-/usr/sbin/nft delete table ip home_vpn_cascade
[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now home-vpn-cascade.service
'''
    ssh_script(ssh_target, code, timeout=300)


def bot_enabled(topology):
    return bool((topology.get("telegram_bot") or {}).get("enabled", True))


def bot_node(topology):
    placement=str((topology.get("telegram_bot") or {}).get("placement") or "control")
    if placement=="control":return None
    return next((node for node in topology.get("nodes") or [] if node.get("id")==placement),None)


def bot_target(topology):
    node=bot_node(topology)
    return node["ssh_host"] if node else topology["site"]["ssh_host"]


def bot_location_label(topology):
    node=bot_node(topology)
    return f"{node['name']} ({node['id']})" if node else "центральная VPS"


def credentials_wizard(topology):
    admin_user=ask("Логин администратора сайта","home-admin")
    admin_password=ask_validated("Пароль администратора сайта","",lambda value:valid_password(value,"Пароль администратора"),True)
    if not bot_enabled(topology):
        return {"admin_user":admin_user,"admin_password":admin_password}
    telegram_token=ask_validated("Telegram Bot Token","",valid_telegram_token,True)
    telegram_admin_id=ask_validated("Цифровой Telegram ID администратора","",valid_telegram_id)
    telegram_channel=ask_validated("Telegram-канал (необязательно)","",valid_telegram_channel)
    panel_user=ask("Логин панели бота","bot-admin")
    panel_password=ask_validated("Пароль панели бота","",lambda value:valid_password(value,"Пароль панели бота"),True)
    return {"admin_user":admin_user,"admin_password":admin_password,"telegram_token":telegram_token,"telegram_admin_id":telegram_admin_id,"telegram_channel":telegram_channel,"bot_panel_user":panel_user,"bot_panel_password":panel_password}


def apply(topology,bootstrap_ssh=False):
    check_servers(topology,bootstrap_ssh)
    credentials=credentials_wizard(topology)
    targets={topology["site"]["ssh_host"]}|{node["ssh_host"] for node in topology["nodes"]}
    with tempfile.TemporaryDirectory(prefix="home-vpn-topology-") as temporary:
        archive=str(pathlib.Path(temporary)/"source.tar.gz");installer=str(pathlib.Path(temporary)/"3x-ui-protocol-installer.sh")
        build_source_archive(archive)
        if topology.get("xui_install_mode","fresh")!="preserve":download_protocol_installer(installer,topology)
        for target in sorted(targets):deploy_source(target,archive)
        if topology.get("xui_install_mode","fresh")=="preserve":
            for node in topology["nodes"]:install_xui_backup(node["ssh_host"])
        else:
            for node in topology["nodes"]:install_kit(node,installer)
    preserved_token=existing_xui_token(topology["site"]["ssh_host"]) if topology.get("xui_install_mode","fresh")=="preserve" else ""
    inventories={node["id"]:node_inventory(node,preserved_token) for node in topology["nodes"]}
    if preserved_token:
        for inventory in inventories.values():panel_api(inventory,"GET","/panel/api/inbounds/list")
    ensure_site_tls(topology)
    install_site(topology,inventories,credentials)
    enforce_primary_site(topology)
    configure_mode3_public_route(topology,inventories)
    bot_result=None
    if bot_enabled(topology):
        install_bot_egress(topology,inventories)
        bot_result=install_bot(topology,credentials)
    install_placeholders(topology)
    configure_cascade(topology,inventories)
    for node in topology["nodes"]:
        services="x-ui nginx home-vpn-kit-backup.timer"+(" kit-sub" if topology.get("xui_install_mode","fresh")!="preserve" else "")
        ssh_script(node["ssh_host"],"systemctl is-active --quiet "+services+"\n")
    ssh_script(topology["site"]["ssh_host"],"systemctl is-active --quiet vpn-shop\n")
    if bot_enabled(topology):
        ssh_script(bot_target(topology),"systemctl is-active --quiet vpn-bot vpn-bot-admin home-vpn-bot-egress\n")
    smoke_public_route(topology)
    print("Установка и проверка ролей завершены")
    print("Сайт:",topology["site"]["public_url"])
    print("Telegram-бот:",bot_location_label(topology) if bot_enabled(topology) else "отключён")
    if bot_result:
        print("Панель Telegram-бота:",bot_result["public_url"])
        print("Логин панели Telegram-бота:",bot_result["user"])
        print("Пароль панели Telegram-бота:",bot_result["password"])
    print("Сохраните показанные реквизиты в менеджере паролей и очистите scrollback терминала.")


def update_managed(topology):
    """Update project-owned files on every role without changing databases or secrets."""
    check_servers(topology)
    targets={topology["site"]["ssh_host"]}|{node["ssh_host"] for node in topology["nodes"]}
    with tempfile.TemporaryDirectory(prefix="home-vpn-topology-update-") as temporary:
        archive=str(pathlib.Path(temporary)/"source.tar.gz");build_source_archive(archive)
        for target in sorted(targets):deploy_source(target,archive)
    central=topology["site"]["ssh_host"]
    ssh_command(central,["python3","/opt/home-vpn-source/scripts/home-vpn-manager.py","update","site"])
    if bot_enabled(topology):
        ssh_command(bot_target(topology),["python3","/opt/home-vpn-source/scripts/home-vpn-manager.py","update","bot"])
        if bot_target(topology)!=central and central=="local":install_bot_admin_tunnel(topology,bot_target(topology))
    for node in topology["nodes"]:
        install_xui_backup(node["ssh_host"])
    for node in placeholder_nodes(topology):
        ssh_command(node["ssh_host"],["python3","/opt/home-vpn-source/scripts/home-vpn-manager.py","update","placeholder"])
    ssh_script(central,"systemctl is-active --quiet vpn-shop\n")
    if bot_enabled(topology):ssh_script(bot_target(topology),"systemctl is-active --quiet vpn-bot vpn-bot-admin home-vpn-bot-egress\n")
    for node in topology["nodes"]:ssh_script(node["ssh_host"],"systemctl is-active --quiet x-ui nginx home-vpn-kit-backup.timer\n")
    smoke_public_route(topology)
    print("Все управляемые роли HOME-VPN обновлены; базы, секреты и клиенты 3x-ui сохранены")


def remove_managed(topology,purge=False):
    """Remove only HOME-VPN-owned roles while preserving every 3x-ui node."""
    check_servers(topology)
    central=topology["site"]["ssh_host"]
    for node in placeholder_nodes(topology):
        ssh_command(
            node["ssh_host"],
            ["python3","/opt/home-vpn-source/scripts/home-vpn-manager.py","remove","placeholder",*(["--purge-data"] if purge else [])],
            input_text="DELETE HOME-VPN\n",
        )
    configure_cascade({**topology,"mode":1}, {})
    remove_bot_admin_tunnel(topology)
    if bot_enabled(topology):
        ssh_command(
            bot_target(topology),
            ["python3","/opt/home-vpn-source/scripts/home-vpn-manager.py","remove","bot",*(["--purge-data"] if purge else [])],
            input_text="DELETE HOME-VPN\n",
        )
    ssh_command(
        central,
        ["python3","/opt/home-vpn-source/scripts/home-vpn-manager.py","remove","site",*(["--purge-data"] if purge else [])],
        input_text="DELETE HOME-VPN\n",
    )
    print("HOME-VPN удалён со всех ролей; 3x-ui, inbounds, клиенты и её backup сохранены")


def parse_arguments():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config",type=pathlib.Path,help="не содержащий секретов JSON-план")
    parser.add_argument("--write-plan",type=pathlib.Path,help="сохранить выбранный не секретный план")
    parser.add_argument("--mode",type=int,choices=tuple(sorted(TOPOLOGY_MODES)),help="заранее выбранный режим архитектуры")
    parser.add_argument("--local-control-node",action="store_true",help="текущая VPS является управляющей нодой mode 1/2")
    parser.add_argument("--apply",action="store_true")
    parser.add_argument("--update-managed",action="store_true",help="обновить HOME-VPN на всех ролях без изменения данных")
    parser.add_argument("--remove-managed",action="store_true",help="удалить HOME-VPN со всех ролей, сохранив 3x-ui")
    parser.add_argument("--purge-data",action="store_true",help="при удалении также удалить данные HOME-VPN")
    parser.add_argument("--confirm",default="")
    parser.add_argument("--interactive-confirm",action="store_true",help="запросить контрольную фразу после показа плана")
    parser.add_argument("--bootstrap-ssh",action="store_true",help="однократно запросить пароль удалённых VPS через ssh-copy-id")
    return parser.parse_args()


def require_confirmation():
    while True:
        value=input(f"Введите {CONFIRMATION!r} для применения или 0 для отмены: ").strip()
        if value==CONFIRMATION:return
        if value=="0":raise SystemExit("Операция отменена")
        print(f"Ответ не совпадает. Введите точно {CONFIRMATION!r} или 0 для отмены.")


def main():
    arguments=parse_arguments()
    actions=sum(bool(value) for value in (arguments.apply,arguments.update_managed,arguments.remove_managed))
    if actions>1:raise SystemExit("Выберите только одно действие: --apply, --update-managed или --remove-managed")
    if (arguments.update_managed or arguments.remove_managed) and not arguments.config:
        raise SystemExit("Для update/remove требуется существующий проверенный --config plan")
    if arguments.purge_data and not arguments.remove_managed:
        raise SystemExit("--purge-data допустим только вместе с --remove-managed")
    if arguments.interactive_confirm and not arguments.apply:
        raise SystemExit("--interactive-confirm допустим только вместе с --apply")
    if arguments.bootstrap_ssh and not arguments.apply:
        raise SystemExit("--bootstrap-ssh допустим только вместе с --apply")
    if arguments.config and (arguments.mode is not None or arguments.local_control_node):
        raise SystemExit("--mode и --local-control-node используются только с интерактивным мастером без --config")
    topology=json.loads(arguments.config.read_text(encoding="utf-8")) if arguments.config else wizard(arguments.mode,arguments.local_control_node)
    validate_topology(topology);show_plan(topology)
    if arguments.write_plan:
        arguments.write_plan.write_text(json.dumps(topology,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        arguments.write_plan.chmod(0o600)
        print("План сохранён без паролей и токенов:",arguments.write_plan)
    if arguments.update_managed:
        update_managed(topology);return
    if arguments.remove_managed:
        if arguments.confirm!=REMOVE_CONFIRMATION:raise SystemExit(f"Для удаления добавьте --confirm {REMOVE_CONFIRMATION!r}")
        remove_managed(topology,arguments.purge_data);return
    if arguments.apply and arguments.interactive_confirm:require_confirmation()
    if not arguments.apply or (arguments.confirm!=CONFIRMATION and not arguments.interactive_confirm):
        raise SystemExit("Dry-run: для установки добавьте --apply --confirm 'INSTALL HOME VPN TOPOLOGY'")
    apply(topology,arguments.bootstrap_ssh)


if __name__=="__main__":main()
