import AppKit
import SwiftUI
import OmegaCore

struct ComposerView: View {
    let appState: AppState
    @Bindable var viewModel: TaskViewModel
    @State private var mentionSelection = 0
    @State private var mentionDismissed = false

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            toolbar
            if !viewModel.attachments.isEmpty {
                attachmentStrip
            }
            if mentionVisible {
                mentionPopover
            }
            editor
            footer
        }
        .padding(12)
        .onDrop(of: [.fileURL], isTargeted: nil) { providers in
            Task { await ingestDrop(providers) }
            return true
        }
        .onChange(of: mentionQuery?.query) { _, _ in
            mentionSelection = 0
            mentionDismissed = false
        }
        .onChange(of: mentionQuery == nil) { _, isNil in
            if isNil { mentionDismissed = false }
        }
    }

    private var toolbar: some View {
        HStack(spacing: 8) {
            Picker("Mode", selection: $viewModel.mode) {
                ForEach(TaskMode.allCases, id: \.self) { mode in
                    Text(mode.rawValue.capitalized).tag(mode)
                }
            }
            .labelsHidden()
            .pickerStyle(.segmented)
            .frame(width: 220)
            .onChange(of: viewModel.mode) { _, newMode in
                Task { await viewModel.setMode(newMode) }
            }

            Menu {
                ForEach(appState.availableModels) { model in
                    Button {
                        Task { await viewModel.setModel(model.alias) }
                    } label: {
                        if model.alias == viewModel.selectedModel {
                            Label(model.alias, systemImage: "checkmark")
                        } else {
                            Text(model.alias)
                        }
                    }
                }
            } label: {
                Label(viewModel.selectedModel, systemImage: "cpu")
                    .font(.callout)
            }
            .menuStyle(.borderlessButton)
            .fixedSize()

            Spacer()

            Button("Cancel") {
                Task { await viewModel.cancelRun() }
            }
            .disabled(viewModel.task?.status != .running)
        }
    }

    private var attachmentStrip: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 6) {
                ForEach(viewModel.attachments) { item in
                    AttachmentChip(item: item) {
                        viewModel.removeAttachment(item.id)
                    }
                }
            }
        }
    }

    private var editor: some View {
        ZStack(alignment: .topLeading) {
            ComposerTextView(
                text: $viewModel.composerText,
                cursorUTF16: $viewModel.composerCursor,
                mentionOpen: mentionVisible,
                onSubmit: { Task { await viewModel.send() } },
                onFiles: { viewModel.attach(urls: $0) },
                onMentionKey: handleMentionKey
            )
            .frame(minHeight: 72, maxHeight: 180)

            if viewModel.composerText.isEmpty {
                Text("Message the agent…  @ to mention a file, drop or paste images")
                    .foregroundStyle(.secondary)
                    .font(.body)
                    .padding(.horizontal, 16)
                    .padding(.vertical, 16)
                    .allowsHitTesting(false)
            }
        }
        .background(Color.primary.opacity(0.04), in: RoundedRectangle(cornerRadius: 8))
        .overlay(
            RoundedRectangle(cornerRadius: 8)
                .strokeBorder(Color.primary.opacity(0.08), lineWidth: 1)
        )
    }

    private var footer: some View {
        HStack {
            Button {
                pickFiles()
            } label: {
                Image(systemName: "paperclip")
            }
            .buttonStyle(.plain)
            .help("Attach files")
            .foregroundStyle(.secondary)

            Text("Return newline · ⌘↩ send · drop or paste images")
                .font(.caption)
                .foregroundStyle(.tertiary)
            Spacer()
            Button {
                Task { await viewModel.send() }
            } label: {
                if viewModel.isSending {
                    ProgressView().controlSize(.small)
                } else {
                    Label("Send", systemImage: "paperplane.fill")
                }
            }
            .keyboardShortcut(.return, modifiers: .command)
            .buttonStyle(.borderedProminent)
            .disabled(isSendDisabled)
        }
    }

    private var mentionPopover: some View {
        VStack(alignment: .leading, spacing: 0) {
            ForEach(Array(mentionHits.enumerated()), id: \.element) { index, path in
                Button {
                    viewModel.insertMention(path)
                } label: {
                    HStack(spacing: 8) {
                        Image(systemName: "doc.text")
                            .foregroundStyle(.secondary)
                            .font(.caption)
                        Text((path as NSString).lastPathComponent)
                            .font(.callout.weight(.medium))
                        Spacer(minLength: 8)
                        Text(path)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .lineLimit(1)
                            .truncationMode(.middle)
                    }
                    .padding(.horizontal, 10)
                    .padding(.vertical, 6)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(index == mentionSelection ? Color.accentColor.opacity(0.18) : Color.clear)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
            }
            if mentionHits.isEmpty {
                Text(viewModel.workspaceFiles.isEmpty ? "Indexing workspace…" : "No matching files")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .padding(10)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 8))
        .overlay(
            RoundedRectangle(cornerRadius: 8)
                .strokeBorder(Color.primary.opacity(0.08), lineWidth: 1)
        )
    }

    private var mentionQuery: MentionQuery? {
        ComposerMentions.query(in: viewModel.composerText, cursorUTF16: viewModel.composerCursor)
    }

    private var mentionHits: [String] {
        guard let query = mentionQuery else { return [] }
        return ComposerMentions.rank(files: viewModel.workspaceFiles, query: query.query)
    }

    private var mentionVisible: Bool {
        mentionQuery != nil && !mentionDismissed
    }

    private var isSendDisabled: Bool {
        let prompt = ComposerPromptBuilder.build(
            text: viewModel.composerText,
            attachmentPaths: viewModel.attachments.map(\.displayPath)
        )
        return prompt.isEmpty || viewModel.isSending
    }

    private func handleMentionKey(_ key: ComposerMentionKey) -> Bool {
        guard mentionVisible else { return false }
        switch key {
        case .up:
            if !mentionHits.isEmpty {
                mentionSelection = (mentionSelection - 1 + mentionHits.count) % mentionHits.count
            }
            return true
        case .down:
            if !mentionHits.isEmpty {
                mentionSelection = (mentionSelection + 1) % mentionHits.count
            }
            return true
        case .accept:
            guard mentionHits.indices.contains(mentionSelection) else { return false }
            viewModel.insertMention(mentionHits[mentionSelection])
            return true
        case .cancel:
            mentionDismissed = true
            return true
        }
    }

    @MainActor
    private func ingestDrop(_ providers: [NSItemProvider]) async {
        var urls: [URL] = []
        for provider in providers {
            if let url = await withCheckedContinuation({ (cont: CheckedContinuation<URL?, Never>) in
                provider.loadItem(forTypeIdentifier: "public.file-url", options: nil) { item, _ in
                    if let data = item as? Data, let url = URL(dataRepresentation: data, relativeTo: nil) {
                        cont.resume(returning: url)
                    } else if let url = item as? URL {
                        cont.resume(returning: url)
                    } else {
                        cont.resume(returning: nil)
                    }
                }
            }) {
                urls.append(url)
            }
        }
        if !urls.isEmpty { viewModel.attach(urls: urls) }
    }

    private func pickFiles() {
        let panel = NSOpenPanel()
        panel.canChooseFiles = true
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = true
        panel.canCreateDirectories = false
        panel.message = "Attach files the agent can read"
        guard panel.runModal() == .OK else { return }
        viewModel.attach(urls: panel.urls)
    }
}

private struct AttachmentChip: View {
    let item: ComposerAttachment
    let onRemove: () -> Void

    var body: some View {
        HStack(spacing: 6) {
            if item.isImage, let nsImage = NSImage(contentsOfFile: item.absolutePath) {
                Image(nsImage: nsImage)
                    .resizable()
                    .aspectRatio(contentMode: .fill)
                    .frame(width: 22, height: 22)
                    .clipShape(RoundedRectangle(cornerRadius: 3))
            } else {
                Image(systemName: item.isImage ? "photo" : "doc")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Text((item.displayPath as NSString).lastPathComponent)
                .font(.caption)
                .lineLimit(1)
            Button(action: onRemove) {
                Image(systemName: "xmark")
                    .font(.caption2.weight(.semibold))
            }
            .buttonStyle(.plain)
            .foregroundStyle(.secondary)
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 5)
        .background(Color.primary.opacity(0.06), in: Capsule())
        .help(item.displayPath)
    }
}
