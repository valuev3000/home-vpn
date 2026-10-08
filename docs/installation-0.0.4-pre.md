# HOME-VPN 0.0.4-pre — тестовая RU/PL-топология

Версия `0.0.4-pre` использует следующую схему:

- LXC 112 (`192.168.10.231`) — сайт, API и управляющая панель 3x-ui;
- LXC 113 (`192.168.10.214`) — Telegram-бот и основная VPN-нода;
- на RU нет локальных VPN-inbound;
- RU-панель отображает синхронизированные PL-inbound как удалённый узел;
- на PL работают 10 протоколов 3X-UI_KIT v1.2 и Emergency MTProto;
- резервные копии обеих 3x-ui создаются штатной командой `kit backup`.

## Полная настройка тестовых LXC

Сценарий запускается с рабочей станции, где SSH-имя `proxmox` указывает на гипервизор. Перед перестройкой он создаёт rollback-копии обеих SQLite-баз и конфигураций.

Проверка параметров без изменений:

```bash
python3 scripts/configure-test-lxc-topology.py
```

Применение:

```bash
python3 scripts/configure-test-lxc-topology.py \
  --proxmox-host proxmox \
  --ru-ctid 112 \
  --pl-ctid 113 \
  --ru-address 192.168.10.231 \
  --pl-address 192.168.10.214 \
  --apply \
  --confirm 'REBUILD TEST LXC'
```

По умолчанию используется текущий прямой режим:
`пользователь → PL → интернет`. Для каскада
`пользователь → RU → PL → интернет` укажите:

```bash
python3 scripts/configure-test-lxc-topology.py \
  --connection-mode cascade \
  --apply \
  --confirm 'REBUILD TEST LXC'
```

В каскадном режиме RU принимает публичные VPN-порты и через отдельную таблицу
nftables перенаправляет их на существующие inbound PL. SNAT гарантирует обратный
путь `PL → RU → пользователь`, а соединение Xray с интернетом создаётся на PL.
Панели управления, сайт и бот не перенаправляются. Ссылки KIT переключаются на
RU-адрес. Локальные VPN-inbound на RU при этом не создаются.

Переключить уже настроенные узлы без переустановки протоколов и без изменения
клиентов можно через `--mode-only`:

```bash
# Включить каскад
python3 scripts/configure-test-lxc-topology.py \
  --mode-only --connection-mode cascade \
  --apply --confirm 'CONFIGURE CONNECTION MODE'

# Вернуть прямой режим
python3 scripts/configure-test-lxc-topology.py \
  --mode-only --connection-mode direct \
  --apply --confirm 'CONFIGURE CONNECTION MODE'
```

При каскаде на RU должны быть свободны и разрешены firewall публичные порты,
которые использует KIT на PL. Сценарий не меняет режим автоматически: без
`--apply` он только показывает план.

Сценарий намеренно не принимает API-токены и пароли через аргументы. Он сохраняет существующие секреты `/etc/vpn-shop.env`, существующий PL node token и настройки Emergency MTProto.

## Итоговые сервисы

- RU 3x-ui: `https://192.168.10.231:4463/<private-path>/`;
- PL 3x-ui: `https://192.168.10.214:7463/<private-path>/`;
- сайт: `https://192.168.10.231:8443`;
- панель Telegram-бота: `http://192.168.10.214:8090`.

Private path, логины, пароли, API-токены и ссылки подписок не должны храниться в Git.

## Проверка

```bash
ssh proxmox 'pct exec 112 -- systemctl is-active x-ui vpn-shop home-vpn-kit-backup.timer'
ssh proxmox 'pct exec 113 -- systemctl is-active x-ui nginx kit-sub vpn-bot vpn-bot-admin home-vpn-kit-backup.timer'
ssh proxmox 'pct exec 112 -- curl -ksSf https://127.0.0.1:8443/health'
```

Проверка и восстановление KIT описаны в [3x-ui-kit-v1.2.md](3x-ui-kit-v1.2.md).
