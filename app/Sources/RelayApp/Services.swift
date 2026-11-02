import AppKit
import Foundation
import RelayKit
import Security

/// Groq keys in the login Keychain, service `relay.groq`, one item per account. The engine reads
/// them with `/usr/bin/security find-generic-password`, so that tool is put on each item's access
/// list next to this app. The app itself never reads a key back.
enum GroqKeychain {
    static let service = "relay.groq"

    static func accounts() -> [String] {
        let query: [CFString: Any] = [kSecClass: kSecClassGenericPassword, kSecAttrService: service,
                                      kSecMatchLimit: kSecMatchLimitAll, kSecReturnAttributes: true]
        var result: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess,
              let items = result as? [[CFString: Any]] else { return [] }
        return items.compactMap { $0[kSecAttrAccount] as? String }.sorted()
    }

    static func add(account: String, key: String) throws {
        let account = account.trimmingCharacters(in: .whitespaces)
        let key = key.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !account.isEmpty, !key.isEmpty else { throw AppError("Account and key are required.") }
        try? delete(account: account)
        var query: [CFString: Any] = [kSecClass: kSecClassGenericPassword, kSecAttrService: service,
                                      kSecAttrAccount: account, kSecAttrLabel: "\(service) (\(account))",
                                      kSecValueData: Data(key.utf8)]
        if let access = makeAccess() { query[kSecAttrAccess] = access }
        let status = SecItemAdd(query as CFDictionary, nil)
        guard status == errSecSuccess else { throw AppError(status) }
    }

    static func delete(account: String) throws {
        let query: [CFString: Any] = [kSecClass: kSecClassGenericPassword, kSecAttrService: service,
                                      kSecAttrAccount: account]
        let status = SecItemDelete(query as CFDictionary)
        if status == errSecSuccess || status == errSecItemNotFound { return }
        // Items made by another tool (e.g. `security add-generic-password` in Terminal) may not list
        // this app; the security tool can always remove its own.
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/security")
        p.arguments = ["delete-generic-password", "-s", service, "-a", account]
        try p.run()
        p.waitUntilExit()
        guard p.terminationStatus == 0 else { throw AppError(status) }
    }

    /// Access list: this app and /usr/bin/security (the engine's reader). Legacy API, but it is the
    /// only way to share a login-keychain item with a command-line tool without a prompt per read.
    private static func makeAccess() -> SecAccess? {
        var me: SecTrustedApplication?
        var tool: SecTrustedApplication?
        guard SecTrustedApplicationCreateFromPath(nil, &me) == errSecSuccess,
              SecTrustedApplicationCreateFromPath("/usr/bin/security", &tool) == errSecSuccess,
              let me, let tool else { return nil }
        var access: SecAccess?
        guard SecAccessCreate("relay Groq key" as CFString, [me, tool] as CFArray, &access) == errSecSuccess else {
            return nil
        }
        return access
    }
}

struct AppError: LocalizedError {
    let message: String
    init(_ message: String) { self.message = message }
    init(_ status: OSStatus) {
        message = (SecCopyErrorMessageString(status, nil) as String?) ?? "Keychain error \(status)"
    }
    var errorDescription: String? { message }
}

/// Starts `relay watch` from the repository checkout when the API does not answer.
enum EngineLauncher {
    static let defaultRepo = (NSHomeDirectory() as NSString).appendingPathComponent("Developer/relay")
    static var logPath: String {
        (NSHomeDirectory() as NSString).appendingPathComponent(".local/state/relay/watch.log")
    }

    static func script(repo: String) -> String { (repo as NSString).appendingPathComponent("bin/relay") }

    /// Runs detached from the app (it keeps advising when the app quits); output goes to watch.log.
    static func start(repo: String) throws {
        let script = script(repo: repo)
        guard FileManager.default.isExecutableFile(atPath: script) else {
            throw AppError("relay not found at \(script). Set the repository path in Settings.")
        }
        try FileManager.default.createDirectory(atPath: (logPath as NSString).deletingLastPathComponent,
                                                withIntermediateDirectories: true)
        if !FileManager.default.fileExists(atPath: logPath) {
            FileManager.default.createFile(atPath: logPath, contents: nil)
        }
        let log = try FileHandle(forWritingTo: URL(fileURLWithPath: logPath))
        log.seekToEndOfFile()
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        p.arguments = ["python3", script, "watch"]
        p.currentDirectoryURL = URL(fileURLWithPath: repo)
        var env = ProcessInfo.processInfo.environment
        env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        env["PYTHONUNBUFFERED"] = "1"
        p.environment = env
        p.standardOutput = log
        p.standardError = log
        p.standardInput = FileHandle.nullDevice
        try p.run()
    }

    static func stop(pid: Int) {
        kill(pid_t(pid), SIGTERM)
    }
}

/// Opens Terminal with a command, through a temporary `.command` file (no Apple Events permission).
enum TerminalLauncher {
    static func run(_ command: String) throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("relay-launch", isDirectory: true)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let file = dir.appendingPathComponent("relay-\(UUID().uuidString.prefix(8)).command")
        // The file removes itself, then replaces the shell with the agent.
        let body = "#!/bin/zsh -l\nrm -f \(ShellQuote.quote(file.path))\n\(command)\n"
        try body.write(to: file, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: file.path)
        let terminal = URL(fileURLWithPath: "/System/Applications/Utilities/Terminal.app")
        NSWorkspace.shared.open([file], withApplicationAt: terminal, configuration: NSWorkspace.OpenConfiguration())
    }
}

enum Clipboard {
    static func copy(_ s: String) {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(s, forType: .string)
    }
}
