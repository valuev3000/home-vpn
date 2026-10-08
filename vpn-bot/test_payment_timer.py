#!/usr/bin/env python3
import importlib.util
import importlib.machinery
import contextlib
import io
import json
import os
import tempfile
import time
import unittest


SPEC = importlib.util.spec_from_file_location("vpn_bot", os.path.join(os.path.dirname(__file__), "bot.py"))
BOT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BOT)
ADMIN_LOADER=importlib.machinery.SourceFileLoader("vpn_bot_admin_control",os.path.join(os.path.dirname(__file__),"admin-control.py"))
ADMIN_SPEC=importlib.util.spec_from_loader("vpn_bot_admin_control",ADMIN_LOADER);ADMIN_CONTROL=importlib.util.module_from_spec(ADMIN_SPEC);ADMIN_LOADER.exec_module(ADMIN_CONTROL)


class PaymentTimerTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp();os.close(handle);os.unlink(self.path)
        BOT.PAYMENT_TIMERS_STATE = self.path
        BOT.PAYMENT_TIMERS.clear()
        self.calls = []
        BOT.tg = lambda method, data=None, files=None: self.calls.append((method, data)) or {"ok": True}

    def tearDown(self):
        if os.path.exists(self.path):os.unlink(self.path)

    def test_countdown_is_persisted_and_message_is_edited(self):
        order={"id":21,"expires_at":int(time.time())+600}
        BOT.track_payment_timer(order,123456789,77,"Заказ №21","{}")
        BOT.PAYMENT_TIMERS.clear();BOT.load_payment_timers();BOT.update_payment_timers()
        self.assertIn("21",BOT.PAYMENT_TIMERS)
        self.assertEqual(self.calls[-1][0],"editMessageCaption")
        self.assertIn("До окончания оплаты",self.calls[-1][1]["caption"])

    def test_expired_timer_removes_payment_controls(self):
        BOT.PAYMENT_TIMERS["22"]={"chat_id":1,"message_id":2,"expires_at":int(time.time())-1,"base_caption":"x","markup":"{}","bucket":-1}
        BOT.update_payment_timers()
        self.assertNotIn("22",BOT.PAYMENT_TIMERS)
        self.assertIn("Время оплаты истекло",self.calls[-1][1]["caption"])
        self.assertNotIn("Оплатить",self.calls[-1][1]["reply_markup"])

    def test_review_status_stops_timer_and_moves_order_to_subscriptions(self):
        BOT.PAYMENT_TIMERS["23"]={"chat_id":1,"message_id":3,"expires_at":int(time.time())+600,"base_caption":"x","markup":"{}","bucket":-1}
        BOT.api=lambda path:{"id":23,"status":"review","xui_email":"familia-io"}
        BOT.sync_payment_timers()
        self.assertNotIn("23",BOT.PAYMENT_TIMERS)
        self.assertIn("Таймер остановлен",self.calls[-1][1]["caption"])
        self.assertIn("Мои подписки",self.calls[-1][1]["caption"])

    def test_menu_configuration_changes_label_and_hides_button(self):
        handle, menu_path = tempfile.mkstemp();os.close(handle)
        previous = BOT.MENU_CONFIG
        try:
            config=json.loads(json.dumps(BOT.MENU_DEFAULT,ensure_ascii=False))
            config["items"]["new"]["label"]="🛒 Оформить доступ"
            config["items"]["support"]["enabled"]=False
            with open(menu_path,"w",encoding="utf-8") as output:json.dump(config,output,ensure_ascii=False)
            BOT.MENU_CONFIG=menu_path;markup=BOT.bot_services_menu()
            self.assertIn("Оформить доступ",markup)
            self.assertNotIn("menu:support",markup)
        finally:
            BOT.MENU_CONFIG=previous;os.unlink(menu_path)

    def test_menu_configuration_changes_button_order(self):
        handle, menu_path = tempfile.mkstemp();os.close(handle)
        previous = BOT.MENU_CONFIG
        try:
            config=json.loads(json.dumps(BOT.MENU_DEFAULT,ensure_ascii=False))
            config["order"]=["status","new"]+[key for key in config["order"] if key not in ("status","new")]
            with open(menu_path,"w",encoding="utf-8") as output:json.dump(config,output,ensure_ascii=False)
            BOT.MENU_CONFIG=menu_path;markup=BOT.bot_services_menu()
            self.assertLess(markup.index("status:show"),markup.index("buy:new"))
        finally:
            BOT.MENU_CONFIG=previous;os.unlink(menu_path)

    def test_welcome_text_is_escaped(self):
        handle, menu_path = tempfile.mkstemp();os.close(handle)
        previous = BOT.MENU_CONFIG
        try:
            config=json.loads(json.dumps(BOT.MENU_DEFAULT,ensure_ascii=False));config["welcome_title"]="Дом <сеть>"
            with open(menu_path,"w",encoding="utf-8") as output:json.dump(config,output,ensure_ascii=False)
            BOT.MENU_CONFIG=menu_path
            self.assertIn("Дом &lt;сеть&gt;",BOT.welcome_text())
        finally:
            BOT.MENU_CONFIG=previous;os.unlink(menu_path)

    def test_command_setup_failure_does_not_stop_bot_startup(self):
        handle,state=tempfile.mkstemp();os.close(handle);os.unlink(state)
        previous_state,previous_tg=BOT.COMMANDS_STATE,BOT.tg
        try:
            BOT.COMMANDS_STATE=state;BOT.tg=lambda *args,**kwargs:(_ for _ in ()).throw(BOT.TelegramAPIError("Too Many Requests",30))
            BOT.configure_commands([{"command":"start","description":"Start"}],[])
            self.assertFalse(os.path.exists(state))
        finally:
            BOT.COMMANDS_STATE=previous_state;BOT.tg=previous_tg


class SiteCatalogIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.original_api=BOT.api;self.original_admin=BOT.ADMIN_ID;BOT.CATALOG_CACHE["value"]=None

    def tearDown(self):
        BOT.api=self.original_api;BOT.ADMIN_ID=self.original_admin;BOT.CATALOG_CACHE["value"]=None

    def test_catalog_is_requested_each_time_and_last_success_is_fallback(self):
        calls=[];value={"currency":"€","max_devices":7,"tariffs":[{"devices":7}]}
        BOT.api=lambda path:calls.append(path) or value
        self.assertEqual(BOT.catalog()["currency"],"€");self.assertEqual(BOT.catalog_max_devices(),7)
        self.assertEqual(len(calls),2)
        BOT.api=lambda path:(_ for _ in ()).throw(OSError("offline"))
        self.assertEqual(BOT.catalog()["currency"],"€")

    def test_personal_catalog_sends_user_context(self):
        paths=[];BOT.ADMIN_ID=77
        BOT.api=lambda path:paths.append(path) or {"tariffs":[]}
        BOT.catalog(77)
        self.assertEqual(paths[0],"/api/bot/catalog?telegram_id=77")


class AdminNotificationTests(unittest.TestCase):
    def test_panel_notification_targets_only_configured_administrator(self):
        original_environment,original_telegram=ADMIN_CONTROL.environment,ADMIN_CONTROL.telegram;calls=[]
        try:
            ADMIN_CONTROL.environment=lambda:{"TELEGRAM_ADMIN_ID":"123456","TELEGRAM_CHANNEL":"@must_not_receive"}
            ADMIN_CONTROL.telegram=lambda values,method,payload:calls.append((method,payload))
            with contextlib.redirect_stdout(io.StringIO()):ADMIN_CONTROL.notify()
        finally:
            ADMIN_CONTROL.environment,ADMIN_CONTROL.telegram=original_environment,original_telegram
        self.assertEqual(calls,[("sendMessage",{"chat_id":"123456","text":"✅ Тест панели управления ботом HOME-VPN выполнен успешно."})])


class EmergencyProxyReminderTests(unittest.TestCase):
    def setUp(self):
        self.original_api,self.original_tg,self.original_guide=BOT.api,BOT.tg,BOT.PROXY_DISABLE_GUIDE

    def tearDown(self):
        BOT.api,BOT.tg,BOT.PROXY_DISABLE_GUIDE=self.original_api,self.original_tg,self.original_guide

    def test_expired_proxy_sends_instruction_and_acknowledges_delivery(self):
        calls=[];acks=[]
        def api(path,body=None):
            if path=="/api/bot/emergency-reminders":return {"reminders":[{"telegram_id":42,"expires_at":100,"stage":"expired"}]}
            acks.append((path,body));return {"ok":True}
        BOT.api=api;BOT.tg=lambda method,data=None,files=None:calls.append((method,data,files)) or {"ok":True}
        BOT.PROXY_DISABLE_GUIDE="/missing/guide.png"
        self.assertEqual(BOT.deliver_emergency_reminders(),1)
        self.assertEqual(calls[0][0],"sendMessage");self.assertIn("Настройки прокси",calls[0][1]["text"])
        self.assertEqual(acks[0][0],"/api/bot/emergency-reminder-delivered")

    def test_ten_minute_warning_has_subscription_button(self):
        calls=[];BOT.api=lambda path,body=None:{"reminders":[{"telegram_id":7,"expires_at":200,"stage":"ten_minutes"}]} if body is None else {"ok":True}
        BOT.tg=lambda method,data=None,files=None:calls.append((method,data)) or {"ok":True}
        BOT.deliver_emergency_reminders()
        self.assertIn("через 10 минут",calls[0][1]["text"]);self.assertIn("menu:buy",calls[0][1]["reply_markup"])


class DomainConfigurationTests(unittest.TestCase):
    def setUp(self):
        handle,self.path=tempfile.mkstemp();os.close(handle);os.unlink(self.path)
        self.original_path,self.original_public=BOT.RUNTIME_CONFIG,BOT.PUBLIC;BOT.RUNTIME_CONFIG=self.path

    def tearDown(self):
        BOT.RUNTIME_CONFIG,BOT.PUBLIC=self.original_path,self.original_public
        if os.path.exists(self.path):os.unlink(self.path)

    def test_domain_without_scheme_is_saved_as_https(self):
        self.assertEqual(BOT.save_runtime_public("vpn.example.ru"),"https://vpn.example.ru")
        self.assertEqual(BOT.load_runtime_public("https://fallback.example"),"https://vpn.example.ru")

    def test_domain_rejects_paths_and_credentials(self):
        with self.assertRaises(ValueError):BOT.normalize_public_url("https://user:pass@example.ru/private")

    def test_agreement_uses_separate_html_links(self):
        BOT.PUBLIC="https://vpn.example.ru";text=BOT.login_agreement_text()
        self.assertIn('<a href="https://vpn.example.ru/terms">Условия использования</a>',text)
        self.assertIn('\n🔐 <a href="https://vpn.example.ru/privacy">',text)


if __name__ == "__main__":
    unittest.main()
