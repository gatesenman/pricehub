#!/usr/bin/env bash
# 构建 dist/PriceHub-Setup.exe（NSIS）：嵌入式 Python 3.11 x64 + 预装 win_amd64 cp311 依赖 + 源码 + 42 商品种子库；
# 同时生成 dist/PriceHub-source.zip（git archive HEAD，不含截图/缓存/venv）。需要：makensis、python3、pip、网络（仅构建时）。
set -euo pipefail
cd "$(dirname "$0")/.."
PYV=3.11.9; B=build_win; ST=$B/stage; PIP=${PIP:-python3 -m pip}
rm -rf "$ST"; mkdir -p "$ST/python" "$ST/seed" "$B/cache/wheels" dist
[ -f "$B/cache/python-$PYV-embed-amd64.zip" ] || curl -sSL -o "$B/cache/python-$PYV-embed-amd64.zip" "https://www.python.org/ftp/python/$PYV/python-$PYV-embed-amd64.zip"
python3 -c "import zipfile,sys;zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" "$B/cache/python-$PYV-embed-amd64.zip" "$ST/python"
printf 'python311.zip\r\n.\r\nLib\\site-packages\r\n..\r\nimport site\r\n' > "$ST/python/python311._pth"
W="--only-binary=:all: --platform win_amd64 --python-version 3.11 --implementation cp --abi cp311"
$PIP download -q -d "$B/cache/wheels" $W -r requirements-windows.txt tzdata
$PIP install -q --no-deps --no-compile --target "$ST/python/Lib/site-packages" $W "$B"/cache/wheels/*.whl
SP="$ST/python/Lib/site-packages"
find "$SP" -type d \( -name tests -o -name test \) \( -path "*/scipy/*" -o -path "*/sklearn/*" -o -path "*/numpy/*" -o -path "*/pandas/*" \) -prune -exec rm -rf {} + || true
find "$SP" -name "*.pyi" -delete; rm -rf "$SP/bin"
# 源码（已提交版本）+ 平台目录 + 示例数据
git archive HEAD app plugins data/platforms.csv data/sample_products.csv | tar x -C "$ST"
rm -rf "$ST/app/static/v1"; mkdir -p "$ST/data/images" "$ST/data/evidence"
python3 - "$ST/seed/pricehub.db" <<'PY'
import sqlite3, sys
src = sqlite3.connect("data/pricehub.db"); src.execute("PRAGMA wal_checkpoint(TRUNCATE)")
dst = sqlite3.connect(sys.argv[1]); src.backup(dst); dst.execute("PRAGMA journal_mode=DELETE"); dst.execute("VACUUM")
assert dst.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
print("seed products:", dst.execute("SELECT COUNT(*) FROM products WHERE merged_into IS NULL").fetchone()[0]); dst.close()
PY
cp windows/installer/PriceHub.pyw "$ST/"; sed 's/\r*$/\r/' windows/installer/使用说明.txt > "$ST/使用说明.txt"
python3 - "$ST/PriceHub.ico" <<'PY'
import sys
from PIL import Image, ImageDraw, ImageFont
im = Image.new("RGBA", (256, 256), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
d.rounded_rectangle((8, 8, 248, 248), 48, fill=(0, 122, 204, 255))
try: f = ImageFont.truetype("DejaVuSans-Bold.ttf", 150)
except Exception: f = ImageFont.load_default()
d.text((128, 132), "P", font=f, fill="white", anchor="mm"); im.save(sys.argv[1], sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
PY
VER=$(git describe --tags --always 2>/dev/null | sed 's/[^0-9.]//g'); VER=1.13.$(git rev-list --count HEAD)
makensis -V2 -DVERSION="$VER" -DSTAGE="$(realpath "$ST")" -DOUTFILE="$(realpath dist)/PriceHub-Setup.exe" windows/installer/PriceHub.nsi
ls -la dist/PriceHub-Setup.exe
# 源码包
rm -f dist/PriceHub-source.zip
git archive --format=zip --prefix=PriceHub-source/ -o dist/PriceHub-source.zip HEAD -- . ':(exclude)screenshots' ':(exclude)data/*.db-shm' ':(exclude)data/*.db-wal' ':(exclude)logs' ':(exclude)e2e_output.log' ':(exclude)screenshot.png' ':(exclude)oom'
ls -la dist/PriceHub-source.zip
