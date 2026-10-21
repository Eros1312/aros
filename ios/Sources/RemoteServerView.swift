import SwiftUI

struct RemoteServerView: View {
    @Environment(AppStore.self) private var store
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                HStack(spacing: 12) { AreaIcon(area: .server, size: 48); VStack(alignment: .leading) { Text("Tu servidor").font(.title2.bold()); Text(store.remoteHealth?.scope ?? "Sin conexión").font(.caption).foregroundStyle(ArosTheme.secondary) } }
                if store.state == .offline { StateNotice(state: .offline) { Task { await store.refreshRemote() } } }
                if let health = store.remoteHealth {
                    VStack(spacing: 20) {
                        ForEach(Array(health.metrics.enumerated()), id: \.element.id) { index, metric in
                            MetricRow(metric: metric, tint: [.cyan,.purple,.orange][index % 3])
                        }
                    }.padding(20).glass()
                    Text(health.uptimeSeconds.map { "Tiempo activo: \($0 / 86400) días" } ?? "Tiempo activo no disponible").font(.subheadline)
                    Text(health.actionsEnabled ? "Las acciones del agente y su historial están disponibles en la web de Aros para Mac." : "Para controlar contenedores y ejecutar acciones, configura el agente VPS. Estas métricas corresponden al equipo que ejecuta el backend.").font(.caption).foregroundStyle(ArosTheme.secondary)
                    NavigationLink(value: Destination.automations) { Label("Automatizaciones", systemImage: "arrow.triangle.branch") }.buttonStyle(.bordered)
                } else { ContentUnavailableView("No se pudo consultar el servidor", systemImage: "server.rack") }
            }.padding(20)
        }.navigationTitle("Servidor").inlineTitle().refreshable { await store.refreshRemote() }
    }
}
