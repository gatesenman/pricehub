#!/bin/bash
# 按文件分批跑测试；用法：scripts/run_tests_batched.sh [pg]
cd "$(dirname "$0")/.."; tag=sqlite
if [ "$1" = pg ]; then tag=pg; export PRICEHUB_DB_URL=postgresql://pricehub:pricehub@127.0.0.1:5432/pricehub_test PRICEHUB_PG_SCHEMA=t_$(date +%s); fi
for f in tests/test_*.py; do [ "$tag" = pg ] && export PRICEHUB_PG_SCHEMA=t_$(date +%s)_$(basename $f .py); echo "=== $f"; timeout 900 nice .venv/bin/python -m pytest -q -x "$f" 2>&1 | tail -15; echo "rc=${PIPESTATUS[0]}"; done
echo ALLDONE
