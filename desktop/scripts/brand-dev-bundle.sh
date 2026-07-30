#!/bin/bash
# Make the development Electron bundle identify itself as Kith.
#
# In development `electron .` runs Electron's own app bundle, so macOS labels everything
# from its Info.plist: the Dock, Cmd-Tab, and — the one that matters — Notification Center,
# which authorises notifications per bundle identifier. Left alone, Kith's notifications
# arrive as "Electron" under a setting nobody would think to look for.
#
# Editing the plist is not enough on its own, and getting that wrong is what sent me round
# in a circle: Electron ships ad-hoc signed, so changing anything inside the bundle
# invalidates the signature, and macOS silently refuses notifications from an app whose
# signature does not verify. Electron still reports the notification as shown. Nothing
# appears. So this re-signs afterwards, every time.
#
# Idempotent, and a no-op once done — safe to run before every start, which is how it is
# wired (npm prestart). A fresh `npm install` replaces the bundle and undoes all of it,
# which is exactly why this is a script and not something done by hand.
#
# None of this applies to a packaged build: `npm run package` produces a properly named,
# properly signed Kith.app and needs none of it.
set -euo pipefail

APP="node_modules/electron/dist/Electron.app"
PLIST="$APP/Contents/Info.plist"
BUNDLE_ID="com.kith.desktop"

[ -d "$APP" ] || { echo "[brand] no dev Electron bundle; nothing to do"; exit 0; }

current_exec=$(/usr/libexec/PlistBuddy -c "Print :CFBundleExecutable" "$PLIST" 2>/dev/null || echo "")
current_id=$(/usr/libexec/PlistBuddy -c "Print :CFBundleIdentifier" "$PLIST" 2>/dev/null || echo "")

installed="$APP/Contents/Resources/electron.icns"
icon_fresh=true
if [ -f resources/icon.icns ] && [ resources/icon.icns -nt "$installed" ]; then
  icon_fresh=false   # the mark was redrawn since it was installed
fi

if [ "$current_exec" = "Kith" ] && [ "$current_id" = "$BUNDLE_ID" ] && $icon_fresh \
   && codesign -v "$APP" 2>/dev/null; then
  exit 0   # already branded, icon current, signature still verifying
fi

echo "[brand] naming the dev bundle Kith"
[ -f "$APP/Contents/MacOS/Electron" ] && mv "$APP/Contents/MacOS/Electron" "$APP/Contents/MacOS/Kith"
/usr/libexec/PlistBuddy -c "Set :CFBundleExecutable Kith" "$PLIST" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Set :CFBundleName Kith" "$PLIST" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Set :CFBundleDisplayName Kith" "$PLIST" 2>/dev/null \
  || /usr/libexec/PlistBuddy -c "Add :CFBundleDisplayName string Kith" "$PLIST" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Set :CFBundleIdentifier $BUNDLE_ID" "$PLIST" 2>/dev/null || true
printf 'Electron.app/Contents/MacOS/Kith' > node_modules/electron/path.txt

# The icon. macOS draws the app's icon beside every notification it attributes to it, so
# without this Kith's notifications arrive wearing Electron's face — which is how this was
# noticed. Same story for the Dock, Cmd-Tab, and Notification Center's settings list.
if [ -f resources/icon.icns ]; then
  echo "[brand] installing Kith's icon"
  cp resources/icon.icns "$APP/Contents/Resources/electron.icns"
fi

# The part that is easy to forget and breaks notifications when forgotten.
echo "[brand] re-signing (a broken signature means macOS drops every notification)"
codesign --force --deep --sign - "$APP" >/dev/null 2>&1

LSREG="/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"
[ -x "$LSREG" ] && "$LSREG" -f "$APP" >/dev/null 2>&1 || true
echo "[brand] done — $(codesign -dv "$APP" 2>&1 | grep '^Identifier' || echo 'unsigned?')"
