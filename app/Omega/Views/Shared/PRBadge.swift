import SwiftUI
import OmegaCore

struct CIStatusGlyph: View {
    var status: CIStatus

    var body: some View {
        switch status {
        case .passing:
            Image(systemName: "checkmark.circle.fill")
                .foregroundStyle(.green)
        case .failing:
            Image(systemName: "xmark.circle.fill")
                .foregroundStyle(.red)
        case .pending:
            Image(systemName: "circle.dotted")
                .foregroundStyle(.secondary)
        case .none:
            EmptyView()
        }
    }
}

struct PRBadge: View {
    var pr: PullRequest

    var body: some View {
        HStack(spacing: 3) {
            Text("#\(pr.number)")
                .font(.system(size: 10, weight: .medium))
            CIStatusGlyph(status: pr.checks)
                .font(.system(size: 10))
        }
        .foregroundStyle(.secondary)
    }
}
