#!/usr/bin/env python3
"""Small HTTPS GUI for a multi-server HOME-VPN backup repository."""

import base64
import hashlib
import hmac
import html
import json
import os
import pathlib
import re
import secrets
import ssl
import subprocess
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

BIND = os.getenv("BACKUP_PANEL_BIND", "0.0.0.0")
PORT = int(os.getenv("BACKUP_PANEL_PORT", "9443"))
USER = os.getenv("BACKUP_PANEL_USER", "backup-admin")
PASSWORD = os.getenv("BACKUP_PANEL_PASSWORD", "")
SECRET = os.getenv("BACKUP_PANEL_SECRET", "change-me").encode()
ENTRY = os.getenv("BACKUP_PANEL_ENTRY_PATH", "/backup-control")
CERT = os.getenv("BACKUP_PANEL_TLS_CERT", "")
KEY = os.getenv("BACKUP_PANEL_TLS_KEY", "")
ROOT = pathlib.Path("/srv/home-vpn-backups")
APP_VERSION = os.getenv("HOME_VPN_VERSION", "0.0.3-pre")
ATTEMPTS = {}
SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{1,31}$")
ARCHIVE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{1,127}\.tar\.gz$")


def control(*args):
    result = subprocess.run(
        ["sudo", "-n", "/usr/local/sbin/home-vpn-backup-panel-control", *args],
        capture_output=True, text=True, timeout=180,
    )
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout or "Ошибка операции")[-600:])
    return json.loads(result.stdout or "{}")


def size(value):
    value = float(value)
    for unit in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
        if value < 1024:
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} ПБ"


def page(title, body):
    css = """*{box-sizing:border-box}body{margin:0;background:#07101d;color:#e8eff8;font:16px system-ui}.w{max-width:1200px;margin:auto;padding:20px}.head{display:flex;justify-content:space-between;gap:12px;align-items:center;flex-wrap:wrap}.brand{font-size:23px;font-weight:850}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px}.card{background:#101d31;border:1px solid #30415a;border-radius:15px;padding:17px;margin:12px 0}.configuration{border-top:4px solid #409eff}.configuration-title{font-size:24px;margin:0 0 5px}.node{margin-top:14px;padding:15px;border:1px solid #2d405b;border-left:5px solid #409eff;border-radius:12px;background:#0a1627}.node.off{border-left-color:#e6a23c}.node.unknown{border-left-color:#7b8799}.node h3{margin:0;font-size:19px}.archive{display:grid;grid-template-columns:1fr auto;gap:10px;border-top:1px solid #293b55;padding:12px 0}.archives{margin-top:10px;border-top:1px solid #263850}.archives summary{padding:12px 0;cursor:pointer;font-weight:800;color:#bcd0e8}.btn,button{display:inline-block;border:0;border-radius:9px;padding:10px 13px;background:#409eff;color:#fff;text-decoration:none;font-weight:750;cursor:pointer}.soft{background:#263a57}.danger{background:#d85454}.ok{color:#67c23a}.warn{color:#e6a23c}.bad{color:#f56c6c}input,select,textarea{width:100%;padding:11px;border:1px solid #405575;border-radius:8px;background:#091424;color:#fff;margin:5px 0 12px}textarea{min-height:95px;resize:vertical}input[type=checkbox]{width:auto;margin-right:8px}.checkrow{display:flex;align-items:center;gap:8px;padding:10px;border:1px solid #2c3f59;border-radius:9px;margin:7px 0}.checkrow input{margin:0;width:20px;height:20px}form.inline{display:inline}.login{max-width:430px;margin:10vh auto}.muted,small{color:#9daec4}.flash{border-color:#4a8e62}.actions{display:flex;gap:7px;flex-wrap:wrap;align-items:center}.bar{height:10px;background:#25364e;border-radius:8px;overflow:hidden}.bar i{display:block;height:100%;background:#409eff}code,pre{background:#081321;border-radius:7px;padding:3px 6px;overflow:auto}.steps{line-height:1.65}.badge{display:inline-flex;align-items:center;padding:5px 9px;border-radius:999px;font-size:12px;font-weight:800;background:#263a57;color:#cbd7e7}.badge.good{background:#173b31;color:#72e2b9}.badge.wait{background:#473817;color:#f2c767}.filter{display:grid;grid-template-columns:1.4fr 1.4fr 1fr auto;gap:10px;align-items:end}.filter select{margin-bottom:0}.filter button{min-height:43px}.chips{display:flex;gap:7px;flex-wrap:wrap;margin-top:10px}.server-meta{display:flex;gap:7px;flex-wrap:wrap;margin:8px 0 2px}.explain{padding:12px 14px;border-radius:10px;background:#0a1627;color:#b9c8da}.empty{text-align:center;padding:36px 20px}@media(max-width:620px){.w{padding:11px}.archive{grid-template-columns:1fr}.actions>*{flex:1;text-align:center}.filter{grid-template-columns:1fr}.filter button{width:100%}.node{padding:12px}}"""
    return f"<!doctype html><html lang=ru><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>{html.escape(title)}</title><style>{css}</style><body><div class=w>{body}</div></body></html>".encode()


def csrf():
    return hmac.new(SECRET, b"backup-panel-csrf", hashlib.sha256).hexdigest()


def hidden(name, value):
    return f'<input type=hidden name="{html.escape(name, quote=True)}" value="{html.escape(str(value), quote=True)}">'


class Handler(BaseHTTPRequestHandler):
    def sendx(self, status, data, ctype="text/html; charset=utf-8", extra=None):
        if isinstance(data, str):
            data = data.encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Strict-Transport-Security", "max-age=31536000")
        self.send_header("Content-Security-Policy", "default-src 'self' 'unsafe-inline'; img-src 'self' data:")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def redirect(self, target, cookie=None):
        self.send_response(303)
        self.send_header("Location", target)
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()

    def session(self):
        raw = next((item.split("=", 1)[1] for item in self.headers.get("Cookie", "").split("; ") if item.startswith("backup_panel=")), "")
        try:
            body, signature = raw.rsplit(".", 1)
            name, expires = base64.urlsafe_b64decode(body + "==").decode().split("|", 1)
            return name == USER and int(expires) > time.time() and hmac.compare_digest(signature, hmac.new(SECRET, body.encode(), hashlib.sha256).hexdigest())
        except Exception:
            return False

    def token(self):
        body = base64.urlsafe_b64encode(f"{USER}|{int(time.time() + 8 * 3600)}".encode()).decode().rstrip("=")
        return body + "." + hmac.new(SECRET, body.encode(), hashlib.sha256).hexdigest()

    def data(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode()
        parsed=parse_qs(raw,keep_blank_values=True);result={key:value[0] for key,value in parsed.items()};result["_server_ids"]=parsed.get("server_id",[]);return result

    def header(self):
        return f'<div class=head><div class=brand>💾 HOME-VPN · Backup-сервер <small>v{html.escape(APP_VERSION)}</small></div><div class=actions><b>{html.escape(USER)}</b><a class="btn soft" href=/>Обзор и архивы</a><a class="btn soft" href=/configurations/add>＋ Конфигурация</a><a class=btn href=/servers/add>＋ Backup-агент</a><a class="btn soft" href=/logout>Выйти</a></div></div>'

    def inventory(self):
        return control("inventory")

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == ENTRY:
            if self.session():
                return self.redirect("/")
            form = f'<div class="card login"><h1>Резервное хранилище</h1><p>Закрытая панель управления.</p><form method=post action="{html.escape(ENTRY, quote=True)}"><label>Логин</label><input name=username required><label>Пароль</label><input type=password name=password required><button>Войти</button></form></div>'
            return self.sendx(200, page("Вход", form))
        if path == "/logout":
            return self.redirect(ENTRY, "backup_panel=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Strict")
        if not self.session():
            return self.redirect(ENTRY)
        if path == "/servers/add":
            return self.show_add_server(parsed)
        if path == "/configurations/add":
            return self.show_configuration("")
        configuration_match=re.fullmatch(r"/configurations/([a-z0-9][a-z0-9_-]{1,31})",path)
        if configuration_match:
            return self.show_configuration(configuration_match.group(1))
        adopt_match = re.fullmatch(r"/servers/([a-z0-9][a-z0-9_-]{1,31})/adopt", path)
        if adopt_match:
            return self.show_adopt_archive(adopt_match.group(1))
        delete_match = re.fullmatch(r"/servers/([a-z0-9][a-z0-9_-]{1,31})/delete", path)
        if delete_match:
            return self.show_delete_server(delete_match.group(1))
        if path.startswith("/download/"):
            return self.download(path)
        if path.startswith("/recovery/"):
            return self.recovery(path)
        if path != "/":
            return self.sendx(404, "not found", "text/plain")
        return self.dashboard(parsed)

    def show_configuration(self, configuration_id):
        try:inventory=self.inventory()
        except Exception as error:return self.error(error)
        configuration=next((item for item in inventory.get("configurations",[]) if item.get("id")==configuration_id),{})
        if configuration_id and not configuration:return self.sendx(404,"configuration not found","text/plain")
        members={server.get("id") for server in inventory.get("servers",[]) if server.get("configuration_id")==configuration_id}
        checks=[]
        for server in inventory.get("servers",[]):
            checked=" checked" if server.get("id") in members else ""
            role={"site":"Сайт + основная 3x-ui","bot":"Telegram-бот + 3x-ui","node":"Дополнительная 3x-ui-нода","archive":"Архив"}.get(server.get("role"),server.get("role") or "роль не указана")
            checks.append(f'<label class=checkrow><input type=checkbox name=server_id value="{html.escape(server["id"],quote=True)}"{checked}><span><b>{html.escape(server.get("name") or server["id"])}</b><br><small>{html.escape(role)} · ID {html.escape(server["id"])}</small></span></label>')
        title="Изменить конфигурацию" if configuration else "Новая конфигурация"
        body=self.header()+f'''<div class=card><h1>{title}</h1><p>Конфигурация объединяет сайт, Telegram-бот и 3x-ui-ноды одного независимого проекта. Архивы внутри неё остаются раздельными по серверам.</p><form method=post action=/action>{hidden('csrf',csrf())}{hidden('action','upsert-configuration')}<label>ID конфигурации</label><input name=configuration_id required pattern="[a-z0-9][a-z0-9_-]{{1,31}}" value="{html.escape(configuration.get('id',''),quote=True)}" {'readonly' if configuration else ''} placeholder=home-vpn-production><label>Название</label><input name=name required maxlength=80 value="{html.escape(configuration.get('name',''),quote=True)}" placeholder="HOME-VPN Production"><label>Описание</label><textarea name=description maxlength=240 placeholder="Основная рабочая конфигурация">{html.escape(configuration.get('description',''))}</textarea><h2>Серверы этой конфигурации</h2>{''.join(checks) or '<p class=muted>Сначала добавьте серверы.</p>'}<div class=actions><button>Сохранить конфигурацию</button><a class="btn soft" href=/>Отмена</a></div></form></div>'''
        return self.sendx(200,page(title,body))

    def show_add_server(self, parsed):
        query=parse_qs(parsed.query);prefill_id=query.get("server_id",[""])[0] if SLUG.fullmatch(query.get("server_id",[""])[0]) else "";prefill_name=query.get("name",[""])[0][:80]
        try:inventory=self.inventory();servers=inventory.get("servers",[]);configurations=inventory.get("configurations",[])
        except Exception:servers=[];configurations=[]
        registered=[server for server in servers if server.get("created_at")]
        chips="".join(f'<span class="badge good">✓ {html.escape(server.get("name") or server["id"])}</span>' for server in registered) or '<span class=muted>Пока нет серверов, добавленных через GUI.</span>'
        config_options='<option value="">Без конфигурации</option>'+''.join(f'<option value="{html.escape(config["id"],quote=True)}">{html.escape(config["name"])}</option>' for config in configurations)
        body = self.header() + f'''<div class=card><h1>Добавить сервер</h1><div class=explain><b>Уже добавлены в GUI:</b><div class=chips>{chips}</div></div><ol class=steps><li>На подключаемом сервере запустите <code>sudo bash install.sh</code>.</li><li>Выберите <b>Backup-агент рабочего сервера</b>.</li><li>Скопируйте показанный публичный ключ. Это не закрытый ключ и не пароль.</li><li>Заполните форму ниже, затем запустите на рабочем сервере первую резервную копию.</li></ol><form method=post action=/action>{hidden('csrf',csrf())}{hidden('action','add')}<div class=grid><div><label>ID сервера</label><input name=server_id value="{html.escape(prefill_id,quote=True)}" placeholder=prod-ru required pattern="[a-z0-9][a-z0-9_-]{{1,31}}"><label>Название</label><input name=name value="{html.escape(prefill_name,quote=True)}" placeholder="Основной RU" required><label>Конфигурация</label><select name=configuration_id>{config_options}</select></div><div><label>Роль</label><select name=role><option value=site>Сайт + основная нода</option><option value=bot>Telegram-бот + нода</option><option value=node>Дополнительная нода</option></select><label>IP или домен рабочего сервера</label><input name=expected_host placeholder=192.168.1.50><label>Хранить копии, дней</label><input type=number min=1 max=3650 name=retention value=30></div></div><label>Публичный SSH-ключ backup-агента</label><textarea name=public_key required placeholder="ssh-ed25519 AAAA..."></textarea><div class=actions><button>Добавить сервер</button><a class="btn soft" href=/>Отмена</a></div></form></div>'''
        return self.sendx(200, page("Добавить сервер", body))

    def show_delete_server(self, server_id):
        try:
            server = next((item for item in self.inventory().get("servers", []) if item.get("id") == server_id), None)
        except Exception as error:
            return self.error(error)
        if not server or not server.get("created_at"):
            return self.sendx(404, "server not found", "text/plain")
        description="Это архивный профиль без активного SSH-ключа." if server.get("profile_type")=="archive" else "SSH-ключ будет отозван, новые копии перестанут приниматься."
        body = self.header() + f'''<div class=card><h1 class=bad>Удалить сервер «{html.escape(server.get('name',server_id))}»?</h1><p>{html.escape(description)} Профиль исчезнет из панели.</p><form method=post action=/action>{hidden('csrf',csrf())}{hidden('action','remove-server')}{hidden('server_id',server_id)}<label>Для подтверждения введите ID: <b>{html.escape(server_id)}</b></label><input name=confirm_id autocomplete=off required><label><input type=checkbox name=purge value=1>Также безвозвратно удалить все архивы этого сервера</label><p class=warn>Если флажок не установлен, архивы будут перенесены в <code>/srv/home-vpn-backups-removed</code> и не будут видны в панели.</p><div class=actions><button class=danger>Удалить сервер</button><a class="btn soft" href=/>Отмена</a></div></form></div>'''
        return self.sendx(200, page("Удалить сервер", body))

    def show_adopt_archive(self, server_id):
        try:server=next((item for item in self.inventory().get("servers",[]) if item.get("id")==server_id),None)
        except Exception as error:return self.error(error)
        if not server or server.get("created_at"):return self.sendx(404,"archive directory not found","text/plain")
        suggested="HOME-VPN-Test" if server_id=="primary" else server_id
        body=self.header()+f'''<div class=card><h1>Добавить архив в GUI</h1><p>Каталог <code>{html.escape(server_id)}</code> уже содержит {len(server.get('archives',[]))} архивов, но не связан с действующим backup-агентом.</p><div class=explain>Будет создан только понятный архивный профиль. SSH-ключ и приём новых копий не включаются.</div><form method=post action=/action>{hidden('csrf',csrf())}{hidden('action','adopt-archive')}{hidden('server_id',server_id)}<label>Название в панели</label><input name=name required maxlength=80 value="{html.escape(suggested,quote=True)}"><div class=actions><button>Добавить архивный профиль</button><a class="btn soft" href=/>Отмена</a></div></form></div>'''
        return self.sendx(200,page("Добавить архив в GUI",body))

    def download(self, path):
        parts = path.split("/")
        if len(parts) != 4 or not SLUG.fullmatch(parts[2]) or not ARCHIVE.fullmatch(parts[3]):
            return self.sendx(400, "bad path", "text/plain")
        file = ROOT / parts[2] / parts[3]
        if not file.is_file() or file.is_symlink():
            return self.sendx(404, "not found", "text/plain")
        self.send_response(200)
        self.send_header("Content-Type", "application/gzip")
        self.send_header("Content-Length", str(file.stat().st_size))
        self.send_header("Content-Disposition", f'attachment; filename="{file.name}"')
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        with file.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                self.wfile.write(block)

    def recovery(self, path):
        parts = path.split("/")
        if len(parts) != 4 or not SLUG.fullmatch(parts[2]) or not ARCHIVE.fullmatch(parts[3]):
            return self.sendx(400, "bad path", "text/plain")
        server, name = parts[2], parts[3]
        command = f"sudo home-vpn-full-restore /root/{name} --apply --replace-host=OLD_IP=NEW_IP"
        body = self.header() + f'''<div class=card><h2>{html.escape(name)}</h2><ol class=steps><li>Подготовьте чистый Debian той же архитектуры.</li><li>Установите 3x-ui и зависимости проекта.</li><li>Скачайте архив и скопируйте его на новый сервер.</li><li>Сначала проверьте архив без <code>--apply</code>.</li><li>Для восстановления выполните:</li></ol><pre>{html.escape(command)}</pre><p class=warn>После восстановления обновите DNS, TLS, firewall и адреса нод. Старый сервер не выключайте до полной проверки.</p><a class=btn href="/download/{quote(server)}/{quote(name)}">Скачать архив</a></div>'''
        return self.sendx(200, page("Восстановление", body))

    def dashboard(self, parsed):
        try:
            inventory, error = self.inventory(), ""
        except Exception as exception:
            inventory = {"servers": [], "disk": {"total": 1, "free": 0}}
            error = f'<div class=card><b class=bad>{html.escape(str(exception))}</b></div>'
        query = parse_qs(parsed.query)
        flash = f'<div class="card flash"><b class=ok>{html.escape(query.get("message", [""])[0])}</b></div>' if query.get("message") else ""
        servers=inventory.get("servers",[]);configurations=inventory.get("configurations",[]);registered_count=sum(bool(server.get("created_at")) for server in servers);detected_count=len(servers)-registered_count
        selected=query.get("server",["all"])[0];selected_configuration=query.get("configuration",["all"])[0];scope=query.get("scope",["all"])[0]
        if selected!="all" and not SLUG.fullmatch(selected):selected="all"
        if selected_configuration!="all" and selected_configuration!="unassigned" and not SLUG.fullmatch(selected_configuration):selected_configuration="all"
        if scope not in ("all","registered","detected"):scope="all"
        filtered=[server for server in servers if (scope=="all" or (scope=="registered" and server.get("created_at")) or (scope=="detected" and not server.get("created_at"))) and (selected=="all" or server.get("id")==selected) and (selected_configuration=="all" or (selected_configuration=="unassigned" and not server.get("configuration_id")) or server.get("configuration_id")==selected_configuration)]
        options=['<option value=all>Все серверы</option>']
        for server in servers:
            marker="🗄 архив GUI" if server.get("profile_type")=="archive" else "✓ GUI" if server.get("created_at") else "⚠ только архивы"
            is_selected=" selected" if selected==server.get("id") else ""
            options.append(f'<option value="{html.escape(server["id"],quote=True)}"{is_selected}>{marker} · {html.escape(server.get("name") or server["id"])}</option>')
        options[0]='<option value=all selected>Все серверы</option>' if selected=="all" else options[0]
        configuration_options=['<option value=all>Все конфигурации</option>']
        for configuration in configurations:
            chosen=" selected" if selected_configuration==configuration.get("id") else ""
            configuration_options.append(f'<option value="{html.escape(configuration["id"],quote=True)}"{chosen}>{html.escape(configuration["name"])}</option>')
        configuration_options.append(f'<option value=unassigned{" selected" if selected_configuration=="unassigned" else ""}>Без конфигурации</option>')
        if selected_configuration=="all":configuration_options[0]='<option value=all selected>Все конфигурации</option>'
        scope_options="".join(f'<option value={value}{" selected" if scope==value else ""}>{label}</option>' for value,label in (("all","Все типы"),("registered","Добавлены в GUI"),("detected","Только обнаруженные архивы")))
        disk = inventory["disk"]
        used = max(0, disk["total"] - disk["free"])
        percent = min(100, round(used * 100 / max(1, disk["total"])))
        grouped={}
        for server in filtered:grouped.setdefault(server.get("configuration_id") or "unassigned",[]).append(server)
        config_index={item.get("id"):item for item in configurations}
        cards=[]
        for configuration in configurations:
            if grouped.get(configuration.get("id")):cards.append(self.configuration_card(configuration,grouped.pop(configuration["id"])))
        if grouped.get("unassigned"):cards.append(self.configuration_card({"id":"unassigned","name":"Без конфигурации","description":"Серверы ещё не объединены в проект."},grouped.pop("unassigned")))
        for configuration_id,members in grouped.items():cards.append(self.configuration_card(config_index.get(configuration_id,{"id":configuration_id,"name":"Неизвестная конфигурация"}),members))
        selector=f'''<div class=card><div class=head><div><h2>Выбор конфигурации</h2><p class=muted>Одна конфигурация объединяет сайт, бота и все её 3x-ui-ноды.</p></div><div class=actions><a class="btn soft" href=/configurations/add>＋ Конфигурация</a><a class=btn href=/servers/add>＋ Сервер</a></div></div><form class=filter method=get action=/><div><label>Конфигурация</label><select name=configuration>{''.join(configuration_options)}</select></div><div><label>Сервер внутри конфигурации</label><select name=server>{''.join(options)}</select></div><div><label>Показывать</label><select name=scope>{scope_options}</select></div><button>Показать</button></form><div class=chips><span class=badge>Конфигураций: {len(configurations)}</span><span class="badge good">✓ Серверов в GUI: {registered_count}</span><span class="badge wait">⚠ Только архивы: {detected_count}</span></div></div>'''
        empty='<div class="card empty"><h2>По выбранному фильтру серверов нет</h2><p class=muted>Сбросьте фильтр или добавьте новый сервер через GUI.</p><div class=actions style="justify-content:center"><a class="btn soft" href=/>Показать все</a><a class=btn href=/servers/add>＋ Добавить сервер</a></div></div>'
        body = self.header() + flash + error + f'''<div class=grid><div class=card><small>Диск</small><h2>{size(used)} / {size(disk['total'])}</h2><div class=bar><i style="width:{percent}%"></i></div><p>Свободно {size(disk['free'])}</p></div><div class=card><small>Конфигурации и серверы</small><h2>{len(configurations)} / {len(servers)}</h2><p><b>{len(configurations)}</b> конфигурации · <b>{len(servers)}</b> серверов.</p><p class=muted>Каждый сервер хранит собственные независимые архивы.</p></div></div>{selector}{''.join(cards) or empty}'''
        return self.sendx(200, page("Резервные копии", body))

    def configuration_card(self,configuration,servers):
        total_archives=sum(len(server.get("archives",[])) for server in servers);total_size=sum(server.get("total_size",0) for server in servers)
        roles=[]
        for server in servers:
            roles.extend({"site":["Сайт","3x-ui","Основная нода"],"bot":["Telegram-бот","3x-ui","Дополнительная нода"],"node":["3x-ui","Дополнительная нода"],"archive":["Архив"]}.get(server.get("role"),[server.get("role") or "Сервер"]))
        role_badges="".join(f'<span class=badge>{html.escape(role)}</span>' for role in dict.fromkeys(roles))
        edit=f'<a class="btn soft" href="/configurations/{quote(configuration["id"])}">Настроить</a>' if configuration.get("id")!="unassigned" else '<a class="btn soft" href=/configurations/add>Объединить серверы</a>'
        return f'''<section class="card configuration"><div class=head><div><h2 class=configuration-title>{html.escape(configuration.get('name') or configuration['id'])}</h2><p class=muted>{html.escape(configuration.get('description') or 'Единая конфигурация серверов')}</p><div class=chips>{role_badges}</div></div>{edit}</div><p><b>{len(servers)}</b> серверов · <b>{total_archives}</b> архивов · {size(total_size)}</p>{''.join(self.server_card(server) for server in servers)}</section>'''

    def server_card(self, server):
        archives = []
        for item in server.get("archives", []):
            state = '<span class=ok>v2</span>' if item["format"] == "home-vpn-full-v2" else ('<span class=warn>legacy</span>' if item["format"] == "legacy-v1" else '<span class=bad>ошибка</span>')
            actions = f'''<a class="btn soft" href="/download/{quote(server['id'])}/{quote(item['name'])}">Скачать</a><form class=inline method=post action=/action>{hidden('csrf',csrf())}{hidden('action','verify')}{hidden('server_id',server['id'])}{hidden('archive',item['name'])}<button class=soft>Проверить</button></form><a class="btn soft" href="/recovery/{quote(server['id'])}/{quote(item['name'])}">Восстановить</a><form class=inline method=post action=/action onsubmit="return confirm('Удалить архив без возможности восстановления?')">{hidden('csrf',csrf())}{hidden('action','delete')}{hidden('server_id',server['id'])}{hidden('archive',item['name'])}<button class=danger>Удалить архив</button></form>'''
            archives.append(f'''<div class=archive><div><b>{html.escape(item['name'])}</b><br><small>{state} · {size(item['size'])} · {time.strftime('%d.%m.%Y %H:%M',time.localtime(item['mtime']))}</small></div><div class=actions>{actions}</div></div>''')
        registered = bool(server.get("created_at"))
        archive_profile=server.get("profile_type")=="archive"
        enabled = bool(server.get("enabled", True))
        controls = ""
        if registered and not archive_profile:
            action, label, style = ("disable", "Отключить приём", "soft") if enabled else ("enable", "Включить приём", "")
            controls = f'''<form class=inline method=post action=/action>{hidden('csrf',csrf())}{hidden('action',action)}{hidden('server_id',server['id'])}<button class="{style}">{label}</button></form><a class="btn danger" href="/servers/{quote(server['id'])}/delete">Удалить сервер</a>'''
        elif archive_profile:
            controls=f'''<a class="btn danger" href="/servers/{quote(server['id'])}/delete">Удалить архивный профиль</a>'''
        else:
            controls=f'''<a class=btn href="/servers/{quote(server['id'])}/adopt">Добавить архив в GUI</a>'''
        last = server.get("archives", [{}])[0].get("mtime") if server.get("archives") else 0
        fresh = '<span class=ok>копия свежая</span>' if last and time.time() - last < 36 * 3600 else '<span class=warn>нет свежей копии</span>'
        reception = '<span class=muted>архивное хранение, приём не настроен</span>' if archive_profile else (('<span class=ok>приём включён</span>' if enabled else '<span class=warn>приём отключён</span>') if registered else '<span class=muted>приём не настроен</span>')
        registration='<span class=badge>🗄 Архив добавлен в GUI</span>' if archive_profile else '<span class="badge good">✓ Добавлен в GUI</span>' if registered else '<span class="badge wait">⚠ Не добавлен в GUI</span>'
        host=f'<span class=badge>Адрес: {html.escape(server.get("expected_host") or "не указан")}</span>' if registered else ''
        css_state='off' if registered and not enabled else 'unknown' if not registered else ''
        role_text={"site":"Сайт + основная 3x-ui-нода","bot":"Telegram-бот + дополнительная 3x-ui-нода","node":"Дополнительная 3x-ui-нода","archive":"Архив прежней конфигурации"}.get(server.get("role"),server.get("role","не определена"))
        archive_block=f'<details class=archives><summary>Архивы сервера: {len(archives)}</summary>{"".join(archives)}</details>' if archives else '<p class=muted>Архивов пока нет.</p>'
        return f'''<div class="node {css_state}"><div class=head><div><h3>{html.escape(server['name'])}</h3><small>ID: {html.escape(server['id'])} · {html.escape(role_text)}</small><div class=server-meta>{registration}{host}</div></div><div class=actions>{controls}</div></div><p>{reception} · {fresh} · архивов {len(server.get('archives',[]))} · {size(server.get('total_size',0))}</p>{archive_block}</div>'''

    def do_POST(self):
        path = urlparse(self.path).path
        data = self.data()
        ip = self.client_address[0]
        if path == ENTRY:
            recent = [item for item in ATTEMPTS.get(ip, []) if item > time.time() - 600]
            ATTEMPTS[ip] = recent
            if len(recent) >= 10:
                return self.sendx(429, "Слишком много попыток", "text/plain")
            if not (secrets.compare_digest(data.get("username", ""), USER) and secrets.compare_digest(data.get("password", ""), PASSWORD)):
                recent.append(time.time())
                return self.sendx(401, page("Ошибка", f'<div class="card login"><h1 class=bad>Неправильный логин или пароль</h1><a class=btn href="{html.escape(ENTRY,quote=True)}">Повторить</a></div>'))
            return self.redirect("/", f"backup_panel={self.token()}; Path=/; Max-Age=28800; HttpOnly; Secure; SameSite=Strict")
        if not self.session():
            return self.redirect(ENTRY)
        if path != "/action" or not secrets.compare_digest(data.get("csrf", ""), csrf()):
            return self.sendx(403, "forbidden", "text/plain")
        try:
            action = data.get("action")
            if action == "add":
                payload = {"id": data.get("server_id"), "name": data.get("name"), "role": data.get("role"), "expected_host": data.get("expected_host"), "public_key": data.get("public_key"), "retention_days": data.get("retention"),"configuration_id":data.get("configuration_id")}
                control("add", json.dumps(payload, ensure_ascii=False))
                message = "Сервер добавлен. Запустите первую копию на рабочем сервере."
            elif action == "upsert-configuration":
                payload={"id":data.get("configuration_id"),"name":data.get("name"),"description":data.get("description"),"server_ids":data.get("_server_ids",[]),"replace_members":True}
                control("upsert-configuration",json.dumps(payload,ensure_ascii=False));message="Конфигурация серверов сохранена."
            elif action == "adopt-archive":
                control("adopt-archive",json.dumps({"id":data.get("server_id"),"name":data.get("name")},ensure_ascii=False))
                message = "Архивный профиль добавлен в GUI; файлы остались на месте."
            elif action in ("disable", "enable"):
                control(action, data.get("server_id", ""))
                message = "Приём копий включён." if action == "enable" else "Приём копий отключён; профиль и архивы сохранены."
            elif action == "remove-server":
                server_id = data.get("server_id", "")
                if not secrets.compare_digest(data.get("confirm_id", ""), server_id):
                    raise RuntimeError("ID подтверждения не совпадает")
                arguments = ["remove-server", server_id]
                if data.get("purge") == "1":
                    arguments.append("--purge")
                result = control(*arguments)
                message = "Сервер и его архивы удалены." if result.get("purged") else "Сервер удалён; архивы сохранены в карантине."
            elif action == "delete":
                control("delete", data.get("server_id", ""), data.get("archive", ""))
                message = "Архив удалён."
            elif action == "verify":
                control("verify", data.get("server_id", ""), data.get("archive", ""))
                message = "Архив и контрольные суммы проверены."
            else:
                raise RuntimeError("Неизвестное действие")
        except Exception as exception:
            return self.error(exception)
        return self.redirect("/?message=" + quote(message))

    def error(self, exception):
        body = self.header() + f'<div class=card><h1 class=bad>Операция не выполнена</h1><p>{html.escape(str(exception))}</p><a class=btn href=/>Назад</a></div>'
        return self.sendx(400, page("Ошибка", body))

    def log_message(self, *_):
        pass


if __name__ == "__main__":
    if not PASSWORD or SECRET == b"change-me":
        raise SystemExit("configure panel credentials")
    server = ThreadingHTTPServer((BIND, PORT), Handler)
    if CERT and KEY:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(CERT, KEY)
        server.socket = context.wrap_socket(server.socket, server_side=True)
    server.serve_forever()
