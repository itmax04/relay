import Foundation
import Observation
import RelayKit
import SwiftUI

/// What the "Continue in a new session" sheet is about.
struct ContinueTarget: Identifiable {
    var session: Session?
    var advice: Advice?
    var id: String { advice?.id ?? session?.key ?? "none" }
}

/// App state: a mirror of the engine's API, kept current by the SSE stream and a slow poll.
@MainActor @Observable
final class AppModel {
    enum EngineState: Equatable {
        case connecting, starting, up
        case down(String)
    }

    var engine: EngineState = .connecting
    var health: Health?
    var sessions: [Session] = []
    var advice: [Advice] = []
    var runs: [Run] = []
    var quota = QuotaResponse(claude: nil, codex: nil)
    var catalog: Catalog = [:]
    var config: ConfigView?
    var agents: [AgentStatus] = []
    var usage: [Int: UsageReport] = [:]
    var usageLoading = false
    var usageError: String?
    var keyAccounts: [String] = []
    var lastError: String?
    var continueTarget: ContinueTarget?
    var now = Date()

    var dismissed: Set<String> {
        didSet { UserDefaults.standard.set(Array(dismissed.suffix(500)), forKey: "dismissedAdvice") }
    }
    var autoStart: Bool {
        didSet { UserDefaults.standard.set(autoStart, forKey: "autoStartEngine") }
    }
    var repoPath: String {
        didSet { UserDefaults.standard.set(repoPath, forKey: "repoPath") }
    }

    private var client: APIClient
    private var loop: Task<Void, Never>?
    private var startAttempted = false

    init() {
        let d = UserDefaults.standard
        dismissed = Set(d.stringArray(forKey: "dismissedAdvice") ?? [])
        autoStart = d.object(forKey: "autoStartEngine") as? Bool ?? true
        repoPath = d.string(forKey: "repoPath") ?? EngineLauncher.defaultRepo
        let port = d.integer(forKey: "apiPort")
        client = APIClient(port: port > 0 ? port : 8765)
        keyAccounts = GroqKeychain.accounts()
    }

    // MARK: Derived

    var soft: Int { config?.effective.softLimit ?? 150_000 }
    var hard: Int { config?.effective.hardLimit ?? 300_000 }
    var quotaWarn: Int { config?.effective.quotaWarn ?? 75 }
    var quotaHigh: Int { config?.effective.quotaHigh ?? 90 }
    var handoffPath: String { config?.effective.handoffPath ?? ".relay/handoff.md" }

    var openAdvice: [Advice] { advice.filter { $0.isOpen(dismissed: dismissed, now: now) }.ranked() }
    var topSeverity: Severity? { openAdvice.first?.severity }

    func session(for advice: Advice) -> Session? {
        sessions.first { $0.key == advice.sessionKey }
    }

    func openAdvice(for session: Session) -> [Advice] {
        openAdvice.filter { $0.sessionKey == session.key }
    }

    /// Touches the file system: call it off the main thread.
    nonisolated static func handoff(cwd: String?, handoffPath: String) -> HandoffInfo? {
        guard let cwd, !cwd.isEmpty else { return nil }
        return HandoffInfo.locate(cwd: cwd, handoffPath: handoffPath)
    }

    func cacheRemaining(_ session: Session) -> Int? {
        guard let idle = session.idleSeconds, let ttl = config?.effective.cacheTtl[session.agent] else { return nil }
        return ttl - idle
    }

    // MARK: Lifecycle

    func start() {
        guard loop == nil else { return }
        loop = Task { [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                if await self.connect() {
                    await self.follow()
                }
                try? await Task.sleep(for: .seconds(3))
            }
        }
        Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(10))
                guard let self else { return }
                self.now = Date()
                if self.engine == .up { await self.refreshLive() }
            }
        }
    }

    /// True once the API answers; starts the engine (once) when it does not.
    private func connect() async -> Bool {
        do {
            health = try await client.health()
        } catch {
            health = nil
            if autoStart && !startAttempted {
                startAttempted = true
                return await startEngine()
            }
            if case .starting = engine { return false }
            engine = .down(error.localizedDescription)
            return false
        }
        engine = .up
        await refreshAll()
        return true
    }

    /// Follows the event stream until the engine goes away.
    private func follow() async {
        do {
            for try await event in client.events() {
                apply(event)
            }
        } catch {}
        engine = .down(String(localized: "The engine stopped answering."))
        startAttempted = false
    }

    private func apply(_ event: ServerEvent) {
        switch event {
        case .session(let s):
            if let i = sessions.firstIndex(where: { $0.key == s.key }) { sessions[i] = s } else { sessions.insert(s, at: 0) }
        case .advice(let a):
            if let i = advice.firstIndex(where: { $0.id == a.id }) { advice[i] = a } else { advice.insert(a, at: 0) }
        case .quota(let q):
            quota = q
        case .run(let r):
            runs.upsert(r)
        case .other:
            break
        }
    }

    func startEngine() async -> Bool {
        engine = .starting
        do {
            try EngineLauncher.start(repo: repoPath)
        } catch {
            engine = .down(error.localizedDescription)
            return false
        }
        for _ in 0..<20 {
            try? await Task.sleep(for: .milliseconds(500))
            if let h = try? await client.health() {
                health = h
                engine = .up
                await refreshAll()
                return true
            }
        }
        engine = .down(String(localized: "relay watch did not start. See \(EngineLauncher.logPath)."))
        return false
    }

    func restartEngine() async {
        if let pid = health?.pid { EngineLauncher.stop(pid: pid) }
        try? await Task.sleep(for: .seconds(1))
        startAttempted = true
        _ = await startEngine()
    }

    func refreshAll() async {
        // Each response on its own: one that fails to decode must not blank the others
        // (2026-11-03: advice without "rule" left the quota rings empty).
        async let s = Result.of { try await self.client.sessions() }
        async let a = Result.of { try await self.client.advice() }
        async let q = Result.of { try await self.client.quota() }
        async let c = Result.of { try await self.client.catalog() }
        async let cfg = Result.of { try await self.client.config() }
        async let ag = Result.of { try await self.client.agents() }
        var errors: [String] = []
        func take<T>(_ r: Result<T, Error>, _ name: String, _ set: (T) -> Void) {
            switch r {
            case .success(let v): set(v)
            case .failure(let e): errors.append("\(name): \(e.localizedDescription)")
            }
        }
        take(await s, "sessions") { sessions = $0 }
        take(await a, "advice") { advice = $0 }
        take(await q, "quota") { quota = $0 }
        take(await c, "catalog") { catalog = $0 }
        take(await cfg, "config") { config = $0 }
        take(await ag, "agents") { agents = $0 }
        lastError = errors.isEmpty ? nil : errors.joined(separator: "\n")
        keyAccounts = GroqKeychain.accounts()
        await refreshRuns()
    }

    /// Separate from refreshAll so an engine without /api/runs (older build) still loads the rest.
    func refreshRuns() async {
        if let r = try? await client.runs() { runs = r }
    }

    func refreshLive() async {
        if let s = try? await client.sessions() { sessions = s }
        await refreshRuns()
        if let h = try? await client.health() { health = h }
    }

    func loadUsage(days: Int, force: Bool = false) async {
        if usage[days] != nil && !force { return }
        usageLoading = true
        usageError = nil
        defer { usageLoading = false }
        do {
            usage[days] = try await client.usage(days: days)
        } catch where !Task.isCancelled {
            usageError = error.localizedDescription
        } catch {}  // the view moved on (period or engine state changed)
    }

    // MARK: Actions

    func setOutcome(_ advice: Advice, _ outcome: Outcome?) async {
        do {
            apply(.advice(try await client.setOutcome(advice.id, outcome)))
        } catch {
            lastError = error.localizedDescription
        }
    }

    func dismiss(_ advice: Advice) { dismissed.insert(advice.id) }

    var activeRuns: [Run] { runs.filter { $0.status.isActive } }

    /// Stop kills the run's agent process; the dispatcher records the run as stopped.
    func stopRun(_ run: Run) async {
        await control { try await self.client.stopRun(run.id) }
    }

    /// Approves the plan or [confirm] step the run waits at.
    func approveRun(_ run: Run) async {
        await control { try await self.client.approveRun(run.id) }
    }

    /// Continues a failed or stopped run from its last step commit.
    func resumeRun(_ run: Run) async {
        await control { try await self.client.resumeRun(run.id) }
    }

    private func control(_ call: () async throws -> RunControlResult) async {
        do {
            let r = try await call()
            lastError = r.ok ? nil : String(localized: "The run did not accept the request.")
        } catch {
            lastError = error.localizedDescription
        }
        await refreshRuns()
    }

    /// Writes settings through the engine (it owns ~/.config/relay/config.json and applies them live).
    @discardableResult
    func saveConfig(_ patch: [String: JSONValue]) async -> Bool {
        do {
            config = try await client.updateConfig(patch)
            health = try? await client.health()
            lastError = nil
            return true
        } catch {
            lastError = error.localizedDescription
            return false
        }
    }

    func addGroqKey(account: String, key: String) async throws {
        try GroqKeychain.add(account: account, key: key)
        await syncKeyAccounts()
    }

    func removeGroqKey(account: String) async throws {
        try GroqKeychain.delete(account: account)
        await syncKeyAccounts()
    }

    /// The engine reads keys for the accounts listed in llm.groq_keychain_accounts.
    private func syncKeyAccounts() async {
        keyAccounts = GroqKeychain.accounts()
        await saveConfig(["llm": .object(["groq_keychain_accounts": .array(keyAccounts.map(JSONValue.string))])])
    }

    func openContinue(session: Session?, advice: Advice?) {
        continueTarget = ContinueTarget(session: session ?? advice.flatMap(self.session(for:)), advice: advice)
    }
}

extension Result where Failure == Error {
    static func of(_ body: () async throws -> Success) async -> Result {
        do { return .success(try await body()) } catch { return .failure(error) }
    }
}
