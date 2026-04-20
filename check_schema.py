import sqlite3
conn = sqlite3.connect('data/sessions/main/chat.db')
c = conn.cursor()
c.execute('PRAGMA table_info(sessions)')
print('sessions schema:')
for row in c.fetchall():
    print(row)
c.execute('PRAGMA table_info(messages)')
print('messages schema:')
for row in c.fetchall():
    print(row)
c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='sessions'")
print('sessions CREATE:', c.fetchone()[0])
conn.close()
