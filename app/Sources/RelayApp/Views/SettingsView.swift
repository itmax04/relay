import RelayKit
import SwiftUI

struct SettingsView: View {
    @Environment(AppModel.self) private var model
    @State private var draft: RelayConfig?
    @State private var saved = false
    @State private var newAccount = ""
    @State private var newKey = ""
    @State private var keyError: String?

    var body: some View {
        Form {
            engineSection
            if model.engine == .up, let cfg = model.config {
                limitsSection(cfg)
                advisorSection(cfg)
                dataSection(cfg)
                agentsSection
            }
            if let error = model.lastError {
                Section { Text(verbatim: error).foregroundStyle(Theme.Colors.danger).textSelection(.enabled) }
            }
        }
        .formStyle(.grouped)
        .onAppear { draft = model.config?.effective }
        .onChange(of: model.config) { _, c in if draft == nil { draft = c?.effective } }
    }

    // MARK: Engine

    private var engineSection: some View {
        @Bindable var model = model
        return Section("Engine") {
            LabeledContent("relay watch") {
                switch model.engine {
                case .up: Text("Running (pid \(String(model.health?.pid ?? 0)), port \(String(model.health?.port ?? 8765)))")
                case .starting: Text("Starting…")
                case .connecting: Text("Connecting…")
                case .down: Text("Not running").foregroundStyle(Theme.Colors.danger)
                }
            }
            TextField("Repository", text: $model.repoPath)
            Toggle("Start relay watch when the app starts", isOn: $model.autoStart)
            HStack {
                if model.engine == .up {
                    Button("Restart engine") { Task { await model.restartEngine() } }
                } else {
                    Button("Start relay watch") { Task { _ = await model.startEngine() } }
                }
                Spacer()
                Text(verbatim: EngineLauncher.logPath)
                    .font(Theme.Fonts.caption)
                    .foregroundStyle(Theme.Colors.secondaryText)
                    .textSelection(.enabled)
            }
        }
    }

    // MARK: Limits

    private func limitsSection(_ cfg: ConfigView) -> some View {
        Section {
            if let binding = Binding($draft) {
                tokenField("Soft limit", value: binding.softLimit, help: "Context at which relay asks for a handoff.")
                tokenField("Hard limit", value: binding.hardLimit, help: "Context at which relay says: stop starting new work here.")
                tokenField("Big context", value: binding.bigContext, help: "Context above which cache expiry and LLM reviews matter.")
                TextField("Handoff path", text: binding.handoffPath)
                Stepper(value: binding.watchActiveHours, in: 1...72, step: 1) {
                    LabeledContent("Active sessions: last", value: "\(Int(binding.wrappedValue.watchActiveHours)) h")
                }
                Stepper(value: binding.quotaWarn, in: 10...100, step: 5) {
                    LabeledContent("Quota warning at", value: "\(binding.wrappedValue.quotaWarn)%")
                }
                Stepper(value: binding.quotaHigh, in: 10...100, step: 5) {
                    LabeledContent("Quota alert at", value: "\(binding.wrappedValue.quotaHigh)%")
                }
                Toggle("macOS notifications for high-severity advice", isOn: binding.notify)
                HStack {
                    Text(verbatim: cfg.path).font(Theme.Fonts.caption).foregroundStyle(Theme.Colors.secondaryText)
                    Spacer()
                    if saved { Text("Saved").foregroundStyle(Theme.Colors.ok) }
                    Button("Revert") { draft = cfg.effective }
                        .disabled(draft == cfg.effective)
                    Button("Save") { Task { await saveLimits(cfg) } }
                        .keyboardShortcut("s")
                        .disabled(draft == cfg.effective)
                }
            }
        } header: {
            Text("Limits")
        } footer: {
            Text("Saved by the engine to ~/.config/relay/config.json and applied without a restart.")
        }
    }

    private func tokenField(_ title: LocalizedStringKey, value: Binding<Int>, help: LocalizedStringKey) -> some View {
        LabeledContent {
            HStack {
                TextField(title, value: value, format: .number.grouping(.never))
                    .labelsHidden()
                    .multilineTextAlignment(.trailing)
                    .frame(width: 100)
                Text(verbatim: Tokens.short(value.wrappedValue))
                    .foregroundStyle(Theme.Colors.secondaryText)
                    .frame(width: 44, alignment: .leading)
            }
        } label: {
            Text(title)
            Text(help)
        }
    }

    /// Sends only the values that differ from the current effective settings.
    private func saveLimits(_ cfg: ConfigView) async {
        guard let d = draft else { return }
        let e = cfg.effective
        var patch: [String: JSONValue] = [:]
        func put(_ key: String, _ new: Int, _ old: Int) { if new != old { patch[key] = .number(Double(new)) } }
        put("soft_limit", d.softLimit, e.softLimit)
        put("hard_limit", d.hardLimit, e.hardLimit)
        put("big_context", d.bigContext, e.bigContext)
        put("watch_active_hours", Int(d.watchActiveHours), Int(e.watchActiveHours))
        put("quota_warn", d.quotaWarn, e.quotaWarn)
        put("quota_high", d.quotaHigh, e.quotaHigh)
        if d.handoffPath != e.handoffPath { patch["handoff_path"] = .string(d.handoffPath) }
        if d.notify != e.notify { patch["notify"] = .bool(d.notify) }
        if await model.saveConfig(patch) {
            draft = model.config?.effective
            saved = true
            try? await Task.sleep(for: .seconds(2))
            saved = false
        }
    }

    // MARK: Advisor

    private func advisorSection(_ cfg: ConfigView) -> some View {
        Section {
            Toggle("LLM advisor (rules always run)", isOn: Binding(
                get: { cfg.effective.llm.enabled },
                set: { on in Task { await model.saveConfig(["llm": .object(["enabled": .bool(on)])]) } }))
            ForEach(model.health?.llm ?? [], id: \.entry) { e in
                HStack {
                    Image(systemName: chainSymbol(e))
                        .foregroundStyle(e.keysReady > 0 && !e.unavailable ? Theme.Colors.ok : Theme.Colors.caution)
                    VStack(alignment: .leading) {
                        Text(verbatim: e.entry)
                        if let error = e.lastError {
                            Text(verbatim: error).font(Theme.Fonts.caption).foregroundStyle(Theme.Colors.secondaryText)
                        }
                    }
                    Spacer()
                    Text(chainState(e)).foregroundStyle(Theme.Colors.secondaryText)
                }
            }
            if cfg.effective.llm.enabled && (model.health?.llm ?? []).isEmpty {
                Text("The chain is empty: no Groq key is loaded, or the engine was started with --no-llm.")
                    .foregroundStyle(Theme.Colors.secondaryText)
            }
            if let error = model.health?.llmLastError {
                Text("Last advisor error: \(error)").foregroundStyle(Theme.Colors.caution)
            }
            groqKeys(cfg)
        } header: {
            Text("Advisor")
        } footer: {
            Text("Advice only: relay never switches a model or effort for you.")
        }
    }

    private func chainSymbol(_ e: ChainEntry) -> String {
        e.keysReady > 0 && !e.unavailable ? Theme.Symbols.ok : Theme.Symbols.warning
    }

    private func chainState(_ e: ChainEntry) -> String {
        if e.unavailable { return String(localized: "model unavailable") }
        if e.keys == 0 { return String(localized: "no keys") }
        if e.coolingSeconds > 0 { return String(localized: "cooling \(Durations.short(e.coolingSeconds))") }
        return String(localized: "\(e.keysReady)/\(e.keys) keys ready")
    }

    @ViewBuilder private func groqKeys(_ cfg: ConfigView) -> some View {
        let listed = Set(cfg.effective.llm.groqKeychainAccounts)
        LabeledContent("Groq keys") {
            Text("Keychain service relay.groq")
        }
        ForEach(model.keyAccounts, id: \.self) { account in
            HStack {
                Label(account, systemImage: Theme.Symbols.key)
                if !listed.contains(account) {
                    Text("not used by the engine").font(Theme.Fonts.caption).foregroundStyle(Theme.Colors.caution)
                }
                Spacer()
                Button("Remove", role: .destructive) {
                    Task {
                        do { try await model.removeGroqKey(account: account) } catch { keyError = error.localizedDescription }
                    }
                }
            }
        }
        ForEach(cfg.effective.llm.groqKeychainAccounts.filter { !model.keyAccounts.contains($0) }, id: \.self) { account in
            Label("\(account): listed in config, no key in Keychain", systemImage: Theme.Symbols.missing)
                .foregroundStyle(Theme.Colors.secondaryText)
        }
        // One row per field: in a grouped form a TextField draws its title as the row label and the
        // input borderless on the right, so squeezing both into one HStack left no visible input.
        TextField("Account name", text: $newAccount, prompt: Text(verbatim: "default"))
        SecureField("Groq API key", text: $newKey, prompt: Text(verbatim: "gsk_…"))
        HStack {
            Spacer()
            Button("Add key") {
                Task {
                    do {
                        try await model.addGroqKey(account: newAccount, key: newKey)
                        newAccount = ""
                        newKey = ""
                        keyError = nil
                    } catch {
                        keyError = error.localizedDescription
                    }
                }
            }
            .disabled(newAccount.trimmingCharacters(in: .whitespaces).isEmpty || newKey.isEmpty)
        }
        if let keyError { Text(verbatim: keyError).foregroundStyle(Theme.Colors.danger) }
        Text("One Keychain item per key; the engine uses them in this order and moves to the next on a rate limit. Which keys to add or rotate is your decision.")
            .font(Theme.Fonts.caption)
            .foregroundStyle(Theme.Colors.secondaryText)
    }

    // MARK: Data sent to the advisor

    private func dataSection(_ cfg: ConfigView) -> some View {
        Section {
            ForEach(AdvisorData.allCases, id: \.self) { item in
                Toggle(isOn: Binding(
                    get: { cfg.effective.llm.sends(item.rawValue) },
                    set: { on in
                        var send = Dictionary(uniqueKeysWithValues: AdvisorData.allCases.map {
                            ($0.rawValue, JSONValue.bool(cfg.effective.llm.sends($0.rawValue)))
                        })
                        send[item.rawValue] = .bool(on)
                        Task { await model.saveConfig(["llm": .object(["send": .object(send)])]) }
                    })) {
                    Text(dataTitle(item))
                    Text(dataHelp(item))
                }
            }
        } header: {
            Text("Data sent to the advisor")
        } footer: {
            Text("Metrics (context, model, effort, quota, call counts) are always sent when the advisor is on.")
        }
    }

    private func dataTitle(_ item: AdvisorData) -> LocalizedStringKey {
        switch item {
        case .prompt: "Last prompt text"
        case .errors: "Error excerpts"
        case .files: "File names"
        case .tools: "Tool-call labels and large outputs"
        }
    }

    private func dataHelp(_ item: AdvisorData) -> LocalizedStringKey {
        switch item {
        case .prompt: "Up to 1,500 characters of your last message."
        case .errors: "Up to 300 characters of the last failed tool outputs."
        case .files: "Names of recently touched files."
        case .tools: "Commands and tools the agent ran, with token sizes."
        }
    }

    // MARK: Agents

    private var agentsSection: some View {
        Section {
            ForEach(model.agents, id: \.agent) { a in
                VStack(alignment: .leading, spacing: Theme.Space.xs) {
                    HStack {
                        Text(verbatim: Agent(rawValue: a.agent)?.displayName ?? a.agent).font(Theme.Fonts.cardTitle)
                        Spacer()
                        status(a.hooksInstalled, "Hooks")
                        status(a.skillLinked, "Handoff skill")
                        if let plan = a.planSkillLinked { status(plan, "Plan skill") }
                        if let rule = a.ruleInstalled { status(rule, "Stage rule") }
                        if let line = a.statusline { status(line, "Status line") }
                    }
                    Text(verbatim: a.config).font(Theme.Fonts.caption).foregroundStyle(Theme.Colors.secondaryText)
                    if !a.hooksInstalled || !a.skillLinked || a.planSkillLinked == false || a.ruleInstalled == false {
                        let command = "\(EngineLauncher.script(repo: model.repoPath)) install \(a.agent)"
                        HStack {
                            Text(verbatim: command).font(Theme.Fonts.code).textSelection(.enabled)
                            Button {
                                Clipboard.copy(command)
                            } label: {
                                Image(systemName: Theme.Symbols.copy)
                            }
                            .buttonStyle(.borderless)
                            .help(Text("Copy"))
                        }
                    }
                }
            }
        } header: {
            Text("Agents")
        } footer: {
            Text("Detected from the agents' config files. Installing changes those files, so run the command yourself.")
        }
    }

    private func status(_ ok: Bool, _ title: LocalizedStringKey) -> some View {
        Label(title, systemImage: ok ? Theme.Symbols.ok : Theme.Symbols.missing)
            .foregroundStyle(ok ? Theme.Colors.ok : Theme.Colors.secondaryText)
            .font(Theme.Fonts.caption)
    }
}
