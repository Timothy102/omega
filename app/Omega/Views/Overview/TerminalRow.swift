import SwiftUI
import Foundation
import OmegaCore

struct TerminalRow: View {
    var terminal: Terminal
    var taskName: String

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: "terminal")
                .foregroundStyle(.secondary)

            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    Text(taskName)
                        .font(.subheadline.weight(.medium))
                    Text(tildify(terminal.cwd))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                if let lastLine = terminal.lastLine, !lastLine.isEmpty {
                    Text(lastLine)
                        .font(.system(.caption, design: .monospaced))
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                        .truncationMode(.tail)
                }
            }

            Spacer()
        }
        .padding(.vertical, 6)
    }

    private func tildify(_ path: String) -> String {
        let home = NSHomeDirectory()
        guard path.hasPrefix(home) else { return path }
        return "~" + path.dropFirst(home.count)
    }
}
