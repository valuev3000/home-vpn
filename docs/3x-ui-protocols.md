# Набор протоколов 3x-ui в HOME-VPN

Каждая зарубежная VPN-нода содержит 11 подключений: REALITY, Hysteria2,
XHTTP, VLESS-WS, Trojan-gRPC, VMess-WS, Shadowsocks, TUIC, AmneziaWG,
AmneziaWG 3.1 и MTProto. Сайт получает фактические inbound ID через API ноды и
сохраняет назначение клиента на конкретный сервер.

Поддерживаются два пути трафика:

- `direct` — пользователь подключается непосредственно к зарубежной VPS;
- `cascade` — пользователь подключается к центральной VPS, которая направляет
  VPN-трафик на выбранную зарубежную VPS.

Внешний установщик протоколов задаётся в локальном плане двумя полями:

```json
{
  "protocol_installer": {
    "url": "https://downloads.example.com/3x-ui-protocol-installer.sh",
    "sha256": "ПРОВЕРЕННАЯ_SHA256_СУММА"
  }
}
```

Мастер принимает только HTTPS и перед запуском проверяет SHA-256. Не используйте
непроверенный `curl | bash` и не сохраняйте токены, клиентские ссылки или
закрытые ключи в Git.

## Резервное копирование

`home-vpn-kit-backup.timer` ежедневно запускает совместимую команду локального
backup, проверяет структуру архива и выполняет `PRAGMA integrity_check` для базы
3x-ui. Архивы остаются в `/root/kit-backup-*.tar.gz`; срок хранения по умолчанию
составляет 30 дней.

```bash
sudo systemctl start home-vpn-kit-backup.service
sudo journalctl -u home-vpn-kit-backup.service -n 30 --no-pager
sudo systemctl list-timers home-vpn-kit-backup.timer
```

Для восстановления используйте установщик, URL и SHA-256 которого зафиксированы
в плане конкретного развёртывания. Сначала распакуйте архив во временный каталог,
проверьте базу и только затем запускайте поддерживаемую этим установщиком команду
восстановления. Полный порядок описан в [backup-and-restore.md](backup-and-restore.md).
