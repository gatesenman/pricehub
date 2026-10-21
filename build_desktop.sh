#!/usr/bin/env bash
# 在当前操作系统打包桌面客户端（PyInstaller 不支持交叉编译：Windows 包需在 Windows 上运行，macOS 包需在 macOS 上运行）
set -e; cd "$(dirname "$0")"
pyinstaller --noconfirm --clean --onedir --windowed --name PriceHub \
  --exclude-module torch --exclude-module sentence_transformers --exclude-module transformers --exclude-module sklearn desktop_client.py
echo "产物: dist/PriceHub/"
