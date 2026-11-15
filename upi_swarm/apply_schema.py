"""Creates the upi_* tables in Supabase using SUPABASE_DB_URL, then mirrors local data.   python apply_schema.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import store

url = os.getenv("SUPABASE_DB_URL", "")
if not url or "your-" in url:
    sys.exit("Set SUPABASE_DB_URL in upi_swarm/.env first.")
import psycopg2
sql = open(os.path.join(store.BASE, "schema.sql"), encoding="utf-8").read()
with psycopg2.connect(url, connect_timeout=15) as c, c.cursor() as cur:
    cur.execute(sql)
print("tables created")
print("supabase ready:", store.check_supabase())
if store.SB["ready"]:
    print("rows queued:", store.sync_all())
    import time; time.sleep(8)
    print("mirrored:", store.SB["mirrored"])
