#!/bin/sh
# Builds the Xcode project for the Office iPhone wrapper and opens it.
#   sh ios/make.sh
# Needs Xcode (App Store) and Homebrew. Then plug in the phone and press Run in Xcode.
# The signing team is asked once and kept in ios/team.txt (not in git), so a rebuild
# doesn't come back with Team: None on both targets.
set -eu
cd "$(dirname "$0")"
command -v xcodebuild >/dev/null 2>&1 || { echo "✗ Xcode is not installed (get it from the App Store, open it once)"; exit 1; }
if ! command -v xcodegen >/dev/null 2>&1; then
  command -v brew >/dev/null 2>&1 || { echo "✗ Homebrew is needed to install xcodegen: https://brew.sh"; exit 1; }
  echo "→ installing xcodegen"; brew install xcodegen
fi
[ -f Office/Assets.xcassets/AppIcon.appiconset/icon-83.5@2x.png ] || sh ./icon.sh

# the signing team: ios/team.txt, else the teams of the Apple Development certificates
# in the keychain (one is taken as it is, several are asked once)
if [ ! -s team.txt ]; then
  tmp=$(mktemp -d)
  security find-certificate -a -c "Apple Development" -p 2>/dev/null \
    | awk -v d="$tmp" '/BEGIN CERT/{n++} {print > (d "/c" n ".pem")}'
  for c in "$tmp"/c*.pem; do
    [ -f "$c" ] || continue
    subj=$(openssl x509 -in "$c" -noout -subject -nameopt RFC2253 2>/dev/null) || continue
    id=$(printf '%s\n' "$subj" | sed -n 's/.*OU=\([A-Z0-9]\{10\}\).*/\1/p')
    org=$(printf '%s\n' "$subj" | sed -n 's/.*,O=\([^,]*\).*/\1/p')
    [ -n "$id" ] && printf '%s\t%s\n' "$id" "$org"
  done | sort -u > "$tmp/teams"
  n=$(wc -l < "$tmp/teams" | tr -d ' ')
  if [ "$n" = 1 ]; then
    cut -f1 "$tmp/teams" > team.txt
  elif [ "$n" -gt 1 ]; then
    echo "Which team signs the app?"; awk -F'\t' '{printf "  %d) %s (%s)\n", NR, $2, $1}' "$tmp/teams"
    printf "Number: "; read -r pick
    sed -n "${pick}p" "$tmp/teams" | cut -f1 > team.txt
  fi
  rm -rf "$tmp"
fi
OFFICE_TEAM=$(tr -d ' \n' < team.txt 2>/dev/null || true)
export OFFICE_TEAM
if [ -n "$OFFICE_TEAM" ]; then echo "→ signing team $OFFICE_TEAM (ios/team.txt; delete it to pick again)"
else echo "! no signing team found: pick it in Xcode under Signing & Capabilities"; fi
xcodegen generate
echo "✓ Office.xcodeproj ready — opening Xcode"
open -a Xcode Office.xcodeproj || open -a Xcode-27 Office.xcodeproj || open Office.xcodeproj
