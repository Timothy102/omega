import Foundation
import OmegaCore

enum DaemonStatus: Equatable {
    case checking
    case running
    case notInstalled
    case failed(String)
}

@MainActor
final class DaemonLauncher {
    private(set) var process: Process?

    func launch(port: Int) async -> (DaemonStatus, OmegaClient?) {
        if let config = try? ServeConfig.load() {
            let client = OmegaClient(config: config)
            if (try? await client.health()) == true {
                return (.running, client)
            }
        }
        guard let omegaPath = Self.findOmegaExecutable() else {
            return (.notInstalled, nil)
        }
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        process.arguments = ["omega", "serve", "--port", String(port)]
        process.environment = Self.environmentWithLocalBin()
        process.currentDirectoryURL = FileManager.default.homeDirectoryForCurrentUser
        do {
            try process.run()
            self.process = process
        } catch {
            return (.failed("Couldn't launch \(omegaPath): \(error.localizedDescription)"), nil)
        }

        for _ in 0..<60 {
            try? await Task.sleep(nanoseconds: 250_000_000)
            if let config = try? ServeConfig.load() {
                let client = OmegaClient(config: config)
                if (try? await client.health()) == true {
                    return (.running, client)
                }
            }
        }
        return (.failed("omega serve did not report healthy within 15s"), nil)
    }

    func stop() {
        process?.terminate()
        process = nil
    }

    static func findOmegaExecutable() -> String? {
        let home = NSHomeDirectory()
        let candidates = [
            "\(home)/.local/bin/omega",
            "/opt/homebrew/bin/omega",
            "/usr/local/bin/omega",
        ]
        for path in candidates where FileManager.default.isExecutableFile(atPath: path) {
            return path
        }
        if let pathEnv = ProcessInfo.processInfo.environment["PATH"] {
            for dir in pathEnv.split(separator: ":") {
                let candidate = "\(dir)/omega"
                if FileManager.default.isExecutableFile(atPath: candidate) {
                    return candidate
                }
            }
        }
        return nil
    }

    private static func environmentWithLocalBin() -> [String: String] {
        var env = ProcessInfo.processInfo.environment
        let localBin = "\(NSHomeDirectory())/.local/bin"
        let existing = env["PATH"] ?? ""
        if !existing.contains(localBin) {
            env["PATH"] = "\(localBin):\(existing)"
        }
        return env
    }

    static let installCommand = "uv tool install omega-code"
}
