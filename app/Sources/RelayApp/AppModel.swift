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
        async let s = client.sessions()
        async let a = client.advice()
        async let q = client.quota()
        async let c = client.catalog()
        async let cfg = client.config()
        async let ag = client.agents()
        do {
            (sessions, advice, quota, catalog, config, agents) = try await (s, a, q, c, cfg, ag)
            lastError = nil
        } catch {
            lastError = error.localizedDescription
        }
        keyAccounts = GroqKeychain.accounts()
    }

    func refreshLive() async {
        if let s = try? await client.sessions() { sessions = s }
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
