import SwiftUI
import OmegaCore

struct AskUserCardView: View {
    let block: AskUserCardBlock
    let viewModel: TaskViewModel

    @State private var customText: String = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if let header = block.request.header {
                Text(header)
                    .font(.caption)
                    .fontWeight(.semibold)
                    .foregroundStyle(.secondary)
            }
            Text(block.request.question)
                .font(.body)

            if block.resolved {
                Label(block.chosen.joined(separator: ", "), systemImage: "checkmark.circle.fill")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            } else {
                VStack(alignment: .leading, spacing: 6) {
                    ForEach(block.request.options, id: \.label) { option in
                        Button {
                            Task { await viewModel.answerAskUser(requestId: block.request.requestId, chosen: [option.label]) }
                        } label: {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(option.label)
                                    .fontWeight(.medium)
                                if let description = option.description {
                                    Text(description)
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                }
                            }
                            .frame(maxWidth: .infinity, alignment: .leading)
                        }
                        .buttonStyle(.bordered)
                    }

                    if block.request.multiSelect {
                        HStack {
                            TextField("Custom answer", text: $customText)
                                .textFieldStyle(.roundedBorder)
                                .onSubmit { submitCustom() }
                            Button("Send") { submitCustom() }
                                .disabled(customText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                        }
                    }
                }
            }
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.yellow.opacity(0.08), in: RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.yellow.opacity(0.35), lineWidth: 1))
    }

    private func submitCustom() {
        let trimmed = customText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        Task { await viewModel.answerAskUser(requestId: block.request.requestId, chosen: [trimmed]) }
        customText = ""
    }
}
