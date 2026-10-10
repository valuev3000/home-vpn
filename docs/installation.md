# Установка HOME-VPN

Ниже описана рекомендуемая чистая установка на **одну VPS** (mode 3): на одном
сервере работают сайт, административные панели, Telegram-бот, 3x-ui и все VPN-
подключения. Для центральной VPS и нескольких зарубежных нод используйте
[инструкцию по топологиям](topology-installation.md).

> Все домены, IP, логины и пути в примерах условные. Не добавляйте реальные
> пароли, Telegram-токены, базы, TLS-ключи и локальный `topology.local.json` в Git.

## Быстрая интерактивная установка на VPS

Сначала направьте два DNS-имени на публичный IPv4 новой VPS. Затем войдите на
неё как `root`, скачайте скрипт и просмотрите его перед запуском:

```bash
wget -qO home-vpn-install.sh \
  https://raw.githubusercontent.com/valuev3000/home-vpn/main/install-single-node.sh
less home-vpn-install.sh
bash home-vpn-install.sh
```

Как и установщик `3x-ui-pro`, мастер выполняется непосредственно на VPS. Он
интерактивно запросит:

- публичный IPv4 и SSH-порт;
- отдельные домены сайта и панели 3x-ui;
- email Let's Encrypt;
- страну, имя ноды и SNI;
- нужна ли установка Telegram-бота;
- URL и SHA-256 зафиксированного установщика KIT;
- логины, пароли, Telegram-токен и Telegram ID на этапе установки компонентов.

Перед изменением системы мастер проверяет обе DNS A-записи, сохраняет план без
секретов в `/root/home-vpn-topology.json`, показывает сводку и требует точное
подтверждение `INSTALL HOME VPN`. Затем он устанавливает пакеты, swap при
необходимости, UFW, fail2ban, TLS, 3x-ui/KIT, HOME-VPN и выполняет smoke-test.
В конце мастер показывает все пользовательские URL, логины и пароли панелей;
внутренний API-токен 3x-ui в итоговый экран не выводится.

Только создать и проверить план, ничего не меняя на VPS:

```bash
bash home-vpn-install.sh --plan-only
```

Остальные разделы ниже подробно объясняют каждый автоматизированный шаг и
нужны для ручной установки или диагностики.

## 1. Что потребуется

- чистая VPS с Ubuntu 22.04/24.04, публичным IPv4 и root-доступом;
- минимум 1 ГБ RAM (для VPS с 1 ГБ рекомендуется swap);
- два DNS-имени: `vpn.example.com` для сайта и `panel.example.com` для 3x-ui;
- Linux/macOS-компьютер администратора с `git`, Python 3.10+ и OpenSSH;
- Telegram-бот, созданный через `@BotFather`, и цифровой Telegram ID
  администратора;
- открытые у провайдера входящие TCP 22, 80, 443 и порты VPN-протоколов.

Обе DNS A-записи должны указывать на адрес VPS **до** получения сертификата:

```text
vpn.example.com    A    203.0.113.10
panel.example.com  A    203.0.113.10
```

Проверьте их с рабочей станции:

```bash
getent ahostsv4 vpn.example.com
getent ahostsv4 panel.example.com
```

## 2. Подготовка VPS

Первый вход может выполняться по временному паролю провайдера. Сразу установите
SSH-ключ; topology installer многократно подключается к серверу и рассчитан на
доступ без запроса пароля:

```bash
ssh-copy-id root@203.0.113.10
ssh root@203.0.113.10 'id -u'
```

Последняя команда должна вывести `0`. После проверки ключа смените выданный
провайдером пароль root. Отключайте парольную SSH-аутентификацию только после
того, как убедились, что вход по ключу работает в новом терминале.

Обновите систему и установите базовые пакеты:

```bash
ssh root@203.0.113.10
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get upgrade -y
apt-get install -y ca-certificates certbot curl fail2ban ufw unattended-upgrades
systemctl enable --now fail2ban unattended-upgrades
```

Для сервера с 1 ГБ RAM можно создать swap:

```bash
fallocate -l 1G /swapfile
chmod 600 /swapfile
mkswap /swapfile
swapon /swapfile
grep -q '^/swapfile ' /etc/fstab || printf '%s\n' '/swapfile none swap sw 0 0' >> /etc/fstab
```

## 3. Firewall и TLS

Сначала разрешите SSH и выпуск сертификата, не закрывая текущую SSH-сессию:

```bash
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 443/udp
ufw --force enable
ufw status verbose
```

Получите один SAN-сертификат для обоих доменов. Порт 80 должен быть свободен и
доступен из интернета:

```bash
certbot certonly --standalone \
  --agree-tos --no-eff-email \
  --email admin@example.com \
  -d vpn.example.com \
  -d panel.example.com
```

Пути сертификата будут такими:

```text
/etc/letsencrypt/live/vpn.example.com/fullchain.pem
/etc/letsencrypt/live/vpn.example.com/privkey.pem
```

Проверьте автоматическое продление:

```bash
certbot renew --dry-run --no-random-sleep-on-renew
```

После установки добавьте hook, чтобы сервисы перечитали продлённый сертификат:

```bash
install -d -m 755 /etc/letsencrypt/renewal-hooks/deploy
printf '%s\n' '#!/bin/sh' 'systemctl reload nginx' 'systemctl restart x-ui' \
  > /etc/letsencrypt/renewal-hooks/deploy/home-vpn-services
chmod 755 /etc/letsencrypt/renewal-hooks/deploy/home-vpn-services
```

Завершите SSH-сессию командой `exit`.

## 4. Получение HOME-VPN

На рабочей станции:

```bash
git clone https://github.com/valuev3000/home-vpn.git
cd home-vpn
```

Для удобства можно создать SSH-алиас:

```sshconfig
Host home-vpn-node
    HostName 203.0.113.10
    User root
    IdentityFile ~/.ssh/id_ed25519
    IdentitiesOnly yes
```

Проверьте его:

```bash
ssh home-vpn-node 'id -u'
```

## 5. Создание плана mode 3

Создайте `topology.local.json` в корне репозитория. Этот файл игнорируется Git,
но всё равно не должен содержать секреты:

```json
{
  "mode": 3,
  "protocol_installer": {
    "url": "https://raw.githubusercontent.com/itsnotkubrick/3X-UI_KIT/v1.2/scripts/3x-ui.sh",
    "sha256": "2d7a0f0dcc26ea4f8bab3d60e1900e7046a9673a88fef85c03630502120961f0"
  },
  "telegram_bot": {
    "enabled": true
  },
  "telegram_egress": {
    "listen": "127.0.0.1",
    "port": 18080,
    "strategy": "leastPing",
    "probe_interval": "30s"
  },
  "site": {
    "ssh_host": "home-vpn-node",
    "address": "203.0.113.10",
    "public_url": "https://vpn.example.com",
    "tls_cert": "/etc/letsencrypt/live/vpn.example.com/fullchain.pem",
    "tls_key": "/etc/letsencrypt/live/vpn.example.com/privkey.pem"
  },
  "nodes": [
    {
      "id": "foreign-1",
      "name": "Main VPN node",
      "country": "DE",
      "flag": "🌐",
      "ssh_host": "home-vpn-node",
      "address": "203.0.113.10",
      "api_host": "panel.example.com",
      "public_host": "panel.example.com",
      "ingress_address": "",
      "sni": "dl.google.com",
      "cert": "/etc/letsencrypt/live/vpn.example.com/fullchain.pem",
      "key": "/etc/letsencrypt/live/vpn.example.com/privkey.pem"
    }
  ],
  "emergency_node": "foreign-1"
}
```

URL установщика зафиксирован тегом `v1.2`, а SHA-256 относится именно к этому
файлу. При выборе другой версии сначала скачайте её на рабочую станцию, изучите
и вычислите новую сумму командой `sha256sum`; не используйте `curl | bash`.

Проверьте план без изменений на VPS:

```bash
python3 scripts/install-home-vpn-topology.py --config topology.local.json
```

Команда должна показать выбранные роли и завершиться сообщением `Dry-run`.

## 6. Установка

Запустите применение плана с рабочей станции:

```bash
python3 scripts/install-home-vpn-topology.py \
  --config topology.local.json \
  --apply \
  --confirm 'INSTALL HOME VPN TOPOLOGY'
```

Мастер запросит:

1. логин и пароль администратора сайта (не менее 10 символов);
2. Telegram Bot Token и цифровой ID администратора;
3. необязательный Telegram-канал;
4. логин и пароль отдельной панели бота (не менее 10 символов).

Секреты вводятся скрыто, не записываются в JSON-план и не передаются в
аргументах процессов. На VPS они сохраняются в root-only env-файлах. Запишите
введённые пароли в менеджер паролей. Установщик 3x-ui создаёт собственный логин,
пароль и скрытый URL панели — также сохраните их из вывода установки.

Topology installer передаёт только отслеживаемые Git файлы, устанавливает 11
подключений 3x-ui, сайт, обе HOME-VPN панели, Telegram-бот, loopback egress и
резервное копирование. Существующая частичная или отличающаяся конфигурация
3x-ui автоматически не удаляется.

## 7. VPN-порты

Topology installer вызывает KIT с `--no-ufw`, поэтому firewall нужно дополнить
вручную. Для зафиксированного выше KIT v1.2 используются следующие порты:

```bash
ssh home-vpn-node
ufw allow 8388/tcp
ufw allow 8388/udp
ufw allow 8444/udp
ufw allow 51821/udp
ufw allow 51822/udp
ufw status numbered
```

TCP/UDP 443 уже были разрешены для сайта и VPN. Если используется другая версия
KIT, сначала сверьте фактические порты её inbounds и открывайте только их.

## 8. Проверка после установки

На VPS:

```bash
systemctl is-active x-ui nginx kit-sub vpn-shop vpn-bot vpn-bot-admin \
  home-vpn-bot-egress fail2ban unattended-upgrades
systemctl is-enabled home-vpn-kit-backup.timer
curl -fsS https://vpn.example.com/health
ss -lnt | grep -E '127\.0\.0\.1:(8080|8090|18080)'
```

Порты `8080`, `8090` и `18080` должны слушать только loopback. Затем с рабочей
станции запустите полный read-only smoke-test:

```bash
python3 scripts/smoke-home-vpn-topology.py --config topology.local.json
```

Проверьте сертификат и HTTP-заголовки:

```bash
curl -fsSI https://vpn.example.com/
curl -fsSI https://panel.example.com/
openssl s_client -connect vpn.example.com:443 -servername vpn.example.com </dev/null 2>/dev/null \
  | openssl x509 -noout -subject -issuer -dates -ext subjectAltName
```

Откройте нового Telegram-бота, нажмите **Start** или отправьте `/start`, затем:

```bash
ssh home-vpn-node 'systemctl restart vpn-bot && journalctl -u vpn-bot -n 50 --no-pager'
```

Без первого `/start` Telegram отвечает `chat not found`, и бот не сможет
отправить администратору стартовое уведомление.

Создайте отдельного тестового клиента, проверьте подписку и подключение из
внешней сети, затем удалите тестового клиента.

## 9. Где находятся URL и секреты

- основной URL — значение `site.public_url` из локального плана;
- скрытый путь HOME-VPN admin — `ADMIN_ENTRY_PATH` в `/etc/vpn-shop.env`;
- скрытый путь bot-admin — `BOT_PANEL_ENTRY_PATH` в `/etc/vpn-bot-admin.env`;
- секреты сайта — `/etc/vpn-shop.env`;
- секреты бота — `/etc/vpn-bot.env` и `/etc/vpn-bot-admin.env`;
- конфигурация без секретов — `/etc/home-vpn/config.json`;
- база сайта — `/var/lib/vpn-shop/shop.db`;
- база 3x-ui — `/etc/x-ui/x-ui.db`.

Показывайте env-файлы только в доверенном локальном терминале:

```bash
sudo less /etc/vpn-shop.env
sudo less /etc/vpn-bot.env
sudo less /etc/vpn-bot-admin.env
```

Не вставляйте их содержимое в issue, чат или CI-лог. Если Telegram-токен где-то
был опубликован, немедленно перевыпустите его через `@BotFather`, обновите
настройку и перезапустите `vpn-bot`.

## 10. Резервное копирование и обновление

Сразу создайте и проверьте резервные копии:

```bash
ssh home-vpn-node
systemctl start home-vpn-kit-backup.service
/usr/local/sbin/home-vpn-state-backup create site
/usr/local/sbin/home-vpn-state-backup create bot
journalctl -u home-vpn-kit-backup.service -n 100 --no-pager
```

Локальная копия на той же VPS не защищает от потери сервера. Настройте off-site
копирование по [инструкции](backup-and-restore.md).

Обновление выполняется с рабочей станции; VPS сама не запускает `git pull`:

```bash
git pull --ff-only
python3 scripts/install-home-vpn-topology.py --config topology.local.json
python3 scripts/install-home-vpn-topology.py \
  --config topology.local.json \
  --apply --confirm 'INSTALL HOME VPN TOPOLOGY'
python3 scripts/smoke-home-vpn-topology.py --config topology.local.json
```

Перед повторным применением сделайте backup. Мастер снова запросит секреты;
введите действующие значения либо осознанно задайте новые.

## 11. Диагностика

```bash
ssh home-vpn-node
systemctl --failed
journalctl -u vpn-shop -u vpn-bot -u vpn-bot-admin -u home-vpn-bot-egress \
  -u x-ui -u nginx -n 200 --no-pager
nginx -t
certbot certificates
fail2ban-client status sshd
```

Полный путь Xray зависит от установленной версии 3x-ui; смотрите его через
`systemctl cat x-ui`. Дополнительные режимы, ограничения и безопасный переход
между архитектурами описаны в
[topology-installation.md](topology-installation.md).
