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
# the classic full set: every size Xcode has ever asked for, so any actool accepts it
images=""
add(){ # idiom size scale -> file
  px=$(awk "BEGIN{printf \"%d\", $2*$3}"); f="icon-$2@$3x.png"
  [ -f "$OUT/$f" ] || sips -z "$px" "$px" "$OUT/icon-1024.png" --out "$OUT/$f" >/dev/null
  images="$images{ \"filename\": \"$f\", \"idiom\": \"$1\", \"scale\": \"${3}x\", \"size\": \"${2}x${2}\" },"
}
for s in 20 29 40 60; do add iphone $s 2; add iphone $s 3; done
for s in 20 29 40 76; do add ipad $s 1; add ipad $s 2; done
add ipad 83.5 2
images="$images{ \"filename\": \"icon-1024.png\", \"idiom\": \"ios-marketing\", \"scale\": \"1x\", \"size\": \"1024x1024\" }"
printf '{ "images": [ %s ], "info": { "version": 1, "author": "xcode" } }\n' "$images" > "$OUT/Contents.json"
python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "$OUT/Contents.json" || { echo "✗ Contents.json is not valid JSON"; exit 1; }
echo "✓ icon set written ($(ls "$OUT" | grep -c png) images)"
