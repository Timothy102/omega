import SwiftUI
import OmegaCore

enum ConnectionStyle {
    static func color(for state: ConnectionState) -> Color {
        switch state {
        case .connected: return .green
        case .configured: return .secondary
        case .needsAuth: return .yellow
        case .error: return .red
        case .disabled: return .secondary
        }
    }
}
