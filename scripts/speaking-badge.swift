// Menu-bar indicator for claude-speaker.
//
// Shows what the voice is doing in the top bar — which terminal is
// speaking, or how many summaries are waiting — so the state is visible
// even when every terminal is buried behind another app.
//
// All the logic lives in speak-response.py; this is just a display. The
// hook writes a line of text to ~/.claude/tts-badge.txt and removes it
// when there is nothing to show. This polls that file, mirrors it into a
// status item, and quits when the file goes away, so no daemon lingers.
//
// Built by install.sh: swiftc -O -o ~/.claude/scripts/speaking-badge
// It is a plain executable, not an app bundle — .accessory activation
// policy is what lets it own a status item with no Dock icon, and it
// never becomes active, so it cannot take focus from anything.

import AppKit
import Foundation

let badgeFile = ("~/.claude/tts-badge.txt" as NSString).expandingTildeInPath
let maxLifetime: TimeInterval = 3600  // backstop: never linger past an hour

func currentText() -> String? {
    guard let raw = try? String(contentsOfFile: badgeFile, encoding: .utf8)
    else { return nil }
    let text = raw.trimmingCharacters(in: .whitespacesAndNewlines)
    return text.isEmpty ? nil : text
}

guard let initial = currentText() else { exit(0) }

let app = NSApplication.shared
app.setActivationPolicy(.accessory)

let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
item.button?.title = initial

Timer.scheduledTimer(withTimeInterval: 0.5, repeats: true) { _ in
    guard let text = currentText() else {
        NSApp.terminate(nil)
        return
    }
    if item.button?.title != text {
        item.button?.title = text
    }
}
Timer.scheduledTimer(withTimeInterval: maxLifetime, repeats: false) { _ in
    NSApp.terminate(nil)
}

app.run()
