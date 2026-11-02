import RelayKit
import SwiftUI

struct Chip: View {
    let text: String
    var symbol: String?

    var body: some View {
        HStack(spacing: Theme.Space.xs) {
            if let symbol { Image(systemName: symbol) }
            Text(verbatim: text)
        }
        .font(Theme.Fonts.chip)
        .lineLimit(1)
        .fixedSize()
        .padding(.horizontal, Theme.Space.s)
        .padding(.vertical, 2)
        .background(Theme.Colors.chipBackground, in: RoundedRectangle(cornerRadius: Theme.Radius.chip))
    }
}

/// Context use against the window, with soft and hard limit markers.
struct ContextBar: View {
    let context: Int
    let window: Int
    let soft: Int
    let hard: Int
    var showLabels = true

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            if showLabels {
                GeometryReader { geo in
                    ForEach([soft, hard], id: \.self) { mark in
                        Text(verbatim: Tokens.short(mark))
                            .font(Theme.Fonts.caption)
                            .foregroundStyle(Theme.Colors.secondaryText)
                            .fixedSize()
                            .position(x: x(mark, geo.size.width), y: 6)
                    }
                }
                .frame(height: 12)
            }
            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    RoundedRectangle(cornerRadius: Theme.Radius.bar).fill(Theme.Colors.track)
                    RoundedRectangle(cornerRadius: Theme.Radius.bar)
                        .fill(Theme.Colors.context(context, soft: soft, hard: hard))
                        .frame(width: max(x(context, geo.size.width), 2))
                    ForEach([soft, hard], id: \.self) { mark in
                        Rectangle()
                            .fill(Theme.Colors.marker)
                            .frame(width: 1, height: Theme.Size.markerHeight)
                            .position(x: x(mark, geo.size.width), y: geo.size.height / 2)
                    }
                }
            }
            .frame(height: Theme.Size.contextBarHeight)
            .padding(.vertical, (Theme.Size.markerHeight - Theme.Size.contextBarHeight) / 2)
        }
        .accessibilityElement()
        .accessibilityLabel(Text("Context \(Tokens.short(context)) of \(Tokens.short(window))"))
    }

    private func x(_ tokens: Int, _ width: CGFloat) -> CGFloat {
        min(CGFloat(tokens) / CGFloat(max(window, 1)), 1) * width
    }
}

/// Context per model call, with the soft limit as a dashed line.
struct Sparkline: View {
    let values: [Int]
    let soft: Int
    let hard: Int

    var body: some View {
        GeometryReader { geo in
            let top = CGFloat(max(values.max() ?? 0, soft) ) * 1.1
            let w = geo.size.width, h = geo.size.height
            let step = values.count > 1 ? w / CGFloat(values.count - 1) : 0
            ZStack {
                Path { p in
                    let y = h - CGFloat(soft) / top * h
                    p.move(to: CGPoint(x: 0, y: y))
                    p.addLine(to: CGPoint(x: w, y: y))
                }
                .stroke(Theme.Colors.marker, style: StrokeStyle(lineWidth: 0.5, dash: [3, 3]))
                Path { p in
                    for (i, v) in values.enumerated() {
                        let pt = CGPoint(x: CGFloat(i) * step, y: h - CGFloat(v) / top * h)
                        i == 0 ? p.move(to: pt) : p.addLine(to: pt)
                    }
                }
                .stroke(Theme.Colors.context(values.last ?? 0, soft: soft, hard: hard), lineWidth: 1.5)
            }
        }
        .frame(height: Theme.Size.sparklineHeight)
        .accessibilityLabel(Text("Context per model call"))
    }
}

struct QuotaRing: View {
    let percent: Double?
    var stale = false
    var warn = 75
    var high = 90
    var large = false

    var body: some View {
        let size = large ? Theme.Size.ringLarge : Theme.Size.ringSmall
        let line = large ? Theme.Size.ringLineLarge : Theme.Size.ringLine
        let color = stale || percent == nil ? Theme.Colors.stale : Theme.Colors.quota(percent!, warn: warn, high: high)
        ZStack {
            Circle().stroke(Theme.Colors.track, lineWidth: line)
            Circle()
                .trim(from: 0, to: min((percent ?? 0) / 100, 1))
                .stroke(color, style: StrokeStyle(lineWidth: line, lineCap: .round))
                .rotationEffect(.degrees(-90))
            Text(verbatim: percent.map { "\(Int($0.rounded()))%" } ?? "–")
                .font(large ? Theme.Fonts.ringLabelLarge : Theme.Fonts.ringLabel)
                .foregroundStyle(stale ? Theme.Colors.stale : Color.primary)
        }
        .frame(width: size, height: size)
    }
}

struct SeverityIcon: View {
    let severity: Severity

    var body: some View {
        Image(systemName: Theme.Symbols.severity(severity))
            .foregroundStyle(Theme.Colors.severity(severity))
            .font(.system(size: Theme.Size.severityIcon * 0.7))
            .frame(width: Theme.Size.severityIcon, height: Theme.Size.severityIcon)
    }
}

/// "Claude · project" heading with the session title underneath.
struct SessionHeading: View {
    let session: Session

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(verbatim: "\(session.agentKind?.displayName ?? session.agent) · \(session.project ?? "?")")
                .font(Theme.Fonts.cardTitle)
                .lineLimit(1)
            if let title = session.title, !title.isEmpty {
                Text(verbatim: title)
                    .font(Theme.Fonts.caption)
                    .foregroundStyle(Theme.Colors.secondaryText)
                    .lineLimit(1)
            }
        }
    }
}

struct ModelChips: View {
    let model: String?
    let effort: String?

    var body: some View {
        HStack(spacing: Theme.Space.xs) {
            if let model { Chip(text: model) }
            if let effort { Chip(text: effort) }
        }
    }
}

struct EngineBanner: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        switch model.engine {
        case .up:
            EmptyView()
        case .connecting:
            Label("Connecting to relay watch…", systemImage: Theme.Symbols.engineDown)
                .foregroundStyle(Theme.Colors.secondaryText)
        case .starting:
            HStack {
                ProgressView().controlSize(.small)
                Text("Starting relay watch…")
            }
        case .down(let reason):
            VStack(alignment: .leading, spacing: Theme.Space.s) {
                Label("Engine is not running", systemImage: Theme.Symbols.engineDown)
                    .font(Theme.Fonts.cardTitle)
                    .foregroundStyle(Theme.Colors.danger)
                Text(verbatim: reason)
                    .font(Theme.Fonts.caption)
                    .foregroundStyle(Theme.Colors.secondaryText)
                    .textSelection(.enabled)
                Text("Advice, sessions and quotas come from relay watch on port 8765.")
                    .font(Theme.Fonts.caption)
                    .foregroundStyle(Theme.Colors.secondaryText)
                Button("Start relay watch") { Task { _ = await model.startEngine() } }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .cardStyle(accent: Theme.Colors.danger)
        }
    }
}

extension Date {
    /// "in 12 min" / "3 h ago", in the user's locale.
    var relative: String {
        let f = RelativeDateTimeFormatter()
        f.unitsStyle = .short
        return f.localizedString(for: self, relativeTo: Date())
    }
}

enum Durations {
    static func short(_ seconds: Int) -> String {
        let f = DateComponentsFormatter()
        f.unitsStyle = .abbreviated
        f.allowedUnits = seconds >= 3600 ? [.hour, .minute] : [.minute]
        f.maximumUnitCount = 2
        return f.string(from: TimeInterval(max(seconds, 0))) ?? "\(seconds / 60) min"
    }
}
