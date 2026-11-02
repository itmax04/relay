import RelayKit
import SwiftUI

/// "Continue in a new session": the handoff, the prompt that resumes from it, and launch buttons.
struct ContinueSheet: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    let target: ContinueTarget
    @State private var copied = false
    @State private var error: String?

    private var session: Session? { target.session }
    private var handoff: HandoffInfo? { session.flatMap(model.handoff(for:)) }
    private var suggested: Suggested? { target.advice?.suggested }

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
                    Text(verbatim: handoff.continuationPrompt)
                        .font(Theme.Fonts.code)
                        .textSelection(.enabled)
                    Spacer()
                }
                .padding(Theme.Space.m)
                .background(Theme.Colors.codeBackground, in: RoundedRectangle(cornerRadius: Theme.Radius.card))
                .overlay(RoundedRectangle(cornerRadius: Theme.Radius.card).strokeBorder(Theme.Colors.cardBorder))
                if !handoff.exists {
                    Label("No handoff yet. Ask the current session to write one first (relay-handoff skill), then come back.",
                          systemImage: Theme.Symbols.warning)
                        .foregroundStyle(Theme.Colors.caution)
                }
            } else {
                Text("This session has no working directory, so relay cannot find its handoff.")
                    .foregroundStyle(Theme.Colors.secondaryText)
            }
            suggestedRow
            if let error { Text(verbatim: error).foregroundStyle(Theme.Colors.danger) }
            Divider()
            HStack {
                Button {
                    if let p = handoff?.continuationPrompt { Clipboard.copy(p); copied = true }
                } label: {
                    Label(copied ? "Copied" : "Copy prompt", systemImage: Theme.Symbols.copy)
                }
                .buttonStyle(.borderedProminent)
                .disabled(handoff == nil)
                Button("Open in Claude CLI") { launch(.claude) }
                    .disabled(handoff == nil)
                Button("Open in Codex") { launch(.codex) }
                    .disabled(handoff == nil)
                Spacer()
                Button("Cancel", role: .cancel) { close() }
                    .keyboardShortcut(.cancelAction)
            }
        }
        .padding(Theme.Space.xl)
        .frame(width: Theme.Size.sheetWidth)
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
        guard let handoff else { return }
        let sameAgent = session?.agent == agent.rawValue || target.advice?.agent == agent.rawValue
        let command = LaunchCommand.make(agent: agent, projectRoot: handoff.projectRoot, prompt: handoff.continuationPrompt,
                                         model: sameAgent ? suggested?.model : nil,
                                         effort: sameAgent && agent == .codex ? suggested?.effort : nil)
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
