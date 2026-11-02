#!/bin/zsh
# Builds build/Relay.app from the Swift package: release binary, String Catalog synced from the
# source strings and compiled into the bundle, Info.plist, ad-hoc signature.
#   scripts/build.sh            build
#   scripts/build.sh --open     build and launch
set -euo pipefail
cd "${0:A:h}/.."

CONFIG=release
APP=build/Relay.app
CATALOG=Sources/RelayApp/Resources/Localizable.xcstrings
LOC=.build/loc-strings

rm -rf "$LOC" && mkdir -p "$LOC"
swift build -c "$CONFIG" --product RelayApp \
  -Xswiftc -emit-localized-strings -Xswiftc -emit-localized-strings-path -Xswiftc "$LOC"

# Add new UI strings to the catalog (translations are kept; removed strings are marked stale).
xcrun xcstringstool sync "$CATALOG" --stringsdata "$LOC"/*.stringsdata

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$(swift build -c "$CONFIG" --show-bin-path)/RelayApp" "$APP/Contents/MacOS/Relay"
cp Info.plist "$APP/Contents/Info.plist"
xcrun xcstringstool compile "$CATALOG" --output-directory "$APP/Contents/Resources"
codesign --force --sign - --timestamp=none "$APP"
echo "built $PWD/$APP"

if [[ "${1:-}" == "--open" ]]; then
  pkill -x Relay 2>/dev/null || true
  open "$APP"
fi
