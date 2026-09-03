import SwiftUI

struct RootView: View {
    @Bindable var appState: AppState

    var body: some View {
        switch appState.daemonStatus {
        case .checking:
            ProgressView("Connecting to omega…")
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        case .notInstalled, .failed:
            DaemonSetupView(status: appState.daemonStatus) {
                await appState.retryConnection()
            }
        case .running:
            NavigationSplitView {
                SidebarView(appState: appState)
            } detail: {
                switch appState.selection {
                case .overview:
                    OverviewView(appState: appState)
                case .task(let id):
                    TaskDetailView(appState: appState, taskId: id)
                        .id(id)
                }
            }
        }
    }
}
