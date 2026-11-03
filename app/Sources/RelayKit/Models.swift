import Foundation

// Shapes of the `relay watch` local API (relay/server.py). Keys arrive in snake_case and are
// decoded with `.convertFromSnakeCase`; every field the engine may omit is optional.

public enum Agent: String, Codable, CaseIterable, Sendable {
    case claude, codex

    public var displayName: String { self == .claude ? "Claude" : "Codex" }

    /// Context window used when the transcript does not report one. Codex sessions usually
    /// report `model_context_window` (~258K); Claude Code transcripts do not.
    public var defaultContextWindow: Int { self == .claude ? 1_000_000 : 258_000 }
}

public struct Session: Codable, Hashable, Sendable {
    public var id: String
    public var agent: String
    public var path: String?
    public var project: String?
    public var cwd: String?
    public var title: String?
    public var entrypoint: String?
    public var model: String?
    public var effort: String?
    public var context: Int
    public var peakContext: Int?
    public var contextWindow: Int?
    public var contextHistory: [Int]?
    public var calls: Int?
    public var turns: Int?
    public var toolCalls: Int?
    public var failures: Int?
    public var idleSeconds: Int?
    public var lastActivity: Double?
    public var lastPrompt: String?
    public var rateLimits: CodexLimits?

    /// Unique across agents (ids are only unique per agent).
    public var key: String { "\(agent):\(id)" }
    public var agentKind: Agent? { Agent(rawValue: agent) }
    public var window: Int { contextWindow ?? agentKind?.defaultContextWindow ?? 1_000_000 }
    public var displayTitle: String { title?.isEmpty == false ? title! : (project ?? id) }
}

public struct Suggested: Codable, Hashable, Sendable {
    public var model: String?
    public var effort: String?

    public init(model: String? = nil, effort: String? = nil) {
        self.model = model
        self.effort = effort
    }
}

public enum Severity: String, Codable, Comparable, Sendable {
    case info, warn, high

    var rank: Int { self == .info ? 0 : self == .warn ? 1 : 2 }
    public static func < (a: Severity, b: Severity) -> Bool { a.rank < b.rank }
}

public enum Outcome: String, Codable, Sendable {
    case useful, wrong
}

public struct Advice: Codable, Identifiable, Hashable, Sendable {
    public var id: String
    public var key: String?
    /// Missing in LLM advice from periodic reviews written before 2026-11-03 (the engine now sends "review").
    public var rule: String?
    public var agent: String?
    public var session: String?
    public var project: String?
    public var sessionTitle: String?
    public var title: String
    public var action: String?
    public var severity: Severity
    public var reason: String
    public var suggested: Suggested?
    public var ts: String
    public var source: String?
    public var outcome: Outcome?
    public var outcomeNote: String?

    public var date: Date? { RelayDates.parse(ts) }
    public var sessionKey: String? { agent.flatMap { a in session.map { "\(a):\($0)" } } }
    public var isFromAdvisor: Bool { source?.hasPrefix("llm") ?? false }
    /// "llm:groq/llama-3.3-70b-versatile" → "groq/llama-3.3-70b-versatile"; rules → nil.
    public var advisorLabel: String? {
        guard let source, source.hasPrefix("llm:") else { return nil }
        return String(source.dropFirst(4))
    }
}

// MARK: Quota

public struct CodexWindow: Codable, Hashable, Sendable {
    public var usedPercent: Double?
    public var windowMinutes: Int?
    public var resetsAt: Double?
}

public struct CodexLimits: Codable, Hashable, Sendable {
    public var primary: CodexWindow?
    public var secondary: CodexWindow?
    public var planType: String?
    public var ts: String?
}

/// Claude Code status-line `rate_limits` windows, as recorded by `relay statusline`.
public struct ClaudeWindow: Codable, Hashable, Sendable {
    public var usedPercentage: Double?
    public var resetsAt: FlexibleDate?
}

public struct ClaudeQuota: Codable, Hashable, Sendable {
    public var fiveHour: ClaudeWindow?
    public var sevenDay: ClaudeWindow?
    public var ts: String?
}

/// The engine's verdict on one agent's reading (`relay watch` `quota_state`): "ok", "stale",
/// "no_reading" or "no_source", and where readings come from ("statusline", "rollout").
public struct QuotaSourceStatus: Codable, Hashable, Sendable {
    public var state: String
    public var source: String?

    public init(state: String, source: String? = nil) {
        self.state = state
        self.source = source
    }
}

public struct QuotaResponse: Codable, Hashable, Sendable {
    public var claude: ClaudeQuota?
    public var codex: CodexLimits?
    public var status: [String: QuotaSourceStatus]?

    public init(claude: ClaudeQuota? = nil, codex: CodexLimits? = nil, status: [String: QuotaSourceStatus]? = nil) {
        self.claude = claude
        self.codex = codex
        self.status = status
    }
}

/// A date the engine sends either as epoch seconds or as an ISO 8601 string.
public struct FlexibleDate: Codable, Hashable, Sendable {
    public var date: Date?

    public init(_ date: Date?) { self.date = date }

    public init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if let n = try? c.decode(Double.self) {
            date = Date(timeIntervalSince1970: n > 1e12 ? n / 1000 : n)
        } else if let s = try? c.decode(String.self) {
            date = RelayDates.parse(s)
        } else {
            date = nil
        }
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        try c.encode(date?.timeIntervalSince1970)
    }
}

// MARK: Health, catalog, agents

public struct ChainEntry: Codable, Hashable, Sendable {
    public var entry: String
    public var keys: Int
    public var keysReady: Int
    public var coolingSeconds: Int
    public var unavailable: Bool
    public var lastError: String?
}

public struct Health: Codable, Hashable, Sendable {
    public var ok: Bool
    public var started: Double?
    public var port: Int?
    public var pid: Int?
    public var sessions: Int?
    public var llm: [ChainEntry]
    public var llmLastError: String?
    public var advice: Int?
}

public struct CatalogModel: Codable, Hashable, Sendable {
    public var slug: String
    public var name: String?
    public var alias: String?
    public var tier: String?
    public var efforts: [String]?
}

public typealias Catalog = [String: [CatalogModel]]

public struct AgentStatus: Codable, Hashable, Sendable {
    public var agent: String
    public var config: String
    public var configExists: Bool
    public var hooks: [String]
    public var hooksInstalled: Bool
    public var skillLinked: Bool
    public var planSkillLinked: Bool?
    public var ruleInstalled: Bool?
    public var statusline: Bool?
    public var autocompact: Int?
}

// MARK: Usage report (`relay usage --json`)

public struct AgentUsage: Codable, Hashable, Sendable {
    public var calls: Int
    public var fresh: Int?
    public var cacheWrite: Int?
    public var cacheRead: Int?
    public var output: Int?
    public var context: Int
    public var weight: Double?
    public var overCap: Int?
}

public struct UsageSession: Codable, Hashable, Sendable {
    public var agent: String
    public var session: String
    public var project: String?
    public var first: String?
    public var last: String?
    public var calls: Int
    public var subagentCalls: Int?
    public var maxContext: Int
    public var contextSum: Int
    public var output: Int?
    public var models: [String: Int]?
    public var share: Double?
}

public struct UsageReport: Codable, Hashable, Sendable {
    public static let bucketOrder = ["<100K", "100-200K", "200-400K", "400-700K", ">=700K"]

    public var since: String
    public var cap: Int
    public var days: Int?
    public var agents: [String: AgentUsage]
    /// label → [calls, context tokens]
    public var buckets: [String: [Int]]
    public var sessions: [UsageSession]

    public var orderedBuckets: [(label: String, calls: Int, tokens: Int)] {
        Self.bucketOrder.compactMap { label in
            buckets[label].map { (label, $0.first ?? 0, $0.count > 1 ? $0[1] : 0) }
        }
    }
}

// MARK: Server-sent events

public enum ServerEvent: Sendable {
    case session(Session)
    case advice(Advice)
    case quota(QuotaResponse)
    case run(Run)
    case other(String)
}

public enum RelayDates {
    private static let iso: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f
    }()
    private static let isoPlain = ISO8601DateFormatter()
    private static let formats = ["yyyy-MM-dd'T'HH:mm:ssZ", "yyyy-MM-dd'T'HH:mm:ss"]

    /// Parses the engine's timestamps: "2026-11-02T22:38:54+0100", "…Z", "…T06:53:53.708Z",
    /// and the status line's local "2026-11-02T22:38:54".
    public static func parse(_ s: String) -> Date? {
        if let d = iso.date(from: s) ?? isoPlain.date(from: s) { return d }
        for format in formats {
            let f = DateFormatter()
            f.locale = Locale(identifier: "en_US_POSIX")
            f.dateFormat = format
            if let d = f.date(from: s) { return d }
        }
        return nil
    }
}
