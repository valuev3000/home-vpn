# Установка HOME-VPN

Если развёртывание включает центральную VPS (желательно в RU) и одну или несколько зарубежных VPN-нод
либо один совмещённый зарубежный сервер, сначала выберите архитектуру в
[мастере многосерверной установки](topology-installation.md).

Актуальная предварительная версия — `0.0.5-pre`. Инструкция для этой версии находится в
[installation-0.0.5-pre.md](installation-0.0.5-pre.md).

## Роли узлов

- управляющая VPS — сайт, API, Telegram-бот и все HOME-VPN web-панели; в mode 3 это единственная иностранная VPS;
- каждая зарубежная VPS — 11 подключений 3x-ui;
- Telegram API-трафик бота балансируется по VLESS/XHTTP-выходам всех доступных зарубежных VPS;
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

## Полная настройка VPS

С рабочей станции, имеющей root SSH-доступ ко всем VPS:

```bash
python3 scripts/install-home-vpn-topology.py \
  --write-plan topology.local.json
python3 scripts/install-home-vpn-topology.py \
  --config topology.local.json \
  --apply --confirm 'INSTALL HOME VPN TOPOLOGY'
```

Первая команда создаёт локальный план и ничего не меняет. Вторая применяет
проверенный план; частично настроенные или несовместимые 3x-ui ноды не удаляются автоматически.

## Прямой и каскадный режимы

Текущий режим по умолчанию:

```text
пользователь → зарубежная VPS → интернет
```

Опциональный каскад:

```text
пользователь → центральная VPS → зарубежная VPS → интернет
```

Для переключения измените `mode`, `ingress_address` и `public_host` в локальном плане, сначала выполнив dry-run:

```bash
python3 scripts/install-home-vpn-topology.py --config topology.local.json
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
