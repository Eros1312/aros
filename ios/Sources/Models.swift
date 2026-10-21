import Foundation

enum Area: String, CaseIterable, Identifiable, Codable {
    case university, prisma, technology, printing, server, personal
    var id: String { rawValue }
    var title: String {
        switch self {
        case .university: return "Universidad"
        case .prisma: return "PRISMA"
        case .technology: return "IA & Tecnología"
        case .printing: return "Impresión 3D"
        case .server: return "Servidor"
        case .personal: return "Personal"
        }
    }
    var symbol: String {
        switch self {
        case .university: return "graduationcap"
        case .prisma: return "triangle"
        case .technology: return "sparkles"
        case .printing: return "cube"
        case .server: return "server.rack"
        case .personal: return "person.fill"
        }
    }
}

struct SummaryRow: Identifiable, Codable {
    let id: String
    let title: String
    let subtitle: String
    let symbol: String
}

struct AreaSummary: Identifiable, Codable {
    var id: Area { area }
    let area: Area
    let eyebrow: String?
    let headline: String?
    let detail: String?
    let footnote: String?
    let rows: [SummaryRow]
}

struct AttentionItem: Identifiable, Codable {
    let id: String
    let area: Area
    let title: String
    let reason: String
    var resolved = false
}

struct ServerMetric: Identifiable, Codable {
    let id: String
    let title: String
    let value: String
    let samples: [Double]
}

struct Dashboard: Codable {
    let city: String
    let temperature: String
    let weather: String
    let summaries: [AreaSummary]
    var attention: [AttentionItem]
    let metrics: [ServerMetric]
    let syncedAt: Date?
    var unresolved: [AttentionItem] { attention.filter { !$0.resolved } }
    func summary(for area: Area) -> AreaSummary? { summaries.first { $0.area == area } }
}

enum LoadState: String, CaseIterable, Identifiable {
    case ready, loading, empty, offline, stale
    var id: String { rawValue }
    var title: String {
        switch self {
        case .ready: return "Con datos"
        case .loading: return "Cargando"
        case .empty: return "Sin datos"
        case .offline: return "Sin conexión"
        case .stale: return "Datos antiguos"
        }
    }
}

enum Destination: Hashable {
    case area(Area), inbox, weather, search, settings, workspace, calendar, files, connections, automations, resource(String)
}

enum MainTab: String, CaseIterable, Identifiable {
    case today, university, prisma, ideas, server
    var id: String { rawValue }
    var title: String {
        switch self {
        case .today: return "Hoy"
        case .university: return "Universidad"
        case .prisma: return "PRISMA"
        case .ideas: return "IA & 3D"
        case .server: return "Servidor"
        }
    }
    var symbol: String {
        switch self {
        case .today: return "house.fill"
        case .university: return "calendar"
        case .prisma: return "triangle"
        case .ideas: return "square.grid.2x2"
        case .server: return "server.rack"
        }
    }
}

struct SearchHit: Identifiable {
    let id: String
    let area: Area
    let title: String
    let subtitle: String
}

enum DashboardSearch {
    static func results(in dashboard: Dashboard, query: String) -> [SearchHit] {
        let hits = dashboard.summaries.flatMap { summary -> [SearchHit] in
            var rows = summary.rows.map {
                SearchHit(id: $0.id, area: summary.area, title: $0.title, subtitle: $0.subtitle)
            }
            if let headline = summary.headline {
                rows.insert(SearchHit(id: summary.area.rawValue, area: summary.area,
                    title: headline, subtitle: [summary.detail, summary.footnote].compactMap { $0 }.joined(separator: " · ")), at: 0)
            }
            return rows
        }
        let term = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !term.isEmpty else { return hits }
        return hits.filter {
            ($0.title + " " + $0.subtitle + " " + $0.area.title)
                .range(of: term, options: [.caseInsensitive, .diacriticInsensitive]) != nil
        }
    }
}

enum DayGreeting {
    static func text(at date: Date, calendar: Calendar = .current) -> String {
        switch calendar.component(.hour, from: date) {
        case 6..<13: return "Buenos días,"
        case 13..<21: return "Buenas tardes,"
        default: return "Buenas noches,"
        }
    }
}

struct ChatMessage: Identifiable {
    let id = UUID()
    let isUser: Bool
    let text: String
    var sources: [Area] = []
    var attachmentNames: [String] = []
}

protocol DashboardProviding {
    func load() async throws -> Dashboard
}

struct DemoDashboardProvider: DashboardProviding {
    func load() async throws -> Dashboard {
        try await Task.sleep(for: .milliseconds(500))
        return DemoData.dashboard()
    }
}

enum DemoData {
    static func dashboard(now: Date = .now) -> Dashboard {
        Dashboard(city: "Valencia", temperature: "24 °C", weather: "Soleado", summaries: [
            AreaSummary(area: .university, eyebrow: "Próxima clase", headline: "Química Orgánica II",
                detail: "Hoy · 12:30 – 14:30", footnote: "Aulario Interfacultativo", rows: [
                    SummaryRow(id: "assignments", title: "2 tareas pendientes", subtitle: "Orgánica y Analítica", symbol: "list.bullet.clipboard"),
                    SummaryRow(id: "document", title: "1 documento nuevo", subtitle: "Guion de prácticas · PDF", symbol: "doc.text")]),
            AreaSummary(area: .prisma, eyebrow: "Próximo evento", headline: "Encuentro creativo de ejemplo",
                detail: "Hoy · 22:30", footnote: "Programa y materiales de ejemplo\nEspacio de demostración", rows: [
                    SummaryRow(id: "prisma-tasks", title: "4 pendientes", subtitle: "Invitaciones, lista y diseños", symbol: "clock.badge.exclamationmark"),
                    SummaryRow(id: "material", title: "Material listo", subtitle: "Checklist preparado", symbol: "checkmark.circle.fill")]),
            AreaSummary(area: .technology, eyebrow: nil, headline: nil, detail: nil, footnote: nil, rows: [
                SummaryRow(id: "news", title: "3 novedades hoy", subtitle: "Gemini · ChatGPT · Herramientas", symbol: "sparkle"),
                SummaryRow(id: "update", title: "1 actualización importante", subtitle: "Resumen de herramientas de IA", symbol: "arrow.triangle.2.circlepath"),
                SummaryRow(id: "discovery", title: "Ideas para tus proyectos", subtitle: "Una selección para ti", symbol: "lightbulb")]),
            AreaSummary(area: .printing, eyebrow: nil, headline: nil, detail: nil, footnote: nil, rows: [
                SummaryRow(id: "models", title: "12 nuevos modelos", subtitle: "4 podrían interesarte", symbol: "cube.transparent"),
                SummaryRow(id: "materials", title: "Materiales", subtitle: "PLA · PETG · Resina", symbol: "square.stack.3d.up"),
                SummaryRow(id: "projects", title: "Proyectos", subtitle: "3 en cola", symbol: "tray")]),
            AreaSummary(area: .server, eyebrow: nil, headline: nil, detail: nil, footnote: nil, rows: [
                SummaryRow(id: "api", title: "API y servicios", subtitle: "Todo funciona correctamente", symbol: "checkmark.circle"),
                SummaryRow(id: "backup", title: "Último backup", subtitle: "Hoy · 04:00", symbol: "externaldrive")]),
            AreaSummary(area: .personal, eyebrow: nil, headline: nil, detail: nil, footnote: nil, rows: [
                SummaryRow(id: "orders", title: "2 pedidos en camino", subtitle: "Tienda de ejemplo · Taller local", symbol: "shippingbox"),
                SummaryRow(id: "payment", title: "Pago pendiente", subtitle: "Reembolso de ejemplo", symbol: "creditcard"),
                SummaryRow(id: "return", title: "Devolución pendiente", subtitle: "Material de oficina · 20,00 €", symbol: "arrow.uturn.backward")])
        ], attention: [
            AttentionItem(id: "organic-task", area: .university, title: "Entregar el informe de Orgánica", reason: "Vence mañana a las 15:00."),
            AttentionItem(id: "guest-list", area: .prisma, title: "Confirmar las invitaciones", reason: "El evento es esta noche."),
            AttentionItem(id: "led-return", area: .personal, title: "Preparar una devolución de ejemplo", reason: "El plazo termina en dos días.")
        ], metrics: [
            ServerMetric(id: "cpu", title: "CPU", value: "14%", samples: [0.3, 0.42, 0.32, 0.7, 0.39, 0.62, 0.45, 0.8]),
            ServerMetric(id: "ram", title: "RAM", value: "1,7 / 4 GB", samples: [0.1, 0.18, 0.6, 0.4, 0.27, 0.56, 0.43, 0.7]),
            ServerMetric(id: "disk", title: "Disco", value: "31 / 120 GB", samples: [0.12, 0.34, 0.31, 0.5, 0.47, 0.65, 0.45, 0.59])
        ], syncedAt: now)
    }
}


extension Dashboard {
    var weatherSymbol: String {
        let condition = weather.lowercased()
        if condition.contains("tormenta") { return "cloud.bolt.rain.fill" }
        if condition.contains("nieve") { return "cloud.snow.fill" }
        if condition.contains("lluvia") || condition.contains("llovizna") { return "cloud.rain.fill" }
        if condition.contains("niebla") { return "cloud.fog.fill" }
        if condition.contains("nublado") { return "cloud.fill" }
        if condition.contains("sin conectar") { return "cloud" }
        return "sun.max.fill"
    }
}
