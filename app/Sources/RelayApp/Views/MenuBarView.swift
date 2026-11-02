import RelayKit
import SwiftUI

struct MenuBarView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        VStack(alignment: .leading, spacing: Theme.Space.m) {
            header
            Divider()
            if model.engine == .up {
                sessionsSection
                if let top = model.openAdvice.first {
                    AdviceCard(advice: top, compact: true)
                }
            } else {
                EngineBanner()
            }
            Divider()
            footer
        }
        .padding(Theme.Space.l)
        .frame(width: Theme.Size.popoverWidth)
        // The sheet lives in the main window: a sheet on the popover would close with it.
        .onChange(of: model.continueTarget?.id) { _, id in
            if id != nil { openMain() }
        }
    }

    private var header: some View {
        HStack {
            Text(verbatim: "relay").font(Theme.Fonts.popoverTitle)
            let count = model.openAdvice.count
            if count > 0 {
                Chip(text: String(localized: "\(count) suggestions"), symbol: Theme.Symbols.severity(model.topSeverity))
                    .foregroundStyle(Theme.Colors.severity(model.topSeverity ?? .info))
            }
            Spacer()
            Menu {
                Button("Open relay") { openMain() }
                Button("Settings…") { openMain(); NotificationCenter.default.post(name: .showSettings, object: nil) }
                Divider()
                Button("Quit relay") { NSApp.terminate(nil) }
            } label: {
                Image(systemName: "ellipsis.circle")
            }
            .menuStyle(.borderlessButton)
            .menuIndicator(.hidden)
            .fixedSize()
        }
    }

    private var sessionsSection: some View {
        VStack(alignment: .leading, spacing: Theme.Space.s) {
            HStack {
                Text("Active sessions").font(Theme.Fonts.sectionTitle)
                Spacer()
                Text("\(model.sessions.count) sessions")
                    .foregroundStyle(Theme.Colors.secondaryText)
            }
            if model.sessions.isEmpty {
                Text("No active Claude Code or Codex sessions.")
                    .foregroundStyle(Theme.Colors.secondaryText)
            }
            ScrollView {
                VStack(spacing: Theme.Space.s) {
                    ForEach(model.sessions.prefix(4), id: \.key) { s in
                        SessionCard(session: s, compact: true)
                    }
                }
            }
            .frame(maxHeight: 330)
            .fixedSize(horizontal: false, vertical: true)
        }
    }

    private var footer: some View {
        HStack(spacing: Theme.Space.m) {
            ringItem(.claude, model.quota.claudeWindows.week)
            ringItem(.codex, model.quota.codexWindows.week)
            Spacer()
            Button {
                openMain()
            } label: {
                Label("Open relay", systemImage: Theme.Symbols.openWindow)
            }
        }
    }

    private func ringItem(_ agent: Agent, _ week: QuotaWindow?) -> some View {
        HStack(spacing: Theme.Space.s) {
            QuotaRing(percent: week?.usedPercent, stale: week?.isStale(now: model.now) ?? false,
                      warn: model.quotaWarn, high: model.quotaHigh)
            VStack(alignment: .leading) {
                Text(verbatim: agent.displayName)
                Text("week").foregroundStyle(Theme.Colors.secondaryText)
            }
            .font(Theme.Fonts.caption)
        }
    }

    private func openMain() {
        openWindow(id: "main")
        WindowRouting.bringToFront()
    }
}

extension Notification.Name {
    static let showSettings = Notification.Name("relay.showSettings")
}
