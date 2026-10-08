# 3X-UI KIT v1.2 в HOME-VPN

Тестовый PL-узел использует штатную схему `3X-UI_KIT v1.2`: 11 inbound, подписки KIT и архивы, создаваемые командой `kit backup`. RU-узел содержит управляющую панель и синхронизированные представления PL-inbound, но не поднимает локальные VPN-inbound. Старый HOME-VPN backup-server, exporter и разделы резервного копирования в веб-панели не используются.

## Протоколы

На PL установлены REALITY, Hysteria2, XHTTP, VLESS-WS, Trojan-gRPC, VMess-WS, Shadowsocks, TUIC, AmneziaWG, AmneziaWG 3.1 и отдельный Emergency MTProto. Пользователей создавайте штатной командой KIT или через логику сайта; готовые ссылки и секреты не сохраняйте в Git.

## Режим подключения

Единый сценарий поддерживает два варианта без переноса inbound на RU:

- `direct` — пользователь подключается напрямую к PL;
- `cascade` — пользователь подключается к портам RU, RU перенаправляет трафик
  на те же inbound PL, а PL выходит в интернет.

В каскаде RU использует отдельную таблицу nftables и остаётся управляющим узлом
без локальных VPN-inbound. Переключение выполняется с `--mode-only` и меняет
публичный адрес в ссылках KIT. Команды приведены в
[installation-0.0.4-pre.md](installation-0.0.4-pre.md).

## Резервное копирование

`home-vpn-kit-backup.timer` ежедневно запускает `/usr/local/bin/kit backup`, проверяет структуру архива и выполняет `PRAGMA integrity_check` для сохранённой базы 3x-ui. Архивы остаются в `/root/kit-backup-*.tar.gz`; по умолчанию хранятся 30 дней.

Изменить срок хранения можно через systemd override:

```bash
sudo systemctl edit home-vpn-kit-backup.service
```

```ini
[Service]
Environment=KIT_BACKUP_RETENTION_DAYS=14
```

После изменения:

```bash
sudo systemctl daemon-reload
sudo systemctl restart home-vpn-kit-backup.timer
```

## Проверка

```bash
sudo systemctl start home-vpn-kit-backup.service
sudo journalctl -u home-vpn-kit-backup.service -n 30 --no-pager
sudo systemctl list-timers home-vpn-kit-backup.timer
```

Успешный запуск заканчивается строкой `verified: /root/kit-backup-...tar.gz`.

## Восстановление

Архив содержит секреты и должен храниться с правами `0600`. Перед восстановлением перенесите выбранный архив на сервер и используйте штатную команду KIT:

Штатная команда восстановления относится к установщику KIT, а не к утилите `kit`:

```bash
sudo bash /root/3x-ui-kit-v1.2.sh --restore /root/kit-backup-YYYYMMDD-HHMM.tar.gz
```

Если сохранённого установщика нет, сначала загрузите именно `scripts/3x-ui.sh` из тега `v1.2`, проверьте источник и затем передайте ему параметр `--restore`.

После восстановления проверьте:

```bash
sudo systemctl is-active x-ui nginx kit-sub vpn-shop
curl -ksSf https://127.0.0.1:8443/health
sudo /usr/local/bin/kit check
```

Полное восстановление заменяет конфигурацию и базу 3x-ui, поэтому выполняйте его только в окно обслуживания. Для безопасной тестовой проверки извлеките архив во временный каталог и проверьте сохранённую SQLite-базу; автоматический wrapper делает это при каждом создании копии.
