# HOME-VPN 0.0.5-pre — установка, миграция и откат

Версия `0.0.5-pre` сохраняет базы, тарифы, пользователей, Telegram-привязки,
заказы и закрепление подписок за нодами из `0.0.4-pre`. GitHub и production VPS
не изменяются самим обновлением локального репозитория.

## Три режима

| Mode | Управляющая нода | VPN-ноды | Пользовательский трафик | Заглушка |
|---|---|---|---|---|
| 1 | центральная VPS: сайт и панели; бот, bot-admin и egress — на выбранной VPS | иностранные VPS: 3x-ui и 11 подключений | пользователь → выбранная иностранная VPS → интернет | только иностранные ноды |
| 2 | центральная VPS плюс nftables cascade; бот — на выбранной VPS | иностранные VPS: 3x-ui и 11 подключений | пользователь → центральная VPS → выбранная иностранная VPS → интернет | только иностранные ноды |
| 3 | единственная иностранная VPS выполняет все управляющие роли | эта же VPS: 3x-ui и 11 подключений | пользователь → единственная VPS → интернет | запрещена |

В каждом режиме Telegram API доступен боту только через loopback-прокси
`127.0.0.1:18080`, который балансирует иностранные VLESS/XHTTP-выходы и
использует blackhole при полном отказе. `SHOP_API_URL` бота указывает на
loopback HOME-VPN API.

## Роли, bind-адреса и firewall

| Компонент | Bind/порт | Где | Правило firewall |
|---|---|---|---|
| Публичный HOME-VPN TLS | public IP:443 | управляющая нода | разрешить TCP 443 из интернета |
| HOME-VPN application | 127.0.0.1:8080 | управляющая нода | не публиковать |
| bot-admin | 127.0.0.1:8090 | управляющая нода | не публиковать; доступ только через закрытый TLS path сайта |
| Telegram egress | 127.0.0.1:18080 | управляющая нода | не публиковать; запретить прямой Telegram egress управляющей ноды политикой провайдера при необходимости |
| 3x-ui API | адрес защищённой сети, порт из KIT | управляющая → иностранные ноды | разрешить только адрес управляющей ноды; запретить public Internet |
| 11 VPN inbound | порты из фактического KIT inventory | иностранные ноды; в mode 2 также central ingress | разрешить только заявленные TCP/UDP порты |
| SSH | TCP 22 или локально выбранный | все VPS | allowlist административных адресов |

В mode 2 проект владеет только таблицей `ip home_vpn_cascade`. Он не выполняет
`flush ruleset` и не изменяет чужие nftables-таблицы. До применения выполняется
`nft -c`; прежнее значение `net.ipv4.ip_forward` сохраняется и восстанавливается
при переходах 2→1 и 2→3.

## Чистая установка

Интерактивная установка всех трёх режимов непосредственно с управляющей VPS:

```bash
curl -fsSLo /tmp/home-vpn-install.sh https://raw.githubusercontent.com/valuev3000/home-vpn/main/install-single-node.sh && sudo bash /tmp/home-vpn-install.sh
```

В modes 1/2 текущая VPS записывается в план как `local`, а SSH запрашивается
только для иностранных нод. В mode 3 сайт и VPN-нода используют одну локальную
цель. Неверный номер режима запрашивается повторно.

Создайте локальный план без секретов:

```bash
cp config/topology.example.json topology.local.json
python3 scripts/install-home-vpn-topology.py --config topology.local.json
```

Замените URL и SHA-256 внешнего установщика, адреса и домены. Затем примените:

```bash
python3 scripts/install-home-vpn-topology.py \
  --config topology.local.json \
  --apply --confirm 'INSTALL HOME VPN TOPOLOGY'
```

Обычный `install.sh` не устанавливает 3x-ui. Topology installer устанавливает
зафиксированный набор 3x-ui на чистой ноде, но останавливается, если обнаружена
иная или частичная конфигурация.

## Миграция с 0.0.4-pre

1. Сохраните копии отдельно для каждого контура и проверьте распаковку:

   ```bash
   sudo systemctl start home-vpn-kit-backup.service
   sudo /usr/local/sbin/home-vpn-state-backup create site
   sudo /usr/local/sbin/home-vpn-state-backup create bot
   restore_dir=$(mktemp -d /var/tmp/home-vpn-restore.XXXXXX)
   sudo /usr/local/sbin/home-vpn-state-backup verify site /var/backups/home-vpn/home-vpn-site-YYYYMMDD-HHMMSS.tar.gz --restore-dir "$restore_dir"
   ```

2. Сохраните локальный topology plan и снимки `/etc/vpn-shop.env`,
   `/etc/vpn-bot.env`, `/etc/vpn-bot-admin.env` вне Git. Проверьте права `0600`.
3. Запустите локальные тесты из раздела «Проверка» ниже.
4. Выполните dry-run topology installer. Для mode 3 убедитесь, что `site.ssh_host`
   совпадает с единственной VPN-нодой.
5. Примените тот же план. Повторный запуск обновляет файлы, но не пересоздаёт
   базы и не меняет существующие непустые секреты без явной настройки.
6. Запустите read-only smoke-test соответствующего режима.

Старые URL кабинета и admin path сохраняются. `BOT_ADMIN_URL` автоматически
становится закрытым path на `site.public_url`; прямой `0.0.0.0:8090` больше не
поддерживается.

## Проверка и повторное применение

```bash
python3 -m compileall -q -f .
python3 -m unittest discover -s scripts -p 'test_*.py' -v
python3 -m unittest discover -s migration -p 'test_*.py' -v
python3 -m unittest discover -s vpn-shop -p 'test_*.py' -v
python3 -m unittest discover -s vpn-bot -p 'test_*.py' -v
python3 scripts/smoke-home-vpn-topology.py --config topology.local.json
git diff --check
```

Для теста идемпотентности примените один и тот же план второй раз, затем снова
запустите smoke-test. Для переходов 1→2, 2→1 и 2→3 изменяйте только локальный
план, сначала выполняйте dry-run, после применения проверяйте отсутствие или
наличие `home_vpn_cascade` согласно выбранному mode.

## Обновление отдельных компонентов

```bash
sudo home-vpn-manager update site bot
sudo home-vpn-setup validate --config /etc/home-vpn/config.json
sudo home-vpn-setup doctor --config /etc/home-vpn/config.json
```

Удаление использует `config/component-manifests.json`. Например, purge бота не
удаляет сайт, а purge сайта не удаляет архивы и timer 3x-ui:

```bash
sudo home-vpn-manager remove bot --purge-data
```

## Откат

1. Не откатывайте базу поверх работающего сервиса. Остановите только затронутый
   компонент и сохраните текущий каталог состояния.
2. Разверните прежний локальный исходный каталог `0.0.4-pre` и верните файлы
   приложения командой его manager update.
3. Если схема/состояние повреждены, распакуйте выбранный архив во временный
   каталог, проверьте SQLite и только затем скопируйте его в окончательный путь.
4. Для mode 2→1/3 примените новый план: installer удалит только
   `home_vpn_cascade` и восстановит `ip_forward`.
5. Запустите сервисы и smoke-test. Не восстанавливайте секреты из открытого
   архива: secret backup допустим только в `.age` с ключом, хранящимся off-site.

Автоматического распределённого rollback между несколькими VPS нет. Перед
production-изменением требуется окно работ и проверенная off-site копия.
