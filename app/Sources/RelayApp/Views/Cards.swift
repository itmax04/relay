import RelayKit
import SwiftUI

struct SessionCard: View {
    @Environment(AppModel.self) private var model
    let session: Session
    var compact = false

    var body: some View {
        VStack(alignment: .leading, spacing: Theme.Space.s) {
            HStack(alignment: .top) {
                SessionHeading(session: session)
                Spacer()
                ModelChips(model: session.model, effort: session.effort)
            }
            HStack {
                Text("Context").foregroundStyle(Theme.Colors.secondaryText)
                Spacer()
                Text(verbatim: "\(Tokens.short(session.context)) / \(Tokens.short(session.window))")
                    .font(compact ? Theme.Fonts.body.monospacedDigit() : Theme.Fonts.metric)
            }
            ContextBar(context: session.context, window: session.window, soft: model.soft, hard: model.hard,
                       showLabels: !compact)
            if !compact, let history = session.contextHistory, history.count > 1 {
                Sparkline(values: history, soft: model.soft, hard: model.hard)
            }
            HStack(spacing: Theme.Space.m) {
                cacheLine
                Spacer()
                if !compact {
                    if let calls = session.calls {
                        Text("\(calls) calls").foregroundStyle(Theme.Colors.secondaryText)
                    }
                    if let failures = session.failures, failures > 0 {
                        Label("\(failures) failed tools", systemImage: Theme.Symbols.warning)
                            .foregroundStyle(Theme.Colors.caution)
                    }
                    Button("New session…") { model.openContinue(session: session, advice: nil) }
                        .controlSize(.small)
                        .help(Text("Continue in a new session…"))
                }
            }
            .font(Theme.Fonts.caption)
            .lineLimit(1)
        }
        .cardStyle()
    }

    @ViewBuilder private var cacheLine: some View {
        if let left = model.cacheRemaining(session) {
            if left > 0 {
                Label("cache expires in \(Durations.short(left))", systemImage: Theme.Symbols.cache)
                    .foregroundStyle(left < (model.config?.effective.cacheWarnSeconds ?? 300)
                                     ? Theme.Colors.caution : Theme.Colors.secondaryText)
            } else {
                Label("cache expired \(Durations.short(-left)) ago", systemImage: Theme.Symbols.cache)
                    .foregroundStyle(session.context >= model.soft ? Theme.Colors.caution : Theme.Colors.secondaryText)
            }
        }
    }
}

/// One advice item with its action and Useful / Wrong feedback.
struct AdviceCard: View {
    @Environment(AppModel.self) private var model
    let advice: Advice
    var compact = false
    @State private var copied = false

    var body: some View {
        HStack(alignment: .top, spacing: Theme.Space.m) {
            SeverityIcon(severity: advice.severity)
            VStack(alignment: .leading, spacing: Theme.Space.s) {
                HStack(alignment: .firstTextBaseline) {
                    Text(verbatim: advice.title).font(Theme.Fonts.cardTitle)
                    Spacer()
                    if !compact, let date = advice.date {
                        Text(date, format: .dateTime.weekday(.abbreviated).hour().minute())
                            .font(Theme.Fonts.caption)
                            .foregroundStyle(Theme.Colors.secondaryText)
                    }
                }
                Text(verbatim: advice.reason)
                    .foregroundStyle(Theme.Colors.secondaryText)
                    .fixedSize(horizontal: false, vertical: true)
                if !compact { sourceChips }
                // Full labels always: side by side when they fit, else the action on its own row.
                ViewThatFits(in: .horizontal) {
                    HStack(spacing: Theme.Space.s) {
                        actionButton
                        Spacer()
                        secondaryControls
                    }
                    VStack(alignment: .leading, spacing: Theme.Space.s) {
                        actionButton
                        secondaryControls
                    }
                }
            }
        }
        .cardStyle(accent: Theme.Colors.severity(advice.severity))
    }

    @ViewBuilder private var secondaryControls: some View {
        if compact {
            Button("Dismiss") { model.dismiss(advice) }
        } else {
            feedback
        }
    }

    private var sourceChips: some View {
        HStack(spacing: Theme.Space.xs) {
            Chip(text: "\(advice.agent.flatMap(Agent.init(rawValue:))?.displayName ?? "-") · \(advice.sessionTitle ?? advice.project ?? "-")",
                 symbol: Theme.Symbols.session)
            if let label = advice.advisorLabel {
                Chip(text: "Advisor · \(label)", symbol: Theme.Symbols.advisor)
            } else {
                Chip(text: String(localized: "Rule"), symbol: Theme.Symbols.rule)
            }
            if let s = advice.suggested {
                Chip(text: [s.model.map(ModelName.display), s.effort].compactMap { $0 }.joined(separator: " · "))
            }
        }
    }

    @ViewBuilder private var actionButton: some View {
        switch AdviceAction.of(advice) {
        case .continueInNewSession:
            Button {
                model.openContinue(session: nil, advice: advice)
            } label: {
                Label("Continue in a new session…", systemImage: Theme.Symbols.handoff)
            }
            .buttonStyle(.borderedProminent)
        case .copyModelCommand(let command):
            Button {
                Clipboard.copy(command)
                copied = true
            } label: {
                Label(copied ? "Copied" : "Copy \(command)", systemImage: Theme.Symbols.copy)
            }
            .buttonStyle(.borderedProminent)
        case .switchInCodex(let slug, let effort):
            Button {
                Clipboard.copy(slug)
                copied = true
            } label: {
                Label(copied ? "Copied" : "Copy model name", systemImage: Theme.Symbols.copy)
            }
            .help(Text("Pick \(slug)\(effort.map { " · \($0)" } ?? "") in Codex's model picker."))
        case .none:
            EmptyView()
        }
    }

    private var feedback: some View {
        HStack(spacing: Theme.Space.xs) {
            Toggle(isOn: outcomeBinding(.useful)) {
                Label("Useful", systemImage: Theme.Symbols.useful)
            }
            Toggle(isOn: outcomeBinding(.wrong)) {
                Label("Wrong", systemImage: Theme.Symbols.wrong)
            }
        }
        .toggleStyle(.button)
    }

    /// Feedback is written back to the engine's advice log; clicking the active one clears it.
    private func outcomeBinding(_ value: Outcome) -> Binding<Bool> {
        Binding(get: { advice.outcome == value },
                set: { on in Task { await model.setOutcome(advice, on ? value : nil) } })
    }
}
