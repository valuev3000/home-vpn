# Резервное копирование 3X-UI_KIT v1.2

В HOME-VPN `0.0.4-pre` используется штатная логика резервного копирования
3X-UI_KIT v1.2. Прежняя отдельная backup-панель, pull-agent и сервер
`home-vpn-backup` удалены из проекта.

## Что сохраняется

Команда `kit backup` формирует архив конфигурации 3x-ui. Обёртка
`home-vpn-kit-backup` дополнительно:

- проверяет SQLite-базу `/etc/x-ui/x-ui.db` через `PRAGMA integrity_check`;
- запускает штатный `kit backup`;
- находит созданный `/root/kit-backup-*.tar.gz`;
- сохраняет рядом SHA-256;
- распаковывает архив во временный каталог и проверяет читаемость SQLite-баз;
- удаляет локальные архивы старше срока хранения.

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

1. Изолируйте неисправный LXC и сохраните его диск до окончания проверки.
2. Разверните совместимую ОС и ту же версию 3X-UI_KIT.
3. Скопируйте выбранный архив на новый узел и проверьте SHA-256.
4. Сначала распакуйте архив в отдельный временный каталог.
5. Проверьте SQLite через `PRAGMA integrity_check` и наличие конфигурации.
6. Выполните штатное восстановление 3X-UI_KIT:

   ```bash
   sudo bash /root/3x-ui-kit-v1.2.sh \
     --restore /root/kit-backup-YYYYMMDD-HHMM.tar.gz
   ```

7. Проверьте права файлов, TLS, firewall и systemd units.
8. Запустите 3x-ui и выполните smoke-test панели и каждого inbound.
9. Для RU проверьте синхронизацию PL-inbound; для PL — Telegram-бот и
   Emergency MTProto.
10. Создайте новый архив, снова проверьте SHA-256 и тестовую распаковку.

Не восстанавливайте архив поверх работающего production-узла без отдельного
окна работ и подтверждения владельца системы.

## Защита от потери LXC

Локальный архив не является off-site-копией. Снимки и архивы LXC должны
дополнительно забираться средствами Proxmox/NAS в хранилище, к которому сам
VPN-узел не имеет прав удаления. Этот инфраструктурный экспорт намеренно не
реализован веб-приложением HOME-VPN и не требует второго GUI-сервера проекта.
