#!/usr/bin/env python3
import argparse,base64,hashlib,re,secrets,shutil,sqlite3,string,time

SPECIAL='!@#$%*-_'

def generated_password(length=16):
 chars=[secrets.choice(string.ascii_lowercase),secrets.choice(string.ascii_uppercase),secrets.choice(string.digits),secrets.choice(SPECIAL)]
 chars.extend(secrets.choice(string.ascii_letters+string.digits+SPECIAL) for _ in range(length-len(chars)));secrets.SystemRandom().shuffle(chars)
 return ''.join(chars)

def main():
 parser=argparse.ArgumentParser(description='Reset HOME-VPN administrator login and password')
 parser.add_argument('--database',default='/var/lib/vpn-shop/shop.db')
 parser.add_argument('--login',required=True,help='New login, for example home-admin')
 parser.add_argument('--password',help='New strong password; if omitted, a random 16-character password is generated')
 args=parser.parse_args();login=args.login.strip().lower();password=args.password or generated_password()
 if not re.fullmatch(r'(?=.{8,32}$)(?=.*[a-z])[a-z0-9][a-z0-9-]*[a-z0-9]',login):raise SystemExit('ERROR: login must contain 8-32 lowercase Latin letters; digits and hyphens are optional')
 if len(password)<8 or not any(x.islower() for x in password) or not any(x.isupper() for x in password) or not any(x.isdigit() for x in password) or not any(not x.isalnum() for x in password):raise SystemExit('ERROR: password must contain lowercase, uppercase, digit and special character')
 backup=args.database+'.admin-reset-'+time.strftime('%Y%m%d-%H%M%S')+'.bak';shutil.copy2(args.database,backup)
 salt=secrets.token_bytes(16);digest=hashlib.scrypt(password.encode(),salt=salt,n=16384,r=8,p=1)
 with sqlite3.connect(args.database) as connection:
  connection.row_factory=sqlite3.Row;admin=connection.execute("SELECT * FROM users WHERE role='admin' ORDER BY id LIMIT 1").fetchone()
  if not admin:raise SystemExit('ERROR: administrator account not found')
  if connection.execute('SELECT 1 FROM users WHERE lower(username)=lower(?) AND id<>?',(login,admin['id'])).fetchone():raise SystemExit('ERROR: login is already in use')
  old=admin['username'];connection.execute('UPDATE users SET username=?,salt=?,pwhash=?,session_epoch=COALESCE(session_epoch,0)+1 WHERE id=?',(login,base64.b64encode(salt).decode(),base64.b64encode(digest).decode(),admin['id']))
  connection.execute('DELETE FROM auth_sessions WHERE username=?',(old,));connection.execute('UPDATE orders SET customer=? WHERE user_id=? AND customer=?',(login,admin['id'],old))
  if admin['telegram_id'] is not None:connection.execute('UPDATE bot_profiles SET login=? WHERE telegram_id=?',(login,admin['telegram_id']))
 print('HOME-VPN administrator credentials reset successfully')
 print('Login: '+login);print('Password: '+password);print('Database backup: '+backup)
 print('Save the password now. Restart is not required; all previous site sessions are invalidated.')

if __name__=='__main__':main()
