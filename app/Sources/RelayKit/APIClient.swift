import Foundation

public enum APIError: Error, LocalizedError, Sendable {
    case status(Int, String)
    case transport(String)

    public var errorDescription: String? {
        switch self {
        case .status(let code, let body): "HTTP \(code): \(body)"
        case .transport(let message): message
        }
    }
}

/// Client of the `relay watch` local API on 127.0.0.1.
public struct APIClient: Sendable {
    public var base: URL
    let session: URLSession

    public init(port: Int = 8765) {
        base = URL(string: "http://127.0.0.1:\(port)")!
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 5
        config.timeoutIntervalForResource = 60
        session = URLSession(configuration: config)
    }

    public static func decoder() -> JSONDecoder {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        return d
    }

    public static func decode<T: Decodable>(_ type: T.Type, from data: Data) throws -> T {
        try decoder().decode(type, from: data)
    }

    func get<T: Decodable>(_ path: String, timeout: TimeInterval = 5) async throws -> T {
        var req = URLRequest(url: URL(string: path, relativeTo: base)!)
        req.timeoutInterval = timeout
        return try await send(req)
    }

    func post<T: Decodable>(_ path: String, body: some Encodable) async throws -> T {
        var req = URLRequest(url: URL(string: path, relativeTo: base)!)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONEncoder().encode(body)
        return try await send(req)
    }

    private func send<T: Decodable>(_ req: URLRequest) async throws -> T {
        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: req)
        } catch {
            throw APIError.transport(error.localizedDescription)
        }
        let code = (response as? HTTPURLResponse)?.statusCode ?? 0
        guard (200..<300).contains(code) else {
            throw APIError.status(code, String(decoding: data.prefix(300), as: UTF8.self))
        }
        return try Self.decode(T.self, from: data)
    }

    public func health() async throws -> Health { try await get("/api/health", timeout: 2) }
    public func sessions() async throws -> [Session] { try await get("/api/sessions") }
    public func quota() async throws -> QuotaResponse { try await get("/api/quota") }
    public func catalog() async throws -> Catalog { try await get("/api/catalog") }
    public func agents() async throws -> [AgentStatus] { try await get("/api/agents") }
    public func config() async throws -> ConfigView { try await get("/api/config") }
    public func advice(limit: Int = 200) async throws -> [Advice] { try await get("/api/advice?limit=\(limit)") }
    /// Reads every transcript of the period on the engine side; can take several seconds.
    public func usage(days: Int) async throws -> UsageReport { try await get("/api/usage?days=\(days)", timeout: 120) }

    public func setOutcome(_ adviceID: String, _ outcome: Outcome?, note: String? = nil) async throws -> Advice {
        struct Body: Encodable { var outcome: String?; var note: String? }
        let id = adviceID.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? adviceID
        return try await post("/api/advice/\(id)/outcome", body: Body(outcome: outcome?.rawValue, note: note))
    }

    /// Writes a partial config to ~/.config/relay/config.json through the engine, which applies it
    /// live. An empty patch only reloads (the engine re-reads Keychain keys).
    public func updateConfig(_ patch: [String: JSONValue]) async throws -> ConfigView {
        try await post("/api/config", body: patch)
    }

    /// The `/api/events` stream. Ends (throws) when the engine goes away.
    public func events() -> AsyncThrowingStream<ServerEvent, Error> {
        let url = URL(string: "/api/events", relativeTo: base)!
        let session = self.session
        return AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    var req = URLRequest(url: url)
                    req.timeoutInterval = 60  // the engine pings every 15 s
                    let (bytes, response) = try await session.bytes(for: req)
                    guard (response as? HTTPURLResponse)?.statusCode == 200 else {
                        throw APIError.transport("event stream refused")
                    }
                    var parser = SSEParser()
                    for try await line in bytes.lines {
                        // `lines` drops empty lines, so a new "event:" also closes the previous one.
                        for event in parser.feed(line) { continuation.yield(event) }
                    }
                    for event in parser.finish() { continuation.yield(event) }
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }
}

/// Server-sent events parser. Accepts lines with or without the blank separator lines, because
/// `URLSession.AsyncBytes.lines` skips empty lines.
public struct SSEParser: Sendable {
    var event: String?
    var data: [String] = []

    public init() {}

    public mutating func feed(_ line: String) -> [ServerEvent] {
        if line.isEmpty { return flush() }
        if line.hasPrefix(":") { return [] }
        let (field, value) = Self.split(line)
        switch field {
        case "event":
            let done = flush()
            event = value
            return done
        case "data":
            data.append(value)
            // The engine always sends one data line per event, so dispatch right away.
            return flush()
        default:
            return []
        }
    }

    public mutating func finish() -> [ServerEvent] { flush() }

    private static func split(_ line: String) -> (String, String) {
        guard let i = line.firstIndex(of: ":") else { return (line, "") }
        var value = line[line.index(after: i)...]
        if value.hasPrefix(" ") { value = value.dropFirst() }
        return (String(line[..<i]), String(value))
    }

    private mutating func flush() -> [ServerEvent] {
        defer { event = nil; data = [] }
        guard !data.isEmpty else { return [] }
        let payload = Data(data.joined(separator: "\n").utf8)
        let name = event ?? "message"
        let d = APIClient.decoder()
        switch name {
        case "session": return (try? d.decode(Session.self, from: payload)).map { [.session($0)] } ?? []
        case "advice": return (try? d.decode(Advice.self, from: payload)).map { [.advice($0)] } ?? []
        case "quota": return (try? d.decode(QuotaResponse.self, from: payload)).map { [.quota($0)] } ?? []
        default: return [.other(name)]
        }
    }
}
