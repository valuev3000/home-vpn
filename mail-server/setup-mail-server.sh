#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "Run this installer as root." >&2
  exit 1
fi

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

mail_domain="${MAIL_DOMAIN:-my.domain.ru}"
mail_host="${MAIL_HOST:-mail.$mail_domain}"
mail_user="${MAIL_USER:-support}"
mail_address="${MAIL_ADDRESS:-$mail_user@$mail_domain}"
mail_bind_ip="${MAIL_BIND_IP:-192.168.1.50}"
mail_tls_dir="/etc/home-vpn-mail/tls"
mail_credentials="/root/home-vpn-mail-credentials.txt"

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y postfix dovecot-imapd opendkim opendkim-tools \
  sasl2-bin mailutils fail2ban ca-certificates openssl

install -d -m 0755 /etc/systemd/system/postfix.service.d /etc/systemd/system/dovecot.service.d
install -m 0644 "$script_dir/postfix-lxc.conf" /etc/systemd/system/postfix.service.d/lxc.conf
install -m 0644 "$script_dir/dovecot-lxc.conf" /etc/systemd/system/dovecot.service.d/lxc.conf
install -m 0644 "$script_dir/99-home-vpn.conf" /etc/dovecot/conf.d/99-home-vpn.conf
install -m 0644 "$script_dir/opendkim.conf" /etc/opendkim.conf
install -m 0644 "$script_dir/home-vpn-mail.local" /etc/fail2ban/jail.d/home-vpn-mail.local
printf '127.0.0.1\nlocalhost\n*.%s\n' "$mail_domain" > /etc/opendkim/trusted.hosts
printf 'mail._domainkey.%s %s:mail:/etc/opendkim/keys/%s/mail.private\n' "$mail_domain" "$mail_domain" "$mail_domain" > /etc/opendkim/key.table
printf '*@%s mail._domainkey.%s\n' "$mail_domain" "$mail_domain" > /etc/opendkim/signing.table

install -d -m 0750 "$mail_tls_dir"
if [[ ! -s "$mail_tls_dir/mail.key" || ! -s "$mail_tls_dir/mail.crt" ]]; then
  openssl req -x509 -newkey rsa:3072 -sha256 -nodes -days 825 \
    -keyout "$mail_tls_dir/mail.key" -out "$mail_tls_dir/mail.crt" \
    -subj "/CN=$mail_host" \
    -addext "subjectAltName=DNS:$mail_host,DNS:$mail_domain,IP:$mail_bind_ip"
  chmod 0600 "$mail_tls_dir/mail.key"
  chmod 0644 "$mail_tls_dir/mail.crt"
fi
install -m 0644 "$mail_tls_dir/mail.crt" /usr/local/share/ca-certificates/home-vpn-mail.crt
update-ca-certificates >/dev/null

if ! id "$mail_user" >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash "$mail_user"
fi
if [[ ! -s "$mail_credentials" ]]; then
  mail_password="$(openssl rand -base64 24 | tr -d '/+=' | head -c 24)Aa1!"
  printf '%s:%s\n' "$mail_user" "$mail_password" | chpasswd
  umask 077
  printf 'MAIL_ADDRESS=%s\nMAIL_USERNAME=%s\nMAIL_PASSWORD=%s\nSMTP_HOST=%s\nSMTP_PORT=587\nIMAP_HOST=%s\nIMAP_PORT=993\n' \
    "$mail_address" "$mail_user" "$mail_password" "$mail_host" "$mail_host" > "$mail_credentials"
fi

install -d -o "$mail_user" -g "$mail_user" -m 0700 "/home/$mail_user/Maildir" "/home/$mail_user/Maildir/cur" "/home/$mail_user/Maildir/new" "/home/$mail_user/Maildir/tmp"

install -d -o opendkim -g postfix -m 0750 /var/spool/postfix/opendkim
install -d -o opendkim -g opendkim -m 0700 "/etc/opendkim/keys/$mail_domain"
if [[ ! -s "/etc/opendkim/keys/$mail_domain/mail.private" ]]; then
  opendkim-genkey -b 2048 -D "/etc/opendkim/keys/$mail_domain" -d "$mail_domain" -s mail
fi
chown -R opendkim:opendkim "/etc/opendkim/keys/$mail_domain"
chmod 0600 "/etc/opendkim/keys/$mail_domain/mail.private"

postconf -e "myhostname = $mail_host"
postconf -e "mydomain = $mail_domain"
postconf -e 'myorigin = $mydomain'
postconf -e 'mydestination = $myhostname, localhost.$mydomain, localhost, $mydomain'
postconf -e 'inet_interfaces = all'
postconf -e 'inet_protocols = ipv4'
postconf -e 'mynetworks = 127.0.0.0/8'
postconf -e 'home_mailbox = Maildir/'
postconf -e 'recipient_delimiter = +'
postconf -e 'disable_vrfy_command = yes'
postconf -e 'smtpd_helo_required = yes'
postconf -e 'message_size_limit = 20971520'
postconf -e "smtpd_tls_cert_file = $mail_tls_dir/mail.crt"
postconf -e "smtpd_tls_key_file = $mail_tls_dir/mail.key"
postconf -e 'smtpd_tls_security_level = may'
postconf -e 'smtpd_tls_auth_only = yes'
postconf -e 'smtp_tls_security_level = may'
postconf -e 'smtpd_sasl_type = dovecot'
postconf -e 'smtpd_sasl_path = private/auth'
postconf -e 'smtpd_sasl_auth_enable = yes'
postconf -e 'smtpd_sasl_security_options = noanonymous'
postconf -e 'smtpd_relay_restrictions = permit_mynetworks, permit_sasl_authenticated, reject_unauth_destination'
postconf -e 'smtpd_recipient_restrictions = reject_unknown_recipient_domain, permit_mynetworks, permit_sasl_authenticated, reject_unauth_destination'
postconf -e 'smtpd_milters = unix:opendkim/opendkim.sock'
postconf -e 'non_smtpd_milters = unix:opendkim/opendkim.sock'
postconf -e 'milter_default_action = accept'
postconf -e 'milter_protocol = 6'

postconf -M 'submission/inet=submission inet n - y - - smtpd'
postconf -P 'submission/inet/syslog_name=postfix/submission'
postconf -P 'submission/inet/smtpd_tls_security_level=encrypt'
postconf -P 'submission/inet/smtpd_sasl_auth_enable=yes'
postconf -P 'submission/inet/smtpd_relay_restrictions=permit_sasl_authenticated,reject'
postconf -P 'submission/inet/smtpd_recipient_restrictions=permit_sasl_authenticated,reject'
postconf -M 'smtps/inet=smtps inet n - y - - smtpd'
postconf -P 'smtps/inet/syslog_name=postfix/smtps'
postconf -P 'smtps/inet/smtpd_tls_wrappermode=yes'
postconf -P 'smtps/inet/smtpd_sasl_auth_enable=yes'
postconf -P 'smtps/inet/smtpd_relay_restrictions=permit_sasl_authenticated,reject'
postconf -P 'smtps/inet/smtpd_recipient_restrictions=permit_sasl_authenticated,reject'

newaliases
doveconf -n >/dev/null
postfix check
systemctl daemon-reload
systemctl enable opendkim dovecot postfix fail2ban >/dev/null
systemctl restart opendkim
systemctl restart dovecot
systemctl restart postfix
fail2ban-client -t
systemctl restart fail2ban

printf 'Mail server configured for %s\n' "$mail_address"
