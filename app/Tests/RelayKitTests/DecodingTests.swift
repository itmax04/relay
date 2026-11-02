import XCTest
@testable import RelayKit

/// Fixtures are anonymised responses of the real engine (`relay watch`, 2026-11-02).
final class DecodingTests: XCTestCase {
    func fixture(_ name: String, _ ext: String = "json") throws -> Data {
        let url = try XCTUnwrap(Bundle.module.url(forResource: name, withExtension: ext, subdirectory: "Fixtures"))
        return try Data(contentsOf: url)
    }

    func testSessions() throws {
        let sessions = try APIClient.decode([Session].self, from: fixture("sessions"))
        XCTAssertEqual(sessions.count, 2)
        let claude = sessions[0]
        XCTAssertEqual(claude.agentKind, .claude)
        XCTAssertNil(claude.contextWindow)
        XCTAssertEqual(claude.window, 1_000_000)
        XCTAssertEqual(claude.contextHistory?.count, 3)
        XCTAssertNil(claude.rateLimits)
        let codex = sessions[1]
        XCTAssertEqual(codex.key, "codex:019a-codex")
        XCTAssertEqual(codex.window, 258_000)
        XCTAssertEqual(codex.rateLimits?.primary?.windowMinutes, 300)
    }

    func testAdvice() throws {
        let advice = try APIClient.decode([Advice].self, from: fixture("advice"))
        XCTAssertEqual(advice.map(\.severity), [.info, .warn, .info])
        XCTAssertNotNil(advice[0].date)
        XCTAssertNil(advice[0].suggested)
        XCTAssertEqual(AdviceAction.of(advice[1]), .continueInNewSession)
        let llm = advice[2]
        XCTAssertEqual(llm.outcome, .useful)
        XCTAssertEqual(llm.advisorLabel, "groq/llama-3.3-70b-versatile")
        XCTAssertEqual(AdviceAction.of(llm), .switchInCodex(model: "gpt-6.1-terra", effort: "low"))
        XCTAssertEqual(advice.ranked().first?.severity, .warn)
        XCTAssertFalse(llm.isOpen(dismissed: []))  // judged already
        XCTAssertFalse(advice[1].isOpen(dismissed: [advice[1].id]))
    }

    func testClaudeSwitchCopiesModelCommand() throws {
        var advice = try APIClient.decode([Advice].self, from: fixture("advice"))[2]
        advice.agent = "claude"
        advice.suggested = Suggested(model: "claude-sonnet-5-5", effort: "medium")
        XCTAssertEqual(AdviceAction.of(advice), .copyModelCommand("/model claude-sonnet-5-5"))
    }

    func testQuota() throws {
        let q = try APIClient.decode(QuotaResponse.self, from: fixture("quota"))
        let claude = q.claudeWindows
        XCTAssertEqual(claude.fiveHour?.usedPercent, 23.5)
        XCTAssertEqual(claude.fiveHour?.resetsAt, Date(timeIntervalSince1970: 1_793_640_000))
        XCTAssertEqual(claude.week?.resetsAt, ISO8601DateFormatter().date(from: "2026-11-05T09:00:00Z"))
        let codex = q.codexWindows
        XCTAssertEqual(codex.fiveHour?.usedPercent, 91)
        XCTAssertEqual(codex.week?.windowMinutes, 10_080)
        XCTAssertEqual(q.codex?.planType, "team")
        // The 5-hour reading was taken before its window reset: stale, no projection.
        let after = Date(timeIntervalSince1970: 1_793_617_963 + 60)
        XCTAssertTrue(codex.fiveHour!.isStale(now: after))
        XCTAssertNil(codex.fiveHour!.projectedAtReset(now: after))
    }

    func testWeeklyForecast() {
        let reset = Date(timeIntervalSince1970: 1_000_000)
        let w = QuotaWindow(usedPercent: 60, resetsAt: reset, windowMinutes: 10_080, readAt: nil)
        let halfway = reset.addingTimeInterval(-3.5 * 86_400)
        XCTAssertEqual(w.projectedAtReset(now: halfway)!, 120, accuracy: 0.001)
        let out = try! XCTUnwrap(w.runsOutAt(now: halfway))
        XCTAssertEqual(out.timeIntervalSince(w.start!), 7 * 86_400 * 100 / 120, accuracy: 1)
    }

    func testHealthCatalogAgentsConfigUsage() throws {
        let h = try APIClient.decode(Health.self, from: fixture("health"))
        XCTAssertEqual(h.llm.map(\.keysReady), [1, 0])
        XCTAssertEqual(h.llm[1].lastError, "no keys")

        let agents = try APIClient.decode([AgentStatus].self, from: fixture("agents"))
        XCTAssertEqual(agents.map(\.hooksInstalled), [true, false])
        XCTAssertEqual(agents[0].autocompact, 350_000)

        let cfg = try APIClient.decode(ConfigView.self, from: fixture("config"))
        XCTAssertEqual(cfg.effective.softLimit, 150_000)
        XCTAssertEqual(cfg.effective.llm.groqKeychainAccounts, ["default"])
        XCTAssertEqual(cfg.effective.llm.chain.first?.provider, "groq")
        XCTAssertTrue(cfg.effective.llm.sends("prompt"))
        XCTAssertEqual(cfg.user["soft_limit"], .number(150_000))

        let usage = try APIClient.decode(UsageReport.self, from: fixture("usage"))
        XCTAssertEqual(usage.orderedBuckets.map(\.label), UsageReport.bucketOrder)
        XCTAssertGreaterThan(usage.agents["claude"]?.calls ?? 0, 0)
        XCTAssertEqual(usage.sessions.count, 2)
    }

    func testEventStream() throws {
        let text = String(decoding: try fixture("events", "txt"), as: UTF8.self)
        var parser = SSEParser()
        var events: [ServerEvent] = []
        // Feed without blank lines, as URLSession's `lines` does.
        for line in text.split(separator: "\n", omittingEmptySubsequences: true) {
            events += parser.feed(String(line))
        }
        events += parser.finish()
        XCTAssertEqual(events.count, 3)
        guard case .advice(let a) = events[0], case .quota(let q) = events[1], case .session(let s) = events[2] else {
            return XCTFail("unexpected events \(events)")
        }
        XCTAssertEqual(a.rule, "context")
        XCTAssertNil(q.claude)
        XCTAssertEqual(s.agentKind, .codex)
    }

    func testConfigPatchEncodesIntegers() throws {
        let patch: [String: JSONValue] = ["soft_limit": .number(120_000), "llm": .object(["send": .object(["prompt": .bool(false)])])]
        let json = String(decoding: try JSONEncoder().encode(patch), as: UTF8.self)
        XCTAssertTrue(json.contains("120000"))
        XCTAssertFalse(json.contains("120000.0"))
    }

    func testFormattingAndCommands() throws {
        XCTAssertEqual(Tokens.short(486_000), "486K")
        XCTAssertEqual(Tokens.short(1_000_000), "1M")
        XCTAssertEqual(Tokens.short(1_250_000), "1.25M")
        XCTAssertEqual(Tokens.short(950), "950")
        XCTAssertEqual(ShellQuote.quote("it's"), #"'it'\''s'"#)
        XCTAssertEqual(LaunchCommand.make(agent: .codex, projectRoot: "/p q", prompt: "Continue.", model: "gpt-6.1-sol", effort: "low"),
                       "cd '/p q' && codex -m 'gpt-6.1-sol' -c 'model_reasoning_effort=low' 'Continue.'")
        XCTAssertEqual(LaunchCommand.make(agent: .claude, projectRoot: "/p", prompt: "Go"), "cd '/p' && claude 'Go'")
    }

    func testHandoffLocation() throws {
        let fm = FileManager.default
        let root = fm.temporaryDirectory.appendingPathComponent("relay-test-\(UUID().uuidString)")
        let sub = root.appendingPathComponent("src/deep")
        try fm.createDirectory(at: sub, withIntermediateDirectories: true)
        try fm.createDirectory(at: root.appendingPathComponent(".relay"), withIntermediateDirectories: true)
        defer { try? fm.removeItem(at: root) }
        var info = HandoffInfo.locate(cwd: sub.path, handoffPath: ".relay/handoff.md")
        XCTAssertEqual(info.projectRoot, root.standardizedFileURL.path)
        XCTAssertFalse(info.exists)
        try "x".write(to: root.appendingPathComponent(".relay/handoff.md"), atomically: true, encoding: .utf8)
        info = HandoffInfo.locate(cwd: sub.path, handoffPath: ".relay/handoff.md")
        XCTAssertTrue(info.exists)
        XCTAssertNotNil(info.modified)
        XCTAssertEqual(info.continuationPrompt, "Continue from the handoff at .relay/handoff.md.")
    }
}
