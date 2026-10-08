# Установка HOME-VPN

Актуальная предварительная версия — `0.0.4-pre`. Точная схема тестовых LXC и
команды полной настройки находятся в
[installation-0.0.4-pre.md](installation-0.0.4-pre.md).

## Роли узлов

- RU — сайт, API и управляющая панель 3x-ui без локальных VPN-inbound;
- PL — Telegram-бот, 10 протоколов 3X-UI_KIT v1.2 и Emergency MTProto;
- RU синхронизирует список удалённых PL-inbound;
- отдельный backup-server и HOME-VPN backup-agent не используются.

## Обычная установка компонентов

```bash
git clone https://github.com/YOUR_ACCOUNT/home-vpn.git
cd home-vpn
sudo bash install.sh
```

Мастер устанавливает или обновляет сайт, Telegram-бот, домен/TLS, почту и
другие компоненты отдельно. Секреты сохраняются только в `/etc/vpn-shop.env` и
`/etc/vpn-bot.env`; реальные значения нельзя добавлять в Git.

Основные команды:

```bash
home-vpn-manager status
sudo home-vpn-setup configure
sudo home-vpn-setup tls
sudo home-vpn-setup doctor
```

## Полная настройка тестовых LXC

С рабочей станции, имеющей SSH-доступ к Proxmox:

```bash
python3 scripts/configure-test-lxc-topology.py
python3 scripts/configure-test-lxc-topology.py \
  --apply --confirm 'REBUILD TEST LXC'
```

Первая команда показывает план и ничего не меняет. Вторая является намеренно
разрушительной для тестовых VPN-inbound: перед изменением она создаёт rollback,
очищает старые подключения и строит текущую схему из 11 протоколов. Production
серверы этим сценарием не обслуживаются.

## Прямой и каскадный режимы

Текущий режим по умолчанию:

```text
пользователь → PL → интернет
```

Опциональный каскад:

```text
пользователь → RU → PL → интернет
```

Переключение уже установленных LXC не удаляет inbound или пользователей:

```bash
python3 scripts/configure-test-lxc-topology.py \
  --mode-only --connection-mode cascade \
  --apply --confirm 'CONFIGURE CONNECTION MODE'

python3 scripts/configure-test-lxc-topology.py \
  --mode-only --connection-mode direct \
  --apply --confirm 'CONFIGURE CONNECTION MODE'
```

Каскад перенаправляет только VPN-порты. Сайт, административные панели и бот не
публикуются через этот механизм.

## Резервные копии

```bash
sudo systemctl enable --now home-vpn-kit-backup.timer
sudo systemctl start home-vpn-kit-backup.service
sudo journalctl -u home-vpn-kit-backup.service -n 100 --no-pager
```

Используется штатный `kit backup` с проверкой SQLite, SHA-256 и тестовой
распаковкой. Подробности: [backup-and-restore.md](backup-and-restore.md).

## Проверка

```bash
python3 -m unittest -q \
  vpn-shop/test_tariff_logic.py \
  vpn-bot/test_payment_timer.py
PYTHONPATH=migration python3 -m unittest -q \
  migration/test_migrate_existing_clients.py
python3 scripts/home-vpn-setup.py validate \
  --config config/config.example.json
```

После установки проверьте `x-ui`, `vpn-shop`, `vpn-bot`, `kit-sub` и
`home-vpn-kit-backup.timer` только на тех узлах, где соответствующий компонент
должен работать.
