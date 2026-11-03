import AppKit
import RelayKit
import SwiftUI

@main
struct RelayApp: App {
    @State private var model = AppModel()

    var body: some Scene {
        MenuBarExtra {
            MenuBarView()
                .environment(model)
        } label: {
            MenuBarLabel(model: model)
        }
        .menuBarExtraStyle(.window)

        Window("relay", id: "main") {
            MainWindow()
                .environment(model)
                .frame(minWidth: Theme.Size.windowMin.width, minHeight: Theme.Size.windowMin.height)
        }
        .defaultSize(width: 1000, height: 680)

        // Popover content in a normal window, for screenshots and checks (`--popover`).
        Window("relay menu", id: "popover") {
            MenuBarView()
                .environment(model)
        }
        .windowResizability(.contentSize)

        Settings {
            SettingsView()
                .environment(model)
                .frame(width: 640, height: 640)
        }
    }
}

/// Menu-bar icon: advice severity, or a crossed bolt while the engine is down.
struct MenuBarLabel: View {
    let model: AppModel
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        let symbol = model.engine == .up ? Theme.Symbols.severity(model.topSeverity) : Theme.Symbols.engineDown
        Image(systemName: symbol)
            .task {
                model.start()
                openRequestedWindows()
            }
            .accessibilityLabel(Text("relay"))
    }

    /// Launch arguments: `--show [now|suggestions|runs|quotas|report|settings]` opens the main window
    /// on that screen (`--continue [cwd-part]` also opens the Continue sheet); `--popover` shows the menu-bar popover content in a window.
    private func openRequestedWindows() {
        let args = CommandLine.arguments
        if let i = args.firstIndex(of: "--show") {
            if i + 1 < args.count, let screen = Screen(rawValue: args[i + 1]) {
                UserDefaults.standard.set(screen.rawValue, forKey: "launchScreen")
            }
            openWindow(id: "main")
            WindowRouting.bringToFront()
        }
        if args.contains("--popover") {
            openWindow(id: "popover")
            WindowRouting.bringToFront()
        }
    }
}

enum WindowRouting {
    @MainActor static func bringToFront() {
        NSApp.activate(ignoringOtherApps: true)
    }
}
