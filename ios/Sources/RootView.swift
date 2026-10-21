import SwiftUI

struct RootView: View {
    @Environment(AppStore.self) private var store
    @Environment(\.accessibilityReduceTransparency) private var opaque
    var body: some View {
        @Bindable var store = store
        NavigationStack(path: $store.path) {
            Group {
                switch store.selectedTab {
                case .today: HomeView()
                case .learning: areaView(.learning)
                case .events: areaView(.events)
                case .ideas: IdeasView()
                case .server: areaView(.server)
                }
            }
            .navigationDestination(for: Destination.self) { destination in
                switch destination {
                case .area(let area): areaView(area)
                case .inbox: if store.connected { RemoteInboxView() } else { InboxView() }
                case .weather: WeatherView()
                case .search: if store.connected { ResourceListView(area: nil, title: "Buscar") } else { GlobalSearchView() }
                case .settings: BackendSettingsView()
                case .workspace: ResourceListView(area: nil)
                case .calendar: if store.connected { RemoteCalendarView() } else { ResourceListView(area: nil, kinds: ["event"], title: "Calendario") }
                case .files: ResourceListView(area: nil, kinds: ["document"], title: "Archivos")
                case .connections: ConnectionsView()
                case .automations: AutomationsView()
                case .resource(let id): ResourceDetailView(id: id)
                }
            }
            .background { ArosBackground() }
            .safeAreaInset(edge: .bottom, spacing: 0) {
                VStack(spacing: 8) {
                    AssistantDock()
                    BottomNavigation()
                }.padding(.horizontal, ArosTheme.horizontalInset).padding(.top, 12).padding(.bottom, 8)
                    .background {
                        if opaque {
                            ArosTheme.canvas.ignoresSafeArea(edges: .bottom)
                        } else {
                            LinearGradient(colors: [ArosTheme.canvas.opacity(0), ArosTheme.canvas.opacity(0.98)], startPoint: .top, endPoint: .bottom)
                                .ignoresSafeArea(edges: .bottom)
                        }
                    }
            }
        }.tint(ArosTheme.blue)
            .sheet(isPresented: $store.chatPresented) {
                ChatView(launch: store.chatLaunch)
                    .presentationDetents([.large])
                    .presentationDragIndicator(.visible)
            }
            .preferredColorScheme(.light)
            .alert("Aros", isPresented: Binding(get: { store.resourceError != nil }, set: { if !$0 { store.resourceError = nil } })) {
                Button("Entendido", role: .cancel) { store.resourceError = nil }
            } message: { Text(store.resourceError ?? "") }
    }
    @ViewBuilder private func areaView(_ area: Area) -> some View {
        if store.connected {
            if area == .server { RemoteServerView() }
            else if area == .resources { ResourceListView(area: .resources) }
            else if area == .events { EventsStudioView() }
            else if area == .technology { TechnologyStudioView() }
            else { ResourceListView(area: area) }
        } else { AreaDetailView(area: area) }
    }

}

#if DEBUG
@MainActor struct ArosPreviews: PreviewProvider {
    static var previews: some View {
        Group {
            RootView().environment(AppStore()).previewDisplayName("Inicio")
            RootView().environment(AppStore()).environment(\.dynamicTypeSize, .accessibility3)
                .previewDisplayName("Texto accesible")
        }
    }
}
#endif
