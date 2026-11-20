#!/bin/sh
# Build dist/trainnet-android.tar.gz (code only, no data) to copy to the phone.
cd "$(dirname "$0")/.." && mkdir -p dist
tar czf dist/trainnet-android.tar.gz -s ',^,trainnet/,' --exclude __pycache__ pyproject.toml README.md trainnet android
echo dist/trainnet-android.tar.gz
