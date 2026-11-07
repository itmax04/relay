import RelayKit
import SwiftUI

/// "Continue in a new session": the handoff, the prompt that resumes from it, and launch buttons.
struct ContinueSheet: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    let target: ContinueTarget
    @State private var copied = false
    @State private var error: String?
    /// Looked up once, off the main thread: the walk up from cwd touches the file system.
    @State private var handoff: HandoffInfo?
    @State private var located = false

    private var session: Session? { target.session }
    private var suggested: Suggested? { target.advice?.suggested }
    /// The agent's own prompt wins over the one built from the handoff.
    private var prompt: String? { suggested?.prompt ?? handoff?.continuationPrompt }
    private var isNext: Bool { target.advice?.rule == "next" }
    private var ownAgent: Agent? { session.flatMap { Agent(rawValue: $0.agent) } ?? target.advice.flatMap { Agent(rawValue: $0.agent ?? "") } }

    var body: some View {
        VStack(alignment: .leading, spacing: Theme.Space.l) {
            HStack(alignment: .top, spacing: Theme.Space.m) {
                Image(systemName: Theme.Symbols.handoff).font(.largeTitle).foregroundStyle(.tint)
                VStack(alignment: .leading, spacing: Theme.Space.xs) {
                    Text("Continue in a new session").font(Theme.Fonts.sheetTitle)
                    handoffLine
                }
            }
            if let reason = target.advice?.reason {
                Text(verbatim: reason).foregroundStyle(Theme.Colors.secondaryText)
            }
            if let handoff {
                HStack(alignment: .top) {
                    Text(verbatim: prompt ?? handoff.continuationPrompt)
                        .font(Theme.Fonts.code)
                        .textSelection(.enabled)
                    Spacer()
                }
                .padding(Theme.Space.m)
                .background(Theme.Colors.codeBackground, in: RoundedRectangle(cornerRadius: Theme.Radius.card))
                .overlay(RoundedRectangle(cornerRadius: Theme.Radius.card).strokeBorder(Theme.Colors.cardBorder))
                if !handoff.exists && suggested?.prompt == nil {
                    Label("No handoff yet. Ask the current session to write one first (relay-handoff skill), then come back.",
                          systemImage: Theme.Symbols.warning)
                        .foregroundStyle(Theme.Colors.caution)
                }
            } else if located {
                Text("This session has no working directory, so relay cannot find its handoff.")
                    .foregroundStyle(Theme.Colors.secondaryText)
            } else {
                ProgressView().controlSize(.small)
            }
            suggestedRow
            if let error { Text(verbatim: error).foregroundStyle(Theme.Colors.danger) }
            Divider()
            HStack {
                copyButton
                launchButton("Open in Claude CLI", .claude)
                launchButton("Open in Codex", .codex)
                Spacer()
                Button("Cancel", role: .cancel) { close() }
                    .keyboardShortcut(.cancelAction)
            }
        }
        .padding(Theme.Space.xl)
        .frame(width: Theme.Size.sheetWidth)
        .task {
            let cwd = session?.cwd, path = model.handoffPath
            handoff = await Task.detached { AppModel.handoff(cwd: cwd, handoffPath: path) }.value
            located = true
        }
    }

    @ViewBuilder private var copyButton: some View {
        let button = Button {
            if let p = prompt { Clipboard.copy(p); copied = true }
        } label: {
            Label(copied ? "Copied" : "Copy prompt", systemImage: Theme.Symbols.copy)
        }
        if isNext {
            button.buttonStyle(.bordered).disabled(prompt == nil)
        } else {
            button.buttonStyle(.borderedProminent).disabled(prompt == nil)
        }
    }

    @ViewBuilder private func launchButton(_ title: LocalizedStringKey, _ agent: Agent) -> some View {
        if isNext && ownAgent == agent {
            Button(title) { launch(agent) }
                .buttonStyle(.borderedProminent)
                .keyboardShortcut(.defaultAction)
                .disabled(handoff == nil || prompt == nil)
        } else {
            Button(title) { launch(agent) }
                .disabled(handoff == nil || prompt == nil)
        }
    }

    @ViewBuilder private var handoffLine: some View {
        if let handoff {
            let path = (handoff.absolutePath as NSString).abbreviatingWithTildeInPath
            if let modified = handoff.modified {
                Text("Handoff updated \(modified.relative) · \(path)")
                    .foregroundStyle(Theme.Colors.secondaryText)
            } else {
                Text(verbatim: path).foregroundStyle(Theme.Colors.secondaryText)
            }
        }
    }

    @ViewBuilder private var suggestedRow: some View {
        if let s = suggested, s.model != nil || s.effort != nil {
            HStack(spacing: Theme.Space.s) {
                Image(systemName: Theme.Symbols.advisor).foregroundStyle(.tint)
                Text("Suggested configuration").font(Theme.Fonts.cardTitle)
                ModelChips(model: s.model, effort: s.effort)
            }
        } else if let session {
            HStack(spacing: Theme.Space.s) {
                Text("Current configuration").font(Theme.Fonts.cardTitle)
                ModelChips(model: session.model, effort: session.effort)
            }
        }
    }

    /// Opens Terminal in the project root with the agent and the prompt. The suggested model is
    /// passed only to the agent it belongs to.
    private func launch(_ agent: Agent) {
        guard let handoff, let prompt else { return }
        let sameAgent = session?.agent == agent.rawValue || target.advice?.agent == agent.rawValue
        let command = LaunchCommand.make(agent: agent, projectRoot: handoff.projectRoot, prompt: prompt,
                                         model: sameAgent ? suggested?.model : nil,
                                         effort: sameAgent ? suggested?.effort : nil)
        do {
            try TerminalLauncher.run(command)
            close()
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func close() {
        model.continueTarget = nil
        dismiss()
    }
}
