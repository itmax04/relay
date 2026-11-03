import RelayKit
import SwiftUI

enum Screen: String, CaseIterable, Identifiable {
    case now, suggestions, runs, quotas, report, settings
    var id: String { rawValue }

    var title: LocalizedStringKey {
        switch self {
        case .now: "Now"
        case .suggestions: "Suggestions"
        case .runs: "Runs"
        case .quotas: "Quotas"
        case .report: "Report"
        case .settings: "Settings"
        }
    }

    var symbol: String {
        switch self {
        case .now: Theme.Symbols.now
        case .suggestions: Theme.Symbols.suggestions
        case .runs: Theme.Symbols.runs
        case .quotas: Theme.Symbols.quotas
        case .report: Theme.Symbols.report
        case .settings: Theme.Symbols.settings
        }
    }
}

struct MainWindow: View {
    @Environment(AppModel.self) private var model
    @SceneStorage("screen") private var screen: Screen = .now

    var body: some View {
        // Read through @Bindable so the sheet tracks continueTarget; a Binding(get:) closure is
        // not observed and the sheet would never open.
        @Bindable var model = model
        NavigationSplitView {
            List(selection: Binding(get: { Optional(screen) }, set: { screen = $0 ?? .now })) {
                ForEach(Screen.allCases) { s in
                    Label(s.title, systemImage: s.symbol)
                        .badge(s == .suggestions ? model.openAdvice.count : s == .runs ? model.activeRuns.count : 0)
                        .tag(s)
                }
            }
            .navigationSplitViewColumnWidth(Theme.Size.sidebarWidth)
            .safeAreaInset(edge: .bottom) { engineStatus }
        } detail: {
            detail
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        }
        .task {
            model.start()
            if let launch = UserDefaults.standard.string(forKey: "launchScreen").flatMap(Screen.init(rawValue:)) {
                screen = launch
                UserDefaults.standard.removeObject(forKey: "launchScreen")
            }
            // `--continue [cwd-part]`: open the Continue sheet for the newest session, or the newest
            // one whose cwd contains cwd-part (screenshots, checks).
            let args = CommandLine.arguments
            if let i = args.firstIndex(of: "--continue") {
                let part = i + 1 < args.count && !args[i + 1].hasPrefix("--") ? args[i + 1] : nil
                for _ in 0..<60 where model.sessions.isEmpty { try? await Task.sleep(for: .milliseconds(250)) }
                let s = model.sessions.first { part == nil || $0.cwd?.contains(part!) == true }
                model.openContinue(session: s, advice: s.flatMap { model.openAdvice(for: $0).first })
            }
        }
        .onReceive(NotificationCenter.default.publisher(for: .showSettings)) { _ in screen = .settings }
        .sheet(item: $model.continueTarget) { target in
            ContinueSheet(target: target)
                .environment(model)
        }
    }

    @ViewBuilder private var detail: some View {
        switch screen {
        case .now: NowView()
        case .suggestions: SuggestionsView()
        case .runs: RunsView()
        case .quotas: QuotasView()
        case .report: ReportView()
        case .settings: SettingsView()
        }
    }

    private var engineStatus: some View {
        HStack(spacing: Theme.Space.s) {
            Circle()
                .fill(model.engine == .up ? Theme.Colors.ok : Theme.Colors.danger)
                .frame(width: 7, height: 7)
            Text(model.engine == .up ? "Engine running" : "Engine down")
                .font(Theme.Fonts.caption)
                .foregroundStyle(Theme.Colors.secondaryText)
            Spacer()
        }
        .padding(Theme.Space.m)
    }
}

/// Title and subtitle at the top of each screen, inside a scroll view.
struct ScreenScaffold<Content: View, Accessory: View>: View {
    let title: LocalizedStringKey
    let subtitle: LocalizedStringKey
    @ViewBuilder var accessory: Accessory
    @ViewBuilder var content: Content

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Theme.Space.l) {
                HStack(alignment: .bottom) {
                    VStack(alignment: .leading, spacing: Theme.Space.xs) {
                        Text(title).font(Theme.Fonts.screenTitle)
                        Text(subtitle).font(Theme.Fonts.screenSubtitle).foregroundStyle(Theme.Colors.secondaryText)
                    }
                    Spacer()
                    accessory
                }
                EngineBanner()
                content
            }
            .padding(Theme.Space.xl)
        }
    }
}

extension ScreenScaffold where Accessory == EmptyView {
    init(title: LocalizedStringKey, subtitle: LocalizedStringKey, @ViewBuilder content: () -> Content) {
        self.title = title
        self.subtitle = subtitle
        self.accessory = EmptyView()
        self.content = content()
    }
}

struct NowView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        ScreenScaffold(title: "Now", subtitle: "Live Claude Code and Codex sessions on this Mac.") {
            if model.engine == .up && model.sessions.isEmpty {
                ContentUnavailableView("No active sessions", systemImage: Theme.Symbols.session,
                                       description: Text("Sessions with activity in the last \(Int(model.config?.effective.watchActiveHours ?? 12)) hours appear here."))
            }
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 360), spacing: Theme.Space.m)], spacing: Theme.Space.m) {
                ForEach(model.sessions, id: \.key) { s in
                    VStack(alignment: .leading, spacing: Theme.Space.s) {
                        SessionCard(session: s)
                        ForEach(model.openAdvice(for: s).prefix(1)) { a in
                            AdviceCard(advice: a, compact: true)
                        }
                    }
                }
            }
        }
    }
}

struct SuggestionsView: View {
    @Environment(AppModel.self) private var model
    @State private var filter: Filter = .all
    @State private var showJudged = true

    enum Filter: String, CaseIterable, Identifiable {
        case all, claude, codex, rules, advisor
        var id: String { rawValue }
        var title: LocalizedStringKey {
            switch self {
            case .all: "All"
            case .claude: "Claude"
            case .codex: "Codex"
            case .rules: "Rules"
            case .advisor: "Advisor"
            }
        }

        func matches(_ a: Advice) -> Bool {
            switch self {
            case .all: true
            case .claude: a.agent == "claude"
            case .codex: a.agent == "codex"
            case .rules: !a.isFromAdvisor
            case .advisor: a.isFromAdvisor
            }
        }
    }

    var body: some View {
        let items = model.advice.filter { filter.matches($0) && (showJudged || $0.outcome == nil) }
        ScreenScaffold(title: "Suggestions",
                       subtitle: "Advice to save context and quota. Mark it Useful or Wrong to teach the advisor.") {
            HStack {
                Picker("Filter", selection: $filter) {
                    ForEach(Filter.allCases) { Text($0.title).tag($0) }
                }
                .pickerStyle(.segmented)
                .labelsHidden()
                .fixedSize()
                Spacer()
                Toggle("Show judged", isOn: $showJudged).fixedSize()
            }
        } content: {
            if items.isEmpty && model.engine == .up {
                ContentUnavailableView("No suggestions", systemImage: Theme.Symbols.suggestions,
                                       description: Text("relay watch has nothing to advise right now."))
            }
            LazyVStack(spacing: Theme.Space.m) {
                ForEach(items) { AdviceCard(advice: $0) }
            }
        }
    }
}
