#!/usr/bin/env python3
import importlib.util
import http.client
import json
import os
import tempfile
import threading
import unittest
import urllib.request

SPEC = importlib.util.spec_from_file_location("vpn_shop_app", os.path.join(os.path.dirname(__file__), "app.py"))
APP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(APP)


class TariffPriceTests(unittest.TestCase):
    def test_runtime_configuration_changes_prices_devices_traffic_and_nodes(self):
        original=json.loads(json.dumps(APP.CONFIG));changed=json.loads(json.dumps(APP.CONFIG))
        try:
            changed['pricing']['traffic_gb']=250;changed['pricing']['device_monthly']={'1':120,'2':210,'3':300};changed['pricing']['max_devices']=3
            changed['nodes']=[{'id':'main','name':'Main test node','flag':'🏠','country':'RU','primary':True,'enabled':True,'panel_url':'https://127.0.0.1:9999/panel','token_env':'XUI_TOKEN','inbound_ids':[7,8],'routes':[{'name':'reality','inbound_id':7,'protocol':'vless','transport':'tcp'}]}]
            APP.apply_runtime_config(changed)
            self.assertEqual(APP.MAX_DEVICES,3);self.assertEqual(APP.TRAFFIC_GB,250);self.assertEqual(APP.XIDS,[7,8]);self.assertEqual(APP.PRIMARY_NODE_NAME,'Main test node');self.assertEqual(APP.PLANS['m1_3'][2],300)
        finally:APP.apply_runtime_config(original)

    def test_totp_accepts_current_code_and_rejects_wrong_code(self):
        secret=APP.new_totp_secret();now=1_700_000_000
        self.assertTrue(APP.verify_totp(secret,APP.totp_code(secret,now),now))
        self.assertFalse(APP.verify_totp(secret,'000000' if APP.totp_code(secret,now)!='000000' else '111111',now))

    def test_admin_page_uses_separate_navigation(self):
        document=APP.page('Админ-панель','<p>test</p>',True).decode()
        self.assertIn('Пользователи',document)
        self.assertIn('Проверка оплат',document)
        self.assertIn('Тестовый пользователь',document)
        self.assertNotIn('Оформление подписки',document)

    def test_generated_backup_password_has_required_12_character_format(self):
        password=APP.generated_password()
        self.assertEqual(len(password),12)
        self.assertTrue(any(x.islower() for x in password))
        self.assertTrue(any(x.isupper() for x in password))
        self.assertTrue(any(x.isdigit() for x in password))
        self.assertTrue(any(x in '!@#$%*-_' for x in password))

    def test_payment_link_runs_through_order_activation_before_external_provider(self):
        self.assertEqual(APP.payment_destination("secret-token"), APP.PUBLIC + "/pay/secret-token/start")
        self.assertEqual(APP.payment_reference(42), "№42")

    def test_half_month_upgrade_from_one_to_two_devices(self):
        now = 1_000_000_000_000
        days, amount = APP.upgrade_price(1, 2, now + 15 * 86_400_000, now)
        self.assertEqual((days, amount), (15, 45))

    def test_full_month_upgrade_from_one_to_five_devices(self):
        now = 1_000_000_000_000
        days, amount = APP.upgrade_price(1, 5, now + 30 * 86_400_000, now)
        self.assertEqual((days, amount), (30, 360))

    def test_partial_day_is_billed_as_remaining_day(self):
        now = 1_000_000_000_000
        days, amount = APP.upgrade_price(2, 3, now + 9 * 86_400_000 + 1, now)
        self.assertEqual((days, amount), (10, 30))

    def test_expired_subscription_has_no_upgrade_price(self):
        now = 1_000_000_000_000
        self.assertEqual(APP.upgrade_price(1, 3, now - 1, now), (0, 0))


class CatalogPersistenceTests(unittest.TestCase):
    def setUp(self):
        handle,self.path=tempfile.mkstemp();os.close(handle);self.previous=APP.DB;APP.DB=self.path;APP.init()

    def tearDown(self):
        APP.DB=self.previous;os.unlink(self.path)

    def test_order_snapshot_is_not_changed_with_catalog_price(self):
        with APP.db() as connection:
            connection.execute("INSERT INTO orders(token,plan,amount,customer,status,created_at,plan_name,plan_days,plan_devices,plan_traffic_gb,base_amount) VALUES('snapshot','m1',77,'u','pending',?,'Старая цена',30,2,50,77)",(int(APP.time.time()),))
            connection.execute("UPDATE tariffs SET price=999,name='Новая цена' WHERE code='m1'")
        plan=APP.order_plan(APP.getorder('snapshot'))
        self.assertEqual(plan,('Старая цена',30,77,2,50))

    def test_catalog_upgrade_uses_published_price_difference(self):
        now_ms=1_000_000_000_000
        with APP.db() as connection:
            connection.execute("UPDATE tariffs SET price=100 WHERE code='m1'")
            target=connection.execute("SELECT * FROM tariffs WHERE code='m1_2'").fetchone()
        days,amount=APP.catalog_upgrade_price(1,target,now_ms+15*86_400_000,None,now_ms)
        self.assertEqual(days,15);self.assertEqual(amount,45)

    def test_blocked_user_cannot_authenticate(self):
        with APP.db() as connection:
            user=connection.execute('SELECT * FROM users WHERE username=?',(APP.ADMIN,)).fetchone()
            connection.execute('UPDATE users SET blocked_at=? WHERE id=?',(int(APP.time.time()),user['id']))
            user=connection.execute('SELECT * FROM users WHERE id=?',(user['id'],)).fetchone()
        self.assertFalse(APP.password_ok(user,APP.ADMIN_PASSWORD))


class YooKassaPaymentTests(unittest.TestCase):
    def setUp(self):
        handle,self.path=tempfile.mkstemp();os.close(handle);self.previous_db=APP.DB;APP.DB=self.path;APP.init()
        self.previous_shop,self.previous_secret=APP.YOOKASSA_SHOP_ID,APP.YOOKASSA_SECRET_KEY;APP.YOOKASSA_SHOP_ID='test-shop';APP.YOOKASSA_SECRET_KEY='test-secret'

    def tearDown(self):
        APP.DB=self.previous_db;APP.YOOKASSA_SHOP_ID=self.previous_shop;APP.YOOKASSA_SECRET_KEY=self.previous_secret;os.unlink(self.path)

    def test_only_one_payment_mode_is_enabled(self):
        original=json.loads(json.dumps(APP.CONFIG))
        try:
            changed=json.loads(json.dumps(APP.CONFIG));changed.setdefault('payment',{})['mode']='manual';APP.apply_runtime_config(changed)
            self.assertEqual([x['id'] for x in APP.enabled_payment_methods()],['manual'])
            changed['payment']['mode']='yookassa';APP.apply_runtime_config(changed)
            self.assertEqual([x['id'] for x in APP.enabled_payment_methods()],['yookassa'])
        finally:APP.apply_runtime_config(original)

    def test_create_payment_uses_idempotence_and_stops_order_timer(self):
        now=int(APP.time.time())
        with APP.db() as connection:
            connection.execute("INSERT INTO orders(token,plan,amount,customer,status,created_at,expires_at,plan_name,plan_days,plan_devices,plan_traffic_gb) VALUES('yk-create','m1',100,'u','pending',?,?, '1 месяц',30,1,0)",(now,now+600))
        order=APP.getorder('yk-create');original=APP.yookassa_request;calls=[]
        try:
            APP.yookassa_request=lambda method,path,body=None,idempotence_key=None:(calls.append((method,path,body,idempotence_key)) or {'id':'pay-1','status':'pending','confirmation':{'confirmation_url':'https://yookassa.test/pay-1'}})
            transaction=APP.create_yookassa_payment(order)
        finally:APP.yookassa_request=original
        self.assertEqual(transaction['provider_payment_id'],'pay-1');self.assertEqual(APP.getorder('yk-create')['status'],'payment_pending');self.assertIsNone(APP.getorder('yk-create')['expires_at'])
        self.assertTrue(calls[0][3]);self.assertEqual(calls[0][2]['metadata']['order_id'],str(order['id']))

    def test_webhook_status_is_verified_against_api_amount_and_order(self):
        now=int(APP.time.time())
        with APP.db() as connection:
            cur=connection.execute("INSERT INTO orders(token,plan,amount,customer,status,created_at,plan_name,plan_days,plan_devices,plan_traffic_gb) VALUES('yk-sync','m1',100,'u','payment_pending',?,'1 месяц',30,1,0)",(now,));order_id=cur.lastrowid
            connection.execute("INSERT INTO payment_transactions(order_id,provider,provider_payment_id,idempotence_key,status,amount,currency,created_at,updated_at) VALUES(?,'yookassa','pay-2','idem','pending',100,'RUB',?,?)",(order_id,now,now))
        original_request,original_activate=APP.yookassa_request,APP.activate_automatic_payment;activated=[]
        try:
            APP.yookassa_request=lambda *args,**kwargs:{'id':'pay-2','status':'succeeded','paid':True,'amount':{'value':'100.00','currency':'RUB'},'metadata':{'order_id':str(order_id)}}
            APP.activate_automatic_payment=lambda value:(activated.append(value) or (True,'ok'))
            ok,_=APP.sync_yookassa_payment('pay-2')
        finally:APP.yookassa_request,APP.activate_automatic_payment=original_request,original_activate
        self.assertTrue(ok);self.assertEqual(activated,[order_id])


class UpgradeApplicationTests(unittest.TestCase):
    def test_upgrade_keeps_expiry_and_updates_only_device_limit(self):
        handle, path = tempfile.mkstemp();os.close(handle)
        try:
            APP.DB = path
            APP.init()
            expiry = int(APP.time.time() * 1000) + 20 * 86_400_000
            captured = {}

            def fake_xapi(method, route, body=None):
                if method == "GET" and "/clients/get/" in route:
                    return {"obj": {"client": {"email": "familia-io", "uuid": "u-1", "subId": "s-1", "expiryTime": expiry, "limitIp": 1, "enable": True}}}
                if method == "POST" and "/clients/update/" in route:
                    captured.update(body)
                    return {"success": True}
                if method == "GET" and "/clients/links/" in route:
                    return {"obj": ["vless://example"]}
                raise AssertionError((method, route))

            APP.xapi = fake_xapi
            APP.provisional_payment({"plan": "m1_3", "target_email": "familia-io", "xui_email": "familia-io", "order_kind": "upgrade", "id": 999})
            self.assertEqual(captured["limitIp"], 3)
            self.assertEqual(captured["expiryTime"], expiry)
        finally:
            os.unlink(path)


class OrderCancellationTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp();os.close(handle);APP.DB=self.path;APP.init()
        with APP.db() as connection:self.user_id=connection.execute("SELECT id FROM users WHERE username=?",(APP.ADMIN,)).fetchone()[0]

    def tearDown(self):
        os.unlink(self.path)

    def test_pending_order_can_be_canceled_without_touching_xui(self):
        with APP.db() as connection:
            cursor=connection.execute("INSERT INTO orders(token,plan,amount,customer,status,created_at,user_id,order_kind) VALUES('pending-token','m1',100,'admin','pending',?,?, 'new')",(int(APP.time.time()),self.user_id));order_id=cursor.lastrowid
        APP.xapi=lambda *args,**kwargs: self.fail("3x-ui must not be called for a pending order")
        ok,message=APP.cancel_order(order_id,user_id=self.user_id)
        self.assertTrue(ok,message)
        with APP.db() as connection:self.assertEqual(connection.execute("SELECT status FROM orders WHERE id=?",(order_id,)).fetchone()[0],"canceled")

    def test_multiple_new_orders_are_allowed_but_same_subscription_change_is_blocked(self):
        with APP.db() as connection:
            connection.execute("INSERT INTO orders(token,plan,amount,customer,status,created_at,user_id,order_kind) VALUES('new-token','m1',100,'admin','pending',?,?,'new')",(int(APP.time.time()),self.user_id))
            self.assertIsNone(APP.unfinished_order_for(connection,self.user_id,None,'new',None))
            connection.execute("INSERT INTO orders(token,plan,amount,customer,status,created_at,user_id,order_kind,target_email) VALUES('renew-token','m1',100,'admin','pending',?,?,'renew','familia-io')",(int(APP.time.time()),self.user_id))
            self.assertIsNotNone(APP.unfinished_order_for(connection,self.user_id,None,'renew','familia-io'))
            self.assertIsNone(APP.unfinished_order_for(connection,self.user_id,None,'renew','second-subscription'))

    def test_review_order_cannot_be_canceled_after_payment_started(self):
        expiry=int(APP.time.time()*1000)+20*86_400_000
        with APP.db() as connection:
            cursor=connection.execute("INSERT INTO orders(token,plan,amount,customer,status,xui_email,created_at,user_id,order_kind,target_email,previous_expiry,previous_limit_ip,new_client) VALUES('review-token','m1_3',60,'admin','review','familia-io',?,?,'upgrade','familia-io',?,1,0)",(int(APP.time.time()),self.user_id,expiry));order_id=cursor.lastrowid
        APP.xapi=lambda *args,**kwargs: self.fail("3x-ui must not be changed when cancellation is refused")
        ok,message=APP.cancel_order(order_id,user_id=self.user_id)
        self.assertFalse(ok);self.assertIn("нельзя",message)
        with APP.db() as connection:self.assertEqual(connection.execute("SELECT status FROM orders WHERE id=?",(order_id,)).fetchone()[0],"review")

    def test_pending_order_expires_after_ten_minutes(self):
        created=int(APP.time.time())-APP.PAYMENT_WINDOW-1
        with APP.db() as connection:
            cursor=connection.execute("INSERT INTO orders(token,plan,amount,customer,status,created_at,expires_at,user_id,order_kind) VALUES('expired-token','m1',100,'admin','pending',?,?,?,'new')",(created,created+APP.PAYMENT_WINDOW,self.user_id));order_id=cursor.lastrowid
        expired=APP.expire_order(APP.getorder('expired-token'))
        self.assertEqual(expired['status'],'expired')
        ok,message,status=APP.confirm_payment(expired)
        self.assertFalse(ok);self.assertEqual(status,'expired')


class PaymentReviewIdempotencyTests(unittest.TestCase):
    def test_only_first_admin_confirmation_changes_review_order(self):
        handle,path=tempfile.mkstemp();os.close(handle);previous=APP.DB
        try:
            APP.DB=path;APP.init()
            with APP.db() as connection:
                cursor=connection.execute("INSERT INTO orders(token,plan,amount,customer,status,created_at,order_kind) VALUES('review-once','m1',100,'familia-io','review',?,'new')",(int(APP.time.time()),));order_id=cursor.lastrowid
            first=APP.review_payment(order_id,True);second=APP.review_payment(order_id,True)
            self.assertTrue(first[0]);self.assertFalse(second[0])
            with APP.db() as connection:
                self.assertEqual(connection.execute('SELECT status FROM orders WHERE id=?',(order_id,)).fetchone()[0],'active')
                self.assertEqual(connection.execute("SELECT count(*) FROM bot_events WHERE order_id=? AND event_type='approved'",(order_id,)).fetchone()[0],1)
        finally:
            APP.DB=previous;os.unlink(path)

    def test_security_log_keeps_event_without_secret_fields(self):
        handle,path=tempfile.mkstemp();os.close(handle);previous=APP.DB
        try:
            APP.DB=path;APP.init();APP.security_event('login_failed','familia-io','127.0.0.1',{'entry':'public'})
            with APP.db() as connection:event=connection.execute('SELECT * FROM security_events').fetchone()
            self.assertEqual(event['event_type'],'login_failed');self.assertEqual(event['username'],'familia-io');self.assertNotIn('password',event['details'])
        finally:
            APP.DB=previous;os.unlink(path)

    def test_interrupted_admin_review_returns_to_review_after_restart(self):
        handle,path=tempfile.mkstemp();os.close(handle);previous=APP.DB
        try:
            APP.DB=path;APP.init()
            with APP.db() as connection:connection.execute("INSERT INTO orders(token,plan,amount,customer,status,created_at,order_kind) VALUES('interrupted-review','m1',100,'familia-io','approving',?,'new')",(int(APP.time.time()),))
            APP.init()
            with APP.db() as connection:status=connection.execute("SELECT status FROM orders WHERE token='interrupted-review'").fetchone()[0]
            self.assertEqual(status,'review')
        finally:
            APP.DB=previous;os.unlink(path)


class SiteNotificationTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp();os.close(handle);APP.DB=self.path;APP.init()
        self.original_expiry_notifications=APP.site_expiry_notifications
        APP.site_expiry_notifications=lambda username: []
        with APP.db() as connection:
            self.user_id=connection.execute("SELECT id FROM users WHERE username=?",(APP.ADMIN,)).fetchone()[0]
            now=int(APP.time.time())
            cursor=connection.execute("INSERT INTO support_tickets(telegram_id,customer,status,created_at,updated_at,user_id,source) VALUES(0,?,'answered',?,?,?,'site')",(APP.ADMIN,now,now,self.user_id))
            self.ticket_id=cursor.lastrowid
            cursor=connection.execute("INSERT INTO support_messages(ticket_id,sender,message,created_at) VALUES(?,'admin','Проверьте подключение ещё раз',?)",(self.ticket_id,now))
            self.message_id=cursor.lastrowid

    def tearDown(self):
        APP.site_expiry_notifications=self.original_expiry_notifications
        os.unlink(self.path)

    def test_support_reply_is_unread_and_links_to_exact_ticket(self):
        user_id,items=APP.site_notification_items(APP.ADMIN)
        self.assertEqual(user_id,self.user_id)
        reply=next(x for x in items if x['key']==f'support:{self.message_id}')
        self.assertTrue(reply['unread'])
        self.assertEqual(reply['href'],f'/support/ticket?id={self.ticket_id}')
        self.assertIn('Проверьте подключение',reply['message'])

    def test_read_notification_is_not_counted_as_unread(self):
        APP.mark_site_notifications_read(self.user_id,[f'support:{self.message_id}'])
        _,items=APP.site_notification_items(APP.ADMIN)
        self.assertEqual(sum(1 for x in items if x['unread']),0)


class AdminIdentityTests(unittest.TestCase):
    def test_renamed_admin_is_not_recreated_under_environment_login(self):
        handle,path=tempfile.mkstemp();os.close(handle);previous=APP.DB
        try:
            APP.DB=path;APP.init()
            with APP.db() as connection:connection.execute("UPDATE users SET username='renamed-admin' WHERE role='admin'")
            APP.init()
            with APP.db() as connection:
                admins=connection.execute("SELECT username FROM users WHERE role='admin'").fetchall()
                old=connection.execute('SELECT 1 FROM users WHERE username=?',(APP.ADMIN,)).fetchone()
            self.assertEqual([x[0] for x in admins],['renamed-admin']);self.assertIsNone(old)
        finally:
            APP.DB=previous;os.unlink(path)


class TelegramSiteRegistrationTests(unittest.TestCase):
    def setUp(self):
        handle,self.path=tempfile.mkstemp();os.close(handle);APP.DB=self.path;APP.init()
        self.original_secret=APP.API_SECRET;self.original_panel_clients=APP.panel_clients;self.original_send_account_email=APP.send_account_email;self.original_smtp_host=APP.SMTP_HOST
        APP.API_SECRET='registration-test-secret';APP.panel_clients=lambda: [];APP.SMTP_HOST='smtp.test'
        self.server=APP.ThreadingHTTPServer(('127.0.0.1',0),APP.H)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join(timeout=2)
        APP.API_SECRET=self.original_secret;APP.panel_clients=self.original_panel_clients;APP.send_account_email=self.original_send_account_email;APP.SMTP_HOST=self.original_smtp_host;os.unlink(self.path)

    def register(self):
        body=json.dumps({'telegram_id':987654321,'login':'familia-io','tg_username':'test_user'}).encode()
        request=urllib.request.Request(f'http://127.0.0.1:{self.server.server_port}/api/bot/profile',body,headers={'X-API-Key':APP.API_SECRET,'Content-Type':'application/json'})
        with urllib.request.urlopen(request,timeout=3) as response:return json.load(response)

    def admin_cookie(self):
        with APP.db() as connection:epoch=connection.execute('SELECT session_epoch FROM users WHERE username=?',(APP.ADMIN,)).fetchone()[0] or 0
        body=APP.base64.urlsafe_b64encode(f'{APP.ADMIN}|{int(APP.time.time()+600)}|{epoch}'.encode()).decode().rstrip('=');signature=APP.hmac.new(APP.SESSION,body.encode(),APP.hashlib.sha256).hexdigest()
        return 'vpn_session='+body+'.'+signature

    def user_cookie(self):
        with APP.db() as connection:
            row=connection.execute("SELECT username,session_epoch FROM users WHERE role='test'").fetchone()
        body=APP.base64.urlsafe_b64encode(f'{row[0]}|{int(APP.time.time()+600)}|{row[1] or 0}'.encode()).decode().rstrip('=');signature=APP.hmac.new(APP.SESSION,body.encode(),APP.hashlib.sha256).hexdigest()
        return 'vpn_session='+body+'.'+signature

    def test_bot_registration_creates_working_site_credentials_once(self):
        result=self.register();password=result['password']
        self.assertTrue(result['site_account_created']);self.assertEqual(len(password),12);self.assertEqual(len(result['recovery_codes']),8)
        with APP.db() as connection:
            user=connection.execute('SELECT * FROM users WHERE username=?',('familia-io',)).fetchone()
            profile=connection.execute('SELECT login FROM bot_profiles WHERE telegram_id=?',(987654321,)).fetchone()
        self.assertEqual(user['telegram_id'],987654321);self.assertEqual(profile['login'],'familia-io')
        actual=APP.hashlib.scrypt(password.encode(),salt=APP.base64.b64decode(user['salt']),n=16384,r=8,p=1)
        self.assertEqual(APP.base64.b64encode(actual).decode(),user['pwhash'])
        with APP.db() as connection:
            self.assertTrue(APP.consume_recovery_code(connection,user['id'],result['recovery_codes'][0]))
            self.assertFalse(APP.consume_recovery_code(connection,user['id'],result['recovery_codes'][0]))
        repeated=self.register();self.assertFalse(repeated['site_account_created']);self.assertIsNone(repeated.get('password'))

    def test_email_registration_verification_login_and_password_reset(self):
        sent=[];APP.send_account_email=lambda email,kind,token,username:sent.append((email,kind,token,username))
        body='email=person%40example.com&username=mailuser1&password=Strong1%21&password_confirm=Strong1%21&terms=1'
        connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3);connection.request('POST','/register',body,{'Content-Type':'application/x-www-form-urlencoded'});response=connection.getresponse();response.read()
        self.assertEqual(response.status,201);self.assertEqual(sent[0][0:2],('person@example.com','verify'))
        connection.request('POST','/login','username=person%40example.com&password=Strong1%21',{'Content-Type':'application/x-www-form-urlencoded'});response=connection.getresponse();response.read();self.assertEqual(response.status,403)
        connection.request('GET','/verify-email?token='+sent[0][2]);response=connection.getresponse();response.read();self.assertEqual(response.status,200)
        connection.request('POST','/login','username=person%40example.com&password=Strong1%21',{'Content-Type':'application/x-www-form-urlencoded'});response=connection.getresponse();response.read();self.assertEqual(response.status,303)
        connection.request('POST','/forgot-password','email=person%40example.com',{'Content-Type':'application/x-www-form-urlencoded'});response=connection.getresponse();response.read();self.assertEqual(response.status,200);self.assertEqual(sent[-1][1],'reset')
        token=sent[-1][2];connection.request('POST','/reset-password',f'token={token}&password=Changed2%21&password_confirm=Changed2%21',{'Content-Type':'application/x-www-form-urlencoded'});response=connection.getresponse();response.read();connection.close();self.assertEqual(response.status,200)
        with APP.db() as database:user=database.execute("SELECT * FROM users WHERE email='person@example.com'").fetchone()
        self.assertTrue(user['email_verified_at']);self.assertTrue(APP.password_ok(user,'Changed2!'))

    def test_admin_mail_settings_encrypt_passwords(self):
        body='smtp_host=smtp.example.com&smtp_port=587&smtp_username=support%40my.domain.ru&smtp_password=MailSecret1%21&smtp_starttls=1&imap_host=imap.example.com&imap_port=993&imap_username=support%40my.domain.ru&imap_password=InboxSecret2%21&imap_ssl=1&mail_from=support%40my.domain.ru&reply_to=support%40my.domain.ru'
        connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3);connection.request('POST','/admin/mail/settings',body,{'Content-Type':'application/x-www-form-urlencoded','Cookie':self.admin_cookie()});response=connection.getresponse();response.read();connection.close()
        self.assertEqual(response.status,303)
        with APP.db() as database:row=database.execute('SELECT * FROM mail_settings WHERE id=1').fetchone()
        self.assertNotIn('MailSecret1!',row['smtp_password_enc']);self.assertNotIn('InboxSecret2!',row['imap_password_enc'])
        settings=APP.mail_settings();self.assertEqual(settings['smtp_password'],'MailSecret1!');self.assertEqual(settings['imap_password'],'InboxSecret2!')

    def test_admin_backup_gui_lists_archive_and_queues_creation(self):
        original=APP.backup_control;calls=[]
        def fake(*args):
            calls.append(args)
            if args==('list',):return {'archives':[{'name':'home-vpn-20260131-120000.tar.gz','size':4096,'created_at':1769850000,'valid':True}],'state':{'status':'success','message':'Резервная копия создана'}}
            return {'ok':True,'queued':True}
        APP.backup_control=fake
        try:
            connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3)
            connection.request('GET','/admin/backups',headers={'Cookie':self.admin_cookie()});response=connection.getresponse();content=response.read().decode()
            self.assertEqual(response.status,200);self.assertIn('home-vpn-20260131-120000.tar.gz',content);self.assertIn('Сайт и 3x-ui',content)
            connection.request('POST','/admin/backups/create','',{'Content-Type':'application/x-www-form-urlencoded','Cookie':self.admin_cookie()});response=connection.getresponse();response.read();connection.close()
            self.assertEqual(response.status,303);self.assertIn(('queue-create',),calls)
        finally:APP.backup_control=original

    def test_guest_support_creates_separate_ticket_and_secret_chat_link(self):
        body='name=Guest+User&contact=&message=Please+help+with+VPN'
        connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3);connection.request('POST','/guest-support/create',body,{'Content-Type':'application/x-www-form-urlencoded'});response=connection.getresponse();response.read();location=response.getheader('Location')
        self.assertEqual(response.status,303);self.assertTrue(location.startswith('/guest-support?token='))
        connection.request('GET',location);response=connection.getresponse();content=response.read().decode();connection.close()
        self.assertEqual(response.status,200);self.assertIn('Сохраните эту личную ссылку',content)
        with APP.db() as connection:self.assertEqual(connection.execute("SELECT source FROM support_tickets ORDER BY id DESC LIMIT 1").fetchone()[0],'guest')

    def test_password_login_requires_valid_totp_when_enabled(self):
        secret=APP.new_totp_secret()
        with APP.db() as connection:connection.execute('UPDATE users SET totp_secret=?,twofa_enabled=1 WHERE username=?',(secret,APP.ADMIN))
        body=f'username={APP.ADMIN}&password={APP.ADMIN_PASSWORD}'
        connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3);connection.request('POST',APP.ADMIN_ENTRY,body,{'Content-Type':'application/x-www-form-urlencoded'});response=connection.getresponse();response.read()
        self.assertEqual(response.status,303);self.assertEqual(response.getheader('Location'),'/2fa-login');cookie=response.getheader('Set-Cookie').split(';',1)[0]
        code=APP.totp_code(secret);connection.request('POST','/2fa-login',f'code={code}',{'Content-Type':'application/x-www-form-urlencoded','Cookie':cookie});response=connection.getresponse();response.read()
        self.assertEqual(response.status,303);self.assertEqual(response.getheader('Location'),'/mode');self.assertIn('vpn_session=',response.getheader('Set-Cookie'))
        connection.close()

    def test_admin_password_is_rejected_on_public_login(self):
        body=f'username={APP.ADMIN}&password={APP.ADMIN_PASSWORD}'
        connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3);connection.request('POST','/login',body,{'Content-Type':'application/x-www-form-urlencoded'});response=connection.getresponse();content=response.read().decode();connection.close()
        self.assertEqual(response.status,401);self.assertIn('Неправильный логин или пароль',content)
        self.assertNotIn('Оформление подписки',content);self.assertNotIn('Вход администратора через общую',content)

    def test_test_user_is_rejected_publicly_and_allowed_on_hidden_entry(self):
        body=f'username={APP.USER}&password={APP.PASSWORD}'
        connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3)
        connection.request('POST','/login',body,{'Content-Type':'application/x-www-form-urlencoded'});response=connection.getresponse();content=response.read().decode()
        self.assertEqual(response.status,401);self.assertIn('Неправильный логин или пароль',content);self.assertIsNone(response.getheader('Set-Cookie'))
        connection.request('POST',APP.TEST_ENTRY,body,{'Content-Type':'application/x-www-form-urlencoded'});response=connection.getresponse();response.read();connection.close()
        self.assertEqual(response.status,303);self.assertEqual(response.getheader('Location'),'/');self.assertIn('vpn_session=',response.getheader('Set-Cookie'))

    def test_admin_can_create_and_change_multiple_test_users(self):
        cookie=self.admin_cookie();connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3)
        body='action=create&username=second-test&password=Abcdef1%21&password_confirm=Abcdef1%21'
        connection.request('POST','/admin/test-user',body,{'Content-Type':'application/x-www-form-urlencoded','Cookie':cookie});response=connection.getresponse();response.read()
        self.assertEqual(response.status,303)
        with APP.db() as database:
            created=database.execute("SELECT id FROM users WHERE username='second-test' AND role='test'").fetchone();self.assertIsNotNone(created)
        body=f'action=update&user_id={created[0]}&username=renamed-test&password=Newpass2%21&password_confirm=Newpass2%21'
        connection.request('POST','/admin/test-user',body,{'Content-Type':'application/x-www-form-urlencoded','Cookie':cookie});response=connection.getresponse();response.read()
        self.assertEqual(response.status,303)
        connection.request('GET',f'/api/bot/test-users?telegram_id={APP.ADMIN_TG}',headers={'X-API-Key':APP.API_SECRET});response=connection.getresponse();payload=json.loads(response.read());connection.close()
        self.assertEqual(response.status,200);self.assertIn('renamed-test',{x['username'] for x in payload['users']})
        with APP.db() as database:user=database.execute("SELECT * FROM users WHERE username='renamed-test'").fetchone()
        self.assertTrue(APP.password_ok(user,'Newpass2!'))

    def test_bot_order_is_created_for_selected_test_user(self):
        previous_admin_tg=APP.ADMIN_TG;APP.ADMIN_TG=123456789
        with APP.db() as database:
            APP.add_user(database,'selected-test','Abcdef1!','test');user_id=database.execute("SELECT id FROM users WHERE username='selected-test'").fetchone()[0]
        try:
            payload=json.dumps({'plan':'m1','action':'new','telegram_id':APP.ADMIN_TG,'customer':'selected-test','test_username':'selected-test'}).encode()
            connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3);connection.request('POST','/api/orders',payload,{'Content-Type':'application/json','X-API-Key':APP.API_SECRET});response=connection.getresponse();result=json.loads(response.read());connection.close()
            self.assertEqual(response.status,201,result)
            with APP.db() as database:order=database.execute('SELECT user_id,customer,telegram_id FROM orders WHERE id=?',(result['id'],)).fetchone()
            self.assertEqual(order['user_id'],user_id);self.assertEqual(order['customer'],'selected-test');self.assertEqual(order['telegram_id'],APP.ADMIN_TG)
        finally:APP.ADMIN_TG=previous_admin_tg

    def test_public_telegram_login_rejects_test_role(self):
        now=int(APP.time.time());nonce='test-role-public-login'
        with APP.db() as connection:
            user=connection.execute("SELECT id FROM users WHERE role='test'").fetchone();connection.execute('UPDATE users SET telegram_id=? WHERE id=?',(7654321,user['id']));connection.execute("INSERT INTO auth_sessions(nonce,status,expires_at) VALUES(?,'pending',?)",(nonce,now+300))
        payload=json.dumps({'nonce':nonce,'telegram_id':7654321,'tg_username':'test-account'}).encode()
        connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3);connection.request('POST','/api/bot/telegram-auth',payload,{'Content-Type':'application/json','X-API-Key':APP.API_SECRET});response=connection.getresponse();content=json.loads(response.read());connection.close()
        self.assertEqual(response.status,403);self.assertEqual(content['error'],'Вход через общую форму запрещён')

    def test_tariff_can_be_added_to_cart(self):
        body='action=new&plan=m1_2&destination=cart'
        connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3);connection.request('POST','/orders',body,{'Content-Type':'application/x-www-form-urlencoded','Cookie':self.user_cookie()});response=connection.getresponse();response.read()
        self.assertEqual(response.status,303);self.assertEqual(response.getheader('Location'),'/cart')
        connection.request('GET','/cart',headers={'Cookie':self.user_cookie()});response=connection.getresponse();content=response.read().decode();connection.close()
        self.assertEqual(response.status,200);self.assertIn('Ваши тарифы',content);self.assertIn('190 ₽',content);self.assertIn('Заказ №',content)

    def test_admin_can_clear_completed_maintenance_history_only(self):
        now=int(APP.time.time())
        with APP.db() as connection:
            completed=connection.execute("INSERT INTO maintenance_notices(kind,message,status,created_at,completed_at) VALUES('planned','done','completed',?,?)",(now,now)).lastrowid
            active=connection.execute("INSERT INTO maintenance_notices(kind,message,status,created_at) VALUES('unplanned','active','active',?)",(now,)).lastrowid
            connection.execute('INSERT INTO maintenance_deliveries(notice_id,telegram_id,delivered_at) VALUES(?,?,?)',(completed,123,now))
        connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3);connection.request('POST','/admin/maintenance/clear','',{'Content-Type':'application/x-www-form-urlencoded','Cookie':self.admin_cookie()});response=connection.getresponse();response.read();connection.close()
        self.assertEqual(response.status,303)
        with APP.db() as database:
            self.assertIsNone(database.execute('SELECT 1 FROM maintenance_notices WHERE id=?',(completed,)).fetchone())
            self.assertIsNotNone(database.execute('SELECT 1 FROM maintenance_notices WHERE id=?',(active,)).fetchone())
            self.assertIsNone(database.execute('SELECT 1 FROM maintenance_deliveries WHERE notice_id=?',(completed,)).fetchone())


if __name__ == "__main__":
    unittest.main()
