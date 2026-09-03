import SwiftUI
import OmegaCore

enum InspectorTab: String, CaseIterable {
    case session = "Session"
    case git = "Git"
    case connections = "Connections"
    case artifacts = "Artifacts"
}

struct InspectorView: View {
    let appState: AppState
    let viewModel: TaskViewModel

    @State private var tab: InspectorTab = .session

    var body: some View {
        VStack(spacing: 0) {
            Picker("", selection: $tab) {
                ForEach(InspectorTab.allCases, id: \.self) { t in
                    Text(t.rawValue).tag(t)
                }
            }
            .labelsHidden()
            .pickerStyle(.segmented)
            .padding(8)

            Divider()

            Group {
                switch tab {
                case .session:
                    InspectorSessionTab(viewModel: viewModel)
                case .git:
                    InspectorGitTab(appState: appState, viewModel: viewModel)
                case .connections:
                    InspectorConnectionsTab(appState: appState)
                case .artifacts:
                    InspectorArtifactsTab(appState: appState, viewModel: viewModel)
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        }
    }
}
