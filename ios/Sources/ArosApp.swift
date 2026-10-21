import SwiftUI

@main
@MainActor struct ArosApp: App {
    @State private var store = AppStore()
    @Environment(\.scenePhase) private var scenePhase
    var body: some Scene {
        WindowGroup {
            Group {
                if store.appLock.locked { LockScreen() }
                else { RootView() }
            }.environment(store)
                .task { await store.restoreSession() }
                .onChange(of: scenePhase) { _, phase in
                    if phase == .background { store.appLock.lock() }
                    else if phase == .active && store.connected && !store.appLock.locked { Task { await store.refreshRemote() } }
                }
        }
    }
}
