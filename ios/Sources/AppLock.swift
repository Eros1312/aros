import SwiftUI
import Observation
import LocalAuthentication

@MainActor @Observable final class AppLock {
    var enabled = UserDefaults.standard.bool(forKey: "aros.appLock")
    var locked = UserDefaults.standard.bool(forKey: "aros.appLock")
    var checking = false
    var errorMessage: String?
    func lock() { if enabled { locked = true } }
    func authenticate() async throws {
        let context = LAContext()
        var error: NSError?
        guard context.canEvaluatePolicy(.deviceOwnerAuthentication, error: &error) else {
            throw APIError(message: "Configura Face ID, Touch ID o un código del dispositivo antes de activar el bloqueo.", status: 0)
        }
        let ok = try await context.evaluatePolicy(.deviceOwnerAuthentication, localizedReason: "Desbloquear tu espacio personal de Aros")
        if !ok { throw APIError(message: "No se pudo verificar tu identidad", status: 0) }
    }
    func setEnabled(_ value: Bool) async throws {
        try await authenticate(); enabled = value; locked = false
        UserDefaults.standard.set(value, forKey: "aros.appLock")
    }
    func unlock() async {
        checking = true; errorMessage = nil; defer { checking = false }
        do { try await authenticate(); locked = false }
        catch { errorMessage = "No se ha desbloqueado la app. Puedes intentarlo de nuevo." }
    }
}
struct LockScreen: View {
    @Environment(AppStore.self) private var store
    var body: some View {
        VStack(spacing: 22) {
            ArosOrb(size: 65)
            Text("Tu espacio personal").font(.title.bold())
            if let error = store.appLock.errorMessage { Text(error).font(.caption).multilineTextAlignment(.center) }
            Button(store.appLock.checking ? "Verificando…" : "Desbloquear Aros") { Task { await store.appLock.unlock() } }
                .buttonStyle(.borderedProminent).disabled(store.appLock.checking)
        }.padding(30).frame(maxWidth: .infinity, maxHeight: .infinity).background { ArosBackground() }
    }
}
