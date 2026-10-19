#!/bin/sh
# Makes the app icon set (a 🏢 on the Office purple) with the tools every Mac has.
set -eu
cd "$(dirname "$0")"
OUT=Office/Assets.xcassets/AppIcon.appiconset
mkdir -p "$OUT"
cat > Office/Assets.xcassets/Contents.json <<'J'
{ "info": { "version": 1, "author": "xcode" } }
J
# 1024px PNG drawn with Swift (no ImageMagick needed)
cat > /tmp/office-icon.swift <<'S'
import AppKit
let size = 1024.0
let img = NSImage(size: NSSize(width: size, height: size))
img.lockFocus()
NSColor(red: 0.357, green: 0.357, blue: 0.839, alpha: 1).setFill()
NSBezierPath(rect: NSRect(x: 0, y: 0, width: size, height: size)).fill()
let text = "🏢" as NSString
let attrs: [NSAttributedString.Key: Any] = [.font: NSFont.systemFont(ofSize: 700)]
let s = text.size(withAttributes: attrs)
text.draw(at: NSPoint(x: (size - s.width) / 2, y: (size - s.height) / 2), withAttributes: attrs)
img.unlockFocus()
let tiff = img.tiffRepresentation!
let png = NSBitmapImageRep(data: tiff)!.representation(using: .png, properties: [:])!
try! png.write(to: URL(fileURLWithPath: CommandLine.arguments[1]))
S
swift /tmp/office-icon.swift "$OUT/icon-1024.png"
[ -s "$OUT/icon-1024.png" ] || { echo "✗ icon PNG was not written"; exit 1; }
cat > "$OUT/Contents.json" <<'J'
{ "images": [ { "filename": "icon-1024.png", "idiom": "universal", "platform": "ios", "size": "1024x1024" } ],
  "info": { "version": 1, "author": "xcode" } }
J
echo "✓ icon set written"
