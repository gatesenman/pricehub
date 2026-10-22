#!/usr/bin/env bash
# 构建 dist/PriceHub-Setup.exe（NSIS）：嵌入式 Python 3.11 x64 + 预装 win_amd64 cp311 依赖
# （含 pywebview/pythonnet，离线桌面窗口）+ 源码 + 种子库；
# 同时生成 dist/PriceHub-source.zip。需要：makensis、python3、pip、网络（仅构建时）。
set -euo pipefail
cd "$(dirname "$0")/.."
PYV=3.11.9; B=build_win; ST=$B/stage; PIP=${PIP:-python3 -m pip}
rm -rf "$ST"; mkdir -p "$ST/python" "$ST/seed" "$B/cache/wheels" dist
[ -f "$B/cache/python-$PYV-embed-amd64.zip" ] || curl -sSL -o "$B/cache/python-$PYV-embed-amd64.zip" "https://www.python.org/ftp/python/$PYV/python-$PYV-embed-amd64.zip"
python3 -c "import zipfile,sys;zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" "$B/cache/python-$PYV-embed-amd64.zip" "$ST/python"
printf 'python311.zip\r\n.\r\nLib\\site-packages\r\n..\r\nimport site\r\n' > "$ST/python/python311._pth"
W="--only-binary=:all: --platform win_amd64 --python-version 3.11 --implementation cp --abi cp311"

# 核心依赖（全二进制）
$PIP download -q -d "$B/cache/wheels" $W -r requirements-windows.txt tzdata 2>/dev/null || true
# requirements 里有纯 python / 仅 sdist 的包时，上面可能不全；再按包名补齐
CORE_PKGS=(fastapi uvicorn httpx beautifulsoup4 APScheduler numpy scikit-learn scipy ImageHash pillow
  python-multipart prometheus_client sqlglot cryptography PyJWT tzdata tzlocal anyio starlette
  pydantic pydantic-core click h11 httpcore idna certifi soupsieve annotated-types typing-extensions
  typing-inspection annotated-doc threadpoolctl joblib cffi pycparser cloudpickle)
$PIP download -q -d "$B/cache/wheels" $W "${CORE_PKGS[@]}" || true

# 桌面窗口依赖：纯 python wheel + pythonnet；proxy_tools 仅有 sdist，现场打 wheel
$PIP download -q -d "$B/cache/wheels" 'pywebview==6.2.1' 'pythonnet==3.2.1' 'clr_loader==0.3.1' 'bottle==0.13.4' || true
if ! ls "$B"/cache/wheels/proxy_tools-*.whl >/dev/null 2>&1; then
  $PIP download -q -d "$B/cache/wheels" --no-binary=:none: --no-deps proxy_tools
  SDIST=$(ls "$B"/cache/wheels/proxy_tools-*.tar.gz | head -1)
  $PIP wheel -q -w "$B/cache/wheels" "$SDIST"
fi
# win_amd64 cffi（若刚才下到了 linux 的，删掉再下）
rm -f "$B"/cache/wheels/cffi-*-manylinux*.whl
$PIP download -q -d "$B/cache/wheels" $W cffi pycparser typing_extensions || true

$PIP install -q --no-deps --no-compile --target "$ST/python/Lib/site-packages" $W "$B"/cache/wheels/*.whl
SP="$ST/python/Lib/site-packages"
find "$SP" -type d \( -name tests -o -name test \) \( -path "*/scipy/*" -o -path "*/sklearn/*" -o -path "*/numpy/*" -o -path "*/pandas/*" \) -prune -exec rm -rf {} + || true
find "$SP" -name "*.pyi" -delete; rm -rf "$SP/bin"

# 确认桌面依赖在包内
python3 - <<PY
import os, sys
sp = r"$SP"
need = ["webview", "clr", "bottle", "proxy_tools", "clr_loader"]
missing = [n for n in need if not (os.path.isdir(os.path.join(sp, n)) or os.path.isfile(os.path.join(sp, n + ".py")) or os.path.isdir(os.path.join(sp, n.replace("-", "_"))) or os.path.isfile(os.path.join(sp, "clr.py")))]
# clr is a top-level clr.py from pythonnet
ok = all([
    os.path.isdir(os.path.join(sp, "webview")),
    os.path.isfile(os.path.join(sp, "clr.py")),
    os.path.isdir(os.path.join(sp, "bottle")) or os.path.isfile(os.path.join(sp, "bottle.py")),
    os.path.isdir(os.path.join(sp, "proxy_tools")),
])
print("site-packages desktop deps ok:", ok)
if not ok:
    print("listing:", sorted(os.listdir(sp))[:80])
    sys.exit(1)
PY

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
VER=1.13.$(git rev-list --count HEAD)
makensis -V2 -DVERSION="$VER" -DSTAGE="$(realpath "$ST")" -DOUTFILE="$(realpath dist)/PriceHub-Setup.exe" windows/installer/PriceHub.nsi
ls -la dist/PriceHub-Setup.exe
# 源码包
rm -f dist/PriceHub-source.zip
git archive --format=zip --prefix=PriceHub-source/ -o dist/PriceHub-source.zip HEAD -- . ':(exclude)screenshots' ':(exclude)data/*.db-shm' ':(exclude)data/*.db-wal' ':(exclude)logs' ':(exclude)e2e_output.log' ':(exclude)screenshot.png' ':(exclude)oom' ':(exclude)build_win' ':(exclude)dist'
# Ensure latest launcher files are in the zip even if we commit after — rebuild zip after commit
ls -la dist/PriceHub-source.zip
