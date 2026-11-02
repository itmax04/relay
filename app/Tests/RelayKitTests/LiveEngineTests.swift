import XCTest
@testable import RelayKit

/// Round-trips against a running `relay watch` (127.0.0.1:8765). Skipped unless RELAY_LIVE=1,
/// because they change and then restore the owner's real advice log and config.
final class LiveEngineTests: XCTestCase {
    override func setUpWithError() throws {
        try XCTSkipUnless(ProcessInfo.processInfo.environment["RELAY_LIVE"] == "1", "set RELAY_LIVE=1")
    }

    func testFeedbackRoundTrip() async throws {
        let api = APIClient()
        let advice = try await api.advice()
        let item = try XCTUnwrap(advice.first { $0.outcome == nil }, "needs one advice without feedback")
        let marked = try await api.setOutcome(item.id, .useful)
        XCTAssertEqual(marked.outcome, .useful)
        let reread = try await api.advice().first { $0.id == item.id }
        XCTAssertEqual(reread?.outcome, .useful)
        let cleared = try await api.setOutcome(item.id, nil)
        XCTAssertNil(cleared.outcome)
    }

    func testSettingsRoundTrip() async throws {
        let api = APIClient()
        let before = try await api.config()
        let old = before.user["watch_active_hours"] ?? .null
        let changed = try await api.updateConfig(["watch_active_hours": .number(13)])
        XCTAssertEqual(changed.effective.watchActiveHours, 13)
        XCTAssertEqual(changed.user["watch_active_hours"], .number(13))
        let restored = try await api.updateConfig(["watch_active_hours": old])
        XCTAssertEqual(restored.effective.watchActiveHours, before.effective.watchActiveHours)
        XCTAssertEqual(restored.user["watch_active_hours"], before.user["watch_active_hours"])
        let health = try await api.health()
        XCTAssertTrue(health.ok)
        _ = try await api.agents()
        _ = try await api.usage(days: 1)
    }
}
