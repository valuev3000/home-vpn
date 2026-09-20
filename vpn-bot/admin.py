#!/usr/bin/env python3
"""Standalone, low-resource administration site for the Telegram bot."""

import base64
import hashlib
import hmac
import html
import json
import os
import secrets
import ssl
import subprocess
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


BIND = os.getenv("BOT_PANEL_BIND", "127.0.0.1")
PORT = int(os.getenv("BOT_PANEL_PORT", "8090"))
USER = os.getenv("BOT_PANEL_USER", "bot-admin")
PASSWORD = os.getenv("BOT_PANEL_PASSWORD", "")
SECRET = os.getenv("BOT_PANEL_SECRET", "change-me").encode()
ENTRY = os.getenv("BOT_PANEL_ENTRY_PATH", "/bot-control")
TLS_CERT = os.getenv("BOT_PANEL_TLS_CERT", "")
TLS_KEY = os.getenv("BOT_PANEL_TLS_KEY", "")
SECURE_COOKIE = os.getenv("BOT_PANEL_SECURE_COOKIE", "1") == "1"
ATTEMPTS = {}
MENU_ITEMS = {
    "new": "Новая подписка", "renew": "Продление", "upgrade": "Изменение тарифа",
    "subscriptions": "Подписки и заказы", "link": "Привязка подписки", "support": "Поддержка",
    "site": "Переход на сайт", "instructions": "Инструкции", "status": "Статус серверов",
    "notifications": "Уведомления",
}


def control(*args):
    result = subprocess.run(["sudo", "-n", "/usr/local/sbin/vpn-bot-admin-control", *args], capture_output=True, text=True, timeout=25)
    if result.returncode: raise RuntimeError((result.stderr or result.stdout or "Ошибка управления ботом").strip()[:500])
    return json.loads(result.stdout or "{}")


def page(title, body):
    css = """*{box-sizing:border-box}body{margin:0;background:#07101d;color:#e5edf8;font:16px system-ui}.w{max-width:1100px;margin:auto;padding:22px}.head{display:flex;justify-content:space-between;align-items:center;margin-bottom:14px}.brand{font-size:22px;font-weight:800}.nav{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:18px}.nav a{padding:10px 14px;border-radius:9px;background:#182a43;color:#c9d7e9;text-decoration:none;font-weight:700}.nav a.on{background:#409eff;color:#fff}.card{background:#101d31;border:1px solid #334158;border-radius:14px;padding:18px;margin:12px 0}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:12px}.btn,button{display:inline-block;border:0;border-radius:9px;padding:11px 15px;background:#409eff;color:white;font-weight:700;text-decoration:none;cursor:pointer}.danger{background:#e05252}.soft{background:#263851}input,textarea{width:100%;padding:12px;border:1px solid #465a77;border-radius:8px;background:#091425;color:white;margin:7px 0 14px;font:inherit}textarea{min-height:105px;resize:vertical}.ok{color:#67c23a}.bad{color:#f56c6c}.saved{border-color:#3f8f54;background:#102a22}.inline{display:inline}.stack{display:block}.menurow{display:grid;grid-template-columns:290px 1fr;gap:14px;align-items:center;border-top:1px solid #263851;padding:12px 0}.menurow.dragging{opacity:.45}.rowtools{display:flex;align-items:center;gap:7px}.handle{color:#9fb0c6;font-size:20px;cursor:grab}.move{padding:5px 9px;background:#263851}.toggle{display:flex;align-items:center;gap:9px;font-weight:700}.toggle input{width:20px;height:20px;margin:0}.menurow input[type=text]{margin:0}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#050b14;border-radius:9px;padding:12px;max-height:430px;overflow:auto;font-size:12px}form{display:inline}.login{max-width:430px;margin:10vh auto}small{color:#9fb0c6}@media(max-width:600px){.w{padding:12px}.head{align-items:flex-start;gap:10px}.head div:last-child{text-align:right}.menurow{grid-template-columns:1fr}.rowtools{flex-wrap:wrap}}"""
    return f"<!doctype html><html lang=ru><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>{html.escape(title)} · Bot Control</title><style>{css}</style><body><div class=w>{body}</div></body></html>".encode()


def navigation(active):
    return '<div class=nav><a class="'+('on' if active=='status' else '')+'" href=/>Состояние</a><a class="'+('on' if active=='menu' else '')+'" href=/menu>Меню и приветствие</a></div>'


def csrf_token():
    return hmac.new(SECRET, b"bot-panel-csrf", hashlib.sha256).hexdigest()


class Handler(BaseHTTPRequestHandler):
    def sendx(self, status, data, content_type="text/html; charset=utf-8"):
        if isinstance(data, str): data = data.encode()
        self.send_response(status); self.send_header("Content-Type", content_type); self.send_header("Content-Length", str(len(data))); self.send_header("X-Frame-Options", "DENY"); self.send_header("X-Content-Type-Options", "nosniff"); self.send_header("Referrer-Policy", "no-referrer"); self.end_headers(); self.wfile.write(data)
    def redirect(self, target, cookie=None):
        self.send_response(303); self.send_header("Location", target)
        if cookie: self.send_header("Set-Cookie", cookie)
        self.end_headers()
    def session(self):
        raw = next((x.split("=",1)[1] for x in self.headers.get("Cookie","").split("; ") if x.startswith("bot_panel=")), "")
        try:
            body, signature = raw.rsplit(".",1); username, expires = base64.urlsafe_b64decode(body+"==").decode().split("|",1)
            return username == USER and int(expires) > time.time() and hmac.compare_digest(signature,hmac.new(SECRET,body.encode(),hashlib.sha256).hexdigest())
        except Exception: return False
    def token(self):
        body=base64.urlsafe_b64encode(f"{USER}|{int(time.time()+8*3600)}".encode()).decode().rstrip("=");return body+"."+hmac.new(SECRET,body.encode(),hashlib.sha256).hexdigest()
    def data(self):
        return {k:v[0] for k,v in parse_qs(self.rfile.read(int(self.headers.get("Content-Length","0"))).decode(),keep_blank_values=True).items()}
    def do_GET(self):
        path=urlparse(self.path).path
        if path==ENTRY:
            if self.session(): return self.redirect("/")
            return self.sendx(200,page("Вход",f'''<div class="card login"><h1>Панель Telegram-бота</h1><p>Закрытая точка управления.</p><form method=post action="{html.escape(ENTRY,quote=True)}"><label>Логин</label><input name=username required><label>Пароль</label><input name=password type=password required><button>Войти</button></form></div>'''))
        if path=="/logout": return self.redirect(ENTRY,"bot_panel=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict")
        if not self.session(): return self.redirect(ENTRY)
        if path=="/menu":
            try:
                config=control("get-menu");error=""
            except Exception as exception:
                config={"welcome_title":"","welcome_text":"","items":{}};error=f'<div class=card><b class=bad>{html.escape(str(exception))}</b></div>'
            rows=[];order=config.get("order") or list(MENU_ITEMS)
            for key in order:
                name=MENU_ITEMS[key]
                item=(config.get("items") or {}).get(key) or {};checked=" checked" if item.get("enabled") else ""
                rows.append(f'''<div class=menurow draggable=true data-key="{key}"><div class=rowtools><span class=handle title="Перетащить">↕</span><button class=move type=button onclick="moveRow(this,-1)" title="Выше">↑</button><button class=move type=button onclick="moveRow(this,1)" title="Ниже">↓</button><label class=toggle><input type=checkbox name="enabled_{key}" value=1{checked}> {html.escape(name)}</label></div><input type=text name="label_{key}" maxlength=64 required value="{html.escape(str(item.get('label') or ''),quote=True)}"></div>''')
            saved='<div class="card saved"><b class=ok>✓ Настройки применены, бот перезапущен.</b></div>' if parse_qs(urlparse(self.path).query).get("saved") else ""
            body=f'''<div class=head><div class=brand>🤖 Панель Telegram-бота</div><div><b>{html.escape(USER)}</b><br><a class=soft href=/logout>Выйти</a></div></div>{navigation('menu')}{saved}{error}<form class=stack method=post action=/menu><input type=hidden name=csrf value="{csrf_token()}"><input type=hidden id=menuOrder name=menu_order value="{','.join(order)}"><div class=card><h2>Приветствие</h2><label>Заголовок</label><input name=welcome_title minlength=3 maxlength=80 required value="{html.escape(str(config.get('welcome_title') or ''),quote=True)}"><label>Текст</label><textarea name=welcome_text minlength=10 maxlength=500 required>{html.escape(str(config.get('welcome_text') or ''))}</textarea></div><div class=card><h2>Пользовательское меню</h2><p><small>Меняйте порядок стрелками или перетаскиванием. Снимите флажок, чтобы скрыть кнопку. Подпись можно изменить вместе с эмодзи.</small></p><div id=menuRows>{''.join(rows)}</div><button type=submit>Сохранить и применить</button></div></form><script>const box=document.getElementById('menuRows'),order=document.getElementById('menuOrder');function syncOrder(){{order.value=[...box.querySelectorAll('.menurow')].map(x=>x.dataset.key).join(',')}}function moveRow(button,delta){{const row=button.closest('.menurow'),other=delta<0?row.previousElementSibling:row.nextElementSibling;if(!other)return;if(delta<0)box.insertBefore(row,other);else box.insertBefore(other,row);syncOrder()}}let dragged=null;box.querySelectorAll('.menurow').forEach(row=>{{row.addEventListener('dragstart',()=>{{dragged=row;row.classList.add('dragging')}});row.addEventListener('dragend',()=>{{row.classList.remove('dragging');dragged=null;syncOrder()}});row.addEventListener('dragover',event=>event.preventDefault());row.addEventListener('drop',event=>{{event.preventDefault();if(!dragged||dragged===row)return;const rect=row.getBoundingClientRect();box.insertBefore(dragged,event.clientY<rect.top+rect.height/2?row:row.nextSibling);syncOrder()}})}});</script>'''
            return self.sendx(200,page("Меню бота",body))
        if path!="/": return self.sendx(404,"not found","text/plain")
        try: info=control("status"); error=""
        except Exception as exception: info={};error=f'<div class=card><b class=bad>{html.escape(str(exception))}</b></div>'
        active=bool(info.get("active")); state='<span class=ok>Работает</span>' if active else '<span class=bad>Остановлен</span>'
        tg='<span class=ok>Доступен</span>' if info.get("telegram_ok") else '<span class=bad>Ошибка</span>';site='<span class=ok>Доступен</span>' if info.get("site_ok") else '<span class=bad>Ошибка</span>'
        actions=f'''<form method=post action=/action><input type=hidden name=csrf value="{csrf_token()}"><input type=hidden name=action value={'restart' if active else 'start'}><button>{'Перезапустить' if active else 'Запустить'}</button></form> <form method=post action=/action onsubmit="return confirm('Остановить Telegram-бота?')"><input type=hidden name=csrf value="{csrf_token()}"><input type=hidden name=action value=stop><button class=danger>Остановить</button></form> <form method=post action=/action><input type=hidden name=csrf value="{csrf_token()}"><input type=hidden name=action value=notify><button class=soft>Тестовое сообщение</button></form>'''
        body=f'''<div class=head><div class=brand>🤖 Панель Telegram-бота</div><div><b>{html.escape(USER)}</b><br><a class=soft href=/logout>Выйти</a></div></div>{navigation('status')}{error}<div class=grid><div class=card><small>Служба</small><h2>{state}</h2></div><div class=card><small>Telegram API</small><h2>{tg}</h2><p>@{html.escape(str(info.get('bot_username') or '—'))}</p></div><div class=card><small>Основной сайт</small><h2>{site}</h2><p>{html.escape(str(info.get('public_url') or 'не задан'))}</p></div><div class=card><small>Канал</small><h2>{html.escape(str(info.get('channel') or 'не задан'))}</h2></div></div><div class=card><h2>Управление</h2>{actions}</div><div class=card><h2>Последние события</h2><pre>{html.escape(str(info.get('logs') or 'Журнал пуст'))}</pre></div><script>setTimeout(function(){{location.reload()}},30000)</script>'''
        return self.sendx(200,page("Управление ботом",body))
    def do_POST(self):
        path=urlparse(self.path).path; data=self.data(); ip=self.client_address[0]
        if path==ENTRY:
            recent=[x for x in ATTEMPTS.get(ip,[]) if x>time.time()-600];ATTEMPTS[ip]=recent
            if len(recent)>=10:return self.sendx(429,"Слишком много попыток","text/plain")
            if not (secrets.compare_digest(data.get("username",""),USER) and secrets.compare_digest(data.get("password",""),PASSWORD)):
                recent.append(time.time());return self.sendx(401,page("Ошибка",f'<div class="card login"><h1 class=bad>Неправильный логин или пароль</h1><a class=btn href="{html.escape(ENTRY,quote=True)}">Повторить</a></div>'))
            flags="; Secure" if SECURE_COOKIE else "";return self.redirect("/",f"bot_panel={self.token()}; Path=/; Max-Age=28800; HttpOnly; SameSite=Strict{flags}")
        if not self.session():return self.redirect(ENTRY)
        if not secrets.compare_digest(data.get("csrf",""),csrf_token()):return self.sendx(403,"Неверный защитный токен","text/plain")
        if path=="/menu":
            payload={"welcome_title":data.get("welcome_title",""),"welcome_text":data.get("welcome_text",""),"order":[x for x in data.get("menu_order","").split(",") if x],"items":{}}
            for key in MENU_ITEMS:payload["items"][key]={"enabled":data.get("enabled_"+key)=="1","label":data.get("label_"+key,"")}
            try:control("save-menu",json.dumps(payload,ensure_ascii=False))
            except Exception as exception:return self.sendx(400,page("Ошибка",f'<div class=card><h1 class=bad>Настройки не сохранены</h1><p>{html.escape(str(exception))}</p><a class=btn href=/menu>Вернуться</a></div>'))
            return self.redirect("/menu?saved=1")
        if path=="/action":
            action=data.get("action","")
            try: control("notify") if action=="notify" else control("action",action)
            except Exception as exception:return self.sendx(503,page("Ошибка",f'<div class=card><h1 class=bad>Команда не выполнена</h1><p>{html.escape(str(exception))}</p><a class=btn href=/>Вернуться</a></div>'))
            return self.redirect("/")
        return self.sendx(404,"not found","text/plain")
    def log_message(self, *_): pass


def main():
    if not PASSWORD or SECRET==b"change-me":raise SystemExit("Configure BOT_PANEL_PASSWORD and BOT_PANEL_SECRET")
    server=ThreadingHTTPServer((BIND,PORT),Handler)
    if TLS_CERT or TLS_KEY:
        if not TLS_CERT or not TLS_KEY:raise SystemExit("Both BOT_PANEL_TLS_CERT and BOT_PANEL_TLS_KEY are required")
        context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.minimum_version=ssl.TLSVersion.TLSv1_2;context.load_cert_chain(TLS_CERT,TLS_KEY);server.socket=context.wrap_socket(server.socket,server_side=True)
    server.serve_forever()


if __name__=="__main__":main()
