// Prints "<window id> <owner> <title>" for on-screen windows of the relay app (used by
// scripts/screenshot.sh with `screencapture -l`).
import CoreGraphics
let list = CGWindowListCopyWindowInfo([.optionOnScreenOnly], kCGNullWindowID) as? [[String: Any]] ?? []
for w in list where (w[kCGWindowOwnerName as String] as? String) == "Relay" || (w[kCGWindowOwnerName as String] as? String) == "relay" {
    let layer = w[kCGWindowLayer as String] as? Int ?? 0
    print(w[kCGWindowNumber as String] ?? 0, layer, w[kCGWindowName as String] ?? "")
}
