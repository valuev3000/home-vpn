#!/usr/bin/env python3
import base64,hashlib,hmac,html,imaplib,io,json,math,mimetypes,os,pathlib,secrets,shutil,smtplib,sqlite3,ssl,subprocess,time,threading,urllib.error,urllib.request,uuid
from decimal import Decimal,InvalidOperation
from email import policy
from email.header import decode_header
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import parseaddr
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import parse_qs,quote,urlparse
DB=os.getenv('VPN_SHOP_DB','/var/lib/vpn-shop/shop.db'); PORT=int(os.getenv('APP_PORT','8080'));BIND=os.getenv('APP_BIND','127.0.0.1');APP_ENV=os.getenv('APP_ENV','production').strip().lower()
TLS_CERT=os.getenv('TLS_CERT','').strip();TLS_KEY=os.getenv('TLS_KEY','').strip()
ASSET_ROOT=pathlib.Path(os.getenv('ASSET_ROOT','/srv/home-vpn/assets')).resolve();DOWNLOAD_ROOT=pathlib.Path(os.getenv('DOWNLOAD_ROOT','/srv/home-vpn/downloads')).resolve()
TRUST_PROXY=os.getenv('TRUST_PROXY','0')=='1'
CONFIG_FILE=os.getenv('HOME_VPN_CONFIG','/etc/home-vpn/config.json')
DEFAULT_CONFIG={'branding':{'name':'HOME-VPN','tagline':'Домашняя свобода','subtitle':'Безопасный интернет каждый день'},'pricing':{'currency':'₽','max_devices':5,'traffic_gb':0,'device_monthly':{'1':100,'2':190,'3':280,'4':370,'5':460},'periods':{'m1':{'name':'1 месяц','days':30,'multiplier':1},'m3':{'name':'3 месяца','days':90,'multiplier':2.8},'m6':{'name':'6 месяцев','days':180,'multiplier':5.6},'y1':{'name':'1 год','days':365,'multiplier':10}},'trial':{'enabled':True,'name':'Пробный · 3 дня','days':3,'price':0,'devices':1}},'payment':{'mode':'manual','provider_name':'Оплата','url':''},'nodes':[{'id':'main','name':'Основная нода','country':'RU','flag':'🌐','primary':True,'enabled':True,'panel_url':'','token_env':'XUI_TOKEN','inbound_ids':[1,2,3,4],'routes':[]}]}
def load_config():
 try:
  with open(CONFIG_FILE,encoding='utf-8') as f:data=json.load(f)
  return data if isinstance(data,dict) else DEFAULT_CONFIG
 except (OSError,json.JSONDecodeError):return DEFAULT_CONFIG
CONFIG=load_config();PRICING=CONFIG.get('pricing') or DEFAULT_CONFIG['pricing'];NODES=[x for x in (CONFIG.get('nodes') or []) if isinstance(x,dict) and x.get('enabled',True)];PRIMARY_NODE=next((x for x in NODES if x.get('primary')),NODES[0] if NODES else DEFAULT_CONFIG['nodes'][0])
PUBLIC=os.getenv('PUBLIC_URL','http://127.0.0.1:8080').rstrip('/'); API_SECRET=os.getenv('API_SECRET','change-me'); SESSION=os.getenv('SESSION_SECRET','change-me').encode(); ADMIN_TG=int(os.getenv('TELEGRAM_ADMIN_ID','0') or 0)
SMTP_HOST=os.getenv('SMTP_HOST','').strip();SMTP_PORT=int(os.getenv('SMTP_PORT','587') or 587);SMTP_USER=os.getenv('SMTP_USERNAME','').strip();SMTP_PASSWORD=os.getenv('SMTP_PASSWORD','');SMTP_STARTTLS=os.getenv('SMTP_STARTTLS','1')=='1';SMTP_SSL=os.getenv('SMTP_SSL','0')=='1';MAIL_FROM=os.getenv('MAIL_FROM','support@my.domain.ru').strip();MAIL_REPLY_TO=os.getenv('MAIL_REPLY_TO',MAIL_FROM).strip()
USER=os.getenv('TEST_USER','test-user'); PASSWORD=os.getenv('TEST_PASSWORD','change-me'); ADMIN=os.getenv('ADMIN_USER','home-admin'); ADMIN_PASSWORD=os.getenv('ADMIN_PASSWORD','change-me'); BOT_URL=os.getenv('TELEGRAM_BOT_URL','https://t.me/your_bot');BOT_ADMIN_URL=os.getenv('BOT_ADMIN_URL','').strip(); XURL=(PRIMARY_NODE.get('panel_url') or os.getenv('XUI_URL','')).rstrip('/'); XTOKEN=os.getenv(str(PRIMARY_NODE.get('token_env') or 'XUI_TOKEN'),os.getenv('XUI_TOKEN','')); XIDS=[int(x) for x in (PRIMARY_NODE.get('inbound_ids') or [int(v) for v in os.getenv('XUI_INBOUND_IDS','1').split(',')])]
BACKUP_PANEL_URL=os.getenv('BACKUP_PANEL_URL','').strip()
BRANDING=CONFIG.get('branding') or DEFAULT_CONFIG['branding'];BRAND_NAME=str(BRANDING.get('name') or 'HOME-VPN');BRAND_TAGLINE=str(BRANDING.get('tagline') or 'Домашняя свобода');BRAND_SUBTITLE=str(BRANDING.get('subtitle') or 'Безопасный интернет каждый день')
PAYMENT=CONFIG.get('payment') or {};PAYMENT_URL=(str(PAYMENT.get('url') or '') or os.getenv('PAYMENT_URL','')).strip();PAYMENT_PROVIDER=str(PAYMENT.get('provider_name') or 'Оплата');PAYMENT_PROVIDER='Оплата' if PAYMENT_PROVIDER=='Оплата по инструкции администратора' else PAYMENT_PROVIDER
YOOKASSA_SHOP_ID=os.getenv('YOOKASSA_SHOP_ID','').strip();YOOKASSA_SECRET_KEY=os.getenv('YOOKASSA_SECRET_KEY','');YOOKASSA_API_URL=os.getenv('YOOKASSA_API_URL','https://api.yookassa.ru/v3').rstrip('/');YOOKASSA_RETURN_URL=os.getenv('YOOKASSA_RETURN_URL','').strip();YOOKASSA_LOCK=threading.Lock()
PRIMARY_NODE_NAME=str(PRIMARY_NODE.get('name') or 'Основная нода');PRIMARY_NODE_FLAG=str(PRIMARY_NODE.get('flag') or '🌐')
ADMIN_ENTRY=os.getenv('ADMIN_ENTRY_PATH','/home-vpn-control').strip() or '/home-vpn-control'
if not ADMIN_ENTRY.startswith('/'):ADMIN_ENTRY='/'+ADMIN_ENTRY
TEST_ENTRY=os.getenv('TEST_ENTRY_PATH','/home-vpn-test').strip() or '/home-vpn-test'
if not TEST_ENTRY.startswith('/'):TEST_ENTRY='/'+TEST_ENTRY
PAYMENT_WINDOW=600
DEVICE_MONTH={int(k):int(v) for k,v in (PRICING.get('device_monthly') or {}).items()};MAX_DEVICES=max(DEVICE_MONTH) if DEVICE_MONTH else 1;TRAFFIC_GB=max(0,int(PRICING.get('traffic_gb') or 0));CURRENCY=str(PRICING.get('currency') or '₽')
PERIODS={k:(str(v.get('name') or k),int(v.get('days') or 30),float(v.get('multiplier') or 1)) for k,v in (PRICING.get('periods') or {}).items()}
trial=PRICING.get('trial') or {};PLANS={'trial':(str(trial.get('name') or 'Пробный'),int(trial.get('days') or 3),int(trial.get('price') or 0),int(trial.get('devices') or 1))} if trial.get('enabled',True) else {}
for period,(name,days,multiplier) in PERIODS.items():
 for devices,monthly in DEVICE_MONTH.items():
  code=period if devices==1 else f'{period}_{devices}';price=int((monthly*multiplier+5)//10*10);PLANS[code]=(name,days,price,devices)
def upgrade_price(current_devices,new_devices,expiry_ms,now_ms=None):
 now_ms=int(time.time()*1000) if now_ms is None else int(now_ms);current=max(1,min(MAX_DEVICES,int(current_devices or 1)));new=max(1,min(MAX_DEVICES,int(new_devices or 1)));remaining=max(0,int(math.ceil((int(expiry_ms or 0)-now_ms)/86400000)))
 return remaining,max(0,int(math.ceil((DEVICE_MONTH[new]-DEVICE_MONTH[current])*remaining/30)))
def traffic_bytes():return TRAFFIC_GB*1024*1024*1024
def traffic_text(limit=None):
 value=TRAFFIC_GB if limit is None else int(limit or 0)
 return 'Без ограничений' if not value else f'{value} ГБ'
CONFIG_LOCK=threading.RLock()
RATE_LOCK=threading.Lock();RATE_BUCKETS={}
def request_allowed(ip,scope,limit,window):
 now=time.monotonic();key=(ip,scope)
 with RATE_LOCK:
  recent=[stamp for stamp in RATE_BUCKETS.get(key,()) if now-stamp<window]
  if len(recent)>=limit:RATE_BUCKETS[key]=recent;return False
  recent.append(now);RATE_BUCKETS[key]=recent
  if len(RATE_BUCKETS)>5000:
   for old_key in list(RATE_BUCKETS)[:1000]:
    if not RATE_BUCKETS[old_key] or now-RATE_BUCKETS[old_key][-1]>=window:RATE_BUCKETS.pop(old_key,None)
  return True
def apply_runtime_config(data):
 global CONFIG,PRICING,NODES,PRIMARY_NODE,DEVICE_MONTH,MAX_DEVICES,TRAFFIC_GB,CURRENCY,PERIODS,PLANS,XURL,XTOKEN,XIDS,PRIMARY_NODE_NAME,PRIMARY_NODE_FLAG,PAYMENT,PAYMENT_URL,PAYMENT_PROVIDER,BRANDING,BRAND_NAME,BRAND_TAGLINE,BRAND_SUBTITLE
 with CONFIG_LOCK:
  pricing=data.get('pricing') or {};devices={int(k):int(v) for k,v in (pricing.get('device_monthly') or {}).items() if str(k).isdigit() and int(k)>0}
  if not devices:raise ValueError('Нужно указать хотя бы одну цену')
  nodes=[x for x in (data.get('nodes') or []) if isinstance(x,dict) and x.get('enabled',True)]
  if not nodes or sum(1 for x in nodes if x.get('primary'))!=1:raise ValueError('Должна быть ровно одна основная нода')
  primary=next(x for x in nodes if x.get('primary'));periods={k:(str(v.get('name') or k),int(v.get('days') or 30),float(v.get('multiplier') or 1)) for k,v in (pricing.get('periods') or {}).items()}
  plans={};trial_cfg=pricing.get('trial') or {}
  if trial_cfg.get('enabled',True):plans['trial']=(str(trial_cfg.get('name') or 'Пробный'),int(trial_cfg.get('days') or 3),int(trial_cfg.get('price') or 0),int(trial_cfg.get('devices') or 1))
  for period,(name,days,multiplier) in periods.items():
   for device,monthly in devices.items():plans[period if device==1 else f'{period}_{device}']=(name,days,int((monthly*multiplier+5)//10*10),device)
  CONFIG=data;PRICING=pricing;NODES=nodes;PRIMARY_NODE=primary;DEVICE_MONTH=devices;MAX_DEVICES=max(devices);TRAFFIC_GB=max(0,int(pricing.get('traffic_gb') or 0));CURRENCY=str(pricing.get('currency') or '₽');PERIODS=periods;PLANS=plans
  XURL=(primary.get('panel_url') or os.getenv('XUI_URL','')).rstrip('/');XTOKEN=os.getenv(str(primary.get('token_env') or 'XUI_TOKEN'),os.getenv('XUI_TOKEN',''));XIDS=[int(x) for x in (primary.get('inbound_ids') or [])];PRIMARY_NODE_NAME=str(primary.get('name') or 'Основная нода');PRIMARY_NODE_FLAG=str(primary.get('flag') or '🌐')
  BRANDING=data.get('branding') or DEFAULT_CONFIG['branding'];BRAND_NAME=str(BRANDING.get('name') or 'HOME-VPN');BRAND_TAGLINE=str(BRANDING.get('tagline') or 'Домашняя свобода');BRAND_SUBTITLE=str(BRANDING.get('subtitle') or 'Безопасный интернет каждый день')
  PAYMENT=data.get('payment') or {};PAYMENT_URL=(str(PAYMENT.get('url') or '') or os.getenv('PAYMENT_URL','')).strip();PAYMENT_PROVIDER=str(PAYMENT.get('provider_name') or 'Оплата');PAYMENT_PROVIDER='Оплата' if PAYMENT_PROVIDER=='Оплата по инструкции администратора' else PAYMENT_PROVIDER
def save_runtime_config(data):
 apply_runtime_config(data);directory=os.path.dirname(CONFIG_FILE);os.makedirs(directory,exist_ok=True);temporary=CONFIG_FILE+'.tmp'
 with open(temporary,'w',encoding='utf-8') as stream:json.dump(data,stream,ensure_ascii=False,indent=2);stream.write('\n')
 os.chmod(temporary,0o660);os.replace(temporary,CONFIG_FILE)
def db(): c=sqlite3.connect(DB);c.row_factory=sqlite3.Row;return c
def init():
 os.makedirs(os.path.dirname(DB),exist_ok=True)
 with db() as c:
  c.executescript("CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,salt TEXT,pwhash TEXT,role TEXT DEFAULT 'user',created_at INTEGER);CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY AUTOINCREMENT,token TEXT UNIQUE,plan TEXT,amount INTEGER,customer TEXT,telegram_id INTEGER,status TEXT DEFAULT 'pending',xui_email TEXT,vpn_uri TEXT,error TEXT,created_at INTEGER,paid_at INTEGER,user_id INTEGER);CREATE TABLE IF NOT EXISTS auth_sessions(nonce TEXT PRIMARY KEY,magic TEXT UNIQUE,username TEXT,telegram_id INTEGER,status TEXT DEFAULT 'pending',expires_at INTEGER,used_at INTEGER);CREATE TABLE IF NOT EXISTS trial_claims(telegram_id INTEGER PRIMARY KEY,order_id INTEGER,claimed_at INTEGER);")
  c.executescript("CREATE TABLE IF NOT EXISTS notification_preferences(telegram_id INTEGER PRIMARY KEY,muted INTEGER DEFAULT 0,updated_at INTEGER);CREATE TABLE IF NOT EXISTS expiry_reminders(email TEXT,expiry_time INTEGER,telegram_id INTEGER,sent_at INTEGER,PRIMARY KEY(email,expiry_time));CREATE TABLE IF NOT EXISTS bot_events(id INTEGER PRIMARY KEY AUTOINCREMENT,event_type TEXT,order_id INTEGER,telegram_id INTEGER,created_at INTEGER);")
  c.execute("CREATE TABLE IF NOT EXISTS expiry_reminder_days(email TEXT NOT NULL,expiry_time INTEGER NOT NULL,days_left INTEGER NOT NULL,telegram_id INTEGER,sent_at INTEGER NOT NULL,PRIMARY KEY(email,expiry_time,days_left))")
  c.execute("CREATE TABLE IF NOT EXISTS site_notification_reads(user_id INTEGER NOT NULL,notification_key TEXT NOT NULL,read_at INTEGER NOT NULL,PRIMARY KEY(user_id,notification_key))")
  c.execute("CREATE TABLE IF NOT EXISTS bot_profiles(telegram_id INTEGER PRIMARY KEY,login TEXT UNIQUE NOT NULL,tg_username TEXT,created_at INTEGER)")
  c.execute("CREATE TABLE IF NOT EXISTS site_trial_claims(user_id INTEGER PRIMARY KEY,order_id INTEGER,claimed_at INTEGER)")
  c.execute("CREATE TABLE IF NOT EXISTS scheduled_changes(order_id INTEGER PRIMARY KEY,email TEXT NOT NULL,limit_ip INTEGER NOT NULL,effective_at INTEGER NOT NULL,applied_at INTEGER)")
  c.execute("CREATE TABLE IF NOT EXISTS support_tickets(id INTEGER PRIMARY KEY AUTOINCREMENT,telegram_id INTEGER NOT NULL,customer TEXT,status TEXT NOT NULL DEFAULT 'open',created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL,closed_at INTEGER)")
  c.execute("CREATE TABLE IF NOT EXISTS support_messages(id INTEGER PRIMARY KEY AUTOINCREMENT,ticket_id INTEGER NOT NULL,sender TEXT NOT NULL,message TEXT NOT NULL,created_at INTEGER NOT NULL)")
  c.execute("CREATE TABLE IF NOT EXISTS maintenance_notices(id INTEGER PRIMARY KEY AUTOINCREMENT,kind TEXT NOT NULL,message TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'active',created_at INTEGER NOT NULL,completed_at INTEGER)")
  c.execute("CREATE TABLE IF NOT EXISTS maintenance_deliveries(notice_id INTEGER NOT NULL,telegram_id INTEGER NOT NULL,delivered_at INTEGER NOT NULL,PRIMARY KEY(notice_id,telegram_id))")
  c.execute("CREATE TABLE IF NOT EXISTS subscription_aliases(client_email TEXT PRIMARY KEY,subscription_url TEXT NOT NULL,source_node TEXT,active INTEGER NOT NULL DEFAULT 1,created_at INTEGER NOT NULL)")
  c.execute("CREATE TABLE IF NOT EXISTS user_recovery_codes(user_id INTEGER NOT NULL,code_hash TEXT NOT NULL,created_at INTEGER NOT NULL,used_at INTEGER,PRIMARY KEY(user_id,code_hash))")
  c.execute("CREATE TABLE IF NOT EXISTS security_events(id INTEGER PRIMARY KEY AUTOINCREMENT,event_type TEXT NOT NULL,username TEXT,ip_address TEXT,details TEXT,created_at INTEGER NOT NULL)")
  c.execute("CREATE TABLE IF NOT EXISTS tariffs(id INTEGER PRIMARY KEY AUTOINCREMENT,code TEXT UNIQUE NOT NULL,name TEXT NOT NULL,days INTEGER NOT NULL,devices INTEGER NOT NULL,traffic_gb INTEGER NOT NULL DEFAULT 0,price INTEGER NOT NULL,active INTEGER NOT NULL DEFAULT 0,archived INTEGER NOT NULL DEFAULT 0,effective_at INTEGER NOT NULL,renewal INTEGER NOT NULL DEFAULT 1,trial INTEGER NOT NULL DEFAULT 0,personal_user_id INTEGER,created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL)")
  c.execute("CREATE TABLE IF NOT EXISTS promo_codes(id INTEGER PRIMARY KEY AUTOINCREMENT,code TEXT UNIQUE NOT NULL,discount_percent INTEGER NOT NULL DEFAULT 0,discount_amount INTEGER NOT NULL DEFAULT 0,active INTEGER NOT NULL DEFAULT 1,starts_at INTEGER,ends_at INTEGER,max_uses INTEGER NOT NULL DEFAULT 0,used_count INTEGER NOT NULL DEFAULT 0,created_at INTEGER NOT NULL)")
  c.execute("CREATE TABLE IF NOT EXISTS device_labels(subscription TEXT NOT NULL,device_id TEXT NOT NULL,label TEXT NOT NULL,updated_at INTEGER NOT NULL,PRIMARY KEY(subscription,device_id))")
  c.execute("CREATE TABLE IF NOT EXISTS admin_actions(id INTEGER PRIMARY KEY AUTOINCREMENT,admin_username TEXT NOT NULL,user_id INTEGER,subscription TEXT,action TEXT NOT NULL,details TEXT,created_at INTEGER NOT NULL)")
  c.execute("CREATE TABLE IF NOT EXISTS subscription_secrets(subscription TEXT PRIMARY KEY,secret TEXT NOT NULL,updated_at INTEGER NOT NULL)")
  c.execute("CREATE TABLE IF NOT EXISTS email_tokens(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,purpose TEXT NOT NULL,token_hash TEXT UNIQUE NOT NULL,expires_at INTEGER NOT NULL,used_at INTEGER,created_at INTEGER NOT NULL)")
  c.execute("CREATE TABLE IF NOT EXISTS mail_settings(id INTEGER PRIMARY KEY CHECK(id=1),smtp_host TEXT,smtp_port INTEGER,smtp_username TEXT,smtp_password_enc TEXT,smtp_starttls INTEGER DEFAULT 1,smtp_ssl INTEGER DEFAULT 0,imap_host TEXT,imap_port INTEGER DEFAULT 993,imap_username TEXT,imap_password_enc TEXT,imap_ssl INTEGER DEFAULT 1,mail_from TEXT,reply_to TEXT,updated_at INTEGER)")
  c.execute("CREATE TABLE IF NOT EXISTS mail_campaigns(id INTEGER PRIMARY KEY AUTOINCREMENT,subject TEXT NOT NULL,body TEXT NOT NULL,audience TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'queued',total INTEGER NOT NULL DEFAULT 0,sent INTEGER NOT NULL DEFAULT 0,failed INTEGER NOT NULL DEFAULT 0,created_by TEXT,created_at INTEGER NOT NULL,finished_at INTEGER)")
  c.execute("CREATE TABLE IF NOT EXISTS mail_campaign_recipients(campaign_id INTEGER NOT NULL,email TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'queued',error TEXT,sent_at INTEGER,PRIMARY KEY(campaign_id,email))")
  c.execute("CREATE TABLE IF NOT EXISTS payment_transactions(id INTEGER PRIMARY KEY AUTOINCREMENT,order_id INTEGER NOT NULL,provider TEXT NOT NULL,provider_payment_id TEXT UNIQUE,idempotence_key TEXT UNIQUE NOT NULL,status TEXT NOT NULL DEFAULT 'creating',amount INTEGER NOT NULL,currency TEXT NOT NULL DEFAULT 'RUB',confirmation_url TEXT,error TEXT,created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL)")
  c.execute("CREATE TABLE IF NOT EXISTS payment_settings(id INTEGER PRIMARY KEY CHECK(id=1),shop_id TEXT,secret_key_enc TEXT,api_url TEXT,return_url TEXT,updated_at INTEGER)")
  c.execute("CREATE INDEX IF NOT EXISTS payment_transactions_order ON payment_transactions(order_id,provider,id DESC)")
  ucols={x[1] for x in c.execute('PRAGMA table_info(users)')};ocols={x[1] for x in c.execute('PRAGMA table_info(orders)')}
  if 'role' not in ucols:c.execute("ALTER TABLE users ADD COLUMN role TEXT DEFAULT 'user'")
  if 'created_at' not in ucols:c.execute("ALTER TABLE users ADD COLUMN created_at INTEGER")
  if 'telegram_id' not in ucols:c.execute("ALTER TABLE users ADD COLUMN telegram_id INTEGER")
  if 'tg_username' not in ucols:c.execute("ALTER TABLE users ADD COLUMN tg_username TEXT")
  if 'email' not in ucols:c.execute("ALTER TABLE users ADD COLUMN email TEXT")
  if 'email_verified_at' not in ucols:c.execute("ALTER TABLE users ADD COLUMN email_verified_at INTEGER")
  if 'email_opt_in' not in ucols:c.execute("ALTER TABLE users ADD COLUMN email_opt_in INTEGER DEFAULT 0")
  if 'totp_secret' not in ucols:c.execute("ALTER TABLE users ADD COLUMN totp_secret TEXT")
  if 'twofa_enabled' not in ucols:c.execute("ALTER TABLE users ADD COLUMN twofa_enabled INTEGER DEFAULT 0")
  if 'session_epoch' not in ucols:c.execute("ALTER TABLE users ADD COLUMN session_epoch INTEGER DEFAULT 0")
  if 'blocked_at' not in ucols:c.execute("ALTER TABLE users ADD COLUMN blocked_at INTEGER")
  if 'user_id' not in ocols:c.execute("ALTER TABLE orders ADD COLUMN user_id INTEGER")
  if 'email' not in ocols:c.execute("ALTER TABLE orders ADD COLUMN email TEXT")
  if 'tg_username' not in ocols:c.execute("ALTER TABLE orders ADD COLUMN tg_username TEXT")
  if 'previous_expiry' not in ocols:c.execute("ALTER TABLE orders ADD COLUMN previous_expiry INTEGER DEFAULT 0")
  if 'previous_limit_ip' not in ocols:c.execute("ALTER TABLE orders ADD COLUMN previous_limit_ip INTEGER DEFAULT 0")
  if 'new_client' not in ocols:c.execute("ALTER TABLE orders ADD COLUMN new_client INTEGER DEFAULT 0")
  if 'reviewed_at' not in ocols:c.execute("ALTER TABLE orders ADD COLUMN reviewed_at INTEGER")
  if 'order_kind' not in ocols:c.execute("ALTER TABLE orders ADD COLUMN order_kind TEXT DEFAULT 'new'")
  if 'target_email' not in ocols:c.execute("ALTER TABLE orders ADD COLUMN target_email TEXT")
  if 'expires_at' not in ocols:c.execute("ALTER TABLE orders ADD COLUMN expires_at INTEGER")
  if 'plan_name' not in ocols:c.execute("ALTER TABLE orders ADD COLUMN plan_name TEXT")
  if 'plan_days' not in ocols:c.execute("ALTER TABLE orders ADD COLUMN plan_days INTEGER")
  if 'plan_devices' not in ocols:c.execute("ALTER TABLE orders ADD COLUMN plan_devices INTEGER")
  if 'plan_traffic_gb' not in ocols:c.execute("ALTER TABLE orders ADD COLUMN plan_traffic_gb INTEGER")
  if 'base_amount' not in ocols:c.execute("ALTER TABLE orders ADD COLUMN base_amount INTEGER")
  if 'discount_amount' not in ocols:c.execute("ALTER TABLE orders ADD COLUMN discount_amount INTEGER DEFAULT 0")
  if 'promo_code' not in ocols:c.execute("ALTER TABLE orders ADD COLUMN promo_code TEXT")
  if 'assigned_node' not in ocols:c.execute("ALTER TABLE orders ADD COLUMN assigned_node TEXT")
  if 'plan_trial' not in ocols:c.execute("ALTER TABLE orders ADD COLUMN plan_trial INTEGER DEFAULT 0")
  c.execute('UPDATE orders SET expires_at=created_at+? WHERE expires_at IS NULL',(PAYMENT_WINDOW,))
  c.execute("UPDATE orders SET expires_at=NULL WHERE status IN ('review','active','payment_pending','payment_error')")
  c.execute("UPDATE orders SET status='review',error='Проверка оплаты восстановлена после перезапуска' WHERE status IN ('approving','rejecting')")
  tcols={x[1] for x in c.execute('PRAGMA table_info(support_tickets)')};ecols={x[1] for x in c.execute('PRAGMA table_info(bot_events)')}
  acols={x[1] for x in c.execute('PRAGMA table_info(auth_sessions)')}
  if 'user_id' not in tcols:c.execute("ALTER TABLE support_tickets ADD COLUMN user_id INTEGER")
  if 'source' not in tcols:c.execute("ALTER TABLE support_tickets ADD COLUMN source TEXT DEFAULT 'telegram'")
  if 'guest_token' not in tcols:c.execute("ALTER TABLE support_tickets ADD COLUMN guest_token TEXT")
  if 'guest_contact' not in tcols:c.execute("ALTER TABLE support_tickets ADD COLUMN guest_contact TEXT")
  if 'ticket_id' not in ecols:c.execute("ALTER TABLE bot_events ADD COLUMN ticket_id INTEGER")
  if 'attempts' not in acols:c.execute("ALTER TABLE auth_sessions ADD COLUMN attempts INTEGER DEFAULT 0")
  now=int(time.time())
  for code,plan in PLANS.items():
   c.execute('INSERT OR IGNORE INTO tariffs(code,name,days,devices,traffic_gb,price,active,archived,effective_at,renewal,trial,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(code,plan[0],plan[1],plan[3],TRAFFIC_GB,plan[2],1,0,now,0 if code=='trial' else 1,1 if code=='trial' else 0,now,now))
  for code,plan in PLANS.items():c.execute('UPDATE orders SET plan_name=COALESCE(plan_name,?),plan_days=COALESCE(plan_days,?),plan_devices=COALESCE(plan_devices,?),plan_traffic_gb=COALESCE(plan_traffic_gb,?),base_amount=COALESCE(base_amount,amount),discount_amount=COALESCE(discount_amount,0) WHERE plan=?',(plan[0],plan[1],plan[3],TRAFFIC_GB,code))
  admin=c.execute("SELECT * FROM users WHERE role='admin' ORDER BY id LIMIT 1").fetchone()
  if not admin:
   if not c.execute('SELECT 1 FROM users WHERE username=?',(ADMIN,)).fetchone():add_user(c,ADMIN,ADMIN_PASSWORD,'admin')
   c.execute("UPDATE users SET role='admin' WHERE username=?",(ADMIN,));admin=c.execute('SELECT * FROM users WHERE username=?',(ADMIN,)).fetchone()
  if ADMIN_TG and not c.execute('SELECT 1 FROM users WHERE telegram_id=? AND id<>?',(ADMIN_TG,admin['id'])).fetchone():c.execute('UPDATE users SET telegram_id=COALESCE(telegram_id,?) WHERE id=?',(ADMIN_TG,admin['id']))
  test_user=c.execute("SELECT * FROM users WHERE role='test' ORDER BY id LIMIT 1").fetchone()
  if not test_user and USER and USER!=admin['username']:
   existing=c.execute('SELECT * FROM users WHERE username=?',(USER,)).fetchone()
   if not existing:add_user(c,USER,PASSWORD,'test')
   elif existing['role']!='admin':c.execute("UPDATE users SET role='test' WHERE id=?",(existing['id'],))
  c.execute('DELETE FROM security_events WHERE created_at<?',(int(time.time())-90*86400,))
  c.execute('DELETE FROM email_tokens WHERE expires_at<? OR used_at IS NOT NULL',(int(time.time())-7*86400,))
  c.execute("CREATE UNIQUE INDEX IF NOT EXISTS users_email_unique ON users(lower(email)) WHERE email IS NOT NULL AND email<>''")
def valid_email_address(value):
 value=str(value or '').strip().lower()
 if len(value)>254 or value.count('@')!=1:return False
 local,domain=value.rsplit('@',1)
 return bool(local and '.' in domain and not domain.startswith('.') and not domain.endswith('.') and all(ch.isalnum() or ch in '.!#$%&\'*+/=?^_`{|}~-' for ch in local) and all(ch.isalnum() or ch in '.-' for ch in domain))
def email_token_hash(token):return hmac.new(SESSION,('email:'+str(token)).encode(),hashlib.sha256).hexdigest()
def create_email_token(c,user_id,purpose,ttl=3600):
 token=secrets.token_urlsafe(32);now=int(time.time());c.execute('DELETE FROM email_tokens WHERE user_id=? AND purpose=? AND used_at IS NULL',(user_id,purpose));c.execute('INSERT INTO email_tokens(user_id,purpose,token_hash,expires_at,created_at) VALUES(?,?,?,?,?)',(user_id,purpose,email_token_hash(token),now+ttl,now));return token
def mail_cipher():
 from cryptography.fernet import Fernet
 return Fernet(base64.urlsafe_b64encode(hashlib.sha256(SESSION+b':mail-settings').digest()))
def encrypt_mail_secret(value):return mail_cipher().encrypt(str(value or '').encode()).decode() if value else ''
def decrypt_mail_secret(value):
 try:return mail_cipher().decrypt(str(value or '').encode()).decode() if value else ''
 except Exception:return ''
def payment_credentials():
 with db() as c:row=c.execute('SELECT * FROM payment_settings WHERE id=1').fetchone()
 if row:return {'shop_id':str(row['shop_id'] or ''),'secret_key':decrypt_mail_secret(row['secret_key_enc']),'api_url':str(row['api_url'] or 'https://api.yookassa.ru/v3').rstrip('/'),'return_url':str(row['return_url'] or '')}
 return {'shop_id':YOOKASSA_SHOP_ID,'secret_key':YOOKASSA_SECRET_KEY,'api_url':YOOKASSA_API_URL,'return_url':YOOKASSA_RETURN_URL}
def mail_settings():
 with db() as c:row=c.execute('SELECT * FROM mail_settings WHERE id=1').fetchone()
 if row:
  data=dict(row);data['smtp_password']=decrypt_mail_secret(data.pop('smtp_password_enc',''));data['imap_password']=decrypt_mail_secret(data.pop('imap_password_enc',''));return data
 return {'smtp_host':SMTP_HOST,'smtp_port':SMTP_PORT,'smtp_username':SMTP_USER,'smtp_password':SMTP_PASSWORD,'smtp_starttls':SMTP_STARTTLS,'smtp_ssl':SMTP_SSL,'imap_host':os.getenv('IMAP_HOST',''),'imap_port':int(os.getenv('IMAP_PORT','993') or 993),'imap_username':os.getenv('IMAP_USERNAME',SMTP_USER),'imap_password':os.getenv('IMAP_PASSWORD',SMTP_PASSWORD),'imap_ssl':os.getenv('IMAP_SSL','1')=='1','mail_from':MAIL_FROM,'reply_to':MAIL_REPLY_TO}
def mail_enabled():return bool(mail_settings().get('smtp_host'))
def send_email(to,subject,text,html_body=None,reply_to=None):
 settings=mail_settings();host=str(settings.get('smtp_host') or '')
 if not host:raise RuntimeError('SMTP не настроен')
 message=EmailMessage();message['Subject']=subject;message['From']=settings.get('mail_from') or MAIL_FROM;message['To']=to
 effective_reply=reply_to or settings.get('reply_to') or MAIL_REPLY_TO
 if effective_reply:message['Reply-To']=effective_reply
 message.set_content(text)
 if html_body:message.add_alternative(html_body,subtype='html')
 port=int(settings.get('smtp_port') or 587);use_ssl=bool(settings.get('smtp_ssl'));client=smtplib.SMTP_SSL(host,port,timeout=20,context=ssl.create_default_context()) if use_ssl else smtplib.SMTP(host,port,timeout=20)
 try:
  if settings.get('smtp_starttls') and not use_ssl:client.starttls(context=ssl.create_default_context())
  if settings.get('smtp_username'):client.login(settings['smtp_username'],settings.get('smtp_password') or '')
  client.send_message(message)
 finally:
  try:client.quit()
  except Exception:client.close()
def decode_mail_header(value):
 parts=[]
 for item,charset in decode_header(value or ''):
  parts.append(item.decode(charset or 'utf-8',errors='replace') if isinstance(item,bytes) else str(item))
 return ''.join(parts)
def imap_connection():
 settings=mail_settings();host=str(settings.get('imap_host') or '')
 if not host:raise RuntimeError('IMAP не настроен')
 client=imaplib.IMAP4_SSL(host,int(settings.get('imap_port') or 993),ssl_context=ssl.create_default_context()) if settings.get('imap_ssl') else imaplib.IMAP4(host,int(settings.get('imap_port') or 143))
 if not settings.get('imap_ssl'):client.starttls(ssl_context=ssl.create_default_context())
 client.login(settings.get('imap_username') or settings.get('smtp_username') or '',settings.get('imap_password') or settings.get('smtp_password') or '')
 return client
def mail_part_text(message):
 if message.is_multipart():
  for part in message.walk():
   if part.get_content_type()=='text/plain' and 'attachment' not in str(part.get('Content-Disposition','')).lower():
    try:return part.get_content()
    except Exception:pass
  return 'Письмо не содержит текстовой версии.'
 try:return message.get_content() if message.get_content_type()=='text/plain' else 'HTML-письмо. Откройте его в обычном почтовом клиенте для просмотра оформления.'
 except Exception:return 'Не удалось прочитать содержимое письма.'
def inbox_messages(limit=30,uid=None):
 client=imap_connection()
 try:
  client.select('INBOX');status,data=client.uid('search',None,'ALL')
  ids=(data[0].split() if status=='OK' and data else []);ids=[str(int(uid)).encode()] if uid else ids[-max(1,min(100,int(limit))):]
  result=[]
  for item in reversed(ids):
   status,payload=client.uid('fetch',item,'(RFC822)')
   if status!='OK' or not payload or not isinstance(payload[0],tuple):continue
   message=BytesParser(policy=policy.default).parsebytes(payload[0][1]);result.append({'uid':item.decode(),'from':decode_mail_header(message.get('From','')),'to':decode_mail_header(message.get('To','')),'subject':decode_mail_header(message.get('Subject','Без темы')),'date':decode_mail_header(message.get('Date','')),'message_id':str(message.get('Message-ID','')),'body':mail_part_text(message)[:20000]})
  return result
 finally:
  try:client.logout()
  except Exception:pass
def deliver_campaign(campaign_id):
 with db() as c:c.execute("UPDATE mail_campaigns SET status='sending' WHERE id=? AND status IN ('queued','sending')",(campaign_id,))
 while True:
  with db() as c:
   campaign=c.execute('SELECT * FROM mail_campaigns WHERE id=?',(campaign_id,)).fetchone();recipient=c.execute("SELECT email FROM mail_campaign_recipients WHERE campaign_id=? AND status='queued' ORDER BY email LIMIT 1",(campaign_id,)).fetchone()
  if not campaign or not recipient:break
  try:send_email(recipient['email'],campaign['subject'],campaign['body']);status='sent';error=None
  except Exception as exc:status='failed';error=str(exc)[:300]
  with db() as c:
   c.execute('UPDATE mail_campaign_recipients SET status=?,error=?,sent_at=? WHERE campaign_id=? AND email=?',(status,error,int(time.time()),campaign_id,recipient['email']));c.execute("UPDATE mail_campaigns SET sent=(SELECT count(*) FROM mail_campaign_recipients WHERE campaign_id=? AND status='sent'),failed=(SELECT count(*) FROM mail_campaign_recipients WHERE campaign_id=? AND status='failed') WHERE id=?",(campaign_id,campaign_id,campaign_id))
  time.sleep(.25)
 with db() as c:c.execute("UPDATE mail_campaigns SET status='completed',finished_at=? WHERE id=?",(int(time.time()),campaign_id))
def resume_mail_campaigns():
 with db() as c:ids=[x[0] for x in c.execute("SELECT id FROM mail_campaigns WHERE status IN ('queued','sending')")]
 for campaign_id in ids:threading.Thread(target=deliver_campaign,args=(campaign_id,),daemon=True).start()
def send_account_email(email,kind,token,username):
 if kind=='verify':
  link=PUBLIC+'/verify-email?token='+quote(token);subject=f'{BRAND_NAME}: подтвердите почту';title='Подтверждение регистрации';action='Подтвердить почту';note='Ссылка действует 24 часа.'
 else:
  link=PUBLIC+'/reset-password?token='+quote(token);subject=f'{BRAND_NAME}: восстановление пароля';title='Восстановление пароля';action='Задать новый пароль';note='Ссылка действует 1 час. Если вы не запрашивали сброс, проигнорируйте письмо.'
 text=f'{title}\n\nЛогин: {username}\n{action}: {link}\n\n{note}\nПоддержка: {MAIL_REPLY_TO}'
 body=f'''<div style="font:16px Arial,sans-serif;max-width:620px;margin:auto;color:#172033"><h1>{html.escape(BRAND_NAME)}</h1><h2>{html.escape(title)}</h2><p>Логин: <b>{html.escape(username)}</b></p><p><a style="display:inline-block;padding:12px 18px;border-radius:8px;background:#409eff;color:white;text-decoration:none;font-weight:bold" href="{html.escape(link,quote=True)}">{html.escape(action)}</a></p><p>{html.escape(note)}</p><hr><p>Поддержка: <a href="mailto:{html.escape(MAIL_REPLY_TO,quote=True)}">{html.escape(MAIL_REPLY_TO)}</a></p></div>'''
 send_email(email,subject,text,body)
def send_login_notice(user,ip_address):
 if not user['email'] or not user['email_verified_at']:return
 try:send_email(user['email'],f'{BRAND_NAME}: выполнен вход',f'В аккаунт {user["username"]} выполнен вход {time.strftime("%d.%m.%Y %H:%M")} (МСК). IP: {ip_address}. Если это были не вы, смените пароль и обратитесь в поддержку: {MAIL_REPLY_TO}')
 except Exception as error:security_event('email_send_failed',user['username'],ip_address,{'kind':'login','error':str(error)[:200]})
def security_event(event_type,username=None,ip_address=None,details=None):
 try:
  payload=json.dumps(details or {},ensure_ascii=False,separators=(',',':'))[:2000]
  with db() as c:c.execute('INSERT INTO security_events(event_type,username,ip_address,details,created_at) VALUES(?,?,?,?,?)',(str(event_type)[:64],str(username or '')[:64] or None,str(ip_address or '')[:64] or None,payload,int(time.time())))
 except Exception:pass
def add_user(c,user,password,role='user',email=None):
 salt=secrets.token_bytes(16);pw=hashlib.scrypt(password.encode(),salt=salt,n=16384,r=8,p=1);c.execute('INSERT INTO users(username,salt,pwhash,role,created_at) VALUES(?,?,?,?,?)',(user,base64.b64encode(salt).decode(),base64.b64encode(pw).decode(),role,int(time.time())))
 if email:c.execute('UPDATE users SET email=? WHERE username=?',(email.lower(),user))
def generated_password(length=12):
 if length<4:raise ValueError('password length must be at least 4')
 chars=[secrets.choice('abcdefghijkmnopqrstuvwxyz'),secrets.choice('ABCDEFGHJKLMNPQRSTUVWXYZ'),secrets.choice('23456789'),secrets.choice('!@#$%*-_')]
 chars.extend(secrets.choice('abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789!@#$%*-_') for _ in range(length-4));secrets.SystemRandom().shuffle(chars)
 return ''.join(chars)
def set_user_password(c,user,password):
 salt=secrets.token_bytes(16);pw=hashlib.scrypt(password.encode(),salt=salt,n=16384,r=8,p=1);c.execute('UPDATE users SET salt=?,pwhash=? WHERE username=?',(base64.b64encode(salt).decode(),base64.b64encode(pw).decode(),user))
def recovery_hash(code):return hmac.new(SESSION,str(code).strip().upper().encode(),hashlib.sha256).hexdigest()
def create_recovery_codes(c,user_id,count=8):
 alphabet='ABCDEFGHJKLMNPQRSTUVWXYZ23456789';codes=['-'.join(''.join(secrets.choice(alphabet) for _ in range(4)) for _ in range(3)) for _ in range(count)];now=int(time.time())
 c.execute('DELETE FROM user_recovery_codes WHERE user_id=?',(user_id,));c.executemany('INSERT INTO user_recovery_codes(user_id,code_hash,created_at) VALUES(?,?,?)',[(user_id,recovery_hash(code),now) for code in codes]);return codes
def consume_recovery_code(c,user_id,code):
 digest=recovery_hash(code);row=c.execute('SELECT used_at FROM user_recovery_codes WHERE user_id=? AND code_hash=?',(user_id,digest)).fetchone()
 if not row or row['used_at']:return False
 return c.execute('UPDATE user_recovery_codes SET used_at=? WHERE user_id=? AND code_hash=? AND used_at IS NULL',(int(time.time()),user_id,digest)).rowcount==1
def password_ok(user,password):
 try:return bool(user) and ('blocked_at' not in user.keys() or not user['blocked_at']) and hmac.compare_digest(hashlib.scrypt(password.encode(),salt=base64.b64decode(user['salt']),n=16384,r=8,p=1),base64.b64decode(user['pwhash']))
 except:return False
def new_totp_secret():return base64.b32encode(secrets.token_bytes(20)).decode().rstrip('=')
def totp_code(secret,at=None):
 counter=int((time.time() if at is None else at)//30);key=base64.b32decode(secret+'='*((8-len(secret)%8)%8));digest=hmac.new(key,counter.to_bytes(8,'big'),hashlib.sha1).digest();offset=digest[-1]&15
 return str((int.from_bytes(digest[offset:offset+4],'big')&0x7fffffff)%1000000).zfill(6)
def verify_totp(secret,code,at=None):
 value=''.join(ch for ch in str(code) if ch.isdigit());now=time.time() if at is None else at
 return len(value)==6 and any(hmac.compare_digest(totp_code(secret,now+step*30),value) for step in (-1,0,1))
def getorder(t):
 with db() as c:return c.execute('SELECT * FROM orders WHERE token=?',(t,)).fetchone()
def order_plan(o):
 keys=o.keys() if hasattr(o,'keys') else o
 if 'plan_name' in keys and o['plan_name'] is not None:return (str(o['plan_name']),int(o['plan_days'] or 0),int(o['amount'] or 0),int(o['plan_devices'] or 1),int(o['plan_traffic_gb'] or 0))
 amount=o['amount'] if 'amount' in keys else 0;legacy=PLANS.get(o['plan'],(o['plan'],0,int(amount or 0),1));return (legacy[0],legacy[1],int(amount or legacy[2]),legacy[3],TRAFFIC_GB)
def tariff_by_code(code,user_id=None,include_inactive=False):
 with db() as c:
  query='SELECT * FROM tariffs WHERE code=?';args=[code]
  if not include_inactive:query+=' AND active=1 AND archived=0 AND effective_at<=? AND (personal_user_id IS NULL OR personal_user_id=?)';args.extend((int(time.time()),user_id))
  return c.execute(query,args).fetchone()
def available_tariffs(user_id=None,renewal=None):
 with db() as c:
  query='SELECT * FROM tariffs WHERE active=1 AND archived=0 AND effective_at<=? AND (personal_user_id IS NULL OR personal_user_id=?)';args=[int(time.time()),user_id]
  if renewal is not None:query+=' AND renewal=?';args.append(1 if renewal else 0)
  return c.execute(query+' ORDER BY trial DESC,days,devices,price',args).fetchall()
def catalog_upgrade_price(current_devices,target_tariff,expiry,user_id=None,now_ms=None):
 now_ms=now_ms or int(time.time()*1000);remaining=max(0,math.ceil((int(expiry or 0)-now_ms)/86400000))
 if remaining<=0:return 0,0
 with db() as c:
  base=c.execute('SELECT price FROM tariffs WHERE devices=? AND days=? AND active=1 AND archived=0 AND effective_at<=? AND (personal_user_id IS NULL OR personal_user_id=?) ORDER BY personal_user_id DESC,updated_at DESC LIMIT 1',(current_devices,int(target_tariff['days']),int(time.time()),user_id)).fetchone()
 if base:
  difference=max(0,int(target_tariff['price'])-int(base['price']));amount=math.ceil(difference*remaining/max(1,int(target_tariff['days'])))
 else:
  _,amount=upgrade_price(current_devices,int(target_tariff['devices']),expiry,now_ms)
 return remaining,max(0,amount)
def apply_promo(code,amount):
 code=str(code or '').strip().upper()
 if not code:return amount,0,None
 now=int(time.time())
 with db() as c:p=c.execute('SELECT * FROM promo_codes WHERE upper(code)=? AND active=1 AND (starts_at IS NULL OR starts_at<=?) AND (ends_at IS NULL OR ends_at>=?) AND (max_uses=0 OR used_count<max_uses)',(code,now,now)).fetchone()
 if not p:return amount,0,None
 discount=max(int(p['discount_amount'] or 0),int(amount)*int(p['discount_percent'] or 0)//100);discount=min(int(amount),discount)
 return int(amount)-discount,discount,p['code']
def record_admin_action(admin,user_id,subscription,action,details=None):
 with db() as c:c.execute('INSERT INTO admin_actions(admin_username,user_id,subscription,action,details,created_at) VALUES(?,?,?,?,?,?)',(admin,user_id,subscription,str(action)[:64],json.dumps(details or {},ensure_ascii=False)[:2000],int(time.time())))
def order_deadline(o):return int(o['expires_at'] or (int(o['created_at'])+PAYMENT_WINDOW))
def expire_order(o):
 if o and o['status'] in ('pending','error') and order_deadline(o)<=int(time.time()):
  with db() as c:c.execute("UPDATE orders SET status='expired',error='Истекло время оплаты' WHERE id=? AND status IN ('pending','error')",(o['id'],))
  return getorder(o['token'])
 return o
def unfinished_order_for(c,user_id,telegram_id,action,target):
 if action not in ('renew','upgrade') or not target:return None
 tgid=int(telegram_id) if telegram_id else None
 return c.execute("SELECT id,token,status FROM orders WHERE ((? IS NOT NULL AND user_id=?) OR (? IS NOT NULL AND telegram_id=?)) AND COALESCE(target_email,xui_email)=? AND status IN ('pending','processing','review','payment_pending','payment_error') ORDER BY id DESC LIMIT 1",(user_id,user_id,tgid,tgid,target)).fetchone()
def payment_destination(token):return PUBLIC+'/pay/'+token+'/start'
def xapi(method,path,body=None):
 req=urllib.request.Request(XURL+path,data=json.dumps(body).encode() if body is not None else None,method=method,headers={'Authorization':'Bearer '+XTOKEN,'Content-Type':'application/json'})
 with urllib.request.urlopen(req,context=ssl._create_unverified_context(),timeout=15) as r:return json.loads(r.read())
def node_xapi(node,method,path,body=None):
 url=str(node.get('panel_url') or '').rstrip('/');token=os.getenv(str(node.get('token_env') or ''),'')
 if not url:raise RuntimeError('У ноды не указан URL API 3x-ui')
 if not token:raise RuntimeError('На сервере сайта не задана переменная '+str(node.get('token_env') or 'с API-токеном'))
 req=urllib.request.Request(url+path,data=json.dumps(body).encode() if body is not None else None,method=method,headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
 with urllib.request.urlopen(req,context=ssl._create_unverified_context(),timeout=15) as response:return json.loads(response.read())
def discover_node_routes(node):
 result=node_xapi(node,'GET','/panel/api/inbounds/list');items=result.get('obj') or []
 if not result.get('success',True):raise RuntimeError(str(result.get('msg') or '3x-ui вернула ошибку'))
 routes=[]
 for inbound in items:
  stream=inbound.get('streamSettings') or {}
  if isinstance(stream,str):
   try:stream=json.loads(stream)
   except:stream={}
  routes.append({'name':str(inbound.get('remark') or f"inbound-{inbound.get('id')}"),'inbound_id':int(inbound.get('id')),'protocol':str(inbound.get('protocol') or ''),'transport':str(stream.get('network') or 'tcp'),'enabled':bool(inbound.get('enable',True))})
 return routes
def panel_clients():
 obj=xapi('GET','/panel/api/clients/list').get('obj') or []
 return [x for x in obj if isinstance(x,dict) and x.get('email')] if isinstance(obj,list) else []
def account_clients(user_id=None,telegram_id=None):
 emails=set()
 with db() as c:
  if user_id:emails.update(r[0] for r in c.execute("SELECT DISTINCT xui_email FROM orders WHERE user_id=? AND xui_email IS NOT NULL AND status IN ('active','review')",(user_id,)))
  if telegram_id:emails.update(r[0] for r in c.execute("SELECT DISTINCT xui_email FROM orders WHERE telegram_id=? AND xui_email IS NOT NULL AND status IN ('active','review')",(int(telegram_id),)))
 return [x for x in panel_clients() if x.get('email') in emails or (telegram_id and int(x.get('tgId') or 0)==int(telegram_id))]
def owned_subscription(user_id=None,telegram_id=None):
 row=None
 with db() as c:
  if user_id:row=c.execute("SELECT xui_email FROM orders WHERE user_id=? AND status='active' AND xui_email IS NOT NULL ORDER BY id DESC LIMIT 1",(user_id,)).fetchone()
  if not row and telegram_id:row=c.execute("SELECT xui_email FROM orders WHERE telegram_id=? AND status='active' AND xui_email IS NOT NULL ORDER BY id DESC LIMIT 1",(int(telegram_id),)).fetchone()
 if row:
  try:
   client=(xapi('GET','/panel/api/clients/get/'+quote(row['xui_email'])).get('obj') or {}).get('client') or {}
   if client:return row['xui_email'],client
  except Exception:pass
 if telegram_id:
  try:
   found=[x for x in panel_clients() if int(x.get('tgId') or 0)==int(telegram_id)]
   if len(found)==1:
    client=(xapi('GET','/panel/api/clients/get/'+quote(found[0]['email'])).get('obj') or {}).get('client') or found[0]
    return found[0]['email'],client
  except Exception:pass
 return None,{}
def client_payload(client):
 allowed=('email','subId','password','auth','flow','security','privateKey','publicKey','allowedIPs','preSharedKey','keepAlive','forwardedPorts','secret','adTag','limitIp','limitHwid','totalGB','expiryTime','enable','tgId','group','comment','reset','resetDay','resetMax','trafficReset','trafficResetDay','reverse')
 out={k:client[k] for k in allowed if k in client}
 if isinstance(out.get('allowedIPs'),str):out['allowedIPs']=[x.strip() for x in out['allowedIPs'].split(',') if x.strip()]
 out['id']=str(client.get('uuid') or client.get('id') or '')
 return out
def scheduled_for(email):
 with db() as c:return c.execute("SELECT * FROM scheduled_changes WHERE email=? AND applied_at IS NULL ORDER BY effective_at,order_id",(email,)).fetchall()
def apply_scheduled_changes():
 now=int(time.time()*1000)
 with db() as c:due=c.execute("SELECT s.* FROM scheduled_changes s JOIN orders o ON o.id=s.order_id WHERE s.applied_at IS NULL AND s.effective_at<=? AND o.status='active' ORDER BY s.effective_at,s.order_id",(now,)).fetchall()
 for change in due:
  try:
   client=(xapi('GET','/panel/api/clients/get/'+quote(change['email'])).get('obj') or {}).get('client') or {}
   if not client:continue
   client['limitIp']=int(change['limit_ip']);r=xapi('POST','/panel/api/clients/update/'+quote(change['email']),client_payload(client))
   if r.get('success'):
    with db() as c:c.execute('UPDATE scheduled_changes SET applied_at=? WHERE order_id=?',(int(time.time()),change['order_id']))
  except Exception:pass
def scheduled_worker():
 while True:
  apply_scheduled_changes();time.sleep(30)
def unique_client_name(base):
 base=''.join(ch for ch in (base or 'vpn-client').lower() if ch in 'abcdefghijklmnopqrstuvwxyz0123456789-').strip('-') or 'vpn-client'
 existing={x.get('email') for x in panel_clients()}
 with db() as c:existing.update(r[0] for r in c.execute("SELECT xui_email FROM orders WHERE xui_email IS NOT NULL AND status IN ('pending','processing','review','payment_pending','payment_error','active')"))
 if base not in existing:return base[:32]
 for n in range(2,1000):
  suffix='-'+str(n);candidate=base[:32-len(suffix)].rstrip('-')+suffix
  if candidate not in existing:return candidate
 raise RuntimeError('Не удалось подобрать имя новой подписки')
def subtoken(email):
 b=base64.urlsafe_b64encode(email.encode()).decode().rstrip('=')
 with db() as c:r=c.execute('SELECT secret FROM subscription_secrets WHERE subscription=?',(email,)).fetchone()
 key=(r['secret'].encode() if r else SESSION);return b+'.'+hmac.new(key,b.encode(),hashlib.sha256).hexdigest()
def subemail(token):
 try:
  b,s=token.rsplit('.',1);email=base64.urlsafe_b64decode(b+'==').decode()
  with db() as c:r=c.execute('SELECT secret FROM subscription_secrets WHERE subscription=?',(email,)).fetchone()
  key=r['secret'].encode() if r else SESSION
  if not hmac.compare_digest(s,hmac.new(key,b.encode(),hashlib.sha256).hexdigest()):return None
  return email
 except:return None
def subscription_url(email):
 with db() as c:r=c.execute('SELECT subscription_url FROM subscription_aliases WHERE client_email=? AND active=1',(email,)).fetchone()
 return r['subscription_url'] if r else PUBLIC+'/client-sub/'+subtoken(email)
PROVISION_LOCK=threading.Lock()
def _provision(o):
 email=None
 with db() as c:
  if o['user_id']:
   u=c.execute('SELECT username FROM users WHERE id=?',(o['user_id'],)).fetchone();email=u['username'] if u and not u['username'].startswith('tg_') else None
  if not email and o['telegram_id']:
   b=c.execute('SELECT login FROM bot_profiles WHERE telegram_id=?',(o['telegram_id'],)).fetchone();email=b['login'] if b else None
 email=unique_client_name(email or ('admin-test' if o['telegram_id'] and int(o['telegram_id'])==ADMIN_TG else f"shop-{o['id']}"));p=order_plan(o);traffic_limit=p[4]*1024*1024*1024
 body={'client':{'email':email,'totalGB':traffic_limit,'expiryTime':int((time.time()+p[1]*86400)*1000),'tgId':o['telegram_id'] or 0,'limitIp':p[3],'enable':True,'comment':f"{BRAND_NAME} · {p[3]} device(s)"},'inboundIds':XIDS}
 r=xapi('POST','/panel/api/clients/add',body)
 if not r.get('success'):raise RuntimeError('Не удалось создать подписку')
 uid=xapi('GET','/panel/api/clients/get/'+quote(email))['obj']['client']['uuid'];links=[x for x in (xapi('GET','/panel/api/inbounds/allLinks').get('obj') or []) if uid in x]
 if len(links)<4:raise RuntimeError(f'получено ссылок: {len(links)}')
 with db() as c:c.execute("UPDATE orders SET status='active',xui_email=?,vpn_uri=?,paid_at=?,expires_at=NULL,error=NULL WHERE id=?",(email,'\n'.join(links),int(time.time()),o['id']))
def provision(o):
 with PROVISION_LOCK:return _provision(o)
def provisional_payment(o):
 p=order_plan(o);target=(o['target_email'] or o['xui_email']) if o['order_kind'] in ('renew','upgrade') else None
 if not target:
  provision(o)
  with db() as c:c.execute("UPDATE orders SET status='review',new_client=1,expires_at=NULL WHERE id=?",(o['id'],))
  return
 obj=xapi('GET','/panel/api/clients/get/'+quote(target)).get('obj') or {};client=obj.get('client') or {}
 if not client:raise RuntimeError('Подписка не найдена')
 now=int(time.time()*1000);prev=int(client.get('expiryTime') or 0);prev_limit=int(client.get('limitIp') or 0);base=max(now,prev);effective_at=base
 if o['order_kind']=='upgrade':
  if prev<=now or p[3]<=prev_limit:raise RuntimeError('Изменение тарифа доступно только для увеличения устройств в действующей подписке')
  client['expiryTime']=prev;client['limitIp']=p[3]
 else:
  client['expiryTime']=base+p[1]*86400000;client['limitIp']=prev_limit
 client['totalGB']=p[4]*1024*1024*1024;client['enable']=True
 r=xapi('POST','/panel/api/clients/update/'+quote(target),client_payload(client))
 if not r.get('success'):raise RuntimeError('Не удалось изменить подписку')
 links=xapi('GET','/panel/api/clients/links/'+quote(target)).get('obj') or []
 with db() as c:
  c.execute("UPDATE orders SET status='review',xui_email=?,vpn_uri=?,previous_expiry=?,previous_limit_ip=?,new_client=0,paid_at=?,expires_at=NULL,error=NULL WHERE id=?",(target,'\n'.join(links),prev,prev_limit,int(time.time()),o['id']))
  if o['order_kind']=='upgrade':c.execute('DELETE FROM scheduled_changes WHERE email=? AND applied_at IS NULL',(target,))
def confirm_payment(o):
 o=expire_order(o)
 if o['status']=='expired':return False,'Время оплаты истекло. Создайте новый заказ.','expired'
 if o['status'] in ('active','review'):return True,'Заказ уже активирован' if o['status']=='active' else 'Оплата уже отправлена на проверку',o['status']
 if o['status']=='rejected':return False,'Оплата этого заказа уже отклонена','rejected'
 if ('plan_trial' in o.keys() and o['plan_trial']) or o['plan']=='trial':
  with db() as c:
   if o['user_id']:
    claim=c.execute('SELECT order_id FROM site_trial_claims WHERE user_id=?',(o['user_id'],)).fetchone()
    if claim and claim['order_id']!=o['id']:return False,'Пробный период уже был использован','rejected'
    if not claim:c.execute('INSERT INTO site_trial_claims(user_id,order_id,claimed_at) VALUES(?,?,?)',(o['user_id'],o['id'],int(time.time())))
   elif o['telegram_id']:
    claim=c.execute('SELECT order_id FROM trial_claims WHERE telegram_id=?',(o['telegram_id'],)).fetchone()
    if claim and claim['order_id']!=o['id']:return False,'Пробный период уже был использован','rejected'
    if not claim:c.execute('INSERT INTO trial_claims(telegram_id,order_id,claimed_at) VALUES(?,?,?)',(o['telegram_id'],o['id'],int(time.time())))
 with db() as c:claimed=c.execute("UPDATE orders SET status='processing' WHERE id=? AND status IN ('pending','error')",(o['id'],)).rowcount
 if not claimed:
  with db() as c:current=c.execute('SELECT status FROM orders WHERE id=?',(o['id'],)).fetchone()
  return False,'Заказ уже обрабатывается',current['status'] if current else 'error'
 try:
  if ('plan_trial' in o.keys() and o['plan_trial']) or o['plan']=='trial':
   provision(o)
   with db() as c:c.execute("INSERT INTO bot_events(event_type,order_id,telegram_id,created_at) VALUES('approved',?,?,?)",(o['id'],o['telegram_id'],int(time.time())))
   return True,'Пробный период активирован','active'
  provisional_payment(o)
  with db() as c:c.execute("INSERT INTO bot_events(event_type,order_id,telegram_id,created_at) VALUES('payment_review',?,?,?)",(o['id'],o['telegram_id'],int(time.time())))
  return True,'Оплата отправлена администратору на проверку','review'
 except Exception as e:
  with db() as c:c.execute("UPDATE orders SET status='error',error=? WHERE id=?",(str(e),o['id']))
  return False,str(e),'error'
def rollback_order(o):
 try:
  with db() as c:remaining=c.execute("SELECT plan FROM orders WHERE id<>? AND xui_email=? AND status IN ('active','review') ORDER BY created_at DESC LIMIT 1",(o['id'],o['xui_email'])).fetchone()
  if o['new_client'] and not remaining:
   r=xapi('POST','/panel/api/clients/del/'+quote(o['xui_email']),{})
   if not r.get('success') and 'not found' not in str(r.get('msg','')).lower():raise RuntimeError(r.get('msg','Ошибка удаления'))
  else:
   obj=xapi('GET','/panel/api/clients/get/'+quote(o['xui_email'])).get('obj') or {};client=obj.get('client') or {}
   if not client:raise RuntimeError('Подписка не найдена — остальные подписки сохранены, требуется восстановление')
   client['expiryTime']=int(o['previous_expiry'] or 0);client['limitIp']=int(o['previous_limit_ip'] or 0)
   r=xapi('POST','/panel/api/clients/update/'+quote(o['xui_email']),client_payload(client))
   if not r.get('success'):raise RuntimeError(r.get('msg','Ошибка отката'))
  with db() as c:c.execute('DELETE FROM scheduled_changes WHERE order_id=?',(o['id'],))
  return True,''
 except Exception as e:return False,str(e)
def cancel_order(order_id,user_id=None,telegram_id=None):
 with db() as c:
  o=c.execute('SELECT * FROM orders WHERE id=?',(order_id,)).fetchone()
  if not o:return False,'Заказ не найден'
  owned=(user_id is not None and o['user_id']==int(user_id)) or (telegram_id is not None and o['telegram_id'] is not None and int(o['telegram_id'])==int(telegram_id))
  if not owned:return False,'Этот заказ принадлежит другому пользователю'
  if o['status']!='pending':return False,'После начала оплаты заказ отменить нельзя'
  previous=o['status'];claimed=c.execute("UPDATE orders SET status='canceling' WHERE id=? AND status=?",(order_id,previous)).rowcount
 if not claimed:return False,'Заказ уже обрабатывается'
 if previous=='review':
  ok,error=rollback_order(o)
  if not ok:
   with db() as c:c.execute("UPDATE orders SET status='review',error=? WHERE id=?",(error,order_id))
   return False,error
 with db() as c:
  c.execute("UPDATE orders SET status='canceled',reviewed_at=?,error='Отменён пользователем' WHERE id=?",(int(time.time()),order_id));c.execute("INSERT INTO bot_events(event_type,order_id,telegram_id,created_at) VALUES('order_canceled',?,?,?)",(order_id,o['telegram_id'],int(time.time())))
 return True,'Заказ отменён. Прежние параметры подписки восстановлены.' if previous=='review' else 'Заказ отменён.'
def review_payment(order_id,approve):
 with db() as c:
  o=c.execute('SELECT * FROM orders WHERE id=?',(order_id,)).fetchone()
  if not o or o['status']!='review':return False,'Заказ уже обработан или не найден'
  transition='approving' if approve else 'rejecting'
  if not c.execute("UPDATE orders SET status=? WHERE id=? AND status='review'",(transition,order_id)).rowcount:return False,'Заказ уже обрабатывается'
 if approve:
  with db() as c:
   c.execute("UPDATE orders SET status='active',reviewed_at=? WHERE id=?",(int(time.time()),order_id));c.execute("INSERT INTO bot_events(event_type,order_id,telegram_id,created_at) VALUES('approved',?,?,?)",(order_id,o['telegram_id'],int(time.time())))
   if 'promo_code' in o.keys() and o['promo_code']:c.execute('UPDATE promo_codes SET used_count=used_count+1 WHERE code=?',(o['promo_code'],))
  return True,'Платёж подтверждён'
 ok,error=rollback_order(o)
 if not ok:
  with db() as c:c.execute("UPDATE orders SET status='review',error=? WHERE id=? AND status='rejecting'",(error,order_id))
  return False,error
 with db() as c:c.execute('DELETE FROM scheduled_changes WHERE order_id=?',(order_id,));c.execute("UPDATE orders SET status='rejected',reviewed_at=?,error='Платёж не подтверждён' WHERE id=?",(int(time.time()),order_id));c.execute("INSERT INTO bot_events(event_type,order_id,telegram_id,created_at) VALUES('rejected',?,?,?)",(order_id,o['telegram_id'],int(time.time())))
 return True,('Платёж отклонён, прежний лимит устройств восстановлен' if o['order_kind']=='upgrade' else 'Платёж отклонён, прежний срок восстановлен')

def payment_mode():
 value=str((CONFIG.get('payment') or {}).get('mode') or os.getenv('PAYMENT_MODE','manual')).strip().lower()
 return value if value in ('manual','yookassa') else 'manual'
def yookassa_ready():
 settings=payment_credentials();return bool(settings['shop_id'] and settings['secret_key'])
def enabled_payment_methods():
 mode=payment_mode();result=[]
 if mode=='manual':result.append({'id':'manual','name':'Ручная оплата','available':True})
 if mode=='yookassa':result.append({'id':'yookassa','name':'ЮKassa','available':yookassa_ready()})
 return result
def payment_options(token):
 result=[]
 for method in enabled_payment_methods():
  item=dict(method);item['url']=PUBLIC+'/pay/'+token+('/yookassa/start' if method['id']=='yookassa' else '/manual/start');result.append(item)
 return result
def yookassa_request(method,path,body=None,idempotence_key=None):
 settings=payment_credentials()
 if not settings['shop_id'] or not settings['secret_key']:raise RuntimeError('ЮKassa не настроена: добавьте Shop ID и секретный ключ')
 headers={'Authorization':'Basic '+base64.b64encode((settings['shop_id']+':'+settings['secret_key']).encode()).decode(),'Accept':'application/json','Content-Type':'application/json'}
 if idempotence_key:headers['Idempotence-Key']=idempotence_key
 request=urllib.request.Request(settings['api_url']+path,data=json.dumps(body,ensure_ascii=False).encode() if body is not None else None,method=method,headers=headers)
 try:
  with urllib.request.urlopen(request,context=ssl.create_default_context(),timeout=20) as response:return json.loads(response.read() or b'{}')
 except urllib.error.HTTPError as error:
  try:details=json.loads(error.read() or b'{}').get('description') or 'HTTP '+str(error.code)
  except Exception:details='HTTP '+str(error.code)
  raise RuntimeError('ЮKassa: '+str(details)[:300])
def yookassa_transaction(order_id):
 with db() as c:return c.execute("SELECT * FROM payment_transactions WHERE order_id=? AND provider='yookassa' ORDER BY id DESC LIMIT 1",(order_id,)).fetchone()
def create_yookassa_payment(o):
 if int(o['amount'] or 0)<=0:raise RuntimeError('Нулевая сумма не отправляется в ЮKassa')
 if o['status'] not in ('pending','error','payment_pending'):raise RuntimeError('Этот заказ уже обработан или отменён')
 with YOOKASSA_LOCK:
  existing=yookassa_transaction(o['id'])
  if existing and existing['confirmation_url'] and existing['status'] not in ('canceled','failed'):return dict(existing)
  now=int(time.time())
  if existing and not existing['confirmation_url'] and existing['status'] in ('creating','failed'):
   key=existing['idempotence_key'];transaction_id=existing['id']
   with db() as c:c.execute("UPDATE payment_transactions SET status='creating',error=NULL,updated_at=? WHERE id=?",(now,transaction_id))
  else:
   key=str(uuid.uuid4())
   with db() as c:
    cur=c.execute("INSERT INTO payment_transactions(order_id,provider,idempotence_key,status,amount,currency,created_at,updated_at) VALUES(?,'yookassa',?,'creating',?,'RUB',?,?)",(o['id'],key,int(o['amount']),now,now));transaction_id=cur.lastrowid
  return_url=payment_credentials()['return_url'] or PUBLIC+'/pay/'+o['token']+'/yookassa/return'
  payload={'amount':{'value':f"{int(o['amount']):.2f}",'currency':'RUB'},'capture':True,'confirmation':{'type':'redirect','return_url':return_url},'description':f'{BRAND_NAME} · заказ №{o["id"]}','metadata':{'order_id':str(o['id'])}}
  try:
   response=yookassa_request('POST','/payments',payload,key);provider_id=str(response.get('id') or '');confirmation=str((response.get('confirmation') or {}).get('confirmation_url') or '');status=str(response.get('status') or 'pending')
   if not provider_id or not confirmation:raise RuntimeError('ЮKassa не вернула ссылку подтверждения')
   with db() as c:
    c.execute('UPDATE payment_transactions SET provider_payment_id=?,status=?,confirmation_url=?,error=NULL,updated_at=? WHERE id=?',(provider_id,status,confirmation,int(time.time()),transaction_id));c.execute("UPDATE orders SET status='payment_pending',expires_at=NULL,error=NULL WHERE id=? AND status IN ('pending','error')",(o['id'],))
   return dict(yookassa_transaction(o['id']))
  except Exception as error:
   with db() as c:c.execute("UPDATE payment_transactions SET status='failed',error=?,updated_at=? WHERE id=?",(str(error)[:500],int(time.time()),transaction_id))
   raise
def activate_automatic_payment(order_id):
 with db() as c:
  o=c.execute('SELECT * FROM orders WHERE id=?',(order_id,)).fetchone()
  if not o:return False,'Заказ не найден'
  if o['status']=='active':return True,'Платёж уже применён'
  if o['status'] not in ('payment_pending','payment_error'):return False,'Заказ не ожидает автоматическую оплату'
  if not c.execute("UPDATE orders SET status='processing',error=NULL WHERE id=? AND status IN ('payment_pending','payment_error')",(order_id,)).rowcount:return False,'Заказ уже обрабатывается'
 try:
  provisional_payment(o)
  with db() as c:
   c.execute("UPDATE orders SET status='active',reviewed_at=?,error=NULL WHERE id=?",(int(time.time()),order_id));c.execute("INSERT INTO bot_events(event_type,order_id,telegram_id,created_at) VALUES('approved',?,?,?)",(order_id,o['telegram_id'],int(time.time())))
   if o['promo_code']:c.execute('UPDATE promo_codes SET used_count=used_count+1 WHERE code=?',(o['promo_code'],))
  return True,'Автоматическая оплата подтверждена'
 except Exception as error:
  with db() as c:c.execute("UPDATE orders SET status='payment_error',error=? WHERE id=?",(str(error)[:500],order_id))
  return False,str(error)
def sync_yookassa_payment(provider_payment_id):
 tx=None
 with db() as c:tx=c.execute("SELECT * FROM payment_transactions WHERE provider='yookassa' AND provider_payment_id=?",(str(provider_payment_id),)).fetchone()
 if not tx:return False,'Платёж не найден'
 payment=yookassa_request('GET','/payments/'+quote(str(provider_payment_id)))
 metadata=payment.get('metadata') or {};amount=payment.get('amount') or {}
 try:amount_ok=Decimal(str(amount.get('value')))==Decimal(int(tx['amount']))
 except (InvalidOperation,TypeError,ValueError):amount_ok=False
 valid=str(payment.get('id'))==str(provider_payment_id) and str(metadata.get('order_id'))==str(tx['order_id']) and amount_ok and amount.get('currency')=='RUB'
 if not valid:return False,'Данные платежа не совпадают с заказом'
 status=str(payment.get('status') or 'unknown');now=int(time.time())
 with db() as c:c.execute('UPDATE payment_transactions SET status=?,error=NULL,updated_at=? WHERE id=?',(status,now,tx['id']))
 if status=='succeeded' and bool(payment.get('paid')):return activate_automatic_payment(tx['order_id'])
 if status=='canceled':
  with db() as c:c.execute("UPDATE orders SET status='canceled',reviewed_at=?,error='Платёж отменён в ЮKassa' WHERE id=? AND status IN ('payment_pending','payment_error')",(now,tx['order_id']))
  return True,'Платёж отменён'
 return True,'Платёж ожидает завершения'
CSS=":root{--b:#07111f;--c:#101d31;--m:#55e6bd;--t:#f3f7fb;--u:#9fb0c6;--l:#24334b;--tg:#2aabee}*{box-sizing:border-box}body{margin:0;font:16px system-ui;background:radial-gradient(circle at 70% 0,#17385a,var(--b) 42%);color:var(--t)}.w{max-width:1080px;margin:auto;padding:40px 22px}.n{display:flex;justify-content:space-between}.brand{font-size:21px;font-weight:800}.a{color:var(--m)}.ok{color:#55e6bd}.bad{color:#ff626f}.hero{padding:70px 0 45px}h1{font-size:clamp(40px,7vw,70px);line-height:1;margin:0 0 20px}p,small{color:var(--u);line-height:1.6}.plans,.guides,.downloads{display:grid;grid-template-columns:repeat(3,1fr);gap:18px}.card{background:linear-gradient(145deg,#14243b,#0c1728);border:1px solid var(--l);border-radius:22px;padding:25px}.guide img{width:100%;height:380px;object-fit:cover;object-position:top;border-radius:14px}.price{font-size:36px;font-weight:800;margin:18px 0}.btn{display:block;width:100%;padding:13px;border:0;border-radius:12px;background:var(--m);color:#062118;font-weight:800;text-align:center;text-decoration:none;cursor:pointer}.tgbtn{display:inline-block;margin-top:20px;padding:13px 20px;border-radius:12px;background:var(--tg);color:white;text-decoration:none;font-weight:800}.soft{background:#1b2a40;color:white}label{display:block;color:var(--u);font-size:13px;margin-top:10px}input{width:100%;padding:13px;border:1px solid var(--l);border-radius:10px;background:#091525;color:white;margin:5px 0 8px}.hint{display:block;margin:2px 0 15px}.box{max-width:680px;margin:55px auto}.qr{background:white;padding:14px;border-radius:18px;width:250px;display:block;margin:22px auto}.uri{white-space:pre-wrap;word-break:break-all;padding:14px;background:#06101d;border-radius:10px;color:var(--m);font-size:12px}.err{color:#ff8d8d}.manual{display:grid;gap:24px}.manual .card{padding:clamp(22px,4vw,42px)}.manual h2{font-size:30px;margin-top:0}.manual h3{margin:28px 0 8px}.manual ol{padding-left:28px}.manual li{padding:7px 0;line-height:1.55}.manual img{width:100%;max-height:720px;object-fit:contain;object-position:top;background:#091525;border-radius:16px;margin:12px 0}.callout{padding:16px 18px;border-left:4px solid var(--m);background:#091525;border-radius:8px;margin:18px 0;color:var(--t)}.warn{border-color:#ffbd59}.osnav{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:25px}.osnav a{padding:12px;text-align:center;border:1px solid var(--l);border-radius:12px;color:var(--t);text-decoration:none}.code{font-family:monospace;color:var(--m);background:#06101d;padding:3px 7px;border-radius:6px;word-break:break-all}.visualsteps{display:grid;grid-template-columns:repeat(2,1fr);gap:18px}.vstep{background:#091525;border:1px solid var(--l);border-radius:18px;overflow:hidden}.vstep h3,.vstep p{padding:0 20px}.shot{height:760px;position:relative;overflow:hidden;background:#eaeaea}.shot img{width:100%;height:100%;object-fit:contain;margin:0;border-radius:0}@media(max-width:760px){.plans,.guides,.downloads{grid-template-columns:1fr}.guide img{height:auto}.osnav{grid-template-columns:1fr 1fr}.manual h2{font-size:25px}.visualsteps{grid-template-columns:1fr}.shot{height:auto}.vstep h3{min-height:0}}"
CSS+=".topnav{display:flex;flex-wrap:wrap;gap:10px;margin:25px 0}.navbtn{display:inline-flex;align-items:center;gap:7px;padding:11px 16px;border-radius:8px;background:#182235;border:1px solid #334158;color:#dce7f8;text-decoration:none;font-weight:650;transition:.2s}.navbtn:hover{background:#253654;border-color:#409eff;color:#fff}.navbtn.primary{background:#409eff;border-color:#409eff;color:#fff}.navbtn.danger{color:#ff7b84}.planhead{display:flex;justify-content:space-between;align-items:center;gap:10px}.device{padding:6px 10px;border-radius:7px;background:#1d2d47;color:#79bbff;font-size:13px;font-weight:700}.unlimited{color:#67c23a;font-weight:700}.card{border-radius:10px;background:#111a2b;border-color:#26354d}.tariffplans .card,.subscriptionplans .card,.manageplans .card{position:relative;border-top:3px solid #409eff}.tariffplans .card:first-child{border-top-color:#67c23a}.tariffplans .btn,.subscriptionplans .btn,.manageplans .btn{background:#409eff;color:#fff;border-radius:7px}.tariffplans .card:first-child .btn{background:#67c23a}.price{color:#e5f1ff}.hero{padding:48px 0 22px}@media(max-width:760px){.topnav{display:grid;grid-template-columns:1fr 1fr}.navbtn{justify-content:center;padding:12px 8px}}"
CSS+=".site-footer{display:flex;flex-wrap:wrap;gap:12px 20px;margin-top:45px;padding:24px 0;border-top:1px solid var(--l);font-size:13px}.site-footer a{color:var(--u);text-decoration:none}.site-footer a:hover{color:var(--t)}.consent{display:flex;align-items:flex-start;gap:10px;margin:14px 0;font-size:14px}.consent input{width:auto;margin-top:3px}.legal{max-width:850px}.legal h2{margin-top:28px}.security-log summary{cursor:pointer;font-weight:800;font-size:20px}"
CSS+=".navbtn.active{background:#409eff;border-color:#79bbff;color:#fff;box-shadow:0 0 0 2px rgba(64,158,255,.18)}.brand a{color:inherit;text-decoration:none}"
CSS+=".telegram-login{background:#2aabee!important;color:#fff!important;margin:12px 0}.divider{display:flex;align-items:center;gap:12px;color:var(--u);margin:18px 0}.divider:before,.divider:after{content:'';height:1px;background:var(--l);flex:1}.authnote{text-align:center;font-size:13px}.plans form{margin-top:20px}"
CSS+="select{width:100%;padding:13px;border:1px solid var(--l);border-radius:8px;background:#091525;color:white;margin:6px 0 16px;font-size:15px}.devselect:focus{outline:2px solid #409eff}.plans{align-items:stretch}"
CSS+="table{width:100%;border-collapse:collapse}th,td{padding:12px 10px;border-bottom:1px solid var(--l);text-align:left}.delbtn{padding:8px 12px;border:1px solid #f56c6c;border-radius:7px;background:#3b1d25;color:#ff9da5;font-weight:700;cursor:pointer}.delbtn:hover{background:#f56c6c;color:white}.notice{padding:13px 16px;border-radius:8px;background:#183d35;color:#75e6bd;margin:0 0 18px}.tablewrap{overflow-x:auto}"
CSS+=".approvebtn{padding:8px 12px;border:1px solid #67c23a;border-radius:7px;background:#173725;color:#8aef75;font-weight:700;cursor:pointer}.review-actions{display:flex;gap:7px;min-width:300px}.review-actions form{margin:0}"
CSS+=".backbtn{margin-top:12px}"
CSS+="textarea{width:100%;min-height:140px;padding:13px;border:1px solid var(--l);border-radius:10px;background:#091525;color:white;margin:6px 0 14px;resize:vertical}.ticketmsg{padding:14px 16px;margin:10px 0;border-radius:10px;background:#091525;border-left:4px solid #409eff}.ticketmsg.admin{border-left-color:#67c23a}.ticketmeta{font-size:12px;color:var(--u);margin-bottom:6px}.ticketactions{display:flex;gap:10px;flex-wrap:wrap}.ticketactions form{flex:1;min-width:180px}"
CSS+=".usergroup{margin:18px 0;border:1px solid var(--l);border-radius:10px;overflow:hidden}.userhead{display:flex;justify-content:space-between;align-items:center;gap:16px;padding:16px;background:#17243a}.userhead h3{margin:0 0 5px}.userhead p{margin:0}.userbody{padding:0 14px 14px}.device-edit{display:flex;align-items:center;gap:8px;min-width:220px}.device-edit select{width:74px;margin:0;padding:8px}.device-edit button{white-space:nowrap}.sub-actions{display:flex;flex-wrap:wrap;gap:8px}.sub-actions form{margin:0}@media(max-width:760px){.userhead{align-items:flex-start;flex-direction:column}.device-edit{min-width:0}}"
CSS+=".payment-warning{padding:18px;margin:16px 0 22px;border:2px solid #f3c846;border-radius:10px;background:#3b3217;color:#fff}.payment-warning h3{margin:0 0 8px;color:#ffd95c}.payment-warning p{margin:6px 0 12px;color:#fff}.order-number{display:block;margin:12px 0;padding:12px;border-radius:8px;background:#ffd84d;color:#211900;text-align:center;font-size:28px;font-weight:900;letter-spacing:.5px}.payment-warning .btn{margin-top:10px;border:1px solid #ffd84d}"
CSS+=".payment-timer{margin:14px 0 18px;padding:15px;border:1px solid #f56c6c;border-radius:10px;background:#351c25;text-align:center;color:#ffd2d5}.payment-timer b{display:block;margin-top:3px;color:#ff8088;font-size:32px;font-variant-numeric:tabular-nums}"
CSS+=".subscription-table{min-width:980px}.subscription-table th{background:#17243a;color:#b9c9dd;position:sticky;top:0}.subscription-table tr:nth-child(even) td{background:#0d1829}.subscription-table td{vertical-align:top}.subscription-table .subname{color:#eaf3ff;font-weight:800;white-space:nowrap}.subscription-table .sub-actions{min-width:210px;display:grid;gap:7px}.subscription-table .sub-actions .btn{margin:0;padding:9px 11px;font-size:13px}.pay-state{display:inline-block;padding:5px 8px;border-radius:6px;background:#1d2d47;color:#c9d8ea;white-space:nowrap}.pay-state.active{background:#173725;color:#8aef75}.pay-state.review{background:#493a16;color:#ffd56a}.pay-state.rejected,.pay-state.expired{background:#3b1d25;color:#ff9da5}"
CSS+=".catalog-table{min-width:760px}.catalog-table th{background:#17243a;color:#b9c9dd}.catalog-table td{vertical-align:middle}.catalog-table tr:nth-child(even) td{background:#0d1829}.catalog-table form{margin:0}.catalog-table select{min-width:190px;margin:0}.catalog-table .btn{min-width:170px;padding:10px 12px}.table-title{font-weight:800;color:#eaf3ff}.table-price{font-size:20px;font-weight:850;color:#79bbff;white-space:nowrap}"
CSS+=".order-actions{display:flex;flex-wrap:wrap;gap:7px;min-width:210px}.order-actions .btn,.order-actions .delbtn,.order-actions .approvebtn{width:auto;min-width:0;margin:0;padding:8px 10px}.order-timer{color:#ffbd59;font-weight:800;font-variant-numeric:tabular-nums;white-space:nowrap}"
CSS+=".operations-table{min-width:650px}.operations-table td{vertical-align:top;padding:7px}.operations-table th{padding:8px 7px;background:#17243a}.operations-table small{font-size:11px;line-height:1.3}.operations-table .review-actions{min-width:175px;max-width:210px;flex-wrap:wrap}.operations-table .review-actions button{padding:6px 8px;font-size:11px}"
CSS+=".tablewrap{overflow:visible}.card-list,.card-list tbody{display:block;width:100%;min-width:0!important}.card-list .card-list-head{display:none}.card-list tr.data-card{display:grid;grid-template-columns:repeat(auto-fit,minmax(145px,1fr));gap:1px;margin:0 0 14px;overflow:hidden;border:1px solid #2d405d;border-left:4px solid #409eff;border-radius:12px;background:#0b1627;box-shadow:0 7px 20px rgba(0,0,0,.16)}.card-list tr.data-card:hover{border-color:#4a6590;transform:translateY(-1px)}.card-list tr.data-card td{display:block;min-width:0;padding:13px!important;border:0;background:transparent!important;overflow-wrap:anywhere}.card-list tr.data-card td:before{content:attr(data-label);display:block;margin-bottom:6px;color:#8296b2;font-size:11px;font-weight:800;line-height:1.2;text-transform:uppercase;letter-spacing:.04em}.card-list tr.data-card td[colspan]:before{display:none}.card-list .sub-actions,.card-list .order-actions,.card-list .review-actions{min-width:0;max-width:none}.card-list .device-edit{min-width:0;flex-wrap:wrap}.card-list .btn,.card-list .approvebtn,.card-list .delbtn{min-height:42px}.usergroup{overflow:visible}.userbody{padding:14px}.card-list tr.data-card:last-child{margin-bottom:0}@media(max-width:760px){.w{padding:18px 12px}.n{align-items:center}.brand{font-size:17px}.hero{padding:30px 0 18px}.hero h1{font-size:34px}.topnav{grid-template-columns:1fr 1fr;gap:7px;margin:16px 0}.navbtn{min-height:46px;padding:9px 7px;font-size:13px;text-align:center}.card{padding:16px;border-radius:12px}.card-list tr.data-card{display:block;margin-bottom:12px}.card-list tr.data-card td{display:grid;grid-template-columns:minmax(105px,38%) minmax(0,62%);align-items:start;gap:10px;padding:10px 12px!important;border-bottom:1px solid #1d2b40}.card-list tr.data-card td:last-child{border-bottom:0}.card-list tr.data-card td:before{margin:2px 0 0}.card-list .sub-actions,.card-list .order-actions,.card-list .review-actions{display:grid;width:100%;gap:7px}.card-list .sub-actions .btn,.card-list .order-actions .btn,.card-list .review-actions button,.card-list form,.card-list form button{width:100%}.userhead{padding:14px}.userbody{padding:8px}.payment-warning{padding:14px}.order-number{font-size:24px}.qr{max-width:100%;height:auto}}@media(max-width:390px){.topnav{grid-template-columns:1fr}.card-list tr.data-card td{display:block}.card-list tr.data-card td:before{margin-bottom:6px}.hero h1{font-size:30px}}"
CSS+="@media(min-width:900px){.subscription-table.card-list tbody{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.subscription-table.card-list tr.data-card{grid-template-columns:repeat(2,minmax(0,1fr));align-content:start;margin:0}.subscription-table.card-list tr.data-card td{padding:9px 11px!important}.subscription-table.card-list tr.data-card td:first-child,.subscription-table.card-list tr.data-card td:last-child{grid-column:1/-1}.subscription-table.card-list tr.data-card td:before{margin-bottom:4px;font-size:10px}.subscription-table.card-list .sub-actions{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:7px}.subscription-table.card-list .sub-actions>*{width:100%;margin:0}.subscription-table.card-list .sub-actions form{grid-column:1/-1}.subscription-table.card-list .btn,.subscription-table.card-list .delbtn{min-height:38px;padding:8px 9px;font-size:12px}}"
CSS+=".user-tools{display:flex;align-items:flex-end;flex-direction:column;gap:6px}.notification-link{display:inline-flex;align-items:center;gap:7px;padding:9px 12px;border:1px solid #334158;border-radius:9px;background:#182235;color:#eaf3ff;text-decoration:none;font-weight:750}.notification-count{display:inline-flex;align-items:center;justify-content:center;min-width:21px;height:21px;padding:0 6px;border-radius:999px;background:#f56c6c;color:#fff;font-size:11px}.login-name{color:#c8d6e8;font-size:16px}.login-name b{font-size:17px;color:#fff}.notice-list{display:grid;gap:12px}.notice-item{position:relative;padding:16px;border:1px solid #3b4f70;border-left:4px solid #f3c846;border-radius:12px;background:#101d31}.notice-item.unread{border-color:#409eff;border-left-color:#409eff;background:#12233a}.notice-item h3{margin:0 0 7px}.notice-item p{margin:4px 0}.unread-label{display:inline-block;margin:0 0 8px;padding:4px 8px;border-radius:999px;background:#409eff;color:#fff;font-size:11px;font-weight:800}.notice-empty{text-align:center;padding:28px}@media(max-width:760px){.n{gap:12px}.user-tools{align-items:flex-end}.notification-link{padding:8px 9px;font-size:12px}.login-name{max-width:230px;text-align:right;overflow-wrap:anywhere}}@media(max-width:390px){.n{align-items:flex-start;flex-direction:column}.user-tools{width:100%;align-items:stretch}.notification-link{justify-content:center}.login-name{text-align:center;max-width:none}}"
CSS+="@media(max-width:520px){.n{align-items:flex-start;flex-direction:column}.user-tools{width:100%;align-items:stretch}.notification-link{justify-content:center}.login-name{text-align:center;max-width:none}}"
CSS+=".adminnav{padding:12px;border:1px solid #334158;border-radius:12px;background:#0c1728}.adminnav .navbtn{font-size:13px;padding:9px 12px}.adminstats{display:grid;grid-template-columns:repeat(6,minmax(120px,1fr));gap:10px;margin:18px 0}.adminstat{padding:16px;border:1px solid #334158;border-radius:10px;background:#101d31}.adminstat b{display:block;font-size:25px;color:#79bbff}.adminstat span{font-size:12px;color:#9fb0c6}.admin-actions{display:flex;flex-wrap:wrap;gap:7px}.admin-actions form{margin:0}.extend-edit{display:flex;gap:7px;align-items:center}.extend-edit input{width:84px;margin:0;padding:8px}.admin-section{scroll-margin-top:15px}@media(max-width:900px){.adminstats{grid-template-columns:repeat(3,1fr)}}@media(max-width:520px){.adminstats{grid-template-columns:repeat(2,1fr)}.adminnav{grid-template-columns:1fr 1fr!important}.extend-edit{flex-wrap:wrap}.extend-edit input{width:100%}}"
CSS+="""body{background:radial-gradient(circle at 85% -10%,rgba(53,116,255,.22),transparent 34%),radial-gradient(circle at 5% 20%,rgba(47,211,170,.12),transparent 30%),#07101d;letter-spacing:-.01em}.w{max-width:1240px;padding:24px 28px 64px}.site-header{position:sticky;top:12px;z-index:50;padding:12px 14px 0;margin:-4px -14px 18px;border:1px solid rgba(119,145,181,.2);border-radius:20px;background:rgba(8,18,32,.86);box-shadow:0 18px 50px rgba(0,0,0,.24);backdrop-filter:blur(18px)}.n{align-items:center;gap:22px}.brand{font-size:20px;letter-spacing:-.03em}.brand a{display:flex;align-items:center;gap:10px}.brand-mark{display:inline-flex;align-items:center;justify-content:center;width:36px;height:36px;border-radius:11px;background:linear-gradient(145deg,#56ecc2,#3a8dff);color:#06151b;font-size:18px;box-shadow:0 8px 25px rgba(74,198,198,.24)}.brand-copy{display:flex;flex-direction:column;line-height:1.05}.brand-copy small{margin-top:4px;color:#8ea1bb;font-size:10px;font-weight:650;letter-spacing:.05em;text-transform:uppercase}.user-tools,.header-actions{display:flex;align-items:center;flex-direction:row;gap:8px;margin-left:auto}.icon-action,.profile-chip,.logout-action{display:inline-flex;align-items:center;justify-content:center;gap:7px;min-height:40px;padding:9px 12px;border:1px solid #2a3b55;border-radius:12px;background:#101d30;color:#eaf2ff;text-decoration:none;font-size:13px;font-weight:750;transition:transform .18s,border-color .18s,background .18s}.icon-action:hover,.profile-chip:hover{transform:translateY(-1px);border-color:#4a77b7;background:#162945}.profile-chip{max-width:220px}.profile-chip span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.logout-action{background:#281921;border-color:#51303c;color:#ffabb1}.logout-action:hover{background:#40202a;border-color:#8a4351}.notification-count{position:relative;min-width:19px;height:19px}.cart-count{background:#55e6bd;color:#052018}.topnav{flex-wrap:nowrap;gap:4px;margin:10px 0 0;padding:7px 0;border-top:1px solid rgba(110,137,173,.16);overflow-x:auto;scrollbar-width:none}.topnav::-webkit-scrollbar{display:none}.navbtn{flex:0 0 auto;padding:9px 11px;border:0;border-radius:10px;background:transparent;color:#9fb1c9;font-size:13px}.navbtn:hover{background:#14243a;border-color:transparent}.navbtn.active{background:#192e49;border:0;box-shadow:none;color:#fff}.hero{position:relative;padding:64px 0 34px}.hero:before{content:'';position:absolute;z-index:-1;inset:20px 48% 0 -30vw;background:radial-gradient(circle,rgba(54,134,255,.12),transparent 64%)}.hero h1{max-width:850px;font-size:clamp(42px,6.5vw,76px);letter-spacing:-.055em}.hero p{max-width:760px;font-size:18px}.eyebrow{display:inline-flex;padding:7px 11px;margin-bottom:18px;border:1px solid rgba(85,230,189,.28);border-radius:999px;background:rgba(85,230,189,.08);color:#7ce8c9;font-size:12px;font-weight:800;letter-spacing:.08em;text-transform:uppercase}.card{border:1px solid rgba(113,142,180,.22);border-radius:18px;background:linear-gradient(145deg,rgba(19,34,55,.96),rgba(10,21,37,.97));box-shadow:0 18px 45px rgba(0,0,0,.14)}.btn{min-height:44px;border-radius:11px;background:linear-gradient(135deg,#59e7be,#3bcba6);box-shadow:0 9px 22px rgba(57,202,164,.14);transition:transform .18s,filter .18s}.btn:hover{transform:translateY(-1px);filter:brightness(1.07)}.btn.soft{box-shadow:none;background:#1a2b42;color:#e7effb}.pricing-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:16px;align-items:stretch}.pricing-card{position:relative;display:flex;flex-direction:column;min-height:390px;padding:24px;overflow:hidden}.pricing-card:after{content:'';position:absolute;width:150px;height:150px;right:-70px;top:-70px;border-radius:50%;background:rgba(64,158,255,.09)}.pricing-card.popular{border-color:#4f91ed;box-shadow:0 22px 55px rgba(30,104,206,.19)}.popular-label{position:absolute;right:16px;top:15px;padding:5px 8px;border-radius:999px;background:#397ee0;color:#fff;font-size:10px;font-weight:850;text-transform:uppercase}.pricing-card h2{margin:4px 0 5px;font-size:22px}.pricing-card .price{margin:18px 0 6px;font-size:38px;letter-spacing:-.04em}.pricing-card .price small{font-size:13px;font-weight:600}.pricing-card ul{padding:0;margin:18px 0 22px;list-style:none;color:#b5c4d8}.pricing-card li{padding:6px 0}.pricing-card li:before{content:'✓';margin-right:9px;color:#55e6bd;font-weight:900}.pricing-card form{margin-top:auto}.pricing-card select{margin:0 0 12px}.trust-strip{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:18px 0 26px}.trust-item{padding:15px 17px;border:1px solid rgba(112,143,182,.16);border-radius:14px;background:rgba(15,29,48,.6);color:#b8c7da}.trust-item b{display:block;margin-bottom:3px;color:#f4f8ff}.cart-layout{display:grid;grid-template-columns:minmax(0,1fr) 330px;gap:20px;align-items:start}.cart-items{display:grid;gap:12px}.cart-item{display:grid;grid-template-columns:1fr auto;gap:18px;align-items:center;padding:20px}.cart-item h3{margin:0 0 7px}.cart-meta{display:flex;flex-wrap:wrap;gap:8px 14px;color:#9fb0c6;font-size:13px}.cart-price{font-size:24px;font-weight:850;color:#eaf3ff}.cart-summary{position:sticky;top:160px}.cart-summary .summary-line{display:flex;justify-content:space-between;gap:16px;padding:11px 0;border-bottom:1px solid #24334b}.cart-summary .summary-total{font-size:24px;font-weight:850}.empty-state{padding:48px 24px;text-align:center}.empty-state .empty-icon{font-size:52px}.status-pill{display:inline-flex;padding:5px 9px;border-radius:999px;background:#1b304b;color:#9ec9ff;font-size:12px;font-weight:800}.desktop-label{display:inline}@media(max-width:980px){.pricing-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.cart-layout{grid-template-columns:1fr}.cart-summary{position:static}.profile-chip{display:none}}@media(max-width:700px){.w{padding:12px 12px 48px}.site-header{top:6px;padding:9px 10px 0;margin:0 0 12px;border-radius:16px}.brand-copy .a,.brand-copy small{display:none}.brand-mark{width:34px;height:34px}.header-actions{gap:5px}.icon-action,.logout-action{min-width:38px;min-height:38px;padding:8px}.desktop-label{display:none}.topnav{margin-top:8px}.navbtn{min-height:38px;padding:8px 10px;font-size:12px}.hero{padding:42px 2px 24px}.hero h1{font-size:42px}.hero p{font-size:16px}.pricing-grid,.trust-strip{grid-template-columns:1fr}.pricing-card{min-height:0}.cart-item{grid-template-columns:1fr}.cart-item .order-actions{width:100%}.cart-item .order-actions>*{flex:1}.card{border-radius:15px}}"""
CSS+="input[type=checkbox]{width:auto;margin-right:8px}.admin-section textarea{font-family:ui-monospace,monospace}"
CSS+=".adminnav{display:flex;flex-wrap:wrap;overflow:visible;gap:7px}.adminnav .navbtn{font-size:16px;padding:12px 15px}@media(max-width:760px){.adminnav{display:flex!important;flex-wrap:nowrap;overflow-x:auto;padding-bottom:10px}.adminnav .navbtn{font-size:15px;padding:11px 13px}}"
CSS+=".recovery-codes{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;padding:0;list-style:none}.recovery-codes li{padding:10px;border:1px solid var(--l);border-radius:9px;background:#091525;text-align:center}.recovery-codes code{color:var(--m);font-size:15px}@media(max-width:520px){.recovery-codes{grid-template-columns:1fr}}"
CSS+=".purchase-wizard{max-width:720px;margin:20px auto}.purchase-wizard .step-number{display:inline-flex;align-items:center;justify-content:center;width:34px;height:34px;margin:15px 8px 5px 0;border-radius:50%;background:#409eff;color:#fff;font-weight:900}.purchase-wizard select{width:100%;padding:13px;border:1px solid var(--l);border-radius:10px;background:#091525;color:#fff}.onboarding ol{padding-left:22px}.onboarding li{margin:10px 0}.user-detail-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.device-list{display:grid;gap:8px}.device-row{display:grid;grid-template-columns:1fr auto;gap:10px;align-items:center;padding:11px;border:1px solid #263b59;border-radius:10px;background:#0a1627}.warning-limit{color:#ffbd59;font-weight:800}.diag-row{display:grid;grid-template-columns:38px 1fr;gap:10px;padding:14px;margin:8px 0;border:1px solid #29415e;border-radius:12px}.diag-row span{font-size:24px}.diag-row.ok{background:#0b2b25;border-color:#17624e}.diag-row.wait{background:#302613;border-color:#7d5c20}details{margin-top:8px}summary{cursor:pointer;color:#90c5ff;font-weight:700}@media(max-width:760px){.user-detail-grid{grid-template-columns:1fr}.device-row{grid-template-columns:1fr}}"
CSS+=".tariff-admin-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px}.tariff-admin-card{display:flex;flex-direction:column;min-height:300px;padding:20px}.tariff-admin-card.archived{opacity:.68}.tariff-card-head{display:flex;align-items:flex-start;justify-content:space-between;gap:12px}.tariff-card-head h2{margin:4px 0}.tariff-code{color:#8296b2;font:12px ui-monospace,monospace}.tariff-admin-price{margin:18px 0 12px;color:#79bbff;font-size:34px;font-weight:900}.tariff-admin-meta{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;margin-bottom:18px}.tariff-admin-meta span{padding:10px;border:1px solid #263b59;border-radius:10px;background:#0a1627;color:#c7d5e8}.tariff-status{display:inline-flex;padding:5px 9px;border-radius:999px;background:#1d2d47;color:#c9d8ea;font-size:11px;font-weight:850;white-space:nowrap}.tariff-status.published{background:#173725;color:#8aef75}.tariff-status.scheduled{background:#493a16;color:#ffd56a}.tariff-status.archived{background:#3b1d25;color:#ff9da5}.tariff-admin-card .admin-actions{margin-top:auto}.tariff-admin-card .admin-actions>*{flex:1}.tariff-admin-card .admin-actions form button,.tariff-admin-card .admin-actions a{width:100%;height:100%}@media(max-width:520px){.tariff-admin-grid{grid-template-columns:1fr}.tariff-admin-meta{grid-template-columns:1fr 1fr}}"
CSS+="#tariffs .pricing-grid{grid-template-columns:repeat(auto-fit,minmax(185px,1fr));gap:10px}.user-tariff-card{min-height:0;padding:15px;border-radius:14px}.user-tariff-card .eyebrow{margin-bottom:8px;padding:5px 8px;font-size:10px}.user-tariff-card h2{margin:2px 0 4px;font-size:19px}.user-tariff-card>p{min-height:38px;margin:4px 0;font-size:12px;line-height:1.35}.user-tariff-card .price{margin:9px 0 5px;font-size:27px}.user-tariff-card ul{margin:7px 0 10px;font-size:12px}.user-tariff-card li{padding:3px 0}.user-tariff-card label{margin-top:6px;font-size:11px}.user-tariff-card select,.user-tariff-card input{margin:3px 0 5px;padding:8px 9px;font-size:12px}.user-tariff-card .btn{min-height:38px;margin-top:5px;padding:8px;font-size:12px}.user-tariff-card.trial-card form{margin-top:auto}@media(max-width:1050px){#tariffs .pricing-grid{grid-template-columns:repeat(3,minmax(0,1fr))}}@media(max-width:700px){#tariffs .pricing-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:480px){#tariffs .pricing-grid{grid-template-columns:1fr}.user-tariff-card>p{min-height:0}}"
def copy_subscription_button(url):
 safe=html.escape(url,quote=True)
 return f'''<button type="button" class="btn" data-url="{safe}" onclick="navigator.clipboard.writeText(this.dataset.url).then(()=>{{this.textContent='✅ Подписка скопирована'}}).catch(()=>{{prompt('Скопируйте ссылку подписки:',this.dataset.url)}})">📋 Скопировать подписку</button>'''
def payment_reference(order_id):return f'№{int(order_id)}'
def payment_timer(deadline):
 return f'''<div class=payment-timer>⏱ До окончания оплаты осталось:<b id=payment-countdown>10:00</b><small>После истечения времени заказ будет закрыт автоматически.</small></div><script>(function(){{var end={int(deadline)*1000},el=document.getElementById('payment-countdown');function tick(){{var s=Math.max(0,Math.ceil((end-Date.now())/1000)),m=Math.floor(s/60),r=s%60;el.textContent=String(m).padStart(2,'0')+':'+String(r).padStart(2,'0');if(!s){{clearInterval(timer);location.reload()}}}}tick();var timer=setInterval(tick,1000)}})()</script>'''
def pending_orders_table(orders,admin=False):
 rows=[]
 for o in orders:
  plan=order_plan(o);kind='Изменение тарифа' if o['order_kind']=='upgrade' else ('Продление' if o['order_kind']=='renew' else 'Новая подписка')
  timer=f"<span class=order-timer data-deadline='{order_deadline(o)*1000}'>--:--</span>" if o['status'] in ('pending','error') else '—'
  actions=[f"<a class='btn soft' href='/pay/{html.escape(o['token'],quote=True)}'>Открыть</a>"]
  if o['status'] in ('pending','error'):actions.insert(0,f"<a class=btn href='/pay/{html.escape(o['token'],quote=True)}'>Оплата</a>")
  if o['status']=='pending':actions.append(cancel_order_button(o))
  if admin and o['status']=='review':actions=[f"<form method=post action=/admin/orders/review><input type=hidden name=order_id value={o['id']}><input type=hidden name=action value=approve><button class=approvebtn>Деньги пришли</button></form>",f"<form method=post action=/admin/orders/review><input type=hidden name=order_id value={o['id']}><input type=hidden name=action value=reject><button class=delbtn>Не поступили</button></form>"]
  elif admin and o['status']=='pending':actions=[f"<a class='btn soft' href='/pay/{html.escape(o['token'],quote=True)}'>Открыть</a>",f"<form method=post action=/admin/orders/cancel onsubmit=\"return confirm('Отменить заказ №{o['id']}?')\"><input type=hidden name=order_id value={o['id']}><button class=delbtn>Отменить</button></form>"]
  customer=f"<td>{html.escape(o['customer'] or '')}</td>" if admin else ''
  rows.append(f"<tr><td class=table-title>№{o['id']}</td>{customer}<td>{html.escape(o['xui_email'] or o['target_email'] or 'создаётся')}</td><td>{kind}<br><small>{plan[0]} · {plan[3]} устр.</small></td><td class=table-price>{o['amount']} ₽</td><td><span class='pay-state {html.escape(o['status'])}'>{html.escape(payment_label(o['status']))}</span></td><td>{timer}</td><td><div class=order-actions>{''.join(actions)}</div></td></tr>")
 colspan=8 if admin else 7;customer_head='<th>Пользователь</th>' if admin else ''
 body=''.join(rows) or f'<tr><td colspan={colspan}>Неподтверждённых заказов нет.</td></tr>'
 script="""<script>(function(){function tick(){var reload=false;document.querySelectorAll('.order-timer').forEach(function(el){var s=Math.max(0,Math.ceil((Number(el.dataset.deadline)-Date.now())/1000));el.textContent=String(Math.floor(s/60)).padStart(2,'0')+':'+String(s%60).padStart(2,'0');if(!s)reload=true});if(reload)setTimeout(function(){location.reload()},1200)}tick();setInterval(tick,1000)})()</script>"""
 return f"<div class=card><div class=tablewrap><table class=catalog-table><tr><th>Заказ</th>{customer_head}<th>Подписка</th><th>Тип и тариф</th><th>Сумма</th><th>Статус</th><th>Осталось</th><th>Действия</th></tr>{body}</table></div></div>{script}"
def copy_payment_reference_button(order_id):
 value=html.escape(payment_reference(order_id),quote=True)
 return f'''<button type="button" class="btn soft" data-value="{value}" onclick="navigator.clipboard.writeText(this.dataset.value).then(()=>{{this.textContent='✅ Номер заказа скопирован'}}).catch(()=>{{prompt('Скопируйте назначение платежа:',this.dataset.value)}})">📋 Скопировать номер заказа</button>'''
def badge(value):
 ok=str(value).lower() in ('online','running','active');return '<b class="ok">Работает</b>' if ok else '<b class="bad">Не работает</b>'
def node_flag(name):
 value=str(name or '').upper()
 if 'RU' in value or 'РОСС' in value:return '🇷🇺'
 if 'PL' in value or 'ПОЛЬ' in value:return '🇵🇱'
 if 'FI' in value or 'FIN' in value or 'ФИН' in value:return '🇫🇮'
 if 'NL' in value or 'NETHER' in value or 'НИДЕР' in value:return '🇳🇱'
 return '🌐'
def payment_status(value):
 if value=='review':return '<div class="callout warn"><b>⏳ Ожидается проверка оплаты</b><p>Подписка временно активирована. Администратор проверит поступление денег.</p></div>'
 if value=='active':return '<div class=notice><b>✅ Оплачено</b><br>Платёж подтверждён администратором.</div>'
 if value=='rejected':return '<div class="callout warn"><b class=bad>❌ Не оплачено</b><p>Платёж не поступил. Временное продление отменено.</p></div>'
 if value=='processing':return '<div class="callout"><b>⏳ Подписка оформляется</b></div>'
 if value=='canceled':return '<div class="callout"><b>Заказ отменён</b><p>Прежние параметры подписки сохранены.</p></div>'
 return '<div class="callout"><b>Ожидается оплата</b></div>'
def payment_label(value):return {'review':'Ожидается проверка','payment_pending':'ЮKassa: ожидается оплата','payment_error':'Оплачено, требуется обработка','active':'Оплачено','rejected':'Не оплачено','processing':'Оформляется','pending':'Ожидается оплата','error':'Ошибка','expired':'Время оплаты истекло','deleted':'Подписка удалена','canceled':'Отменён','canceling':'Отменяется'}.get(value,value)
def cancel_order_button(o):
 return f'''<form method=post action="/pay/{o['token']}/cancel" onsubmit="return confirm('Отменить заказ №{o['id']} до начала оплаты?')"><button class="delbtn">Отменить заказ</button></form>''' if o['status']=='pending' else ''
def date_text(ms):return 'без ограничения' if not ms else time.strftime('%d.%m.%Y',time.localtime(int(ms)/1000))
def scheduled_status(email):
 rows=scheduled_for(email)
 return ''.join(f'<div class="callout warn"><b>Запланировано изменение тарифа</b><br>После окончания текущего периода — {date_text(r["effective_at"])} — будет установлен лимит {r["limit_ip"]} устройств.<br>Ссылка подписки останется прежней.</div>' for r in rows)
def used_devices(email):
 try:
  obj=xapi('POST','/panel/api/clients/ips/'+quote(email),{}).get('obj') or []
  return len(obj) if isinstance(obj,list) else 0
 except Exception:return 0
def format_bytes(value):
 value=max(0,int(value or 0))
 for unit in ('Б','КБ','МБ','ГБ','ТБ'):
  if value<1024 or unit=='ТБ':return f'{value:.0f} {unit}' if unit in ('Б','КБ') else f'{value:.1f} {unit}'
  value/=1024
def client_devices(email):
 items=[]
 try:
  ips=xapi('POST','/panel/api/clients/ips/'+quote(email),{}).get('obj') or []
  for index,item in enumerate(ips,1):
   raw=str(item);stamp=0
   if '(' in raw and raw.endswith(')'):
    tail=raw.rsplit('(',1)[1][:-1]
    if tail.isdigit():stamp=int(tail);raw=raw.rsplit('(',1)[0].strip()
   key='ip-'+hashlib.sha256(raw.encode()).hexdigest()[:16];items.append({'id':key,'source_id':raw,'kind':'ip','name':f'Устройство {index}','address':raw,'last_seen':stamp})
 except Exception:pass
 try:
  result=xapi('POST','/panel/api/clients/hwids/'+quote(email),{}).get('obj') or []
  if isinstance(result,dict):result=result.get('hwids') or result.get('items') or []
  for item in result if isinstance(result,list) else []:
   source=str(item.get('id') or item.get('hwid') or item) if isinstance(item,dict) else str(item);key='hwid-'+hashlib.sha256(source.encode()).hexdigest()[:16]
   items.append({'id':key,'source_id':source,'kind':'hwid','name':'Устройство '+str(len(items)+1),'address':str(item.get('short') or item.get('fingerprint') or source) if isinstance(item,dict) else source,'last_seen':int(item.get('lastSeen') or item.get('updatedAt') or 0) if isinstance(item,dict) else 0})
 except Exception:pass
 with db() as c:labels={x['device_id']:x['label'] for x in c.execute('SELECT device_id,label FROM device_labels WHERE subscription=?',(email,))}
 for item in items:item['name']=labels.get(item['id'],item['name'])
 return items
def client_usage(client):
 traffic=client.get('traffic') or {};used=int(client.get('usedTraffic') or 0) or int(traffic.get('up') or 0)+int(traffic.get('down') or 0);limit=int(client.get('totalGB') or traffic.get('total') or 0)
 return used,limit
def node_metrics(node):
 item={'id':node.get('id'),'name':node.get('name'),'flag':node.get('flag','🌐'),'country':node.get('country',''),'weight':max(1,int(node.get('weight') or 100)),'accept_new':node.get('accept_new',True),'maintenance':node.get('maintenance',False),'online':False,'active_users':0}
 try:
  state=node_xapi(node,'GET','/panel/api/server/status').get('obj') or {};clients=node_xapi(node,'GET','/panel/api/clients/list').get('obj') or [];now=int(time.time()*1000)
  item.update({'online':str((state.get('xray') or {}).get('state',state.get('xrayState',''))).lower() in ('online','running','active','true'),'cpu':round(float(state.get('cpu') or 0),1),'ram_percent':round(100*float((state.get('mem') or {}).get('current') or 0)/max(1,float((state.get('mem') or {}).get('total') or 1)),1),'disk_percent':round(100*float((state.get('disk') or {}).get('current') or 0)/max(1,float((state.get('disk') or {}).get('total') or 1)),1),'network_up':int((state.get('netIO') or {}).get('up') or 0),'network_down':int((state.get('netIO') or {}).get('down') or 0),'traffic_sent':int((state.get('netTraffic') or {}).get('sent') or 0),'traffic_received':int((state.get('netTraffic') or {}).get('recv') or 0),'active_users':sum(1 for x in clients if x.get('enable') and (not int(x.get('expiryTime') or 0) or int(x.get('expiryTime') or 0)>now))})
 except Exception as error:item['error']=str(error)
 return item
def choose_node():
 candidates=[node_metrics(n) for n in NODES if n.get('enabled',True) and n.get('accept_new',True) and not n.get('maintenance',False)]
 candidates=[x for x in candidates if x.get('online')]
 if not candidates:return str(PRIMARY_NODE.get('id') or 'main')
 return min(candidates,key=lambda x:(x.get('cpu',100)+x.get('ram_percent',100)+x.get('active_users',0)*2)/max(1,x.get('weight',100)))['id']
def status_data():
 out={'shop':'online','database':'online','panel':'offline','xray':'unknown','nodes':[],'primary':{'name':PRIMARY_NODE_NAME,'flag':PRIMARY_NODE_FLAG}}
 try:
  s=xapi('GET','/panel/api/server/status').get('obj',{});out['panel']='online';out['xray']=s.get('xray',{}).get('state',s.get('xrayState','unknown'))
 except Exception as e:out['error']=str(e)
 for node in NODES:
  if node is PRIMARY_NODE:continue
  item={'id':node.get('id'),'name':node.get('name','Дополнительный сервер'),'country':node.get('country',''),'flag':node.get('flag','🌐'),'status':'offline','routes':node.get('routes') or []}
  try:
   state=node_xapi(node,'GET','/panel/api/server/status').get('obj',{});xray=state.get('xray',{}).get('state',state.get('xrayState','unknown'));item['status']='online' if str(xray).lower() in ('online','running','active','true') else 'offline'
  except Exception as error:item['error']=str(error)
  out['nodes'].append(item)
 return out
def system_health_notice():
 try:
  with open('/var/lib/vpn-shop/system-health.json',encoding='utf-8') as stream:data=json.load(stream)
  if data.get('ok'):return ''
  warnings=data.get('warnings') or []
  return '<div class="callout warn"><b>Требуется внимание</b><ul>'+''.join('<li>'+html.escape(str(item))+'</li>' for item in warnings)+'</ul></div>'
 except Exception:return '<div class="callout warn"><b>Системная проверка ещё не выполнялась</b></div>'
def active_client(client):
 expiry=int(client.get('expiryTime') or 0);return bool(client.get('enable')) and (expiry==0 or expiry>int(time.time()*1000))
def active_telegram_subscribers():
 try:
  with db() as c:
   states={}
   for r in c.execute('SELECT xui_email,status FROM orders WHERE xui_email IS NOT NULL'):states.setdefault(r['xui_email'],set()).add(r['status'])
  return sorted({int(x.get('tgId') or 0) for x in panel_clients() if int(x.get('tgId') or 0)>0 and active_client(x) and (x.get('email') not in states or 'active' in states[x.get('email')])})
 except Exception:return []
def maintenance_banner(username):
 if not username:return ''
 try:
  with db() as c:
   u=c.execute('SELECT id FROM users WHERE username=?',(username,)).fetchone();notices=c.execute("SELECT * FROM maintenance_notices WHERE status='active' ORDER BY id DESC").fetchall()
  if not u or not notices:return ''
  blocks=''.join(f"<div class='callout warn'><b>{'⚠️ Внеплановые технические работы' if n['kind']=='unplanned' else '📅 Плановые технические работы'}</b><br>{html.escape(n['message']).replace(chr(10),'<br>')}</div>" for n in notices)
  return '<div class=maintenance-banner>'+blocks+'</div>'
 except Exception:return ''
def site_expiry_notifications(username):
 if not username:return []
 try:
  with db() as c:u=c.execute('SELECT id,telegram_id FROM users WHERE username=?',(username,)).fetchone()
  if not u:return []
  now=int(time.time()*1000);until=now+3*86400000;items=[]
  for client in account_clients(u['id'],u['telegram_id']):
   expiry=int(client.get('expiryTime') or 0)
   if client.get('enable') and now<expiry<=until:items.append({'email':client.get('email','Подписка'),'expiryTime':expiry,'days_left':max(1,int(math.ceil((expiry-now)/86400000)))})
  return sorted(items,key=lambda x:x['expiryTime'])
 except Exception:return []
def site_notification_items(username):
 if not username:return None,[]
 try:
  with db() as c:
   u=c.execute('SELECT id FROM users WHERE username=?',(username,)).fetchone()
   if not u:return None,[]
   maintenance=c.execute("SELECT id,kind,message,created_at FROM maintenance_notices WHERE status='active' ORDER BY id DESC").fetchall()
   replies=c.execute("SELECT m.id,m.message,m.created_at,t.id ticket_id FROM support_messages m JOIN support_tickets t ON t.id=m.ticket_id WHERE t.user_id=? AND m.sender='admin' ORDER BY m.id DESC LIMIT 20",(u['id'],)).fetchall()
   read={x[0] for x in c.execute('SELECT notification_key FROM site_notification_reads WHERE user_id=?',(u['id'],))}
  items=[]
  for x in site_expiry_notifications(username):
   key=f"expiry:{x['email']}:{x['expiryTime']}:{x['days_left']}";items.append({'key':key,'kind':'expiry','title':'⏰ Подписка скоро закончится','message':f"Подписка: {x['email']} · осталось {x['days_left']} дн. · действует до {date_text(x['expiryTime'])}",'href':'/subscription/manage?email='+quote(x['email']),'action':'Продлить подписку','created_at':int(time.time()),'unread':key not in read})
  for n in maintenance:
   key=f"maintenance:{n['id']}";items.append({'key':key,'kind':'maintenance','title':'⚠️ Внеплановые технические работы' if n['kind']=='unplanned' else '📅 Плановые технические работы','message':n['message'],'href':'/status','action':'Статус серверов','created_at':n['created_at'],'unread':key not in read})
  for r in replies:
   key=f"support:{r['id']}";items.append({'key':key,'kind':'support','title':f"💬 Ответ поддержки по тикету #{r['ticket_id']}",'message':r['message'],'href':f"/support/ticket?id={r['ticket_id']}",'action':'Открыть ответ','created_at':r['created_at'],'unread':key not in read})
  return u['id'],sorted(items,key=lambda x:(x['unread'],x['created_at']),reverse=True)
 except Exception:return None,[]
def mark_site_notifications_read(user_id,keys):
 if not user_id or not keys:return
 with db() as c:c.executemany('INSERT OR IGNORE INTO site_notification_reads(user_id,notification_key,read_at) VALUES(?,?,?)',[(user_id,key,int(time.time())) for key in keys])
def user_tools(username):
 if not username:return ''
 _,items=site_notification_items(username);count=sum(1 for x in items if x['unread'])
 with db() as c:
  u=c.execute('SELECT id,telegram_id,role FROM users WHERE username=?',(username,)).fetchone()
  cart=c.execute("SELECT count(*) FROM orders WHERE (user_id=? OR (? IS NOT NULL AND telegram_id=?)) AND status IN ('pending','error') AND (expires_at IS NULL OR expires_at>?)",(u['id'],u['telegram_id'],u['telegram_id'],int(time.time()))).fetchone()[0] if u else 0
 notice_badge=f'<span class=notification-count>{count}</span>' if count else ''
 cart_badge=f'<span class="notification-count cart-count">{cart}</span>' if cart else ''
 cart_link='' if u and u['role']=='admin' else f'<a class=icon-action href=/cart aria-label="Корзина">🛒 <span class=desktop-label>Корзина</span>{cart_badge}</a>'
 return f'''<div class="user-tools header-actions"><a class=icon-action href=/notifications aria-label="Уведомления">🔔 <span class=desktop-label>Уведомления</span>{notice_badge}</a>{cart_link}<a class=profile-chip href=/settings/security title="Настройки аккаунта">👤 <span>{html.escape(username)}</span></a><a class=logout-action href=/logout aria-label="Выйти">↪ <span class=desktop-label>Выйти</span></a></div>'''
def telegram_link_warning(username):
 if not username:return ''
 try:
  with db() as c:u=c.execute('SELECT role,telegram_id FROM users WHERE username=?',(username,)).fetchone()
  if not u or u['role']!='user' or u['telegram_id']:return ''
  return '''<div class="account-link-warning callout warn"><b>⚠ Аккаунт пока не привязан к Telegram</b><p>Без привязки вы не получите в Telegram уведомления об оплате, окончании подписки и технических работах.</p><a class="btn telegram-login" href=/link-telegram-start>✈ Привязать Telegram</a></div>'''
 except Exception:return ''
def backup_control(*arguments):
 command=['sudo','-n','/usr/local/sbin/home-vpn-backup-control',*arguments]
 result=subprocess.run(command,capture_output=True,text=True,timeout=20)
 if result.returncode:raise RuntimeError((result.stderr or result.stdout or 'Ошибка управления резервными копиями').strip()[:500])
 return json.loads(result.stdout or '{}')
def page(title,body,admin=False):
 bot_panel_link=f'<a class=navbtn href="{html.escape(BOT_ADMIN_URL,quote=True)}" target=_blank rel=noopener>🤖 Панель бота</a>' if BOT_ADMIN_URL else ''
 admin_nav=f'<div class="topnav adminnav"><a class=navbtn href={html.escape(ADMIN_ENTRY,quote=True)}>📊 Обзор</a><a class=navbtn href=/admin#users>👥 Пользователи</a><a class=navbtn href=/admin/tariffs>💰 Тарифы</a><a class=navbtn href=/admin/payment>💳 Оплата</a><a class=navbtn href=/admin/servers>🖥 Серверы</a><a class=navbtn href=/admin/orders/pending>✅ Проверка оплат</a><a class=navbtn href=/admin/tickets>🎫 Тикеты</a><a class=navbtn href=/admin/guest-tickets>💬 Гостевой чат</a><a class=navbtn href=/admin/mail>✉ Почта</a><a class=navbtn href=/admin/backups>💾 Резервное копирование</a>{bot_panel_link}<a class=navbtn href=/admin/test-user>🧪 Тестовые пользователи</a><a class=navbtn href=/admin/maintenance>🛠 Техработы</a><a class=navbtn href=/admin/config>🧩 Настройки сервиса</a><a class=navbtn href=/settings/security>🔐 Безопасность</a><a class="navbtn soft" href={html.escape(TEST_ENTRY,quote=True)}>👤 Тестовый пользователь</a></div>'
 user_nav='<div class=topnav><a class=navbtn data-tab=tariffs href=/#tariffs>Тарифы</a><a class=navbtn data-tab=account href=/account>Мои подписки</a><a class=navbtn data-tab=instructions href=/instructions>Подключение</a><a class=navbtn data-tab=downloads href=/downloads>Приложения</a><a class=navbtn data-tab=support href=/support>Поддержка</a><a class=navbtn data-tab=status href=/status>Серверы</a><a class=navbtn data-tab=settings href=/settings/security>Безопасность</a></div>'
 nav='' if title in ('Вход','Вход администратора','Вход тестового пользователя','Регистрация','Выбор режима','Проверка 2FA','Условия использования','Конфиденциальность','Оплата и возвраты') else (admin_nav if admin else user_nav)
 script="""<script>(function(){function mark(){document.querySelectorAll('.navbtn').forEach(function(x){x.classList.remove('active')});var p=location.pathname,k=p==='/'?(location.hash==='#tariffs'?'tariffs':'home'):p.slice(1).split('/')[0];var a=document.querySelector('[data-tab="'+k+'"]');if(a)a.classList.add('active')}function cards(){document.querySelectorAll('table').forEach(function(t){t.classList.add('card-list');var rows=Array.from(t.rows),head=rows.find(function(r){return r.querySelector('th')});if(!head)return;head.classList.add('card-list-head');var labels=Array.from(head.cells).map(function(c){return c.textContent.trim()});rows.forEach(function(r){if(r===head)return;r.classList.add('data-card');Array.from(r.cells).forEach(function(c,i){c.setAttribute('data-label',labels[i]||'')})})})}mark();cards();addEventListener('hashchange',mark)})()</script>"""
 footer='''<footer class=site-footer><a href=/terms>Условия использования</a><a href=/privacy>Конфиденциальность</a><a href=/refund-policy>Оплата и возвраты</a><a href=/guest-support>Поддержка</a></footer>'''
 mark=html.escape(BRAND_NAME[:1].upper() or 'V');return f"<!doctype html><html lang=ru><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><meta name=theme-color content='#07101d'><title>{title} · {html.escape(BRAND_NAME)}</title><style>{CSS}</style><body><div class=w><header class=site-header><div class=n><b class=brand><a href=/><span class=brand-mark>{mark}</span><span class=brand-copy>{html.escape(BRAND_NAME)} <span class=a>{html.escape(BRAND_TAGLINE)}</span><small>{html.escape(BRAND_SUBTITLE)}</small></span></a></b><div data-user-tools></div></div>{nav}</header>{body}{footer}</div>{script}</body></html>".encode()
class H(BaseHTTPRequestHandler):
 def setup(self):super().setup();self.connection.settimeout(20)
 def request_ip(self):return str((self.headers.get('X-Real-IP') if TRUST_PROXY else '') or self.client_address[0] or '')[:64]
 def sendx(self,code,data,typ='text/html; charset=utf-8'):
  if isinstance(data,str):data=data.encode()
  if typ.startswith('text/html') and b'<div class=w>' in data:
   username=self.who();data=data.replace(b'<div data-user-tools></div>',user_tools(username).encode(),1);banner=telegram_link_warning(username)+maintenance_banner(username)
   if banner:data=data.replace(b'<div class=w>',b'<div class=w>'+banner.encode(),1)
  self.send_response(code);self.send_header('Content-Type',typ);self.send_header('Content-Length',str(len(data)));self.send_header('X-Content-Type-Options','nosniff');self.send_header('X-Frame-Options','DENY');self.send_header('Referrer-Policy','no-referrer');self.send_header('Permissions-Policy','camera=(), microphone=(), geolocation=()');self.end_headers()
  if self.command!='HEAD':self.wfile.write(data)
 def limited(self,scope='general'):
  limit,window=(15,600) if scope=='auth' else (180,60)
  if request_allowed(self.request_ip(),scope,limit,window):return False
  self.sendx(429,'Слишком много запросов. Повторите попытку позже.','text/plain; charset=utf-8');return True
 def static_file(self,prefix,root,download=False):
  relative=urlparse(self.path).path[len(prefix):]
  try:path=(root/relative).resolve();path.relative_to(root)
  except (ValueError,OSError):return self.sendx(404,'not found','text/plain')
  if not path.is_file():return self.sendx(404,'not found','text/plain')
  typ=mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
  self.send_response(200);self.send_header('Content-Type',typ);self.send_header('Content-Length',str(path.stat().st_size));self.send_header('X-Content-Type-Options','nosniff')
  if download:self.send_header('Content-Disposition',f'attachment; filename="{path.name}"')
  self.end_headers()
  if self.command!='HEAD':
   with path.open('rb') as stream:shutil.copyfileobj(stream,self.wfile,1024*256)
 def jout(self,code,x):self.sendx(code,json.dumps(x,ensure_ascii=False),'application/json')
 def data(self):
  raw=self.rfile.read(int(self.headers.get('Content-Length','0')));return json.loads(raw or b'{}') if 'json' in self.headers.get('Content-Type','') else {k:v[0] for k,v in parse_qs(raw.decode()).items()}
 def cookie(self,name):return next((x.split('=',1)[1] for x in self.headers.get('Cookie','').split('; ') if x.startswith(name+'=')),'')
 def who(self):
  t=self.cookie('vpn_session')
  try:
   b,s=t.rsplit('.',1);parts=base64.urlsafe_b64decode(b+'==').decode().split('|');u,e=parts[0],parts[1];token_epoch=int(parts[2]) if len(parts)>2 else 0
   if int(e)<=time.time() or not hmac.compare_digest(s,hmac.new(SESSION,b.encode(),hashlib.sha256).hexdigest()):return None
   with db() as c:r=c.execute('SELECT session_epoch,blocked_at FROM users WHERE username=?',(u,)).fetchone()
   return u if r and not r['blocked_at'] and int(r['session_epoch'] or 0)==token_epoch else None
  except:return None
 def need(self):
  if self.who():return True
  self.redir('/login');return False
 def isadmin(self):
  u=self.who()
  if not u:return False
  with db() as c:r=c.execute('SELECT role FROM users WHERE username=?',(u,)).fetchone()
  return bool(r and r['role']=='admin')
 def token(self,u):
  with db() as c:r=c.execute('SELECT session_epoch FROM users WHERE username=?',(u,)).fetchone()
  epoch=int(r['session_epoch'] or 0) if r else 0;b=base64.urlsafe_b64encode(f'{u}|{int(time.time()+86400)}|{epoch}'.encode()).decode().rstrip('=');return b+'.'+hmac.new(SESSION,b.encode(),hashlib.sha256).hexdigest()
 def redir(self,url,cookie=None):self.send_response(303);self.send_header('Location',url);cookie and self.send_header('Set-Cookie',cookie);self.end_headers()
 def do_GET(self):
  p=urlparse(self.path).path
  if self.limited('auth' if p in ('/login','/register','/2fa-login','/forgot-password','/reset-password') or p==ADMIN_ENTRY or p==TEST_ENTRY else 'general'):return
  if p.startswith('/assets/'):return self.static_file('/assets/',ASSET_ROOT)
  if p.startswith('/files/'):return self.static_file('/files/',DOWNLOAD_ROOT,True)
  brand=html.escape(BRAND_NAME)
  if p=='/health':return self.jout(200,{'ok':True})
  if p==ADMIN_ENTRY:
   if self.isadmin():return self.redir('/admin')
   action=html.escape(ADMIN_ENTRY,quote=True)
   body=f'''<div class="card box"><h2>Вход администратора</h2><p>Закрытая точка входа в панель управления {brand}.</p><form method=post action="{action}"><label>Логин администратора</label><input name=username autocomplete=username required><label>Пароль</label><input name=password type=password autocomplete=current-password required><button class=btn>Войти в панель управления</button></form><div class=divider>или</div><a class="btn telegram-login" href=/admin-telegram-start>✈ Войти через Telegram администратора</a></div>'''
   return self.sendx(200,page('Вход администратора',body))
  if p==TEST_ENTRY:
   if self.who():
    with db() as c:current=c.execute('SELECT role FROM users WHERE username=?',(self.who(),)).fetchone()
    if current and current['role']=='test':return self.redir('/')
   action=html.escape(TEST_ENTRY,quote=True)
   body=f'''<div class="card box"><h2>Вход тестового пользователя</h2><p>Закрытая точка входа для проверки пользовательской части {brand}.</p><form method=post action="{action}"><label>Логин тестового пользователя</label><input name=username autocomplete=username required><label>Пароль</label><input name=password type=password autocomplete=current-password required><button class=btn>Войти</button></form></div>'''
   return self.sendx(200,page('Вход тестового пользователя',body))
  if p=='/signin':return self.redir('/login','vpn_session=; Path=/; Max-Age=0; Secure; HttpOnly; SameSite=Lax')
  if p=='/2fa-login':return self.sendx(200,page('Проверка 2FA','<div class="card box"><h2>Двухфакторная аутентификация</h2><p>Введите шестизначный код из приложения-аутентификатора.</p><form method=post action=/2fa-login><label>Одноразовый код</label><input name=code inputmode=numeric pattern="[0-9]{6}" maxlength=6 autocomplete=one-time-code required autofocus><button class=btn>Подтвердить вход</button></form><a class="btn soft backbtn" href=/login>← Вернуться ко входу</a></div>'))
  if p=='/login':
   enabled=mail_enabled();email_links='<a class=a href=/register>Зарегистрироваться по почте</a> · <a class=a href=/forgot-password>Забыли пароль?</a>' if enabled else '<a class=a href=/register>Зарегистрироваться</a>';email_note='Почта используется для подтверждения регистрации, восстановления доступа и уведомлений о входе.' if enabled else 'Вход по почте включится после настройки SMTP.'
   return self.sendx(200,page('Вход',f'<div class="card box"><h2>Вход</h2><form method=post action=/login><label>{"Почта или логин" if enabled else "Логин"}</label><input name=username autocomplete=username placeholder="{"name@example.com или familiaio" if enabled else "familiaio"}" required><label>Пароль или резервный код</label><input name=password type=password autocomplete=current-password placeholder="Введите пароль или одноразовый код" required><button class=btn>Войти</button></form><p>{email_links}</p><p><a class=a href=/guest-support>Написать в поддержку без входа</a></p><div class=divider>или</div><a class="btn telegram-login" href=/telegram-start>✈ Войти через Telegram</a><p class=authnote>{email_note}</p><button type=button class="btn soft backbtn" onclick="history.length>1?history.back():location.href=&quot;/&quot;">← Назад</button></div>'))
  if p=='/register':
   enabled=mail_enabled();email_field='<label>Электронная почта</label><input name=email type=email maxlength=254 autocomplete=email placeholder="name@example.com" required>' if enabled else '';email_note='На почту придёт ссылка подтверждения. Без подтверждения вход будет недоступен.' if enabled else 'Почтовая регистрация появится после подключения SMTP. Сейчас доступны логин и Telegram.'
   return self.sendx(200,page('Регистрация',f'<div class="card box"><h2>{"Регистрация по почте" if enabled else "Регистрация"}</h2><form method=post>{email_field}<label>Логин</label><input name=username minlength=8 maxlength=32 pattern="(?=.*[A-Za-z])[A-Za-z0-9][A-Za-z0-9-]{{6,30}}[A-Za-z0-9]" title="8–32 символа: латинские буквы обязательны, цифры и дефис разрешены" placeholder="Например: familiaio" required><label>Пароль</label><input name=password type=password minlength=8 pattern="(?=.*[a-z])(?=.*[A-Z])(?=.*[0-9])(?=.*[^A-Za-z0-9]).{{8,}}" title="Минимум 8 символов: большая и маленькая буквы, цифра и спецсимвол" required><label>Повторите пароль</label><input name=password_confirm type=password minlength=8 required><label class=consent><input type=checkbox name=terms value=1 required> Я принимаю <a class=a href=/terms target=_blank>условия использования</a> и <a class=a href=/privacy target=_blank>политику конфиденциальности</a></label><label class=consent><input type=checkbox name=email_opt_in value=1> Получать новости и специальные предложения по почте</label><button class=btn>Создать аккаунт</button></form><p class=authnote>{email_note}</p><div class=divider>или</div><a class="btn telegram-login" href=/telegram-start>✈ Зарегистрироваться через Telegram</a></div>'))
  if p=='/forgot-password':return self.sendx(200,page('Восстановление пароля','<div class="card box"><h2>Восстановление пароля</h2><p>Введите подтверждённую почту аккаунта.</p><form method=post><label>Электронная почта</label><input name=email type=email autocomplete=email required><button class=btn>Отправить ссылку</button></form><a class="btn soft backbtn" href=/login>← Ко входу</a></div>'))
  if p=='/verify-email':
   token=parse_qs(urlparse(self.path).query).get('token',[''])[0];now=int(time.time())
   with db() as c:
    row=c.execute("SELECT t.id,t.user_id,u.username FROM email_tokens t JOIN users u ON u.id=t.user_id WHERE t.token_hash=? AND t.purpose='verify' AND t.used_at IS NULL AND t.expires_at>?",(email_token_hash(token),now)).fetchone()
    if row:c.execute('UPDATE users SET email_verified_at=? WHERE id=?',(now,row['user_id']));c.execute('UPDATE email_tokens SET used_at=? WHERE id=?',(now,row['id']))
   return self.sendx(200,page('Почта подтверждена','<div class="card box"><h2>✅ Почта подтверждена</h2><p>Теперь можно войти по адресу электронной почты или логину.</p><a class=btn href=/login>Перейти ко входу</a></div>' if row else '<div class="card box"><h2 class=err>Ссылка недействительна или устарела</h2><p>Запросите новую регистрацию либо обратитесь в поддержку.</p><a class=btn href=/register>Регистрация</a></div>'))
  if p=='/reset-password':
   token=parse_qs(urlparse(self.path).query).get('token',[''])[0]
   return self.sendx(200,page('Новый пароль',f'<div class="card box"><h2>Задайте новый пароль</h2><form method=post><input type=hidden name=token value="{html.escape(token,quote=True)}"><label>Новый пароль</label><input name=password type=password minlength=8 required><label>Повторите пароль</label><input name=password_confirm type=password minlength=8 required><button class=btn>Сохранить пароль</button></form></div>'))
  if p=='/terms':return self.sendx(200,page('Условия использования',f'''<section class=hero><h1>Условия использования</h1><p>Краткие правила сервиса {brand}.</p></section><div class="card legal"><h2>Доступ к сервису</h2><p>Подписка предоставляет доступ на оплаченный срок и для указанного количества устройств. Пользователь отвечает за сохранность своей ссылки и данных входа.</p><h2>Допустимое использование</h2><p>Запрещены рассылка спама, атаки, мошенничество, распространение вредоносных материалов и иные действия, нарушающие применимое законодательство или работу инфраструктуры.</p><h2>Оплата</h2><p>Заказ активируется временно после нажатия кнопки оплаты и становится окончательно подтверждённым после проверки поступления средств администратором.</p><h2>Доступность</h2><p>Плановые и внеплановые работы публикуются на сайте и в Telegram. При длительной неисправности вопрос продления рассматривается через поддержку.</p><h2>Поддержка</h2><p>Вопросы по оплате, доступу и ошибочным суммам направляйте через раздел поддержки.</p></div>'''))
  if p=='/privacy':return self.sendx(200,page('Конфиденциальность',f'''<section class=hero><h1>Конфиденциальность</h1><p>Какие данные нужны для работы {brand}.</p></section><div class="card legal"><h2>Какие данные обрабатываются</h2><p>Логин, адрес электронной почты, Telegram ID при привязке, заказы, параметры подписок, обращения в поддержку, технические сведения об авторизации и использовании устройств.</p><h2>Зачем они нужны</h2><p>Для входа, подтверждения регистрации, восстановления доступа, выдачи и продления подписок, проверки оплаты, уведомлений, поддержки и защиты сервиса.</p><h2>Хранение и защита</h2><p>Секреты и пароли не показываются в административных журналах. Одноразовые почтовые ссылки имеют ограниченный срок. Журнал безопасности хранится ограниченное время. Доступ к данным имеет только администратор сервиса.</p><h2>Удаление</h2><p>Запрос на удаление аккаунта можно отправить через поддержку. Часть сведений о платежах может сохраняться столько, сколько необходимо для разрешения споров и выполнения применимых требований.</p></div>'''))
  if p=='/refund-policy':return self.sendx(200,page('Оплата и возвраты',f'''<section class=hero><h1>Оплата и возвраты</h1><p>Правила ручной проверки платежей {brand}.</p></section><div class="card legal"><h2>Проверка оплаты</h2><p>После оплаты заказ ожидает подтверждения администратора. Обязательно указывайте номер заказа, если платёжная форма позволяет добавить комментарий.</p><h2>Неверная сумма</h2><p>Если отправлена неверная сумма или выбран не тот заказ, не выполняйте повторный перевод — обратитесь в поддержку.</p><h2>Возврат</h2><p>Запрос рассматривается администратором с учётом фактически использованного периода, состояния подписки и применимых правил платёжной системы. Для обращения укажите номер заказа.</p></div>'''))
  if p=='/guest-support':
   token=str(parse_qs(urlparse(self.path).query).get('token',[''])[0]).strip()
   if not token:
    body='''<div class="card box"><h2>Гостевой чат поддержки</h2><p>Можно написать администратору без регистрации и Telegram. После отправки сохраните личную ссылку обращения.</p><form method=post action=/guest-support/create><label>Ваше имя</label><input name=name minlength=2 maxlength=64 required placeholder="Как к вам обращаться"><label>Контакт для ответа (необязательно)</label><input name=contact maxlength=120 placeholder="Telegram, телефон или другой удобный контакт"><label>Сообщение</label><textarea name=message minlength=5 maxlength=2000 required placeholder="Опишите вопрос"></textarea><button class=btn>Начать чат</button></form><a class="btn soft backbtn" href=/login>Войти в аккаунт</a></div>'''
    return self.sendx(200,page('Гостевой чат',body))
   with db() as c:ticket=c.execute("SELECT * FROM support_tickets WHERE guest_token=? AND source='guest'",(token,)).fetchone();messages=c.execute('SELECT * FROM support_messages WHERE ticket_id=? ORDER BY id',(ticket['id'],)).fetchall() if ticket else []
   if not ticket:return self.sendx(404,page('Чат не найден','<div class="card box"><h2>Ссылка гостевого чата недействительна</h2><a class=btn href=/guest-support>Создать новое обращение</a></div>'))
   thread=''.join(f"<div class='ticketmsg {'admin' if m['sender']=='admin' else ''}'><div class=ticketmeta>{'Поддержка' if m['sender']=='admin' else 'Вы'} · {time.strftime('%d.%m.%Y %H:%M',time.localtime(m['created_at']))}</div>{html.escape(m['message']).replace(chr(10),'<br>')}</div>" for m in messages)
   form='' if ticket['status']=='closed' else f'''<form method=post action=/guest-support/reply><input type=hidden name=token value="{html.escape(token,quote=True)}"><label>Ваш ответ</label><textarea name=message minlength=5 maxlength=2000 required></textarea><button class=btn>Отправить</button></form>'''
   link=PUBLIC+'/guest-support?token='+quote(token);body=f'''<section class=hero><h1>Обращение #{ticket['id']}</h1><p>Статус: {html.escape({'open':'Ожидает ответа','answered':'Получен ответ','closed':'Закрыт'}.get(ticket['status'],ticket['status']))}</p></section><div class="callout warn"><b>Сохраните эту личную ссылку.</b><p>Без неё открыть переписку повторно не получится.</p><div class=uri>{html.escape(link)}</div><button type=button class=btn data-url="{html.escape(link,quote=True)}" onclick="navigator.clipboard.writeText(this.dataset.url).then(()=>this.textContent='✅ Ссылка скопирована')">📋 Скопировать ссылку чата</button></div><div class=card>{thread}{form}</div>'''
   return self.sendx(200,page('Гостевой чат',body))
  if p=='/telegram-start':
   nonce=secrets.token_urlsafe(18)
   with db() as c:c.execute('INSERT INTO auth_sessions(nonce,expires_at) VALUES(?,?)',(nonce,int(time.time()+900)))
   return self.redir(BOT_URL+('?start=login_'+nonce))
  if p=='/admin-telegram-start':
   nonce=secrets.token_urlsafe(18)
   with db() as c:c.execute("INSERT INTO auth_sessions(nonce,status,expires_at) VALUES(?,'admin_pending',?)",(nonce,int(time.time()+900)))
   return self.redir(BOT_URL+('?start=login_'+nonce))
  if p=='/link-telegram-start':
   if not self.need():return
   nonce=secrets.token_urlsafe(18)
   with db() as c:c.execute("INSERT INTO auth_sessions(nonce,username,status,expires_at) VALUES(?,?,'link_pending',?)",(nonce,self.who(),int(time.time()+900)))
   return self.redir(BOT_URL+('?start=linkaccount_'+nonce))
  if p.startswith('/tg-login/'):
   magic=p.rsplit('/',1)[-1]
   with db() as c:
    a=c.execute("SELECT * FROM auth_sessions WHERE magic=? AND status='ready' AND used_at IS NULL AND expires_at>?",(magic,int(time.time()))).fetchone()
    if a:c.execute("UPDATE auth_sessions SET used_at=?,status='used' WHERE nonce=?",(int(time.time()),a['nonce']))
   if not a:return self.sendx(400,page('Ссылка недействительна','<div class="card box"><h2 class=err>Ссылка входа устарела или уже использована</h2><a class=btn href=/telegram-start>Получить новую ссылку</a></div>'))
   with db() as c:u=c.execute('SELECT role,blocked_at FROM users WHERE username=?',(a['username'],)).fetchone()
   if u and u['blocked_at']:return self.sendx(403,page('Аккаунт заблокирован','<div class="card box"><h2 class=err>Аккаунт временно заблокирован</h2><p>Обратитесь в поддержку.</p></div>'))
   if u and u['role'] in ('admin','test'):return self.sendx(403,page('Вход запрещён','<div class="card box"><h2 class=err>Неправильный логин или пароль</h2><a class="btn soft" href=/login>Повторить</a></div>'))
   security_event('login_success',a['username'],self.request_ip(),{'entry':'telegram'})
   return self.redir('/mode' if u and u['role']=='admin' else '/',f"vpn_session={self.token(a['username'])}; Path=/; Max-Age=86400; Secure; HttpOnly; SameSite=Lax")
  if p=='/logout':return self.redir('/login','vpn_session=; Path=/; Max-Age=0; Secure; HttpOnly')
  if p=='/settings/2fa-qr.png':
   if not self.need():return
   with db() as c:u=c.execute('SELECT username,totp_secret FROM users WHERE username=?',(self.who(),)).fetchone()
   if not u or not u['totp_secret']:return self.sendx(404,'2FA not configured','text/plain')
   import qrcode;label=quote(BRAND_NAME+':'+u['username']);uri=f"otpauth://totp/{label}?secret={u['totp_secret']}&issuer={quote(BRAND_NAME)}&algorithm=SHA1&digits=6&period=30";im=qrcode.make(uri);out=io.BytesIO();im.save(out,format='PNG');return self.sendx(200,out.getvalue(),'image/png')
  if p=='/settings/security':
   if not self.need():return
   admin=self.isadmin()
   with db() as c:
    u=c.execute('SELECT * FROM users WHERE username=?',(self.who(),)).fetchone()
    if not u['totp_secret']:
     secret=new_totp_secret();c.execute('UPDATE users SET totp_secret=? WHERE id=?',(secret,u['id']));u=c.execute('SELECT * FROM users WHERE id=?',(u['id'],)).fetchone()
   params=parse_qs(urlparse(self.path).query);notice='<div class=notice>Логин изменён. Используйте новый логин при следующем входе.</div>' if params.get('login_changed') else ('<div class=notice>Пароль изменён. Остальные сеансы завершены.</div>' if params.get('password_changed') else ('<div class=notice>Двухфакторная аутентификация включена.</div>' if params.get('twofa_enabled') else ('<div class=notice>Двухфакторная аутентификация отключена.</div>' if params.get('twofa_disabled') else '')))
   if u['twofa_enabled']:twofa="""<div class=card><h2>2FA включена</h2><p>После ввода логина и пароля сайт запрашивает одноразовый код.</p><form method=post action=/settings/2fa-disable><label>Текущий пароль</label><input type=password name=current_password required><label>Код из приложения</label><input name=code inputmode=numeric pattern="[0-9]{6}" maxlength=6 required><button class=delbtn>Отключить 2FA</button></form></div>"""
   else:twofa=f"""<div class=card><h2>Подключить 2FA</h2><ol><li>Установите приложение-аутентификатор.</li><li>Отсканируйте QR-код.</li><li>Введите появившийся шестизначный код.</li></ol><img class=qr src=/settings/2fa-qr.png alt='QR-код 2FA'><p>Ручной секрет:</p><div class=uri>{html.escape(u['totp_secret'])}</div><form method=post action=/settings/2fa-enable><label>Код из приложения</label><input name=code inputmode=numeric pattern="[0-9]{{6}}" maxlength=6 required><button class=btn>Включить 2FA</button></form></div>"""
   credentials=f"""<div class=card><h2>Изменить логин</h2><p>Текущий логин: <b>{html.escape(u['username'])}</b>. Ссылки существующих подписок не изменятся.</p><form method=post action=/settings/change-login><label>Новый логин</label><input name=new_login minlength=8 maxlength=32 placeholder='familiaio' required><label>Текущий пароль</label><input type=password name=current_password required><button class=btn>Изменить логин</button></form></div><div class=card><h2>Изменить пароль</h2><p>Минимум 8 символов: строчная и заглавная буквы, цифра и специальный символ.</p><form method=post action=/settings/change-password><label>Текущий пароль</label><input type=password name=current_password required><label>Новый пароль</label><input type=password name=new_password minlength=8 required><label>Повторите новый пароль</label><input type=password name=confirm_password minlength=8 required><button class=btn>Изменить пароль</button></form></div>"""
   telegram_card=(f'''<div class=card><h2>Telegram привязан</h2><p>Telegram ID: <b>{int(u['telegram_id'])}</b>. Уведомления и управление подписками доступны в боте.</p></div>''' if u['telegram_id'] else '''<div class=card><h2>⚠ Telegram не привязан</h2><p>Без привязки не будут приходить уведомления об оплате, окончании подписки и технических работах.</p><a class="btn telegram-login" href=/link-telegram-start>✈ Привязать Telegram</a></div>''')
   email_card=f'''<div class=card><h2>Электронная почта</h2><p>{html.escape(u['email']) if u['email'] else 'Не указана'} · <b>{'подтверждена' if u['email_verified_at'] else 'не подтверждена'}</b></p><p>Почта используется для восстановления пароля и уведомлений о входе.</p></div>'''
   audit=''
   if admin:
    with db() as c:events=c.execute('SELECT * FROM security_events ORDER BY id DESC LIMIT 50').fetchall()
    event_names={'login_success':'Успешный вход','login_failed':'Неудачный вход','admin_action':'Действие администратора','payment_review':'Проверка оплаты','registration':'Регистрация','twofa_failed':'Ошибка 2FA'}
    event_rows=''.join(f"<tr><td>{time.strftime('%d.%m.%Y %H:%M',time.localtime(e['created_at']))}</td><td>{html.escape(event_names.get(e['event_type'],e['event_type']))}</td><td>{html.escape(e['username'] or '—')}</td><td>{html.escape(e['ip_address'] or '—')}</td></tr>" for e in events) or '<tr><td colspan=4>Событий пока нет</td></tr>'
    audit=f'''<details class="card security-log"><summary>Журнал безопасности</summary><p>Последние 50 входов и административных действий. Записи хранятся 90 дней; пароли и секреты сюда не попадают.</p><div class=tablewrap><table><tr><th>Время</th><th>Событие</th><th>Аккаунт</th><th>IP</th></tr>{event_rows}</table></div></details>'''
   return self.sendx(200,page('Безопасность',f"<section class=hero><h1>Безопасность аккаунта</h1><p>Двухфакторная защита и данные для входа.</p></section>{notice}<div class=manual>{email_card}{telegram_card}{twofa}{credentials}{audit}</div>",admin))
  if p=='/mode':
   if not self.need():return
   if not self.isadmin():return self.redir('/')
   body=f'''<div class="card box"><h2>Выберите режим</h2><p>Административный режим предназначен для управления системой. Пользовательская проверка выполняется через отдельный тестовый аккаунт.</p><a class=btn href=/admin>⚙ Я администратор</a><a class="btn soft backbtn" href="{html.escape(TEST_ENTRY,quote=True)}">👤 Войти тестовым пользователем</a><a class="btn soft backbtn" href=/logout>Выйти</a></div>'''
   return self.sendx(200,page('Выбор режима',body))
  if p=='/':
   if not self.who():return self.redir('/login')
   with db() as c:user_id=c.execute('SELECT id FROM users WHERE username=?',(self.who(),)).fetchone()['id']
   tariffs=available_tariffs(user_id);paid=[dict(x) for x in tariffs if not x['trial']];trials=[x for x in tariffs if x['trial']]
   devices=sorted({int(x['devices']) for x in paid});tariff_cards=[]
   for device_count in devices:
    variants=sorted((x for x in paid if int(x['devices'])==device_count),key=lambda x:(int(x['days']),int(x['price'])))
    first=variants[0];options=''.join(f'''<option value="{html.escape(x['code'],quote=True)}" data-price="{int(x['price'])}" data-days="{int(x['days'])}" data-traffic="{int(x['traffic_gb'])}">{html.escape(x['name'])} · {int(x['days'])} дней</option>''' for x in variants)
    device_word='устройство' if device_count==1 else 'устройства' if device_count<5 else 'устройств'
    tariff_cards.append(f'''<article class="card pricing-card user-tariff-card" data-tariff-card><span class=eyebrow>{device_count} {device_word}</span><h2>{device_count} {device_word}</h2><p>Одна ссылка для HAPP на всех ваших устройствах.</p><div class=price data-card-price>{int(first['price'])} <small>{html.escape(CURRENCY)}</small></div><ul><li data-card-days>{int(first['days'])} дней</li><li data-card-traffic>{traffic_text(first['traffic_gb'])}</li><li>Все доступные страны</li></ul><form method=post action=/orders><input type=hidden name=action value=new><label>Выберите срок</label><select name=plan data-plan-select>{options}</select><label>Промокод</label><input name=promo_code maxlength=32 placeholder="Необязательно"><button class=btn>Перейти к оплате</button></form></article>''')
   trial_card=''
   if trials:
    trial=trials[0];trial_card=f'''<article class="card pricing-card user-tariff-card trial-card"><span class=eyebrow>Пробный</span><h2>{html.escape(trial['name'])}</h2><div class=price>0 <small>{html.escape(CURRENCY)}</small></div><ul><li>{trial['days']} дня</li><li>{trial['devices']} устройство</li><li>{traffic_text(trial['traffic_gb'])}</li></ul><form method=post action=/orders><input type=hidden name=action value=new><input type=hidden name=plan value="{html.escape(trial['code'],quote=True)}"><button class="btn soft">Попробовать бесплатно</button></form></article>'''
   cards=trial_card+''.join(tariff_cards) if tariff_cards else trial_card+'<div class="card box"><h2>Тарифы временно недоступны</h2></div>'
   cards_script=f'''<script>(function(){{document.querySelectorAll('[data-tariff-card]').forEach(function(card){{var select=card.querySelector('[data-plan-select]'),price=card.querySelector('[data-card-price]'),days=card.querySelector('[data-card-days]'),traffic=card.querySelector('[data-card-traffic]');function update(){{var option=select.options[select.selectedIndex],limit=Number(option.dataset.traffic);price.innerHTML=option.dataset.price+' <small>{html.escape(CURRENCY)}</small>';days.textContent=option.dataset.days+' дней';traffic.textContent=limit?limit+' ГБ трафика':'Без лимита трафика'}}select.addEventListener('change',update);update()}})}})()</script>'''
   adm='<a class="navbtn" href=/mode>⚙ Выбор режима администратора</a>' if self.isadmin() else ''
   hero=f'''<section class=hero><span class=eyebrow>VPN без сложных настроек</span><h1>Свободный интернет на ваших устройствах</h1><p>Выберите количество устройств, срок и перейдите к оплате. После подтверждения вы получите одну ссылку для HAPP.</p>{adm}</section>'''
   return self.sendx(200,page(BRAND_NAME,hero+f'<section id=tariffs><span class=eyebrow>Оформление подписки</span><h2>Выберите количество устройств</h2><p>В карточке выберите нужный срок и перейдите к оплате.</p><div class=pricing-grid>{cards}</div>{cards_script}</section>'))
  if p=='/cart':
   if not self.need():return
   with db() as c:
    u=c.execute('SELECT id,telegram_id FROM users WHERE username=?',(self.who(),)).fetchone();c.execute("UPDATE orders SET status='expired',error='Истекло время оплаты' WHERE status IN ('pending','error') AND expires_at IS NOT NULL AND expires_at<=?",(int(time.time()),))
    orders=c.execute("SELECT * FROM orders WHERE (user_id=? OR (? IS NOT NULL AND telegram_id=?)) AND status IN ('pending','error') ORDER BY id DESC",(u['id'],u['telegram_id'],u['telegram_id'])).fetchall()
   if not orders:
    empty="""<section class=hero><span class=eyebrow>Корзина</span><h1>Корзина пока пуста</h1><p>Добавьте тариф, чтобы перейти к оформлению и оплате.</p></section><div class='card empty-state'><div class=empty-icon>🛒</div><h2>Выберите свою подписку</h2><p>Тариф появится здесь вместе с суммой, сроком и количеством устройств.</p><a class=btn href=/#tariffs>Посмотреть тарифы</a></div>"""
    return self.sendx(200,page('Корзина',empty))
   items=[];total=0
   for o in orders:
    plan=order_plan(o);total+=int(o['amount'] or 0);deadline=order_deadline(o)*1000
    items.append(f'''<article class="card cart-item"><div><span class=status-pill>Заказ №{o['id']}</span><h3>{html.escape(str(plan[0]))} · {plan[3]} устр.</h3><div class=cart-meta><span>Трафик: {traffic_text()}</span><span>Все серверы HOME‑VPN</span><span>Оплатить за <b class=order-timer data-deadline="{deadline}">--:--</b></span></div></div><div><div class=cart-price>{o['amount']} {html.escape(CURRENCY)}</div><div class=order-actions><a class=btn href="/pay/{html.escape(o['token'],quote=True)}">Оплата</a>{cancel_order_button(o)}</div></div></article>''')
   script="""<script>(function(){function tick(){var reload=false;document.querySelectorAll('.order-timer').forEach(function(el){var s=Math.max(0,Math.ceil((Number(el.dataset.deadline)-Date.now())/1000));el.textContent=String(Math.floor(s/60)).padStart(2,'0')+':'+String(s%60).padStart(2,'0');if(!s)reload=true});if(reload)setTimeout(function(){location.reload()},1200)}tick();setInterval(tick,1000)})()</script>"""
   summary=f'''<aside class="card cart-summary"><span class=eyebrow>Ваш заказ</span><h2>Итого</h2><div class=summary-line><span>Позиций</span><b>{len(orders)}</b></div><div class="summary-line summary-total"><span>К оплате</span><b>{total} ₽</b></div><p><small>Каждый заказ оплачивается отдельно. После нажатия «Оплатить» отмена заказа невозможна.</small></p><a class="btn soft" href=/#tariffs>＋ Добавить ещё тариф</a></aside>'''
   return self.sendx(200,page('Корзина',f'<section class=hero><span class=eyebrow>Корзина</span><h1>Ваши тарифы</h1><p>Проверьте срок и количество устройств перед переходом к оплате.</p></section><div class=cart-layout><div class=cart-items>{"".join(items)}</div>{summary}</div>{script}'))
  if p=='/subscription/manage':
   if not self.need():return
   email=parse_qs(urlparse(self.path).query).get('email',[''])[0]
   with db() as c:u=c.execute('SELECT id,telegram_id FROM users WHERE username=?',(self.who(),)).fetchone()
   owned={x.get('email'):x for x in account_clients(u['id'],u['telegram_id']) if x.get('email')}
   if email not in owned:return self.sendx(404,page('Подписка не найдена','<div class="card box"><h2 class=err>Подписка не найдена или не принадлежит этому аккаунту</h2><a class="btn soft" href=/account>Вернуться</a></div>'))
   client=owned[email];current_devices=max(1,int(client.get('limitIp') or 1));expiry=int(client.get('expiryTime') or 0);remaining=max(0,math.ceil((expiry-int(time.time()*1000))/86400000));catalog=available_tariffs(u['id'],renewal=True);renew_rows=''
   for tariff in catalog:
    if tariff['trial'] or int(tariff['devices'])!=current_devices:continue
    renew_rows+=f"<tr><td class=table-title>{html.escape(tariff['name'])}</td><td>{current_devices}</td><td>+{tariff['days']} дней</td><td>{traffic_text(tariff['traffic_gb'])}</td><td class=table-price>{tariff['price']} ₽</td><td><form method=post action=/orders><input type=hidden name=action value=renew><input type=hidden name=subscription value='{html.escape(email,quote=True)}'><input type=hidden name=plan value='{html.escape(tariff['code'],quote=True)}'><button class=btn>Продлить</button></form></td></tr>"
   renew_rows=renew_rows or '<tr><td colspan=6>Нет опубликованных тарифов для продления с этим лимитом.</td></tr>'
   upgrade_rows=''
   if remaining>0:
    targets={}
    for tariff in catalog:
     if not tariff['trial'] and int(tariff['devices'])>current_devices:
      old=targets.get(int(tariff['devices']))
      if not old or int(tariff['days'])<int(old['days']):targets[int(tariff['devices'])]=tariff
    for devices,tariff in sorted(targets.items()):
     days,price=catalog_upgrade_price(current_devices,tariff,expiry,u['id'])
     if price<=0:continue
     upgrade_rows+=f"<tr><td>{current_devices}</td><td class=table-title>{devices}</td><td>{days} дней</td><td>Доплата только за оставшиеся дни</td><td class=table-price>{price} ₽</td><td><form method=post action=/orders><input type=hidden name=action value=upgrade><input type=hidden name=subscription value='{html.escape(email,quote=True)}'><input type=hidden name=plan value='{html.escape(tariff['code'],quote=True)}'><button class=btn>Изменить</button></form></td></tr>"
   if not upgrade_rows:upgrade_rows=f"<tr><td colspan=6>{'Нет доступных тарифов с большим лимитом.' if remaining else 'Срок подписки закончился. Сначала продлите тариф.'}</td></tr>"
   info=f"<section class=hero><h1>Управление тарифом</h1><p>Подписка: <b>{html.escape(email)}</b></p></section><div class=card><h2>Текущий тариф</h2><div class=tablewrap><table class=catalog-table><tr><th>Подписка</th><th>Используется</th><th>Лимит</th><th>Действует до</th><th>Осталось</th><th>Трафик</th></tr><tr><td class=table-title>{html.escape(email)}</td><td>{used_devices(email)}</td><td>{current_devices}</td><td>{date_text(expiry)}</td><td>{remaining} дней</td><td>{traffic_text()}</td></tr></table></div></div><br>"
   renew=f"<div class=card id=renew><h2>1. Продлить тариф</h2><p>Количество устройств останется прежним — {current_devices}. Выберите новый срок.</p><div class=tablewrap><table class=catalog-table><tr><th>Срок</th><th>Устройства</th><th>Добавится</th><th>Трафик</th><th>Стоимость</th><th>Действие</th></tr>{renew_rows}</table></div></div>"
   upgrade=f"<div class=card id=change><h2>2. Изменить тариф</h2><p>Можно увеличить количество устройств. Система сама рассчитает доплату за оставшиеся дни; дата окончания и ссылка не изменятся.</p><div class=tablewrap><table class=catalog-table><tr><th>Было</th><th>Станет</th><th>Осталось</th><th>Расчёт</th><th>Доплата</th><th>Действие</th></tr>{upgrade_rows}</table></div></div>"
   return self.sendx(200,page('Управление тарифом',info+renew+'<br>'+upgrade+'<br><a class="btn soft" href=/account>← Вернуться к подпискам</a>'))
  if p=='/connection-check':
   if not self.need():return
   with db() as c:u=c.execute('SELECT id,telegram_id FROM users WHERE username=?',(self.who(),)).fetchone()
   clients=account_clients(u['id'],u['telegram_id']);now=int(time.time()*1000);cards=[]
   for client in clients:
    traffic=client.get('traffic') or {};last=int(traffic.get('lastOnline') or 0);last_ms=last if last>10**12 else last*1000;connected=bool(last_ms and now-last_ms<10*60*1000);loaded=bool(client.get('email'))
    state=lambda ok,title,text:f'''<div class="diag-row {'ok' if ok else 'wait'}"><span>{'✅' if ok else '⏳'}</span><div><b>{title}</b><br><small>{text}</small></div></div>'''
    cards.append(f'''<div class=card><h2>{html.escape(client.get('email') or 'Подписка')}</h2>{state(True,'Интернет работает','Страница HOME-VPN доступна.')}{state(loaded,'Подписка загружена','Система видит вашу подписку.')}{state(connected,'VPN подключён','Активность видна сейчас.' if connected else 'Включите VPN в HAPP, откройте любой сайт и нажмите «Проверить снова».')}</div>''')
   empty='<div class="card box"><h2>Подписка пока не найдена</h2><a class=btn href=/#tariffs>Выбрать тариф</a></div>'
   return self.sendx(200,page('Проверка соединения',f'''<section class=hero><h1>Проверка соединения</h1><p>Проверим три простых шага без технических терминов.</p><a class=btn href=/connection-check>Проверить снова</a></section>{''.join(cards) or empty}'''))
  if p=='/instructions':
   intro="""<section class=hero><h1>HAPP: от скачивания до запуска</h1><p>Очень подробная инструкция. Делайте действия по одному и не спешите. Одна ссылка HOME-VPN сразу добавит все доступные серверы.</p></section><div class=osnav><a href=#windows>🪟 Windows</a><a href=#linux>🐧 Linux</a><a href=#android>🤖 Android</a><a href=#ios>🍎 iPhone</a></div>"""
   visual="""<div class=card><h2>Инструкция для подключения мобильных устройств</h2><p>Сначала скопируйте ссылку подписки HOME-VPN. Затем выполните действия по красным цифрам.</p><div class=visualsteps><div class=vstep><div class=shot><img src='/assets/happ-original-step-1.png' alt='Шаг 1: нажать Из буфера в HAPP'></div><h3>Шаг 1. Добавьте подписку</h3><p>Нажмите «Из буфера». HAPP автоматически возьмёт скопированную ссылку HOME-VPN.</p></div><div class=vstep><div class=shot><img src='/assets/happ-original-step-2.png' alt='Шаги 2 и 3: выбрать сервер и включить HAPP'></div><h3>Шаги 2–3. Выберите и включите</h3><p>Нажмите нужный сервер, затем большую круглую кнопку питания.</p></div></div></div>"""
   start="""<div class=card><h2>Сначала подготовьте ссылку</h2><ol><li>Войдите в HOME-VPN под своим логином.</li><li>Выберите срок и количество устройств, затем добавьте тариф в корзину.</li><li>В течение 10 минут нажмите <b>«Оплатить»</b>. Подписка будет создана временно, а администратор получит уведомление.</li><li>Выполните оплату способом, который настроил владелец сервиса.</li><li>После проверки оплаты подписка появится в разделе <b>«Мои подписки»</b>.</li><li>Откройте единую ссылку подписки и добавьте её в HAPP.</li></ol><div class=callout>Эта ссылка — ваш ключ от VPN. Не отправляйте её посторонним. Внутри автоматически появятся все доступные узлы HOME-VPN; добавлять каждый сервер отдельно не нужно.</div><a class=btn href=/account>Открыть мои подписки</a></div>"""
   win="""<div class=card id=windows><h2>🪟 Windows — совсем по шагам</h2><a class=btn href=/files/setup-Happ.x64.exe>1. Скачать HAPP для Windows</a><ol><li>Нажмите кнопку выше. В браузере справа сверху появится значок загрузки. Дождитесь окончания.</li><li>Откройте файл <span class=code>setup-Happ.x64.exe</span>. Обычно он лежит в папке <b>Загрузки</b>.</li><li>Если Windows спросит «Разрешить этому приложению вносить изменения?», проверьте имя файла и нажмите <b>Да</b>.</li><li>Если появился защитный экран SmartScreen, продолжайте только если файл скачан кнопкой на этой странице: нажмите <b>Подробнее</b>, затем <b>Выполнить в любом случае</b>.</li><li>В установщике нажимайте <b>Next / Далее</b>, затем <b>Install / Установить</b> и <b>Finish / Готово</b>.</li><li>Откройте HAPP через меню <b>Пуск</b> или ярлык на рабочем столе.</li><li>Вернитесь на страницу оплаты HOME-VPN и скопируйте единую ссылку подписки.</li><li>В HAPP нажмите большую кнопку <b>+</b>. Выберите добавление <b>подписки по URL</b> или <b>из буфера обмена</b>.</li><li>Вставьте ссылку клавишами <b>Ctrl + V</b> и подтвердите добавление.</li><li>В списке появятся все доступные серверы HOME-VPN. Выберите нужную страну и нажмите круглую кнопку подключения.</li><li>При первом запуске Windows может спросить разрешение для сети — нажмите <b>Разрешить</b>.</li></ol><div class=callout>Готово, когда кнопка HAPP стала активной и приложение показывает <b>Connected / Подключено</b>.</div><img src='/assets/happ-screen-1.jpg' alt='Оригинальный экран HAPP'></div>"""
   linux="""<div class=card id=linux><h2>🐧 Linux Ubuntu / Debian</h2><a class=btn href=/files/Happ.linux.x64.deb>1. Скачать HAPP для Linux</a><ol><li>Скачайте файл <span class=code>Happ.linux.x64.deb</span> и дождитесь окончания загрузки.</li><li>Откройте папку <b>Загрузки</b> и дважды нажмите файл. В открывшемся центре приложений нажмите <b>Установить</b>.</li><li>Введите пароль пользователя компьютера, если система попросит. Символы пароля могут не отображаться — это нормально.</li><li>Если установка двойным нажатием не открылась, запустите терминал в папке Загрузки и выполните <span class=code>sudo apt install ./Happ.linux.x64.deb</span>.</li><li>Найдите HAPP в меню приложений и запустите его.</li><li>Скопируйте ссылку подписки HOME-VPN, нажмите в HAPP <b>+</b>, выберите подписку по URL и вставьте ссылку.</li><li>Подтвердите импорт, выберите нужную страну из доступного списка серверов и нажмите подключение.</li><li>Если Linux попросит пароль для изменения сети, введите пароль пользователя компьютера.</li></ol><div class=callout>При успехе HAPP покажет <b>Connected / Подключено</b>. Окно можно свернуть — VPN продолжит работать.</div></div>"""
   android="""<div class=card id=android><h2>🤖 Android — телефон или планшет</h2><a class=btn href='https://play.google.com/store/apps/details?id=com.happproxy&amp;hl=ru' target=_blank rel=noopener>1. Скачать HAPP в Google Play</a><a class='btn soft backbtn' href=/files/Happ.apk>Запасной вариант: скачать APK</a><ol><li>Нажмите кнопку <b>«Скачать HAPP в Google Play»</b>.</li><li>На странице приложения нажмите <b>Установить</b>, дождитесь завершения и нажмите <b>Открыть</b>.</li><li>Если Google Play недоступен, скачайте APK запасной кнопкой. Откройте <b>Файлы → Загрузки → Happ.apk</b>, разрешите установку из этого источника и подтвердите установку.</li><li>Скопируйте единую ссылку подписки HOME-VPN.</li><li>В HAPP нажмите <b>Из буфера</b>. Приложение само добавит подписку.</li><li>Выберите нужный сервер и нажмите большую кнопку питания.</li><li>Android покажет запрос «Настроить VPN-подключение?» — нажмите <b>ОК / Разрешить</b>.</li></ol><div class=callout>Готово, когда сверху телефона появился значок ключа или надпись VPN.</div><img src='/assets/happ-original-step-2.png' alt='Оригинальный интерфейс HAPP со стрелками и цифрами'></div>"""
   perapp="""<div class=card><h2>Если VPN в автоматическом режиме работает нестабильно</h2><p>Попробуйте включить VPN только для тех приложений, которым он действительно нужен. Например, для WhatsApp, YouTube или браузера. Остальные программы продолжат пользоваться обычным интернетом — это может сделать подключение стабильнее.</p><div class='callout warn'><b>Обратите внимание:</b> после включения этого режима через HOME-VPN будут работать только приложения с установленной галочкой. Если нужной программы нет на экране, найдите её прокруткой или через значок поиска.</div><div class=visualsteps><div class=vstep><div class=shot><img src='/assets/happ-apps-step-4.png' alt='Шаг 4: открыть настройки HAPP'></div><h3>Шаг 4. Откройте настройки</h3><p>На главном экране HAPP нажмите значок шестерёнки в левом верхнем углу.</p></div><div class=vstep><div class=shot><img src='/assets/happ-apps-step-5.png' alt='Шаг 5: открыть прокси для выбранных приложений'></div><h3>Шаг 5. Откройте список программ</h3><p>Найдите раздел «Настройки туннеля» и нажмите строку «Прокси для выбранных приложений».</p></div><div class=vstep><div class=shot><img src='/assets/happ-apps-step-6.png' alt='Шаг 6: выбрать приложения для VPN'></div><h3>Шаг 6. Выберите нужные программы</h3><p>Нажмите вкладку «ВКЛ». Затем поставьте галочки напротив всех приложений, которые должны подключаться через VPN.</p></div></div><div class=callout><b>Завершение:</b> нажмите стрелку «Назад», вернитесь на главный экран HAPP, выберите сервер и нажмите большую круглую кнопку включения. Если какое-то приложение перестало открываться, вернитесь в список и проверьте, установлена ли возле него галочка.</div></div>"""
   ios="""<div class=card id=ios><h2>🍎 iPhone и iPad</h2><a class=btn href='https://apps.apple.com/us/app/happ-proxy-utility/id6504287215' target=_blank rel=noopener>1. Открыть HAPP в App Store</a><ol><li>В App Store нажмите <b>Загрузить</b> и подтвердите Face ID, Touch ID или паролем Apple Account.</li><li>После установки нажмите <b>Открыть</b>.</li><li>Скопируйте единую ссылку подписки на странице HOME-VPN.</li><li>В HAPP нажмите <b>+</b>, выберите импорт подписки по URL или из буфера обмена.</li><li>Вставьте ссылку и нажмите кнопку подтверждения.</li><li>Когда iPhone спросит, можно ли добавить конфигурацию VPN, нажмите <b>Разрешить</b>. Подтвердите кодом телефона или Face ID.</li><li>Выберите нужную страну из списка доступных серверов и нажмите кнопку подключения.</li></ol><div class=callout>Готово, когда в HAPP написано <b>Connected</b>, а в настройках или строке состояния iPhone появился значок VPN.</div><img src='/assets/happ-screen-2.jpg' alt='Оригинальный интерфейс HAPP на iPhone'><h3>Если HAPP не найден в вашем App Store</h3><p>Сначала попробуйте открыть кнопку App Store ещё раз. Если приложение недоступно для страны аккаунта, Apple разрешает сменить регион, но перед этим может потребоваться потратить остаток баланса, дождаться завершения возвратов и отменить мешающие подписки.</p><ol><li>Откройте <b>Настройки</b> iPhone.</li><li>Нажмите своё имя в самом верху.</li><li>Откройте <b>Медиаматериалы и покупки → Просмотреть</b>.</li><li>Нажмите <b>Страна/регион → Изменить страну или регион</b>.</li><li>Выберите доступную страну, примите условия и укажите действительный способ оплаты и адрес для этой страны.</li><li>Вернитесь в App Store, найдите HAPP и установите его.</li></ol><div class='callout warn'>Не указывайте вымышленные платёжные данные. Смена региона влияет на покупки и подписки Apple. Если есть семейная группа или активные подписки, сначала прочитайте официальные условия Apple.</div><img src='/assets/apple-region.png' alt='Где изменить страну или регион Apple'><a class='btn soft' href='https://support.apple.com/118283' target=_blank rel=noopener>Официальная инструкция Apple</a></div>"""
   helpx=f"""<div class=card><h2>Если VPN не подключается</h2><ol><li>Убедитесь, что обычный интернет работает без VPN.</li><li>Проверьте, что дата и время на устройстве установлены автоматически.</li><li>В HAPP выберите другой доступный сервер.</li><li>Обновите подписку в меню HAPP или удалите её и добавьте ту же ссылку заново.</li><li>Полностью закройте и снова откройте HAPP. Если не помогло — перезагрузите устройство.</li><li>Проверьте страницу <a class=a href=/status>«Статус серверов»</a>. Зелёная надпись <b>Работает</b> означает, что сервер доступен.</li></ol><a class=tgbtn href='{html.escape(BOT_URL)}' target=_blank rel=noopener>✈ Написать боту HOME-VPN</a></div>"""
   return self.sendx(200,page('Подробная инструкция HAPP',intro+f'<div class=manual>{visual}{start}{win}{linux}{android}{perapp}{ios}{helpx}</div>'))
  if p=='/downloads':
   items=[('Windows x64','Версия 4.2.1 · EXE','/files/setup-Happ.x64.exe','Скачать'),('Linux x64','Версия 4.2.1 · DEB','/files/Happ.linux.x64.deb','Скачать'),('Android','Официальный Google Play','https://play.google.com/store/apps/details?id=com.happproxy&amp;hl=ru','Открыть Google Play'),('Android · APK','Версия 4.4.1 · запасной вариант','/files/Happ.apk','Скачать APK'),('iPhone / iPad','Официальный App Store','https://apps.apple.com/us/app/happ-proxy-utility/id6504287215','Открыть App Store')]
   download_rows=''.join(f"<tr><td class=table-title>{n}</td><td>{d}</td><td><a class=btn href='{u}' {'target=_blank rel=noopener' if u.startswith('https:') else ''}>{label}</a></td></tr>" for n,d,u,label in items)
   return self.sendx(200,page('Скачать HAPP',f'<section class=hero><h1>Скачать HAPP</h1><p>Официальные установщики HAPP, сохранённые на сервере HOME-VPN.</p></section><div class=card><div class=tablewrap><table class=catalog-table><tr><th>Система</th><th>Версия и формат</th><th>Действие</th></tr>{download_rows}</table></div></div>'))
  if p=='/support':
   if not self.need():return
   with db() as c:
    u=c.execute('SELECT id FROM users WHERE username=?',(self.who(),)).fetchone()
    tickets=c.execute('SELECT * FROM support_tickets WHERE user_id=? ORDER BY updated_at DESC',(u['id'],)).fetchall() if u else []
   ticket_rows=''.join(f"<tr><td class=table-title>#{t['id']}</td><td>{html.escape({'open':'Ожидает ответа','answered':'Получен ответ','closed':'Закрыт'}.get(t['status'],t['status']))}</td><td>{time.strftime('%d.%m.%Y %H:%M',time.localtime(t['created_at']))}</td><td>{time.strftime('%d.%m.%Y %H:%M',time.localtime(t['updated_at']))}</td><td><a class=btn href='/support/ticket?id={t['id']}'>Открыть</a></td></tr>" for t in tickets) or '<tr><td colspan=5>Обращений пока нет. Создайте тикет в форме выше.</td></tr>'
   form="""<div class=card><h2>Создать тикет</h2><p>Ответ поддержки появится в личном кабинете. Привязка Telegram не требуется.</p><form method=post action=/support/create><label>Опишите проблему</label><textarea name=message minlength=5 maxlength=2000 required placeholder="Что произошло, на каком устройстве и какую ошибку вы видите?"></textarea><button class=btn>Отправить в поддержку</button></form></div>"""
   ticket_table=f"<div class=card><h2>Мои обращения</h2><div class=tablewrap><table class=catalog-table><tr><th>Тикет</th><th>Статус</th><th>Создан</th><th>Обновлён</th><th>Действие</th></tr>{ticket_rows}</table></div></div>"
   return self.sendx(200,page('Поддержка',f'<section class=hero><h1>Поддержка</h1><p>Переписка с администратором HOME-VPN через сайт.</p></section>{form}<br>{ticket_table}'))
  if p=='/support/ticket':
   if not self.need():return
   try:ticket_id=int(parse_qs(urlparse(self.path).query).get('id',[0])[0])
   except:return self.sendx(400,'bad ticket')
   with db() as c:
    u=c.execute('SELECT id FROM users WHERE username=?',(self.who(),)).fetchone();ticket=c.execute('SELECT * FROM support_tickets WHERE id=? AND user_id=?',(ticket_id,u['id'] if u else 0)).fetchone()
    messages=c.execute('SELECT * FROM support_messages WHERE ticket_id=? ORDER BY id',(ticket_id,)).fetchall() if ticket else []
   if not ticket:return self.sendx(404,page('Не найдено','<div class="card box"><h2>Тикет не найден</h2><a class="btn soft" href=/support>Вернуться</a></div>'))
   mark_site_notifications_read(u['id'],[f"support:{m['id']}" for m in messages if m['sender']=='admin'])
   thread=''.join(f"<div class='ticketmsg {'admin' if m['sender']=='admin' else ''}'><div class=ticketmeta>{'Поддержка HOME-VPN' if m['sender']=='admin' else 'Вы'} · {time.strftime('%d.%m.%Y %H:%M',time.localtime(m['created_at']))}</div>{html.escape(m['message']).replace(chr(10),'<br>')}</div>" for m in messages)
   actions='' if ticket['status']=='closed' else f"""<form method=post action=/support/reply><input type=hidden name=ticket_id value={ticket_id}><label>Ваш ответ</label><textarea name=message minlength=5 maxlength=2000 required></textarea><button class=btn>Отправить ответ</button></form><form method=post action=/support/close onsubmit="return confirm('Закрыть тикет?')"><input type=hidden name=ticket_id value={ticket_id}><button class='btn soft backbtn'>Закрыть тикет</button></form>"""
   return self.sendx(200,page(f'Тикет #{ticket_id}',f"<section class=hero><h1>Тикет #{ticket_id}</h1><p>Статус: {html.escape({'open':'Ожидает ответа','answered':'Получен ответ','closed':'Закрыт'}.get(ticket['status'],ticket['status']))}</p></section><div class=card>{thread}{actions}</div><a class='btn soft backbtn' href=/support>← Все тикеты</a>"))
  if p=='/admin/tickets':
   if not self.isadmin():return self.sendx(403,'forbidden')
   with db() as c:tickets=c.execute("SELECT * FROM support_tickets WHERE COALESCE(source,'telegram')!='guest' ORDER BY CASE WHEN status='closed' THEN 1 ELSE 0 END,updated_at DESC LIMIT 100").fetchall()
   rows=''.join(f"<tr><td>#{t['id']}</td><td>{html.escape(t['customer'] or '')}</td><td>{'Сайт' if t['source']=='site' else 'Telegram'}</td><td>{html.escape({'open':'Ожидает ответа','answered':'Получен ответ','closed':'Закрыт'}.get(t['status'],t['status']))}</td><td>{time.strftime('%d.%m.%Y %H:%M',time.localtime(t['updated_at']))}</td><td><div class=admin-actions><a class=navbtn href='/admin/ticket?id={t['id']}'>Открыть</a><form method=post action=/admin/tickets/delete onsubmit=\"return confirm('Удалить тикет #{t['id']} и всю переписку?')\"><input type=hidden name=ticket_id value={t['id']}><button class=delbtn>Удалить</button></form></div></td></tr>" for t in tickets) or '<tr><td colspan=6>Тикетов пока нет</td></tr>'
   bulk="""<form method=post action=/admin/tickets/delete-closed onsubmit="return confirm('Удалить все закрытые тикеты и переписку?')"><button class=delbtn>Удалить все закрытые тикеты</button></form>"""
   return self.sendx(200,page('Тикеты',f"<section class=hero><h1>Тикеты поддержки</h1><p>Обращения с сайта и из Telegram.</p>{bulk}</section><div class=card><div class=tablewrap><table><tr><th>Тикет</th><th>Пользователь</th><th>Источник</th><th>Статус</th><th>Обновлён</th><th>Управление</th></tr>{rows}</table></div></div>",True))
  if p=='/admin/guest-tickets':
   if not self.isadmin():return self.sendx(403,'forbidden')
   with db() as c:tickets=c.execute("SELECT * FROM support_tickets WHERE source='guest' ORDER BY CASE WHEN status='closed' THEN 1 ELSE 0 END,updated_at DESC LIMIT 100").fetchall()
   rows=''.join(f"<tr><td>#{t['id']}</td><td>{html.escape(t['customer'] or '')}<br><small>{html.escape(t['guest_contact'] or 'контакт не указан')}</small></td><td>{html.escape({'open':'Ожидает ответа','answered':'Получен ответ','closed':'Закрыт'}.get(t['status'],t['status']))}</td><td>{time.strftime('%d.%m.%Y %H:%M',time.localtime(t['updated_at']))}</td><td><div class=admin-actions><a class=navbtn href='/admin/ticket?id={t['id']}'>Открыть чат</a><form method=post action=/admin/tickets/delete onsubmit=\"return confirm('Удалить гостевой чат #{t['id']}?')\"><input type=hidden name=ticket_id value={t['id']}><input type=hidden name=return_to value=guest><button class=delbtn>Удалить</button></form></div></td></tr>" for t in tickets) or '<tr><td colspan=5>Гостевых обращений пока нет</td></tr>'
   return self.sendx(200,page('Гостевой чат',f"<section class=hero><h1>Гостевой чат</h1><p>Отдельные обращения людей без регистрации и привязки Telegram.</p></section><div class=card><div class=tablewrap><table><tr><th>Чат</th><th>Гость</th><th>Статус</th><th>Обновлён</th><th>Управление</th></tr>{rows}</table></div></div>",True))
  if p=='/admin/test-user':
   if not self.isadmin():return self.sendx(403,'forbidden')
   with db() as c:tests=c.execute("SELECT * FROM users WHERE role='test' ORDER BY id").fetchall()
   params=parse_qs(urlparse(self.path).query);notice='<div class=notice>Данные тестового пользователя обновлены. Его предыдущие сеансы завершены.</div>' if params.get('saved') else ''
   cards=''.join(f'''<div class=card><h2>{html.escape(test['username'])}</h2><form method=post action=/admin/test-user><input type=hidden name=action value=update><input type=hidden name=user_id value={test['id']}><label>Логин</label><input name=username minlength=8 maxlength=32 value="{html.escape(test['username'],quote=True)}" required><label>Новый пароль</label><input name=password type=password minlength=8 placeholder="Не заполняйте, если не меняете"><label>Повторите пароль</label><input name=password_confirm type=password minlength=8><button class=btn>Сохранить</button></form><a class="btn soft backbtn" href="{html.escape(TEST_ENTRY,quote=True)}">Открыть форму входа</a></div>''' for test in tests) or '<div class=card><h2>Тестовых пользователей пока нет</h2></div>'
   create='''<div class=card><h2>Добавить тестового пользователя</h2><form method=post action=/admin/test-user><input type=hidden name=action value=create><label>Логин</label><input name=username minlength=8 maxlength=32 placeholder="test-user" required><label>Пароль</label><input name=password type=password minlength=8 required><label>Повторите пароль</label><input name=password_confirm type=password minlength=8 required><button class=btn>Создать</button></form></div>'''
   body=f'''<section class=hero><h1>Тестовые пользователи</h1><p>Несколько изолированных аккаунтов для проверки сайта и Telegram-бота.</p></section>{notice}<div class=tariff-admin-grid>{cards}{create}</div><p><small>Логин: 8–32 символа, обязательны латинские буквы; цифры и дефис разрешены. Пароль: строчная и заглавная буквы, цифра и специальный символ.</small></p>'''
   return self.sendx(200,page('Тестовые пользователи',body,True))
  if p=='/admin/ticket':
   if not self.isadmin():return self.sendx(403,'forbidden')
   try:ticket_id=int(parse_qs(urlparse(self.path).query).get('id',[0])[0])
   except:return self.sendx(400,'bad ticket')
   with db() as c:ticket=c.execute('SELECT * FROM support_tickets WHERE id=?',(ticket_id,)).fetchone();messages=c.execute('SELECT * FROM support_messages WHERE ticket_id=? ORDER BY id',(ticket_id,)).fetchall() if ticket else []
   if not ticket:return self.sendx(404,'ticket not found')
   return_field='<input type=hidden name=return_to value=guest>' if ticket['source']=='guest' else ''
   thread=''.join(f"<div class='ticketmsg {'admin' if m['sender']=='admin' else ''}'><div class=ticketmeta>{'Администратор' if m['sender']=='admin' else 'Пользователь'} · {time.strftime('%d.%m.%Y %H:%M',time.localtime(m['created_at']))}</div>{html.escape(m['message']).replace(chr(10),'<br>')}</div>" for m in messages)
   actions='' if ticket['status']=='closed' else f"""<form method=post action=/admin/tickets/reply><input type=hidden name=ticket_id value={ticket_id}><label>Ответ администратора</label><textarea name=message minlength=5 maxlength=2000 required></textarea><button class=btn>Ответить</button></form><form method=post action=/admin/tickets/close onsubmit="return confirm('Закрыть тикет?')"><input type=hidden name=ticket_id value={ticket_id}><button class='btn soft backbtn'>Закрыть тикет</button></form>"""
   actions+=f"""<form method=post action=/admin/tickets/delete onsubmit="return confirm('Удалить тикет #{ticket_id} и всю переписку без возможности восстановления?')"><input type=hidden name=ticket_id value={ticket_id}>{return_field}<button class='delbtn backbtn'>Удалить тикет</button></form>"""
   source_name='Гостевой чат' if ticket['source']=='guest' else ('Сайт' if ticket['source']=='site' else 'Telegram');back='/admin/guest-tickets' if ticket['source']=='guest' else '/admin/tickets'
   return self.sendx(200,page(f'Тикет #{ticket_id}',f"<section class=hero><h1>Тикет #{ticket_id}</h1><p>{html.escape(ticket['customer'] or '')} · {source_name}</p></section><div class=card>{thread}{actions}</div><a class='btn soft backbtn' href={back}>← Все тикеты</a>",True))
  if p in ('/orders/pending','/admin/orders/pending'):
   if not self.need():return
   admin_view=p.startswith('/admin/')
   if not admin_view:return self.redir('/account')
   if admin_view and not self.isadmin():return self.sendx(403,'forbidden')
   with db() as c:
    c.execute("UPDATE orders SET status='expired',error='Истекло время оплаты' WHERE status IN ('pending','error') AND expires_at IS NOT NULL AND expires_at<=?",(int(time.time()),))
    if admin_view:orders=c.execute("SELECT * FROM orders WHERE status IN ('pending','processing','review','payment_pending','payment_error','error') ORDER BY id DESC").fetchall()
    else:
     u=c.execute('SELECT id,telegram_id FROM users WHERE username=?',(self.who(),)).fetchone();orders=c.execute("SELECT * FROM orders WHERE (user_id=? OR (? IS NOT NULL AND telegram_id=?)) AND status IN ('pending','processing','review','payment_pending','payment_error','error') ORDER BY id DESC",(u['id'],u['telegram_id'],u['telegram_id'])).fetchall()
   heading='Все неподтверждённые заказы' if admin_view else 'Неподтверждённые заказы';back='<a class="btn soft backbtn" href=/admin>← Админ-панель</a>' if admin_view else ''
   return self.sendx(200,page(heading,f"<section class=hero><h1>{heading}</h1><p>Здесь находятся заказы, ожидающие оплаты, оформления или проверки администратором.</p></section>{pending_orders_table(orders,admin_view)}{back}",admin_view))
  if p=='/admin/mail/settings':
   if not self.isadmin():return self.sendx(403,'forbidden')
   settings=mail_settings();params=parse_qs(urlparse(self.path).query);notice='<div class=notice>Настройки почты сохранены.</div>' if params.get('saved') else ('<div class=notice>Соединение и отправка тестового письма выполнены успешно.</div>' if params.get('tested') else '')
   checked=lambda value:'checked' if value else ''
   form=f'''<div class=card><h2>SMTP — отправка</h2><form method=post action=/admin/mail/settings><label>SMTP-сервер</label><input name=smtp_host value="{html.escape(str(settings.get('smtp_host') or ''),quote=True)}" placeholder=mail.my.domain.ru required><label>Порт</label><input type=number name=smtp_port value="{int(settings.get('smtp_port') or 587)}" required><label>Логин</label><input name=smtp_username value="{html.escape(str(settings.get('smtp_username') or ''),quote=True)}"><label>Новый пароль приложения</label><input type=password name=smtp_password autocomplete=new-password placeholder="Оставьте пустым, чтобы не менять"><label><input type=checkbox name=smtp_starttls value=1 {checked(settings.get('smtp_starttls'))}> STARTTLS</label><label><input type=checkbox name=smtp_ssl value=1 {checked(settings.get('smtp_ssl'))}> SSL/TLS с подключения</label><label>Адрес отправителя</label><input type=email name=mail_from value="{html.escape(str(settings.get('mail_from') or 'support@my.domain.ru'),quote=True)}" required><label>Reply-To</label><input type=email name=reply_to value="{html.escape(str(settings.get('reply_to') or 'support@my.domain.ru'),quote=True)}" required><h2>IMAP — входящие письма</h2><label>IMAP-сервер</label><input name=imap_host value="{html.escape(str(settings.get('imap_host') or ''),quote=True)}" placeholder=mail.my.domain.ru><label>Порт</label><input type=number name=imap_port value="{int(settings.get('imap_port') or 993)}"><label>Логин</label><input name=imap_username value="{html.escape(str(settings.get('imap_username') or ''),quote=True)}"><label>Новый пароль приложения</label><input type=password name=imap_password autocomplete=new-password placeholder="Оставьте пустым, чтобы не менять"><label><input type=checkbox name=imap_ssl value=1 {checked(settings.get('imap_ssl'))}> IMAP через SSL/TLS</label><button class=btn>Сохранить настройки</button></form><form method=post action=/admin/mail/test><label>Куда отправить тестовое письмо</label><input type=email name=test_email value="{html.escape(str(settings.get('mail_from') or 'support@my.domain.ru'),quote=True)}" required><button class="btn soft">Проверить SMTP и IMAP</button></form><p><small>Пароль хранится в базе в зашифрованном виде и никогда не показывается повторно. Для порта 465 обычно используется SSL; для 587 — STARTTLS.</small></p></div>'''
   return self.sendx(200,page('Настройка почты',f'<section class=hero><h1>Настройка почтового сервера</h1><p>Ящик поддержки, регистрационные письма и почтовый клиент.</p></section>{notice}{form}<a class="btn soft backbtn" href=/admin/mail>← Почтовый клиент</a>',True))
  if p=='/admin/mail/message':
   if not self.isadmin():return self.sendx(403,'forbidden')
   uid=parse_qs(urlparse(self.path).query).get('uid',[''])[0]
   try:message=inbox_messages(1,uid)[0]
   except Exception as error:return self.sendx(502,page('Ошибка почты',f'<div class="card box"><h2 class=err>Не удалось открыть письмо</h2><p>{html.escape(str(error))}</p><a class=btn href=/admin/mail>Вернуться</a></div>',True))
   reply=parseaddr(message['from'])[1];quoted='\n\n--- Исходное сообщение ---\n'+message['body'][:4000]
   body=f'''<section class=hero><h1>{html.escape(message['subject'])}</h1><p>{html.escape(message['from'])} · {html.escape(message['date'])}</p></section><div class=card><pre style="white-space:pre-wrap">{html.escape(message['body'])}</pre></div><div class=card><h2>Ответить</h2><form method=post action=/admin/mail/send><input type=hidden name=to value="{html.escape(reply,quote=True)}"><input type=hidden name=subject value="{html.escape('Re: '+message['subject'],quote=True)}"><label>Кому</label><div class=uri>{html.escape(reply)}</div><label>Сообщение</label><textarea name=body minlength=1 maxlength=20000 required></textarea><button class=btn>Отправить ответ</button></form></div>'''
   return self.sendx(200,page('Письмо',body,True))
  if p=='/admin/mail':
   if not self.isadmin():return self.sendx(403,'forbidden')
   settings=mail_settings();inbox_error='';messages=[]
   if settings.get('imap_host'):
    try:messages=inbox_messages(30)
    except Exception as error:inbox_error=f'<div class="callout warn"><b>IMAP недоступен</b><p>{html.escape(str(error))}</p></div>'
   else:inbox_error='<div class="callout warn"><b>IMAP ещё не настроен</b><p>Добавьте сервер входящей почты в настройках.</p></div>'
   rows=''.join(f'''<tr><td>{html.escape(x['date'])}</td><td>{html.escape(x['from'])}</td><td><a class=a href="/admin/mail/message?uid={quote(x['uid'])}">{html.escape(x['subject'])}</a></td></tr>''' for x in messages) or '<tr><td colspan=3>Входящих писем пока нет</td></tr>'
   with db() as c:campaigns=c.execute('SELECT * FROM mail_campaigns ORDER BY id DESC LIMIT 20').fetchall()
   campaign_rows=''.join(f"<tr><td>#{x['id']}</td><td>{html.escape(x['subject'])}</td><td>{html.escape(x['audience'])}</td><td>{html.escape(x['status'])}</td><td>{x['sent']} / {x['total']}</td><td>{x['failed']}</td></tr>" for x in campaigns) or '<tr><td colspan=6>Рассылок ещё нет</td></tr>'
   compose='''<div class=card><h2>Новое письмо</h2><form method=post action=/admin/mail/send><label>Кому</label><input type=email name=to required><label>Тема</label><input name=subject maxlength=200 required><label>Сообщение</label><textarea name=body maxlength=20000 required></textarea><button class=btn>Отправить</button></form></div>'''
   campaign='''<div class=card><h2>Массовая рассылка</h2><form method=post action=/admin/mail/campaign onsubmit="return confirm('Запустить рассылку выбранной аудитории?')"><label>Аудитория</label><select name=audience><option value=marketing>Подписались на новости</option><option value=active>Клиенты с действующей подпиской — сервисное уведомление</option></select><label>Тема</label><input name=subject maxlength=200 required><label>Сообщение</label><textarea name=body maxlength=20000 required></textarea><label class=consent><input type=checkbox name=confirmed value=1 required> Я проверил текст и аудиторию</label><button class=btn>Создать рассылку</button></form></div>'''
   return self.sendx(200,page('Почта',f'''<section class=hero><h1>Почта поддержки</h1><p>{html.escape(str(settings.get('mail_from') or 'support@my.domain.ru'))}</p><a class="btn soft" href=/admin/mail/settings>⚙ Настроить SMTP и IMAP</a></section>{inbox_error}<div class=card><h2>Входящие</h2><div class=tablewrap><table><tr><th>Дата</th><th>Отправитель</th><th>Тема</th></tr>{rows}</table></div></div><div class=manual>{compose}{campaign}</div><br><div class=card><h2>История рассылок</h2><div class=tablewrap><table><tr><th>ID</th><th>Тема</th><th>Аудитория</th><th>Статус</th><th>Отправлено</th><th>Ошибки</th></tr>{campaign_rows}</table></div></div>''',True))
  if p=='/admin/backups':
   if not self.isadmin():return self.sendx(403,'forbidden')
   try:data=backup_control('list');agent=backup_control('agent-status')
   except Exception as error:return self.sendx(503,page('Резервные копии',f'<div class="card box"><h2 class=err>Модуль резервных копий недоступен</h2><p>{html.escape(str(error))}</p></div>',True))
   state=data.get('state') or {};state_name={'queued':'В очереди','running':'Выполняется','success':'Успешно','error':'Ошибка'}.get(state.get('status'),'Нет операций');state_class='ok' if state.get('status')=='success' else ('bad' if state.get('status')=='error' else 'wait')
   status=f'''<div class="callout {state_class}"><b>Последняя операция: {html.escape(state_name)}</b><p>{html.escape(str(state.get('message') or 'Операций ещё не было'))}</p>{'<p>Архив: <code>'+html.escape(str(state.get('archive')))+'</code></p>' if state.get('archive') else ''}</div>'''
   cards=[]
   for item in data.get('archives') or []:
    name=str(item.get('name') or '');valid=bool(item.get('valid'));created=time.strftime('%d.%m.%Y %H:%M',time.localtime(int(item.get('created_at') or 0)))
    restore=f'''<form method=post action=/admin/backups/restore onsubmit="return confirm('Запустить восстановление из {html.escape(name)}? Перед изменением будет создана страховочная копия.')"><input type=hidden name=archive value="{html.escape(name,quote=True)}"><label>Что восстановить</label><select name=scope><option value=all>Сайт и 3x-ui — базы данных</option><option value=site>Только сайт</option><option value=xui>Только 3x-ui</option><option value=full>Полностью: базы, настройки и TLS</option></select><label>Для подтверждения введите имя архива</label><input name=confirmation autocomplete=off placeholder="{html.escape(name,quote=True)}" required><button class=delbtn>Восстановить</button></form>''' if valid else '<p class=err>Архив повреждён или имеет неизвестный формат.</p>'
    cards.append(f'''<article class=card><span class=eyebrow>{created}</span><h2>{html.escape(name)}</h2><p>{format_bytes(int(item.get('size') or 0))} · {'проверен' if valid else 'недоступен'}</p>{restore}</article>''')
   archive_cards=''.join(cards) or '<div class="card box"><h2>Копий пока нет</h2><p>Создайте первую резервную копию кнопкой выше.</p></div>'
   polling='<script>setTimeout(function(){location.reload()},5000)</script>' if state.get('status') in ('queued','running') else ''
   local_state='<b class=ok>Работает</b>' if agent.get('local_timer') else '<b class=bad>Остановлен</b>';agent_state='<b class=ok>Работает</b>' if agent.get('full_agent_timer') else ('<b class=warn>Установлен, но остановлен</b>' if agent.get('full_agent_installed') else '<b class=bad>Не установлен</b>');remote_state='<b class=ok>Настроен</b>' if agent.get('remote_configured') else '<b class=warn>Не подключён</b>'
   remote_button=f'<a class=btn href="{html.escape(BACKUP_PANEL_URL,quote=True)}" target=_blank rel=noopener>Открыть Backup-сервер</a>' if BACKUP_PANEL_URL else '<p><small>URL панели задаётся через <code>BACKUP_PANEL_URL</code>.</small></p>'
   dashboard=f'''<div class=tariff-admin-grid><article class=card><span class=eyebrow>На этом сервере</span><h2>Локальные копии</h2><p>Расписание: {local_state}<br>Архивов: <b>{len(data.get('archives') or [])}</b></p><form method=post action=/admin/backups/create><button class=btn>＋ Создать сейчас</button></form></article><article class=card><span class=eyebrow>Передача наружу</span><h2>Backup-агент</h2><p>Служба: {agent_state}<br>Приёмник: {remote_state}<br>ID: <code>{html.escape(str(agent.get('server_id') or 'не задан'))}</code></p><small>Настройка: <code>sudo home-vpn-setup backup-remote</code> или компонент «Backup-агент».</small></article><article class=card><span class=eyebrow>Центральное хранилище</span><h2>Backup-сервер</h2><p>Независимые конфигурации, серверы и архивы управляются в единой панели хранилища.</p>{remote_button}</article></div>'''
   body=f'''<section class=hero><h1>Резервное копирование</h1><p>Локальные архивы, Backup-агент и центральный Backup-сервер в одном разделе.</p></section>{dashboard}{status}<div class="callout warn"><b>Перед восстановлением</b><p>Пользователи могут потерять изменения, сделанные после выбранной даты. Система автоматически создаст страховочную копию текущего состояния и проверит целостность архива. Полное восстановление также заменяет настройки и TLS-файлы.</p></div><h2>Локальные архивы</h2><div class=tariff-admin-grid>{archive_cards}</div><div class=card><h2>Восстановление через SSH</h2><p>Проверить архивы: <code>sudo home-vpn-restore list</code></p><p>Восстановить обе базы: <code>sudo home-vpn-restore restore ИМЯ_АРХИВА all</code></p><p>Подробная инструкция находится в <code>docs/backup-and-restore.md</code> проекта.</p></div>{polling}'''
   return self.sendx(200,page('Резервные копии',body,True))
  if p=='/admin/payment':
   if not self.isadmin():return self.sendx(403,'forbidden')
   cfg=json.loads(json.dumps(CONFIG));payment=cfg.get('payment') or {};selected_mode=str(payment.get('mode') or 'manual');params=parse_qs(urlparse(self.path).query)
   notice='<div class=notice>Способ оплаты сохранён и уже применяется на сайте и в Telegram-боте.</div>' if params.get('saved') else ''
   credentials=payment_credentials();yk_ready=yookassa_ready();yk_state='<b class=ok>Настроена и готова к включению</b>' if yk_ready else '<b class=bad>Не настроена</b>'
   current='Ручное подтверждение администратором' if selected_mode=='manual' else 'Автоматическая оплата через ЮKassa';webhook=PUBLIC+'/api/payments/yookassa/webhook'
   body=f'''<section class=hero><h1>Настройка оплаты</h1><p>Активен только один способ оплаты. Изменение сразу применяется на сайте и в подключённом Telegram-боте.</p></section>{notice}<div class="callout ok"><b>Сейчас используется: {html.escape(current)}</b></div><div class=tariff-admin-grid><article class=card><span class=eyebrow>Ручной режим</span><h2>Подтверждает администратор</h2><p>Пользователь открывает платёжную ссылку, а подписка выдаётся после вашей проверки поступления денег.</p></article><article class=card><span class=eyebrow>Автоматический режим</span><h2>ЮKassa</h2><p>Платёж создаётся и проверяется сервером, успешный заказ подтверждается автоматически.</p><p>Состояние: {yk_state}</p></article></div><br><div class="card box"><h2>Способ и реквизиты</h2><form method=post action=/admin/config/payment><label>Активный способ</label><select name=mode><option value=manual {'selected' if selected_mode=='manual' else ''}>Ручная оплата — подтверждает администратор</option><option value=yookassa {'selected' if selected_mode=='yookassa' else ''}>ЮKassa — автоматическое подтверждение</option></select><h3>Ручная оплата</h3><label>Название способа</label><input name=provider_name maxlength=80 value="{html.escape(str(payment.get('provider_name') or 'Оплата'),quote=True)}"><label>Ссылка для оплаты</label><input type=url name=url value="{html.escape(str(payment.get('url') or PAYMENT_URL or ''),quote=True)}" placeholder="https://bank.example/payment"><h3>ЮKassa API</h3><label>Shop ID</label><input name=yookassa_shop_id value="{html.escape(credentials['shop_id'],quote=True)}" autocomplete=off><label>Новый секретный ключ</label><input type=password name=yookassa_secret_key autocomplete=new-password placeholder="{'Ключ сохранён — оставьте пустым, чтобы не менять' if credentials['secret_key'] else 'Введите секретный ключ'}"><label>Адрес API</label><input type=url name=yookassa_api_url value="{html.escape(credentials['api_url'],quote=True)}" required><small>Для ЮKassa обычно используется <code>https://api.yookassa.ru/v3</code>.</small><label>URL возврата после оплаты</label><input type=url name=yookassa_return_url value="{html.escape(credentials['return_url'],quote=True)}" placeholder="Пусто = страница заказа HOME-VPN"><button class=btn>Сохранить настройки оплаты</button></form><p><small>Секретный ключ хранится в базе в зашифрованном виде и никогда не показывается повторно. В API Telegram-бота передаются только режим, название и готовые ссылки заказа.</small></p></div><div class="callout warn"><b>Webhook ЮKassa</b><p>Укажите в личном кабинете ЮKassa: <code>{html.escape(webhook)}</code></p></div><div class=callout><b>Обновление из GitHub временно отключено.</b><p>Сайт не выполняет автоматический <code>git pull</code> и не загружает код из GitHub. Локальное обновление администратором остаётся доступно.</p></div>'''
   return self.sendx(200,page('Настройка оплаты',body,True))
  if p=='/admin/config':
   if not self.isadmin():return self.sendx(403,'forbidden')
   cfg=json.loads(json.dumps(CONFIG));branding=cfg.get('branding') or {};pricing=cfg.get('pricing') or {};prices='\n'.join(f'{k}={v}' for k,v in sorted((pricing.get('device_monthly') or {}).items(),key=lambda x:int(x[0])))
   flag_catalog=[('🌐','Другое / без страны'),('🇷🇺','Россия'),('🇵🇱','Польша'),('🇫🇮','Финляндия'),('🇸🇪','Швеция'),('🇳🇱','Нидерланды'),('🇩🇪','Германия'),('🇫🇷','Франция'),('🇬🇧','Великобритания'),('🇺🇸','США'),('🇨🇦','Канада'),('🇨🇭','Швейцария'),('🇪🇪','Эстония'),('🇱🇻','Латвия'),('🇱🇹','Литва'),('🇨🇿','Чехия'),('🇦🇹','Австрия'),('🇪🇸','Испания'),('🇮🇹','Италия'),('🇹🇷','Турция'),('🇯🇵','Япония'),('🇸🇬','Сингапур')]
   def flag_select(selected):
    selected=str(selected or '🌐');items=list(flag_catalog)
    if selected not in {x[0] for x in items}:items.insert(0,(selected,'Текущий пользовательский флаг'))
    return ''.join(f'<option value="{html.escape(flag,quote=True)}" {"selected" if flag==selected else ""}>{html.escape(flag)} {html.escape(label)}</option>' for flag,label in items)
   branding_form=f'''<div class=card><h2>Название и описание</h2><form method=post action=/admin/config/branding><label>Название</label><input name=name maxlength=40 value="{html.escape(str(branding.get('name','HOME-VPN')),quote=True)}" required><label>Слоган</label><input name=tagline maxlength=80 value="{html.escape(str(branding.get('tagline','Домашняя свобода')),quote=True)}"><label>Подзаголовок</label><input name=subtitle maxlength=120 value="{html.escape(str(branding.get('subtitle','Безопасный интернет каждый день')),quote=True)}"><button class=btn>Сохранить оформление</button></form></div>'''
   payment_form='''<div class=card><h2>Оплата</h2><p>Ручной режим и ЮKassa управляются в отдельном разделе.</p><a class=btn href=/admin/payment>Открыть настройку оплаты</a></div>'''
   period_fields=''.join(f'''<div class=card><h3>{html.escape(code)}</h3><label>Название</label><input name="period_{html.escape(code)}_name" value="{html.escape(str(info.get('name',code)),quote=True)}"><label>Дней</label><input type=number min=1 name="period_{html.escape(code)}_days" value="{int(info.get('days',30))}"><label>Множитель цены</label><input type=number min=0 step=.01 name="period_{html.escape(code)}_multiplier" value="{float(info.get('multiplier',1))}"></div>''' for code,info in (pricing.get('periods') or {}).items())
   pricing_form=f'''<div class=card><h2>Тарифы</h2><p>Изменения сразу применяются на сайте и передаются Telegram-боту.</p><form method=post action=/admin/config/pricing><label>Валюта</label><input name=currency value="{html.escape(str(pricing.get('currency','₽')),quote=True)}" required><label>Цены за 1 месяц</label><textarea name=device_prices required placeholder="1=100&#10;2=190">{html.escape(prices)}</textarea><small>Одна строка на вариант: количество устройств = цена. Максимальное число устройств определяется последней строкой.</small><label>Лимит трафика, ГБ</label><input type=number min=0 name=traffic_gb value="{int(pricing.get('traffic_gb',0))}"><small>0 — без ограничений.</small><label><input type=checkbox name=trial_enabled value=1 {'checked' if (pricing.get('trial') or {}).get('enabled',True) else ''}> Пробный тариф включён</label><label>Название пробного тарифа</label><input name=trial_name value="{html.escape(str((pricing.get('trial') or {}).get('name','Пробный')),quote=True)}"><label>Дней</label><input type=number min=1 name=trial_days value="{int((pricing.get('trial') or {}).get('days',3))}"><label>Устройств</label><input type=number min=1 name=trial_devices value="{int((pricing.get('trial') or {}).get('devices',1))}"><h3>Периоды</h3><div class=plans>{period_fields}</div><button class=btn>Сохранить тарифы</button></form></div>'''
   node_forms=[]
   for index,node in enumerate(cfg.get('nodes') or []):
    routes='\n'.join('|'.join((str(r.get('name','')),str(r.get('inbound_id','')),str(r.get('protocol','vless')),str(r.get('transport','tcp')))) for r in node.get('routes') or []);inbounds=','.join(map(str,node.get('inbound_ids') or []))
    delete='' if node.get('primary') else f'''<form method=post action=/admin/config/node/delete onsubmit="return confirm('Удалить ноду {html.escape(str(node.get('name','')))} из конфигурации?')"><input type=hidden name=index value={index}><button class=delbtn>Удалить ноду</button></form>'''
    sync=f'''<form method=post action=/admin/config/node/sync><input type=hidden name=index value={index}><button class="btn soft">↻ Получить inbound и маршруты из 3x-ui</button></form>'''
    node_forms.append(f'''<div class=card><h2>{'⭐ ' if node.get('primary') else ''}{html.escape(str(node.get('name','Нода')))}</h2><form method=post action=/admin/config/node><input type=hidden name=index value={index}><label>ID</label><input name=node_id value="{html.escape(str(node.get('id','')),quote=True)}" required><label>Название</label><input name=name value="{html.escape(str(node.get('name','')),quote=True)}" required><label>Страна</label><input name=country value="{html.escape(str(node.get('country','')),quote=True)}"><label>Флаг</label><select name=flag>{flag_select(node.get('flag','🌐'))}</select><label>URL API 3x-ui</label><input name=panel_url value="{html.escape(str(node.get('panel_url','')),quote=True)}"><label>Переменная окружения с API-токеном</label><input name=token_env value="{html.escape(str(node.get('token_env','XUI_TOKEN')),quote=True)}" readonly><small>Секрет хранится только в /etc/vpn-shop.env. После добавления ноды нажмите синхронизацию.</small><label>Inbound ID через запятую</label><input name=inbound_ids value="{html.escape(inbounds,quote=True)}"><label>Существующий домен подписки</label><input name=subscription_base_url value="{html.escape(str(node.get('subscription_base_url','')),quote=True)}"><label>Вес при распределении</label><input type=number min=1 max=100 name=weight value="{int(node.get('weight',100))}"><label>Маршруты</label><textarea name=routes placeholder="reality|1|vless|tcp">{html.escape(routes)}</textarea><label><input type=checkbox name=primary value=1 {'checked' if node.get('primary') else ''}> Основная нода</label><label><input type=checkbox name=enabled value=1 {'checked' if node.get('enabled',True) else ''}> Включена</label><label><input type=checkbox name=accept_new value=1 {'checked' if node.get('accept_new',True) else ''}> Выдавать новым пользователям</label><label><input type=checkbox name=maintenance value=1 {'checked' if node.get('maintenance',False) else ''}> Техническое обслуживание</label><button class=btn>Сохранить ноду</button></form>{sync}{delete}</div>''')
   new_node=f'''<div class=card><h2>Добавить сервер</h2><form method=post action=/admin/config/node><input type=hidden name=index value=new><label>ID</label><input name=node_id placeholder=pl-2 required><label>Название</label><input name=name placeholder="PL — дополнительный" required><label>Страна</label><input name=country placeholder=PL><label>Флаг</label><select name=flag>{flag_select('🌐')}</select><label>URL API</label><input name=panel_url placeholder="https://node2.my.domain.ru:2053/panel-path"><label>Переменная окружения с API-токеном</label><input name=token_env placeholder=PL_XUI_TOKEN><label>Inbound ID через запятую</label><input name=inbound_ids placeholder=1,2,3><label>Вес при распределении</label><input type=number min=1 max=100 name=weight value=100><label>Маршруты</label><textarea name=routes placeholder="reality|1|vless|tcp&#10;xhttp|2|vless|xhttp"></textarea><label><input type=checkbox name=enabled value=1 checked> Включен</label><label><input type=checkbox name=accept_new value=1 checked> Выдавать новым пользователям</label><button class=btn>Добавить сервер</button></form></div>'''
   params=parse_qs(urlparse(self.path).query);notice='<div class=notice>Настройки сохранены и уже применены.</div>' if params.get('saved') else ''
   console='''<div class="callout warn"><b>Домен и TLS меняются только в консоли.</b><br>Это защищает сертификаты и закрытые ключи от доступа веб-приложения.<br><span class=code>sudo home-vpn-setup tls</span></div>'''
   return self.sendx(200,page('Настройки сервиса',f'''<section class=hero><h1>Настройки {html.escape(BRAND_NAME)}</h1><p>Название, тарифы, лимиты, серверы и маршрутизация.</p></section>{notice}{console}<div class=manual>{branding_form}{payment_form}{pricing_form}</div><br><div class=manual>{''.join(node_forms)}{new_node}</div>''',True))
  if p=='/admin/status':
   if not self.isadmin():return self.sendx(403,'forbidden')
   s=status_data();primary_ok=str(s.get('panel')).lower()=='online' and str(s.get('xray')).lower() in ('online','running','active','true')
   rows=f"<tr><td>{PRIMARY_NODE_FLAG} {html.escape(PRIMARY_NODE_NAME)}</td><td>{badge('online' if primary_ok else 'offline')}</td><td>3x-ui: {badge(s.get('panel'))} · Xray: {badge(s.get('xray'))}</td></tr>"
   rows+=''.join(f"<tr><td>{html.escape(str(n.get('flag') or node_flag(n.get('name'))))} {html.escape(str(n.get('name','Дополнительный сервер')))}</td><td>{badge(n.get('status'))}</td><td>Дополнительная нода</td></tr>" for n in s.get('nodes',[]))
   return self.sendx(200,page('Мониторинг серверов',f"<section class=hero><h1>Мониторинг серверов</h1><p>Состояние основной панели, Xray и дополнительных узлов.</p></section><div class=card><div class=tablewrap><table><tr><th>Узел</th><th>Состояние</th><th>Компоненты</th></tr>{rows}</table></div></div>",True))
  if p=='/admin/servers':
   if not self.isadmin():return self.sendx(403,'forbidden')
   metrics=[node_metrics(node) for node in NODES];rows=''
   for node in metrics:
    state=badge('online' if node.get('online') else 'offline');mode='Обслуживание' if node.get('maintenance') else ('Новых не принимать' if not node.get('accept_new') else 'Принимает новых')
    rows+=f'''<tr><td><b>{html.escape(node['flag'])} {html.escape(str(node['name']))}</b><br><small>{html.escape(str(node['country']))}</small></td><td>{state}<br>{mode}</td><td>{node.get('cpu','—')}%</td><td>{node.get('ram_percent','—')}%</td><td>{node.get('disk_percent','—')}%</td><td>{format_bytes(node.get('network_down',0))} ↓ / {format_bytes(node.get('network_up',0))} ↑</td><td>{node.get('active_users',0)}</td><td>{format_bytes(node.get('traffic_received',0)+node.get('traffic_sent',0))}</td><td>{node.get('weight',100)}</td></tr>'''
   actions='''<div class=card><h2>Массовое обслуживание</h2><p>Режим обслуживания исключает узлы из автоматической выдачи новым пользователям. Действующие подписки не удаляются.</p><div class=admin-actions><form method=post action=/admin/servers/mass><input type=hidden name=action value=maintenance_on><button class=delbtn>Начать обслуживание всех узлов</button></form><form method=post action=/admin/servers/mass><input type=hidden name=action value=maintenance_off><button class=approvebtn>Завершить обслуживание</button></form><a class=navbtn href=/admin/config>Изменить вес и режим отдельного узла</a></div></div>'''
   return self.sendx(200,page('Управление серверами',f'''<section class=hero><h1>Управление серверами</h1><p>Новые пользователи автоматически получают предпочтительный узел с учётом нагрузки, веса и режима обслуживания.</p></section><div class=card><div class=tablewrap><table><tr><th>Страна / узел</th><th>Доступность</th><th>CPU</th><th>RAM</th><th>Диск</th><th>Сеть сейчас</th><th>Активные</th><th>Трафик узла</th><th>Вес</th></tr>{rows}</table></div></div><br>{actions}''',True))
  if p=='/admin/maintenance':
   if not self.isadmin():return self.sendx(403,'forbidden')
   with db() as c:notices=c.execute('SELECT *, (SELECT count(*) FROM maintenance_deliveries d WHERE d.notice_id=maintenance_notices.id) delivered FROM maintenance_notices ORDER BY id DESC LIMIT 50').fetchall()
   rows=''.join(f"<tr><td>#{n['id']}</td><td>{'Плановые' if n['kind']=='planned' else 'Внеплановые'}</td><td>{html.escape(n['message']).replace(chr(10),'<br>')}</td><td>{'Активно' if n['status']=='active' else 'Завершено'}</td><td>{n['delivered']}</td><td>{time.strftime('%d.%m.%Y %H:%M',time.localtime(n['created_at']))}</td><td>{'—' if n['status']=='active' else f'''<form method=post action=/admin/maintenance/delete onsubmit="return confirm('Удалить запись #{n['id']} из истории технических работ?')"><input type=hidden name=notice_id value={n['id']}><button class=delbtn>Удалить</button></form>'''}</td></tr>" for n in notices) or '<tr><td colspan=7>Объявлений ещё нет</td></tr>'
   forms="""<div class=card><h2>Создать уведомление</h2><form method=post action=/admin/maintenance/start><label>Тип работ</label><select name=kind><option value=unplanned>Внеплановые</option><option value=planned>Плановые</option></select><label>Сообщение и сроки</label><textarea name=message minlength=5 maxlength=2000 required placeholder="Опишите работы и укажите сроки"></textarea><button class=btn>Опубликовать уведомление</button></form><div class=admin-actions><form method=post action=/admin/maintenance/complete onsubmit="return confirm('Завершить все активные уведомления?')"><button class=approvebtn>Завершить активные работы</button></form><form method=post action=/admin/maintenance/clear onsubmit="return confirm('Удалить всю историю завершённых технических работ? Это действие нельзя отменить.')"><button class=delbtn>Очистить завершённые</button></form></div></div>"""
   return self.sendx(200,page('Технические работы',f"<section class=hero><h1>Технические работы</h1><p>Уведомление появится на сайте и будет отправлено ботом активным подписчикам.</p></section>{forms}<br><div class=card><h2>История уведомлений</h2><div class=tablewrap><table><tr><th>ID</th><th>Тип</th><th>Сообщение</th><th>Статус</th><th>Доставлено</th><th>Создано</th><th>Действие</th></tr>{rows}</table></div></div>",True))
  if p=='/status':
   s=status_data();primary_ok=str(s.get('panel')).lower()=='online' and str(s.get('xray')).lower() in ('online','running','active','true')
   def server_row(name,state,flag='🌐'):
    working=str(state).lower() in ('online','running','active','true');message='Можно подключаться к этому серверу.' if working else 'Сервер временно недоступен. Выберите другой сервер.'
    return f"<tr><td class=table-title>{html.escape(flag)} {html.escape(str(name))}</td><td>{badge('online' if working else 'offline')}</td><td>{message}</td></tr>"
   server_rows=server_row(PRIMARY_NODE_NAME,primary_ok,PRIMARY_NODE_FLAG)+''.join(server_row(n.get('name','Дополнительный сервер'),n.get('status'),n.get('flag') or node_flag(n.get('name'))) for n in s['nodes'])
   return self.sendx(200,page('Статус серверов',f'<section class=hero><h1>Статус серверов</h1><p>Здесь показано, к каким серверам сейчас можно подключиться.</p></section><div class=card><div class=tablewrap><table class=catalog-table><tr><th>Сервер</th><th>Статус</th><th>Описание</th></tr>{server_rows}</table></div></div><div class=callout>Если выбранный сервер не работает, откройте HAPP и выберите другой сервер со статусом <b>«Работает»</b>.</div>'))
  if p=='/notifications':
   if not self.need():return
   user_id,items=site_notification_items(self.who())
   notices=''.join(f"<div class='notice-item {'unread' if x['unread'] else ''}'>{'<span class=unread-label>Новое</span>' if x['unread'] else ''}<h3>{html.escape(x['title'])}</h3><p>{html.escape(x['message']).replace(chr(10),'<br>')}</p><a class=btn href='{html.escape(x['href'],quote=True)}'>{html.escape(x['action'])}</a></div>" for x in items)
   notices=notices or '<div class="card notice-empty"><h2>Уведомлений пока нет</h2><p>Здесь появятся ответы поддержки, предупреждения об окончании подписки и технических работах.</p></div>'
   mark_site_notifications_read(user_id,[x['key'] for x in items if x['unread']])
   return self.sendx(200,page('Уведомления',f'<section class=hero><h1>Уведомления</h1><p>Ответы поддержки, напоминания о подписках и важные сообщения {brand}.</p></section><div class=notice-list>{notices}</div>'))
  if p=='/account':
   if not self.need():return
   with db() as c:u=c.execute('SELECT * FROM users WHERE username=?',(self.who(),)).fetchone()
   with db() as c:
    c.execute("UPDATE orders SET status='expired',error='Истекло время оплаты' WHERE status IN ('pending','error') AND expires_at IS NOT NULL AND expires_at<=?",(int(time.time()),))
    account_orders=c.execute('SELECT * FROM orders WHERE user_id=? OR (? IS NOT NULL AND telegram_id=?) ORDER BY id DESC',(u['id'],u['telegram_id'],u['telegram_id'])).fetchall();owned={email for x in account_orders for email in (x['xui_email'],x['target_email']) if email}
   latest={}
   for o in account_orders:
    email=o['xui_email'] or o['target_email']
    if email and email not in latest:latest[email]=o
   clients=[x for x in panel_clients() if x.get('email') in owned or (u['telegram_id'] and int(x.get('tgId') or 0)==int(u['telegram_id']))]
   subscription_rows=[];included_orders=set();unfinished={'pending','processing','review','error'}
   for x in clients:
    email=x.get('email','');last=latest.get(email);pay=last['status'] if last else 'active'
    devices=client_devices(email);used,limit=client_usage(x);limit_ip=max(1,int(x.get('limitIp') or 1));expiry=int(x.get('expiryTime') or 0);days_left=max(0,math.ceil((expiry-int(time.time()*1000))/86400000)) if expiry else 0
    traffic=(f'{format_bytes(used)} из {format_bytes(limit)}' if limit else f'{format_bytes(used)} · без лимита')
    last_online=int((x.get('traffic') or {}).get('lastOnline') or 0);last_online_ms=last_online if last_online>10**12 else last_online*1000
    device_list=''.join(f'''<div class=device-row><span><b>{html.escape(device['name'])}</b><br><small>Последняя активность: {date_text(device['last_seen'] if device['last_seen']>10**12 else device['last_seen']*1000) if device['last_seen'] else 'нет данных'}</small></span><form method=post action=/subscription/device/remove onsubmit="return confirm('Отключить это устройство?')"><input type=hidden name=email value="{html.escape(email,quote=True)}"><input type=hidden name=device_kind value={device['kind']}><input type=hidden name=device_source value="{html.escape(device['source_id'],quote=True)}"><button class=delbtn>Отключить</button></form></div>''' for device in devices) or '<small>Подключённых устройств пока нет</small>'
    warning='<div class="callout warn">Достигнут лимит устройств. Отключите ненужное или измените тариф.</div>' if len(devices)>=limit_ip else ''
    order_actions=''
    if last and last['status'] in unfinished:
     included_orders.add(last['id']);pay_button=f"<a class=btn href='/pay/{html.escape(last['token'],quote=True)}'>Оплата</a>" if last['status'] in ('pending','error') else ''
     order_actions=f"{pay_button}<a class='btn soft' href='/pay/{html.escape(last['token'],quote=True)}'>Открыть заказ №{last['id']}</a>{cancel_order_button(last)}"
    suburl=subscription_url(email);actions=f"<div class=sub-actions>{copy_subscription_button(suburl)}<a class='btn soft' href='happ://' onclick=\"navigator.clipboard.writeText('{html.escape(suburl,quote=True)}')\">Открыть в HAPP</a><a class='btn soft' href='/subscription/manage?email={quote(email)}#renew'>Продлить</a><a class='btn soft' href='/subscription/manage?email={quote(email)}#change'>Изменить тариф</a>{order_actions}</div>"
    subscription_rows.append(f"<tr><td><span class=subname>{html.escape(email or 'Подписка')}</span><br><small>Осталось: {days_left} дн.</small></td><td>{badge('online' if x.get('enable') else 'offline')}<br><small>Последнее подключение: {date_text(last_online_ms) if last_online else 'нет данных'}</small></td><td><span class='pay-state {html.escape(pay)}'>{html.escape(payment_label(pay))}</span></td><td><b>{len(devices)} из {limit_ip}</b>{warning}<details><summary>Показать устройства</summary>{device_list}</details></td><td>{traffic}</td><td>{date_text(expiry)}</td><td>{actions}</td></tr>")
   for o in account_orders:
    if o['status'] not in unfinished or o['id'] in included_orders:continue
    plan=order_plan(o);name=o['xui_email'] or o['target_email'] or f"Заказ №{o['id']} · новая подписка";pay_button=f"<a class=btn href='/pay/{html.escape(o['token'],quote=True)}'>Оплата</a>" if o['status'] in ('pending','error') else ''
    actions=f"<div class=sub-actions>{pay_button}<a class='btn soft' href='/pay/{html.escape(o['token'],quote=True)}'>Открыть заказ №{o['id']}</a>{cancel_order_button(o)}</div>"
    subscription_rows.append(f"<tr><td><span class=subname>{html.escape(name)}</span><br><small>{html.escape(str(plan[0]))}</small></td><td>Оформляется</td><td><span class='pay-state {html.escape(o['status'])}'>{html.escape(payment_label(o['status']))}</span></td><td>до {plan[3]}</td><td>{traffic_text()}</td><td>После оплаты</td><td>{actions}</td></tr>")
   rows=''.join(subscription_rows) or '<tr><td colspan=7>Подписок пока нет. Выберите тариф — после оплаты подписка появится здесь автоматически.</td></tr>'
   items=f"<div class=card><h2>Подписки и текущие заказы</h2><p>Действующие подписки и заказы, ожидающие оплаты или проверки, собраны в одном месте.</p><div class=tablewrap><table class=subscription-table><tr><th>Подписка или заказ</th><th>Работа</th><th>Оплата</th><th>Устройства</th><th>Трафик</th><th>Действует до</th><th>Действия</th></tr>{rows}</table></div></div>"
   history=account_orders[:20]
   hr=''.join(f"<tr><td>№{o['id']}</td><td>{html.escape(o['xui_email'] or o['target_email'] or 'создаётся')}</td><td>{'Изменение тарифа' if o['order_kind']=='upgrade' else html.escape(order_plan(o)[0])}<br><small>{'до ' if o['order_kind']=='upgrade' else ''}{order_plan(o)[3]} устр. · {order_plan(o)[1]} дн.</small></td><td>{order_plan(o)[3]}</td><td>{o['amount']} ₽</td><td>{html.escape(payment_label(o['status']))}</td><td>{time.strftime('%d.%m.%Y %H:%M',time.localtime(o['created_at']))}</td><td>{time.strftime('%d.%m.%Y %H:%M',time.localtime(o['reviewed_at'])) if o['reviewed_at'] else '—'}</td><td>{cancel_order_button(o)}</td></tr>" for o in history) or '<tr><td colspan=9>Заказов пока нет</td></tr>'
   refresh='<script>setTimeout(function(){location.reload()},10000)</script>' if any(o['status']=='review' for o in account_orders) else ''
   new_button='<a class="btn" href=/#tariffs>＋ Выбрать тариф для новой подписки</a>'
   return self.sendx(200,page('Мои подписки',f'<section class=hero><h1>Управление подписками</h1><p>Каждая новая подписка имеет отдельную ссылку. Продление добавляет срок только выбранной подписке.</p>{new_button}</section>{items}<br><div class=card><h2>История операций</h2><div class=tablewrap><table><tr><th>Заказ</th><th>Подписка</th><th>Тариф</th><th>Устройства</th><th>Сумма</th><th>Статус</th><th>Создан</th><th>Проверен</th><th>Действие</th></tr>{hr}</table></div></div>{refresh}'))
  if p.startswith('/client-sub/'):
   email=subemail(p.rsplit('/',1)[-1])
   if not email:return self.sendx(404,'subscription not found','text/plain')
   r=xapi('GET','/panel/api/clients/links/'+quote(email));links=r.get('obj') or []
   if not r.get('success') or not links:return self.sendx(404,'subscription not found','text/plain')
   payload=base64.b64encode(('\n'.join(links)).encode());self.send_response(200);self.send_header('Content-Type','text/plain; charset=utf-8');self.send_header('Profile-Title',BRAND_NAME);self.send_header('Profile-Update-Interval','12');self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload);return
  if p=='/api/bot/test-users':
   if self.headers.get('X-API-Key')!=API_SECRET:return self.jout(403,{'error':'forbidden'})
   tgid=int(parse_qs(urlparse(self.path).query).get('telegram_id',[0])[0] or 0)
   if tgid!=ADMIN_TG:return self.jout(403,{'error':'Доступно только администратору'})
   with db() as c:items=[dict(x) for x in c.execute("SELECT id,username FROM users WHERE role='test' AND blocked_at IS NULL ORDER BY username")]
   return self.jout(200,{'users':items})
  if p=='/api/bot/clients':
   if self.headers.get('X-API-Key')!=API_SECRET:return self.jout(403,{'error':'forbidden'})
   query=parse_qs(urlparse(self.path).query);tgid=int(query.get('telegram_id',[0])[0]);test_username=str(query.get('test_username',[''])[0]).strip();items=[]
   if tgid<=0:return self.jout(400,{'error':'bad telegram id'})
   selected=None
   if test_username:
    if tgid!=ADMIN_TG:return self.jout(403,{'error':'Доступно только администратору'})
    with db() as c:selected=c.execute("SELECT id FROM users WHERE username=? AND role='test' AND blocked_at IS NULL",(test_username,)).fetchone()
    if not selected:return self.jout(404,{'error':'Тестовый пользователь не найден'})
   source=account_clients(selected['id'],None) if selected else panel_clients()
   for x in source:
    if selected or int(x.get('tgId') or 0)==tgid:items.append({'email':x.get('email'),'enable':x.get('enable'),'expiryTime':x.get('expiryTime'),'limitIp':x.get('limitIp'),'usedDevices':used_devices(x.get('email','')),'scheduledChanges':[{'limitIp':r['limit_ip'],'effectiveAt':r['effective_at']} for r in scheduled_for(x.get('email',''))],'subscription_url':subscription_url(x.get('email',''))})
   return self.jout(200,{'clients':items})
  if p=='/api/bot/pending-orders':
   if self.headers.get('X-API-Key')!=API_SECRET:return self.jout(403,{'error':'forbidden'})
   q=parse_qs(urlparse(self.path).query);tgid=int(q.get('telegram_id',[0])[0] or 0);admin=q.get('admin',['0'])[0]=='1';test_username=str(q.get('test_username',[''])[0]).strip();selected=None
   if not tgid:return self.jout(400,{'error':'bad telegram id'})
   if admin and tgid!=ADMIN_TG:return self.jout(403,{'error':'Доступно только администратору'})
   if test_username and not admin:
    if tgid!=ADMIN_TG:return self.jout(403,{'error':'Доступно только администратору'})
    with db() as c:selected=c.execute("SELECT id FROM users WHERE username=? AND role='test' AND blocked_at IS NULL",(test_username,)).fetchone()
    if not selected:return self.jout(404,{'error':'Тестовый пользователь не найден'})
   with db() as c:
    c.execute("UPDATE orders SET status='expired',error='Истекло время оплаты' WHERE status IN ('pending','error') AND expires_at IS NOT NULL AND expires_at<=?",(int(time.time()),))
    rows=c.execute("SELECT * FROM orders WHERE status IN ('pending','processing','review','payment_pending','payment_error','error') ORDER BY id DESC LIMIT 20").fetchall() if admin else (c.execute("SELECT * FROM orders WHERE user_id=? AND status IN ('pending','processing','review','payment_pending','payment_error','error') ORDER BY id DESC LIMIT 20",(selected['id'],)).fetchall() if selected else c.execute("SELECT * FROM orders WHERE telegram_id=? AND status IN ('pending','processing','review','payment_pending','payment_error','error') ORDER BY id DESC LIMIT 20",(tgid,)).fetchall())
   items=[]
   for row in rows:
    x=dict(row);x['pay_url']=PUBLIC+'/pay/'+x['token'];x['payment_url']=x['pay_url']+'/start';x['payment_method']=payment_mode();x['payment_reference']=payment_reference(x['id'])
    if x.get('status')=='review' and x.get('xui_email'):
     x['subscription_url']=subscription_url(x['xui_email']);x['subscription_qr_url']=PUBLIC+'/subscription-qr/'+x['token']+'.png'
    items.append(x)
   return self.jout(200,{'orders':items,'admin':admin})
  if p=='/api/bot/status':
   if self.headers.get('X-API-Key')!=API_SECRET:return self.jout(403,{'error':'forbidden'})
   s=status_data();s['checked_at']=int(time.time());return self.jout(200,s)
  if p=='/api/bot/catalog':
   if self.headers.get('X-API-Key')!=API_SECRET:return self.jout(403,{'error':'forbidden'})
   periods={k:{'name':v[0],'days':v[1],'multiplier':v[2]} for k,v in PERIODS.items()};trial=PRICING.get('trial') or {}
   tariffs=[{'code':x['code'],'name':x['name'],'days':x['days'],'devices':x['devices'],'traffic_gb':x['traffic_gb'],'price':x['price'],'trial':bool(x['trial']),'renewal':bool(x['renewal'])} for x in available_tariffs(None)]
   return self.jout(200,{'branding':BRANDING,'currency':CURRENCY,'traffic_gb':TRAFFIC_GB,'max_devices':MAX_DEVICES,'device_monthly':DEVICE_MONTH,'periods':periods,'trial':trial,'tariffs':tariffs,'payment':{'mode':payment_mode(),'name':'ЮKassa' if payment_mode()=='yookassa' else PAYMENT_PROVIDER,'configured':yookassa_ready() if payment_mode()=='yookassa' else True},'nodes':[{'id':x.get('id'),'name':x.get('name'),'country':x.get('country'),'flag':x.get('flag'),'primary':bool(x.get('primary')),'routes':x.get('routes') or []} for x in NODES]})
  if p=='/api/bot/order-status':
   if self.headers.get('X-API-Key')!=API_SECRET:return self.jout(403,{'error':'forbidden'})
   try:order_id=int(parse_qs(urlparse(self.path).query).get('order_id',[0])[0])
   except:return self.jout(400,{'error':'bad order id'})
   with db() as c:o=c.execute('SELECT id,status,xui_email FROM orders WHERE id=?',(order_id,)).fetchone()
   if not o:return self.jout(404,{'error':'Заказ не найден'})
   return self.jout(200,dict(o))
  if p=='/api/bot/profile':
   if self.headers.get('X-API-Key')!=API_SECRET:return self.jout(403,{'error':'forbidden'})
   tgid=int(parse_qs(urlparse(self.path).query).get('telegram_id',[0])[0])
   with db() as c:r=c.execute('SELECT login,tg_username FROM bot_profiles WHERE telegram_id=?',(tgid,)).fetchone()
   return self.jout(200,{'profile':dict(r) if r else None})
  if p=='/api/bot/tickets':
   if self.headers.get('X-API-Key')!=API_SECRET:return self.jout(403,{'error':'forbidden'})
   q=parse_qs(urlparse(self.path).query);tgid=int(q.get('telegram_id',[0])[0] or 0);ticket_id=int(q.get('id',[0])[0] or 0);admin=q.get('admin',['0'])[0]=='1'
   if not tgid:return self.jout(400,{'error':'bad telegram id'})
   if admin and tgid!=ADMIN_TG:return self.jout(403,{'error':'Доступно только администратору'})
   with db() as c:
    if ticket_id:
     ticket=c.execute('SELECT * FROM support_tickets WHERE id=?',(ticket_id,)).fetchone()
     if not ticket:return self.jout(404,{'error':'Тикет не найден'})
     if not admin and int(ticket['telegram_id'])!=tgid:return self.jout(403,{'error':'Это обращение принадлежит другому пользователю'})
     messages=[dict(x) for x in c.execute('SELECT sender,message,created_at FROM support_messages WHERE ticket_id=? ORDER BY id',(ticket_id,))]
     return self.jout(200,{'ticket':dict(ticket),'messages':messages})
    rows=c.execute("SELECT t.*,COALESCE((SELECT message FROM support_messages m WHERE m.ticket_id=t.id ORDER BY m.id DESC LIMIT 1),'') last_message FROM support_tickets t WHERE status!='closed' ORDER BY updated_at DESC LIMIT 50").fetchall() if admin else c.execute("SELECT t.*,COALESCE((SELECT message FROM support_messages m WHERE m.ticket_id=t.id ORDER BY m.id DESC LIMIT 1),'') last_message FROM support_tickets t WHERE telegram_id=? ORDER BY updated_at DESC LIMIT 20",(tgid,)).fetchall()
   return self.jout(200,{'tickets':[dict(x) for x in rows]})
  if p=='/admin/tariffs':
   if not self.isadmin():return self.sendx(403,'forbidden')
   params=parse_qs(urlparse(self.path).query);edit_id=int(params.get('edit',[0])[0] or 0);preview_id=int(params.get('preview',[0])[0] or 0)
   with db() as c:tariffs=c.execute('SELECT t.*,u.username personal_username FROM tariffs t LEFT JOIN users u ON u.id=t.personal_user_id ORDER BY t.archived,t.trial DESC,t.days,t.devices').fetchall();promos=c.execute('SELECT * FROM promo_codes ORDER BY id DESC').fetchall();users=c.execute("SELECT id,username FROM users WHERE role!='admin' ORDER BY username").fetchall()
   current=next((x for x in tariffs if x['id']==edit_id),None);preview=next((x for x in tariffs if x['id']==preview_id),None)
   tariff_cards=[]
   for x in tariffs:
    status='Архив' if x['archived'] else ('Опубликован' if x['active'] and x['effective_at']<=int(time.time()) else 'Запланирован' if x['active'] else 'Черновик');status_class='archived' if x['archived'] else ('published' if status=='Опубликован' else 'scheduled' if status=='Запланирован' else 'draft')
    personal=f'''<span>👤 {html.escape(x['personal_username'])}</span>''' if x['personal_username'] else '<span>👥 Для всех</span>';trial='<span>🎁 Пробный период</span>' if x['trial'] else '';renewal='<span>↻ Для продления</span>' if x['renewal'] else ''
    archive='' if x['archived'] else f'''<form method=post action=/admin/tariffs/archive onsubmit="return confirm('Переместить тариф в архив? Существующие заказы не изменятся.')"><input type=hidden name=tariff_id value={x['id']}><button class=delbtn>В архив</button></form>'''
    tariff_cards.append(f'''<article class="card tariff-admin-card {'archived' if x['archived'] else ''}"><div class=tariff-card-head><div><div class=tariff-code>{html.escape(x['code'])}</div><h2>{html.escape(x['name'])}</h2></div><span class="tariff-status {status_class}">{status}</span></div><div class=tariff-admin-price>{x['price']} <small>{html.escape(CURRENCY)}</small></div><div class=tariff-admin-meta><span>📅 {x['days']} дней</span><span>📱 {x['devices']} устройств</span><span>📊 {'Без лимита' if not x['traffic_gb'] else str(x['traffic_gb'])+' ГБ'}</span>{personal}{trial}{renewal}</div><small>Цена действует с {time.strftime('%d.%m.%Y %H:%M',time.localtime(x['effective_at']))}</small><div class=admin-actions><a class=navbtn href=/admin/tariffs?preview={x['id']}>Предпросмотр</a><a class=navbtn href=/admin/tariffs?edit={x['id']}>Изменить</a>{archive}</div></article>''')
   tariff_cards=''.join(tariff_cards) or '<div class="card box"><h2>Тарифов пока нет</h2><p>Создайте первый тариф в форме ниже.</p></div>'
   user_options='<option value="">Для всех пользователей</option>'+''.join(f'<option value={u["id"]} {"selected" if current and current["personal_user_id"]==u["id"] else ""}>{html.escape(u["username"])}</option>' for u in users)
   effective=time.strftime('%Y-%m-%dT%H:%M',time.localtime(current['effective_at'])) if current else time.strftime('%Y-%m-%dT%H:%M')
   form=f'''<div class=card><h2>{'Изменить тариф' if current else 'Создать тариф'}</h2><form method=post action=/admin/tariffs/save><input type=hidden name=tariff_id value={current['id'] if current else ''}><label>Код тарифа</label><input name=code pattern="[a-zA-Z0-9_-]+" value="{html.escape(current['code'],quote=True) if current else ''}" required><label>Название</label><input name=name value="{html.escape(current['name'],quote=True) if current else ''}" required><div class=user-detail-grid><div><label>Срок, дней</label><input type=number name=days min=1 value={current['days'] if current else 30} required></div><div><label>Устройств</label><input type=number name=devices min=1 max=100 value={current['devices'] if current else 1} required></div><div><label>Трафик, ГБ</label><input type=number name=traffic_gb min=0 value={current['traffic_gb'] if current else 0}></div><div><label>Цена</label><input type=number name=price min=0 value={current['price'] if current else 100} required></div></div><label>Начало действия цены</label><input type=datetime-local name=effective_at value="{effective}" required><label>Персональный тариф</label><select name=personal_user_id>{user_options}</select><label><input type=checkbox name=renewal value=1 {'checked' if not current or current['renewal'] else ''}> Разрешить для продления</label><label><input type=checkbox name=trial value=1 {'checked' if current and current['trial'] else ''}> Пробный период</label><label><input type=checkbox name=active value=1 {'checked' if current and current['active'] else ''}> Опубликовать</label><button class=btn>Сохранить</button></form></div>'''
   preview_html=''
   if preview:preview_html=f'''<div class="card pricing-card"><span class=eyebrow>Предварительный просмотр</span><h2>{html.escape(preview['name'])}</h2><div class=price>{preview['price']} {html.escape(CURRENCY)}</div><ul><li>{preview['days']} дней</li><li>{preview['devices']} устройств</li><li>{'Без лимита трафика' if not preview['traffic_gb'] else str(preview['traffic_gb'])+' ГБ трафика'}</li></ul><button class=btn disabled>Перейти к оплате</button></div>'''
   promo_rows=''.join(f'<tr><td>{html.escape(x["code"])}</td><td>{str(x["discount_percent"])+"%" if x["discount_percent"] else str(x["discount_amount"])+" "+CURRENCY}</td><td>{x["used_count"]}{" / "+str(x["max_uses"]) if x["max_uses"] else ""}</td><td>{"Активен" if x["active"] else "Выключен"}<form method=post action=/admin/promos/toggle><input type=hidden name=promo_id value={x['id']}><input type=hidden name=active value={0 if x['active'] else 1}><button class=navbtn>{'Выключить' if x['active'] else 'Включить'}</button></form></td></tr>' for x in promos) or '<tr><td colspan=4>Промокодов нет</td></tr>'
   promo_form=f'''<div class=card><h2>Скидки и промокоды</h2><form method=post action=/admin/promos/save><label>Промокод</label><input name=code maxlength=32 required><div class=user-detail-grid><div><label>Скидка, %</label><input type=number name=discount_percent min=0 max=100 value=0></div><div><label>Или фиксированная скидка</label><input type=number name=discount_amount min=0 value=0></div><div><label>Максимум использований, 0 — без лимита</label><input type=number name=max_uses min=0 value=0></div></div><button class=btn>Создать промокод</button></form><div class=tablewrap><table><tr><th>Код</th><th>Скидка</th><th>Использовано</th><th>Статус</th></tr>{promo_rows}</table></div></div>'''
   return self.sendx(200,page('Управление тарифами',f'''<section class=hero><h1>Управление тарифами</h1><p>Новые цены применяются только к новым заказам. В уже созданном заказе название, срок, устройства, трафик и сумма сохраняются отдельно.</p></section>{preview_html}<div class=tariff-admin-grid>{tariff_cards}</div><br><div class=user-detail-grid>{form}{promo_form}</div>''',True))
  if p=='/admin/user':
   if not self.isadmin():return self.sendx(403,'forbidden')
   try:user_id=int(parse_qs(urlparse(self.path).query).get('id',[0])[0])
   except:return self.sendx(400,'bad user')
   with db() as c:
    user=c.execute('SELECT * FROM users WHERE id=?',(user_id,)).fetchone()
    if not user:return self.sendx(404,'user not found')
    orders=c.execute('SELECT * FROM orders WHERE user_id=? OR (? IS NOT NULL AND telegram_id=?) ORDER BY id DESC',(user_id,user['telegram_id'],user['telegram_id'])).fetchall()
    tickets=c.execute('SELECT * FROM support_tickets WHERE user_id=? OR (? IS NOT NULL AND telegram_id=?) ORDER BY id DESC',(user_id,user['telegram_id'],user['telegram_id'])).fetchall()
    actions=c.execute('SELECT * FROM admin_actions WHERE user_id=? ORDER BY id DESC LIMIT 100',(user_id,)).fetchall()
    all_users=c.execute("SELECT id,username FROM users WHERE id<>? AND role!='admin' ORDER BY username",(user_id,)).fetchall()
   owned={email for o in orders for email in (o['xui_email'],o['target_email']) if email};clients=[x for x in panel_clients() if x.get('email') in owned or (user['telegram_id'] and int(x.get('tgId') or 0)==int(user['telegram_id']))]
   subscription_cards=[]
   for client in clients:
    email=str(client.get('email') or '');devices=client_devices(email);used,limit=client_usage(client);last=int((client.get('traffic') or {}).get('lastOnline') or 0);last_ms=last if last>10**12 else last*1000
    device_rows=''.join(f'''<div class=device-row><div><b>{html.escape(x['name'])}</b><br><small>{html.escape(x['address'])} · последнее подключение: {date_text(x['last_seen'] if x['last_seen']>10**12 else x['last_seen']*1000) if x['last_seen'] else 'нет данных'}</small></div><form method=post action=/admin/clients/device/remove onsubmit="return confirm('Отвязать это устройство?')"><input type=hidden name=user_id value={user_id}><input type=hidden name=email value="{html.escape(email,quote=True)}"><input type=hidden name=device_kind value={x['kind']}><input type=hidden name=device_source value="{html.escape(x['source_id'],quote=True)}"><button class=delbtn>Отвязать</button></form></div>''' for x in devices) or '<p>Подключённых устройств пока не обнаружено.</p>'
    warning='<p class=warning-limit>Достигнут лимит устройств. Отключите ненужное устройство или увеличьте тариф.</p>' if len(devices)>=int(client.get('limitIp') or 1) else ''
    options=''.join(f'<option value={n} {"selected" if n==int(client.get("limitIp") or 1) else ""}>{n}</option>' for n in range(1,MAX_DEVICES+1))
    subscription_cards.append(f'''<div class=card><h3>{html.escape(email)}</h3><p>{badge('online' if client.get('enable') else 'offline')} · действует до {date_text(client.get('expiryTime'))} · последнее подключение {date_text(last_ms) if last else 'нет данных'}</p><p>Устройства: <b>{len(devices)} из {int(client.get('limitIp') or 1)}</b> · трафик: <b>{format_bytes(used)} из {format_bytes(limit) if limit else 'без ограничений'}</b></p>{warning}<div class=device-list>{device_rows}</div><div class=admin-actions><form method=post action=/admin/clients/limits><input type=hidden name=user_id value={user_id}><input type=hidden name=email value="{html.escape(email,quote=True)}"><label>Устройств</label><select name=limit_ip>{options}</select><label>Трафик, ГБ (0 — без лимита)</label><input type=number min=0 name=traffic_gb value={int(limit/1024/1024/1024) if limit else 0}><button class=approvebtn>Сохранить лимиты</button></form><form method=post action=/admin/clients/extend><input type=hidden name=user_id value={user_id}><input type=hidden name=email value="{html.escape(email,quote=True)}"><label>Добавить дней</label><input type=number name=days min=1 max=3650 value=30><button class=approvebtn>Добавить дни</button></form><form method=post action=/admin/clients/reset-link onsubmit="return confirm('Старая ссылка подписки перестанет работать. Продолжить?')"><input type=hidden name=user_id value={user_id}><input type=hidden name=email value="{html.escape(email,quote=True)}"><button class=delbtn>Сбросить ссылку</button></form><form method=post action=/admin/clients/clear-devices onsubmit="return confirm('Отвязать все устройства этой подписки?')"><input type=hidden name=user_id value={user_id}><input type=hidden name=email value="{html.escape(email,quote=True)}"><button class=delbtn>Отвязать все устройства</button></form></div></div>''')
   order_rows=''.join(f'<tr><td>№{o["id"]}</td><td>{html.escape(order_plan(o)[0])}</td><td>{order_plan(o)[3]}</td><td>{o["amount"]} {html.escape(CURRENCY)}</td><td>{html.escape(payment_label(o["status"]))}</td><td>{time.strftime("%d.%m.%Y %H:%M",time.localtime(o["created_at"]))}</td></tr>' for o in orders) or '<tr><td colspan=6>Заказов нет</td></tr>'
   ticket_rows=''.join(f'<tr><td><a class=a href=/admin/ticket?id={t["id"]}>#{t["id"]}</a></td><td>{html.escape(t["source"] or "site")}</td><td>{html.escape(t["status"])}</td><td>{time.strftime("%d.%m.%Y %H:%M",time.localtime(t["updated_at"]))}</td></tr>' for t in tickets) or '<tr><td colspan=4>Тикетов нет</td></tr>'
   _,notifications=site_notification_items(user['username']);notification_rows=''.join(f'<tr><td>{html.escape(x["title"])}</td><td>{html.escape(x["message"])}</td><td>{"Не прочитано" if x["unread"] else "Прочитано"}</td></tr>' for x in notifications) or '<tr><td colspan=3>Уведомлений нет</td></tr>'
   action_rows=''.join(f'<tr><td>{time.strftime("%d.%m.%Y %H:%M",time.localtime(a["created_at"]))}</td><td>{html.escape(a["admin_username"])}</td><td>{html.escape(a["action"])}</td><td>{html.escape(a["subscription"] or "—")}</td></tr>' for a in actions) or '<tr><td colspan=4>Административных действий нет</td></tr>'
   merge_options=''.join(f'<option value={x["id"]}>{html.escape(x["username"])}</option>' for x in all_users)
   account_actions='' if user['role']=='admin' else f'''<div class="card"><h2>Управление аккаунтом</h2><div class=admin-actions><form method=post action=/admin/users/toggle-block><input type=hidden name=user_id value={user_id}><input type=hidden name=blocked value={0 if user['blocked_at'] else 1}><button class={'approvebtn' if user['blocked_at'] else 'delbtn'}>{'Разблокировать' if user['blocked_at'] else 'Заблокировать аккаунт'}</button></form><form method=post action=/admin/users/merge onsubmit="return confirm('Объединить аккаунты? Заказы, подписки, Telegram и тикеты будут перенесены.')"><input type=hidden name=source_user_id value={user_id}><label>Объединить с аккаунтом</label><select name=target_user_id required>{merge_options}</select><button class=delbtn>Объединить дубли</button></form></div></div>'''
   body=f'''<section class=hero><h1>{html.escape(user['username'])}</h1><p>Telegram: {('@'+html.escape(user['tg_username'])) if user['tg_username'] else (str(user['telegram_id']) if user['telegram_id'] else 'не привязан')} · статус: {'заблокирован' if user['blocked_at'] else 'активен'} · подписок: {len(clients)}</p></section>{account_actions}<div class=user-detail-grid>{''.join(subscription_cards) or '<div class=card><h2>Подписок нет</h2></div>'}</div><br><div class=card><h2>Заказы и платежи</h2><div class=tablewrap><table><tr><th>Заказ</th><th>Тариф</th><th>Устройства</th><th>Сумма</th><th>Статус</th><th>Дата</th></tr>{order_rows}</table></div></div><br><div class=card><h2>История уведомлений</h2><div class=tablewrap><table><tr><th>Тип</th><th>Сообщение</th><th>Статус</th></tr>{notification_rows}</table></div></div><br><div class=card><h2>Тикеты</h2><div class=tablewrap><table><tr><th>ID</th><th>Источник</th><th>Статус</th><th>Обновлён</th></tr>{ticket_rows}</table></div></div><br><div class=card><h2>Административные действия</h2><div class=tablewrap><table><tr><th>Дата</th><th>Администратор</th><th>Действие</th><th>Подписка</th></tr>{action_rows}</table></div></div>'''
   return self.sendx(200,page('Управление пользователем',body,True))
  if p=='/admin':
   if not self.isadmin():return self.sendx(403,page('Доступ запрещён','<div class="card box"><h2 class=err>Требуются права администратора</h2></div>'))
   with db() as c:
    users=[dict(x) for x in c.execute('SELECT id,username,role,created_at,telegram_id,tg_username,twofa_enabled,blocked_at FROM users ORDER BY id DESC')];orders=c.execute('SELECT * FROM orders ORDER BY id DESC LIMIT 100').fetchall();ownership=[dict(x) for x in c.execute('SELECT user_id,telegram_id,xui_email,target_email FROM orders')];profiles=[dict(x) for x in c.execute('SELECT telegram_id,login FROM bot_profiles')]
    total_orders=c.execute('SELECT count(*) FROM orders').fetchone()[0];review_total=c.execute("SELECT count(*) FROM orders WHERE status='review'").fetchone()[0];pending_total=c.execute("SELECT count(*) FROM orders WHERE status IN ('pending','processing','payment_pending','payment_error','error')").fetchone()[0];revenue=c.execute("SELECT COALESCE(sum(amount),0) FROM orders WHERE status='active'").fetchone()[0];open_tickets=c.execute("SELECT count(*) FROM support_tickets WHERE status!='closed'").fetchone()[0];active_notices=c.execute("SELECT count(*) FROM maintenance_notices WHERE status='active'").fetchone()[0]
   clients=panel_clients();by_id={u['id']:u for u in users};by_tg={int(u['telegram_id']):u for u in users if u.get('telegram_id') is not None};by_name={u['username'].lower():u for u in users};email_owner={}
   for row in ownership:
    owner=by_id.get(row.get('user_id')) or by_tg.get(int(row.get('telegram_id') or 0))
    if owner:
     for email in (row.get('xui_email'),row.get('target_email')):
      if email:email_owner.setdefault(email,owner)
   profile_by_tg={int(x['telegram_id']):x for x in profiles};groups={('user',u['id']):{'user':u,'clients':[]} for u in users}
   for client in clients:
    email=str(client.get('email') or '');tgid=int(client.get('tgId') or 0);owner=email_owner.get(email) or by_tg.get(tgid) or by_name.get(email.lower())
    if owner:key=('user',owner['id'])
    elif tgid and tgid in profile_by_tg:key=('telegram',tgid);groups.setdefault(key,{'user':{'id':None,'username':profile_by_tg[tgid]['login'],'role':'telegram','telegram_id':tgid},'clients':[]})
    else:key=('unlinked',0);groups.setdefault(key,{'user':{'id':None,'username':'Без привязки к аккаунту','role':'unlinked','telegram_id':None},'clients':[]})
    groups[key]['clients'].append(client)
   order_counts={}
   for row in ownership:
    owner=by_id.get(row.get('user_id')) or by_tg.get(int(row.get('telegram_id') or 0))
    if owner:order_counts[owner['id']]=order_counts.get(owner['id'],0)+1
   monitor=[]
   for group in groups.values():
    u=group['user'];subscriptions=[]
    for x in sorted(group['clients'],key=lambda z:str(z.get('email') or '')):
     email=str(x.get('email') or '');limit=max(1,min(MAX_DEVICES,int(x.get('limitIp') or 1)));options=''.join(f"<option value={n} {'selected' if n==limit else ''}>{n}</option>" for n in range(1,MAX_DEVICES+1));pending=scheduled_for(email)
     planned=f"<br><small>Запланировано: {pending[-1]['limit_ip']} устройств с {date_text(pending[-1]['effective_at'])}</small>" if pending else ''
     edit=f"<form class=device-edit method=post action=/admin/clients/devices><input type=hidden name=email value='{html.escape(email,quote=True)}'><select name=limit_ip aria-label='Количество устройств'>{options}</select><button class=approvebtn>Сохранить</button></form><form class=extend-edit method=post action=/admin/clients/extend><input type=hidden name=email value='{html.escape(email,quote=True)}'><input type=number name=days min=1 max=3650 value=30 aria-label='Добавить дней'><button class=approvebtn>+ дней</button></form>"
     toggle=f"<form method=post action=/admin/clients/toggle><input type=hidden name=email value='{html.escape(email,quote=True)}'><input type=hidden name=enable value='{0 if x.get('enable') else 1}'><button class='{'delbtn' if x.get('enable') else 'approvebtn'}'>{'Отключить' if x.get('enable') else 'Включить'}</button></form>"
     delete=f"<form method=post action=/admin/clients/delete onsubmit=\"return confirm('Удалить подписку {html.escape(email)}? Доступ будет отключён, история покупок сохранится.')\"><input type=hidden name=email value='{html.escape(email,quote=True)}'><button class=delbtn>Удалить</button></form>"
     subscriptions.append(f"<tr><td><b>{html.escape(email)}</b><br><small>до {date_text(x.get('expiryTime'))}</small></td><td>{used_devices(email)} из {limit}{planned}</td><td>{badge('online' if x.get('enable') else 'offline')}</td><td>{edit}</td><td><div class=admin-actions>{toggle}{delete}</div></td></tr>")
    rows=''.join(subscriptions) or '<tr><td colspan=5>Подписок пока нет</td></tr>';tg=u.get('telegram_id') or 'не привязан';role={'admin':'Администратор','test':'Тестовый пользователь','user':'Пользователь','telegram':'Только Telegram','unlinked':'Владелец не найден'}.get(u.get('role'),u.get('role'))
    if not u.get('id') or u.get('role')=='admin':user_actions='—'
    else:
     detach=f'''<form method=post action=/admin/users/detach-telegram onsubmit="return confirm('Отвязать Telegram от пользователя {html.escape(u['username'])}?')"><input type=hidden name=user_id value={u['id']}><button class=navbtn>Отвязать Telegram</button></form>''' if u.get('telegram_id') else ''
     disable_2fa=f'''<form method=post action=/admin/users/disable-2fa onsubmit="return confirm('Аварийно отключить 2FA у пользователя {html.escape(u['username'])}?')"><input type=hidden name=user_id value={u['id']}><button class=delbtn>Отключить 2FA</button></form>''' if u.get('twofa_enabled') else ''
     reset=f'''<form method=post action=/admin/users/reset-password onsubmit="return confirm('Создать новый пароль для {html.escape(u['username'])}? Старый пароль перестанет работать.')"><input type=hidden name=user_id value={u['id']}><button class=navbtn>Сбросить пароль</button></form>'''
     remove=f'''<form method=post action=/admin/users/delete onsubmit="return confirm('Удалить пользователя {html.escape(u['username'])}, все его подписки, заказы и Telegram-привязку? Это действие нельзя отменить.')"><input type=hidden name=user_id value={u['id']}><button class=delbtn>Удалить пользователя</button></form>'''
     user_actions=f'<div class=admin-actions><a class=approvebtn href=/admin/user?id={u["id"]}>Открыть карточку</a>{detach}{disable_2fa}{reset}{remove}</div>'
    monitor.append(f"<div class=usergroup><div class=userhead><div><h3>{html.escape(u['username'])}</h3><p>{role} · Telegram ID: {tg} · {'заблокирован' if u.get('blocked_at') else 'активен'} · 2FA: {'включена' if u.get('twofa_enabled') else 'выключена'} · Подписок: {len(subscriptions)} · Заказов: {order_counts.get(u.get('id'),0)}</p></div>{user_actions}</div><div class=userbody><div class=tablewrap><table><tr><th>Подписка</th><th>Устройства</th><th>Статус</th><th>Изменить</th><th>Управление</th></tr>{rows}</table></div></div></div>")
   params=parse_qs(urlparse(self.path).query);notice='<div class=notice>Подписка удалена из 3x-ui. История заказов сохранена.</div>' if params.get('subscription_deleted') else ('<div class=notice>Пользователь, его подписки и связанные данные удалены.</div>' if params.get('user_deleted') else (f"<div class=notice>Количество устройств для подписки <b>{html.escape(params.get('client',[''])[0])}</b> обновлено.</div>" if params.get('devices_updated') else ''))
   stats=status_data();monitor_html=''.join(monitor)
   operation_rows=[]
   for o in orders:
    plan=order_plan(o);kind='Изменение тарифа' if o['order_kind']=='upgrade' else ('Продление' if o['order_kind']=='renew' else 'Новая подписка');subscription=html.escape(o['xui_email'] or o['target_email'] or 'создаётся')
    if o['status']=='review':review=f'''<div class=review-actions><form method=post action=/admin/orders/review><input type=hidden name=order_id value={o['id']}><input type=hidden name=action value=approve><button class=approvebtn>Деньги пришли</button></form><form method=post action=/admin/orders/review onsubmit="return confirm('Отклонить оплату и вернуть прежний остаток подписки?')"><input type=hidden name=order_id value={o['id']}><input type=hidden name=action value=reject><button class=delbtn>Не поступили</button></form></div>'''
    elif o['status']=='pending':review=f'''<form method=post action=/admin/orders/cancel onsubmit="return confirm('Отменить заказ №{o['id']}?')"><input type=hidden name=order_id value={o['id']}><button class=delbtn>Отменить</button></form>'''
    elif o['status'] in ('canceled','rejected','expired','deleted'):review=f'''<form method=post action=/admin/orders/delete onsubmit="return confirm('Удалить запись заказа №{o['id']} из истории?')"><input type=hidden name=order_id value={o['id']}><button class=delbtn>Удалить запись</button></form>'''
    else:review='—'
    checked=f"<br><small>Проверен: {time.strftime('%d.%m.%Y %H:%M',time.localtime(o['reviewed_at']))}</small>" if o['reviewed_at'] else ''
    operation_rows.append(f"<tr><td class=table-title>№{o['id']}<br><small>{time.strftime('%d.%m.%Y %H:%M',time.localtime(o['created_at']))}</small></td><td>{html.escape(o['customer'] or '—')}<br><small>{subscription}</small></td><td>{kind}<br><small>{html.escape(str(plan[0]))} · {plan[3]} устр.</small><br><b>{o['amount']} ₽</b> · <span class='pay-state {html.escape(o['status'])}'>{html.escape(payment_label(o['status']))}</span>{checked}</td><td>{review}</td></tr>")
   orr=''.join(operation_rows) or '<tr><td colspan=4>Операций пока нет</td></tr>'
   expiring=sum(1 for x in clients if 0<int(x.get('expiryTime') or 0)-int(time.time()*1000)<=3*86400000)
   review_banner=f"<div class=notice><b>Ожидают проверки: {review_total}</b><p>Для каждого платежа используйте кнопки «Деньги пришли» или «Оплата не поступила» в таблице заказов ниже.</p></div>" if review_total else ''
   stats_html=f"<div class=adminstats><div class=adminstat><b>{len(users)}</b><span>пользователей</span></div><div class=adminstat><b>{len(clients)}</b><span>подписок</span></div><div class=adminstat><b>{review_total}</b><span>проверок оплаты</span></div><div class=adminstat><b>{open_tickets}</b><span>открытых тикетов</span></div><div class=adminstat><b>{expiring}</b><span>истекают за 3 дня</span></div><div class=adminstat><b>{revenue} ₽</b><span>подтверждено</span></div></div>"
   system_notice=f"<div class=callout><b>Система:</b> панель {badge(stats['panel'])} · Xray {badge(stats['xray'])} · ожидают обработки {pending_total} · активных объявлений {active_notices} · всего заказов {total_orders}</div>"
   return self.sendx(200,page('Админ-панель',f"<section class=hero><h1>Панель администратора</h1><p>Полный мониторинг {brand} и управление данными сервиса.</p></section>{stats_html}{system_notice}{system_health_notice()}{notice}{review_banner}<div class='card admin-section' id=users><h2>Пользователи и подписки</h2><p id=subscriptions>Все подписки пользователя собраны в одном блоке. Можно изменить лимит устройств, добавить дни, временно отключить или полностью удалить подписку.</p>{monitor_html}</div><br><div class='card admin-section' id=orders><h2>Заказы, операции и проверка оплаты</h2><p>Показаны последние 100 операций. Полный счётчик заказов: {total_orders}.</p><div class=tablewrap><table class=operations-table><tr><th>Заказ</th><th>Пользователь / подписка</th><th>Тариф / оплата</th><th>Проверка</th></tr>{orr}</table></div></div>",True))
  if p.startswith('/pay/') and p.endswith('/yookassa/return'):
   t=p.split('/')[2];o=getorder(t)
   if not o:return self.sendx(404,'not found')
   tx=yookassa_transaction(o['id'])
   if tx and tx['provider_payment_id']:
    try:sync_yookassa_payment(tx['provider_payment_id'])
    except Exception as error:
     with db() as c:c.execute("UPDATE orders SET error=? WHERE id=?",('Не удалось проверить ЮKassa: '+str(error)[:300],o['id']))
   return self.redir(PUBLIC+'/pay/'+t)
  if p.startswith('/pay/') and (p.endswith('/start') or p.endswith('/manual/start') or p.endswith('/yookassa/start')):
   t=p.split('/')[2];o=expire_order(getorder(t))
   if not o:return self.sendx(404,'not found')
   requested='yookassa' if p.endswith('/yookassa/start') else ('manual' if p.endswith('/manual/start') else payment_mode())
   if requested!=payment_mode():return self.sendx(409,page('Способ оплаты отключён','<div class="card box"><h2 class=err>Этот способ оплаты сейчас отключён</h2><a class=btn href="/pay/'+html.escape(t,quote=True)+'">Вернуться к заказу</a></div>'))
   if requested=='yookassa':
    try:transaction=create_yookassa_payment(o);return self.redir(transaction['confirmation_url'])
    except Exception as error:return self.sendx(502,page('Ошибка ЮKassa',f'<div class="card box"><h2 class=err>Не удалось открыть оплату</h2><p>{html.escape(str(error))}</p><a class=btn href="/pay/{html.escape(t,quote=True)}">Повторить</a></div>'))
   ok,message,status=confirm_payment(o)
   if ok and status in ('review','active'):return self.redir(PAYMENT_URL or PUBLIC+'/pay/'+t)
   return self.redir(PUBLIC+'/pay/'+t)
  if p.startswith('/pay/') and not p.endswith('.png'):
   o=expire_order(getorder(p.split('/')[2]));
   if not o:return self.sendx(404,'not found')
   plan=order_plan(o);plan_traffic='Без ограничений' if not plan[4] else f'{plan[4]} ГБ'
   if o['status']=='active':
    sub=subscription_url(o['xui_email']) if o['xui_email'] else PUBLIC+'/sub/'+o['token'];body=f"<h2>✅ Оплата подтверждена</h2><p><b>{html.escape(o['xui_email'] or plan[0])}</b> · заказ №{o['id']}</p>{scheduled_status(o['xui_email']) if o['xui_email'] else ''}<div class=onboarding><h3>Подключение за 5 шагов</h3><ol><li><a href=/downloads>Скачайте HAPP</a></li><li>Нажмите «Скопировать подписку»</li><li>Нажмите «Открыть HAPP»</li><li>Вставьте ссылку из буфера и подключитесь</li><li>Вернитесь и проверьте соединение</li></ol>{copy_subscription_button(sub)}<a class='btn soft backbtn' href='happ://' onclick=\"navigator.clipboard.writeText('{html.escape(sub,quote=True)}')\">Открыть в HAPP</a><a class='btn soft backbtn' href=/connection-check>Проверить соединение</a></div>"
   elif o['status']=='review':
    sub=subscription_url(o['xui_email']) if o['xui_email'] else PUBLIC+'/sub/'+o['token'];body=f"<h2>⏳ Оплата начата</h2><p>Заказ №{o['id']}. Подписка временно активирована, администратор проверит оплату.</p><h3>QR-код подписки</h3><p>Отсканируйте его в HAPP или скопируйте ссылку.</p><img class=qr src='/subscription-qr/{html.escape(o['token'],quote=True)}.png' alt='QR-код подписки'><div class=uri>{html.escape(sub)}</div>{scheduled_status(o['xui_email']) if o['xui_email'] else ''}{copy_subscription_button(sub)}<a class='btn soft backbtn' href='happ://' onclick=\"navigator.clipboard.writeText('{html.escape(sub,quote=True)}')\">Открыть в HAPP</a>"
   elif o['status']=='payment_pending':
    tx=yookassa_transaction(o['id']);retry=f'<a class=btn href="{html.escape(tx["confirmation_url"],quote=True)}">Вернуться к оплате</a>' if tx and tx['confirmation_url'] else ''
    body=f'<h2>⏳ ЮKassa обрабатывает платёж</h2><p>Заказ №{o["id"]}. Подписка будет выдана автоматически после подтверждения оплаты.</p>{retry}<a class="btn soft backbtn" href="/pay/{html.escape(o["token"],quote=True)}/yookassa/return">Проверить оплату</a>'
   elif o['status']=='payment_error':body=f'<h2 class=err>Платёж получен, но подписка ещё не выдана</h2><p>{html.escape(o["error"] or "Система повторит обработку после следующей проверки.")}</p><a class=btn href="/pay/{html.escape(o["token"],quote=True)}/yookassa/return">Повторить обработку</a><a class="btn soft backbtn" href=/support>Поддержка</a>'
   elif o['status']=='processing':body="<h2>⏳ Подписка обновляется</h2><p>Подождите несколько секунд и обновите страницу.</p>"
   elif o['status']=='rejected':body=("<h2 class=err>Оплата не подтверждена</h2><p>Изменение тарифа отменено. Восстановлено прежнее количество устройств.</p><a class=btn href=/account>Мои подписки</a>" if o['order_kind']=='upgrade' else "<h2 class=err>Оплата не подтверждена</h2><p>Временное продление отменено. Восстановлен срок подписки, который был до заявки на оплату.</p><a class=btn href=/>Выбрать тариф</a>")
   elif o['status']=='canceled':body="<h2>Заказ отменён</h2><p>Администратор больше не сможет подтвердить этот заказ. Если параметры подписки применялись временно, они восстановлены.</p><a class=btn href=/account>Мои подписки</a>"
   elif o['status']=='expired':body="<h2 class=err>Время оплаты истекло</h2><p>На оплату заказа отводится 10 минут. Создайте новый заказ — этот больше нельзя активировать.</p><a class=btn href=/#tariffs>Создать новый заказ</a>"
   elif o['status']=='error':body=f"<h2 class=err>Ошибка</h2><p>{html.escape(o['error'] or '')}</p>{payment_timer(order_deadline(o))}<a class=btn href='/pay/{o['token']}/start'>Повторить оформление</a>"
   elif o['amount']==0:body=f"<h2>Пробный доступ</h2><p>{plan[1]} дня · {plan[3]} устройство · трафик: {plan_traffic}</p><form method=post action='/pay/{o['token']}/confirm'><button class=btn>Активировать бесплатно</button></form>{cancel_order_button(o)}"
   else:
    current=0;old_limit=0
    if o['xui_email']:
     try:
      client=(xapi('GET','/panel/api/clients/get/'+quote(o['xui_email'])).get('obj') or {}).get('client') or {};current=int(client.get('expiryTime') or 0);old_limit=int(client.get('limitIp') or 0)
     except Exception:pass
    kind=o['order_kind'] or ('renew' if o['xui_email'] else 'new');projected=current if kind=='upgrade' else max(int(time.time()*1000),current)+plan[1]*86400000
    action='Изменение тарифа' if kind=='upgrade' else ('Продление подписки' if kind=='renew' else 'Новая подписка')
    device_change=f"{old_limit or 'без лимита'} → {plan[3]}" if o['xui_email'] else str(plan[3])
    remaining,_=upgrade_price(old_limit,plan[3],current);change_note=(f"<br>Доплата рассчитана за оставшиеся <b>{remaining} дн.</b><br>Срок и ссылка подписки не изменятся." if kind=='upgrade' else ("<br>Количество устройств не изменится.<br>Ссылка подписки не изменится." if kind=='renew' else ''))
    tariff=('до '+str(plan[3])+' устройств' if kind=='upgrade' else plan[0]);reference=payment_reference(o['id']);mode=payment_mode();payment_button=f'<a class=btn href="/pay/{o["token"]}/{"yookassa" if mode=="yookassa" else "manual"}/start">💳 Оплатить</a>'
    if mode=='manual':
     configured_note=f'<p>После нажатия откроется настроенная страница: <b>{html.escape(PAYMENT_PROVIDER)}</b>.</p>' if PAYMENT_URL else '<p>Внешняя платёжная ссылка не настроена. Следуйте инструкции владельца сервиса.</p>'
     payment_intro=f'<h2>{html.escape(PAYMENT_PROVIDER)}</h2><div class=payment-warning><h3>⚠️ Обязательно укажите номер заказа</h3><p>Если способ оплаты поддерживает комментарий, укажите:</p><div class=order-number>{html.escape(reference)}</div>{copy_payment_reference_button(o["id"])}{configured_note}<p><b>Если отправили не ту сумму, не выполняйте повторную оплату — обратитесь в поддержку.</b></p><a class="btn soft" href=/support>Обратиться в поддержку</a></div>';after='Подписка будет временно активирована, а платёж отправлен администратору на проверку. После этого отменить заказ нельзя.'
    else:
     readiness='<div class="callout warn"><b>ЮKassa ещё не настроена администратором.</b><p>Добавьте Shop ID и секретный ключ в защищённый файл окружения.</p></div>' if not yookassa_ready() else '<div class=notice><b>Безопасная автоматическая оплата через ЮKassa</b><br>После подтверждения подписка будет выдана автоматически.</div>'
     payment_intro=f'<h2>Оплата через ЮKassa</h2>{readiness}';after='После нажатия вы перейдёте на защищённую страницу ЮKassa. Отменить заказ после начала оплаты нельзя.'
     if not yookassa_ready():payment_button='<button class=btn disabled disabled>ЮKassa не настроена</button>'
    body=f"{payment_intro}{payment_timer(order_deadline(o))}<div class=callout><b>Заказ №{o['id']} · {action}</b><br>Тариф: {tariff}<br>Устройства: {device_change}<br>Трафик: {plan_traffic}<br>Текущий срок: {date_text(current) if current else 'новая подписка'}<br><b>{'Срок остаётся до' if kind=='upgrade' else 'Новый срок: до'} {date_text(projected)}</b>{change_note}<br>Стоимость: <b>{o['amount']} {html.escape(CURRENCY)}</b></div><img class=qr src='/qr/{o['token']}.png'>{payment_button}<small>{after}</small>{cancel_order_button(o)}"
   return self.sendx(200,page('Оплата',f"<div class='card box'>{body}</div>"))
  if p.startswith('/qr/') and p.endswith('.png'):
   t=p.rsplit('/',1)[-1][:-4]
   if not getorder(t):return self.sendx(404,'not found')
   import qrcode;target=payment_destination(t);im=qrcode.make(target);out=io.BytesIO();im.save(out,format='PNG');return self.sendx(200,out.getvalue(),'image/png')
  if p.startswith('/subscription-qr/') and p.endswith('.png'):
   t=p.rsplit('/',1)[-1][:-4];o=getorder(t)
   if not o or o['status'] not in ('active','review') or not o['xui_email']:return self.sendx(404,'subscription not found','text/plain')
   import qrcode;target=subscription_url(o['xui_email']);im=qrcode.make(target);out=io.BytesIO();im.save(out,format='PNG');return self.sendx(200,out.getvalue(),'image/png')
  if p.startswith('/sub/'):
   o=getorder(p.rsplit('/',1)[-1])
   if not o or o['status'] not in ('active','review') or not o['vpn_uri']:return self.sendx(404,'subscription not found','text/plain')
   payload=base64.b64encode(o['vpn_uri'].encode())
   self.send_response(200);self.send_header('Content-Type','text/plain; charset=utf-8');self.send_header('Profile-Title',BRAND_NAME);self.send_header('Profile-Update-Interval','12');self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload);return
  if p=='/api/bot/events':
   if self.headers.get('X-API-Key')!=API_SECRET:return self.jout(403,{'error':'forbidden'})
   after=int(parse_qs(urlparse(self.path).query).get('after',[0])[0])
   with db() as c:
    rows=[dict(x) for x in c.execute("SELECT e.id event_id,e.event_type,e.telegram_id event_tg,e.ticket_id,t.telegram_id ticket_tg,t.customer ticket_customer,t.source ticket_source,t.status ticket_status,(SELECT message FROM support_messages sm WHERE sm.ticket_id=t.id ORDER BY sm.id DESC LIMIT 1) ticket_message,o.* FROM bot_events e LEFT JOIN orders o ON o.id=e.order_id LEFT JOIN support_tickets t ON t.id=e.ticket_id WHERE e.id>? ORDER BY e.id",(after,))];last=c.execute("SELECT COALESCE(MAX(id),0) FROM bot_events").fetchone()[0]
   for x in rows:
    x['telegram_id']=x.pop('event_tg') or x.pop('ticket_tg') or x.get('telegram_id')
    x['subscription_url']=subscription_url(x['xui_email']) if x.get('xui_email') else None
   return self.jout(200,{'events':rows,'last_id':last})
  if p=='/api/bot/expiry-reminders':
   if self.headers.get('X-API-Key')!=API_SECRET:return self.jout(403,{'error':'forbidden'})
   now=int(time.time()*1000);until=now+3*86400000;items=[]
   with db() as c:
    muted={int(x[0]) for x in c.execute('SELECT telegram_id FROM notification_preferences WHERE muted=1')}
    for x in panel_clients():
     tg=int(x.get('tgId') or 0);expiry=int(x.get('expiryTime') or 0);email=x.get('email','')
     if not tg or tg in muted or not x.get('enable') or not (now<expiry<=until):continue
     days_left=max(1,int(math.ceil((expiry-now)/86400000)))
     if days_left not in (1,2,3) or c.execute('SELECT 1 FROM expiry_reminder_days WHERE email=? AND expiry_time=? AND days_left=?',(email,expiry,days_left)).fetchone():continue
     c.execute('INSERT INTO expiry_reminder_days(email,expiry_time,days_left,telegram_id,sent_at) VALUES(?,?,?,?,?)',(email,expiry,days_left,tg,int(time.time())))
     items.append({'telegram_id':tg,'email':email,'expiryTime':expiry,'days_left':days_left})
   return self.jout(200,{'reminders':items})
  if p=='/api/bot/maintenance-pending':
   if self.headers.get('X-API-Key')!=API_SECRET:return self.jout(403,{'error':'forbidden'})
   subscribers=active_telegram_subscribers();items=[]
   with db() as c:
    notices=c.execute("SELECT * FROM maintenance_notices WHERE status='active' ORDER BY id").fetchall()
    for notice in notices:
     delivered={int(x[0]) for x in c.execute('SELECT telegram_id FROM maintenance_deliveries WHERE notice_id=?',(notice['id'],))}
     items.extend({'notice_id':notice['id'],'telegram_id':tg,'kind':notice['kind'],'message':notice['message']} for tg in subscribers if tg not in delivered)
   return self.jout(200,{'items':items})
  if p=='/api/bot/audience':
   if self.headers.get('X-API-Key')!=API_SECRET:return self.jout(403,{'error':'forbidden'})
   with db() as c:
    muted={int(x[0]) for x in c.execute('SELECT telegram_id FROM notification_preferences WHERE muted=1')}
    ids={int(x[0]) for x in c.execute('SELECT telegram_id FROM bot_profiles WHERE telegram_id IS NOT NULL')}
    ids.update(int(x[0]) for x in c.execute('SELECT DISTINCT telegram_id FROM users WHERE telegram_id IS NOT NULL'))
   ids.update(int(x.get('tgId') or 0) for x in panel_clients() if int(x.get('tgId') or 0)>0)
   return self.jout(200,{'telegram_ids':sorted(ids-muted)})
  if p=='/api/migration/subscription-aliases':
   if self.headers.get('X-API-Key')!=API_SECRET:return self.jout(403,{'error':'forbidden'})
   with db() as c:items=[dict(x) for x in c.execute('SELECT client_email,subscription_url,source_node,active,created_at FROM subscription_aliases ORDER BY client_email')]
   return self.jout(200,{'aliases':items})
  self.sendx(404,'not found')
 def do_HEAD(self):return self.do_GET()
 def do_POST(self):
  p=urlparse(self.path).path
  if self.limited('auth' if p in ('/login','/register','/2fa-login') or p==ADMIN_ENTRY or p==TEST_ENTRY else 'general'):return
  if int(self.headers.get('Content-Length','0') or 0)>1024*1024:return self.sendx(413,'Запрос слишком большой.','text/plain; charset=utf-8')
  d=self.data()
  if p=='/api/payments/yookassa/webhook':
   event=str(d.get('event') or '');payment=d.get('object') or {};provider_id=str(payment.get('id') or '')
   if event not in ('payment.succeeded','payment.canceled') or not provider_id:return self.jout(200,{'ok':True})
   try:
    ok,message=sync_yookassa_payment(provider_id)
    if not ok and message=='Платёж не найден':return self.jout(200,{'ok':True})
    if not ok:return self.jout(409,{'ok':False,'error':message})
    return self.jout(200,{'ok':True})
   except Exception as error:return self.jout(503,{'ok':False,'error':'Не удалось перепроверить платёж'})
  if p.startswith('/admin/') and self.isadmin():
   safe={key:str(d.get(key,''))[:80] for key in ('order_id','user_id','email','action','notice_id') if d.get(key,'')!=''}
   security_event('admin_action',self.who(),self.request_ip(),{'path':p,**safe})
  if p==ADMIN_ENTRY:
   with db() as c:u=c.execute("SELECT * FROM users WHERE username=? AND role='admin'",(d.get('username',''),)).fetchone()
   if not password_ok(u,d.get('password','')):
    security_event('login_failed',d.get('username',''),self.request_ip(),{'entry':'admin'})
    return self.sendx(401,page('Вход администратора',f'<div class="card box"><h2 class=err>Неправильный логин или пароль</h2><a class="btn soft" href="{html.escape(ADMIN_ENTRY,quote=True)}">Повторить</a></div>'))
   if u['twofa_enabled']:
    nonce=secrets.token_urlsafe(24)
    with db() as c:c.execute("INSERT INTO auth_sessions(nonce,username,status,expires_at,attempts) VALUES(?,?,'2fa_pending',?,0)",(nonce,u['username'],int(time.time()+300)))
    return self.redir('/2fa-login',f"vpn_2fa={nonce}; Path=/; Max-Age=300; Secure; HttpOnly; SameSite=Strict")
   security_event('login_success',u['username'],self.request_ip(),{'entry':'admin'})
   return self.redir('/mode',f"vpn_session={self.token(u['username'])}; Path=/; Max-Age=86400; Secure; HttpOnly; SameSite=Lax")
  if p==TEST_ENTRY:
   with db() as c:u=c.execute("SELECT * FROM users WHERE username=? AND role='test'",(d.get('username',''),)).fetchone()
   if not password_ok(u,d.get('password','')):
    security_event('login_failed',d.get('username',''),self.request_ip(),{'entry':'test'})
    return self.sendx(401,page('Вход тестового пользователя',f'<div class="card box"><h2 class=err>Неправильный логин или пароль</h2><a class="btn soft" href="{html.escape(TEST_ENTRY,quote=True)}">Повторить</a></div>'))
   if u['twofa_enabled']:
    nonce=secrets.token_urlsafe(24)
    with db() as c:c.execute("INSERT INTO auth_sessions(nonce,username,status,expires_at,attempts) VALUES(?,?,'2fa_pending',?,0)",(nonce,u['username'],int(time.time()+300)))
    return self.redir('/2fa-login',f"vpn_2fa={nonce}; Path=/; Max-Age=300; Secure; HttpOnly; SameSite=Strict")
   security_event('login_success',u['username'],self.request_ip(),{'entry':'test'})
   return self.redir('/',f"vpn_session={self.token(u['username'])}; Path=/; Max-Age=86400; Secure; HttpOnly; SameSite=Lax")
  if p=='/login':
   recovered=False
   with db() as c:
    u=c.execute('SELECT * FROM users WHERE username=? OR lower(email)=lower(?)',(d.get('username',''),d.get('username',''))).fetchone()
    valid=password_ok(u,d.get('password',''))
    if not valid and u and u['role'] not in ('admin','test'):recovered=consume_recovery_code(c,u['id'],d.get('password',''));valid=recovered
   if not valid:
    security_event('login_failed',d.get('username',''),self.request_ip(),{'entry':'public'})
    return self.sendx(401,page('Вход','<div class="card box"><h2 class=err>Неправильный логин, пароль или резервный код</h2><a class="btn soft" href=/login>Повторить</a></div>'))
   if u['role'] in ('admin','test'):
    security_event('login_failed',u['username'],self.request_ip(),{'entry':'public','reason':'restricted_role'})
    return self.sendx(401,page('Вход','<div class="card box"><h2 class=err>Неправильный логин или пароль</h2><a class="btn soft" href=/login>Повторить</a></div>'))
   if u['email'] and not u['email_verified_at']:
    return self.sendx(403,page('Подтвердите почту','<div class="card box"><h2>Почта ещё не подтверждена</h2><p>Откройте письмо от '+html.escape(MAIL_FROM)+' и перейдите по ссылке подтверждения.</p><a class="btn soft" href=/login>Вернуться</a></div>'))
   if u['twofa_enabled'] and not recovered:
    nonce=secrets.token_urlsafe(24)
    with db() as c:c.execute("INSERT INTO auth_sessions(nonce,username,status,expires_at,attempts) VALUES(?,?,'2fa_pending',?,0)",(nonce,u['username'],int(time.time()+300)))
    return self.redir('/2fa-login',f"vpn_2fa={nonce}; Path=/; Max-Age=300; Secure; HttpOnly; SameSite=Strict")
   security_event('login_success',u['username'],self.request_ip(),{'entry':'public','recovery_code':bool(recovered)})
   threading.Thread(target=send_login_notice,args=(dict(u),self.request_ip()),daemon=True).start()
   return self.redir('/mode' if u['role']=='admin' else '/',f"vpn_session={self.token(u['username'])}; Path=/; Max-Age=86400; Secure; HttpOnly; SameSite=Lax")
  if p=='/2fa-login':
   nonce=self.cookie('vpn_2fa');code=d.get('code','')
   with db() as c:
    a=c.execute("SELECT * FROM auth_sessions WHERE nonce=? AND status='2fa_pending' AND used_at IS NULL AND expires_at>?",(nonce,int(time.time()))).fetchone();u=c.execute('SELECT * FROM users WHERE username=?',(a['username'],)).fetchone() if a else None
    if not a or not u or int(a['attempts'] or 0)>=5:return self.sendx(401,page('Проверка 2FA','<div class="card box"><h2 class=err>Сеанс проверки истёк</h2><a class=btn href=/login>Начать вход заново</a></div>'))
    if not verify_totp(u['totp_secret'],code):
     attempts=int(a['attempts'] or 0)+1;c.execute('UPDATE auth_sessions SET attempts=? WHERE nonce=?',(attempts,nonce))
     security_event('twofa_failed',u['username'],self.request_ip(),{'attempts':attempts})
     return self.sendx(401,page('Проверка 2FA',f'<div class="card box"><h2 class=err>Неверный одноразовый код</h2><p>Осталось попыток: {max(0,5-attempts)}</p><a class=btn href=/2fa-login>Повторить</a></div>'))
    c.execute("UPDATE auth_sessions SET status='used',used_at=? WHERE nonce=?",(int(time.time()),nonce))
   security_event('login_success',u['username'],self.request_ip(),{'entry':'2fa'})
   threading.Thread(target=send_login_notice,args=(dict(u),self.request_ip()),daemon=True).start()
   return self.redir('/mode' if u['role']=='admin' else '/',f"vpn_session={self.token(u['username'])}; Path=/; Max-Age=86400; Secure; HttpOnly; SameSite=Lax")
  if p=='/forgot-password':
   email=str(d.get('email','')).strip().lower()
   with db() as c:
    u=c.execute("SELECT * FROM users WHERE lower(email)=lower(?) AND email_verified_at IS NOT NULL AND role='user'",(email,)).fetchone()
    token=create_email_token(c,u['id'],'reset',3600) if u else None
   if u:
    try:send_account_email(u['email'],'reset',token,u['username'])
    except Exception as error:security_event('email_send_failed',u['username'],self.request_ip(),{'kind':'reset','error':str(error)[:200]})
   return self.sendx(200,page('Проверьте почту','<div class="card box"><h2>Проверьте почту</h2><p>Если адрес зарегистрирован и подтверждён, мы отправили ссылку для смены пароля.</p><a class=btn href=/login>Вернуться ко входу</a></div>'))
  if p=='/reset-password':
   token=str(d.get('token',''));new=str(d.get('password',''));confirm=str(d.get('password_confirm',''));valid=len(new)>=8 and any(ch.islower() for ch in new) and any(ch.isupper() for ch in new) and any(ch.isdigit() for ch in new) and any(not ch.isalnum() for ch in new);now=int(time.time())
   if not valid or new!=confirm:return self.sendx(400,page('Ошибка','<div class="card box"><h2 class=err>Пароль должен содержать большие и маленькие буквы, цифру и специальный символ</h2></div>'))
   with db() as c:
    row=c.execute("SELECT t.id,t.user_id,u.username FROM email_tokens t JOIN users u ON u.id=t.user_id WHERE t.token_hash=? AND t.purpose='reset' AND t.used_at IS NULL AND t.expires_at>?",(email_token_hash(token),now)).fetchone()
    if row:set_user_password(c,row['username'],new);c.execute('UPDATE users SET session_epoch=session_epoch+1 WHERE id=?',(row['user_id'],));c.execute('UPDATE auth_sessions SET used_at=? WHERE username=? AND used_at IS NULL',(now,row['username']));c.execute('UPDATE email_tokens SET used_at=? WHERE id=?',(now,row['id']))
   if not row:return self.sendx(400,page('Ошибка','<div class="card box"><h2 class=err>Ссылка недействительна или устарела</h2><a class=btn href=/forgot-password>Запросить новую</a></div>'))
   security_event('password_reset',row['username'],self.request_ip(),{'source':'email'})
   return self.sendx(200,page('Пароль изменён','<div class="card box"><h2>✅ Пароль изменён</h2><a class=btn href=/login>Войти</a></div>'))
  if p in ('/settings/2fa-enable','/settings/2fa-disable','/settings/change-login','/settings/change-password'):
   if not self.need():return
   with db() as c:u=c.execute('SELECT * FROM users WHERE username=?',(self.who(),)).fetchone()
   admin=bool(u and u['role']=='admin')
   if p=='/settings/2fa-enable':
    if not u['totp_secret'] or not verify_totp(u['totp_secret'],d.get('code','')):return self.sendx(400,page('Ошибка 2FA','<div class="card box"><h2 class=err>Неверный одноразовый код</h2><a class=btn href=/settings/security>Повторить</a></div>',admin))
    with db() as c:c.execute('UPDATE users SET twofa_enabled=1 WHERE id=?',(u['id'],))
    return self.redir('/settings/security?twofa_enabled=1')
   if p=='/settings/2fa-disable':
    if not password_ok(u,d.get('current_password','')) or not verify_totp(u['totp_secret'],d.get('code','')):return self.sendx(400,page('Ошибка 2FA','<div class="card box"><h2 class=err>Пароль или одноразовый код неверен</h2><a class=btn href=/settings/security>Повторить</a></div>',admin))
    with db() as c:c.execute('UPDATE users SET twofa_enabled=0,totp_secret=NULL,session_epoch=session_epoch+1 WHERE id=?',(u['id'],))
    return self.redir('/login','vpn_session=; Path=/; Max-Age=0; Secure; HttpOnly; SameSite=Lax')
   if not password_ok(u,d.get('current_password','')):return self.sendx(400,page('Ошибка','<div class="card box"><h2 class=err>Текущий пароль указан неверно</h2><a class=btn href=/settings/security>Повторить</a></div>',admin))
   if p=='/settings/change-login':
    new=str(d.get('new_login','')).strip().lower();valid=8<=len(new)<=32 and new[0].isalnum() and new[-1].isalnum() and any(ch in 'abcdefghijklmnopqrstuvwxyz' for ch in new) and all(ch in 'abcdefghijklmnopqrstuvwxyz0123456789-' for ch in new)
    if not valid:return self.sendx(400,page('Ошибка','<div class="card box"><h2 class=err>Логин: 8–32 символа, обязательны латинские буквы; цифры и дефис разрешены</h2><a class=btn href=/settings/security>Повторить</a></div>',admin))
    try:
     with db() as c:
      if c.execute('SELECT 1 FROM users WHERE lower(username)=lower(?) AND id<>?',(new,u['id'])).fetchone() or (u['telegram_id'] and c.execute('SELECT 1 FROM bot_profiles WHERE lower(login)=lower(?) AND telegram_id<>?',(new,u['telegram_id'])).fetchone()):raise sqlite3.IntegrityError()
      old=u['username'];c.execute('UPDATE users SET username=? WHERE id=?',(new,u['id']));c.execute('UPDATE auth_sessions SET username=? WHERE username=?',(new,old));c.execute('UPDATE orders SET customer=? WHERE user_id=? AND customer=?',(new,u['id'],old))
      if u['telegram_id']:c.execute('UPDATE bot_profiles SET login=? WHERE telegram_id=?',(new,u['telegram_id']))
    except sqlite3.IntegrityError:return self.sendx(409,page('Ошибка','<div class="card box"><h2 class=err>Этот логин уже занят</h2><a class=btn href=/settings/security>Повторить</a></div>',admin))
    return self.redir('/settings/security?login_changed=1',f"vpn_session={self.token(new)}; Path=/; Max-Age=86400; Secure; HttpOnly; SameSite=Lax")
   new=d.get('new_password','');confirm=d.get('confirm_password','');special=any(not ch.isalnum() for ch in new);valid=len(new)>=8 and any(ch.islower() for ch in new) and any(ch.isupper() for ch in new) and any(ch.isdigit() for ch in new) and special
   if not valid or new!=confirm:return self.sendx(400,page('Ошибка','<div class="card box"><h2 class=err>Пароли не совпадают или не отвечают требованиям</h2><a class=btn href=/settings/security>Повторить</a></div>',admin))
   with db() as c:set_user_password(c,u['username'],new);c.execute('UPDATE users SET session_epoch=session_epoch+1 WHERE id=?',(u['id'],));c.execute('DELETE FROM auth_sessions WHERE username=?',(u['username'],))
   return self.redir('/settings/security?password_changed=1',f"vpn_session={self.token(u['username'])}; Path=/; Max-Age=86400; Secure; HttpOnly; SameSite=Lax")
  if p=='/register':
   enabled=mail_enabled();name=d.get('username','').strip().lower();email=str(d.get('email','')).strip().lower();pw=d.get('password','');confirm=d.get('password_confirm','')
   if d.get('terms')!='1':return self.sendx(400,page('Ошибка','<div class="card box"><h2 class=err>Для регистрации необходимо принять условия использования</h2><a class="btn soft" href=/register>Вернуться</a></div>'))
   valid_name=8<=len(name)<=32 and name[0].isalnum() and name[-1].isalnum() and any(ch in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ' for ch in name) and all(ch in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-' for ch in name)
   valid_password=len(pw)>=8 and any(ch.islower() for ch in pw) and any(ch.isupper() for ch in pw) and any(ch.isdigit() for ch in pw) and any(not ch.isalnum() for ch in pw)
   if enabled and not valid_email_address(email):return self.sendx(400,page('Ошибка','<div class="card box"><h2 class=err>Укажите корректный адрес электронной почты</h2><a class="btn soft" href=/register>Вернуться</a></div>'))
   if not valid_name:return self.sendx(400,page('Ошибка','<div class="card box"><h2 class=err>Логин должен содержать 8–32 символа и латинские буквы. Цифры и дефис разрешены</h2><p>Пример: <b>familiaio</b></p><a class="btn soft" href=/register>Вернуться</a></div>'))
   if not valid_password:return self.sendx(400,page('Ошибка','<div class="card box"><h2 class=err>Пароль: минимум 8 символов, цифра, заглавная и строчная буквы, специальный символ</h2><a class="btn soft" href=/register>Вернуться</a></div>'))
   if pw!=confirm:return self.sendx(400,page('Ошибка','<div class="card box"><h2 class=err>Пароли не совпадают</h2><a class="btn soft" href=/register>Вернуться</a></div>'))
   try:
    with db() as c:
     if c.execute('SELECT 1 FROM bot_profiles WHERE lower(login)=lower(?)',(name,)).fetchone() or (email and c.execute('SELECT 1 FROM users WHERE lower(email)=lower(?)',(email,)).fetchone()):raise sqlite3.IntegrityError()
     add_user(c,name,pw,email=email or None);user_id=c.execute('SELECT id FROM users WHERE username=?',(name,)).fetchone()['id'];c.execute('UPDATE users SET email_opt_in=? WHERE id=?',(1 if d.get('email_opt_in')=='1' else 0,user_id));codes=create_recovery_codes(c,user_id);token=create_email_token(c,user_id,'verify',86400) if enabled else None
    if enabled:send_account_email(email,'verify',token,name)
   except sqlite3.IntegrityError:return self.sendx(409,page('Ошибка','<div class="card box"><h2 class=err>Такой логин или адрес почты уже используется</h2><a class="btn soft" href=/register>Вернуться</a></div>'))
   except Exception as error:
    with db() as c:
     created=c.execute("SELECT id FROM users WHERE username=? AND email_verified_at IS NULL",(name,)).fetchone()
     if created:c.execute('DELETE FROM user_recovery_codes WHERE user_id=?',(created['id'],));c.execute('DELETE FROM email_tokens WHERE user_id=?',(created['id'],));c.execute('DELETE FROM users WHERE id=?',(created['id'],))
    security_event('email_send_failed',name,self.request_ip(),{'kind':'verify','error':str(error)[:200]})
    return self.sendx(503,page('Почта временно недоступна','<div class="card box"><h2 class=err>Не удалось отправить письмо</h2><p>Регистрация не завершена. Попробуйте позже или обратитесь в поддержку: '+html.escape(MAIL_REPLY_TO)+'</p><a class="btn soft" href=/register>Повторить</a></div>'))
   security_event('registration',name,self.request_ip(),{'source':'site'})
   if not enabled:
    code_list=''.join(f'<li><code>{html.escape(code)}</code></li>' for code in codes);warning='<div class="callout warn"><b>⚠ Аккаунт пока не привязан к Telegram</b><p>Привяжите Telegram в разделе «Безопасность», чтобы получать уведомления.</p></div>'
    return self.sendx(201,page('Регистрация завершена',f'<div class="card box">{warning}<h2>✅ Регистрация завершена</h2><p>Сохраните данные сейчас.</p><label>Логин</label><div class=uri>{html.escape(name)}</div><label>Пароль</label><div class=uri>{html.escape(pw)}</div><h3>Резервные коды</h3><ul class=recovery-codes>{code_list}</ul><a class=btn href=/login>Перейти ко входу</a></div>'))
   body=f'''<div class="card box"><h2>✉ Подтвердите почту</h2><p>Мы отправили письмо на <b>{html.escape(email)}</b>.</p><p>Перейдите по ссылке в письме в течение 24 часов. После подтверждения можно войти по почте <b>{html.escape(email)}</b> или логину <b>{html.escape(name)}</b>.</p><p>Если письма нет, проверьте папку «Спам».</p><a class=btn href=/login>Перейти ко входу</a></div>'''
   return self.sendx(201,page('Подтвердите почту',body))
  if p=='/guest-support/create':
   name=str(d.get('name','')).strip()[:64];contact=str(d.get('contact','')).strip()[:120];message=str(d.get('message','')).strip()
   if len(name)<2 or not (5<=len(message)<=2000):return self.sendx(400,page('Ошибка','<div class="card box"><h2 class=err>Укажите имя и сообщение от 5 до 2000 символов</h2><a class=btn href=/guest-support>Повторить</a></div>'))
   token=secrets.token_urlsafe(32);now=int(time.time())
   with db() as c:
    cur=c.execute("INSERT INTO support_tickets(telegram_id,customer,status,created_at,updated_at,user_id,source,guest_token,guest_contact) VALUES(0,?,'open',?,?,NULL,'guest',?,?)",(name,now,now,token,contact));ticket_id=cur.lastrowid
    c.execute("INSERT INTO support_messages(ticket_id,sender,message,created_at) VALUES(?,'user',?,?)",(ticket_id,message,now));c.execute("INSERT INTO bot_events(event_type,ticket_id,telegram_id,created_at) VALUES('ticket_created',?,0,?)",(ticket_id,now))
   return self.redir('/guest-support?token='+quote(token))
  if p=='/guest-support/reply':
   token=str(d.get('token','')).strip();message=str(d.get('message','')).strip()
   if not (5<=len(message)<=2000):return self.sendx(400,'bad message')
   with db() as c:
    ticket=c.execute("SELECT * FROM support_tickets WHERE guest_token=? AND source='guest'",(token,)).fetchone()
    if not ticket:return self.sendx(404,'chat not found')
    if ticket['status']=='closed':return self.sendx(409,'chat closed')
    now=int(time.time());c.execute("INSERT INTO support_messages(ticket_id,sender,message,created_at) VALUES(?,'user',?,?)",(ticket['id'],message,now));c.execute("UPDATE support_tickets SET status='open',updated_at=? WHERE id=?",(now,ticket['id']));c.execute("INSERT INTO bot_events(event_type,ticket_id,telegram_id,created_at) VALUES('ticket_user_reply',?,0,?)",(ticket['id'],now))
   return self.redir('/guest-support?token='+quote(token))
  if p=='/admin/test-user':
   if not self.isadmin():return self.sendx(403,'forbidden')
   action=str(d.get('action','update'));name=str(d.get('username','')).strip().lower();password=str(d.get('password',''));confirm=str(d.get('password_confirm',''));valid_name=8<=len(name)<=32 and name[0].isalnum() and name[-1].isalnum() and any(ch in 'abcdefghijklmnopqrstuvwxyz' for ch in name) and all(ch in 'abcdefghijklmnopqrstuvwxyz0123456789-' for ch in name)
   if not valid_name:return self.sendx(400,page('Ошибка','<div class="card box"><h2 class=err>Неверный логин тестового пользователя</h2><a class=btn href=/admin/test-user>Повторить</a></div>',True))
   if password or action=='create':
    valid_password=len(password)>=8 and any(ch.islower() for ch in password) and any(ch.isupper() for ch in password) and any(ch.isdigit() for ch in password) and any(not ch.isalnum() for ch in password)
    if not valid_password or password!=confirm:return self.sendx(400,page('Ошибка','<div class="card box"><h2 class=err>Пароли не совпадают или не отвечают требованиям</h2><a class=btn href=/admin/test-user>Повторить</a></div>',True))
   try:
    with db() as c:
     if c.execute('SELECT 1 FROM users WHERE lower(username)=lower(?)',(name,)).fetchone() and action=='create':raise sqlite3.IntegrityError()
     if action=='create':
      add_user(c,name,password,'test');created_uid=c.execute('SELECT id FROM users WHERE username=?',(name,)).fetchone()['id'];c.execute('INSERT INTO admin_actions(admin_username,user_id,action,details,created_at) VALUES(?,?,?,?,?)',(self.who(),created_uid,'test_user_created',json.dumps({'username':name},ensure_ascii=False),int(time.time())));return self.redir('/admin/test-user?saved=1')
     try:uid=int(d.get('user_id',0))
     except:return self.sendx(400,'bad user id')
     test=c.execute("SELECT * FROM users WHERE id=? AND role='test'",(uid,)).fetchone()
     if not test:return self.sendx(404,'test user not found')
     if c.execute('SELECT 1 FROM users WHERE lower(username)=lower(?) AND id<>?',(name,uid)).fetchone():raise sqlite3.IntegrityError()
     old=test['username'];c.execute('UPDATE users SET username=?,session_epoch=session_epoch+1 WHERE id=?',(name,uid));c.execute('UPDATE auth_sessions SET username=? WHERE username=?',(name,old));c.execute('UPDATE orders SET customer=? WHERE user_id=? AND customer=?',(name,uid,old))
     if password:set_user_password(c,name,password)
   except sqlite3.IntegrityError:return self.sendx(409,page('Ошибка','<div class="card box"><h2 class=err>Этот логин уже занят</h2><a class=btn href=/admin/test-user>Повторить</a></div>',True))
   return self.redir('/admin/test-user?saved=1')
  if p=='/support/create':
   if not self.need():return
   message=str(d.get('message','')).strip()
   if not (5<=len(message)<=2000):return self.sendx(400,page('Ошибка','<div class="card box"><h2 class=err>Описание должно содержать от 5 до 2000 символов</h2><a class="btn soft" href=/support>Вернуться</a></div>'))
   with db() as c:
    u=c.execute('SELECT id,username,telegram_id FROM users WHERE username=?',(self.who(),)).fetchone()
    if not u:return self.redir('/login','vpn_session=; Path=/; Max-Age=0; Secure; HttpOnly')
    now=int(time.time());cur=c.execute("INSERT INTO support_tickets(telegram_id,customer,status,created_at,updated_at,user_id,source) VALUES(?,?,'open',?,?,?,'site')",(int(u['telegram_id'] or 0),u['username'],now,now,u['id']));ticket_id=cur.lastrowid
    c.execute("INSERT INTO support_messages(ticket_id,sender,message,created_at) VALUES(?,'user',?,?)",(ticket_id,message,now));c.execute("INSERT INTO bot_events(event_type,ticket_id,telegram_id,created_at) VALUES('ticket_created',?,?,?)",(ticket_id,int(u['telegram_id'] or 0),now))
   return self.redir('/support/ticket?id='+str(ticket_id))
  if p=='/support/reply':
   if not self.need():return
   try:ticket_id=int(d.get('ticket_id',0));message=str(d.get('message','')).strip()
   except:return self.sendx(400,'bad ticket')
   if not (5<=len(message)<=2000):return self.sendx(400,'bad message')
   with db() as c:
    u=c.execute('SELECT id,telegram_id FROM users WHERE username=?',(self.who(),)).fetchone();ticket=c.execute('SELECT * FROM support_tickets WHERE id=? AND user_id=?',(ticket_id,u['id'] if u else 0)).fetchone()
    if not ticket:return self.sendx(403,'forbidden')
    if ticket['status']=='closed':return self.sendx(409,'ticket closed')
    now=int(time.time());c.execute("INSERT INTO support_messages(ticket_id,sender,message,created_at) VALUES(?,'user',?,?)",(ticket_id,message,now));c.execute("UPDATE support_tickets SET status='open',updated_at=? WHERE id=?",(now,ticket_id));c.execute("INSERT INTO bot_events(event_type,ticket_id,telegram_id,created_at) VALUES('ticket_user_reply',?,?,?)",(ticket_id,int(u['telegram_id'] or 0),now))
   return self.redir('/support/ticket?id='+str(ticket_id))
  if p=='/support/close':
   if not self.need():return
   try:ticket_id=int(d.get('ticket_id',0))
   except:return self.sendx(400,'bad ticket')
   with db() as c:
    u=c.execute('SELECT id,telegram_id FROM users WHERE username=?',(self.who(),)).fetchone();ticket=c.execute('SELECT * FROM support_tickets WHERE id=? AND user_id=?',(ticket_id,u['id'] if u else 0)).fetchone()
    if not ticket:return self.sendx(403,'forbidden')
    now=int(time.time());c.execute("UPDATE support_tickets SET status='closed',updated_at=?,closed_at=? WHERE id=?",(now,now,ticket_id));c.execute("INSERT INTO bot_events(event_type,ticket_id,telegram_id,created_at) VALUES('ticket_closed',?,?,?)",(ticket_id,int(u['telegram_id'] or 0),now))
   return self.redir('/support/ticket?id='+str(ticket_id))
  if p=='/admin/tickets/reply':
   if not self.isadmin():return self.sendx(403,'forbidden')
   try:ticket_id=int(d.get('ticket_id',0));message=str(d.get('message','')).strip()
   except:return self.sendx(400,'bad ticket')
   if not (5<=len(message)<=2000):return self.sendx(400,'bad message')
   with db() as c:
    ticket=c.execute('SELECT * FROM support_tickets WHERE id=?',(ticket_id,)).fetchone()
    if not ticket:return self.sendx(404,'ticket not found')
    if ticket['status']=='closed':return self.sendx(409,'ticket closed')
    now=int(time.time());c.execute("INSERT INTO support_messages(ticket_id,sender,message,created_at) VALUES(?,'admin',?,?)",(ticket_id,message,now));c.execute("UPDATE support_tickets SET status='answered',updated_at=? WHERE id=?",(now,ticket_id))
   return self.redir('/admin/ticket?id='+str(ticket_id))
  if p=='/admin/tickets/close':
   if not self.isadmin():return self.sendx(403,'forbidden')
   try:ticket_id=int(d.get('ticket_id',0))
   except:return self.sendx(400,'bad ticket')
   with db() as c:now=int(time.time());c.execute("UPDATE support_tickets SET status='closed',updated_at=?,closed_at=? WHERE id=?",(now,now,ticket_id))
   return self.redir('/admin/ticket?id='+str(ticket_id))
  if p=='/admin/tickets/delete':
   if not self.isadmin():return self.sendx(403,'forbidden')
   try:ticket_id=int(d.get('ticket_id',0))
   except:return self.sendx(400,'bad ticket')
   with db() as c:
    message_ids=[x[0] for x in c.execute('SELECT id FROM support_messages WHERE ticket_id=?',(ticket_id,))]
    for message_id in message_ids:c.execute('DELETE FROM site_notification_reads WHERE notification_key=?',(f'support:{message_id}',))
    c.execute('DELETE FROM bot_events WHERE ticket_id=?',(ticket_id,));c.execute('DELETE FROM support_messages WHERE ticket_id=?',(ticket_id,));deleted=c.execute('DELETE FROM support_tickets WHERE id=?',(ticket_id,)).rowcount
   destination='/admin/guest-tickets' if d.get('return_to')=='guest' else '/admin/tickets'
   return self.redir(destination) if deleted else self.sendx(404,'ticket not found')
  if p=='/admin/tickets/delete-closed':
   if not self.isadmin():return self.sendx(403,'forbidden')
   with db() as c:
    ticket_ids=[x[0] for x in c.execute("SELECT id FROM support_tickets WHERE status='closed' AND COALESCE(source,'telegram')!='guest'")]
    if ticket_ids:
     marks=','.join('?' for _ in ticket_ids);message_ids=[x[0] for x in c.execute(f'SELECT id FROM support_messages WHERE ticket_id IN ({marks})',ticket_ids)]
     for message_id in message_ids:c.execute('DELETE FROM site_notification_reads WHERE notification_key=?',(f'support:{message_id}',))
     c.execute(f'DELETE FROM bot_events WHERE ticket_id IN ({marks})',ticket_ids);c.execute(f'DELETE FROM support_messages WHERE ticket_id IN ({marks})',ticket_ids);c.execute(f'DELETE FROM support_tickets WHERE id IN ({marks})',ticket_ids)
   return self.redir('/admin/tickets')
  if p=='/admin/backups/create':
   if not self.isadmin():return self.sendx(403,'forbidden')
   try:backup_control('queue-create')
   except Exception as error:return self.sendx(503,page('Ошибка резервного копирования',f'<div class="card box"><h2 class=err>Не удалось запустить резервное копирование</h2><p>{html.escape(str(error))}</p><a class=btn href=/admin/backups>Вернуться</a></div>',True))
   record_admin_action(self.who(),None,None,'backup_created_manually');return self.redir('/admin/backups')
  if p=='/admin/backups/restore':
   if not self.isadmin():return self.sendx(403,'forbidden')
   archive=str(d.get('archive','')).strip();scope=str(d.get('scope','')).strip();confirmation=str(d.get('confirmation','')).strip()
   if confirmation!=archive:return self.sendx(400,page('Подтверждение не совпало','<div class="card box"><h2 class=err>Имя архива введено неверно</h2><p>Восстановление не запускалось.</p><a class=btn href=/admin/backups>Вернуться</a></div>',True))
   if scope not in ('site','xui','all','full'):return self.sendx(400,'bad restore scope')
   try:backup_control('queue-restore',archive,scope)
   except Exception as error:return self.sendx(503,page('Ошибка восстановления',f'<div class="card box"><h2 class=err>Не удалось запустить восстановление</h2><p>{html.escape(str(error))}</p><a class=btn href=/admin/backups>Вернуться</a></div>',True))
   record_admin_action(self.who(),None,None,'backup_restore_started',{'archive':archive,'scope':scope});return self.redir('/admin/backups')
  if p=='/admin/mail/settings':
   if not self.isadmin():return self.sendx(403,'forbidden')
   try:
    current=mail_settings();smtp_host=str(d.get('smtp_host','')).strip()[:255];imap_host=str(d.get('imap_host','')).strip()[:255];smtp_port=int(d.get('smtp_port',587));imap_port=int(d.get('imap_port',993));mail_from=str(d.get('mail_from','')).strip().lower();reply_to=str(d.get('reply_to','')).strip().lower()
    if not smtp_host or any(ch.isspace() for ch in smtp_host) or (imap_host and any(ch.isspace() for ch in imap_host)) or not (1<=smtp_port<=65535 and 1<=imap_port<=65535) or not valid_email_address(mail_from) or not valid_email_address(reply_to):raise ValueError('Проверьте адреса серверов, порты и почтовые адреса')
    smtp_password=str(d.get('smtp_password','')) or str(current.get('smtp_password') or '');imap_password=str(d.get('imap_password','')) or str(current.get('imap_password') or '')
    values=(smtp_host,smtp_port,str(d.get('smtp_username','')).strip()[:254],encrypt_mail_secret(smtp_password),1 if d.get('smtp_starttls')=='1' else 0,1 if d.get('smtp_ssl')=='1' else 0,imap_host,imap_port,str(d.get('imap_username','')).strip()[:254],encrypt_mail_secret(imap_password),1 if d.get('imap_ssl')=='1' else 0,mail_from,reply_to,int(time.time()))
    with db() as c:c.execute('INSERT INTO mail_settings(id,smtp_host,smtp_port,smtp_username,smtp_password_enc,smtp_starttls,smtp_ssl,imap_host,imap_port,imap_username,imap_password_enc,imap_ssl,mail_from,reply_to,updated_at) VALUES(1,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET smtp_host=excluded.smtp_host,smtp_port=excluded.smtp_port,smtp_username=excluded.smtp_username,smtp_password_enc=excluded.smtp_password_enc,smtp_starttls=excluded.smtp_starttls,smtp_ssl=excluded.smtp_ssl,imap_host=excluded.imap_host,imap_port=excluded.imap_port,imap_username=excluded.imap_username,imap_password_enc=excluded.imap_password_enc,imap_ssl=excluded.imap_ssl,mail_from=excluded.mail_from,reply_to=excluded.reply_to,updated_at=excluded.updated_at',values)
    record_admin_action(self.who(),None,None,'mail_settings_updated',{'smtp_host':smtp_host,'smtp_port':smtp_port,'imap_host':imap_host,'imap_port':imap_port,'mail_from':mail_from})
   except Exception as error:return self.sendx(400,page('Ошибка почты',f'<div class="card box"><h2 class=err>Не удалось сохранить настройки</h2><p>{html.escape(str(error))}</p><a class=btn href=/admin/mail/settings>Вернуться</a></div>',True))
   return self.redir('/admin/mail/settings?saved=1')
  if p=='/admin/mail/test':
   if not self.isadmin():return self.sendx(403,'forbidden')
   target=str(d.get('test_email','')).strip().lower()
   if not valid_email_address(target):return self.sendx(400,'bad email')
   try:
    send_email(target,f'{BRAND_NAME}: проверка почты',f'SMTP настроен и работает. Время проверки: {time.strftime("%d.%m.%Y %H:%M")}')
    if mail_settings().get('imap_host'):
     client=imap_connection();client.logout()
   except Exception as error:return self.sendx(502,page('Ошибка соединения',f'<div class="card box"><h2 class=err>Проверка не пройдена</h2><p>{html.escape(str(error))}</p><a class=btn href=/admin/mail/settings>Вернуться</a></div>',True))
   return self.redir('/admin/mail/settings?tested=1')
  if p=='/admin/mail/send':
   if not self.isadmin():return self.sendx(403,'forbidden')
   target=str(d.get('to','')).strip().lower();subject=str(d.get('subject','')).strip()[:200];body=str(d.get('body','')).strip()[:20000]
   if not valid_email_address(target) or not subject or '\r' in subject or '\n' in subject or not body:return self.sendx(400,page('Ошибка письма','<div class="card box"><h2 class=err>Заполните получателя, тему и сообщение</h2><a class=btn href=/admin/mail>Вернуться</a></div>',True))
   try:send_email(target,subject,body)
   except Exception as error:return self.sendx(502,page('Ошибка отправки',f'<div class="card box"><h2 class=err>Письмо не отправлено</h2><p>{html.escape(str(error))}</p><a class=btn href=/admin/mail>Вернуться</a></div>',True))
   record_admin_action(self.who(),None,None,'mail_sent',{'to':target,'subject':subject});return self.redir('/admin/mail?sent=1')
  if p=='/admin/mail/campaign':
   if not self.isadmin():return self.sendx(403,'forbidden')
   audience=str(d.get('audience',''));subject=str(d.get('subject','')).strip()[:200];body=str(d.get('body','')).strip()[:20000]
   if audience not in ('marketing','active') or d.get('confirmed')!='1' or not subject or '\r' in subject or '\n' in subject or not body:return self.sendx(400,'bad campaign')
   with db() as c:
    if audience=='marketing':recipients=[x[0] for x in c.execute("SELECT DISTINCT lower(email) FROM users WHERE email_verified_at IS NOT NULL AND email_opt_in=1 AND blocked_at IS NULL AND email IS NOT NULL")]
    else:recipients=[x[0] for x in c.execute("SELECT DISTINCT lower(u.email) FROM users u JOIN orders o ON o.user_id=u.id WHERE u.email_verified_at IS NOT NULL AND u.blocked_at IS NULL AND u.email IS NOT NULL AND o.status IN ('active','review')")]
    cur=c.execute("INSERT INTO mail_campaigns(subject,body,audience,status,total,created_by,created_at) VALUES(?,?,?,'queued',?,?,?)",(subject,body,audience,len(recipients),self.who(),int(time.time())));campaign_id=cur.lastrowid
    c.executemany('INSERT INTO mail_campaign_recipients(campaign_id,email) VALUES(?,?)',[(campaign_id,email) for email in recipients])
   record_admin_action(self.who(),None,None,'mail_campaign_created',{'campaign_id':campaign_id,'audience':audience,'total':len(recipients)});threading.Thread(target=deliver_campaign,args=(campaign_id,),daemon=True).start();return self.redir('/admin/mail?campaign=1')
  if p=='/admin/maintenance/start':
   if not self.isadmin():return self.sendx(403,'forbidden')
   kind=str(d.get('kind','unplanned'));message=str(d.get('message','')).strip()
   if kind not in ('planned','unplanned') or not (5<=len(message)<=2000):return self.sendx(400,'bad notice')
   with db() as c:c.execute("INSERT INTO maintenance_notices(kind,message,status,created_at) VALUES(?,?,'active',?)",(kind,message,int(time.time())))
   return self.redir('/admin/maintenance')
  if p=='/admin/config/branding':
   if not self.isadmin():return self.sendx(403,'forbidden')
   try:
    cfg=json.loads(json.dumps(CONFIG));name=str(d.get('name','')).strip();
    if not name:raise ValueError('Название не может быть пустым')
    cfg['branding']={'name':name[:40],'tagline':str(d.get('tagline','')).strip()[:80],'subtitle':str(d.get('subtitle','')).strip()[:120]};save_runtime_config(cfg)
   except Exception as error:return self.sendx(400,page('Ошибка настройки',f'<div class="card box"><h2 class=err>Не удалось сохранить оформление</h2><p>{html.escape(str(error))}</p><a class=btn href=/admin/config>Вернуться</a></div>',True))
   return self.redir('/admin/config?saved=1')
  if p=='/admin/tariffs/save':
   if not self.isadmin():return self.sendx(403,'forbidden')
   try:
    tariff_id=int(d.get('tariff_id') or 0);code=str(d.get('code','')).strip().lower();name=str(d.get('name','')).strip();days=int(d.get('days',0));devices=int(d.get('devices',0));traffic_gb=int(d.get('traffic_gb',0));price=int(d.get('price',0));effective_at=int(time.mktime(time.strptime(str(d.get('effective_at','')),'%Y-%m-%dT%H:%M')));personal=int(d.get('personal_user_id')) if d.get('personal_user_id') else None
    if not code or not name or days<1 or devices<1 or traffic_gb<0 or price<0:raise ValueError('Проверьте параметры тарифа')
    now=int(time.time())
    with db() as c:
     values=(code,name,days,devices,traffic_gb,price,1 if d.get('active')=='1' else 0,effective_at,1 if d.get('renewal')=='1' else 0,1 if d.get('trial')=='1' else 0,personal,now)
     if tariff_id:c.execute('UPDATE tariffs SET code=?,name=?,days=?,devices=?,traffic_gb=?,price=?,active=?,effective_at=?,renewal=?,trial=?,personal_user_id=?,updated_at=? WHERE id=?',values+(tariff_id,))
     else:c.execute('INSERT INTO tariffs(code,name,days,devices,traffic_gb,price,active,archived,effective_at,renewal,trial,personal_user_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,0,?,?,?,?,?,?)',values[:-1]+(now,now))
    record_admin_action(self.who(),personal,None,'tariff_saved',{'code':code,'price':price,'effective_at':effective_at})
   except Exception as error:return self.sendx(400,page('Ошибка тарифа',f'<div class="card box"><h2 class=err>Не удалось сохранить тариф</h2><p>{html.escape(str(error))}</p><a class=btn href=/admin/tariffs>Вернуться</a></div>',True))
   return self.redir('/admin/tariffs')
  if p=='/admin/tariffs/archive':
   if not self.isadmin():return self.sendx(403,'forbidden')
   try:tariff_id=int(d.get('tariff_id',0))
   except:return self.sendx(400,'bad tariff')
   with db() as c:tariff=c.execute('SELECT code FROM tariffs WHERE id=?',(tariff_id,)).fetchone();c.execute('UPDATE tariffs SET archived=1,active=0,updated_at=? WHERE id=?',(int(time.time()),tariff_id))
   record_admin_action(self.who(),None,None,'tariff_archived',{'code':tariff['code'] if tariff else tariff_id});return self.redir('/admin/tariffs')
  if p=='/admin/promos/save':
   if not self.isadmin():return self.sendx(403,'forbidden')
   try:
    code=str(d.get('code','')).strip().upper();percent=max(0,min(100,int(d.get('discount_percent',0))));amount=max(0,int(d.get('discount_amount',0)));max_uses=max(0,int(d.get('max_uses',0)))
    if not code or (not percent and not amount):raise ValueError('Укажите код и размер скидки')
    with db() as c:c.execute('INSERT INTO promo_codes(code,discount_percent,discount_amount,active,max_uses,created_at) VALUES(?,?,?,?,?,?)',(code,percent,amount,1,max_uses,int(time.time())))
    record_admin_action(self.who(),None,None,'promo_created',{'code':code})
   except Exception as error:return self.sendx(400,page('Ошибка промокода',f'<div class="card box"><h2 class=err>Не удалось создать промокод</h2><p>{html.escape(str(error))}</p><a class=btn href=/admin/tariffs>Вернуться</a></div>',True))
   return self.redir('/admin/tariffs')
  if p=='/admin/promos/toggle':
   if not self.isadmin():return self.sendx(403,'forbidden')
   try:promo_id=int(d.get('promo_id',0));active=1 if d.get('active')=='1' else 0
   except:return self.sendx(400,'bad promo')
   with db() as c:c.execute('UPDATE promo_codes SET active=? WHERE id=?',(active,promo_id))
   record_admin_action(self.who(),None,None,'promo_toggled',{'id':promo_id,'active':active});return self.redir('/admin/tariffs')
  if p=='/admin/config/payment':
   if not self.isadmin():return self.sendx(403,'forbidden')
   mode=str(d.get('mode') or '').strip().lower()
   if mode not in ('manual','yookassa'):return self.sendx(400,'bad payment mode')
   try:
    old=payment_credentials();shop_id=str(d.get('yookassa_shop_id') or '').strip()[:100];secret=str(d.get('yookassa_secret_key') or '') or old['secret_key'];api_url=str(d.get('yookassa_api_url') or 'https://api.yookassa.ru/v3').strip().rstrip('/');return_url=str(d.get('yookassa_return_url') or '').strip();manual_url=str(d.get('url') or '').strip()
    for label,value,required in (('Адрес API',api_url,True),('URL возврата',return_url,False),('Ссылка ручной оплаты',manual_url,False)):
     if (required or value) and urlparse(value).scheme!='https':raise ValueError(label+' должен начинаться с https://')
    if mode=='yookassa' and (not shop_id or not secret):raise ValueError('Для включения ЮKassa укажите Shop ID и секретный ключ')
    with db() as c:c.execute('INSERT INTO payment_settings(id,shop_id,secret_key_enc,api_url,return_url,updated_at) VALUES(1,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET shop_id=excluded.shop_id,secret_key_enc=excluded.secret_key_enc,api_url=excluded.api_url,return_url=excluded.return_url,updated_at=excluded.updated_at',(shop_id,encrypt_mail_secret(secret),api_url,return_url,int(time.time())))
    cfg=json.loads(json.dumps(CONFIG));payment=cfg.setdefault('payment',{});payment['mode']=mode;payment['provider_name']=str(d.get('provider_name') or 'Оплата').strip()[:80] or 'Оплата';payment['url']=manual_url
    save_runtime_config(cfg);record_admin_action(self.who(),None,None,'payment_settings_changed',{'mode':mode,'provider_name':payment['provider_name'],'shop_id_set':bool(shop_id),'api_url':api_url});return self.redir('/admin/payment?saved=1')
   except Exception as error:return self.sendx(400,page('Ошибка настройки оплаты',f'<div class="card box"><h2 class=err>Не удалось сохранить настройки</h2><p>{html.escape(str(error))}</p><a class=btn href=/admin/payment>Вернуться</a></div>',True))
  if p=='/admin/config/pricing':
   if not self.isadmin():return self.sendx(403,'forbidden')
   try:
    cfg=json.loads(json.dumps(CONFIG));pricing=cfg.setdefault('pricing',{});prices={}
    for line in str(d.get('device_prices','')).splitlines():
     if not line.strip():continue
     key,value=[x.strip() for x in line.split('=',1)];prices[str(int(key))]=int(value)
    expected=[str(x) for x in range(1,max(map(int,prices))+1)]
    if sorted(prices,key=int)!=expected or any(v<0 for v in prices.values()):raise ValueError('Укажите последовательные варианты устройств от 1 без пропусков')
    pricing['currency']=str(d.get('currency','₽')).strip()[:8] or '₽';pricing['device_monthly']=prices;pricing['max_devices']=len(prices);pricing['traffic_gb']=max(0,int(d.get('traffic_gb',0)))
    trial_cfg=pricing.setdefault('trial',{});trial_cfg.update({'enabled':d.get('trial_enabled')=='1','name':str(d.get('trial_name','Пробный')).strip()[:80] or 'Пробный','days':max(1,int(d.get('trial_days',3))),'devices':max(1,min(len(prices),int(d.get('trial_devices',1))))})
    for code,info in pricing.setdefault('periods',{}).items():info['name']=str(d.get(f'period_{code}_name',info.get('name',code))).strip()[:80] or code;info['days']=max(1,int(d.get(f'period_{code}_days',info.get('days',30))));info['multiplier']=max(0,float(d.get(f'period_{code}_multiplier',info.get('multiplier',1))))
    save_runtime_config(cfg)
   except Exception as error:return self.sendx(400,page('Ошибка настройки',f'<div class="card box"><h2 class=err>Не удалось сохранить тарифы</h2><p>{html.escape(str(error))}</p><a class=btn href=/admin/config>Вернуться</a></div>',True))
   return self.redir('/admin/config?saved=1')
  if p=='/admin/config/node':
   if not self.isadmin():return self.sendx(403,'forbidden')
   try:
    cfg=json.loads(json.dumps(CONFIG));nodes=cfg.setdefault('nodes',[]);index=d.get('index','new');node={} if index=='new' else nodes[int(index)]
    inbound_ids=[int(x.strip()) for x in str(d.get('inbound_ids','')).split(',') if x.strip()];routes=[]
    for line in str(d.get('routes','')).splitlines():
     if not line.strip():continue
     parts=[x.strip() for x in line.split('|')]
     if len(parts)<2:raise ValueError('Маршрут: имя|inbound_id|протокол|транспорт')
     routes.append({'name':parts[0],'inbound_id':int(parts[1]),'protocol':parts[2] if len(parts)>2 else 'vless','transport':parts[3] if len(parts)>3 else 'tcp','enabled':True})
    node.update({'id':str(d.get('node_id','')).strip(),'name':str(d.get('name','')).strip(),'country':str(d.get('country','')).strip().upper(),'flag':str(d.get('flag','🌐')).strip() or '🌐','panel_url':str(d.get('panel_url','')).strip().rstrip('/'),'token_env':str(d.get('token_env','XUI_TOKEN')).strip(),'inbound_ids':inbound_ids,'subscription_base_url':str(d.get('subscription_base_url','')).strip().rstrip('/'),'routes':routes,'enabled':d.get('enabled')=='1','primary':d.get('primary')=='1','weight':max(1,min(1000,int(d.get('weight',100)))),'accept_new':d.get('accept_new')=='1','maintenance':d.get('maintenance')=='1'})
    if not node['id'] or not node['name']:raise ValueError('ID и название обязательны')
    if node['panel_url'] and os.getenv(node['token_env']):
     discovered=discover_node_routes(node)
     if discovered:node['routes']=discovered;node['inbound_ids']=[x['inbound_id'] for x in discovered if x.get('enabled',True)]
    if any(x.get('id')==node['id'] for i,x in enumerate(nodes) if index=='new' or i!=int(index)):raise ValueError('ID ноды уже используется')
    if index=='new':
     if not nodes:node['primary']=True
     nodes.append(node)
    if node['primary']:
     for other in nodes:other['primary']=other is node
    if not any(x.get('primary') for x in nodes):raise ValueError('Нельзя убрать единственную основную ноду')
    save_runtime_config(cfg)
   except Exception as error:return self.sendx(400,page('Ошибка настройки',f'<div class="card box"><h2 class=err>Не удалось сохранить ноду</h2><p>{html.escape(str(error))}</p><a class=btn href=/admin/config>Вернуться</a></div>',True))
   return self.redir('/admin/config?saved=1')
  if p=='/admin/config/node/delete':
   if not self.isadmin():return self.sendx(403,'forbidden')
   try:
    cfg=json.loads(json.dumps(CONFIG));index=int(d.get('index',-1));node=cfg['nodes'][index]
    if node.get('primary'):raise ValueError('Основную ноду удалить нельзя')
    cfg['nodes'].pop(index);save_runtime_config(cfg)
   except Exception as error:return self.sendx(400,page('Ошибка настройки',f'<div class="card box"><h2 class=err>Не удалось удалить ноду</h2><p>{html.escape(str(error))}</p><a class=btn href=/admin/config>Вернуться</a></div>',True))
   return self.redir('/admin/config?saved=1')
  if p=='/admin/servers/mass':
   if not self.isadmin():return self.sendx(403,'forbidden')
   action=str(d.get('action',''))
   if action not in ('maintenance_on','maintenance_off'):return self.sendx(400,'bad action')
   cfg=json.loads(json.dumps(CONFIG));maintenance=action=='maintenance_on'
   for node in cfg.get('nodes') or []:node['maintenance']=maintenance;node['accept_new']=not maintenance
   save_runtime_config(cfg);record_admin_action(self.who(),None,None,action)
   return self.redir('/admin/servers')
  if p=='/admin/config/node/sync':
   if not self.isadmin():return self.sendx(403,'forbidden')
   try:
    cfg=json.loads(json.dumps(CONFIG));index=int(d.get('index',-1));node=cfg['nodes'][index];routes=discover_node_routes(node)
    if not routes:raise ValueError('В 3x-ui не найдено ни одного inbound')
    node['routes']=routes;node['inbound_ids']=[x['inbound_id'] for x in routes if x.get('enabled',True)];save_runtime_config(cfg)
   except Exception as error:return self.sendx(400,page('Ошибка синхронизации',f'<div class="card box"><h2 class=err>Не удалось получить данные 3x-ui</h2><p>{html.escape(str(error))}</p><p>Проверьте URL ноды и наличие переменной окружения с API-токеном.</p><a class=btn href=/admin/config>Вернуться</a></div>',True))
   return self.redir('/admin/config?saved=1')
  if p=='/admin/maintenance/complete':
   if not self.isadmin():return self.sendx(403,'forbidden')
   with db() as c:c.execute("UPDATE maintenance_notices SET status='completed',completed_at=? WHERE status='active'",(int(time.time()),))
   return self.redir('/admin/maintenance')
  if p=='/admin/maintenance/delete':
   if not self.isadmin():return self.sendx(403,'forbidden')
   try:notice_id=int(d.get('notice_id',0))
   except:return self.sendx(400,'bad notice id')
   with db() as c:
    notice=c.execute('SELECT status FROM maintenance_notices WHERE id=?',(notice_id,)).fetchone()
    if not notice:return self.sendx(404,'notice not found')
    if notice['status']=='active':return self.sendx(409,page('Нельзя удалить','<div class="card box"><h2 class=err>Сначала завершите активные технические работы</h2><a class=btn href=/admin/maintenance>Вернуться</a></div>',True))
    c.execute('DELETE FROM maintenance_deliveries WHERE notice_id=?',(notice_id,));c.execute('DELETE FROM site_notification_reads WHERE notification_key=?',(f'maintenance:{notice_id}',));c.execute('DELETE FROM maintenance_notices WHERE id=?',(notice_id,))
   return self.redir('/admin/maintenance')
  if p=='/admin/maintenance/clear':
   if not self.isadmin():return self.sendx(403,'forbidden')
   with db() as c:
    notice_ids=[x[0] for x in c.execute("SELECT id FROM maintenance_notices WHERE status='completed'")]
    if notice_ids:
     marks=','.join('?' for _ in notice_ids);c.execute(f'DELETE FROM maintenance_deliveries WHERE notice_id IN ({marks})',notice_ids)
     for notice_id in notice_ids:c.execute('DELETE FROM site_notification_reads WHERE notification_key=?',(f'maintenance:{notice_id}',))
     c.execute(f'DELETE FROM maintenance_notices WHERE id IN ({marks})',notice_ids)
   return self.redir('/admin/maintenance')
  if p=='/api/bot/telegram-auth':
   if self.headers.get('X-API-Key')!=API_SECRET:return self.jout(403,{'error':'forbidden'})
   nonce=d.get('nonce','');tgid=int(d.get('telegram_id',0) or 0);tgname=str(d.get('tg_username','')).lstrip('@')[:32]
   if not nonce or not tgid:return self.jout(400,{'error':'bad request'})
   with db() as c:
    a=c.execute("SELECT * FROM auth_sessions WHERE nonce=? AND status IN ('pending','admin_pending') AND expires_at>?",(nonce,int(time.time()))).fetchone()
    if not a:return self.jout(400,{'error':'Ссылка устарела. Начните вход на сайте заново.'})
    u=c.execute('SELECT * FROM users WHERE telegram_id=?',(tgid,)).fetchone()
    if a['status']=='admin_pending' and (not u or u['role']!='admin'):return self.jout(403,{'error':'Эта ссылка предназначена только для администратора'})
    if a['status']=='pending' and u and u['role'] in ('admin','test'):return self.jout(403,{'error':'Вход через общую форму запрещён'})
    if not u:
     username='tg_'+str(tgid);add_user(c,username,secrets.token_urlsafe(32),'user');c.execute('UPDATE users SET telegram_id=?,tg_username=? WHERE username=?',(tgid,tgname or None,username))
    else:username=u['username'];c.execute('UPDATE users SET tg_username=? WHERE id=?',(tgname or u['tg_username'],u['id']))
    magic=secrets.token_urlsafe(32);c.execute("UPDATE auth_sessions SET magic=?,username=?,telegram_id=?,status='ready' WHERE nonce=?",(magic,username,tgid,nonce))
   return self.jout(200,{'ok':True,'new_user':not bool(u),'login_url':PUBLIC+'/tg-login/'+magic})
  if p=='/api/bot/link-account':
   if self.headers.get('X-API-Key')!=API_SECRET:return self.jout(403,{'error':'forbidden'})
   nonce=str(d.get('nonce',''));tgid=int(d.get('telegram_id',0) or 0);tgname=str(d.get('tg_username','')).lstrip('@')[:32]
   with db() as c:
    a=c.execute("SELECT * FROM auth_sessions WHERE nonce=? AND status='link_pending' AND expires_at>?",(nonce,int(time.time()))).fetchone()
    if not a:return self.jout(400,{'error':'Ссылка устарела. Начните привязку на сайте заново.'})
    u=c.execute('SELECT * FROM users WHERE username=?',(a['username'],)).fetchone();owner=c.execute('SELECT username FROM users WHERE telegram_id=?',(tgid,)).fetchone()
    if not u:return self.jout(404,{'error':'Аккаунт сайта не найден'})
    if owner and owner['username']!=u['username']:return self.jout(409,{'error':'Этот Telegram уже связан с другим аккаунтом'})
    c.execute('UPDATE users SET telegram_id=?,tg_username=? WHERE id=?',(tgid,tgname or None,u['id']))
    c.execute('UPDATE orders SET telegram_id=?,tg_username=? WHERE user_id=? OR (? IS NOT NULL AND lower(email)=lower(?))',(tgid,'@'+tgname if tgname else None,u['id'],u['email'],u['email']))
    orders=c.execute('SELECT DISTINCT xui_email FROM orders WHERE telegram_id=? AND xui_email IS NOT NULL',(tgid,)).fetchall()
    c.execute("UPDATE auth_sessions SET status='linked',telegram_id=?,used_at=? WHERE nonce=?",(tgid,int(time.time()),nonce))
   updated=0
   for row in orders:
    try:
     obj=xapi('GET','/panel/api/clients/get/'+quote(row['xui_email'])).get('obj') or {};client=obj.get('client') or {}
     if client:
      client['tgId']=tgid;r=xapi('POST','/panel/api/clients/update/'+quote(row['xui_email']),client_payload(client))
      if r.get('success'):updated+=1
    except Exception:pass
   return self.jout(200,{'ok':True,'username':u['username'],'clients_updated':updated})
  if p=='/api/bot/link-client':
   if self.headers.get('X-API-Key')!=API_SECRET:return self.jout(403,{'error':'forbidden'})
   tgid=int(d.get('telegram_id',0) or 0);proof=str(d.get('subscription','')).strip()
   match=next((x for x in panel_clients() if x.get('subId') and x['subId'] in proof),None)
   if not tgid or not match:return self.jout(404,{'error':'Подписка не найдена. Отправьте полную ссылку существующей подписки из программы VPN.'})
   obj=xapi('GET','/panel/api/clients/get/'+quote(match['email'])).get('obj') or {};client=obj.get('client') or {};client['tgId']=tgid
   r=xapi('POST','/panel/api/clients/update/'+quote(match['email']),client_payload(client))
   if not r.get('success'):return self.jout(502,{'error':'Ошибка привязки подписки'})
   return self.jout(200,{'ok':True,'email':match['email'],'subscription_url':subscription_url(match['email'])})
  if p=='/api/migration/subscription-alias':
   if self.headers.get('X-API-Key')!=API_SECRET:return self.jout(403,{'error':'forbidden'})
   email=str(d.get('email','')).strip();url=str(d.get('subscription_url','')).strip();source=str(d.get('source_node','')).strip()[:64];active=1 if d.get('active',True) else 0
   parsed=urlparse(url)
   if not email or parsed.scheme not in ('http','https') or not parsed.netloc:return self.jout(400,{'error':'Требуются email клиента и корректная HTTP(S)-ссылка подписки'})
   try:exists=any(x.get('email')==email for x in panel_clients())
   except Exception:return self.jout(502,{'error':'Не удалось проверить подписку'})
   if not exists:return self.jout(404,{'error':'Подписка не найдена. Проверьте исходную ссылку.'})
   with db() as c:c.execute('INSERT INTO subscription_aliases(client_email,subscription_url,source_node,active,created_at) VALUES(?,?,?,?,?) ON CONFLICT(client_email) DO UPDATE SET subscription_url=excluded.subscription_url,source_node=excluded.source_node,active=excluded.active',(email,url,source or None,active,int(time.time())))
   return self.jout(200,{'ok':True,'email':email,'subscription_url':subscription_url(email),'source_node':source or None,'active':bool(active)})
  if p=='/api/bot/link-order':
   if self.headers.get('X-API-Key')!=API_SECRET:return self.jout(403,{'error':'forbidden'})
   token=str(d.get('token',''));tgid=int(d.get('telegram_id',0) or 0);tgname=str(d.get('tg_username','')).lstrip('@')[:32]
   o=getorder(token)
   if not o or not tgid:return self.jout(404,{'error':'Заказ не найден'})
   if o['telegram_id'] and int(o['telegram_id'])!=tgid:return self.jout(409,{'error':'Заказ уже привязан к другому Telegram-аккаунту'})
   if ('plan_trial' in o.keys() and o['plan_trial']) or o['plan']=='trial':
    with db() as c:
     claim=c.execute('SELECT order_id FROM trial_claims WHERE telegram_id=?',(tgid,)).fetchone()
    if claim and claim['order_id']!=o['id']:return self.jout(409,{'error':'Пробный период для этого Telegram-аккаунта уже использован'})
   if o['xui_email']:
    obj=xapi('GET','/panel/api/clients/get/'+quote(o['xui_email'])).get('obj') or {};client=obj.get('client') or {}
    if client:
     client['tgId']=tgid;r=xapi('POST','/panel/api/clients/update/'+quote(o['xui_email']),client_payload(client))
     if not r.get('success'):return self.jout(502,{'error':'Ошибка привязки подписки'})
   with db() as c:c.execute('UPDATE orders SET telegram_id=?,tg_username=?,customer=? WHERE id=?',(tgid,'@'+tgname if tgname else None,'@'+tgname if tgname else 'Telegram '+str(tgid),o['id']))
   o=expire_order(getorder(token))
   return self.jout(200,{'ok':True,'order_id':o['id'],'status':o['status'],'pay_url':PUBLIC+'/pay/'+token,'payment_url':PUBLIC+'/pay/'+token+'/start','payment_method':payment_mode(),'expires_at':order_deadline(o),'active':o['status']=='active'})
  if p=='/api/bot/notifications':
   if self.headers.get('X-API-Key')!=API_SECRET:return self.jout(403,{'error':'forbidden'})
   tgid=int(d.get('telegram_id',0) or 0);muted=1 if d.get('muted') else 0
   if not tgid:return self.jout(400,{'error':'bad telegram id'})
   with db() as c:c.execute('INSERT INTO notification_preferences(telegram_id,muted,updated_at) VALUES(?,?,?) ON CONFLICT(telegram_id) DO UPDATE SET muted=excluded.muted,updated_at=excluded.updated_at',(tgid,muted,int(time.time())))
   return self.jout(200,{'ok':True,'muted':bool(muted)})
  if p=='/api/bot/maintenance':
   if self.headers.get('X-API-Key')!=API_SECRET:return self.jout(403,{'error':'forbidden'})
   action=str(d.get('action','start'))
   if action=='start':
    kind=str(d.get('kind','unplanned'));message=str(d.get('message','')).strip()
    if kind not in ('planned','unplanned') or len(message)<5:return self.jout(400,{'error':'Некорректное уведомление'})
    with db() as c:cur=c.execute("INSERT INTO maintenance_notices(kind,message,status,created_at) VALUES(?,?,'active',?)",(kind,message,int(time.time())))
    return self.jout(201,{'ok':True,'notice_id':cur.lastrowid})
   if action=='complete':
    with db() as c:
     active=[x[0] for x in c.execute("SELECT id FROM maintenance_notices WHERE status='active'")]
     recipients=sorted({int(x[0]) for x in c.execute("SELECT DISTINCT telegram_id FROM maintenance_deliveries WHERE notice_id IN (SELECT id FROM maintenance_notices WHERE status='active')")})
     c.execute("UPDATE maintenance_notices SET status='completed',completed_at=? WHERE status='active'",(int(time.time()),))
    return self.jout(200,{'ok':True,'completed':len(active),'telegram_ids':recipients})
   return self.jout(400,{'error':'bad action'})
  if p=='/api/bot/maintenance-delivered':
   if self.headers.get('X-API-Key')!=API_SECRET:return self.jout(403,{'error':'forbidden'})
   try:notice_id=int(d.get('notice_id',0));tgid=int(d.get('telegram_id',0))
   except:return self.jout(400,{'error':'bad delivery'})
   if not notice_id or not tgid:return self.jout(400,{'error':'bad delivery'})
   with db() as c:c.execute('INSERT OR IGNORE INTO maintenance_deliveries(notice_id,telegram_id,delivered_at) VALUES(?,?,?)',(notice_id,tgid,int(time.time())))
   return self.jout(200,{'ok':True})
  if p=='/api/bot/tickets/create':
   if self.headers.get('X-API-Key')!=API_SECRET:return self.jout(403,{'error':'forbidden'})
   tgid=int(d.get('telegram_id',0) or 0);message=str(d.get('message','')).strip();customer=str(d.get('customer','')).strip()[:64]
   if not tgid or not (5<=len(message)<=2000):return self.jout(400,{'error':'Опишите проблему текстом от 5 до 2000 символов'})
   now=int(time.time())
   with db() as c:
    cur=c.execute("INSERT INTO support_tickets(telegram_id,customer,status,created_at,updated_at) VALUES(?,?,'open',?,?)",(tgid,customer or 'Telegram '+str(tgid),now,now));ticket_id=cur.lastrowid
    c.execute("INSERT INTO support_messages(ticket_id,sender,message,created_at) VALUES(?,'user',?,?)",(ticket_id,message,now))
   return self.jout(201,{'ok':True,'ticket_id':ticket_id,'status':'open'})
  if p=='/api/bot/tickets/reply':
   if self.headers.get('X-API-Key')!=API_SECRET:return self.jout(403,{'error':'forbidden'})
   tgid=int(d.get('telegram_id',0) or 0);ticket_id=int(d.get('ticket_id',0) or 0);message=str(d.get('message','')).strip()
   if not (5<=len(message)<=2000):return self.jout(400,{'error':'Сообщение должно содержать от 5 до 2000 символов'})
   with db() as c:
    ticket=c.execute('SELECT * FROM support_tickets WHERE id=?',(ticket_id,)).fetchone()
    if not ticket:return self.jout(404,{'error':'Тикет не найден'})
    admin=tgid==ADMIN_TG
    if not admin and int(ticket['telegram_id'])!=tgid:return self.jout(403,{'error':'Это обращение принадлежит другому пользователю'})
    if ticket['status']=='closed':return self.jout(409,{'error':'Тикет уже закрыт'})
    now=int(time.time());sender='admin' if admin else 'user';status='answered' if admin else 'open'
    c.execute('INSERT INTO support_messages(ticket_id,sender,message,created_at) VALUES(?,?,?,?)',(ticket_id,sender,message,now));c.execute('UPDATE support_tickets SET status=?,updated_at=? WHERE id=?',(status,now,ticket_id))
   return self.jout(200,{'ok':True,'ticket_id':ticket_id,'status':status,'telegram_id':ticket['telegram_id']})
  if p=='/api/bot/tickets/close':
   if self.headers.get('X-API-Key')!=API_SECRET:return self.jout(403,{'error':'forbidden'})
   tgid=int(d.get('telegram_id',0) or 0);ticket_id=int(d.get('ticket_id',0) or 0)
   with db() as c:
    ticket=c.execute('SELECT * FROM support_tickets WHERE id=?',(ticket_id,)).fetchone()
    if not ticket:return self.jout(404,{'error':'Тикет не найден'})
    if tgid!=ADMIN_TG and int(ticket['telegram_id'])!=tgid:return self.jout(403,{'error':'Это обращение принадлежит другому пользователю'})
    now=int(time.time());c.execute("UPDATE support_tickets SET status='closed',updated_at=?,closed_at=? WHERE id=?",(now,now,ticket_id))
   return self.jout(200,{'ok':True,'ticket_id':ticket_id,'telegram_id':ticket['telegram_id'],'closed_by':'admin' if tgid==ADMIN_TG else 'user'})
  if p=='/api/bot/profile':
   if self.headers.get('X-API-Key')!=API_SECRET:return self.jout(403,{'error':'forbidden'})
   tgid=int(d.get('telegram_id',0) or 0);login=str(d.get('login','')).strip().lower();tgname=str(d.get('tg_username','')).lstrip('@')[:32]
   valid=8<=len(login)<=32 and login[0].isalnum() and login[-1].isalnum() and any(ch in 'abcdefghijklmnopqrstuvwxyz' for ch in login) and all(ch in 'abcdefghijklmnopqrstuvwxyz0123456789-' for ch in login)
   if not tgid or not valid:return self.jout(400,{'error':'Логин: 8–32 символа, обязательны латинские буквы; цифры и дефис разрешены. Например: familiaio'})
   with db() as c:existing=c.execute('SELECT login FROM bot_profiles WHERE telegram_id=?',(tgid,)).fetchone()
   if existing:return self.jout(200,{'ok':True,'login':existing['login'],'renamed':0,'site_account_created':False})
   password=generated_password(12);site_account_created=False;recovery_codes=[]
   try:
    with db() as c:
     owner=c.execute('SELECT * FROM users WHERE telegram_id=?',(tgid,)).fetchone();taken=c.execute('SELECT id FROM users WHERE lower(username)=lower(?)',(login,)).fetchone()
     if taken and (not owner or int(taken['id'])!=int(owner['id'])):return self.jout(409,{'error':'Этот логин уже занят на сайте'})
     if owner:
      if owner['username'].lower()!=login:
       if not owner['username'].startswith('tg_'):return self.jout(409,{'error':f"Ваш Telegram уже связан с аккаунтом {owner['username']}. Укажите этот логин."})
       c.execute('UPDATE users SET username=?,tg_username=? WHERE id=?',(login,tgname or owner['tg_username'],owner['id']));c.execute('UPDATE auth_sessions SET username=? WHERE username=?',(login,owner['username']))
      else:c.execute('UPDATE users SET tg_username=? WHERE id=?',(tgname or owner['tg_username'],owner['id']))
      set_user_password(c,login if owner['username'].lower()!=login else owner['username'],password);recovery_codes=create_recovery_codes(c,owner['id'])
     else:
      add_user(c,login,password,'user');c.execute('UPDATE users SET telegram_id=?,tg_username=? WHERE username=?',(tgid,tgname or None,login));created=c.execute('SELECT id FROM users WHERE username=?',(login,)).fetchone();recovery_codes=create_recovery_codes(c,created['id'])
     c.execute('INSERT INTO bot_profiles(telegram_id,login,tg_username,created_at) VALUES(?,?,?,?)',(tgid,login,tgname or None,int(time.time())))
     site_account_created=True
   except sqlite3.IntegrityError:
    with db() as c:r=c.execute('SELECT telegram_id FROM bot_profiles WHERE login=?',(login,)).fetchone()
    if r and int(r['telegram_id'])!=tgid:return self.jout(409,{'error':'Этот логин уже занят'})
    return self.jout(409,{'error':'Не удалось создать аккаунт. Выберите другой логин.'})
   renamed=0
   for x in panel_clients():
    old=x.get('email','')
    if int(x.get('tgId') or 0)!=tgid or not old.startswith('shop-'):continue
    try:
     obj=xapi('GET','/panel/api/clients/get/'+quote(old)).get('obj') or {};client=obj.get('client') or {};client['email']=login
     r=xapi('POST','/panel/api/clients/update/'+quote(old),client_payload(client))
     if r.get('success'):
      with db() as c:c.execute('UPDATE orders SET xui_email=? WHERE xui_email=?',(login,old))
      renamed+=1
    except Exception:pass
   return self.jout(200,{'ok':True,'login':login,'renamed':renamed,'site_account_created':site_account_created,'password':password if site_account_created else None,'recovery_codes':recovery_codes if site_account_created else []})
  if p=='/api/bot/review-payment':
   if self.headers.get('X-API-Key')!=API_SECRET:return self.jout(403,{'error':'forbidden'})
   try:order_id=int(d.get('order_id',0));action=d.get('action','')
   except:return self.jout(400,{'error':'bad order id'})
   if action not in ('approve','reject'):return self.jout(400,{'error':'bad action'})
   security_event('payment_review','telegram-admin',self.request_ip(),{'order_id':order_id,'action':action})
   ok,msg=review_payment(order_id,action=='approve')
   return self.jout(200 if ok else 409,{'ok':ok,'message':msg})
  if p=='/api/bot/cancel-order':
   if self.headers.get('X-API-Key')!=API_SECRET:return self.jout(403,{'error':'forbidden'})
   try:order_id=int(d.get('order_id',0));tgid=int(d.get('telegram_id',0))
   except:return self.jout(400,{'error':'Некорректный заказ'})
   ok,msg=cancel_order(order_id,telegram_id=tgid)
   return self.jout(200 if ok else 409,{'ok':ok,'message':msg,'order_id':order_id,'status':'canceled' if ok else None})
  if p=='/api/bot/confirm-payment':
   if self.headers.get('X-API-Key')!=API_SECRET:return self.jout(403,{'error':'forbidden'})
   try:order_id=int(d.get('order_id',0));tgid=int(d.get('telegram_id',0))
   except:return self.jout(400,{'error':'Некорректный заказ'})
   with db() as c:o=c.execute('SELECT * FROM orders WHERE id=?',(order_id,)).fetchone()
   if not o:return self.jout(404,{'error':'Заказ не найден'})
   if not o['telegram_id'] or int(o['telegram_id'])!=tgid:return self.jout(403,{'error':'Этот заказ принадлежит другому пользователю'})
   ok,msg,status=confirm_payment(o)
   return self.jout(200 if ok else 409,{'ok':ok,'message':msg,'status':status,'order_id':order_id})
  if p=='/admin/orders/review':
   if not self.isadmin():return self.sendx(403,'forbidden')
   try:order_id=int(d.get('order_id',0));action=d.get('action','')
   except:return self.sendx(400,'bad order id')
   if action not in ('approve','reject'):return self.sendx(400,'bad action')
   ok,msg=review_payment(order_id,action=='approve')
   if not ok:return self.sendx(409,page('Не обработано',f'<div class="card box"><h2 class=err>Не удалось обработать заказ</h2><p>{html.escape(msg)}</p><a class="btn soft" href=/admin>Вернуться</a></div>'))
   return self.redir('/admin')
  if p=='/admin/orders/cancel':
   if not self.isadmin():return self.sendx(403,'forbidden')
   try:order_id=int(d.get('order_id',0))
   except:return self.sendx(400,'bad order id')
   with db() as c:o=c.execute('SELECT * FROM orders WHERE id=?',(order_id,)).fetchone()
   if not o:return self.sendx(404,'order not found')
   if o['status']!='pending':return self.sendx(409,page('Не отменено','<div class="card box"><h2 class=err>После начала оплаты заказ отменить нельзя</h2><a class="btn soft" href=/admin/orders/pending>Вернуться</a></div>',True))
   with db() as c:c.execute("UPDATE orders SET status='canceled',reviewed_at=?,error='Отменён администратором' WHERE id=? AND status='pending'",(int(time.time()),order_id));c.execute("INSERT INTO bot_events(event_type,order_id,telegram_id,created_at) VALUES('order_canceled',?,?,?)",(order_id,o['telegram_id'],int(time.time())))
   return self.redir('/admin/orders/pending')
  if p=='/admin/orders/delete':
   if not self.isadmin():return self.sendx(403,'forbidden')
   try:order_id=int(d.get('order_id',0))
   except:return self.sendx(400,'bad order id')
   with db() as c:
    o=c.execute('SELECT status FROM orders WHERE id=?',(order_id,)).fetchone()
    if not o:return self.sendx(404,'order not found')
    if o['status'] not in ('canceled','rejected','expired','deleted'):return self.sendx(409,'only completed inactive orders can be deleted')
    c.execute('DELETE FROM bot_events WHERE order_id=?',(order_id,));c.execute('DELETE FROM scheduled_changes WHERE order_id=?',(order_id,));c.execute('DELETE FROM orders WHERE id=?',(order_id,))
   return self.redir('/admin#orders')
  if p=='/admin/users/toggle-block':
   if not self.isadmin():return self.sendx(403,'forbidden')
   try:user_id=int(d.get('user_id',0));blocked=bool(int(d.get('blocked',1)))
   except:return self.sendx(400,'bad user')
   with db() as c:
    user=c.execute('SELECT * FROM users WHERE id=?',(user_id,)).fetchone()
    if not user or user['role']=='admin':return self.sendx(409,'administrator cannot be blocked')
    emails={x[0] for x in c.execute('SELECT DISTINCT COALESCE(xui_email,target_email) FROM orders WHERE user_id=? AND COALESCE(xui_email,target_email) IS NOT NULL',(user_id,))}
    c.execute('UPDATE users SET blocked_at=?,session_epoch=COALESCE(session_epoch,0)+1 WHERE id=?',(int(time.time()) if blocked else None,user_id))
   errors=[]
   for email in emails:
    try:
     client=(xapi('GET','/panel/api/clients/get/'+quote(email)).get('obj') or {}).get('client') or {}
     if client:client['enable']=not blocked;xapi('POST','/panel/api/clients/update/'+quote(email),client_payload(client))
    except Exception as error:errors.append(str(error))
   record_admin_action(self.who(),user_id,None,'account_blocked' if blocked else 'account_unblocked',{'subscription_errors':errors})
   return self.redir('/admin/user?id='+str(user_id))
  if p=='/admin/users/merge':
   if not self.isadmin():return self.sendx(403,'forbidden')
   try:source_id=int(d.get('source_user_id',0));target_id=int(d.get('target_user_id',0))
   except:return self.sendx(400,'bad users')
   if source_id==target_id:return self.sendx(400,'same user')
   with db() as c:
    source=c.execute('SELECT * FROM users WHERE id=?',(source_id,)).fetchone();target=c.execute('SELECT * FROM users WHERE id=?',(target_id,)).fetchone()
    if not source or not target or source['role']=='admin' or target['role']=='admin':return self.sendx(409,'accounts cannot be merged')
    source_tg=source['telegram_id'];target_tg=target['telegram_id'] or source_tg
    if source_tg and target['telegram_id'] and int(source_tg)!=int(target['telegram_id']):return self.sendx(409,page('Нельзя объединить',f'<div class="card box"><h2 class=err>Оба аккаунта привязаны к разным Telegram</h2><p>Сначала отвяжите лишнюю Telegram-учётную запись.</p><a class=btn href=/admin/user?id={source_id}>Вернуться</a></div>',True))
    emails={x[0] for x in c.execute('SELECT DISTINCT COALESCE(xui_email,target_email) FROM orders WHERE user_id=? AND COALESCE(xui_email,target_email) IS NOT NULL',(source_id,))}
    c.execute('UPDATE orders SET user_id=?,telegram_id=COALESCE(telegram_id,?) WHERE user_id=?',(target_id,target_tg,source_id));c.execute('UPDATE support_tickets SET user_id=?,telegram_id=COALESCE(NULLIF(telegram_id,0),?) WHERE user_id=?',(target_id,target_tg,source_id))
    c.execute('INSERT OR IGNORE INTO site_notification_reads(user_id,notification_key,read_at) SELECT ?,notification_key,read_at FROM site_notification_reads WHERE user_id=?',(target_id,source_id));c.execute('DELETE FROM site_notification_reads WHERE user_id=?',(source_id,))
    if not c.execute('SELECT 1 FROM site_trial_claims WHERE user_id=?',(target_id,)).fetchone():c.execute('UPDATE site_trial_claims SET user_id=? WHERE user_id=?',(target_id,source_id))
    else:c.execute('DELETE FROM site_trial_claims WHERE user_id=?',(source_id,))
    c.execute('UPDATE admin_actions SET user_id=? WHERE user_id=?',(target_id,source_id));c.execute('UPDATE tariffs SET personal_user_id=? WHERE personal_user_id=?',(target_id,source_id))
    if target_tg and not target['telegram_id']:c.execute('UPDATE users SET telegram_id=?,tg_username=COALESCE(tg_username,?) WHERE id=?',(target_tg,source['tg_username'],target_id))
    if source_tg:
     c.execute('DELETE FROM bot_profiles WHERE telegram_id=?',(source_tg,));c.execute('INSERT OR REPLACE INTO bot_profiles(telegram_id,login,tg_username,created_at) VALUES(?,?,?,?)',(target_tg,target['username'],target['tg_username'] or source['tg_username'],int(time.time())))
    c.execute('DELETE FROM user_recovery_codes WHERE user_id=?',(source_id,));c.execute('DELETE FROM auth_sessions WHERE username=?',(source['username'],));c.execute('DELETE FROM users WHERE id=?',(source_id,))
   for email in emails:
    try:
     client=(xapi('GET','/panel/api/clients/get/'+quote(email)).get('obj') or {}).get('client') or {}
     if client and target_tg:client['tgId']=target_tg;xapi('POST','/panel/api/clients/update/'+quote(email),client_payload(client))
    except Exception:pass
   record_admin_action(self.who(),target_id,None,'accounts_merged',{'source':source['username'],'target':target['username']})
   return self.redir('/admin/user?id='+str(target_id))
  if p=='/admin/clients/reset-link':
   if not self.isadmin():return self.sendx(403,'forbidden')
   email=str(d.get('email','')).strip();user_id=int(d.get('user_id',0) or 0)
   if not email:return self.sendx(400,'bad subscription')
   with db() as c:c.execute('INSERT INTO subscription_secrets(subscription,secret,updated_at) VALUES(?,?,?) ON CONFLICT(subscription) DO UPDATE SET secret=excluded.secret,updated_at=excluded.updated_at',(email,secrets.token_urlsafe(48),int(time.time())));c.execute('UPDATE subscription_aliases SET active=0 WHERE client_email=?',(email,))
   record_admin_action(self.who(),user_id,email,'subscription_link_reset');return self.redir('/admin/user?id='+str(user_id))
  if p=='/admin/clients/clear-devices':
   if not self.isadmin():return self.sendx(403,'forbidden')
   email=str(d.get('email','')).strip();user_id=int(d.get('user_id',0) or 0)
   try:xapi('POST','/panel/api/clients/clearIps/'+quote(email),{});xapi('DELETE','/panel/api/clients/hwids/'+quote(email))
   except Exception as error:return self.sendx(502,page('Ошибка',f'<div class="card box"><h2 class=err>Не удалось отвязать устройства</h2><p>{html.escape(str(error))}</p></div>',True))
   with db() as c:c.execute('DELETE FROM device_labels WHERE subscription=?',(email,))
   record_admin_action(self.who(),user_id,email,'all_devices_detached');return self.redir('/admin/user?id='+str(user_id))
  if p=='/subscription/device/remove':
   if not self.need():return
   email=str(d.get('email','')).strip();kind=str(d.get('device_kind',''));source=str(d.get('device_source','')).strip()
   with db() as c:u=c.execute('SELECT id,telegram_id FROM users WHERE username=?',(self.who(),)).fetchone()
   owned={x.get('email') for x in account_clients(u['id'],u['telegram_id']) if x.get('email')}
   if email not in owned:return self.sendx(403,'forbidden')
   try:
    if kind=='hwid':xapi('DELETE','/panel/api/clients/hwids/'+quote(email)+'/'+quote(source),{})
    elif kind=='ip':xapi('POST','/panel/api/clients/clearIps/'+quote(email),{})
    else:return self.sendx(400,'bad device')
    with db() as c:c.execute('DELETE FROM device_labels WHERE subscription=? AND device_id=?',(email,kind+'-'+hashlib.sha256(source.encode()).hexdigest()[:16]))
   except Exception as error:return self.sendx(502,page('Ошибка',f'<div class="card box"><h2>Не удалось отключить устройство</h2><p>{html.escape(str(error))}</p><a class=btn href=/account>Вернуться</a></div>'))
   return self.redir('/account')
  if p=='/admin/clients/device/remove':
   if not self.isadmin():return self.sendx(403,'forbidden')
   email=str(d.get('email','')).strip();kind=str(d.get('device_kind',''));source=str(d.get('device_source',''));user_id=int(d.get('user_id',0) or 0)
   try:
    if kind=='hwid':xapi('DELETE','/panel/api/clients/hwids/'+quote(email)+'/'+quote(source))
    else:xapi('POST','/panel/api/clients/clearIps/'+quote(email),{})
   except Exception as error:return self.sendx(502,str(error),'text/plain')
   record_admin_action(self.who(),user_id,email,'device_detached',{'kind':kind});return self.redir('/admin/user?id='+str(user_id))
  if p=='/admin/clients/limits':
   if not self.isadmin():return self.sendx(403,'forbidden')
   email=str(d.get('email','')).strip();user_id=int(d.get('user_id',0) or 0)
   try:limit_ip=max(1,min(MAX_DEVICES,int(d.get('limit_ip',1))));traffic_gb=max(0,int(d.get('traffic_gb',0)));client=(xapi('GET','/panel/api/clients/get/'+quote(email)).get('obj') or {}).get('client') or {};client['limitIp']=limit_ip;client['totalGB']=traffic_gb*1024*1024*1024;result=xapi('POST','/panel/api/clients/update/'+quote(email),client_payload(client))
   except Exception as error:return self.sendx(502,str(error),'text/plain')
   if not result.get('success'):return self.sendx(502,str(result.get('msg') or 'update failed'),'text/plain')
   record_admin_action(self.who(),user_id,email,'subscription_limits_changed',{'devices':limit_ip,'traffic_gb':traffic_gb});return self.redir('/admin/user?id='+str(user_id))
  if p=='/admin/clients/delete':
   if not self.isadmin():return self.sendx(403,'forbidden')
   email=str(d.get('email','')).strip()
   if not email:return self.sendx(400,'bad subscription')
   try:r=xapi('POST','/panel/api/clients/del/'+quote(email),{})
   except Exception as e:return self.sendx(502,page('Ошибка удаления',f'<div class="card box"><h2 class=err>Не удалось связаться с 3x-ui</h2><p>{html.escape(str(e))}</p><a class="btn soft" href=/admin>Вернуться</a></div>'))
   if not r.get('success') and 'not found' not in str(r.get('msg','')).lower():return self.sendx(502,page('Ошибка удаления',f'<div class="card box"><h2 class=err>3x-ui не удалил подписку</h2><p>{html.escape(str(r.get("msg","Ошибка")))}</p><a class="btn soft" href=/admin>Вернуться</a></div>'))
   with db() as c:
    c.execute("UPDATE orders SET status=CASE WHEN status IN ('pending','processing','review','payment_pending','payment_error','active','error') THEN 'deleted' ELSE status END,vpn_uri=NULL,error='Подписка удалена администратором',reviewed_at=COALESCE(reviewed_at,?) WHERE xui_email=? OR target_email=?",(int(time.time()),email,email))
    c.execute('DELETE FROM expiry_reminders WHERE email=?',(email,))
    c.execute('DELETE FROM scheduled_changes WHERE email=?',(email,))
   return self.redir('/admin?subscription_deleted=1')
  if p=='/admin/clients/devices':
   if not self.isadmin():return self.sendx(403,'forbidden')
   email=str(d.get('email','')).strip()
   try:limit_ip=int(d.get('limit_ip',0))
   except:return self.sendx(400,'bad device limit')
   if not email or limit_ip not in range(1,MAX_DEVICES+1):return self.sendx(400,page('Ошибка', f'<div class="card box"><h2 class=err>Выберите от 1 до {MAX_DEVICES} устройств</h2><a class="btn soft" href=/admin>Вернуться</a></div>'))
   try:
    obj=xapi('GET','/panel/api/clients/get/'+quote(email)).get('obj') or {};client=obj.get('client') or {}
    if not client:return self.sendx(404,page('Не найдено','<div class="card box"><h2 class=err>Подписка не найдена в 3x-ui</h2><a class="btn soft" href=/admin>Вернуться</a></div>'))
    client['limitIp']=limit_ip;r=xapi('POST','/panel/api/clients/update/'+quote(email),client_payload(client))
   except Exception as e:return self.sendx(502,page('Ошибка изменения',f'<div class="card box"><h2 class=err>Не удалось обновить 3x-ui</h2><p>{html.escape(str(e))}</p><a class="btn soft" href=/admin>Вернуться</a></div>'))
   if not r.get('success'):return self.sendx(502,page('Ошибка изменения',f'<div class="card box"><h2 class=err>3x-ui не применил изменение</h2><p>{html.escape(str(r.get("msg","Ошибка")))}</p><a class="btn soft" href=/admin>Вернуться</a></div>'))
   with db() as c:c.execute('DELETE FROM scheduled_changes WHERE email=? AND applied_at IS NULL',(email,))
   return self.redir('/admin?devices_updated=1&client='+quote(email))
  if p in ('/admin/clients/toggle','/admin/clients/extend'):
   if not self.isadmin():return self.sendx(403,'forbidden')
   email=str(d.get('email','')).strip()
   if not email:return self.sendx(400,'bad subscription')
   try:
    obj=xapi('GET','/panel/api/clients/get/'+quote(email)).get('obj') or {};client=obj.get('client') or {}
    if not client:return self.sendx(404,'subscription not found')
    if p.endswith('/toggle'):client['enable']=str(d.get('enable','0'))=='1'
    else:
     days=int(d.get('days',0))
     if days not in range(1,3651):return self.sendx(400,'days must be 1..3650')
     now_ms=int(time.time()*1000);client['expiryTime']=max(now_ms,int(client.get('expiryTime') or 0))+days*86400000;client['enable']=True
    r=xapi('POST','/panel/api/clients/update/'+quote(email),client_payload(client))
   except Exception as e:return self.sendx(502,page('Ошибка изменения',f'<div class="card box"><h2 class=err>Не удалось обновить 3x-ui</h2><p>{html.escape(str(e))}</p><a class="btn soft" href=/admin>Вернуться</a></div>',True))
   if not r.get('success'):return self.sendx(502,'3x-ui update failed')
   user_id=int(d.get('user_id',0) or 0)
   if user_id:record_admin_action(self.who(),user_id,email,'subscription_toggled' if p.endswith('/toggle') else 'subscription_days_added',{'days':d.get('days'),'enabled':client.get('enable')});return self.redir('/admin/user?id='+str(user_id))
   return self.redir('/admin?client='+quote(email))
  if p=='/admin/users/reset-password':
   if not self.isadmin():return self.sendx(403,'forbidden')
   try:uid=int(d.get('user_id',0))
   except:return self.sendx(400,'bad user id')
   password=generated_password(12)
   with db() as c:
    u=c.execute("SELECT * FROM users WHERE id=? AND role!='admin'",(uid,)).fetchone()
    if not u:return self.sendx(404,'user not found')
    set_user_password(c,u['username'],password);c.execute('DELETE FROM auth_sessions WHERE username=?',(u['username'],))
   body=f"<div class='card box'><h2>Новый пароль создан</h2><p>Пользователь: <b>{html.escape(u['username'])}</b></p><div class=uri>{html.escape(password)}</div><p>Скопируйте пароль сейчас. После ухода со страницы он больше не показывается.</p><a class=btn href=/admin#users>Вернуться к пользователям</a></div>"
   return self.sendx(200,page('Новый пароль',body,True))
  if p=='/admin/users/disable-2fa':
   if not self.isadmin():return self.sendx(403,'forbidden')
   try:uid=int(d.get('user_id',0))
   except:return self.sendx(400,'bad user id')
   with db() as c:
    u=c.execute("SELECT * FROM users WHERE id=? AND role!='admin'",(uid,)).fetchone()
    if not u:return self.sendx(404,'user not found')
    c.execute('UPDATE users SET twofa_enabled=0,totp_secret=NULL,session_epoch=session_epoch+1 WHERE id=?',(uid,));c.execute('DELETE FROM auth_sessions WHERE username=?',(u['username'],))
   return self.redir('/admin#users')
  if p=='/admin/users/detach-telegram':
   if not self.isadmin():return self.sendx(403,'forbidden')
   try:uid=int(d.get('user_id',0))
   except:return self.sendx(400,'bad user id')
   with db() as c:
    u=c.execute("SELECT * FROM users WHERE id=? AND role!='admin'",(uid,)).fetchone()
    if not u:return self.sendx(404,'user not found')
    tgid=u['telegram_id'];emails=[x[0] for x in c.execute('SELECT DISTINCT xui_email FROM orders WHERE user_id=? AND xui_email IS NOT NULL',(uid,))]
   for email in emails:
    try:
     obj=xapi('GET','/panel/api/clients/get/'+quote(email)).get('obj') or {};client=obj.get('client') or {}
     if client:client['tgId']=0;xapi('POST','/panel/api/clients/update/'+quote(email),client_payload(client))
    except Exception:pass
   with db() as c:
    c.execute('UPDATE users SET telegram_id=NULL,tg_username=NULL WHERE id=?',(uid,));c.execute('UPDATE orders SET telegram_id=NULL,tg_username=NULL WHERE user_id=?',(uid,))
    if tgid is not None:c.execute('DELETE FROM bot_profiles WHERE telegram_id=?',(tgid,));c.execute('DELETE FROM notification_preferences WHERE telegram_id=?',(tgid,));c.execute('DELETE FROM auth_sessions WHERE telegram_id=?',(tgid,))
   return self.redir('/admin#users')
  if p=='/admin/users/delete':
   if not self.isadmin():return self.sendx(403,page('Доступ запрещён','<div class="card box"><h2 class=err>Требуются права администратора</h2></div>'))
   try:uid=int(d.get('user_id',0))
   except:return self.sendx(400,'bad user id')
   with db() as c:
    u=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()
    if not u:return self.sendx(404,page('Не найден','<div class="card box"><h2>Пользователь не найден</h2></div>'))
    if u['role']=='admin' or u['username']==self.who():return self.sendx(400,page('Удаление запрещено','<div class="card box"><h2 class=err>Администратора удалить нельзя</h2><a class="btn soft" href=/admin>Вернуться</a></div>'))
    c.execute('DELETE FROM scheduled_changes WHERE order_id IN (SELECT id FROM orders WHERE user_id=? OR (? IS NOT NULL AND telegram_id=?))',(uid,u['telegram_id'],u['telegram_id']))
    orders=c.execute('SELECT id,xui_email,target_email FROM orders WHERE user_id=? OR (? IS NOT NULL AND telegram_id=?)',(uid,u['telegram_id'],u['telegram_id'])).fetchall()
   emails={e for o in orders for e in (o['xui_email'],o['target_email']) if e}
   if u['telegram_id'] is not None:
    try:emails.update(x.get('email') for x in panel_clients() if int(x.get('tgId') or 0)==int(u['telegram_id']) and x.get('email'))
    except Exception:pass
   errors=[]
   for email in emails:
    try:
     r=xapi('POST','/panel/api/clients/del/'+quote(email),{})
     if not r.get('success') and 'not found' not in str(r.get('msg','')).lower():errors.append(email+': '+str(r.get('msg','ошибка 3x-ui')))
    except Exception as e:errors.append(email+': '+str(e))
   if errors:return self.sendx(502,page('Ошибка удаления',f'<div class="card box"><h2 class=err>Не все подключения удалены из 3x-ui</h2><p>{html.escape("; ".join(errors))}</p><p>Аккаунт на сайте сохранён. Исправьте ошибку и повторите.</p><a class="btn soft" href=/admin>Вернуться</a></div>'))
   with db() as c:
    c.execute('DELETE FROM bot_events WHERE order_id IN (SELECT id FROM orders WHERE user_id=? OR (? IS NOT NULL AND telegram_id=?))',(uid,u['telegram_id'],u['telegram_id']))
    c.execute('DELETE FROM trial_claims WHERE order_id IN (SELECT id FROM orders WHERE user_id=? OR (? IS NOT NULL AND telegram_id=?))',(uid,u['telegram_id'],u['telegram_id']))
    c.execute('DELETE FROM site_trial_claims WHERE user_id=?',(uid,))
    c.execute('DELETE FROM site_notification_reads WHERE user_id=?',(uid,))
    c.execute('DELETE FROM user_recovery_codes WHERE user_id=?',(uid,))
    for email in emails:c.execute('DELETE FROM expiry_reminders WHERE email=?',(email,))
    c.execute('DELETE FROM orders WHERE user_id=? OR (? IS NOT NULL AND telegram_id=?)',(uid,u['telegram_id'],u['telegram_id']))
    c.execute('DELETE FROM auth_sessions WHERE username=?',(u['username'],))
    if u['telegram_id'] is not None:
     c.execute('DELETE FROM auth_sessions WHERE telegram_id=?',(u['telegram_id'],));c.execute('DELETE FROM notification_preferences WHERE telegram_id=?',(u['telegram_id'],));c.execute('DELETE FROM bot_profiles WHERE telegram_id=?',(u['telegram_id'],))
    c.execute('DELETE FROM users WHERE id=?',(uid,))
   return self.redir('/admin?user_deleted=1')
  if p in ('/orders','/api/orders'):
   if p.startswith('/api/') and self.headers.get('X-API-Key')!=API_SECRET:return self.jout(403,{'error':'forbidden'})
   plan=d.get('plan','m1');t=secrets.token_urlsafe(18);tg=d.get('telegram_id')
   test_username=str(d.get('test_username','')).strip();test_mode=False
   email=d.get('email','').strip().lower();tgname=d.get('tg_username','').strip()
   if tgname and not tgname.startswith('@'):tgname='@'+tgname
   valid_email=('@' in email and '.' in email.rsplit('@',1)[-1]) if email else False
   valid_tg=(tgname.startswith('@') and 6<=len(tgname)<=33 and all(ch.isalnum() or ch=='_' for ch in tgname[1:])) if tgname else False
   signed=self.who();uid=None
   if signed:
    with db() as c:z=c.execute('SELECT id,telegram_id FROM users WHERE username=?',(signed,)).fetchone()
    if z:
     uid=z['id']
     if not tg and z['telegram_id']:tg=z['telegram_id']
   elif tg:
    try:
     with db() as c:z=c.execute('SELECT id FROM users WHERE telegram_id=?',(int(tg),)).fetchone()
     if z:uid=z['id']
    except (TypeError,ValueError):pass
   if p.startswith('/api/') and test_username:
    try:admin_test=int(tg)==ADMIN_TG
    except (TypeError,ValueError):admin_test=False
    if not admin_test:return self.jout(403,{'error':'Выбор тестового пользователя доступен только администратору'})
    with db() as c:z=c.execute("SELECT id,username FROM users WHERE username=? AND role='test' AND blocked_at IS NULL",(test_username,)).fetchone()
    if not z:return self.jout(404,{'error':'Тестовый пользователь не найден'})
    uid=z['id'];test_username=z['username'];test_mode=True
   tariff=tariff_by_code(plan,uid)
   if not tariff:return self.jout(400,{'error':'Тариф недоступен'}) if p.startswith('/api/') else self.sendx(400,page('Тариф недоступен','<div class="card box"><h2 class=err>Этот тариф больше нельзя оформить</h2><a class=btn href=/#tariffs>Выбрать тариф</a></div>'))
   plan_values=(tariff['name'],int(tariff['days']),int(tariff['price']),int(tariff['devices']),int(tariff['traffic_gb']))
   if p=='/orders' and not uid:return self.redir('/login')
   if tariff['trial']:
    if p.startswith('/api/') and not tg and not test_mode:return self.jout(400,{'error':'Пробный период доступен только для Telegram-аккаунта'})
    with db() as c:claimed=c.execute('SELECT 1 FROM site_trial_claims WHERE user_id=?',(uid,)).fetchone() if test_mode or not p.startswith('/api/') else c.execute('SELECT 1 FROM trial_claims WHERE telegram_id=?',(int(tg),)).fetchone()
    if claimed:
     if p.startswith('/api/'):return self.jout(409,{'error':'Пробный период для этого Telegram-аккаунта уже использован'})
     return self.sendx(409,page('Пробный период использован','<div class="card box"><h2 class=err>Пробный период уже использован</h2><p>Для одной учётной записи доступен только один пробный период.</p><a class=btn href=/#tariffs>Выбрать платный тариф</a></div>'))
   if not (valid_email or valid_tg or tg or signed or test_mode or p=='/orders'):
    if p.startswith('/api/'):return self.jout(400,{'error':'Укажите корректный Email или Telegram'})
    return self.sendx(400,page('Контакты','<div class="card box"><h2 class=err>Укажите корректный Email или Telegram</h2><a class="btn soft" href=/>Вернуться</a></div>'))
   customer=test_username if test_mode else (email or tgname or d.get('customer','') or signed or 'Гость сайта')
   requested=str(d.get('subscription','')).strip();action=str(d.get('action','renew' if requested else 'new'))
   ownership_tg=None if test_mode else tg
   owned={x.get('email'):x for x in account_clients(uid,ownership_tg) if x.get('email')}
   if action in ('renew','upgrade') and not requested and len(owned)==1:requested=next(iter(owned))
   if action not in ('new','renew','upgrade') or (action in ('renew','upgrade') and requested not in owned):
    msg='Выберите принадлежащую вам подписку'
    if p.startswith('/api/'):return self.jout(400,{'error':msg})
    return self.sendx(400,page('Ошибка',f'<div class="card box"><h2 class=err>{msg}</h2><a class="btn soft" href=/#tariffs>Вернуться</a></div>'))
   if tariff['trial']:action='new';requested=''
   target=requested if action in ('renew','upgrade') else None;target_client=owned.get(target,{})
   current_expiry=int(target_client.get('expiryTime') or 0);current_limit=max(1,min(MAX_DEVICES,int(target_client.get('limitIp') or 1)));new_limit=plan_values[3]
   now_ms=int(time.time()*1000);base_amount=plan_values[2];amount=base_amount;remaining_days=0
   if action=='renew' and new_limit!=current_limit:
    msg='Для изменения количества устройств используйте отдельный раздел «Изменить тариф»'
    return self.jout(400,{'error':msg}) if p.startswith('/api/') else self.sendx(400,page('Ошибка',f'<div class="card box"><h2 class=err>{msg}</h2><a class="btn soft" href="/subscription/manage?email={quote(target)}">Вернуться</a></div>'))
   if action=='upgrade':
    remaining_days,amount=catalog_upgrade_price(current_limit,tariff,current_expiry,uid,now_ms)
    if current_expiry<=now_ms:return self.jout(409,{'error':'Срок подписки закончился. Сначала продлите тариф.'}) if p.startswith('/api/') else self.sendx(409,page('Срок закончился',f'<div class="card box"><h2 class=err>Сначала продлите подписку</h2><a class="btn soft" href="/subscription/manage?email={quote(target)}">Вернуться</a></div>'))
    if new_limit<=current_limit or amount<=0:return self.jout(400,{'error':'Новый тариф должен содержать больше устройств'}) if p.startswith('/api/') else self.sendx(400,page('Ошибка',f'<div class="card box"><h2 class=err>Выберите большее количество устройств</h2><a class="btn soft" href="/subscription/manage?email={quote(target)}">Вернуться</a></div>'))
   base_amount=amount;amount,discount,promo=apply_promo(d.get('promo_code'),amount)
   projected=current_expiry if action=='upgrade' else max(now_ms,current_expiry)+plan_values[1]*86400000
   effective_at=now_ms if action=='upgrade' else (current_expiry if action=='renew' and current_expiry>now_ms else now_ms)
   with db() as c:
    c.execute('BEGIN IMMEDIATE')
    c.execute("UPDATE orders SET status='expired',error='Истекло время оплаты' WHERE status IN ('pending','error') AND expires_at IS NOT NULL AND expires_at<=?",(int(time.time()),))
    unfinished=unfinished_order_for(c,uid,ownership_tg,action,target)
    if unfinished:
     existing_url=PUBLIC+'/pay/'+unfinished['token']
     if p.startswith('/api/'):return self.jout(409,{'error':'Для этой подписки уже есть незавершённое изменение или продление','order_id':unfinished['id'],'status':unfinished['status'],'pay_url':existing_url})
     return self.sendx(409,page('Есть незавершённый заказ',f'<div class="card box"><h2>Для этой подписки уже создан заказ</h2><p>Сначала завершите или отмените его. Новые отдельные подписки и заказы для других подписок можно оплачивать параллельно.</p><a class=btn href="{html.escape(existing_url)}">Открыть заказ №{unfinished["id"]}</a><a class="btn soft backbtn" href=/account>Мои подписки</a></div>'))
    created_at=int(time.time());expires_at=created_at+PAYMENT_WINDOW
    assigned=choose_node();cur=c.execute('INSERT INTO orders(token,plan,amount,customer,telegram_id,created_at,expires_at,user_id,email,tg_username,xui_email,order_kind,target_email,plan_name,plan_days,plan_devices,plan_traffic_gb,base_amount,discount_amount,promo_code,assigned_node,plan_trial) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(t,plan,amount,customer,int(tg) if tg else None,created_at,expires_at,uid,email or None,tgname or None,target,action,target,plan_values[0],plan_values[1],new_limit,plan_values[4],base_amount,discount,promo,assigned,1 if tariff['trial'] else 0))
   x={'id':cur.lastrowid,'token':t,'pay_url':PUBLIC+'/pay/'+t,'payment_url':PUBLIC+'/pay/'+t+'/start','payment_method':payment_mode(),'qr_url':PUBLIC+'/qr/'+t+'.png','payment_reference':payment_reference(cur.lastrowid),'expires_at':expires_at,'amount':amount,'currency':CURRENCY,'traffic_gb':plan_values[4],'action':action,'subscription':target,'current_expiry':current_expiry,'projected_expiry':projected,'effective_at':effective_at,'link_unchanged':bool(target),'old_limit_ip':current_limit,'new_limit_ip':new_limit,'days':plan_values[1],'remaining_days':remaining_days};return self.jout(201,x) if p.startswith('/api/') else self.redir('/cart' if d.get('destination')=='cart' else x['pay_url'])
  if p.startswith('/pay/') and p.endswith('/cancel'):
   if not self.need():return
   token=p.split('/')[2];o=getorder(token)
   if not o:return self.sendx(404,'not found')
   with db() as c:u=c.execute('SELECT id,telegram_id FROM users WHERE username=?',(self.who(),)).fetchone()
   ok,msg=cancel_order(o['id'],user_id=u['id'] if u else None,telegram_id=u['telegram_id'] if u else None)
   if not ok:return self.sendx(409,page('Не удалось отменить',f'<div class="card box"><h2 class=err>Заказ не отменён</h2><p>{html.escape(msg)}</p><a class="btn soft" href="/pay/{html.escape(token,quote=True)}">Вернуться</a></div>'))
   return self.redir('/pay/'+token)
  if p.startswith('/pay/') and p.endswith('/confirm'):
   t=p.split('/')[2];o=getorder(t)
   if not o:return self.sendx(404,'not found')
   confirm_payment(o)
   return self.redir(PUBLIC+'/pay/'+t)
  self.sendx(404,'not found')
 def log_message(self,*a):pass
if __name__=='__main__':
 init();resume_mail_campaigns();threading.Thread(target=scheduled_worker,daemon=True).start();ThreadingHTTPServer.request_queue_size=128;ThreadingHTTPServer.daemon_threads=True;server=ThreadingHTTPServer((BIND,PORT),H)
 if TLS_CERT or TLS_KEY:
  if not TLS_CERT or not TLS_KEY:raise SystemExit('TLS_CERT и TLS_KEY должны быть указаны вместе')
  context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.minimum_version=ssl.TLSVersion.TLSv1_2;context.load_cert_chain(TLS_CERT,TLS_KEY);server.socket=context.wrap_socket(server.socket,server_side=True)
 server.serve_forever()
