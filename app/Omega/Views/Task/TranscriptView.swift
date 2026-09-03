import SwiftUI
import OmegaCore

struct TranscriptView: View {
    let viewModel: TaskViewModel

    private var transcript: TranscriptModel { viewModel.transcript }

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 8) {
                    ForEach(transcript.blocks) { block in
                        TranscriptBlockView(block: block, viewModel: viewModel)
                            .id(block.id)
                    }
                    Color.clear
                        .frame(height: 1)
                        .id("transcript-bottom")
                }
                .padding(12)
            }
            .onChange(of: transcript.blocks.count) {
                scrollToBottom(proxy)
            }
            .onChange(of: lastAssistantTextLength) {
                scrollToBottom(proxy)
            }
            .onAppear {
                scrollToBottom(proxy, animated: false)
            }
        }
    }

    private func scrollToBottom(_ proxy: ScrollViewProxy, animated: Bool = true) {
        if animated {
            withAnimation(.easeOut(duration: 0.15)) {
                proxy.scrollTo("transcript-bottom", anchor: .bottom)
            }
        } else {
            proxy.scrollTo("transcript-bottom", anchor: .bottom)
        }
    }

    private var lastAssistantTextLength: Int {
        guard case .assistantText(let block) = transcript.blocks.last else { return 0 }
        return block.text.count
    }
}

struct TranscriptBlockView: View {
    let block: TranscriptBlock
    let viewModel: TaskViewModel

    var body: some View {
        switch block {
        case .userPrompt(let b):
            UserPromptBlockView(block: b)
        case .assistantText(let b):
            AssistantTextBlockView(block: b)
        case .toolGroup(let b):
            ToolGroupBlockView(block: b, viewModel: viewModel)
        case .systemLine(let b):
            SystemLineBlockView(block: b)
        case .askUser(let b):
            AskUserCardView(block: b, viewModel: viewModel)
        case .confirm(let b):
            ConfirmCardView(block: b, viewModel: viewModel)
        }
    }
}

struct UserPromptBlockView: View {
    let block: UserPromptBlock

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            Text("› \(block.text)")
                .font(.body)
                .textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading)

            VStack(alignment: .trailing, spacing: 2) {
                Text(block.mode.capitalized)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                Text(block.timestamp, format: .dateTime.hour().minute())
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
        }
        .padding(8)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.accentColor.opacity(0.08), in: RoundedRectangle(cornerRadius: 6))
    }
}

struct AssistantTextBlockView: View {
    let block: AssistantTextBlock

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            markdownText(block.text)
                .textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading)

            if !block.finalized {
                HStack(spacing: 4) {
                    ProgressView()
                        .controlSize(.mini)
                    Text("typing…")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
            }
        }
    }

    private func markdownText(_ raw: String) -> Text {
        let options = AttributedString.MarkdownParsingOptions(interpretedSyntax: .inlineOnlyPreservingWhitespace)
        if let attributed = try? AttributedString(markdown: raw, options: options) {
            return Text(attributed)
        }
        return Text(raw)
    }
}

struct SystemLineBlockView: View {
    let block: SystemLineBlock

    var body: some View {
        Text(block.text)
            .font(.system(.caption, design: .monospaced))
            .foregroundStyle(block.isError ? .red : .secondary)
            .frame(maxWidth: .infinity, alignment: .leading)
    }
}
