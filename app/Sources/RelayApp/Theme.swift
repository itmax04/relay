import RelayKit
import SwiftUI

/// Every visual value of the app. Screens use only these names, so a later design can be applied
/// here without touching them. Current values are plain macOS system styling (placeholder).
enum Theme {
    enum Space {
        static let xs: CGFloat = 4
        static let s: CGFloat = 8
        static let m: CGFloat = 12
        static let l: CGFloat = 16
        static let xl: CGFloat = 24
    }

    enum Radius {
        static let card: CGFloat = 10
        static let chip: CGFloat = 6
        static let bar: CGFloat = 3
    }

    enum Size {
        static let popoverWidth: CGFloat = 360
        static let windowMin = CGSize(width: 820, height: 560)
        static let sidebarWidth: CGFloat = 190
        static let contextBarHeight: CGFloat = 6
        static let markerHeight: CGFloat = 12
        static let ringSmall: CGFloat = 44
        static let ringLarge: CGFloat = 96
        static let ringLine: CGFloat = 5
        static let ringLineLarge: CGFloat = 9
        static let sparklineHeight: CGFloat = 28
        static let severityIcon: CGFloat = 22
        static let sheetWidth: CGFloat = 560
    }

    enum Fonts {
        static let screenTitle = Font.largeTitle.weight(.semibold)
        static let screenSubtitle = Font.callout
        static let popoverTitle = Font.title2.weight(.bold)
        static let sheetTitle = Font.title.weight(.semibold)
        static let sectionTitle = Font.headline
        static let cardTitle = Font.headline
        static let body = Font.body
        static let caption = Font.caption
        static let metric = Font.system(.title3, design: .rounded).weight(.semibold).monospacedDigit()
        static let metricLarge = Font.system(.title, design: .rounded).weight(.semibold).monospacedDigit()
        static let ringLabel = Font.system(.caption, design: .rounded).weight(.semibold).monospacedDigit()
        static let ringLabelLarge = Font.system(.title2, design: .rounded).weight(.semibold).monospacedDigit()
        static let code = Font.system(.body, design: .monospaced)
        static let chip = Font.caption.weight(.medium)
    }

    enum Colors {
        static let cardBackground = Color(nsColor: .controlBackgroundColor)
        static let cardBorder = Color(nsColor: .separatorColor)
        static let track = Color(nsColor: .quaternaryLabelColor)
        static let marker = Color(nsColor: .secondaryLabelColor)
        static let chipBackground = Color(nsColor: .quaternaryLabelColor).opacity(0.5)
        static let secondaryText = Color.secondary
        static let codeBackground = Color(nsColor: .textBackgroundColor)
        static let ok = Color(nsColor: .systemGreen)
        static let caution = Color(nsColor: .systemOrange)
        static let danger = Color(nsColor: .systemRed)
        static let neutral = Color(nsColor: .systemBlue)
        static let stale = Color(nsColor: .tertiaryLabelColor)
        static let chartClaude = Color(nsColor: .systemOrange)
        static let chartCodex = Color(nsColor: .systemTeal)

        static func run(_ s: RunStatus) -> Color {
            switch s {
            case .running: neutral
            case .waiting: caution
            case .done: ok
            case .failed: danger
            case .stopped: stale
            }
        }

        static func step(_ status: String) -> Color {
            switch status {
            case "done": ok
            case "running": neutral
            case "waiting": caution
            case "failed": danger
            default: stale
            }
        }

        static func severity(_ s: Severity) -> Color {
            switch s {
            case .info: neutral
            case .warn: caution
            case .high: danger
            }
        }

        /// Context fill: below soft limit, between soft and hard, above hard.
        static func context(_ tokens: Int, soft: Int, hard: Int) -> Color {
            tokens >= hard ? danger : tokens >= soft ? caution : ok
        }

        static func quota(_ percent: Double, warn: Int, high: Int) -> Color {
            percent >= Double(high) ? danger : percent >= Double(warn) ? caution : ok
        }

        static func agent(_ agent: String?) -> Color { agent == "codex" ? chartCodex : chartClaude }
    }

    enum Symbols {
        static let menuIdle = "arrow.triangle.branch"
        static let menuDown = "bolt.horizontal.circle"
        static let now = "gauge.with.dots.needle.50percent"
        static let suggestions = "lightbulb"
        static let quotas = "chart.pie"
        static let report = "chart.bar.xaxis"
        static let settings = "gearshape"
        static let session = "terminal"
        static let handoff = "doc.text"
        static let copy = "doc.on.doc"
        static let useful = "hand.thumbsup"
        static let wrong = "hand.thumbsdown"
        static let advisor = "sparkles"
        static let rule = "list.bullet.rectangle"
        static let engineDown = "bolt.slash"
        static let openWindow = "macwindow"
        static let cache = "clock.arrow.circlepath"
        static let key = "key"
        static let ok = "checkmark.circle.fill"
        static let missing = "xmark.circle"
        static let warning = "exclamationmark.triangle.fill"
        static let noData = "questionmark"
        static let runs = "list.number"
        static let stop = "stop.circle"
        static let approve = "checkmark.seal"
        static let resume = "arrow.clockwise.circle"
        static let branch = "arrow.triangle.branch"

        /// Step status from relay/dispatch.py: pending, waiting, running, done, failed.
        static func step(_ status: String) -> String {
            switch status {
            case "done": "checkmark.circle.fill"
            case "running": "circle.dotted.circle"
            case "waiting": "pause.circle"
            case "failed": "xmark.circle.fill"
            default: "circle"
            }
        }

        static func severity(_ s: Severity?) -> String {
            switch s {
            case nil: menuIdle
            case .info: "info.circle"
            case .warn: "exclamationmark.triangle"
            case .high: "exclamationmark.octagon.fill"
            }
        }
    }
}

extension View {
    /// Card container used by session and advice cards.
    func cardStyle(accent: Color? = nil) -> some View {
        padding(Theme.Space.m)
            .background(Theme.Colors.cardBackground, in: RoundedRectangle(cornerRadius: Theme.Radius.card))
            .overlay(alignment: .leading) {
                if let accent {
                    UnevenRoundedRectangle(topLeadingRadius: Theme.Radius.card, bottomLeadingRadius: Theme.Radius.card)
                        .fill(accent)
                        .frame(width: 3)
                }
            }
            .overlay(RoundedRectangle(cornerRadius: Theme.Radius.card).strokeBorder(Theme.Colors.cardBorder))
    }
}
