#!/usr/bin/env python3
import html, json, math, os, ssl, time, urllib.request, urllib.parse, urllib.error, uuid

TOKEN=os.getenv("TELEGRAM_BOT_TOKEN","")
SHOP=os.getenv("SHOP_API_URL","http://127.0.0.1:8080").rstrip("/")
PUBLIC=os.getenv("PUBLIC_BASE_URL","https://vpn.my.domain.ru").rstrip("/")
SECRET=os.getenv("SHOP_API_SECRET","")
CHANNEL=os.getenv("TELEGRAM_CHANNEL","")
ADMIN_ID=int(os.getenv("TELEGRAM_ADMIN_ID","0") or 0)
POLL=int(os.getenv("POLL_SECONDS","3"))
STATE=os.getenv("BOT_STATE_FILE","/var/lib/vpn-bot/last_event_v2")
PAYMENT_TIMERS_STATE=os.getenv("PAYMENT_TIMERS_STATE","/var/lib/vpn-bot/payment_timers.json")
MENU_CONFIG=os.getenv("BOT_MENU_CONFIG","/etc/vpn-bot-menu.json")
DEVICE_MONTH={1:100,2:190,3:280,4:370,5:460}
PERIODS={"m1":("1 месяц",1),"m3":("3 месяца",2.8),"m6":("6 месяцев",5.6),"y1":("1 год",10)}
CATALOG_CACHE={"value":None,"expires":0}
MENU_DEFAULT={
    "welcome_title":"Домашняя приватная сеть",
    "welcome_text":"Добро пожаловать! Здесь можно оформить и продлить доступ, управлять подключениями и обратиться в поддержку.",
    "order":["new","renew","upgrade","subscriptions","link","support","site","instructions","status","notifications"],
    "items":{
        "new":{"enabled":True,"label":"➕ Купить новую подписку"},
        "renew":{"enabled":True,"label":"🔄 Продлить подписку"},
        "upgrade":{"enabled":True,"label":"⬆️ Изменить тариф"},
        "subscriptions":{"enabled":True,"label":"📋 Подписки и текущие заказы"},
        "link":{"enabled":True,"label":"🔗 Привязать подписку"},
        "support":{"enabled":True,"label":"🎫 Поддержка"},
        "site":{"enabled":True,"label":"🌐 Перейти на сайт"},
        "instructions":{"enabled":True,"label":"📖 Инструкции по подключению"},
        "status":{"enabled":True,"label":"🟢 Статус серверов"},
        "notifications":{"enabled":True,"label":"🔕 Отписаться от уведомлений"},
    },
}
LINK_WAIT=set()
LOGIN_WAIT={}
MAINT_WAIT=set()
NAME_WAIT=set()
BUY_TARGET={}
TEST_USER_SELECTED={}
TICKET_NEW_WAIT=set()
TICKET_REPLY_WAIT={}
PAYMENT_TIMERS={}

def menu_config():
    result=json.loads(json.dumps(MENU_DEFAULT,ensure_ascii=False))
    try:
        with open(MENU_CONFIG,encoding="utf-8") as source:stored=json.load(source)
        if isinstance(stored.get("welcome_title"),str):result["welcome_title"]=stored["welcome_title"]
        if isinstance(stored.get("welcome_text"),str):result["welcome_text"]=stored["welcome_text"]
        order=stored.get("order")
        if isinstance(order,list) and len(order)==len(result["items"]) and set(order)==set(result["items"]):result["order"]=order
        for key in result["items"]:
            item=(stored.get("items") or {}).get(key) or {}
            if isinstance(item.get("enabled"),bool):result["items"][key]["enabled"]=item["enabled"]
            if isinstance(item.get("label"),str):result["items"][key]["label"]=item["label"]
    except (OSError,ValueError,TypeError):pass
    return result

def welcome_text():
    cfg=menu_config();return f"🏠 <b>{html.escape(cfg['welcome_title'])}</b>\n\n{html.escape(cfg['welcome_text'])}"

def tg(method,data=None,files=None):
    url=f"https://api.telegram.org/bot{TOKEN}/{method}"
    if files:
        boundary="----vpn"+uuid.uuid4().hex; body=bytearray()
        for k,v in (data or {}).items(): body.extend(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
        for k,(name,content,ctype) in files.items(): body.extend(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"; filename=\"{name}\"\r\nContent-Type: {ctype}\r\n\r\n".encode()+content+b"\r\n")
        body.extend(f"--{boundary}--\r\n".encode()); req=urllib.request.Request(url,bytes(body),headers={"Content-Type":f"multipart/form-data; boundary={boundary}"})
    else: req=urllib.request.Request(url,urllib.parse.urlencode(data or {}).encode())
    with urllib.request.urlopen(req,timeout=30) as r:return json.loads(r.read())

def save_payment_timers():
    os.makedirs(os.path.dirname(PAYMENT_TIMERS_STATE),exist_ok=True)
    temp=PAYMENT_TIMERS_STATE+".tmp"
    with open(temp,"w",encoding="utf-8") as f:json.dump(PAYMENT_TIMERS,f,ensure_ascii=False)
    os.replace(temp,PAYMENT_TIMERS_STATE)

def load_payment_timers():
    try:
        with open(PAYMENT_TIMERS_STATE,encoding="utf-8") as f:
            raw=json.load(f)
        PAYMENT_TIMERS.update({str(k):v for k,v in raw.items() if isinstance(v,dict)})
    except (FileNotFoundError,json.JSONDecodeError,OSError):pass

def countdown_text(seconds):
    seconds=max(0,int(seconds));return f"{seconds//60:02d}:{seconds%60:02d}"

def track_payment_timer(order,chat,message_id,base_caption,markup):
    PAYMENT_TIMERS[str(order["id"])]={"chat_id":chat,"message_id":message_id,"expires_at":int(order["expires_at"]),"base_caption":base_caption,"markup":markup,"bucket":-1}
    save_payment_timers()

def stop_payment_timer(order_id,caption=None):
    item=PAYMENT_TIMERS.pop(str(order_id),None)
    if item and caption:
        try:tg("editMessageCaption",{"chat_id":item["chat_id"],"message_id":item["message_id"],"caption":caption,"parse_mode":"HTML","reply_markup":json.dumps({"inline_keyboard":[[{"text":"📋 Мои подписки","callback_data":"menu:subscriptions"}],[{"text":"🎫 Поддержка","callback_data":"menu:support"}]]},ensure_ascii=False)})
        except Exception:pass
    if item:save_payment_timers()

def update_payment_timers():
    now=int(time.time());changed=False
    for order_id,item in list(PAYMENT_TIMERS.items()):
        left=max(0,int(item.get("expires_at",0))-now);bucket=left//30
        if left<=0:
            try:tg("editMessageCaption",{"chat_id":item["chat_id"],"message_id":item["message_id"],"caption":"⌛ <b>Время оплаты истекло</b>\n\nСоздайте новый заказ. Этот заказ больше нельзя оплатить.","parse_mode":"HTML","reply_markup":json.dumps({"inline_keyboard":[[{"text":"🛒 Создать новый заказ","callback_data":"buy:new"}],[{"text":"🎫 Поддержка","callback_data":"menu:support"}]]},ensure_ascii=False)})
            except Exception:pass
            PAYMENT_TIMERS.pop(order_id,None);changed=True;continue
        if int(item.get("bucket",-1))==bucket:continue
        caption=f"⏱ <b>До окончания оплаты: {countdown_text(left)}</b>\n\n{item['base_caption']}"
        try:
            tg("editMessageCaption",{"chat_id":item["chat_id"],"message_id":item["message_id"],"caption":caption,"parse_mode":"HTML","reply_markup":item["markup"]})
            item["bucket"]=bucket;changed=True
        except Exception:pass
    if changed:save_payment_timers()

def api(path,body=None):
    raw=json.dumps(body).encode() if body is not None else None
    req=urllib.request.Request(SHOP+path,raw,headers={"X-API-Key":SECRET,"Content-Type":"application/json"},method="POST" if body is not None else "GET")
    context=ssl._create_unverified_context() if SHOP.startswith("https://") else None
    with urllib.request.urlopen(req,timeout=15,context=context) as r:return json.loads(r.read())

def sync_payment_timers():
    for order_id in list(PAYMENT_TIMERS):
        try:status=api(f"/api/bot/order-status?order_id={int(order_id)}").get("status")
        except Exception:continue
        if status=="review":stop_payment_timer(order_id,"⏳ <b>Оплата начата</b>\n\nТаймер остановлен. Заказ перенесён в раздел «Мои подписки» и ожидает проверки администратора.")
        elif status=="payment_pending":stop_payment_timer(order_id,"⏳ <b>ЮKassa обрабатывает платёж</b>\n\nПодписка будет выдана автоматически после подтверждения оплаты.")
        elif status=="payment_error":stop_payment_timer(order_id,"⚠️ <b>Платёж получен, подписка оформляется</b>\n\nОткройте заказ для повторной проверки или обратитесь в поддержку.")
        elif status=="active":stop_payment_timer(order_id,"✅ <b>Подписка активна</b>\n\nЗаказ находится в разделе «Мои подписки».")
        elif status in ("rejected","canceled","expired","deleted"):stop_payment_timer(order_id)

def catalog():
    now=time.time()
    if CATALOG_CACHE["value"] and CATALOG_CACHE["expires"]>now:return CATALOG_CACHE["value"]
    try:
        value=api("/api/bot/catalog");CATALOG_CACHE.update(value=value,expires=now+10);return value
    except Exception:return {"branding":{"name":"HOME-VPN","tagline":"Домашняя свобода","subtitle":"Безопасный интернет каждый день"},"currency":"₽","max_devices":5,"traffic_gb":0,"device_monthly":DEVICE_MONTH,"periods":{k:{"name":v[0],"multiplier":v[1]} for k,v in PERIODS.items()},"trial":{"enabled":True,"name":"Пробный · 3 дня"}}

def brand_name():return str((catalog().get("branding") or {}).get("name") or "HOME-VPN")
def brand_tagline():return str((catalog().get("branding") or {}).get("tagline") or "Домашняя свобода")

def keyboard(mode="new"):
    cfg=catalog();trial=cfg.get("trial") or {};currency=cfg.get("currency","₽");devices_map={int(k):int(v) for k,v in cfg.get("device_monthly",DEVICE_MONTH).items()};periods=cfg.get("periods") or {}
    if cfg.get("tariffs"):
        rows=[]
        for item in cfg["tariffs"]:
            if item.get("trial") and mode!="new":continue
            label=f"{item['name']} · {item['devices']} устр. · {'бесплатно' if not item['price'] else str(item['price'])+' '+currency}"
            rows.append([{"text":label,"callback_data":f"order:{mode}:"+item["code"]}])
        return json.dumps({"inline_keyboard":rows},ensure_ascii=False)
    rows=[[{"text":f"{trial.get('name','Пробный')} · бесплатно","callback_data":"order:new:trial"}]] if mode=="new" and trial.get("enabled",True) else []
    for period,info in periods.items():
        name=info.get("name",period);multiplier=float(info.get("multiplier",1))
        for devices,monthly in devices_map.items():
            code=period if devices==1 else f"{period}_{devices}";price=int((monthly*multiplier+5)//10*10)
            rows.append([{"text":f"{name} · {devices} устр. · {price} {currency}","callback_data":f"order:{mode}:"+code}])
    return json.dumps({"inline_keyboard":rows},ensure_ascii=False)

def renew_keyboard(devices):
    cfg=catalog();currency=cfg.get("currency","₽");devices_map={int(k):int(v) for k,v in cfg.get("device_monthly",DEVICE_MONTH).items()};devices=max(1,min(max(devices_map),int(devices or 1)));rows=[]
    if cfg.get("tariffs"):
        rows=[[{"text":f"{x['name']} · {x['devices']} устр. · {x['price']} {currency}","callback_data":"order:renew:"+x["code"]}] for x in cfg["tariffs"] if x.get("renewal") and not x.get("trial") and int(x["devices"])==devices]
        rows.append([{"text":"⬅️ Назад","callback_data":"menu:bot"}]);return json.dumps({"inline_keyboard":rows},ensure_ascii=False)
    for period,info in (cfg.get("periods") or {}).items():
        name=info.get("name",period);multiplier=float(info.get("multiplier",1));code=period if devices==1 else f"{period}_{devices}";price=int((devices_map[devices]*multiplier+5)//10*10)
        rows.append([{"text":f"{name} · {devices} устр. · {price} {currency}","callback_data":f"order:renew:{code}"}])
    rows.append([{"text":"⬅️ Назад","callback_data":"menu:bot"}])
    return json.dumps({"inline_keyboard":rows},ensure_ascii=False)

def upgrade_keyboard(client):
    cfg=catalog();currency=cfg.get("currency","₽");devices_map={int(k):int(v) for k,v in cfg.get("device_monthly",DEVICE_MONTH).items()};current=max(1,min(max(devices_map),int(client.get("limitIp") or 1)));remaining=max(0,int(math.ceil((int(client.get("expiryTime") or 0)-int(time.time()*1000))/86400000)));rows=[]
    if cfg.get("tariffs"):
        tariffs=[x for x in cfg["tariffs"] if x.get("renewal") and not x.get("trial")];targets={}
        for item in tariffs:
            devices=int(item["devices"])
            if devices>current and (devices not in targets or int(item["days"])<int(targets[devices]["days"])):targets[devices]=item
        for devices,item in sorted(targets.items()):
            base=next((x for x in tariffs if int(x["devices"])==current and int(x["days"])==int(item["days"])),None)
            estimate=max(0,int(math.ceil((int(item["price"])-int(base["price"]))*remaining/max(1,int(item["days"]))))) if base else 0
            rows.append([{"text":f"{current} → {devices} устр. · доплата {estimate} {currency}","callback_data":"order:upgrade:"+item["code"]}])
        rows.append([{"text":"⬅️ Назад","callback_data":"menu:bot"}]);return json.dumps({"inline_keyboard":rows},ensure_ascii=False),remaining
    for devices in range(current+1,max(devices_map)+1):
        price=max(0,int(math.ceil((devices_map[devices]-devices_map[current])*remaining/30)));code=f"m1_{devices}"
        rows.append([{"text":f"{current} → {devices} устр. · доплата {price} {currency}","callback_data":f"order:upgrade:{code}"}])
    rows.append([{"text":"⬅️ Назад","callback_data":"menu:bot"}])
    return json.dumps({"inline_keyboard":rows},ensure_ascii=False),remaining

def plan_info(code):
    cfg=catalog()
    item=next((x for x in cfg.get("tariffs",[]) if x.get("code")==code),None)
    if item:return item.get("name",code),int(item.get("devices") or 1)
    if code=="trial":return (cfg.get("trial") or {}).get("name","Пробный"),1
    parts=code.split("_");devices=int(parts[1]) if len(parts)>1 else 1
    return (cfg.get("periods") or {}).get(parts[0],{}).get("name",parts[0]),devices

def save_cursor(value):
    os.makedirs(os.path.dirname(STATE),exist_ok=True)
    with open(STATE,"w") as f:f.write(str(value))

def send(chat,text,markup=None):
    d={"chat_id":chat,"text":text,"parse_mode":"HTML","disable_web_page_preview":"true"}
    if markup:d["reply_markup"]=markup
    return tg("sendMessage",d)

def deliver_maintenance():
    sent=0;failed=0
    for item in api("/api/bot/maintenance-pending").get("items",[]):
        try:
            send(item["telegram_id"],item["message"]);api("/api/bot/maintenance-delivered",{"notice_id":item["notice_id"],"telegram_id":item["telegram_id"]});sent+=1
        except Exception:failed+=1
    return sent,failed

def start_maintenance(kind,text):
    api("/api/bot/maintenance",{"action":"start","kind":kind,"message":text})
    if CHANNEL:send(CHANNEL,text)
    return deliver_maintenance()

def complete_maintenance(text):
    result=api("/api/bot/maintenance",{"action":"complete"});sent=0;failed=0
    if CHANNEL:send(CHANNEL,text)
    for user_id in result.get("telegram_ids",[]):
        try:send(user_id,text);sent+=1
        except Exception:failed+=1
    return sent,failed,result.get("completed",0)

def is_admin(user_id):return bool(ADMIN_ID and int(user_id)==ADMIN_ID)

def selected_test_user(user_id):
    return TEST_USER_SELECTED.get(int(user_id),"") if is_admin(user_id) else ""

def test_query(user_id):
    name=selected_test_user(user_id)
    return "&test_username="+urllib.parse.quote(name) if name else ""

def test_user_menu():
    users=api(f"/api/bot/test-users?telegram_id={ADMIN_ID}").get("users",[])
    rows=[[{"text":"👤 "+str(x["username"]),"callback_data":"testuser:"+str(x["username"])}] for x in users]
    rows.append([{"text":"⚙️ Управление пользователями на сайте","url":PUBLIC+"/admin/test-user"}])
    rows.append([{"text":"⬅️ Назад","callback_data":"mode:select"}])
    return users,json.dumps({"inline_keyboard":rows},ensure_ascii=False)

def profile(user_id):
    try:return api(f"/api/bot/profile?telegram_id={int(user_id)}").get("profile")
    except Exception:return None

def ask_login(chat,pending="/start"):
    LOGIN_WAIT[chat]=pending
    send(chat,f"<b>Регистрация {html.escape(brand_name())}</b>\n\nУкажите логин для входа на сайт. Он также будет использоваться как название вашей VPN-подписки.\n\nДлина — от 8 до 32 символов. Обязательны латинские буквы. Цифры и дефис разрешены, но не обязательны.\n\nНапример: <code>familiaio</code>\n\nПосле регистрации бот пришлёт логин и автоматически созданный пароль.")

def registration_result(x):
    login=html.escape(str(x.get("login") or ""));password=x.get("password")
    if not password:return f"✅ Логин <b>{login}</b> сохранён."
    codes=[html.escape(str(code)) for code in (x.get("recovery_codes") or [])];codes_text=("\n\n<b>Резервные коды для входа:</b>\n"+"\n".join(f"<code>{code}</code>" for code in codes)) if codes else ""
    return ("✅ <b>Регистрация завершена</b>\n\n"
            f"Логин: <code>{login}</code>\n"
            f"Пароль: <code>{html.escape(str(password))}</code>{codes_text}\n\n"
            "⚠️ <b>Сохраните эти данные в надёжном месте.</b> Пароль и одноразовые резервные коды показываются только один раз.\n\n"
            f"Если Telegram перестанет работать, используйте логин и пароль для входа на сайт: {html.escape(PUBLIC)}/login\n"
            "Сайт — резервный канал доступа к подпискам, оплате и поддержке. Никому не сообщайте пароль, включая сотрудников поддержки.")

def bot_services_menu(site_url=None):
    menu=menu_config();cfg=menu["items"];rows=[]
    targets={
        "new":{"callback_data":"buy:new"},"renew":{"callback_data":"buy:renew"},"upgrade":{"callback_data":"buy:upgrade"},
        "subscriptions":{"callback_data":"menu:subscriptions"},"link":{"callback_data":"menu:link"},"support":{"callback_data":"menu:support"},
        "site":{"url":site_url or PUBLIC+"/signin"},"instructions":{"url":PUBLIC+"/instructions"},
        "status":{"callback_data":"status:show"},"notifications":{"callback_data":"notifications:off"},
    }
    for key in menu["order"]:
        target=targets[key]
        if cfg[key]["enabled"]:rows.append([{"text":cfg[key]["label"],**target}])
    return json.dumps({"inline_keyboard":rows},ensure_ascii=False)

def admin_menu():
    return json.dumps({"inline_keyboard":[
        [{"text":"⚙️ Админ-панель сайта","url":os.getenv("ADMIN_PANEL_URL",PUBLIC+"/admin")}],
        [{"text":"⏳ Неподтверждённые заказы","callback_data":"orders:admin"}],
        [{"text":"🛠 Технические работы","callback_data":"menu:maintenance"}],
        [{"text":"🎫 Открытые тикеты","callback_data":"tickets:admin"}],
        [{"text":"🟢 Статус серверов","callback_data":"status:show"}],
        [{"text":"⬅️ Выбрать режим","callback_data":"mode:select"}],
    ]},ensure_ascii=False)

def admin_mode_menu():
    return json.dumps({"inline_keyboard":[
        [{"text":"⚙️ Я администратор","callback_data":"mode:admin"}],
        [{"text":"👤 Я пользователь (тест)","callback_data":"mode:user"}],
    ]},ensure_ascii=False)

def maintenance_menu():
    return json.dumps({"inline_keyboard":[
        [{"text":"⚠️ Ведутся внеплановые работы","callback_data":"maintenance:unplanned"}],
        [{"text":"📅 Указать плановые работы","callback_data":"maintenance:planned"}],
        [{"text":"✅ Работы завершены","callback_data":"maintenance:end"}],
        [{"text":"⬅️ Назад","callback_data":"menu:admin"}],
    ]},ensure_ascii=False)

def support_menu():
    return json.dumps({"inline_keyboard":[
        [{"text":"✍️ Создать тикет","callback_data":"tickets:new"}],
        [{"text":"📂 Мои тикеты","callback_data":"tickets:list"}],
        [{"text":"⬅️ Назад","callback_data":"menu:bot"}],
    ]},ensure_ascii=False)

def ticket_status(value):
    return {"open":"🟠 Ожидает ответа","answered":"🟢 Получен ответ","closed":"⚪ Закрыт"}.get(value,value)

def show_ticket_list(chat,tgid,admin=False):
    suffix="&admin=1" if admin else ""
    items=api(f"/api/bot/tickets?telegram_id={tgid}{suffix}").get("tickets",[])
    if not items:
        return send(chat,"Открытых тикетов нет." if admin else "У вас пока нет тикетов.",admin_menu() if admin else support_menu())
    rows=[]
    for x in items:
        preview=" ".join(str(x.get("last_message") or "").split())[:35]
        label=f"#{x['id']} · {ticket_status(x['status'])}"+(f" · {preview}" if preview else "")
        rows.append([{"text":label,"callback_data":f"ticket:view:{x['id']}"}])
    rows.append([{"text":"⬅️ Назад","callback_data":"menu:admin" if admin else "menu:support"}])
    send(chat,"<b>Открытые тикеты</b>" if admin else "<b>Мои тикеты</b>",json.dumps({"inline_keyboard":rows},ensure_ascii=False))

def show_pending_orders(chat,tgid,admin=False):
    suffix="&admin=1" if admin else test_query(tgid);items=api(f"/api/bot/pending-orders?telegram_id={tgid}{suffix}").get("orders",[])
    if not items:return send(chat,"✅ Неподтверждённых заказов нет.",admin_menu() if admin else bot_services_menu())
    labels={"pending":"Ожидается оплата","processing":"Оформляется","review":"Ожидается проверка","payment_pending":"ЮKassa обрабатывает платёж","payment_error":"Платёж получен, требуется обработка","error":"Ошибка оформления"};lines=["<b>Неподтверждённые заказы</b>"];rows=[];now=int(time.time())
    for o in items:
        name,devices=plan_info(o["plan"]);kind="Изменение тарифа" if o.get("order_kind")=="upgrade" else ("Продление" if o.get("order_kind")=="renew" else "Новая подписка")
        left=max(0,int(o.get("expires_at") or 0)-now);remaining=f" · осталось {countdown_text(left)}" if o["status"] in ("pending","error") else "";owner=f" · {html.escape(str(o.get('customer') or 'не указан'))}" if admin else ""
        lines.append(f"\n<b>№{o['id']}</b>{owner}\n{kind} · {name} · {devices} устр. · {o['amount']} ₽\n{labels.get(o['status'],o['status'])}{remaining}")
        if admin and o["status"]=="review":rows.append([{"text":f"✅ №{o['id']} Деньги пришли","callback_data":f"payapprove:{o['id']}"},{"text":"❌ Не поступили","callback_data":f"payreject:{o['id']}"}])
        elif not admin and o["status"] in ("pending","error"):rows.append([{"text":f"💳 Оплатить №{o['id']}","url":o["payment_url"]},{"text":"Открыть","url":o["pay_url"]}])
        elif o["status"]=="review" and o.get("subscription_qr_url"):
            rows.append([{"text":f"📷 QR-код подписки №{o['id']}","url":o["subscription_qr_url"]},{"text":"📋 Скопировать","copy_text":{"text":o["subscription_url"]}}])
        else:rows.append([{"text":f"🧾 Открыть №{o['id']}","url":o["pay_url"]}])
        if not admin and o["status"]=="pending":rows.append([{"text":f"❌ Отменить №{o['id']}","callback_data":f"ordercancel:{o['id']}"}])
    rows.append([{"text":"🔄 Обновить","callback_data":"orders:admin" if admin else "orders:pending"}]);rows.append([{"text":"⬅️ Назад","callback_data":"menu:admin" if admin else "menu:bot"}])
    send(chat,"\n".join(lines)[:3900],json.dumps({"inline_keyboard":rows},ensure_ascii=False))

def show_ticket(chat,tgid,ticket_id,admin=False):
    suffix="&admin=1" if admin else ""
    x=api(f"/api/bot/tickets?telegram_id={tgid}&id={ticket_id}{suffix}");ticket=x["ticket"];messages=x.get("messages",[])[-8:]
    lines=[f"<b>Тикет #{ticket['id']}</b>",f"Статус: {ticket_status(ticket['status'])}"]
    if admin:lines.append(f"Пользователь: <code>{ticket['telegram_id']}</code> · {html.escape(str(ticket.get('customer') or ''))}")
    lines.append("")
    for m in messages:
        author="Поддержка" if m["sender"]=="admin" else "Пользователь"
        stamp=time.strftime("%d.%m %H:%M",time.localtime(m["created_at"]));body=html.escape(str(m["message"]))[:700]
        lines.append(f"<b>{author}</b> · {stamp}\n{body}\n")
    rows=[]
    if ticket["status"]!="closed":rows=[[{"text":"💬 Ответить","callback_data":f"ticket:reply:{ticket_id}"}],[{"text":"✅ Закрыть тикет","callback_data":f"ticket:close:{ticket_id}"}]]
    rows.append([{"text":"⬅️ К списку","callback_data":"tickets:admin" if admin else "tickets:list"}])
    send(chat,"\n".join(lines)[:3900],json.dumps({"inline_keyboard":rows},ensure_ascii=False))

def show_subscriptions(chat,tgid):
    suffix=test_query(tgid);items=api(f"/api/bot/clients?telegram_id={tgid}{suffix}").get("clients",[])
    orders=api(f"/api/bot/pending-orders?telegram_id={tgid}{suffix}").get("orders",[])
    if not items and not orders:return send(chat,"Подписок и текущих заказов пока нет. Если подписка уже есть в программе VPN, отправьте /link и затем её полную ссылку.",bot_services_menu())
    selected=selected_test_user(tgid);lines=["<b>Мои подписки и текущие заказы</b>"+(f"\nТестовый пользователь: <code>{html.escape(selected)}</code>" if selected else "")];rows=[]
    for x in items:
        until="без ограничения" if not x.get("expiryTime") else time.strftime("%d.%m.%Y",time.localtime(x["expiryTime"]/1000))
        scheduled="".join(f"\n\n⏳ <b>Запланировано изменение</b>\nС {time.strftime('%d.%m.%Y',time.localtime(z['effectiveAt']/1000))}: {z['limitIp']} устройств.\nСсылка подписки останется прежней." for z in x.get("scheduledChanges",[]))
        lines.append(f"\n<b>Подписка: {html.escape(x['email'])}</b>\nСтатус: {'работает' if x.get('enable') else 'отключена'}\nИспользуется устройств: {x.get('usedDevices',0)} из {x.get('limitIp') or 'без лимита'}\nДействует до: {until}{scheduled}")
        rows.append([{"text":"📋 Скопировать "+str(x['email'])[:32],"copy_text":{"text":x["subscription_url"]}}])
    labels={"pending":"Ожидается оплата","processing":"Оформляется","review":"Ожидается проверка","payment_pending":"ЮKassa обрабатывает платёж","payment_error":"Платёж получен, требуется обработка","error":"Ошибка оформления"};now=int(time.time())
    for o in orders:
        name,devices=plan_info(o["plan"]);kind="Изменение тарифа" if o.get("order_kind")=="upgrade" else ("Продление" if o.get("order_kind")=="renew" else "Новая подписка");left=max(0,int(o.get("expires_at") or 0)-now);remaining=f" · осталось {countdown_text(left)}" if o["status"] in ("pending","error") else ""
        lines.append(f"\n<b>Заказ №{o['id']}</b>\n{kind} · {name} · {devices} устр. · {o['amount']} ₽\n{labels.get(o['status'],o['status'])}{remaining}")
        if o["status"] in ("pending","error"):rows.append([{"text":f"💳 Оплатить №{o['id']}","url":o["payment_url"]},{"text":"Открыть","url":o["pay_url"]}])
        elif o["status"]=="review" and o.get("subscription_qr_url"):
            rows.append([{"text":f"📷 QR-код №{o['id']}","url":o["subscription_qr_url"]},{"text":"📋 Скопировать","copy_text":{"text":o["subscription_url"]}}])
        else:rows.append([{"text":f"🧾 Открыть заказ №{o['id']}","url":o["pay_url"]}])
        if o["status"]=="pending":rows.append([{"text":f"❌ Отменить №{o['id']}","callback_data":f"ordercancel:{o['id']}"}])
    rows.append([{"text":"🔄 Обновить","callback_data":"menu:subscriptions"}]);rows.append([{"text":"⬅️ Назад","callback_data":"menu:bot"}])
    send(chat,"\n".join(lines)[:3900],json.dumps({"inline_keyboard":rows},ensure_ascii=False))

def status_word(value):
    return "🟢 Работает" if str(value).lower() in ("online","running","active","true") else "🔴 Не работает"

def node_flag(name):
    value=str(name or "").upper()
    if "RU" in value or "РОСС" in value:return "🇷🇺"
    if "PL" in value or "ПОЛЬ" in value:return "🇵🇱"
    if "FI" in value or "FIN" in value or "ФИН" in value:return "🇫🇮"
    if "NL" in value or "NETHER" in value or "НИДЕР" in value:return "🇳🇱"
    return "🌐"

def show_status(chat):
    try:
        s=api("/api/bot/status")
        primary=s.get("primary") or {};primary_name=html.escape(str(primary.get("name") or "Основная нода"));primary_flag=html.escape(str(primary.get("flag") or node_flag(primary_name)))
        primary_ok=str(s.get("panel")).lower()=="online" and str(s.get("xray")).lower() in ("online","running","active","true")
        primary_state="🟢 Работает — можно подключаться" if primary_ok else "🔴 Не работает — выберите другой сервер"
        lines=[f"<b>Статус серверов {html.escape(brand_name())}</b>","",f"{primary_flag} <b>{primary_name}</b>",primary_state]
        for node in s.get("nodes",[]):
            name=html.escape(str(node.get("name") or "Дополнительный сервер"));working=str(node.get("status")).lower() in ("online","running","active","true");state="🟢 Работает — можно подключаться" if working else "🔴 Не работает — выберите другой сервер"
            flag=html.escape(str(node.get("flag") or node_flag(name)))
            lines.extend(["",f"{flag} <b>{name}</b>",state])
        lines.extend(["","Если один сервер недоступен, выберите в HAPP другой сервер со статусом «Работает»."])
        markup=json.dumps({"inline_keyboard":[[{"text":"🔄 Обновить","callback_data":"status:show"}],[{"text":"⬅️ Назад","callback_data":"menu:bot"}]]},ensure_ascii=False)
        send(chat,"\n".join(lines),markup)
    except Exception:
        send(chat,"🔴 Не удалось получить статус с основной ноды. Попробуйте обновить через несколько секунд.",json.dumps({"inline_keyboard":[[{"text":"🔄 Повторить","callback_data":"status:show"}]]},ensure_ascii=False))

def link_subscription(chat,user,text):
    try:
        x=api("/api/bot/link-client",{"telegram_id":user["id"],"subscription":text})
        LINK_WAIT.discard(chat);markup=json.dumps({"inline_keyboard":[[{"text":"📋 Скопировать подписку","copy_text":{"text":x["subscription_url"]}}]]},ensure_ascii=False)
        return send(chat,f"✅ Подписка <b>{html.escape(x['email'])}</b> привязана к вашему Telegram.",markup)
    except Exception:return send(chat,"Подписка не найдена. Пришлите полную ссылку существующей подписки из VPN-приложения.")

def handle(update):
    if "message" in update:
        m=update["message"]; text=m.get("text",""); chat=m["chat"]["id"]
        if chat in TICKET_NEW_WAIT:
            if text.startswith("/cancel"):
                TICKET_NEW_WAIT.discard(chat);send(chat,"Создание тикета отменено.",support_menu());return
            try:
                p=profile(m["from"]["id"]) or {};customer=p.get("login") or m["from"].get("username") or str(m["from"]["id"])
                x=api("/api/bot/tickets/create",{"telegram_id":m["from"]["id"],"customer":customer,"message":text});TICKET_NEW_WAIT.discard(chat);ticket_id=x["ticket_id"]
                send(chat,f"✅ Тикет <b>#{ticket_id}</b> создан. Поддержка ответит в этом чате.",json.dumps({"inline_keyboard":[[{"text":"Открыть тикет","callback_data":f"ticket:view:{ticket_id}"}],[{"text":"⬅️ В поддержку","callback_data":"menu:support"}]]},ensure_ascii=False))
                if ADMIN_ID:send(ADMIN_ID,f"🎫 <b>Новый тикет #{ticket_id}</b>\nПользователь: {html.escape(str(customer))} · <code>{m['from']['id']}</code>\n\n{html.escape(text[:1500])}",json.dumps({"inline_keyboard":[[{"text":"💬 Ответить","callback_data":f"ticket:reply:{ticket_id}"}],[{"text":"Открыть тикет","callback_data":f"ticket:view:{ticket_id}"}]]},ensure_ascii=False))
            except urllib.error.HTTPError as e:
                try:msg=json.loads(e.read()).get("error","Не удалось создать тикет")
                except Exception:msg="Не удалось создать тикет"
                send(chat,"⛔ "+html.escape(msg)+"\nПопробуйте ещё раз или используйте /cancel.")
            return
        if chat in TICKET_REPLY_WAIT:
            ticket_id=TICKET_REPLY_WAIT[chat]
            if text.startswith("/cancel"):
                TICKET_REPLY_WAIT.pop(chat,None);send(chat,"Ответ отменён.",admin_menu() if is_admin(m["from"]["id"]) else support_menu());return
            try:
                x=api("/api/bot/tickets/reply",{"telegram_id":m["from"]["id"],"ticket_id":ticket_id,"message":text});TICKET_REPLY_WAIT.pop(chat,None)
                send(chat,f"✅ Ответ добавлен в тикет <b>#{ticket_id}</b>.",json.dumps({"inline_keyboard":[[{"text":"Открыть тикет","callback_data":f"ticket:view:{ticket_id}"}]]},ensure_ascii=False))
                if is_admin(m["from"]["id"]) and int(x.get("telegram_id") or 0)>0:
                    send(x["telegram_id"],f"💬 <b>Поддержка ответила в тикете #{ticket_id}</b>\n\n{html.escape(text[:1500])}",json.dumps({"inline_keyboard":[[{"text":"Открыть тикет","callback_data":f"ticket:view:{ticket_id}"}]]},ensure_ascii=False))
                elif ADMIN_ID:send(ADMIN_ID,f"💬 <b>Новое сообщение в тикете #{ticket_id}</b>\n\n{html.escape(text[:1500])}",json.dumps({"inline_keyboard":[[{"text":"Открыть тикет","callback_data":f"ticket:view:{ticket_id}"}]]},ensure_ascii=False))
            except urllib.error.HTTPError as e:
                try:msg=json.loads(e.read()).get("error","Не удалось отправить ответ")
                except Exception:msg="Не удалось отправить ответ"
                send(chat,"⛔ "+html.escape(msg))
            return
        if chat in NAME_WAIT:
            try:
                x=api("/api/bot/profile",{"telegram_id":m["from"]["id"],"login":text,"tg_username":m["from"].get("username","")})
                NAME_WAIT.discard(chat);send(chat,registration_result(x)+f"\n\nПереименовано подписок: {x.get('renamed',0)}.",admin_menu() if is_admin(m["from"]["id"]) else bot_services_menu())
            except urllib.error.HTTPError as e:
                try:msg=json.loads(e.read()).get("error","Проверьте имя")
                except Exception:msg="Проверьте имя"
                send(chat,"⛔ "+html.escape(msg)+"\nПопробуйте ещё раз. Пример: <code>familiaio</code>")
            return
        if chat in MAINT_WAIT and is_admin(m["from"]["id"]):
            if text.startswith("/cancel"):
                MAINT_WAIT.discard(chat);send(chat,"Ввод плановых работ отменён.",admin_menu());return
            if len(text.strip())<5:
                send(chat,"Укажите дату и время начала и окончания. Например: <code>18.09.2026 с 02:00 до 04:00 МСК</code>\nДля отмены: /cancel");return
            notice=f"📅 Плановые технические работы {brand_name()}\n\nСроки: {text.strip()}\nВ указанный период возможны временные перебои подключения. После завершения будет опубликовано отдельное сообщение."
            try:
                sent,failed=start_maintenance("planned",notice);MAINT_WAIT.discard(chat)
                send(chat,f"✅ Плановые работы включены. Уведомлены активные подписчики: {sent}. Не доставлено: {failed}. Новые подписчики получат сообщение автоматически.",admin_menu())
            except Exception:send(chat,"⛔ Не удалось опубликовать сообщение. Проверьте канал и повторите.")
            return
        if chat in LOGIN_WAIT:
            pending=LOGIN_WAIT[chat]
            try:
                x=api("/api/bot/profile",{"telegram_id":m["from"]["id"],"login":text,"tg_username":m["from"].get("username","")})
                LOGIN_WAIT.pop(chat,None);send(chat,registration_result(x))
                replay=dict(update);replay["message"]=dict(m);replay["message"]["text"]=pending;handle(replay)
            except urllib.error.HTTPError as e:
                try:msg=json.loads(e.read()).get("error","Проверьте логин")
                except Exception:msg="Проверьте логин"
                send(chat,"⛔ "+html.escape(msg)+"\nПопробуйте ещё раз. Пример: <code>familiaio</code>")
            return
        if not is_admin(m["from"]["id"]) and not profile(m["from"]["id"]):
            ask_login(chat,text or "/start");return
        if text.startswith("/start login_"):
            nonce=text.split("login_",1)[1].strip()
            name=html.escape(brand_name())
            agreement=(f"<b>Пользовательское соглашение {name}</b>\n\n"
                       f"Нажимая «Принимаю», вы соглашаетесь на автоматическую регистрацию на сайте {name}. "
                       "Для создания учётной записи будут переданы ваш Telegram ID и имя пользователя. "
                       "Данные используются только для входа, управления подпиской и связи по вашему заказу. "
                       "Не передавайте полученную ссылку входа другим людям.\n\n"
                       f"Условия: {html.escape(PUBLIC)}/terms\n"
                       f"Конфиденциальность: {html.escape(PUBLIC)}/privacy\n"
                       f"Оплата и возвраты: {html.escape(PUBLIC)}/refund-policy")
            buttons=json.dumps({"inline_keyboard":[[{"text":"✅ Принимаю и регистрируюсь","callback_data":"agree:"+nonce}],
                                                   [{"text":"Отказаться","callback_data":"decline"}]]},ensure_ascii=False)
            send(chat,agreement,buttons)
        elif text.startswith("/start linkaccount_"):
            nonce=text.split("linkaccount_",1)[1].strip()
            buttons=json.dumps({"inline_keyboard":[[{"text":"🔗 Объединить аккаунты","callback_data":"linkaccount:"+nonce}],[{"text":"Отмена","callback_data":"decline"}]]},ensure_ascii=False)
            send(chat,f"<b>Привязка Telegram к аккаунту {html.escape(brand_name())}</b>\n\nПосле подтверждения существующие заказы и подписки аккаунта будут связаны с вашим Telegram ID. Данные и сроки подписок сохранятся.",buttons)
        elif text.startswith("/start order_"):
            token=text.split("order_",1)[1].strip()
            try:
                x=api("/api/bot/link-order",{"token":token,"telegram_id":m["from"]["id"],"tg_username":m["from"].get("username","")})
                rows=[]
                if x.get("status") in ("pending","error"):rows.append([{"text":"💳 Оплатить","url":x["payment_url"]}])
                rows.extend([[{"text":"🧾 Открыть заказ","url":x["pay_url"]}],[{"text":"📋 Мои подписки","callback_data":"menu:subscriptions"}]])
                markup=json.dumps({"inline_keyboard":rows},ensure_ascii=False)
                send(chat,"✅ Заказ привязан к вашему Telegram ID. Теперь оплата и подписка доступны в этом аккаунте.",markup)
            except urllib.error.HTTPError as e:
                try:msg=json.loads(e.read()).get("error","Не удалось привязать заказ")
                except Exception:msg="Не удалось привязать заказ"
                send(chat,"⛔ "+html.escape(msg))
        elif text.startswith("/admin"):
            if is_admin(m["from"]["id"]):send(chat,f"<b>{html.escape(brand_name())}</b>\nВыберите режим работы:",admin_mode_menu())
            else:send(chat,"⛔ Эта команда доступна только администратору.")
        elif text.startswith("/name"):
            current=profile(m["from"]["id"])
            if current:send(chat,f"Ваше имя (логин): <b>{html.escape(current['login'])}</b>")
            else:NAME_WAIT.add(chat);send(chat,"Введите имя (логин) от 8 до 32 символов. Обязательны латинские буквы; цифры и дефис разрешены. Например: <code>familiaio</code>")
        elif text.startswith(("/subscriptions","/subs")):show_subscriptions(chat,m["from"]["id"])
        elif text.startswith("/pending"):show_pending_orders(chat,m["from"]["id"],True) if is_admin(m["from"]["id"]) else show_subscriptions(chat,m["from"]["id"])
        elif text.startswith("/link"):
            proof=text.partition(" ")[2].strip()
            if proof:link_subscription(chat,m["from"],proof)
            else:LINK_WAIT.add(chat);send(chat,"Пришлите следующим сообщением полную ссылку другой существующей подписки из программы VPN. Она нужна один раз для безопасной привязки к Telegram ID.")
        elif chat in LINK_WAIT:link_subscription(chat,m["from"],text)
        elif text.startswith("/start"):
            welcome=welcome_text()
            if is_admin(m["from"]["id"]):send(chat,welcome+"\n\nВыберите, в каком режиме открыть бота:",admin_mode_menu())
            else:send(chat,welcome+"\n\nВыберите нужное действие:",bot_services_menu())
        elif text.startswith("/buy"):send(chat,f"<b>{html.escape(brand_name())} · Подписки</b>\nВыберите действие:",bot_services_menu())
        elif text.startswith(("/support","/tickets")):
            if is_admin(m["from"]["id"]):show_ticket_list(chat,m["from"]["id"],True)
            else:send(chat,f"<b>Поддержка {html.escape(brand_name())}</b>\nСоздайте новый тикет или откройте историю обращений.",support_menu())
        elif text.startswith("/status"):show_status(chat)
        elif text.startswith(("/unsubscribe","/stop")):
            api("/api/bot/notifications",{"telegram_id":m["from"]["id"],"muted":True})
            markup=json.dumps({"inline_keyboard":[[{"text":"🔔 Снова получать уведомления","callback_data":"notifications:on"}]]},ensure_ascii=False)
            send(chat,"🔕 Уведомления о скором окончании подписки отключены. Ответы на ваши команды и результат проверки оплаты останутся доступны.",markup)
        elif text.startswith("/subscribe"):
            api("/api/bot/notifications",{"telegram_id":m["from"]["id"],"muted":False});send(chat,"🔔 Уведомления о скором окончании подписки включены.")
        else: send(chat,"Команды: /buy — купить VPN, /subscriptions — мои подписки, /link — привязать существующую подписку, /status — статус, /unsubscribe — отключить напоминания.")
    if "callback_query" in update:
        q=update["callback_query"];data=q.get("data","");chat=q["message"]["chat"]["id"]
        admin_action=is_admin(q["from"]["id"])
        if not admin_action and not profile(q["from"]["id"]):
            tg("answerCallbackQuery",{"callback_query_id":q["id"],"text":"Сначала укажите логин"});ask_login(chat,"/start");return
        if data in ("menu:user","mode:user"):
            if not is_admin(q["from"]["id"]):tg("answerCallbackQuery",{"callback_query_id":q["id"],"text":"Только для администратора","show_alert":"true"});return
            users,markup=test_user_menu();tg("answerCallbackQuery",{"callback_query_id":q["id"]})
            send(chat,"<b>Выберите тестового пользователя</b>\n\nВсе покупки, продления и подписки будут открыты от имени выбранной учётной записи." if users else "<b>Тестовых пользователей пока нет</b>\n\nСначала добавьте пользователя в административной панели сайта.",markup);return
        if data.startswith("testuser:"):
            if not is_admin(q["from"]["id"]):tg("answerCallbackQuery",{"callback_query_id":q["id"],"text":"Только для администратора","show_alert":"true"});return
            name=data.split(":",1)[1];users,_=test_user_menu();valid={str(x["username"]) for x in users}
            if name not in valid:tg("answerCallbackQuery",{"callback_query_id":q["id"],"text":"Пользователь не найден","show_alert":"true"});return
            TEST_USER_SELECTED[int(q["from"]["id"])]=name;BUY_TARGET.pop(q["from"]["id"],None);tg("answerCallbackQuery",{"callback_query_id":q["id"],"text":"Выбран "+name})
            send(chat,f"<b>Пользовательский режим</b>\nТестовый пользователь: <code>{html.escape(name)}</code>\n\nПокупки и управление подписками выполняются от его имени.",bot_services_menu());return
        if data in ("menu:admin","mode:admin"):
            if not is_admin(q["from"]["id"]):tg("answerCallbackQuery",{"callback_query_id":q["id"],"text":"Только для администратора","show_alert":"true"});return
            TEST_USER_SELECTED.pop(int(q["from"]["id"]),None);BUY_TARGET.pop(q["from"]["id"],None);tg("answerCallbackQuery",{"callback_query_id":q["id"]});send(chat,"<b>Административный режим</b>",admin_menu());return
        if data=="mode:select":tg("answerCallbackQuery",{"callback_query_id":q["id"]});send(chat,f"<b>{html.escape(brand_name())}</b>\nВыберите режим работы:",admin_mode_menu());return
        if data=="menu:maintenance":
            if not is_admin(q["from"]["id"]):tg("answerCallbackQuery",{"callback_query_id":q["id"],"text":"Только для администратора","show_alert":"true"});return
            tg("answerCallbackQuery",{"callback_query_id":q["id"]});send(chat,"<b>Технические работы</b>\nВыберите тип уведомления:",maintenance_menu());return
        if data=="menu:bot":
            selected=selected_test_user(q["from"]["id"]);tg("answerCallbackQuery",{"callback_query_id":q["id"]});send(chat,"<b>Оплата и управление в Telegram</b>"+(f"\nТестовый пользователь: <code>{html.escape(selected)}</code>" if selected else "")+"\nВыберите действие:",bot_services_menu());return
        if data=="status:show":tg("answerCallbackQuery",{"callback_query_id":q["id"],"text":"Обновляю статус…"});show_status(chat);return
        if data=="menu:buy":tg("answerCallbackQuery",{"callback_query_id":q["id"]});send(chat,"Выберите действие:",bot_services_menu());return
        if data in ("orders:pending","orders:admin"):
            admin=data=="orders:admin"
            if admin and not is_admin(q["from"]["id"]):tg("answerCallbackQuery",{"callback_query_id":q["id"],"text":"Только для администратора","show_alert":"true"});return
            tg("answerCallbackQuery",{"callback_query_id":q["id"],"text":"Обновляю список…"});show_pending_orders(chat,q["from"]["id"],True) if admin else show_subscriptions(chat,q["from"]["id"]);return
        if data=="menu:support":tg("answerCallbackQuery",{"callback_query_id":q["id"]});send(chat,f"<b>Поддержка {html.escape(brand_name())}</b>\nСоздайте новый тикет или откройте историю обращений.",support_menu());return
        if data=="tickets:new":
            TICKET_NEW_WAIT.add(chat);tg("answerCallbackQuery",{"callback_query_id":q["id"]});send(chat,"Опишите проблему одним сообщением — от 5 до 2000 символов.\n\nДля отмены используйте /cancel.");return
        if data in ("tickets:list","tickets:admin"):
            admin=data=="tickets:admin"
            if admin and not is_admin(q["from"]["id"]):tg("answerCallbackQuery",{"callback_query_id":q["id"],"text":"Только для администратора","show_alert":"true"});return
            tg("answerCallbackQuery",{"callback_query_id":q["id"]});show_ticket_list(chat,q["from"]["id"],admin);return
        if data.startswith("ticket:view:"):
            ticket_id=int(data.rsplit(":",1)[1]);tg("answerCallbackQuery",{"callback_query_id":q["id"]})
            try:show_ticket(chat,q["from"]["id"],ticket_id,is_admin(q["from"]["id"]))
            except urllib.error.HTTPError:send(chat,"⛔ Тикет не найден или недоступен.")
            return
        if data.startswith("ticket:reply:"):
            ticket_id=int(data.rsplit(":",1)[1]);TICKET_REPLY_WAIT[chat]=ticket_id;tg("answerCallbackQuery",{"callback_query_id":q["id"]});send(chat,f"Введите ответ для тикета <b>#{ticket_id}</b> одним сообщением.\nДля отмены используйте /cancel.");return
        if data.startswith("ticket:close:"):
            ticket_id=int(data.rsplit(":",1)[1])
            try:
                x=api("/api/bot/tickets/close",{"telegram_id":q["from"]["id"],"ticket_id":ticket_id});tg("answerCallbackQuery",{"callback_query_id":q["id"],"text":"Тикет закрыт"});send(chat,f"✅ Тикет <b>#{ticket_id}</b> закрыт.",admin_menu() if is_admin(q["from"]["id"]) else support_menu())
                if is_admin(q["from"]["id"]) and int(x.get("telegram_id") or 0)>0 and int(x["telegram_id"])!=q["from"]["id"]:send(x["telegram_id"],f"✅ Поддержка закрыла тикет <b>#{ticket_id}</b>.")
                elif ADMIN_ID and not is_admin(q["from"]["id"]):send(ADMIN_ID,f"✅ Пользователь закрыл тикет <b>#{ticket_id}</b>.")
            except urllib.error.HTTPError as e:
                try:msg=json.loads(e.read()).get("error","Не удалось закрыть тикет")
                except Exception:msg="Не удалось закрыть тикет"
                tg("answerCallbackQuery",{"callback_query_id":q["id"],"text":msg,"show_alert":"true"})
            return
        if data=="buy:new":
            BUY_TARGET.pop(q["from"]["id"],None);tg("answerCallbackQuery",{"callback_query_id":q["id"]});send(chat,"<b>Новая подписка</b>\nБудет создана отдельная подписка и новая ссылка. Выберите тариф:",keyboard("new"));return
        if data=="buy:renew":
            items=api(f"/api/bot/clients?telegram_id={q['from']['id']}{test_query(q['from']['id'])}").get("clients",[])
            if not items:tg("answerCallbackQuery",{"callback_query_id":q["id"],"text":"Нет подписок для продления","show_alert":"true"});return
            rows=[[{"text":"🔄 "+x["email"],"callback_data":"renewselect:"+x["email"]}] for x in items]
            rows.append([{"text":"⬅️ Назад","callback_data":"menu:bot"}]);tg("answerCallbackQuery",{"callback_query_id":q["id"]});send(chat,"Выберите подписку, срок которой нужно увеличить:",json.dumps({"inline_keyboard":rows},ensure_ascii=False));return
        if data.startswith("renewselect:"):
            email=data.split(":",1)[1];items=api(f"/api/bot/clients?telegram_id={q['from']['id']}{test_query(q['from']['id'])}").get("clients",[]);client=next((x for x in items if x.get("email")==email),None)
            if not client:tg("answerCallbackQuery",{"callback_query_id":q["id"],"text":"Подписка не найдена","show_alert":"true"});return
            BUY_TARGET[q["from"]["id"]]=email;tg("answerCallbackQuery",{"callback_query_id":q["id"]});send(chat,f"Продление <b>{html.escape(email)}</b>\nКоличество устройств останется прежним: {client.get('limitIp') or 1}. Выберите срок:",renew_keyboard(client.get("limitIp") or 1));return
        if data=="buy:upgrade":
            items=api(f"/api/bot/clients?telegram_id={q['from']['id']}{test_query(q['from']['id'])}").get("clients",[]);now=int(time.time()*1000);available=[x for x in items if int(x.get("expiryTime") or 0)>now and int(x.get("limitIp") or 1)<5]
            if not available:tg("answerCallbackQuery",{"callback_query_id":q["id"],"text":"Нет действующих подписок для увеличения тарифа","show_alert":"true"});return
            rows=[[{"text":"⬆️ "+x["email"],"callback_data":"upgradeselect:"+x["email"]}] for x in available];rows.append([{"text":"⬅️ Назад","callback_data":"menu:bot"}]);tg("answerCallbackQuery",{"callback_query_id":q["id"]});send(chat,"Выберите подписку, в которой нужно увеличить количество устройств:",json.dumps({"inline_keyboard":rows},ensure_ascii=False));return
        if data.startswith("upgradeselect:"):
            email=data.split(":",1)[1];items=api(f"/api/bot/clients?telegram_id={q['from']['id']}{test_query(q['from']['id'])}").get("clients",[]);client=next((x for x in items if x.get("email")==email),None)
            if not client:tg("answerCallbackQuery",{"callback_query_id":q["id"],"text":"Подписка не найдена","show_alert":"true"});return
            markup,remaining=upgrade_keyboard(client)
            if remaining<=0 or int(client.get("limitIp") or 1)>=5:tg("answerCallbackQuery",{"callback_query_id":q["id"],"text":"Тариф сейчас нельзя увеличить","show_alert":"true"});return
            BUY_TARGET[q["from"]["id"]]=email;tg("answerCallbackQuery",{"callback_query_id":q["id"]});send(chat,f"<b>Изменение тарифа {html.escape(email)}</b>\nТекущий лимит: {client.get('limitIp') or 1} устройств. Осталось дней: {remaining}.\nОплачивается только разница за оставшиеся дни; срок и ссылка не изменятся.",markup);return
        if data=="menu:subscriptions":tg("answerCallbackQuery",{"callback_query_id":q["id"]});show_subscriptions(chat,q["from"]["id"]);return
        if data=="menu:link":tg("answerCallbackQuery",{"callback_query_id":q["id"]});LINK_WAIT.add(chat);send(chat,"Пришлите полную ссылку другой существующей подписки из программы VPN.");return
        if data.startswith("notifications:"):
            muted=data.endswith(":off");api("/api/bot/notifications",{"telegram_id":q["from"]["id"],"muted":muted})
            tg("answerCallbackQuery",{"callback_query_id":q["id"],"text":"Уведомления отключены" if muted else "Уведомления включены"})
            markup=json.dumps({"inline_keyboard":[[{"text":"🔔 Снова получать уведомления","callback_data":"notifications:on"}]]},ensure_ascii=False) if muted else bot_services_menu()
            send(chat,"🔕 Напоминания отключены. Результаты проверки оплаты будут приходить." if muted else "🔔 Напоминания о сроке подписки включены.",markup);return
        if data.startswith(("payapprove:","payreject:")):
            if not is_admin(q["from"]["id"]):tg("answerCallbackQuery",{"callback_query_id":q["id"],"text":"Только для администратора","show_alert":"true"});return
            order_id=int(data.split(":",1)[1]);action="approve" if data.startswith("payapprove:") else "reject"
            try:
                x=api("/api/bot/review-payment",{"order_id":order_id,"action":action})
                tg("answerCallbackQuery",{"callback_query_id":q["id"],"text":x.get("message","Готово")});send(chat,"✅ "+html.escape(x.get("message","Заказ обработан")))
            except urllib.error.HTTPError as e:
                try:
                    payload=json.loads(e.read());msg=payload.get("error") or payload.get("message")
                except Exception:msg="Заказ уже обработан или произошла ошибка"
                tg("answerCallbackQuery",{"callback_query_id":q["id"],"text":msg,"show_alert":"true"})
            return
        if data.startswith("payconfirm:"):
            try:
                order_id=int(data.split(":",1)[1]);x=api("/api/bot/confirm-payment",{"order_id":order_id,"telegram_id":q["from"]["id"]})
                tg("answerCallbackQuery",{"callback_query_id":q["id"],"text":x.get("message","Оплата отправлена на проверку"),"show_alert":"true"})
            except urllib.error.HTTPError as e:
                try:
                    payload=json.loads(e.read());msg=payload.get("error") or payload.get("message") or "Не удалось подтвердить оплату"
                except Exception:msg="Не удалось подтвердить оплату"
                tg("answerCallbackQuery",{"callback_query_id":q["id"],"text":msg,"show_alert":"true"})
            return
        if data.startswith("ordercancel:"):
            try:
                order_id=int(data.split(":",1)[1]);x=api("/api/bot/cancel-order",{"order_id":order_id,"telegram_id":q["from"]["id"]})
                tg("answerCallbackQuery",{"callback_query_id":q["id"],"text":x.get("message","Заказ отменён"),"show_alert":"true"});send(chat,"✅ Заказ отменён. Если изменения уже применялись временно, восстановлены прежние параметры подписки.",bot_services_menu())
            except urllib.error.HTTPError as e:
                try:payload=json.loads(e.read());msg=payload.get("error") or payload.get("message")
                except Exception:msg="Заказ уже обработан или не может быть отменён"
                tg("answerCallbackQuery",{"callback_query_id":q["id"],"text":msg,"show_alert":"true"})
            return
        if data.startswith("maintenance:"):
            if not is_admin(q["from"]["id"]):tg("answerCallbackQuery",{"callback_query_id":q["id"],"text":"Только для администратора","show_alert":"true"});return
            if not CHANNEL:tg("answerCallbackQuery",{"callback_query_id":q["id"],"text":"Канал не настроен","show_alert":"true"});return
            if data=="maintenance:planned":
                MAINT_WAIT.add(chat);tg("answerCallbackQuery",{"callback_query_id":q["id"]})
                send(chat,"Введите сроки плановых работ одним сообщением.\nНапример: <code>18.09.2026 с 02:00 до 04:00 МСК</code>\n\nДля отмены используйте /cancel.");return
            if data=="maintenance:unplanned":text=f"⚠️ Ведутся внеплановые технические работы {brand_name()}\n\nВозможны временные перебои подключения. Специалисты уже устраняют проблему. О завершении работ сообщим отдельно."
            else:text=f"✅ Технические работы завершены\n\nСервисы {brand_name()} снова работают в штатном режиме. Спасибо за ожидание."
            try:
                if data=="maintenance:unplanned":sent,failed=start_maintenance("unplanned",text);completed=0
                else:sent,failed,completed=complete_maintenance(text)
                tg("answerCallbackQuery",{"callback_query_id":q["id"],"text":"Уведомление отправлено"})
                suffix=f" Завершено активных уведомлений: {completed}." if data=="maintenance:end" else " Новые подписчики получат сообщение автоматически."
                send(chat,f"✅ Сообщение опубликовано в канале {html.escape(CHANNEL)} и отправлено активным подписчикам: {sent}. Не доставлено: {failed}.{suffix}")
            except Exception:tg("answerCallbackQuery",{"callback_query_id":q["id"],"text":"Ошибка публикации","show_alert":"true"})
            return
        if data=="decline":
            tg("answerCallbackQuery",{"callback_query_id":q["id"],"text":"Регистрация отменена"});send(chat,"Регистрация отменена. Данные на сайт не передавались.");return
        if data.startswith("agree:"):
            tg("answerCallbackQuery",{"callback_query_id":q["id"],"text":"Создаём аккаунт…"})
            try:
                a=api("/api/bot/telegram-auth",{"nonce":data.split(":",1)[1],"telegram_id":q["from"]["id"],"tg_username":q["from"].get("username","")})
                result="✅ Аккаунт создан автоматически." if a.get("new_user") else "✅ Аккаунт найден."
                send(chat,result+"\n\n<b>Оплата и управление в Telegram</b>\nДля одноразового входа используйте кнопку «Перейти на сайт».",bot_services_menu(a["login_url"]))
            except Exception as e:send(chat,"Не удалось завершить вход. Вернитесь на сайт и нажмите «Войти через Telegram» ещё раз.")
            return
        if data.startswith("linkaccount:"):
            tg("answerCallbackQuery",{"callback_query_id":q["id"],"text":"Объединяем аккаунты…"})
            try:
                x=api("/api/bot/link-account",{"nonce":data.split(":",1)[1],"telegram_id":q["from"]["id"],"tg_username":q["from"].get("username","")})
                send(chat,f"✅ Telegram привязан к аккаунту <b>{html.escape(x['username'])}</b>. Подписок обновлено: {x.get('clients_updated',0)}.",bot_services_menu())
            except urllib.error.HTTPError as e:
                try:msg=json.loads(e.read()).get("error","Не удалось объединить аккаунты")
                except Exception:msg="Не удалось объединить аккаунты"
                send(chat,"⛔ "+html.escape(msg))
            return
        if not data.startswith("order:"):
            tg("answerCallbackQuery",{"callback_query_id":q["id"],"text":"Команда устарела. Откройте меню заново.","show_alert":"true"});return
        _,mode,plan=data.split(":",2);pinfo=profile(q["from"]["id"]) or {};tg("answerCallbackQuery",{"callback_query_id":q["id"]})
        selected=selected_test_user(q["from"]["id"]);customer=selected or pinfo.get("login") or ("admin-test" if is_admin(q["from"]["id"]) else "@"+q["from"].get("username",""))
        try:o=api("/api/orders",{"plan":plan,"action":mode,"subscription":BUY_TARGET.get(q["from"]["id"],"") if mode in ("renew","upgrade") else "","telegram_id":q["from"]["id"],"customer":customer,"test_username":selected})
        except urllib.error.HTTPError as e:
            try:payload=json.loads(e.read());msg=payload.get("error","Не удалось создать заказ")
            except Exception:payload={};msg="Не удалось создать заказ"
            markup=json.dumps({"inline_keyboard":[[{"text":"💳 Открыть незавершённый заказ","url":payload["pay_url"]}]]},ensure_ascii=False) if payload.get("pay_url") and payload.get("order_id") else None
            send(chat,"⛔ "+html.escape(msg),markup);return
        if o["amount"]==0:
            markup=json.dumps({"inline_keyboard":[[{"text":"🎁 Активировать пробный период","callback_data":f"payconfirm:{o['id']}"}],[{"text":"❌ Отменить заказ","callback_data":f"ordercancel:{o['id']}"}]]},ensure_ascii=False)
            send(chat,f"Пробный заказ №{o['id']} создан. Один Telegram-аккаунт может получить его только один раз.",markup)
        else:
            qr=urllib.request.urlopen(o["qr_url"],context=ssl._create_unverified_context(),timeout=15).read()
            current="новая подписка" if not o.get("current_expiry") else time.strftime("%d.%m.%Y",time.localtime(o["current_expiry"]/1000))
            projected=time.strftime("%d.%m.%Y",time.localtime(o["projected_expiry"]/1000))
            action="Изменение тарифа" if o.get("action")=="upgrade" else ("Продление" if o.get("action")=="renew" else "Новая подписка")
            change_note=""
            if o.get("action")=="renew":
                change_note="\nКоличество устройств не изменится.\nСсылка подписки не изменится."
            elif o.get("action")=="upgrade":
                change_note=f"\nДоплата за оставшиеся дни: {o.get('remaining_days',0)} дн.\nСрок и ссылка подписки не изменятся."
            reference=o.get("payment_reference") or f"№{o['id']}";automatic=o.get("payment_method")=="yookassa"
            term_label="Срок остаётся до" if o.get("action")=="upgrade" else "Новый срок: до"
            if automatic:base_caption=f"💳 <b>Автоматическая оплата через ЮKassa</b>\n\nЗаказ №{o['id']} · {o['amount']} ₽\n{action}\nУстройства: {o.get('old_limit_ip') or '—'} → {o['new_limit_ip']}\nТекущий срок: {current}\n{term_label} {projected}{change_note}\n\nПосле подтверждения платежа подписка будет выдана автоматически. После начала оплаты отменить заказ нельзя."
            else:base_caption=f"⚠️ <b>ОБЯЗАТЕЛЬНО УКАЖИТЕ НОМЕР ЗАКАЗА</b>\nПри переводе в поле «Сообщение получателю» или «Комментарий» укажите: <b>{reference}</b>\n\nЗаказ №{o['id']} · {o['amount']} ₽\n{action}\nУстройства: {o.get('old_limit_ip') or '—'} → {o['new_limit_ip']}\nТекущий срок: {current}\n{term_label} {projected}{change_note}\n\nЕсли отправили не ту сумму, не выполняйте повторный перевод — обратитесь в поддержку.\n\nНажмите «Оплатить», чтобы временно активировать подписку и перейти к оплате. Заказ будет отправлен администратору на проверку. После этого отменить его нельзя."
            caption=f"⏱ <b>До окончания оплаты: {countdown_text(int(o['expires_at'])-int(time.time()))}</b>\n\n{base_caption}"
            buttons=[[{"text":"💳 Оплатить через ЮKassa" if automatic else "💳 Оплатить","url":o["payment_url"]}]]
            if not automatic:buttons.append([{"text":"📋 Скопировать номер заказа","copy_text":{"text":reference}}])
            buttons.extend([[{"text":"🎫 Обратиться в поддержку","callback_data":"menu:support"}],[{"text":"🧾 Открыть заказ","url":o["pay_url"]}],[{"text":"❌ Отменить заказ","callback_data":f"ordercancel:{o['id']}"}]])
            markup=json.dumps({"inline_keyboard":buttons},ensure_ascii=False)
            sent=tg("sendPhoto",{"chat_id":chat,"caption":caption,"parse_mode":"HTML","reply_markup":markup}, {"photo":("payment-qr.png",qr,"image/png")})
            track_payment_timer(o,chat,sent["result"]["message_id"],base_caption,markup)
        if ADMIN_ID and q["from"]["id"]!=ADMIN_ID: send(ADMIN_ID,f"🆕 Тестовый заказ №{o['id']} · {o['amount']} ₽\nПользователь: {q['from'].get('username') or q['from']['id']}")

def main():
    if not TOKEN: raise SystemExit("Set TELEGRAM_BOT_TOKEN in /etc/vpn-bot.env")
    enabled=menu_config()["items"];user_commands=[{"command":"start","description":"Оплата и управление"},{"command":"name","description":"Моё имя и логин"}]
    command_items=(("new","buy","Оформить подписку"),("subscriptions","subscriptions","Подписки и текущие заказы"),("support","support","Поддержка и тикеты"),("link","link","Привязать подписку"),("status","status","Статус серверов"),("notifications","unsubscribe","Отключить напоминания"))
    user_commands.extend({"command":command,"description":description} for key,command,description in command_items if enabled[key]["enabled"])
    if enabled["notifications"]["enabled"]:user_commands.append({"command":"subscribe","description":"Включить напоминания"})
    tg("setMyCommands",{"commands":json.dumps(user_commands,ensure_ascii=False),"scope":json.dumps({"type":"default"})})
    if ADMIN_ID:
        admin_commands=[{"command":"start","description":"Выбрать режим"},{"command":"admin","description":"Выбрать режим"},{"command":"pending","description":"Неподтверждённые заказы"},{"command":"tickets","description":"Открытые тикеты"},{"command":"name","description":"Имя и логин подписки"},{"command":"status","description":"Статус серверов"},{"command":"buy","description":"Тест покупки"},{"command":"subscriptions","description":"Тест подписок"}]
        tg("setMyCommands",{"commands":json.dumps(admin_commands,ensure_ascii=False),"scope":json.dumps({"type":"chat","chat_id":ADMIN_ID})})
    load_payment_timers();sync_payment_timers();update_payment_timers();offset=0
    try:
        with open(STATE) as f:last_event=int(f.read().strip())
    except Exception:
        last_event=api("/api/bot/events?after=0").get("last_id",0);save_cursor(last_event)
    last_expiry_check=0;last_maintenance_check=0
    while True:
        try:
            for u in tg("getUpdates",{"offset":offset,"timeout":25,"allowed_updates":json.dumps(["message","callback_query"])})["result"]:
                offset=u["update_id"]+1; handle(u)
            events=api(f"/api/bot/events?after={last_event}").get("events",[])
            for e in events:
                last_event=max(last_event,e["event_id"]);save_cursor(last_event);kind=e.get("event_type")
                if kind in ("ticket_created","ticket_user_reply","ticket_closed"):
                    if ADMIN_ID:
                        ticket_id=e.get("ticket_id");titles={"ticket_created":"🎫 Новый тикет с сайта","ticket_user_reply":"💬 Новое сообщение в тикете","ticket_closed":"✅ Пользователь закрыл тикет"}
                        source="гостевой чат" if e.get("ticket_source")=="guest" else ("сайт" if e.get("ticket_source")=="site" else "Telegram")
                        text=f"{titles[kind]} <b>#{ticket_id}</b>\nПользователь: {html.escape(str(e.get('ticket_customer') or 'не указан'))}\nИсточник: {source}"
                        if e.get("ticket_message") and kind!="ticket_closed":text+="\n\n"+html.escape(str(e["ticket_message"])[:1500])
                        rows=[[{"text":"Открыть тикет","callback_data":f"ticket:view:{ticket_id}"}]]
                        if kind!="ticket_closed":rows.insert(0,[{"text":"💬 Ответить","callback_data":f"ticket:reply:{ticket_id}"}])
                        send(ADMIN_ID,text,json.dumps({"inline_keyboard":rows},ensure_ascii=False))
                    continue
                name,devices=plan_info(e["plan"])
                if e.get("order_kind")=="upgrade":name="Изменение тарифа"
                if kind=="payment_review":
                    stop_payment_timer(e["id"],"⏳ <b>Оплата начата</b>\n\nТаймер остановлен. Заказ перенесён в раздел «Мои подписки» и ожидает проверки администратора.")
                    if e.get("telegram_id"):
                        subscription=e.get("subscription_url") or PUBLIC+"/sub/"+e["token"]
                        subscription_name=e.get("xui_email") or brand_name()
                        caption=f"⏳ <b>Оплата начата</b>\n\nЗаказ №{e['id']}\nПодписка: <b>{html.escape(subscription_name)}</b>\n\nПодписка временно активирована и передана администратору на проверку.\n\nСсылка подписки:\n<code>{html.escape(subscription)}</code>\n\nОтсканируйте QR-код в HAPP или скопируйте ссылку кнопкой ниже."
                        markup=json.dumps({"inline_keyboard":[[{"text":"📋 Скопировать подписку","copy_text":{"text":subscription}}],[{"text":"📖 Инструкции по подключению","url":PUBLIC+"/instructions"}]]},ensure_ascii=False)
                        try:
                            qr=urllib.request.urlopen(PUBLIC+"/subscription-qr/"+e["token"]+".png",context=ssl._create_unverified_context(),timeout=15).read()
                            tg("sendPhoto",{"chat_id":e["telegram_id"],"caption":caption,"parse_mode":"HTML","reply_markup":markup},{"photo":("home-vpn-subscription.png",qr,"image/png")})
                        except Exception:send(e["telegram_id"],caption,markup)
                    if ADMIN_ID:
                        markup=json.dumps({"inline_keyboard":[[{"text":"✅ Деньги пришли","callback_data":f"payapprove:{e['id']}"}],[{"text":"❌ Оплата не поступила","callback_data":f"payreject:{e['id']}"}]]},ensure_ascii=False)
                        send(ADMIN_ID,f"💳 <b>Пользователь нажал «Оплатить»</b>\nЗаказ: №{e['id']}\nПользователь: {html.escape(str(e.get('customer') or 'не указан'))}\nТариф: {name}\nУстройств: {devices}\nСумма: {e['amount']} ₽\nИмя подписки: <code>{html.escape(e.get('xui_email') or '—')}</code>",markup)
                elif kind=="approved":
                    stop_payment_timer(e["id"])
                    if e.get("telegram_id"):
                        subscription_name=e.get("xui_email") or brand_name()
                        if e.get("plan")=="trial":
                            subscription=e.get("subscription_url") or PUBLIC+"/sub/"+e["token"]
                            caption=f"🎁 <b>Пробный период активирован</b>\n\nЗаказ №{e['id']}\nПодписка: <b>{html.escape(subscription_name)}</b>\n\nСсылка подписки:\n<code>{html.escape(subscription)}</code>\n\nОтсканируйте QR-код в HAPP или скопируйте ссылку кнопкой ниже."
                            markup=json.dumps({"inline_keyboard":[[{"text":"📋 Скопировать подписку","copy_text":{"text":subscription}}],[{"text":"📖 Инструкции по подключению","url":PUBLIC+"/instructions"}]]},ensure_ascii=False)
                            try:
                                qr=urllib.request.urlopen(PUBLIC+"/subscription-qr/"+e["token"]+".png",context=ssl._create_unverified_context(),timeout=15).read()
                                tg("sendPhoto",{"chat_id":e["telegram_id"],"caption":caption,"parse_mode":"HTML","reply_markup":markup},{"photo":("home-vpn-subscription.png",qr,"image/png")})
                            except Exception:send(e["telegram_id"],caption,markup)
                        else:
                            send(e["telegram_id"],f"✅ <b>Оплата подтверждена</b>\n\nЗаказ №{e['id']}\nПодписка: <b>{html.escape(subscription_name)}</b>")
                elif kind=="rejected":
                    stop_payment_timer(e["id"])
                    if e.get("telegram_id"):
                        text="❌ Платёж не поступил. Изменение тарифа отменено; восстановлено прежнее количество устройств." if e.get("order_kind")=="upgrade" else "❌ Платёж не поступил. Временное продление отменено; восстановлен прежний срок подписки с учётом уже прошедшего времени."
                        send(e["telegram_id"],text)
                elif kind=="order_canceled":
                    stop_payment_timer(e["id"],"❌ <b>Заказ отменён</b>\n\nСоздайте новый заказ, если захотите продолжить оформление.")
                    if ADMIN_ID:send(ADMIN_ID,f"❌ Пользователь отменил заказ №{e['id']}. Подтверждение оплаты для него больше недоступно.")
            sync_payment_timers();update_payment_timers()
            if time.time()-last_maintenance_check>=30:
                last_maintenance_check=time.time();deliver_maintenance()
            if time.time()-last_expiry_check>=3600:
                last_expiry_check=time.time()
                for x in api("/api/bot/expiry-reminders").get("reminders",[]):
                    markup=json.dumps({"inline_keyboard":[[{"text":"💳 Продлить подписку","callback_data":"menu:buy"}],[{"text":"🌐 Оплатить на сайте","url":PUBLIC}],[{"text":"🔕 Отписаться","callback_data":"notifications:off"}]]},ensure_ascii=False)
                    until=time.strftime("%d.%m.%Y",time.localtime(x["expiryTime"]/1000))
                    send(x["telegram_id"],f"⏰ <b>Подписка скоро закончится</b>\nОсталось: {x['days_left']} дн.\nДата окончания: {until}\nПожалуйста, оплатите продление, чтобы VPN продолжил работать.",markup)
        except Exception as e: print(type(e).__name__,e,flush=True); time.sleep(POLL)

if __name__=="__main__": main()
