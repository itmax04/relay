import Foundation

// Dispatcher runs (`relay run`, relay/dispatch.py), served as GET /api/runs and SSE "run" events.

public enum RunStatus: String, Codable, Sendable, CaseIterable {
    case running, waiting, done, failed, stopped

    public var isActive: Bool { self == .running || self == .waiting }
    /// A failed or stopped run can continue from its last step commit (`relay run resume`).
    public var canResume: Bool { self == .failed || self == .stopped }
}

public struct RunGate: Codable, Hashable, Sendable {
    /// "plan", "step-<n>" or "quota".
    public var id: String
    /// "plan", "confirm" or "quota".
    public var kind: String
    public var step: Int?
    /// Epoch seconds, for a quota wait.
    public var until: Double?

    /// The owner can approve plan and [confirm] gates; a quota wait ends by itself.
    public var needsOwner: Bool { kind != "quota" }
}

public struct RunCurrent: Codable, Hashable, Sendable {
    public var step: Int
    public var total: Int
    public var title: String?
    public var agent: String?
    public var model: String?
    public var effort: String?
    public var tier: String?
    public var attempt: Int
}

public struct RunCheck: Codable, Hashable, Sendable {
    public var command: String
    public var exit: Int?
}

public struct RunAttempt: Codable, Hashable, Sendable {
    public var n: Int
    public var agent: String?
    public var tier: String?
    public var model: String?
    public var effort: String?
    public var started: String?
    public var ended: String?
    /// passed, checks_failed, no_changes, outside_files, timeout, agent_failed, agent_error, quota; nil while running.
    public var result: String?
    public var error: String?
    public var checks: [RunCheck]?
}

public struct RunStep: Codable, Hashable, Sendable, Identifiable {
    public var n: Int
    public var title: String
    public var tier: String
    public var confirm: Bool?
    public var agent: String?
    public var checks: [String]?
    public var files: [String]?
    /// pending, waiting, running, done, failed.
    public var status: String
    public var attempts: [RunAttempt]
    public var summary: String?
    public var diffstat: String?
    public var commit: String?

    public var id: Int { n }
}

public struct Run: Codable, Hashable, Sendable, Identifiable {
    public var id: String
    public var pid: Int?
    public var project: String
    public var planTitle: String
    public var branch: String
    public var worktree: String?
    public var created: String
    public var updated: String?
    public var status: RunStatus
    public var gate: RunGate?
    public var error: String?
    public var current: RunCurrent?
    public var steps: [RunStep]

    public var doneSteps: Int { steps.filter { $0.status == "done" }.count }
    public var projectName: String { (project as NSString).lastPathComponent }
    public var createdDate: Date? { RelayDates.parse(created) }

    /// One line for the popover: "step 3/7 · Sol 5.6 · attempt 1", or what the run waits for.
    public var statusLine: String {
        if let gate, status == .waiting {
            switch gate.kind {
            case "plan": return "plan waits for approval"
            case "quota": return "waiting for Codex quota"
            default: return "step \(gate.step ?? 0)/\(steps.count) waits for you"
            }
        }
        if let c = current, status == .running {
            let model = c.model.map(ModelName.display) ?? (c.agent ?? "")
            return "step \(c.step)/\(c.total) · \(model) · attempt \(c.attempt)"
        }
        switch status {
        case .done: return "done · \(doneSteps)/\(steps.count) steps"
        case .failed: return "failed at \(doneSteps)/\(steps.count)"
        case .stopped: return "stopped at \(doneSteps)/\(steps.count)"
        default: return "starting"
        }
    }
}

public struct RunControlResult: Codable, Sendable {
    public var ok: Bool
    public var gate: String?
    /// Set by POST /resume: "resuming", or why the run cannot be resumed.
    public var message: String?
}

public extension Array where Element == Run {
    /// Replace or insert by id, newest first.
    mutating func upsert(_ run: Run) {
        if let i = firstIndex(where: { $0.id == run.id }) { self[i] = run } else { insert(run, at: 0) }
        sort { $0.created > $1.created }
    }
}

public extension APIClient {
    func runs() async throws -> [Run] { try await get("/api/runs") }

    func stopRun(_ id: String) async throws -> RunControlResult {
        try await post("/api/runs/\(id)/stop", body: [String: String]())
    }

    func approveRun(_ id: String) async throws -> RunControlResult {
        try await post("/api/runs/\(id)/approve", body: [String: String]())
    }

    /// Starts a detached dispatcher that continues the run in its existing worktree and branch.
    func resumeRun(_ id: String) async throws -> RunControlResult {
        try await post("/api/runs/\(id)/resume", body: [String: String]())
    }
}
