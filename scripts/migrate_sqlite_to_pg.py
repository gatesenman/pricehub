"""把单机 SQLite 数据迁移到 PostgreSQL。用法：
PRICEHUB_DB_URL=postgresql://user:pass@host/db python scripts/migrate_sqlite_to_pg.py data/pricehub.db"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if not os.environ.get("PRICEHUB_DB_URL"): sys.exit("请先设置 PRICEHUB_DB_URL=postgresql://...")
from app.core import storage as db
db.init(); db.import_sqlite(sys.argv[1])
for t in ("products", "prices", "sources", "platforms"): print(t, db.q(f"SELECT COUNT(*) n FROM {t}", one=True)["n"])
