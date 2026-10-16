#!/bin/sh
# Builds the Xcode project for the Office iPhone wrapper and opens it.
#   sh ios/make.sh
# Needs Xcode (App Store) and Homebrew. Then in Xcode: pick your team under
# Signing & Capabilities, plug in the phone, press Run.
set -eu
cd "$(dirname "$0")"
command -v xcodebuild >/dev/null 2>&1 || { echo "✗ Xcode is not installed (get it from the App Store, open it once)"; exit 1; }
if ! command -v xcodegen >/dev/null 2>&1; then
  command -v brew >/dev/null 2>&1 || { echo "✗ Homebrew is needed to install xcodegen: https://brew.sh"; exit 1; }
  echo "→ installing xcodegen"; brew install xcodegen
fi
[ -d Office/Assets.xcassets ] || sh ./icon.sh
xcodegen generate
echo "✓ Office.xcodeproj ready — opening Xcode"
open Office.xcodeproj
