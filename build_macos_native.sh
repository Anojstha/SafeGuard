#!/bin/bash
set -euo pipefail

SOURCE="spp_support_bundle_Automation_windows_macos.py"
APP_NAME="SafeGuard Support Bundle Automation"
BUNDLE_ID="com.safeguardtools.supportbundleautomation"
ARCH="$(uname -m)"

case "$ARCH" in
  arm64) SUFFIX="apple-silicon" ;;
  x86_64) SUFFIX="intel" ;;
  *) echo "Unsupported Mac architecture: $ARCH" >&2; exit 1 ;;
esac

python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install --only-binary=:all: "cryptography>=43,<47"
python -m pip install -r requirements-macos.txt
python -m py_compile "$SOURCE"

rm -rf build dist dmg-content
rm -f ./*.spec

python -m PyInstaller \
  --onedir \
  --windowed \
  --clean \
  --noconfirm \
  --osx-bundle-identifier "$BUNDLE_ID" \
  --name "$APP_NAME" \
  --collect-all paramiko \
  "$SOURCE"

codesign --force --deep --sign - "dist/$APP_NAME.app"
mkdir -p dmg-content
cp -R "dist/$APP_NAME.app" dmg-content/
ln -s /Applications dmg-content/Applications
hdiutil create \
  -volname "$APP_NAME" \
  -srcfolder dmg-content \
  -ov \
  -format UDZO \
  "dist/SafeGuard_Support_Bundle_Automation_${SUFFIX}.dmg"

shasum -a 256 "dist/SafeGuard_Support_Bundle_Automation_${SUFFIX}.dmg" \
  > "dist/SHA256_${SUFFIX}.txt"

echo "Created: dist/SafeGuard_Support_Bundle_Automation_${SUFFIX}.dmg"
