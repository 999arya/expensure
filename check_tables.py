import os, sys
sys.path.insert(0, r'C:\Users\Lenovo\finance')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()
from django.db import connection
with connection.cursor() as cursor:
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'telegram_bot%'")
    for row in cursor.fetchall():
        print(row[0])