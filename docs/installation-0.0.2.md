# HOME-VPN 0.0.2 — полная установка и настройка

Инструкция использует только примеры. Заменяйте их своими значениями:

- `192.168.1.50` — IP основного RU-сервера;
- `192.168.1.51` — IP сервера Telegram-бота или PL-ноды;
- `192.168.1.53` — IP backup-сервера;
- `my.domain.ru` — домен сайта;
- `backup.my.domain.ru` — домен backup-панели.

Не копируйте в Git реальные токены, пароли, закрытые ключи, банковские ссылки и
файлы `/etc/*.env`.

## 1. Что входит в версию 0.0.2

Один репозиторий устанавливает независимые компоненты:

1. сайт, API и кабинет администратора;
2. Telegram-бот и отдельную панель управления ботом;
3. домен и TLS для сайта;
4. новый VLESS XHTTP/TLS или VLESS TCP/REALITY inbound в установленной 3x-ui;
5. сайт-заглушку для ноды;
6. SMTP/IMAP-сервер;
7. отдельный backup-сервер с HTTPS GUI;
8. backup-агент рабочего сервера.

Установщик не удаляет 3x-ui и не изменяет существующий inbound без отдельного
действия. Банковская ссылка в публичный проект не включена.

## 2. Рекомендуемая схема

Для небольшого сервиса до 500 пользователей:

| Сервер | Компоненты |
|---|---|
| RU | 3x-ui, сайт/API, основная нода, backup-агент |
| PL | 3x-ui, Telegram-бот, дополнительная нода, backup-агент |
| Дополнительные FI/SE | 3x-ui, заглушка, backup-агент |
| Backup | backup-хранилище и его HTTPS-панель |

Backup-сервер не подключается к рабочим серверам. Каждый агент сам отправляет
архив по отдельному SSH-ключу. Этот push-only подход не даёт хранилищу root-доступ
к RU, PL и другим узлам.

Минимум для сайта с 3x-ui: 2 ГБ RAM. Для отдельного backup-LXC достаточно
1 CPU, 512 МБ RAM и диска от 8 ГБ, но размер диска должен учитывать срок
хранения и число серверов.

## 3. Подготовка каждого сервера

Поддерживаемая основа — чистая Debian 12 или совместимая Debian/Ubuntu-система.

```bash
sudo apt-get update
sudo apt-get install -y git ca-certificates python3
git clone https://github.com/YOUR_ACCOUNT/home-vpn.git
cd home-vpn
cat VERSION
```

Ожидаемый результат: `0.0.2`.

Перед публикацией наружу:

- настройте вход администратора по SSH-ключу;
- ограничьте firewall;
- создайте A/AAAA-записи доменов;
- используйте доверенные TLS-сертификаты;
- синхронизируйте время через systemd-timesyncd или chrony.

## 4. Общий мастер

Запуск:

```bash
sudo bash install.sh
```

Главное меню:

- **Проверить установленные компоненты** — ничего не меняет;
- **Установить компоненты** — первичная установка;
- **Обновить компоненты** — заменяет программу, сохраняя базы и настройки;
- **Изменить настройки** — повторный мастер выбранного компонента;
- **Полное удаление компонентов** — требует точную фразу подтверждения.

Компоненты можно вызвать без меню:

```bash
sudo home-vpn-manager status
sudo home-vpn-manager install site
sudo home-vpn-manager install bot
sudo home-vpn-manager install backup
sudo home-vpn-manager install backup-agent
sudo home-vpn-manager update backup backup-agent
```

Менеджер запоминает каталог клонированного репозитория в
`/etc/home-vpn/source-root`. Не удаляйте этот каталог: он нужен для последующих
обновлений. Если проект перенесён, снова запустите `sudo bash install.sh` из
нового каталога — путь обновится автоматически.

При удалении без `--purge-data` пользовательские данные сохраняются. Перед
любым удалением мастер всё равно запрашивает `DELETE HOME-VPN`.

## 5. Основной RU-сервер: 3x-ui и сайт

### 5.1 Установите и проверьте 3x-ui

Сначала установите 3x-ui выбранным вами официальным способом. Проект ожидает,
что служба и база уже работают:

```bash
sudo systemctl status x-ui --no-pager
sudo test -f /etc/x-ui/x-ui.db
```

Создайте API-токен панели. Не публикуйте его и не записывайте в пример
конфигурации репозитория.

### 5.2 Установите сайт

```bash
cd /path/to/home-vpn
sudo bash install.sh
```

Выберите **Установить компоненты → Сайт и API**. Мастер запросит публичный URL,
администратора, тестовых пользователей, параметры основной и дополнительных
нод, Telegram и почту. Настройки сохраняются локально:

- `/etc/home-vpn/config.json` — общая конфигурация;
- `/etc/vpn-shop.env` — пароли и токены, права `600`;
- `/var/lib/vpn-shop/shop.db` — база сайта.

Если HTTPS-порт свободен, сайт может слушать его напрямую. Если порт занят
существующим reverse proxy, сайт запускается на `127.0.0.1:8080`. Проект не
устанавливает Nginx автоматически.

Проверка:

```bash
sudo home-vpn-setup doctor
sudo systemctl status vpn-shop x-ui --no-pager
curl -fsS http://127.0.0.1:8080/health || curl -kfsS https://127.0.0.1/health
```

### 5.3 Домен и TLS

```bash
sudo home-vpn-manager install domain
```

Мастер ищет сертификат, уже используемый 3x-ui, либо просит указать пути.
Пример:

```text
/etc/letsencrypt/live/my.domain.ru/fullchain.pem
/etc/letsencrypt/live/my.domain.ru/privkey.pem
```

После обновления сертификата перезапустите только затронутые службы:

```bash
sudo systemctl restart vpn-shop x-ui
```

## 6. VLESS и сайт-заглушка

На сервере с 3x-ui:

```bash
sudo home-vpn-manager install placeholder
sudo home-vpn-manager install xui
```

Мастер заглушки запросит название, ссылку основного сайта и локальный порт.
Мастер 3x-ui предложит VLESS XHTTP/TLS или VLESS TCP/REALITY, покажет итоговую
конфигурацию и только после подтверждения создаст новый inbound. Уже существующие
inbound не изменяются.

Перед подтверждением проверьте:

- свободен ли внешний порт;
- совпадает ли домен сертификата;
- указан ли правильный fallback на локальную заглушку;
- не конфликтует ли маршрут с существующей конфигурацией.

## 7. Telegram-бот на отдельном сервере

На RU-сервере прочитайте `API_SECRET`, не выводя весь env-файл в публичный чат:

```bash
sudo grep '^API_SECRET=' /etc/vpn-shop.env
```

На сервере бота:

```bash
cd /path/to/home-vpn
sudo bash install.sh
```

Выберите **Telegram-бот и панель**. Понадобятся:

- публичный URL сайта;
- URL API сайта;
- `API_SECRET` RU-сервера;
- Telegram Bot Token;
- цифровой Telegram ID администратора;
- канал технических уведомлений;
- закрытый URL администратора сайта;
- адрес и порт отдельной панели бота.

Проверка:

```bash
sudo systemctl status vpn-bot vpn-bot-admin --no-pager
sudo journalctl -u vpn-bot -n 50 --no-pager
```

Файлы `/etc/vpn-bot.env` и `/etc/vpn-bot-admin.env` имеют права `600`.
Публично открывайте панель бота только через доверенный TLS и ограничение
административных адресов.

## 8. Установка backup-сервера

На отдельной машине со стабильным IP:

```bash
cd /path/to/home-vpn
sudo bash install.sh
```

Выберите **Backup-сервер и GUI**. Мастер запросит:

- IP, который добавить в тестовый сертификат;
- будущий публичный домен или IP;
- адрес прослушивания, обычно `0.0.0.0`;
- HTTPS-порт, по умолчанию `9443`.

В версии 0.0.2 установщик **не просит ключ рабочего сервера**. Источники
подключаются позже кнопкой **Добавить сервер**.

Учётные данные создаются один раз и сохраняются только в:

```text
/root/home-vpn-backup-panel-credentials.txt
```

Проверка:

```bash
sudo systemctl status home-vpn-backup-panel ssh --no-pager
sudo ss -lntp | grep -E ':(22|9443)\b'
sudo cat /root/home-vpn-backup-panel-credentials.txt
```

Самоподписанный сертификат предназначен для тестов. Для production замените
пути в `/etc/home-vpn-backup-panel.env`:

```text
BACKUP_PANEL_TLS_CERT=/etc/letsencrypt/live/backup.my.domain.ru/fullchain.pem
BACKUP_PANEL_TLS_KEY=/etc/letsencrypt/live/backup.my.domain.ru/privkey.pem
```

Затем:

```bash
sudo systemctl restart home-vpn-backup-panel
```

Firewall:

- `22/tcp` разрешить только с IP рабочих серверов;
- `9443/tcp` разрешить только с административного IP или через VPN;
- не публиковать закрытый путь и пароль панели.

## 9. Подключение первого и последующих серверов к backup

Одинаковый порядок используется для RU, PL, бота, FI, SE и будущих серверов.

### Шаг 1. Установите агент на рабочем сервере

```bash
cd /path/to/home-vpn
sudo bash install.sh
```

Выберите **Backup-агент рабочего сервера** и укажите:

- уникальный ID: `prod-ru`, `prod-pl`, `bot-pl`, `node-fi`;
- понятное название;
- роль `site`, `bot` или `node`;
- IP/домен backup-сервера;
- SSH-порт backup-сервера.

Мастер покажет SSH fingerprint. Сравните его с backup-сервером:

```bash
sudo ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
```

Только после совпадения введите `YES`. В конце мастер покажет публичный ключ
backup-агента.

### Шаг 2. Добавьте сервер через GUI

1. Откройте закрытый URL backup-панели.
2. Нажмите **＋ Добавить сервер**.
3. Введите тот же ID, название и роль.
4. Вставьте публичный ключ агента.
5. Укажите срок хранения.
6. Нажмите **Добавить сервер**.

Закрытый ключ остаётся только на рабочем сервере в
`/root/.ssh/home-vpn-full-backup`.

### Шаг 3. Отправьте первую копию

На рабочем сервере:

```bash
sudo systemctl start home-vpn-full-backup.service
sudo systemctl status home-vpn-full-backup.service --no-pager
sudo journalctl -u home-vpn-full-backup.service -n 80 --no-pager
sudo systemctl status home-vpn-full-backup.timer --no-pager
```

Обновите страницу backup-панели. Должен появиться архив формата `v2`. Нажмите
**Проверить** — панель сверит структуру и SHA-256 каждого файла.

### Что делает GUI

- **Добавить сервер** — регистрирует отдельный публичный ключ и каталог;
- **Отключить приём** — отзывает ключ, но сохраняет профиль и архивы;
- **Включить приём** — возвращает ранее сохранённый публичный ключ;
- **Удалить сервер** — открывает отдельное подтверждение с вводом ID;
- удаление без флажка переносит архивы в
  `/srv/home-vpn-backups-removed`;
- удаление с флажком безвозвратно удаляет профиль, ключ и архивы.

Для обычного обслуживания используйте **Отключить приём**, а не удаление.

## 10. Добавление уже настроенного 3x-ui-сервера

Установка backup-агента не меняет inbound, клиентов и параметры 3x-ui. Роль
определяет только состав архива:

- `site` — сайт, базы, конфигурация и основная 3x-ui;
- `bot` — бот, его панель и 3x-ui этого сервера;
- `node` — конфигурация и база 3x-ui-ноды.

Перед первой копией проверьте профиль:

```bash
sudo python3 -m json.tool /etc/home-vpn-full-backup.json
```

Не добавляйте вручную каталоги с многогигабайтными установщиками и временными
файлами. Архив — переносимая копия состояния приложения, а не образ всего диска.

## 11. Обновление с 0.0.1-pre до 0.0.2

Сначала создайте контрольную копию. Затем обновите код проекта и выполните:

На backup-сервере:

```bash
cd /path/to/home-vpn
git pull --ff-only
sudo bash install.sh
```

Выберите **Обновить компоненты → Backup-сервер и GUI**. Профили, архивы,
закрытый URL и пароль сохраняются. При первом открытии панель переносит публичные
ключи старых профилей в новый формат реестра.

На каждом рабочем сервере:

```bash
cd /path/to/home-vpn
git pull --ff-only
sudo home-vpn-manager update backup-agent
```

Профиль, host key, закрытый ключ и локальные архивы сохраняются.

После обновления:

```bash
sudo home-vpn-manager status
sudo systemctl start home-vpn-full-backup.service
```

В GUI проверьте, что у серверов показывается **приём включён**, затем проверьте
новый архив.

## 12. Полное восстановление при смене IP

Архив `v2` восстанавливает приложение, базы, конфигурацию, сертификаты и units,
но не является побитовым образом ОС. Для полного образа LXC дополнительно
используйте Proxmox `vzdump`.

1. Подготовьте чистую совместимую ОС на новом сервере.
2. Установите зависимости проекта и 3x-ui.
3. Скачайте архив из GUI.
4. Скопируйте `backup-node/home-vpn-full-restore.py` как
   `/usr/local/sbin/home-vpn-full-restore`.
5. Сначала только проверьте:

   ```bash
   sudo home-vpn-full-restore /root/prod-ru-YYYYMMDD-HHMMSS.tar.gz
   ```

6. Затем восстановите с заменой старого адреса:

   ```bash
   sudo home-vpn-full-restore /root/prod-ru-YYYYMMDD-HHMMSS.tar.gz \
     --apply --replace-host=OLD_IP=NEW_IP
   ```

7. Введите `RESTORE`.
8. Обновите DNS, TLS, firewall, адреса нод и URL API/webhook.
9. Проверьте сайт, панель, тестовую подписку, HAPP и Telegram-бот.
10. Старый сервер выключайте только после полной проверки.

## 13. Почта

Выберите **SMTP/IMAP** только на выделенном почтовом сервере. Потребуются домен,
hostname, имя ящика, адрес отправителя и IP. Для реальной доставки также нужны:

- PTR/rDNS от провайдера;
- DNS-записи A, MX, SPF, DKIM и DMARC;
- открытые 25, 465/587 и 993 порты;
- доверенный сертификат почтового домена.

Пароль сохраняется в `/root/home-vpn-mail-credentials.txt`. Подробности:
[email-setup.md](email-setup.md).

## 14. Проверка всей системы перед запуском

```bash
python3 -m unittest discover -s vpn-shop -p 'test_*.py'
python3 -m unittest discover -s vpn-bot -p 'test_*.py'
python3 -m unittest discover -s backup-node -p 'test_*.py'
python3 -m unittest discover -s scripts -p 'test_*.py'
python3 scripts/home-vpn-setup.py validate --config config/config.example.json
```

Ручной сценарий:

1. создать тестового пользователя;
2. войти через сайт и Telegram;
3. создать заказ и проверить таймер;
4. подтвердить и отклонить тестовые платежи;
5. получить одну ссылку подписки и QR;
6. подключить HAPP;
7. проверить лимит устройств;
8. создать и ответить на тикет;
9. опубликовать тестовое техническое уведомление;
10. создать, проверить и скачать backup;
11. выполнить check-only восстановление на отдельной тестовой машине.

## 15. Диагностика

Состояние компонентов:

```bash
sudo home-vpn-manager status
```

Журналы:

```bash
sudo journalctl -u vpn-shop -n 100 --no-pager
sudo journalctl -u vpn-bot -n 100 --no-pager
sudo journalctl -u home-vpn-backup-panel -n 100 --no-pager
sudo journalctl -u home-vpn-full-backup.service -n 100 --no-pager
```

Если backup не отправляется:

1. убедитесь, что сервер включён в GUI;
2. проверьте совпадение ID профиля и ID в панели;
3. проверьте доступность SSH-порта;
4. не принимайте новый host key вслепую после неожиданной смены;
5. проверьте свободное место на backup-сервере;
6. запустите service вручную и прочитайте журнал.

Если сервер удалён без уничтожения архивов, root может найти сохранённый каталог:

```bash
sudo find /srv/home-vpn-backups-removed -maxdepth 1 -mindepth 1 -type d -print
```

Возврат такого каталога выполняйте вручную только после повторной регистрации
сервера и проверки владельца/прав. GUI намеренно не восстанавливает удалённый
профиль одной кнопкой.

## 16. Важные файлы

| Назначение | Путь |
|---|---|
| Конфигурация сайта | `/etc/home-vpn/config.json` |
| Секреты сайта | `/etc/vpn-shop.env` |
| База сайта | `/var/lib/vpn-shop/shop.db` |
| Секреты бота | `/etc/vpn-bot.env` |
| Меню бота | `/etc/vpn-bot-menu.json` |
| Реестр backup-серверов | `/etc/home-vpn-backup-panel/servers.json` |
| Секреты backup-панели | `/etc/home-vpn-backup-panel.env` |
| Данные входа backup-панели | `/root/home-vpn-backup-panel-credentials.txt` |
| Активные архивы | `/srv/home-vpn-backups/<server-id>` |
| Сохранённые после удаления | `/srv/home-vpn-backups-removed` |
| Профиль агента | `/etc/home-vpn-full-backup.json` |
| Закрытый ключ агента | `/root/.ssh/home-vpn-full-backup` |
| Проверенный host key | `/etc/home-vpn-full-backup-known-hosts` |

Файлы с секретами должны иметь права `600` или более строгие и никогда не
должны попадать в публичный репозиторий.
