import Foundation

/// Any JSON value; used for config patches and for parts of responses the app only passes through.
public enum JSONValue: Codable, Hashable, Sendable {
    case null
    case bool(Bool)
    case number(Double)
    case string(String)
    case array([JSONValue])
    case object([String: JSONValue])

    public init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if c.decodeNil() { self = .null }
        else if let b = try? c.decode(Bool.self) { self = .bool(b) }
        else if let n = try? c.decode(Double.self) { self = .number(n) }
        else if let s = try? c.decode(String.self) { self = .string(s) }
        else if let a = try? c.decode([JSONValue].self) { self = .array(a) }
        else { self = .object(try c.decode([String: JSONValue].self)) }
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        switch self {
        case .null: try c.encodeNil()
        case .bool(let b): try c.encode(b)
        case .number(let n):
            if n.rounded() == n, abs(n) < 1e15 { try c.encode(Int64(n)) } else { try c.encode(n) }
        case .string(let s): try c.encode(s)
        case .array(let a): try c.encode(a)
        case .object(let o): try c.encode(o)
        }
    }

    public var objectValue: [String: JSONValue]? { if case .object(let o) = self { o } else { nil } }
}

public struct ChainItem: Codable, Hashable, Sendable {
    public var provider: String
    public var model: String
}

public struct LLMConfig: Codable, Hashable, Sendable {
    public var enabled: Bool
    public var chain: [ChainItem]
    public var groqKeychainAccounts: [String]
    public var cloudflareAccountId: String?
    public var timeout: Double?
    public var minIntervalPerSession: Int?
    public var maxPerHour: Int?
    public var reviewEveryCalls: Int?
    public var send: [String: Bool]?

    public func sends(_ key: String) -> Bool { send?[key] ?? true }
}

/// The settings the app shows and edits; the engine's `config.DEFAULTS` holds the rest.
public struct RelayConfig: Codable, Hashable, Sendable {
    public var softLimit: Int
    public var hardLimit: Int
    public var handoffPath: String
    public var watchActiveHours: Double
    public var cacheTtl: [String: Int]
    public var cacheWarnSeconds: Int
    public var bigContext: Int
    public var repeatFailures: Int
    public var largeReadTokens: Int
    public var mechanicalRun: Int
    public var quotaWarn: Int
    public var quotaHigh: Int
    public var apiPort: Int
    public var notify: Bool
    public var llm: LLMConfig
}

public struct ConfigView: Codable, Hashable, Sendable {
    public var path: String
    public var user: [String: JSONValue]
    public var effective: RelayConfig
    public var defaults: RelayConfig
}

/// Fields the engine sends for `send` toggles, in display order.
public enum AdvisorData: String, CaseIterable, Sendable {
    case prompt, errors, files, tools
}
