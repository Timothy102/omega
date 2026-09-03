import SwiftUI

struct SettingsView: View {
    @Bindable var settings: AppSettings

    var body: some View {
        Form {
            Section("Daemon") {
                Stepper(value: $settings.daemonPort, in: 1...65535) {
                    HStack {
                        Text("Port")
                        Spacer()
                        Text("\(settings.daemonPort)")
                            .foregroundStyle(.secondary)
                            .monospacedDigit()
                    }
                }
            }

            Section("Tasks") {
                TextField("Default model", text: $settings.defaultModel)
            }

            Section("Notifications") {
                Toggle("Notify when a task needs input or finishes", isOn: $settings.notificationsEnabled)
            }

            Section("Terminals") {
                Toggle("Open terminals in an external app", isOn: $settings.terminalsOpenExternally)
                Text(settings.terminalsOpenExternally
                     ? "Terminals launch in your default terminal app."
                     : "Terminals open inline in Omega.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .formStyle(.grouped)
        .padding(20)
    }
}
