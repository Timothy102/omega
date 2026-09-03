import SwiftUI
import OmegaCore

enum TaskStatusStyle {
    static func color(for status: TaskStatus) -> Color {
        switch status {
        case .running: return .green
        case .waitingInput: return .orange
        case .idle, .done: return .gray
        case .failed: return .red
        }
    }
}

struct StatusDot: View {
    var status: TaskStatus
    var size: CGFloat = 8

    @State private var isDim = false

    var body: some View {
        Circle()
            .fill(TaskStatusStyle.color(for: status))
            .frame(width: size, height: size)
            .opacity(status == .waitingInput && isDim ? 0.45 : 1)
            .onAppear {
                guard status == .waitingInput else { return }
                withAnimation(.easeInOut(duration: 1.2).repeatForever(autoreverses: true)) {
                    isDim = true
                }
            }
    }
}
