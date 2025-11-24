import sys, sqlite3
from werkzeug.security import generate_password_hash

if len(sys.argv) < 2:
    print("Uso: python set_admin_password.py NOVA_SENHA [USERNAME]")
    sys.exit(1)

nova = sys.argv[1]
username = sys.argv[2] if len(sys.argv) > 2 else 'admin'

conn = sqlite3.connect('database.db')
cur = conn.cursor()
hashed = generate_password_hash(nova)
cur.execute("UPDATE admin SET password=? WHERE username=?", (hashed, username))
conn.commit()
conn.close()
print(f"Senha alterada para {username}")