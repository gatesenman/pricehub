#!/usr/bin/env bash
# 打包 Windows 版：dist/PriceHub-windows.zip（源码 + vendor + 当前 SQLite 数据 + 启动脚本 + 使用说明），要求 < 95 MB。
set -euo pipefail
cd "$(dirname "$0")/.."
OUT=dist/PriceHub-windows.zip; STAGE=$(mktemp -d)/PriceHub; mkdir -p "$STAGE" dist
# 1) 已提交的源码（不含截图、测试、打包产物、旧的 db-shm/db-wal）
git archive HEAD app plugins extension docs/PLUGIN_SDK.md docs/ARCHITECTURE.md docs/PRD.md README.md requirements.txt data/platforms.csv data/sample_products.csv \
  | tar x -C "$STAGE"
rm -rf "$STAGE/app/static/v1" 2>/dev/null || true
find "$STAGE" -name '__pycache__' -prune -exec rm -rf {} +
# 2) 数据库：先 checkpoint WAL，再用 SQLite 在线备份接口拷一份一致的快照（不带 -wal/-shm）
python3 - "$STAGE/data/pricehub.db" <<'PY'
import sqlite3, sys
src = sqlite3.connect("data/pricehub.db"); src.execute("PRAGMA wal_checkpoint(TRUNCATE)")
dst = sqlite3.connect(sys.argv[1]); src.backup(dst); dst.execute("PRAGMA journal_mode=DELETE"); dst.execute("VACUUM")
ok = dst.execute("PRAGMA integrity_check").fetchone()[0]; n = dst.execute("SELECT COUNT(*) FROM products WHERE merged_into IS NULL").fetchone()[0]
dst.close(); src.close(); assert ok == "ok", ok; print("db snapshot ok, products:", n)
PY
mkdir -p "$STAGE/data/images" "$STAGE/data/evidence" "$STAGE/logs"
cp -r data/images/. "$STAGE/data/images/" 2>/dev/null || true
# 3) Windows 启动文件（.bat 用 CRLF、UTF-8 无 BOM）
cp requirements-windows.txt windows/run_windows.py "$STAGE/"
sed 's/\r*$/\r/' windows/start.bat > "$STAGE/启动PriceHub.bat"
sed 's/\r*$/\r/' windows/使用说明.txt > "$STAGE/使用说明.txt"
# 4) 压缩
rm -f "$OUT"
python3 - "$(dirname "$STAGE")" "$OUT" <<'PY'
import os, sys, zipfile   # Python 写入的中文文件名带 UTF-8 标志，Windows 10/11 资源管理器可正常解压
base, out = sys.argv[1], sys.argv[2]
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
    for dp, dn, fn in os.walk(os.path.join(base, "PriceHub")):
        dn.sort()
        for f in sorted(fn): full = os.path.join(dp, f); z.write(full, os.path.relpath(full, base))
PY
SZ=$(stat -c %s "$OUT"); echo "$OUT $SZ bytes"; [ "$SZ" -lt $((95*1024*1024)) ] || { echo "ERROR: zip >= 95MB"; exit 1; }
rm -rf "$(dirname "$STAGE")"
