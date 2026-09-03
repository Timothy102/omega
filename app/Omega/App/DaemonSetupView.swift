import AppKit
import SwiftUI

struct DaemonSetupView: View {
    let status: DaemonStatus
    let onRetry: () async -> Void

    @State private var copied = false

    var body: some View {
        VStack(spacing: 16) {
            Image(systemName: "terminal")
                .font(.system(size: 40))
                .foregroundStyle(.secondary)

            switch status {
            case .notInstalled:
                Text("omega isn't installed")
                    .font(.title2.bold())
                Text("Install the omega CLI, then Omega will launch its daemon automatically.")
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                installCommandRow
            case .failed(let message):
                Text("Couldn't reach the omega daemon")
                    .font(.title2.bold())
                Text(message)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
            default:
                EmptyView()
            }

            Button("Retry") { Task { await onRetry() } }
                .keyboardShortcut(.defaultAction)
        }
        .padding(40)
        .frame(maxWidth: 480, maxHeight: .infinity, alignment: .center)
        .frame(maxWidth: .infinity)
    }

    private var installCommandRow: some View {
        HStack {
            Text(DaemonLauncher.installCommand)
                .font(.system(.body, design: .monospaced))
                .padding(.horizontal, 12)
                .padding(.vertical, 8)
                .background(.quaternary, in: RoundedRectangle(cornerRadius: 6))

            Button(copied ? "Copied" : "Copy") {
                let pasteboard = NSPasteboard.general
                pasteboard.clearContents()
                pasteboard.setString(DaemonLauncher.installCommand, forType: .string)
                copied = true
                Task {
                    try? await Task.sleep(nanoseconds: 1_500_000_000)
                    copied = false
                }
            }
        }
    }
}
