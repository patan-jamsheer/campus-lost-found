import re

with open('app.py', 'r') as f:
    content = f.read()

old = '''def get_db_connection():
    return mysql.connector.connect(
        host="sql12.freesqldatabase.com",
        user="sql12818306",
        password="HgCSNGey8Q",
        database="sql12818306",
        autocommit=True
    )'''

new = '''def get_db_connection():
    return mysql.connector.connect(
        host=os.environ.get("MYSQL_HOST", "sql12.freesqldatabase.com"),
        user=os.environ.get("MYSQL_USER", "sql12818306"),
        password=os.environ.get("MYSQL_PASSWORD", "HgCSNGey8Q"),
        database=os.environ.get("MYSQL_DB", "sql12818306"),
        autocommit=True
    )'''

content = content.replace(old, new)

with open('app.py', 'w') as f:
    f.write(content)

print("Done!")
