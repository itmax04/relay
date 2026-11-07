import Foundation

public enum Tokens {
    /// 486_000 → "486K", 1_000_000 → "1M", 1_250_000 → "1.25M", 950 → "950".
    public static func short(_ n: Int) -> String {
        let v = Double(n)
        if abs(v) >= 1_000_000 {
            let m = v / 1_000_000
            return (m.rounded() == m ? String(Int(m)) : String(format: "%.2f", m)
                .replacingOccurrences(of: #"0+$"#, with: "", options: .regularExpression)) + "M"
        }
        if abs(v) >= 1_000 { return "\(Int((v / 1_000).rounded()))K" }
        return "\(n)"
    }
}

// MARK: Quota windows

public struct QuotaWindow: Hashable, Sendable {
    public var usedPercent: Double
    public var resetsAt: Date?
    public var windowMinutes: Int
    public var readAt: Date?

    /// The reading is from a window that has since reset, so the real figure is unknown (likely low).
    public func isStale(now: Date = Date()) -> Bool { resetsAt.map { $0 <= now } ?? false }

    public var start: Date? { resetsAt.map { $0.addingTimeInterval(-Double(windowMinutes) * 60) } }

    /// Linear projection of use at reset time, from the share of the window already elapsed.
    /// nil when there is too little of the window behind us (under 5 %) to say anything.
    public func projectedAtReset(now: Date = Date()) -> Double? {
        guard let start, let resetsAt, !isStale(now: now) else { return nil }
        let total = resetsAt.timeIntervalSince(start)
        let elapsed = now.timeIntervalSince(start)
        guard total > 0, elapsed / total >= 0.05 else { return nil }
        return usedPercent * total / elapsed
    }

    /// When use reaches 100 % at the current pace, if before the reset.
    public func runsOutAt(now: Date = Date()) -> Date? {
        guard let start, let resetsAt, usedPercent > 0, !isStale(now: now) else { return nil }
        let elapsed = now.timeIntervalSince(start)
        guard elapsed > 0 else { return nil }
        let at = start.addingTimeInterval(elapsed * 100 / usedPercent)
        return at < resetsAt ? at : nil
    }
}

public enum QuotaState: String, Sendable {
    case ok
    /// Every window reset after the reading: the current figure is unknown.
    case stale
    /// Nothing recorded yet. For Claude: the status line is installed but only the desktop app ran,
    /// which does not run it; one Claude CLI message records a reading.
    case noReading = "no_reading"
    /// Nothing can record a reading (the relay status line is not installed in Claude Code).
    case noSource = "no_source"
}

public struct AgentQuota: Hashable, Sendable {
    public var agent: Agent
    public var fiveHour: QuotaWindow?
    public var week: QuotaWindow?
    /// The engine's state when the windows are missing; staleness is judged here, against `now`.
    public var reported: QuotaState?

    public func state(now: Date = Date()) -> QuotaState {
        let windows = [fiveHour, week].compactMap { $0 }
        if windows.isEmpty { return reported == .noSource ? .noSource : .noReading }
        return windows.allSatisfy { $0.isStale(now: now) } ? .stale : .ok
    }
}

extension QuotaResponse {
    public var claudeWindows: AgentQuota {
        let read = claude?.ts.flatMap(RelayDates.parse)
        func window(_ w: ClaudeWindow?, minutes: Int) -> QuotaWindow? {
            guard let w, let used = w.usedPercentage else { return nil }
            return QuotaWindow(usedPercent: used, resetsAt: w.resetsAt?.date, windowMinutes: minutes, readAt: read)
        }
        return AgentQuota(agent: .claude, fiveHour: window(claude?.fiveHour, minutes: 300),
                          week: window(claude?.sevenDay, minutes: 10_080), reported: reported(.claude))
    }

    public var codexWindows: AgentQuota {
        let read = codex?.ts.flatMap(RelayDates.parse)
        func window(_ w: CodexWindow?, fallback: Int) -> QuotaWindow? {
            guard let w, let used = w.usedPercent else { return nil }
            return QuotaWindow(usedPercent: used, resetsAt: w.resetsAt.map { Date(timeIntervalSince1970: $0) },
                               windowMinutes: w.windowMinutes ?? fallback, readAt: read)
        }
        return AgentQuota(agent: .codex, fiveHour: window(codex?.primary, fallback: 300),
                          week: window(codex?.secondary, fallback: 10_080), reported: reported(.codex))
    }

    private func reported(_ agent: Agent) -> QuotaState? {
        status?[agent.rawValue].flatMap { QuotaState(rawValue: $0.state) }
    }
}

// MARK: Model names

public enum ModelName {
    /// "claude-opus-5-5" → "Opus 5.5", "gpt-6.1-sol" → "Sol 6.1"; a slug that fits neither pattern
    /// is shown as it is.
    public static func display(_ slug: String) -> String {
        if let m = slug.wholeMatch(of: #/claude-([a-z]+)-(\d+)-(\d+)(?:-\d{8})?(\[1m\])?/#) {
            return "\(m.1.capitalized) \(m.2).\(m.3)" + (m.4 == nil ? "" : " 1M")
        }
        if let m = slug.wholeMatch(of: #/gpt-(\d+(?:\.\d+)?)-([a-z]+)/#) {
            return "\(m.2.capitalized) \(m.1)"
        }
        return slug
    }
}

// MARK: Handoff

public struct HandoffInfo: Hashable, Sendable {
    public var projectRoot: String
    /// As configured (`handoff_path`, relative to the project root).
    public var relativePath: String
    public var exists: Bool
    public var modified: Date?

    public var absolutePath: String { (projectRoot as NSString).appendingPathComponent(relativePath) }
    /// The prompt the relay-handoff skill asks the next session to start with.
    public var continuationPrompt: String { "Continue from the handoff at \(relativePath)." }

    /// Same rule as `config.project_root`: nearest ancestor holding .git or .relay, else cwd.
    public static func projectRoot(for cwd: String, fileManager fm: FileManager = .default) -> String {
        let start = URL(fileURLWithPath: cwd).standardizedFileURL.path
        var probe = start
        while true {
            for marker in [".git", ".relay"] where fm.fileExists(atPath: (probe as NSString).appendingPathComponent(marker)) {
                return probe
            }
            // NSString stops at "/"; URL.deletingLastPathComponent turns "/" into "/.." forever.
            let parent = (probe as NSString).deletingLastPathComponent
            if parent == probe || parent.isEmpty { return start }
            probe = parent
        }
    }

    public static func locate(cwd: String, handoffPath: String, fileManager fm: FileManager = .default) -> HandoffInfo {
        let root = projectRoot(for: cwd, fileManager: fm)
        var info = HandoffInfo(projectRoot: root, relativePath: handoffPath, exists: false, modified: nil)
        if let attrs = try? fm.attributesOfItem(atPath: info.absolutePath) {
            info.exists = true
            info.modified = attrs[.modificationDate] as? Date
        }
        return info
    }
}

public enum ShellQuote {
    /// POSIX single-quoting: safe for any string, including quotes and newlines.
    public static func quote(_ s: String) -> String { "'" + s.replacingOccurrences(of: "'", with: #"'\''"#) + "'" }
}

/// Command line that starts a new interactive agent session with a first prompt.
public enum LaunchCommand {
    public static func make(agent: Agent, projectRoot: String, prompt: String,
                            model: String? = nil, effort: String? = nil) -> String {
        var args: [String]
        switch agent {
        case .claude:
            args = ["claude"]
            if let model { args += ["--model", ShellQuote.quote(model)] }
            if let effort { args += ["--effort", ShellQuote.quote(effort)] }
        case .codex:
            args = ["codex"]
            if let model { args += ["-m", ShellQuote.quote(model)] }
            if let effort { args += ["-c", ShellQuote.quote("model_reasoning_effort=\(effort)")] }
        }
        args.append(ShellQuote.quote(prompt))
        return "cd \(ShellQuote.quote(projectRoot)) && " + args.joined(separator: " ")
    }
}

// MARK: Advice actions

/// What the app offers for an advice item. Model switching stays the owner's click:
/// the app copies the command, it never types into an agent.
public enum AdviceAction: Hashable, Sendable {
    case continueInNewSession
    /// Claude Code: `/model <slug>` to paste into the session.
    case copyModelCommand(String)
    /// Codex has no `/model` command for this; the owner switches in Codex's own picker.
    case switchInCodex(model: String, effort: String?)
    case none

    public static func of(_ advice: Advice) -> AdviceAction {
        switch advice.action {
        case "new_session":
            return .continueInNewSession
        case "switch_model":
            guard let model = advice.suggested?.model else { return .none }
            if advice.agent == Agent.codex.rawValue {
                return .switchInCodex(model: model, effort: advice.suggested?.effort)
            }
            return .copyModelCommand("/model \(model)")
        default:
            return .none
        }
    }
}

extension Advice {
    /// Open = not judged, not dismissed, and recent enough to still matter.
    public func isOpen(dismissed: Set<String>, now: Date = Date(), maxAge: TimeInterval = 12 * 3600) -> Bool {
        guard outcome == nil, !dismissed.contains(id) else { return false }
        guard let date else { return true }
        return now.timeIntervalSince(date) < maxAge
    }
}

extension Array where Element == Advice {
    /// Highest severity first, then newest.
    public func ranked() -> [Advice] {
        sorted { a, b in
            a.severity != b.severity ? a.severity > b.severity : (a.date ?? .distantPast) > (b.date ?? .distantPast)
        }
    }
}
