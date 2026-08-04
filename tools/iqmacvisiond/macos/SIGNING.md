# Signing & notarization — IQ Vision.app

Unsigned, the app runs after a right-click ▸ Open (Gatekeeper first-run). For
distribution, sign + notarize:

```bash
APP="tools/iqmacvisiond/macos/dist/IQ Vision.app"
ENT="tools/iqmacvisiond/macos/entitlements.plist"
IDENTITY="Developer ID Application: <YOUR NAME> (<TEAMID>)"

codesign --force --deep --options runtime --entitlements "$ENT" \
  --sign "$IDENTITY" "$APP"

hdiutil create -volname "IQ Vision" -srcfolder "$(dirname "$APP")" -ov -format UDZO \
  tools/iqmacvisiond/macos/dist/IQVision.dmg

xcrun notarytool submit tools/iqmacvisiond/macos/dist/IQVision.dmg \
  --apple-id "<APPLE_ID>" --team-id "<TEAMID>" --password "<APP_PW>" --wait
xcrun stapler staple tools/iqmacvisiond/macos/dist/IQVision.dmg
```

The entitlements cover: JIT + unsigned exec memory + library-validation off
(tinygrad Metal JIT) and network server/client (LAN discovery + inference).
