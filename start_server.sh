#!/usr/bin/env bash
# 启动 :8800 主服务。默认使用 SQLite（data/pricehub.db，含商品数据）。
# 如确需 PostgreSQL，显式传入：PRICEHUB_DB_URL=postgresql://... ./start_server.sh
set -e
cd "$(dirname "$0")"
[ -f server.env ] && set -a && . ./server.env && set +a
[ -z "$PRICEHUB_DB_URL" ] && unset PRICEHUB_DB_URL PRICEHUB_PG_SCHEMA PRICEHUB_PG_SCHEMA_FROM_DB
export PRICEHUB_DB="${PRICEHUB_DB:-$PWD/data/pricehub.db}"
if [ ! -x .venv/bin/uvicorn ]; then
  echo "找不到 .venv/bin/uvicorn。请先在项目目录创建虚拟环境并安装依赖：" >&2
  echo "  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi
mkdir -p logs
pkill -f "uvicorn app.main:app --host 0.0.0.0 --port 8800" 2>/dev/null || true
sleep 2
nohup .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8800 >> logs/server_8800.log 2>&1 &
echo "started pid $! (db: ${PRICEHUB_DB_URL:-$PRICEHUB_DB})"
