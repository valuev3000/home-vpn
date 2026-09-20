#!/usr/bin/env python3
import importlib.util
import json
import os
import tempfile
import time
import unittest


SPEC = importlib.util.spec_from_file_location("vpn_bot", os.path.join(os.path.dirname(__file__), "bot.py"))
BOT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BOT)


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


if __name__ == "__main__":
    unittest.main()
