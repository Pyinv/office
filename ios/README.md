# Office for iPhone

A thin native shell around your Office page. Why bother: iOS draws a keyboard accessory bar
(⌃ ⌄ ✓) above every web text field, in Safari and in home-screen web apps alike, and only a
native app can switch it off. Everything else is the web page itself — terminals, the
message box, pull-to-refresh — so the Office keeps working exactly the
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

## Share → Office

The app carries a Share Sheet entry (the `OfficeShare` extension). In any app, share texts, links,
photos or files → **Office** → optionally type a note → tap the agent. Everything you selected goes to
that agent as **one message, in the order you selected it**, in a single upload (`POST /api/share`).
A WhatsApp selection works the same way: its texts arrive as text, its photos as files in the agent's
`inbox/`. If the agent is asking a question or has an unsent draft, the message waits and goes in
right after.

The extension reads the Office address the app saved, through an App Group
(`group.com.pyinv.office`), so open the app once before sharing. App Groups need a paid developer
team; with a free Apple ID, build without the extension (remove `OfficeShare` from `project.yml`).
