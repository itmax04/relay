import Charts
import RelayKit
import SwiftUI

struct QuotasView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        ScreenScaffold(title: "Quotas", subtitle: "Subscription limits: 5-hour and weekly windows. Percent of quota, not dollars.") {
            ForEach([model.quota.claudeWindows, model.quota.codexWindows], id: \.agent) { q in
                AgentQuotaCard(quota: q)
            }
            Text("Claude readings come from the relay status line, which only Claude CLI runs (the Claude desktop app keeps plan usage to itself); Codex readings come from its session logs. A reading taken before its window reset is shown grey: the current figure is unknown until the agent runs again.")
                .font(Theme.Fonts.caption)
                .foregroundStyle(Theme.Colors.secondaryText)
        }
    }
}

struct AgentQuotaCard: View {
    @Environment(AppModel.self) private var model
    let quota: AgentQuota

    var body: some View {
        VStack(alignment: .leading, spacing: Theme.Space.m) {
            Text(verbatim: quota.agent.displayName).font(Theme.Fonts.sectionTitle)
            if quota.fiveHour == nil && quota.week == nil {
                Text(verbatim: QuotaHint.text(quota.agent, quota.state(now: model.now)) ?? String(localized: "No reading yet."))
                    .foregroundStyle(Theme.Colors.secondaryText)
            } else {
                HStack(alignment: .top, spacing: Theme.Space.xl) {
                    window(quota.fiveHour, title: "5-hour window")
                    window(quota.week, title: "Week")
                    if let week = quota.week { forecast(week) }
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .cardStyle()
    }

    private func window(_ w: QuotaWindow?, title: LocalizedStringKey) -> some View {
        let stale = w?.isStale(now: model.now) ?? false
        return HStack(spacing: Theme.Space.m) {
            QuotaRing(percent: w?.usedPercent, stale: stale, warn: model.quotaWarn, high: model.quotaHigh, large: true)
            VStack(alignment: .leading, spacing: Theme.Space.xs) {
                Text(title).font(Theme.Fonts.cardTitle)
                if let reset = w?.resetsAt {
                    if stale {
                        Text("Reset \(reset.relative) — reading is stale")
                            .foregroundStyle(Theme.Colors.secondaryText)
                    } else {
                        Text("Resets \(reset.relative)")
                        Text(reset, format: .dateTime.weekday(.wide).hour().minute())
                            .foregroundStyle(Theme.Colors.secondaryText)
                    }
                }
                if let read = w?.readAt {
                    Text("Read \(read.relative)").foregroundStyle(Theme.Colors.secondaryText)
                }
            }
            .font(Theme.Fonts.caption)
        }
    }

    private func forecast(_ week: QuotaWindow) -> some View {
        VStack(alignment: .leading, spacing: Theme.Space.xs) {
            Text("Weekly forecast").font(Theme.Fonts.cardTitle)
            if let projected = week.projectedAtReset(now: model.now) {
                Text("At this pace: ~\(Int(projected.rounded()))% by reset")
                    .foregroundStyle(Theme.Colors.quota(projected, warn: model.quotaWarn, high: model.quotaHigh))
                if let out = week.runsOutAt(now: model.now) {
                    Text("Runs out \(out.relative) (\(out.formatted(.dateTime.weekday(.abbreviated).hour().minute())))")
                        .foregroundStyle(Theme.Colors.danger)
                } else {
                    Text("Lasts until the reset.").foregroundStyle(Theme.Colors.secondaryText)
                }
            } else {
                Text("Not enough of the week has passed to project.")
                    .foregroundStyle(Theme.Colors.secondaryText)
            }
        }
        .font(Theme.Fonts.caption)
    }
}

struct ReportView: View {
    @Environment(AppModel.self) private var model
    @AppStorage("reportDays") private var days = 7

    var body: some View {
        ScreenScaffold(title: "Report", subtitle: "Where the tokens went (relay usage). Context is re-sent with every call.") {
            HStack {
                Picker("Period", selection: $days) {
                    Text("7 days").tag(7)
                    Text("30 days").tag(30)
                }
                .pickerStyle(.segmented)
                .labelsHidden()
                .fixedSize()
                Button {
                    Task { await model.loadUsage(days: days, force: true) }
                } label: {
                    Image(systemName: "arrow.clockwise")
                }
                .help(Text("Reload"))
            }
        } content: {
            if model.usageLoading {
                HStack { ProgressView().controlSize(.small); Text("Reading transcripts…") }
            }
            if let error = model.usageError {
                Text(verbatim: error).foregroundStyle(Theme.Colors.danger)
            }
            if let report = model.usage[days] {
                ReportContent(report: report)
            }
        }
        .task(id: "\(days)-\(model.engine == .up)") {
            if model.engine == .up { await model.loadUsage(days: days) }
        }
    }
}

struct ReportContent: View {
    let report: UsageReport

    var body: some View {
        VStack(alignment: .leading, spacing: Theme.Space.l) {
            agentsTable
            buckets
            topSessions
        }
    }

    private var agentsTable: some View {
        Grid(alignment: .trailing, horizontalSpacing: Theme.Space.xl, verticalSpacing: Theme.Space.s) {
            GridRow {
                Text("Agent").gridColumnAlignment(.leading)
                Text("Calls")
                Text("Context re-sent")
                Text("Above \(Tokens.short(report.cap))")
                Text("Output")
            }
            .font(Theme.Fonts.caption)
            .foregroundStyle(Theme.Colors.secondaryText)
            ForEach(report.agents.keys.sorted(), id: \.self) { key in
                let a = report.agents[key]!
                GridRow {
                    Text(verbatim: Agent(rawValue: key)?.displayName ?? key)
                    Text(verbatim: "\(a.calls)")
                    Text(verbatim: Tokens.short(a.context))
                    Text(verbatim: "\(Tokens.short(a.overCap ?? 0)) (\(percent(a.overCap ?? 0, of: a.context)))")
                    Text(verbatim: Tokens.short(a.output ?? 0))
                }
                .font(Theme.Fonts.body.monospacedDigit())
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .cardStyle()
    }

    private var buckets: some View {
        VStack(alignment: .leading, spacing: Theme.Space.s) {
            Text("Tokens by context size of the call").font(Theme.Fonts.sectionTitle)
            Chart(report.orderedBuckets, id: \.label) { b in
                BarMark(x: .value("Context", b.label), y: .value("Tokens", b.tokens))
                    .annotation(position: .top) {
                        Text(verbatim: "\(b.calls) calls").font(Theme.Fonts.caption).foregroundStyle(Theme.Colors.secondaryText)
                    }
            }
            .chartYAxis {
                AxisMarks { v in
                    AxisGridLine()
                    AxisValueLabel { Text(verbatim: Tokens.short(v.as(Int.self) ?? 0)) }
                }
            }
            .frame(height: 200)
        }
        .cardStyle()
    }

    private var topSessions: some View {
        VStack(alignment: .leading, spacing: Theme.Space.s) {
            Text("Heaviest sessions since \(report.since)").font(Theme.Fonts.sectionTitle)
            ForEach(report.sessions, id: \.session) { s in
                HStack {
                    Text(verbatim: "\(Agent(rawValue: s.agent)?.displayName ?? s.agent) · \(((s.project ?? "?") as NSString).lastPathComponent)")
                        .lineLimit(1)
                    Text(verbatim: String(s.session.prefix(8))).font(Theme.Fonts.caption).foregroundStyle(Theme.Colors.secondaryText)
                    Spacer()
                    Text("\(s.calls) calls · peak \(Tokens.short(s.maxContext)) · \(Tokens.short(s.contextSum)) re-sent")
                        .monospacedDigit()
                    Text(verbatim: s.share.map { "\(Int(($0 * 100).rounded()))%" } ?? "")
                        .frame(width: 44, alignment: .trailing)
                        .font(Theme.Fonts.body.weight(.semibold).monospacedDigit())
                }
                Divider()
            }
        }
        .cardStyle()
    }

    private func percent(_ part: Int, of whole: Int) -> String {
        whole > 0 ? "\(Int((Double(part) / Double(whole) * 100).rounded()))%" : "–"
    }
}
