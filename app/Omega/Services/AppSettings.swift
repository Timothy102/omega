import Foundation

@MainActor
@Observable
final class AppSettings {
    private enum Keys {
        static let daemonPort = "daemonPort"
        static let defaultModel = "defaultModel"
        static let notificationsEnabled = "notificationsEnabled"
        static let terminalsOpenExternally = "terminalsOpenExternally"
        static let recentRepoPaths = "recentRepoPaths"
    }

    private let defaults: UserDefaults

    var daemonPort: Int {
        didSet { defaults.set(daemonPort, forKey: Keys.daemonPort) }
    }
    var defaultModel: String {
        didSet { defaults.set(defaultModel, forKey: Keys.defaultModel) }
    }
    var notificationsEnabled: Bool {
        didSet { defaults.set(notificationsEnabled, forKey: Keys.notificationsEnabled) }
    }
    var terminalsOpenExternally: Bool {
        didSet { defaults.set(terminalsOpenExternally, forKey: Keys.terminalsOpenExternally) }
    }
    private(set) var recentRepoPaths: [String] {
        didSet { defaults.set(recentRepoPaths, forKey: Keys.recentRepoPaths) }
    }

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        daemonPort = defaults.object(forKey: Keys.daemonPort) as? Int ?? 7777
        defaultModel = defaults.string(forKey: Keys.defaultModel) ?? "opus"
        notificationsEnabled = defaults.object(forKey: Keys.notificationsEnabled) as? Bool ?? true
        terminalsOpenExternally = defaults.object(forKey: Keys.terminalsOpenExternally) as? Bool ?? false
        recentRepoPaths = defaults.stringArray(forKey: Keys.recentRepoPaths) ?? []
    }

    func noteRecentRepo(_ path: String) {
        var paths = recentRepoPaths.filter { $0 != path }
        paths.insert(path, at: 0)
        recentRepoPaths = Array(paths.prefix(10))
    }
}
