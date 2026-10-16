# Office for iPhone

A thin native shell around your Office page. Why bother: iOS draws a keyboard accessory bar
(⌃ ⌄ ✓) above every web text field, in Safari and in home-screen web apps alike, and only a
native app can switch it off. Everything else is the web page itself — terminals, the
message box, pull-to-refresh, the share Shortcut — so the Office keeps working exactly the
same for anyone using it from a browser. The shell never needs an update when the Office
changes.

## Build (once)

On a Mac with Xcode installed:

    sh ios/make.sh

That installs `xcodegen` (via Homebrew) if needed, generates `Office.xcodeproj` and opens
it. In Xcode: **Signing & Capabilities → Team** (your Apple ID or developer team), change
the bundle identifier if you like, plug in the iPhone, press **Run**. With a free Apple ID
the app expires after 7 days and must be re-run from Xcode; with a paid developer account
it stays for a year, or ship it to yourself through TestFlight.

## Use

First launch asks for the Office address and remembers it. Shake the phone to change it.
Links to other sites open in an in-app Safari sheet; a popped-out terminal or a file from
the Files browser opens as a sheet on top with a Done button.

The app has to be on the same network as the Office (your VPN on the phone).
