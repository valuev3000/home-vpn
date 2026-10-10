#!/usr/bin/env python3
"""Installer and interactive configurator for HOME-VPN.

Run after 3x-ui is installed. Secrets are written only to /etc and never to
the repository configuration file.
"""
import argparse, getpass, grp, ipaddress, json, os, pathlib, re, secrets, shutil, subprocess, sys, tempfile, urllib.request
from urllib.parse import urlparse

from home_vpn_config import ENV_SCHEMAS, PROJECT_VERSION, default_config, merge_env, validate_project_config
from home_vpn_installer import install_component_files

ROOT=pathlib.Path(__file__).resolve().parents[1]
SYSTEM_CONFIG=pathlib.Path('/etc/home-vpn/config.json')
INSTALL_ANSWERS={}
NON_INTERACTIVE=False

def ask(prompt,default=None,secret=False):
    suffix=f' [{default}]' if default not in (None,'') else ''
    value=(getpass.getpass if secret else input)(f'{prompt}{suffix}: ').strip()
    return value if value else (default or '')

def yes(prompt,default=True):
    value=ask(prompt,'Y/n' if default else 'y/N').lower()
    return value in ('y','yes','д','да') if value not in ('y/n','y/N') else default

def install_answer(key,prompt,default='',secret=False):
    if key in INSTALL_ANSWERS:return str(INSTALL_ANSWERS[key])
    if NON_INTERACTIVE:return str(default or '')
    return ask(prompt,default,secret)

def load(path):
    if not path.exists():return default_config()
    with path.open(encoding='utf-8') as stream:return json.load(stream)

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
    return validate_project_config(data)

def public_url(value):
    try:parsed=urlparse(value);port=parsed.port
    except ValueError as error:raise SystemExit('Некорректный публичный URL') from error
    host=parsed.hostname or ''
    try:ipaddress.ip_address(host);valid_host=True
    except ValueError:valid_host=bool(re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?',host))
    if parsed.scheme not in ('http','https') or not valid_host or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('','/'):
        raise SystemExit('Публичный URL не должен содержать credentials, path, query или fragment')
    if port is not None and not 1<=port<=65535:raise SystemExit('Недопустимый порт публичного URL')
    return parsed

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
    with urllib.request.urlopen(req,timeout=20) as response:result=json.loads(response.read())
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

def write_env(path,values,overwrite_secrets=False):
    component='site' if path.name=='vpn-shop.env' else 'bot_admin' if path.name=='vpn-bot-admin.env' else 'bot'
    protected=set(ENV_SCHEMAS[component]['secret'])
    if component=='site':protected.update(key for key in values if key=='XUI_TOKEN' or key.endswith('_XUI_TOKEN'))
    merge_env(path,values,component=component,preserve_secrets=protected,overwrite_secrets=overwrite_secrets,mode=0o600)

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

def install_reverse_proxy(public,cert='',key=''):
    parsed=urlparse(public);port=parsed.port or (443 if parsed.scheme=='https' else 80);host=parsed.hostname
    if parsed.scheme=='https' and not (cert and key):raise SystemExit('Для TLS reverse proxy нужны сертификат и ключ')
    run('apt-get','install','-y','nginx')
    tls=''
    if parsed.scheme=='https':
        cert,key=install_tls_copy(cert,key);tls=f'\n    ssl_certificate {cert};\n    ssl_certificate_key {key};\n    ssl_protocols TLSv1.2 TLSv1.3;'
    config=f'''server {{
    listen {port}{' ssl' if parsed.scheme=='https' else ''};
    server_name {host};{tls}
    client_max_body_size 1m;
    location / {{
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }}
}}
'''
    target=pathlib.Path('/etc/nginx/sites-available/home-vpn.conf');target.write_text(config,encoding='utf-8');os.chmod(target,0o644)
    link=pathlib.Path('/etc/nginx/sites-enabled/home-vpn.conf')
    if not link.exists():link.symlink_to(target)
    run('nginx','-t');run('systemctl','enable','--now','nginx');run('systemctl','reload','nginx')

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
    install_component_files('site',ROOT);shutil.copy2(__file__,'/usr/local/sbin/home-vpn-setup');os.chmod('/usr/local/sbin/home-vpn-setup',0o750)
    public=install_answer('public_url','Публичный домен или URL сайта','https://vpn.my.domain.ru');public=public if '://' in public else 'https://'+public;public=public.rstrip('/');parsed=public_url(public);public_port=parsed.port or (443 if parsed.scheme=='https' else 80)
    detected=next(((cert,key) for cert,key in tls_candidates() if pathlib.Path(cert).is_file() and pathlib.Path(key).is_file()),('',''))
    cert=key=''
    if parsed.scheme=='https':
        cert=install_answer('tls_cert','TLS-сертификат для сайта',detected[0]);key=install_answer('tls_key','TLS-ключ для сайта',detected[1]) if cert else ''
        if cert and (not pathlib.Path(cert).is_file() or not pathlib.Path(key).is_file()):raise SystemExit('TLS-сертификат или ключ не найден')
    occupied=listener_on(public_port)
    direct=not occupied and (parsed.scheme!='https' or bool(cert and key))
    if direct:print(f'Порт {public_port} свободен: будет настроен TLS reverse proxy на loopback-приложение.')
    else:
        reason=f'порт {public_port} уже занят: {occupied}' if occupied else 'для HTTPS не указан сертификат и ключ'
        print('Обнаружен внешний вход ('+reason+'). Сайт останется на 127.0.0.1:8080; topology installer проверит Xray fallback.')
    admin_user=install_answer('admin_user','Логин администратора','home-admin');admin_password=install_answer('admin_password','Пароль администратора',secrets.token_urlsafe(16),True)
    bot_mode=os.getenv('HOME_VPN_INSTALL_BOT','ask').lower()
    telegram_token='' if bot_mode in ('0','no','false') else install_answer('telegram_token','Telegram Bot Token (пусто = не устанавливать бота)','',True)
    telegram_admin_id=install_answer('telegram_admin_id','Telegram ID администратора бота','0') if telegram_token else '0'
    if telegram_admin_id and not telegram_admin_id.isdigit():raise SystemExit('Telegram ID администратора должен состоять только из цифр')
    telegram_bot_name=install_answer('telegram_bot_name','Имя Telegram-бота без @','your_bot') if telegram_token else 'your_bot'
    telegram_channel=install_answer('telegram_channel','Telegram-канал, например @my_channel (необязательно)','') if telegram_token else ''
    telegram_proxy_url=install_answer('telegram_proxy_url','Loopback HTTP-прокси health-aware Telegram egress','http://127.0.0.1:18080') if telegram_token else ''
    if telegram_token and urlparse(telegram_proxy_url).hostname not in ('127.0.0.1','::1','localhost'):raise SystemExit('Telegram egress должен слушать loopback; прямой обход запрещён')
    bot_admin_url=install_answer('bot_admin_url','URL отдельной панели управления ботом (необязательно)','') if telegram_token else ''
    bot_panel_user=install_answer('bot_panel_user','Логин отдельной панели бота','bot-admin') if telegram_token else ''
    bot_panel_password=install_answer('bot_panel_password','Пароль отдельной панели бота (пусто = сгенерировать)','',True) if telegram_token else ''
    if telegram_token and not bot_panel_password:bot_panel_password=secrets.token_urlsafe(16)
    bot_panel_entry='/bot-'+secrets.token_urlsafe(8) if telegram_token else ''
    if telegram_token:bot_admin_url=(bot_admin_url.rstrip('/') if bot_admin_url else public)+bot_panel_entry
    smtp_host=install_answer('smtp_host','SMTP-сервер для писем (пусто = почтовая регистрация отключена)','')
    smtp_port=install_answer('smtp_port','SMTP-порт','587') if smtp_host else '587'
    if smtp_host and not smtp_port.isdigit():raise SystemExit('SMTP-порт должен быть числом')
    mail_from=install_answer('mail_from','Адрес отправителя','support@my.domain.ru') if smtp_host else 'support@my.domain.ru'
    smtp_user=install_answer('smtp_user','SMTP-логин',mail_from) if smtp_host else ''
    smtp_password=install_answer('smtp_password','SMTP-пароль приложения','',True) if smtp_host else ''
    smtp_ssl='1' if smtp_host and smtp_port=='465' else '0';smtp_starttls='1' if smtp_host and smtp_port!='465' else '0'
    imap_host=install_answer('imap_host','IMAP-сервер для входящих писем','') if smtp_host else ''
    imap_port=install_answer('imap_port','IMAP-порт','993') if imap_host else '993'
    if imap_host and not imap_port.isdigit():raise SystemExit('IMAP-порт должен быть числом')
    imap_user=install_answer('imap_user','IMAP-логин',smtp_user) if imap_host else ''
    imap_password=install_answer('imap_password','IMAP-пароль приложения (пусто = как SMTP)','',True) if imap_host else ''
    payment=data.setdefault('payment',{});payment_mode=install_answer('payment_mode','Оплата: manual или yookassa',payment.get('mode','manual')).lower()
    if payment_mode not in ('manual','yookassa'):raise SystemExit('Допустимо только manual или yookassa')
    payment['mode']=payment_mode;yookassa_shop_id=install_answer('yookassa_shop_id','ЮKassa Shop ID','') if payment_mode=='yookassa' else '';yookassa_secret=install_answer('yookassa_secret','ЮKassa секретный ключ','',True) if payment_mode=='yookassa' else ''
    if payment_mode=='yookassa' and (not yookassa_shop_id or not yookassa_secret):raise SystemExit('Для автоматической оплаты нужны Shop ID и секретный ключ ЮKassa')
    api_secret=secrets.token_urlsafe(48);admin_entry='/admin-'+secrets.token_urlsafe(8)
    env={'VPN_SHOP_DB':'/var/lib/vpn-shop/shop.db','HOME_VPN_CONFIG':str(config_path),'APP_ENV':'production','PUBLIC_URL':public,'APP_BIND':'127.0.0.1','APP_PORT':'8080','TRUST_PROXY':'1','SESSION_SECRET':secrets.token_urlsafe(48),'API_SECRET':api_secret,'MAIL_SETTINGS_KEY':secrets.token_urlsafe(48),'ADMIN_USER':admin_user,'ADMIN_PASSWORD':admin_password,'ADMIN_ENTRY_PATH':admin_entry,'TELEGRAM_ADMIN_ID':telegram_admin_id or '0','TELEGRAM_BOT_URL':'https://t.me/'+telegram_bot_name.lstrip('@'),'BOT_ADMIN_URL':bot_admin_url,'SMTP_HOST':smtp_host,'SMTP_PORT':smtp_port,'SMTP_USERNAME':smtp_user,'SMTP_PASSWORD':smtp_password,'SMTP_STARTTLS':smtp_starttls,'SMTP_SSL':smtp_ssl,'IMAP_HOST':imap_host,'IMAP_PORT':imap_port,'IMAP_USERNAME':imap_user,'IMAP_PASSWORD':imap_password or smtp_password,'IMAP_SSL':'1','MAIL_FROM':mail_from,'MAIL_REPLY_TO':mail_from,'PAYMENT_URL':'','YOOKASSA_SHOP_ID':yookassa_shop_id,'YOOKASSA_SECRET_KEY':yookassa_secret,'YOOKASSA_API_URL':'https://api.yookassa.ru/v3','YOOKASSA_RETURN_URL':''}
    for node in data.get('nodes') or []:
        token_name=str(node.get('token_env') or ('XUI_TOKEN' if node.get('primary') else str(node.get('id','NODE')).upper().replace('-','_')+'_XUI_TOKEN'))
        node['token_env']=token_name;token=str((INSTALL_ANSWERS.get('node_tokens') or {}).get(str(node.get('id')),'')) if NON_INTERACTIVE else ask(f"API-токен 3x-ui: {node.get('name') or node.get('id')}",'',True);env[token_name]=token
        if token and node.get('panel_url'):
            try:
                routes=discover_node(node,token)
                if routes:
                    node['routes']=routes;node['inbound_ids']=[route['inbound_id'] for route in routes if route.get('enabled',True)]
                    print(f"Получено из {node.get('name')}: inbound {','.join(map(str,node['inbound_ids']))}; маршрутов {len(routes)}")
            except Exception as error:print(f"Предупреждение: не удалось получить данные {node.get('name')}: {error}")
    save(config_path,data)
    write_env(pathlib.Path('/etc/vpn-shop.env'),env);run('chown','-R','vpnshop:vpnshop','/var/lib/vpn-shop');run('chown','-R','root:root','/opt/vpn-shop');run('chmod','755','/opt/vpn-shop');run('chmod','644','/opt/vpn-shop/app.py')
    if direct:install_reverse_proxy(public,cert,key)
    if telegram_token:
        run('useradd','--system','--home','/var/lib/vpn-bot','--shell','/usr/sbin/nologin','vpnshopbot',check=False)
        pathlib.Path('/var/lib/vpn-bot').mkdir(parents=True,exist_ok=True);install_component_files('bot',ROOT);run('visudo','-cf','/etc/sudoers.d/vpn-bot-admin')
        menu_config=pathlib.Path('/etc/vpn-bot-menu.json')
        if not menu_config.exists():shutil.copy2(ROOT/'config/vpn-bot-menu.example.json',menu_config)
        run('chown','root:vpnshopbot',str(menu_config));os.chmod(menu_config,0o640)
        bot_env={'TELEGRAM_BOT_TOKEN':telegram_token,'TELEGRAM_ADMIN_ID':telegram_admin_id or '0','TELEGRAM_CHANNEL':telegram_channel,'TELEGRAM_PROXY_URL':telegram_proxy_url,'SHOP_API_URL':'http://127.0.0.1:8080','PUBLIC_BASE_URL':public,'SHOP_API_SECRET':api_secret,'ADMIN_PANEL_URL':public+admin_entry,'BOT_STATE_FILE':'/var/lib/vpn-bot/last_event_v2','PAYMENT_TIMERS_STATE':'/var/lib/vpn-bot/payment_timers.json','BOT_MENU_CONFIG':str(menu_config),'BOT_RUNTIME_CONFIG':'/var/lib/vpn-bot/runtime.json'}
        write_env(pathlib.Path('/etc/vpn-bot.env'),bot_env);run('chown','-R','vpnshopbot:vpnshopbot','/var/lib/vpn-bot');run('chown','-R','root:root','/opt/vpn-bot');run('chmod','755','/opt/vpn-bot')
        write_env(pathlib.Path('/etc/vpn-bot-admin.env'),{'BOT_PANEL_BIND':'127.0.0.1','BOT_PANEL_PORT':'8090','BOT_PANEL_USER':bot_panel_user,'BOT_PANEL_PASSWORD':bot_panel_password,'BOT_PANEL_SECRET':secrets.token_urlsafe(48),'BOT_PANEL_ENTRY_PATH':bot_panel_entry,'BOT_PANEL_SECURE_COOKIE':'1'})
    run('systemctl','daemon-reload');run('systemctl','enable','--now','vpn-shop');run('systemctl','enable','--now','home-vpn-healthcheck.timer','home-vpn-system-check.timer','home-vpn-site-backup.timer');run('systemctl','start','home-vpn-system-check.service')
    if pathlib.Path('/usr/local/bin/kit').exists():run('systemctl','enable','--now','home-vpn-kit-backup.timer')
    if telegram_token:run('systemctl','enable','--now','vpn-bot','vpn-bot-admin','home-vpn-bot-backup.timer')
    print('\nHOME-VPN установлен: '+public);print('Редактор: sudo home-vpn-setup configure');print('Проверка: sudo home-vpn-setup doctor')
    if telegram_token:print('Панель бота:',bot_admin_url);print('Логин панели бота:',bot_panel_user);print('Пароль панели бота:',bot_panel_password)

def doctor(path):
    data=load(path);errors=validate(data)
    print('Конфигурация:', 'OK' if not errors else '\n- '+'\n- '.join(errors))
    primary=next((x for x in data.get('nodes',[]) if x.get('primary')),None);print('Основная 3x-ui:',primary.get('name') if primary else 'не задана');print('Узлов:',len(data.get('nodes',[])));print('Тарифов устройств:',len((data.get('pricing') or {}).get('device_monthly',{})))
    if pathlib.Path('/etc/systemd/system/vpn-shop.service').exists():run('systemctl','is-active','vpn-shop',check=False)
    if pathlib.Path('/etc/systemd/system/home-vpn-kit-backup.timer').exists():run('systemctl','is-active','home-vpn-kit-backup.timer','home-vpn-healthcheck.timer','home-vpn-system-check.timer',check=False)
    env_path=pathlib.Path('/etc/vpn-shop.env')
    if env_path.exists():
        values=read_env(env_path);configured=bool(values.get('SMTP_HOST'))
        print('Почтовая регистрация:', 'SMTP настроен' if configured else 'отключена — SMTP_HOST не задан')
        mode=str((data.get('payment') or {}).get('mode') or 'manual');yk=bool(values.get('YOOKASSA_SHOP_ID') and values.get('YOOKASSA_SECRET_KEY'))
        print('Оплата:',('ЮKassa настроена' if yk else 'ЮKassa требует Shop ID и ключ') if mode=='yookassa' else 'ручное подтверждение администратора')

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
    if occupied and 'nginx' not in occupied:raise SystemExit(f'Порт {port} занят внешним входом. TLS меняется через topology installer: {occupied}')
    values['PUBLIC_URL']='https://'+domain;values['APP_BIND']='127.0.0.1';values['APP_PORT']='8080';values['TRUST_PROXY']='1';values.pop('TLS_CERT',None);values.pop('TLS_KEY',None);write_env(env_path,values)
    install_reverse_proxy('https://'+domain,cert,key);run('systemctl','restart','vpn-shop');print('Домен и TLS reverse proxy применены: https://'+domain)

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
    save(config_path,data);write_env(env_path,values,overwrite_secrets=True);run('systemctl','restart','vpn-shop');public=values.get('PUBLIC_URL','https://vpn.my.domain.ru').rstrip('/')
    print('Режим оплаты применён:',mode)
    if mode=='yookassa':print('Webhook для кабинета ЮKassa:',public+'/api/payments/yookassa/webhook')

def main():
    global INSTALL_ANSWERS,NON_INTERACTIVE
    parser=argparse.ArgumentParser(description='HOME-VPN installer and configuration editor');parser.add_argument('command',choices=('install','configure','tls','payment','validate','show','doctor'));parser.add_argument('--config',type=pathlib.Path,default=SYSTEM_CONFIG);parser.add_argument('--answers',type=pathlib.Path);args=parser.parse_args()
    if args.answers:
        mode=args.answers.stat().st_mode & 0o777
        if mode & 0o077:raise SystemExit('Answers-файл должен иметь права 0600')
        INSTALL_ANSWERS=json.loads(args.answers.read_text(encoding='utf-8'));NON_INTERACTIVE=True
    if args.command=='install':install(args.config)
    elif args.command=='configure':configure(args.config);run('systemctl','restart','vpn-shop',check=False) if os.geteuid()==0 else None
    elif args.command=='tls':tls_setup()
    elif args.command=='payment':payment_setup(args.config)
    elif args.command=='validate':
        errors=validate(load(args.config));print('OK' if not errors else '\n'.join(errors));raise SystemExit(bool(errors))
    elif args.command=='show':print(json.dumps(load(args.config),ensure_ascii=False,indent=2))
    else:doctor(args.config)
if __name__=='__main__':main()
