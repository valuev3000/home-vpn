#!/usr/bin/env python3
"""Installer and interactive configurator for HOME-VPN.

Run after 3x-ui is installed. Secrets are written only to /etc and never to
the repository configuration file.
"""
import argparse, getpass, grp, json, os, pathlib, secrets, shutil, ssl, subprocess, sys, tempfile, urllib.request
from urllib.parse import urlparse

ROOT=pathlib.Path(__file__).resolve().parents[1]
DEFAULT_CONFIG=ROOT/'config/config.example.json'
if not DEFAULT_CONFIG.exists():DEFAULT_CONFIG=pathlib.Path('/usr/local/share/home-vpn/config.example.json')
SYSTEM_CONFIG=pathlib.Path('/etc/home-vpn/config.json')

def ask(prompt,default=None,secret=False):
    suffix=f' [{default}]' if default not in (None,'') else ''
    value=(getpass.getpass if secret else input)(f'{prompt}{suffix}: ').strip()
    return value if value else (default or '')

def yes(prompt,default=True):
    value=ask(prompt,'Y/n' if default else 'y/N').lower()
    return value in ('y','yes','д','да') if value not in ('y/n','y/N') else default

def load(path):
    source=path if path.exists() else DEFAULT_CONFIG
    with source.open(encoding='utf-8') as stream:return json.load(stream)

def save(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix=path.name+'.',dir=path.parent,text=True)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as stream:json.dump(data,stream,ensure_ascii=False,indent=2);stream.write('\n')
        os.chmod(tmp,0o660);os.replace(tmp,path)
        if os.geteuid()==0:
            try:os.chown(path,0,grp.getgrnam('vpnshop').gr_gid);os.chmod(path.parent,0o770);os.chown(path.parent,0,grp.getgrnam('vpnshop').gr_gid)
            except KeyError:pass
    finally:
        if os.path.exists(tmp):os.unlink(tmp)

def validate(data):
    errors=[];branding=data.get('branding') or {};pricing=data.get('pricing') or {};devices=pricing.get('device_monthly') or {};nodes=data.get('nodes') or []
    if not str(branding.get('name') or '').strip():errors.append('branding.name is required')
    if not devices:errors.append('pricing.device_monthly is empty')
    for key,value in devices.items():
        if not str(key).isdigit() or int(key)<1 or int(value)<0:errors.append(f'invalid device price: {key}={value}')
    if int(pricing.get('traffic_gb',0))<0:errors.append('traffic_gb must be 0 or greater')
    if not nodes:errors.append('at least one node is required')
    if sum(1 for node in nodes if node.get('primary'))!=1:errors.append('exactly one primary node is required')
    ids=set()
    for node in nodes:
        node_id=str(node.get('id',''))
        if not node_id or node_id in ids:errors.append(f'invalid or duplicate node id: {node_id}')
        ids.add(node_id)
        if not node.get('name'):errors.append(f'node {node_id} has no name')
        if node.get('primary') and (not node.get('panel_url') or not node.get('inbound_ids')):errors.append('primary node needs panel_url and inbound_ids')
        for route in node.get('routes') or []:
            if not route.get('name') or not isinstance(route.get('inbound_id'),int):errors.append(f'node {node_id} has invalid route')
    return errors

def branding_wizard(data):
    branding=data.setdefault('branding',{})
    branding['name']=ask('Название сервиса',branding.get('name','HOME-VPN'))
    branding['tagline']=ask('Слоган',branding.get('tagline','Домашняя свобода'))
    branding['subtitle']=ask('Подзаголовок',branding.get('subtitle','Безопасный интернет каждый день'))

def discover_node(node,token):
    """Read inbound IDs and route labels directly from a 3x-ui node."""
    url=str(node.get('panel_url') or '').rstrip('/')
    if not url or not token:return []
    req=urllib.request.Request(url+'/panel/api/inbounds/list',headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
    with urllib.request.urlopen(req,context=ssl._create_unverified_context(),timeout=20) as response:result=json.loads(response.read())
    if not result.get('success',True):raise RuntimeError(str(result.get('msg') or '3x-ui вернула ошибку'))
    routes=[]
    for inbound in result.get('obj') or []:
        stream=inbound.get('streamSettings') or {}
        if isinstance(stream,str):
            try:stream=json.loads(stream)
            except json.JSONDecodeError:stream={}
        routes.append({'name':str(inbound.get('remark') or f"inbound-{inbound.get('id')}"),'inbound_id':int(inbound.get('id')),'protocol':str(inbound.get('protocol') or ''),'transport':str(stream.get('network') or 'tcp'),'enabled':bool(inbound.get('enable',True))})
    return routes

def pricing_wizard(data):
    pricing=data.setdefault('pricing',{});pricing['currency']=ask('Валюта',pricing.get('currency','₽'))
    count=int(ask('Максимальное количество устройств',pricing.get('max_devices',5)));prices=pricing.setdefault('device_monthly',{})
    pricing['max_devices']=count;pricing['device_monthly']={str(n):int(ask(f'Цена за 1 месяц / {n} устройств',prices.get(str(n),n*100))) for n in range(1,count+1)}
    pricing['traffic_gb']=int(ask('Лимит трафика в ГБ, 0 = без ограничений',pricing.get('traffic_gb',0)))
    trial=pricing.setdefault('trial',{});trial['enabled']=yes('Включить пробный тариф',trial.get('enabled',True))
    if trial['enabled']:
        trial['name']=ask('Название пробного тарифа',trial.get('name','Пробный'));trial['days']=int(ask('Дней в пробном тарифе',trial.get('days',3)));trial['devices']=int(ask('Устройств в пробном тарифе',trial.get('devices',1)))
    for code,period in pricing.setdefault('periods',{}).items():
        print(f'\nПериод {code}');period['name']=ask('Название',period.get('name',code));period['days']=int(ask('Дней',period.get('days',30)));period['multiplier']=float(ask('Множитель цены',period.get('multiplier',1)))

def route_wizard(node):
    routes=[]
    print('Маршруты: имя,inbound_id,protocol,transport. Пустая строка завершает ввод.')
    while True:
        raw=ask('Маршрут')
        if not raw:break
        parts=[x.strip() for x in raw.split(',')]
        if len(parts)<2 or not parts[1].isdigit():print('Неверный формат');continue
        routes.append({'name':parts[0],'inbound_id':int(parts[1]),'protocol':parts[2] if len(parts)>2 else 'vless','transport':parts[3] if len(parts)>3 else 'tcp','enabled':True})
    if routes:node['routes']=routes;node['inbound_ids']=sorted({x['inbound_id'] for x in routes})

def edit_node(node):
    node['name']=ask('Имя сервера',node.get('name','Новая нода'));node['country']=ask('Код страны',node.get('country',''));node['flag']=ask('Флаг',node.get('flag','🌐'))
    node['panel_url']=ask('URL API 3x-ui',node.get('panel_url',''));node['token_env']=ask('Имя переменной с токеном',node.get('token_env','XUI_TOKEN'))
    inbound=ask('Inbound ID через запятую',','.join(map(str,node.get('inbound_ids') or [])));node['inbound_ids']=[int(x) for x in inbound.split(',') if x.strip().isdigit()]
    node['subscription_base_url']=ask('Существующий домен подписки (необязательно)',node.get('subscription_base_url',''));node['enabled']=yes('Нода включена',node.get('enabled',True))
    if yes('Изменить таблицу маршрутизации',False):route_wizard(node)

def nodes_wizard(data):
    nodes=data.setdefault('nodes',[])
    while True:
        print('\nУзлы:');[print(f" {i+1}. {'*' if n.get('primary') else ' '} {n.get('name')} [{n.get('id')}]") for i,n in enumerate(nodes)]
        action=ask('Номер для редактирования, A добавить, P назначить основную, D удалить, Enter готово').lower()
        if not action:break
        if action=='a':
            node={'id':ask('Короткий ID ноды',f'node-{len(nodes)+1}'),'primary':not nodes,'enabled':True,'routes':[]};edit_node(node);nodes.append(node)
        elif action=='p':
            idx=int(ask('Номер основной ноды'))-1
            for i,node in enumerate(nodes):node['primary']=i==idx
        elif action=='d':
            idx=int(ask('Номер удаляемой ноды'))-1
            if nodes[idx].get('primary'):print('Сначала назначьте другую основную ноду')
            elif yes(f"Удалить {nodes[idx].get('name')}?",False):nodes.pop(idx)
        elif action.isdigit() and 0<int(action)<=len(nodes):edit_node(nodes[int(action)-1])

def configure(path):
    data=load(path)
    while True:
        print('\nHOME-VPN: 1 название, 2 тарифы, 3 серверы/маршруты, 4 оплата, 5 показать, 6 сохранить и выйти')
        choice=ask('Выберите раздел')
        if choice=='1':branding_wizard(data)
        elif choice=='2':pricing_wizard(data)
        elif choice=='3':nodes_wizard(data)
        elif choice=='4':
            payment=data.setdefault('payment',{});mode=ask('Режим: manual или yookassa',payment.get('mode','manual')).lower()
            if mode not in ('manual','yookassa'):print('Допустимо только manual или yookassa');continue
            payment['mode']=mode;payment['provider_name']=ask('Название ручного способа оплаты',payment.get('provider_name','Оплата'));payment['url']=ask('Внешняя ссылка ручной оплаты (пусто = без ссылки)',payment.get('url',''))
        elif choice=='5':print(json.dumps(data,ensure_ascii=False,indent=2))
        elif choice=='6':
            errors=validate(data)
            if errors:print('Ошибки:\n- '+'\n- '.join(errors));continue
            save(path,data);print(f'Сохранено: {path}');return

def run(*args,check=True):return subprocess.run(args,check=check)

def write_env(path,values):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(''.join(f'{key}={value}\n' for key,value in values.items()),encoding='utf-8');path.chmod(0o600)

def read_env(path):
    result={}
    if path.exists():
        for line in path.read_text(encoding='utf-8').splitlines():
            if line and not line.lstrip().startswith('#') and '=' in line:
                key,value=line.split('=',1);result[key]=value
    return result

def listener_on(port):
    result=subprocess.run(['ss','-lntp'],check=False,capture_output=True,text=True)
    return next((line.strip() for line in result.stdout.splitlines() if f':{port} ' in line), '')

def tls_candidates():
    return [('/etc/x-ui/cert/fullchain.pem','/etc/x-ui/cert/privatekey.pem'),('/etc/x-ui/cert/panel.crt','/etc/x-ui/cert/panel.key'),('/root/cert/fullchain.pem','/root/cert/privkey.pem')]

def install_tls_copy(cert,key):
    target=pathlib.Path('/etc/home-vpn/tls');target.mkdir(parents=True,exist_ok=True)
    cert_target=target/'site.crt';key_target=target/'site.key'
    shutil.copy2(cert,cert_target);shutil.copy2(key,key_target)
    group=grp.getgrnam('vpnshop').gr_gid
    os.chown(target,0,group);os.chmod(target,0o750)
    for path in (cert_target,key_target):os.chown(path,0,group);os.chmod(path,0o640)
    return str(cert_target),str(key_target)

def install(config_path):
    if os.geteuid()!=0:raise SystemExit('Запустите установку через sudo')
    if not config_path.exists():configure(config_path)
    data=load(config_path);errors=validate(data)
    if errors:raise SystemExit('Ошибка конфигурации: '+'; '.join(errors))
    primary=next(x for x in data['nodes'] if x.get('primary'))
    if primary.get('panel_url','').startswith(('https://127.0.0.1','http://127.0.0.1','https://localhost','http://localhost')):
        active=subprocess.run(['systemctl','is-active','--quiet','x-ui']).returncode==0
        if not active:raise SystemExit('Сначала установите и запустите 3x-ui (systemctl status x-ui)')
    run('apt-get','update');run('apt-get','install','-y','python3','python3-qrcode','python3-cryptography','sudo')
    run('useradd','--system','--home','/var/lib/vpn-shop','--shell','/usr/sbin/nologin','vpnshop',check=False)
    run('chown','root:vpnshop',str(config_path.parent),str(config_path));run('chmod','770',str(config_path.parent));run('chmod','660',str(config_path))
    pathlib.Path('/opt/vpn-shop').mkdir(parents=True,exist_ok=True);pathlib.Path('/srv/home-vpn/assets').mkdir(parents=True,exist_ok=True);pathlib.Path('/var/lib/vpn-shop').mkdir(parents=True,exist_ok=True)
    shutil.copy2(ROOT/'vpn-shop/app.py','/opt/vpn-shop/app.py')
    for item in (ROOT/'vpn-shop/static/original').glob('*'):shutil.copy2(item,pathlib.Path('/srv/home-vpn/assets')/item.name)
    shutil.copy2(ROOT/'vpn-shop/vpn-shop.service','/etc/systemd/system/vpn-shop.service')
    service_files=('home-vpn-backup.service','home-vpn-backup.timer','home-vpn-healthcheck.service','home-vpn-healthcheck.timer','home-vpn-system-check.service','home-vpn-system-check.timer')
    shutil.copy2(ROOT/'scripts/home-vpn-backup.py','/usr/local/sbin/home-vpn-backup');os.chmod('/usr/local/sbin/home-vpn-backup',0o700)
    shutil.copy2(ROOT/'scripts/home-vpn-backup-control.py','/usr/local/sbin/home-vpn-backup-control');os.chmod('/usr/local/sbin/home-vpn-backup-control',0o700)
    restore_alias=pathlib.Path('/usr/local/sbin/home-vpn-restore')
    restore_alias.unlink(missing_ok=True);restore_alias.symlink_to('/usr/local/sbin/home-vpn-backup-control')
    shutil.copy2(ROOT/'scripts/home-vpn-backup-sudoers','/etc/sudoers.d/home-vpn-backup');os.chmod('/etc/sudoers.d/home-vpn-backup',0o440)
    run('visudo','-cf','/etc/sudoers.d/home-vpn-backup')
    shutil.copy2(ROOT/'scripts/home-vpn-healthcheck.sh','/usr/local/sbin/home-vpn-healthcheck');os.chmod('/usr/local/sbin/home-vpn-healthcheck',0o700)
    shutil.copy2(ROOT/'scripts/home-vpn-system-check.py','/usr/local/sbin/home-vpn-system-check');os.chmod('/usr/local/sbin/home-vpn-system-check',0o700)
    for service_file in service_files:shutil.copy2(ROOT/'scripts'/service_file,pathlib.Path('/etc/systemd/system')/service_file)
    pathlib.Path('/usr/local/share/home-vpn').mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/'config/config.example.json','/usr/local/share/home-vpn/config.example.json')
    shutil.copy2(__file__,'/usr/local/sbin/home-vpn-setup');os.chmod('/usr/local/sbin/home-vpn-setup',0o750)
    public=ask('Публичный URL сайта','https://vpn.my.domain.ru');parsed=urlparse(public if '://' in public else 'https://'+public);public_port=parsed.port or (443 if parsed.scheme=='https' else 80)
    detected=next(((cert,key) for cert,key in tls_candidates() if pathlib.Path(cert).is_file() and pathlib.Path(key).is_file()),('',''))
    cert=key=''
    if parsed.scheme=='https':
        cert=ask('TLS-сертификат для сайта',detected[0]);key=ask('TLS-ключ для сайта',detected[1]) if cert else ''
        if cert and (not pathlib.Path(cert).is_file() or not pathlib.Path(key).is_file()):raise SystemExit('TLS-сертификат или ключ не найден')
    occupied=listener_on(public_port)
    direct=not occupied and (parsed.scheme!='https' or bool(cert and key))
    if direct:print(f'Порт {public_port} свободен: сайт будет опубликован напрямую, Nginx не нужен.')
    else:
        reason=f'порт {public_port} уже занят: {occupied}' if occupied else 'для HTTPS не указан сертификат и ключ'
        print('Обнаружен внешний вход ('+reason+'). Сайт останется на 127.0.0.1:8080; настройте уже установленный прокси на этот адрес. Nginx проект не устанавливает.')
    admin_user=ask('Логин администратора','home-admin');admin_password=ask('Пароль администратора',secrets.token_urlsafe(16),True);test_user=ask('Логин тестового пользователя','test-user');test_password=ask('Пароль тестового пользователя',secrets.token_urlsafe(16),True)
    bot_mode=os.getenv('HOME_VPN_INSTALL_BOT','ask').lower()
    telegram_token='' if bot_mode in ('0','no','false') else ask('Telegram Bot Token (пусто = не устанавливать бота)','',True)
    telegram_admin_id=ask('Telegram ID администратора бота','0') if telegram_token else '0'
    if telegram_admin_id and not telegram_admin_id.isdigit():raise SystemExit('Telegram ID администратора должен состоять только из цифр')
    telegram_bot_name=ask('Имя Telegram-бота без @','your_bot') if telegram_token else 'your_bot'
    telegram_channel=ask('Telegram-канал, например @my_channel (необязательно)','') if telegram_token else ''
    bot_admin_url=ask('URL отдельной панели управления ботом (необязательно)','') if telegram_token else ''
    bot_panel_user=ask('Логин отдельной панели бота','bot-admin') if telegram_token else ''
    bot_panel_password=ask('Пароль отдельной панели бота (пусто = сгенерировать)','',True) if telegram_token else ''
    if telegram_token and not bot_panel_password:bot_panel_password=secrets.token_urlsafe(16)
    bot_panel_entry='/bot-'+secrets.token_urlsafe(8) if telegram_token else ''
    smtp_host=ask('SMTP-сервер для писем (пусто = почтовая регистрация отключена)','')
    smtp_port=ask('SMTP-порт','587') if smtp_host else '587'
    if smtp_host and not smtp_port.isdigit():raise SystemExit('SMTP-порт должен быть числом')
    mail_from=ask('Адрес отправителя','support@my.domain.ru') if smtp_host else 'support@my.domain.ru'
    smtp_user=ask('SMTP-логин',mail_from) if smtp_host else ''
    smtp_password=ask('SMTP-пароль приложения','',True) if smtp_host else ''
    smtp_ssl='1' if smtp_host and smtp_port=='465' else '0';smtp_starttls='1' if smtp_host and smtp_port!='465' else '0'
    imap_host=ask('IMAP-сервер для входящих писем','') if smtp_host else ''
    imap_port=ask('IMAP-порт','993') if imap_host else '993'
    if imap_host and not imap_port.isdigit():raise SystemExit('IMAP-порт должен быть числом')
    imap_user=ask('IMAP-логин',smtp_user) if imap_host else ''
    imap_password=ask('IMAP-пароль приложения (пусто = как SMTP)','',True) if imap_host else ''
    payment=data.setdefault('payment',{});payment_mode=ask('Оплата: manual или yookassa',payment.get('mode','manual')).lower()
    if payment_mode not in ('manual','yookassa'):raise SystemExit('Допустимо только manual или yookassa')
    payment['mode']=payment_mode;yookassa_shop_id=ask('ЮKassa Shop ID','') if payment_mode=='yookassa' else '';yookassa_secret=ask('ЮKassa секретный ключ','',True) if payment_mode=='yookassa' else ''
    if payment_mode=='yookassa' and (not yookassa_shop_id or not yookassa_secret):raise SystemExit('Для автоматической оплаты нужны Shop ID и секретный ключ ЮKassa')
    api_secret=secrets.token_urlsafe(48);admin_entry='/admin-'+secrets.token_urlsafe(8)
    env={'VPN_SHOP_DB':'/var/lib/vpn-shop/shop.db','HOME_VPN_CONFIG':str(config_path),'APP_ENV':'production','PUBLIC_URL':public,'APP_BIND':'0.0.0.0' if direct else '127.0.0.1','APP_PORT':str(public_port if direct else 8080),'TRUST_PROXY':'0' if direct else '1','SESSION_SECRET':secrets.token_urlsafe(48),'API_SECRET':api_secret,'ADMIN_USER':admin_user,'ADMIN_PASSWORD':admin_password,'ADMIN_ENTRY_PATH':admin_entry,'TEST_USER':test_user,'TEST_PASSWORD':test_password,'TEST_ENTRY_PATH':'/test-'+secrets.token_urlsafe(8),'TELEGRAM_ADMIN_ID':telegram_admin_id or '0','TELEGRAM_BOT_URL':'https://t.me/'+telegram_bot_name.lstrip('@'),'BOT_ADMIN_URL':bot_admin_url,'SMTP_HOST':smtp_host,'SMTP_PORT':smtp_port,'SMTP_USERNAME':smtp_user,'SMTP_PASSWORD':smtp_password,'SMTP_STARTTLS':smtp_starttls,'SMTP_SSL':smtp_ssl,'IMAP_HOST':imap_host,'IMAP_PORT':imap_port,'IMAP_USERNAME':imap_user,'IMAP_PASSWORD':imap_password or smtp_password,'IMAP_SSL':'1','MAIL_FROM':mail_from,'MAIL_REPLY_TO':mail_from,'PAYMENT_URL':'','YOOKASSA_SHOP_ID':yookassa_shop_id,'YOOKASSA_SECRET_KEY':yookassa_secret,'YOOKASSA_API_URL':'https://api.yookassa.ru/v3','YOOKASSA_RETURN_URL':''}
    if direct and cert and key:env['TLS_CERT'],env['TLS_KEY']=install_tls_copy(cert,key)
    for node in data.get('nodes') or []:
        token_name=str(node.get('token_env') or ('XUI_TOKEN' if node.get('primary') else str(node.get('id','NODE')).upper().replace('-','_')+'_XUI_TOKEN'))
        node['token_env']=token_name;token=ask(f"API-токен 3x-ui: {node.get('name') or node.get('id')}",'',True);env[token_name]=token
        if token and node.get('panel_url'):
            try:
                routes=discover_node(node,token)
                if routes:
                    node['routes']=routes;node['inbound_ids']=[route['inbound_id'] for route in routes if route.get('enabled',True)]
                    print(f"Получено из {node.get('name')}: inbound {','.join(map(str,node['inbound_ids']))}; маршрутов {len(routes)}")
            except Exception as error:print(f"Предупреждение: не удалось получить данные {node.get('name')}: {error}")
    save(config_path,data)
    write_env(pathlib.Path('/etc/vpn-shop.env'),env);run('chown','-R','vpnshop:vpnshop','/var/lib/vpn-shop','/opt/vpn-shop')
    if telegram_token:
        run('useradd','--system','--home','/var/lib/vpn-bot','--shell','/usr/sbin/nologin','vpnshopbot',check=False)
        pathlib.Path('/opt/vpn-bot').mkdir(parents=True,exist_ok=True);pathlib.Path('/var/lib/vpn-bot').mkdir(parents=True,exist_ok=True)
        shutil.copy2(ROOT/'vpn-bot/bot.py','/opt/vpn-bot/bot.py');shutil.copy2(ROOT/'vpn-bot/vpn-bot.service','/etc/systemd/system/vpn-bot.service')
        shutil.copy2(ROOT/'vpn-bot/admin.py','/opt/vpn-bot/admin.py');shutil.copy2(ROOT/'vpn-bot/admin-control.py','/usr/local/sbin/vpn-bot-admin-control');os.chmod('/usr/local/sbin/vpn-bot-admin-control',0o700)
        shutil.copy2(ROOT/'vpn-bot/vpn-bot-admin.service','/etc/systemd/system/vpn-bot-admin.service');shutil.copy2(ROOT/'vpn-bot/vpn-bot-admin-sudoers','/etc/sudoers.d/vpn-bot-admin');os.chmod('/etc/sudoers.d/vpn-bot-admin',0o440);run('visudo','-cf','/etc/sudoers.d/vpn-bot-admin')
        menu_config=pathlib.Path('/etc/vpn-bot-menu.json')
        if not menu_config.exists():shutil.copy2(ROOT/'config/vpn-bot-menu.example.json',menu_config)
        run('chown','root:vpnshopbot',str(menu_config));os.chmod(menu_config,0o640)
        bot_env={'TELEGRAM_BOT_TOKEN':telegram_token,'TELEGRAM_ADMIN_ID':telegram_admin_id or '0','TELEGRAM_CHANNEL':telegram_channel,'SHOP_API_URL':'http://127.0.0.1:8080','PUBLIC_BASE_URL':public,'SHOP_API_SECRET':api_secret,'ADMIN_PANEL_URL':public+admin_entry,'BOT_STATE_FILE':'/var/lib/vpn-bot/last_event_v2','PAYMENT_TIMERS_STATE':'/var/lib/vpn-bot/payment_timers.json','BOT_MENU_CONFIG':str(menu_config)}
        write_env(pathlib.Path('/etc/vpn-bot.env'),bot_env);run('chown','-R','vpnshopbot:vpnshopbot','/var/lib/vpn-bot','/opt/vpn-bot')
        write_env(pathlib.Path('/etc/vpn-bot-admin.env'),{'BOT_PANEL_BIND':'127.0.0.1','BOT_PANEL_PORT':'8090','BOT_PANEL_USER':bot_panel_user,'BOT_PANEL_PASSWORD':bot_panel_password,'BOT_PANEL_SECRET':secrets.token_urlsafe(48),'BOT_PANEL_ENTRY_PATH':bot_panel_entry,'BOT_PANEL_SECURE_COOKIE':'1'})
    run('systemctl','daemon-reload');run('systemctl','enable','--now','vpn-shop');run('systemctl','enable','--now','home-vpn-backup.timer','home-vpn-healthcheck.timer','home-vpn-system-check.timer');run('systemctl','start','home-vpn-backup.service');run('systemctl','start','home-vpn-system-check.service')
    if telegram_token:run('systemctl','enable','--now','vpn-bot','vpn-bot-admin')
    print('\nHOME-VPN установлен: '+public);print('Редактор: sudo home-vpn-setup configure');print('Проверка: sudo home-vpn-setup doctor')
    if telegram_token:print('Панель бота:',(bot_admin_url.rstrip('/') if bot_admin_url else 'http://127.0.0.1:8090')+bot_panel_entry);print('Логин панели бота:',bot_panel_user);print('Пароль панели бота:',bot_panel_password)

def doctor(path):
    data=load(path);errors=validate(data)
    print('Конфигурация:', 'OK' if not errors else '\n- '+'\n- '.join(errors))
    primary=next((x for x in data.get('nodes',[]) if x.get('primary')),None);print('Основная 3x-ui:',primary.get('name') if primary else 'не задана');print('Узлов:',len(data.get('nodes',[])));print('Тарифов устройств:',len((data.get('pricing') or {}).get('device_monthly',{})))
    if pathlib.Path('/etc/systemd/system/vpn-shop.service').exists():run('systemctl','is-active','vpn-shop',check=False)
    if pathlib.Path('/etc/systemd/system/home-vpn-backup.timer').exists():run('systemctl','is-active','home-vpn-backup.timer','home-vpn-healthcheck.timer','home-vpn-system-check.timer',check=False)
    env_path=pathlib.Path('/etc/vpn-shop.env')
    if env_path.exists():
        values=read_env(env_path);configured=bool(values.get('SMTP_HOST'))
        print('Почтовая регистрация:', 'SMTP настроен' if configured else 'отключена — SMTP_HOST не задан')
        mode=str((data.get('payment') or {}).get('mode') or 'manual');yk=bool(values.get('YOOKASSA_SHOP_ID') and values.get('YOOKASSA_SECRET_KEY'))
        print('Оплата:',('ЮKassa настроена' if yk else 'ЮKassa требует Shop ID и ключ') if mode=='yookassa' else 'ручное подтверждение администратора')
        print('Backup-панель:',values.get('BACKUP_PANEL_URL') or 'URL не задан')

def backup_remote_setup():
    if os.geteuid()!=0:raise SystemExit('Запустите через sudo')
    host=ask('IP или домен backup-сервера')
    if not host:raise SystemExit('Backup-сервер не указан')
    port=ask('SSH-порт backup-сервера','22')
    if not port.isdigit():raise SystemExit('SSH-порт должен быть числом')
    remote_user=ask('Системный пользователь backup-сервера','homevpnbackup')
    panel_url=ask('URL графической панели Backup-сервера (необязательно)','')
    key=pathlib.Path('/root/.ssh/home-vpn-backup');key.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    if not key.exists():run('ssh-keygen','-q','-t','ed25519','-N','','-f',str(key))
    scan=subprocess.run(['ssh-keyscan','-p',port,host],check=True,capture_output=True,text=True).stdout
    if not scan.strip():raise SystemExit('Не удалось получить SSH-ключ backup-сервера')
    with tempfile.NamedTemporaryFile('w',delete=False) as temporary:temporary.write(scan);scan_path=temporary.name
    try:
        fingerprint=subprocess.run(['ssh-keygen','-lf',scan_path],check=True,capture_output=True,text=True).stdout.strip()
    finally:os.unlink(scan_path)
    print('Отпечаток backup-сервера:\n'+fingerprint)
    if not yes('Отпечаток проверен и соответствует вашему backup-серверу',False):raise SystemExit('Настройка отменена')
    known=pathlib.Path('/etc/home-vpn-backup-known-hosts');known.write_text(scan,encoding='utf-8');known.chmod(0o600)
    write_env(pathlib.Path('/etc/home-vpn-backup.env'),{'HOME_VPN_BACKUP_REMOTE':f'{remote_user}@{host}','HOME_VPN_BACKUP_REMOTE_PORT':port,'HOME_VPN_BACKUP_SSH_KEY':str(key),'HOME_VPN_BACKUP_KNOWN_HOSTS':str(known)})
    shop_env=pathlib.Path('/etc/vpn-shop.env')
    if shop_env.exists():
        values=read_env(shop_env);values['BACKUP_PANEL_URL']=panel_url;write_env(shop_env,values);run('systemctl','restart','vpn-shop',check=False)
    public=key.with_suffix('.pub').read_text(encoding='utf-8').strip()
    print('\nНа backup-сервере выполните из каталога проекта:\n')
    print("sudo python3 backup-node/install-backup-node.py --public-key "+repr(public))
    print('\nПосле этого проверьте отправку: sudo systemctl start home-vpn-backup.service')

def tls_setup():
    if os.geteuid()!=0:raise SystemExit('Запустите через sudo')
    env_path=pathlib.Path('/etc/vpn-shop.env')
    if not env_path.exists():raise SystemExit('Сначала установите HOME-VPN')
    values={}
    for line in env_path.read_text(encoding='utf-8').splitlines():
        if line and not line.lstrip().startswith('#') and '=' in line:
            key,value=line.split('=',1);values[key]=value
    domain=ask('Домен HOME-VPN',values.get('PUBLIC_URL','https://vpn.my.domain.ru').split('://')[-1].split('/')[0]);parsed=urlparse('https://'+domain);port=parsed.port or 443
    detected=next(((cert,key) for cert,key in tls_candidates() if pathlib.Path(cert).exists() and pathlib.Path(key).exists()),('',''))
    cert=ask('TLS-сертификат 3x-ui',detected[0]);key=ask('TLS-ключ 3x-ui',detected[1])
    if not pathlib.Path(cert).is_file() or not pathlib.Path(key).is_file():raise SystemExit('Сертификат или ключ не найден')
    occupied=listener_on(port)
    if occupied and 'python3' not in occupied:raise SystemExit(f'Порт {port} занят. Используйте существующий внешний прокси или освободите порт: {occupied}')
    values['PUBLIC_URL']='https://'+domain;values['APP_BIND']='0.0.0.0';values['APP_PORT']=str(port);values['TRUST_PROXY']='0';values['TLS_CERT'],values['TLS_KEY']=install_tls_copy(cert,key);write_env(env_path,values)
    run('systemctl','restart','vpn-shop');print('Домен и встроенный TLS применены без Nginx: https://'+domain)

def payment_setup(config_path):
    if os.geteuid()!=0:raise SystemExit('Запустите через sudo')
    env_path=pathlib.Path('/etc/vpn-shop.env')
    if not env_path.exists():raise SystemExit('Сначала установите HOME-VPN')
    data=load(config_path);payment=data.setdefault('payment',{});values=read_env(env_path)
    mode=ask('Активный режим оплаты: manual или yookassa',payment.get('mode','manual')).lower()
    if mode not in ('manual','yookassa'):raise SystemExit('Допустимо только manual или yookassa')
    payment['mode']=mode
    if mode=='manual':
        payment['provider_name']=ask('Название ручного способа',payment.get('provider_name','Оплата'));payment['url']=ask('Ссылка ручной оплаты (пусто = инструкция администратора)',payment.get('url',''))
    else:
        shop_id=ask('ЮKassa Shop ID',values.get('YOOKASSA_SHOP_ID',''));secret=ask('Новый секретный ключ (Enter = оставить текущий)','',True) or values.get('YOOKASSA_SECRET_KEY','')
        if not shop_id or not secret:raise SystemExit('Shop ID и секретный ключ обязательны')
        values['YOOKASSA_SHOP_ID']=shop_id;values['YOOKASSA_SECRET_KEY']=secret;values['YOOKASSA_API_URL']='https://api.yookassa.ru/v3';values.setdefault('YOOKASSA_RETURN_URL','')
    save(config_path,data);write_env(env_path,values);run('systemctl','restart','vpn-shop');public=values.get('PUBLIC_URL','https://vpn.my.domain.ru').rstrip('/')
    print('Режим оплаты применён:',mode)
    if mode=='yookassa':print('Webhook для кабинета ЮKassa:',public+'/api/payments/yookassa/webhook')

def main():
    parser=argparse.ArgumentParser(description='HOME-VPN installer and configuration editor');parser.add_argument('command',choices=('install','configure','tls','payment','backup-remote','validate','show','doctor'));parser.add_argument('--config',type=pathlib.Path,default=SYSTEM_CONFIG);args=parser.parse_args()
    if args.command=='install':install(args.config)
    elif args.command=='configure':configure(args.config);run('systemctl','restart','vpn-shop',check=False) if os.geteuid()==0 else None
    elif args.command=='tls':tls_setup()
    elif args.command=='payment':payment_setup(args.config)
    elif args.command=='backup-remote':backup_remote_setup()
    elif args.command=='validate':
        errors=validate(load(args.config));print('OK' if not errors else '\n'.join(errors));raise SystemExit(bool(errors))
    elif args.command=='show':print(json.dumps(load(args.config),ensure_ascii=False,indent=2))
    else:doctor(args.config)
if __name__=='__main__':main()
