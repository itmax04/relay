import XCTest
@testable import RelayKit

/// Fixture: run.json of a real 2-step `relay run` on codex-cli 0.144.6 (2026-11-03), paths
/// anonymised, plus a copy edited to wait at a [confirm] step.
final class RunTests: XCTestCase {
    func runs() throws -> [Run] {
        let url = try XCTUnwrap(Bundle.module.url(forResource: "runs", withExtension: "json", subdirectory: "Fixtures"))
        return try APIClient.decode([Run].self, from: Data(contentsOf: url))
    }

    func testDecodeAndStatusLine() throws {
        let (waiting, done) = try { let r = try runs(); return (r[0], r[1]) }()
        XCTAssertEqual(done.status, .done)
        XCTAssertFalse(done.status.isActive)
        XCTAssertEqual(done.planTitle, "Tiny greeting tool")
        XCTAssertEqual(done.projectName, "probe")
        XCTAssertEqual(done.steps.map(\.status), ["done", "done"])
        XCTAssertEqual(done.steps[0].attempts[0].result, "passed")
        XCTAssertEqual(done.steps[0].attempts[0].checks?.first?.exit, 0)
        XCTAssertNotNil(done.steps[0].commit)
        XCTAssertNotNil(done.createdDate)
        XCTAssertEqual(done.statusLine, "done · 2/2 steps")

        XCTAssertEqual(waiting.status, .waiting)
        XCTAssertEqual(waiting.gate?.kind, "confirm")
        XCTAssertTrue(waiting.gate?.needsOwner ?? false)
        XCTAssertEqual(waiting.statusLine, "step 2/2 waits for you")
        var running = waiting
        running.status = .running
        running.gate = nil
        XCTAssertEqual(running.statusLine, "step 2/2 · Sol 5.6 · attempt 1")
    }

    func testUpsertKeepsNewestFirst() throws {
        var list = try runs()
        var newer = list[1]
        newer.id = "x"
        newer.created = "2026-11-04T00:00:00+00:00"
        list.upsert(newer)
        XCTAssertEqual(list.first?.id, "x")
        newer.status = .failed
        list.upsert(newer)
        XCTAssertEqual(list.count, 3)
        XCTAssertEqual(list.first?.status, .failed)
    }

    func testRunEventParses() throws {
        let one = try JSONEncoder().encode(try runs()[1])
        var p = SSEParser()
        _ = p.feed("event: run")
        let events = p.feed("data: " + String(decoding: one, as: UTF8.self))
        guard case .run(let r)? = events.first else { return XCTFail("no run event: \(events)") }
        XCTAssertEqual(r.id, "20261103-181724-d910")
    }
}

final class AdviceShapeTests: XCTestCase {
    /// Real item from the engine log (2026-11-03): LLM advice from a periodic review had no "rule",
    /// and the whole /api/advice array failed to decode.
    func testAdviceWithoutRuleDecodes() throws {
        let json = #"[{"agent": "claude", "session": "s1", "project": "Downloads", "action": "new_session", "severity": "warn", "title": "Context exceeds limits", "reason": "Token count 309k surpasses hard limit.", "suggested": null, "source": "llm:groq/openai/gpt-oss-120b", "trigger": null, "key": "llm:claude:s1:1793726449", "id": "a01ee3f3be85", "ts": "2026-11-03T18:20:49+0100", "outcome": null}]"#
        let advice = try APIClient.decode([Advice].self, from: Data(json.utf8))
        XCTAssertNil(advice[0].rule)
        XCTAssertEqual(advice[0].advisorLabel, "groq/openai/gpt-oss-120b")
    }
}
