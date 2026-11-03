import RelayKit
import SwiftUI

/// Dispatcher runs (`relay run`). Starting a run from the app comes later: runs start in a terminal.
struct RunsView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        ScreenScaffold(title: "Runs",
                       subtitle: "Plans that relay run works through step by step, each in a fresh agent process.") {
            if model.engine == .up && model.runs.isEmpty {
                ContentUnavailableView {
                    Label("No runs yet", systemImage: Theme.Symbols.runs)
                } description: {
                    Text("Write .relay/plan.md in a project (or draft one with `relay plan \"<task>\"`), then start it with `relay run start` in that project.")
                }
            }
            LazyVStack(spacing: Theme.Space.m) {
                ForEach(model.runs) { RunCard(run: $0) }
            }
        }
    }
}

struct RunCard: View {
    @Environment(AppModel.self) private var model
    let run: Run
    var compact = false
    @State private var expanded: Bool?

    private var showSteps: Bool { expanded ?? run.status.isActive }

    var body: some View {
        VStack(alignment: .leading, spacing: Theme.Space.s) {
            HStack(alignment: .firstTextBaseline) {
                VStack(alignment: .leading, spacing: Theme.Space.xs) {
                    Text(verbatim: run.planTitle).font(Theme.Fonts.cardTitle).lineLimit(1)
                    Text(verbatim: compact ? run.projectName : "\(run.projectName) · \(run.branch)")
                        .font(Theme.Fonts.caption)
                        .foregroundStyle(Theme.Colors.secondaryText)
                        .lineLimit(1)
                        .truncationMode(.middle)
                }
                Spacer()
                Chip(text: run.status.rawValue)
                    .foregroundStyle(Theme.Colors.run(run.status))
            }
            ProgressView(value: Double(run.doneSteps), total: Double(max(run.steps.count, 1)))
                .tint(Theme.Colors.run(run.status))
            // The buttons move to their own row when the line would not fit next to them (popover).
            ViewThatFits(in: .horizontal) {
                HStack(spacing: Theme.Space.s) {
                    statusText.fixedSize()
                    Spacer()
                    controls
                }
                VStack(alignment: .leading, spacing: Theme.Space.s) {
                    statusText
                    HStack(spacing: Theme.Space.s) {
                        Spacer()
                        controls
                    }
                }
            }
            if let error = run.error, !run.status.isActive {
                Text(verbatim: error)
                    .font(Theme.Fonts.caption)
                    .foregroundStyle(run.status == .failed ? Theme.Colors.danger : Theme.Colors.secondaryText)
                    .lineLimit(compact ? 2 : 6)
                    .textSelection(.enabled)
            }
            if !compact {
                DisclosureGroup(isExpanded: Binding(get: { showSteps }, set: { expanded = $0 })) {
                    VStack(alignment: .leading, spacing: Theme.Space.s) {
                        ForEach(run.steps) { StepRow(step: $0) }
                    }
                    .padding(.top, Theme.Space.s)
                } label: {
                    Text("\(run.doneSteps) of \(run.steps.count) steps done")
                        .font(Theme.Fonts.caption)
                        .foregroundStyle(Theme.Colors.secondaryText)
                }
            }
        }
        .cardStyle(accent: run.status == .waiting && run.gate?.needsOwner == true ? Theme.Colors.caution : nil)
    }

    private var statusText: some View {
        Text(verbatim: run.statusLine)
            .font(Theme.Fonts.body.monospacedDigit())
            .lineLimit(1)
    }

    @ViewBuilder private var controls: some View {
        if run.status == .waiting, let gate = run.gate, gate.needsOwner {
            Button {
                Task { await model.approveRun(run) }
            } label: {
                Label(gate.kind == "plan" ? "Approve plan" : "Approve step", systemImage: Theme.Symbols.approve)
            }
            .controlSize(.small)
            .buttonStyle(.borderedProminent)
        }
        if run.status.canResume {
            Button {
                Task { await model.resumeRun(run) }
            } label: {
                Label("Resume", systemImage: Theme.Symbols.resume)
            }
            .controlSize(.small)
            .help(Text("Continues from the last passed step in the same worktree. Unfinished changes of the failed step are discarded."))
        }
        if run.status.isActive {
            Button(role: .destructive) {
                Task { await model.stopRun(run) }
            } label: {
                Label("Stop", systemImage: Theme.Symbols.stop)
            }
            .controlSize(.small)
            .help(Text("Stops the run and its agent process. Commits made so far stay on the run's branch."))
        }
    }
}

private struct StepRow: View {
    let step: RunStep

    var body: some View {
        VStack(alignment: .leading, spacing: Theme.Space.xs) {
            HStack(alignment: .firstTextBaseline, spacing: Theme.Space.s) {
                Image(systemName: Theme.Symbols.step(step.status))
                    .foregroundStyle(Theme.Colors.step(step.status))
                Text(verbatim: "\(step.n). \(step.title)")
                    .lineLimit(1)
                Spacer()
                if step.confirm == true { Chip(text: "confirm") }
                Chip(text: step.tier)
                if let commit = step.commit { Chip(text: commit, symbol: Theme.Symbols.branch) }
            }
            if !step.attempts.isEmpty {
                Text(verbatim: step.attempts.map { a in
                    let model = a.model.map(ModelName.display) ?? "?"
                    return "\(model)\(a.effort.map { " \($0)" } ?? ""): \(a.result ?? "running")"
                }.joined(separator: " → "))
                .font(Theme.Fonts.caption)
                .foregroundStyle(Theme.Colors.secondaryText)
                .lineLimit(2)
            }
            if let error = step.attempts.last?.error, step.status != "done" {
                Text(verbatim: error)
                    .font(Theme.Fonts.caption)
                    .foregroundStyle(Theme.Colors.caution)
                    .lineLimit(4)
                    .textSelection(.enabled)
            }
            if let summary = step.summary {
                Text(verbatim: summary)
                    .font(Theme.Fonts.caption)
                    .lineLimit(3)
                    .textSelection(.enabled)
            }
        }
        .padding(.leading, Theme.Space.xs)
    }
}
