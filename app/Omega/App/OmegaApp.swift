import SwiftUI

@main
struct OmegaApp: App {
    @State private var appState = AppState()

    var body: some Scene {
        WindowGroup {
            RootView(appState: appState)
                .frame(minWidth: 900, minHeight: 560)
                .task { await appState.start() }
        }
        .commands {
            CommandGroup(after: .newItem) {
                Button("New Task…") {
                    NotificationCenter.default.post(name: .omegaShowNewTaskSheet, object: nil)
                }
                .keyboardShortcut("n", modifiers: .command)
            }
            CommandGroup(after: .toolbar) {
                Button("Overview") { appState.selection = .overview }
                    .keyboardShortcut("0", modifiers: .command)
            }
        }

        Settings {
            SettingsView(settings: appState.settings)
                .frame(width: 420, height: 320)
        }
    }
}

extension Notification.Name {
    static let omegaShowNewTaskSheet = Notification.Name("omegaShowNewTaskSheet")
}
