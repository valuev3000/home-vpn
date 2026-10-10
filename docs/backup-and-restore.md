# Раздельное резервное копирование HOME-VPN

В HOME-VPN `0.0.5-pre` используются три независимых контура. Прежняя отдельная backup-панель, pull-agent и сервер
`home-vpn-backup` удалены из проекта.

| Контур | Команда/timer | Состояние по умолчанию |
|---|---|---|
| 3x-ui | `home-vpn-kit-backup.timer` | база и конфигурация KIT/3x-ui |
| сайт | `home-vpn-site-backup.timer` | `/var/lib/vpn-shop`, публичный `config.json`; без env-секретов |
| Telegram-бот | `home-vpn-bot-backup.timer` | `/var/lib/vpn-bot`, меню; без токенов и паролей |

Сайт и бот создаются командой `home-vpn-state-backup create site|bot`. Каждый
архив получает SHA-256, безопасно распаковывается во временный каталог, а все
найденные SQLite-базы проходят `PRAGMA integrity_check`.

Env-файлы содержат токены, API secrets и пароли и по умолчанию не включаются.
Их можно включить только с немедленным шифрованием `age`:

```bash
sudo home-vpn-state-backup create site --include-secrets --age-recipient age1...
```

Открытый архив с секретами сценарий создавать отказывается. Закрытый ключ age
нельзя хранить на той же VPS или в Git.

## Что сохраняется

Команда `kit backup` формирует архив конфигурации 3x-ui. Обёртка
`home-vpn-kit-backup` дополнительно:

- проверяет SQLite-базу `/etc/x-ui/x-ui.db` через `PRAGMA integrity_check`;
- запускает штатный `kit backup`;
- находит созданный `/root/kit-backup-*.tar.gz`;
- сохраняет рядом SHA-256;
- распаковывает архив во временный каталог и проверяет читаемость SQLite-баз;
- удаляет локальные архивы старше срока хранения.

Если HOME-VPN установлен поверх обычной 3x-ui без команды `kit`, тот же unit
создаёт `/root/xui-backup-*.tar.gz` непосредственно из базы 3x-ui, её служебных
файлов и Nginx. Проверка SQLite, SHA-256, тестовая распаковка и retention
остаются обязательными.

Архивы доступны только root. Пароли, токены и закрытые ключи нельзя добавлять
в Git или пересылать в журнал задания.

## Расписание и срок хранения

```bash
sudo systemctl enable --now home-vpn-kit-backup.timer
sudo systemctl list-timers home-vpn-kit-backup.timer
```

Срок хранения по умолчанию — 30 дней. Чтобы изменить его, создайте drop-in:

```bash
sudo systemctl edit home-vpn-kit-backup.service
```

```ini
[Service]
Environment=KIT_BACKUP_RETENTION_DAYS=60
```

Затем выполните:

```bash
sudo systemctl daemon-reload
sudo systemctl start home-vpn-kit-backup.service
```

## Проверка архива

```bash
sudo systemctl status home-vpn-kit-backup.service --no-pager
sudo journalctl -u home-vpn-kit-backup.service -n 100 --no-pager
sudo sha256sum -c /root/kit-backup-YYYYMMDD-HHMM.tar.gz.sha256
sudo tar -tzf /root/kit-backup-YYYYMMDD-HHMM.tar.gz >/dev/null
sudo home-vpn-state-backup verify site /var/backups/home-vpn/home-vpn-site-YYYYMMDD-HHMMSS.tar.gz --restore-dir /var/tmp/site-restore-test
sudo home-vpn-state-backup verify bot /var/backups/home-vpn/home-vpn-bot-YYYYMMDD-HHMMSS.tar.gz --restore-dir /var/tmp/bot-restore-test
```

Безопасная тестовая распаковка не затрагивает работающую панель:

```bash
restore_dir=$(sudo mktemp -d /var/tmp/kit-restore-test.XXXXXX)
sudo tar -xzf /root/kit-backup-YYYYMMDD-HHMM.tar.gz -C "$restore_dir"
sudo find "$restore_dir" -type f -name '*.db' \
  -exec sqlite3 {} 'PRAGMA integrity_check;' \;
```

Удалите временный каталог после проверки явным абсолютным путём.

## Восстановление

1. Изолируйте неисправную VPS и сохраните снимок её диска до окончания проверки.
2. Разверните совместимую ОС и тот же зафиксированный установщик протоколов.
3. Скопируйте выбранный архив на новый узел и проверьте SHA-256.
4. Сначала распакуйте архив в отдельный временный каталог.
5. Проверьте SQLite через `PRAGMA integrity_check` и наличие конфигурации.
6. Выполните команду восстановления, поддерживаемую зафиксированным установщиком:

   ```bash
   sudo bash /root/3x-ui-protocol-installer.sh \
     --restore /root/kit-backup-YYYYMMDD-HHMM.tar.gz
   ```

7. Проверьте права файлов, TLS, firewall и systemd units.
8. Запустите 3x-ui и выполните smoke-test панели и каждого inbound.
9. На центральной VPS проверьте синхронизацию inbound; на ноде бота — Telegram-бот и
   Emergency MTProto.
10. Создайте новый архив, снова проверьте SHA-256 и тестовую распаковку.

Не восстанавливайте архив поверх работающего production-узла без отдельного
окна работ и подтверждения владельца системы.

## Защита от потери VPS

Локальный архив не является off-site-копией. Снимки и архивы VPS должны
дополнительно забираться средствами провайдера или NAS в хранилище, к которому сам
VPN-узел не имеет прав удаления. Этот инфраструктурный экспорт намеренно не
реализован веб-приложением HOME-VPN и не требует второго GUI-сервера проекта.
