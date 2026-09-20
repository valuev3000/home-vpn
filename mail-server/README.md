# HOME-VPN mail server

Small Postfix + Dovecot mail server intended for the HOME-VPN control site.
It provides authenticated SMTP submission on ports 587/465, IMAPS on 993,
DKIM signing, and Fail2ban protection.

## Install

Run on the mail server as root from the repository root, or select the mail
component in `sudo bash install.sh`. For a non-interactive example:

```bash
MAIL_DOMAIN=my.domain.ru \
MAIL_HOST=mail.my.domain.ru \
MAIL_ADDRESS=support@my.domain.ru \
MAIL_BIND_IP=192.168.1.50 \
bash mail-server/setup-mail-server.sh
```

The generated mailbox credentials are stored only in
`/root/home-vpn-mail-credentials.txt` with mode `0600`. Never commit that file.

The bundled certificate is self-signed and intended only for an internal test
stand. Replace it with the certificate for `mail.my.domain.ru` before public
deployment.

## Required public DNS

- `A mail.my.domain.ru` -> the public IP of the mail server/router.
- `MX my.domain.ru` -> `10 mail.my.domain.ru`.
- `TXT my.domain.ru` -> SPF for the actual outbound public IP.
- `TXT mail._domainkey.my.domain.ru` -> value generated in
  `/etc/opendkim/keys/my.domain.ru/mail.txt`.
- `TXT _dmarc.my.domain.ru` -> begin with monitoring policy `p=none`.
- PTR/rDNS for the outbound public IP -> `mail.my.domain.ru` (configured by the
  ISP or hosting provider, not at the DNS registrar).

Do not advertise the server publicly until forward DNS, PTR, TLS, SPF, DKIM and
DMARC agree with the real outbound IP.
