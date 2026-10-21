import Foundation

enum JSONValue: Codable, Hashable {
    case string(String), number(Double), bool(Bool), object([String: JSONValue]), array([JSONValue]), null
    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if container.decodeNil() { self = .null }
        else if let value = try? container.decode(Bool.self) { self = .bool(value) }
        else if let value = try? container.decode(Double.self) { self = .number(value) }
        else if let value = try? container.decode(String.self) { self = .string(value) }
        else if let value = try? container.decode([String: JSONValue].self) { self = .object(value) }
        else { self = .array(try container.decode([JSONValue].self)) }
    }
    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case .string(let value): try container.encode(value)
        case .number(let value): try container.encode(value)
        case .bool(let value): try container.encode(value)
        case .object(let value): try container.encode(value)
        case .array(let value): try container.encode(value)
        case .null: try container.encodeNil()
        }
    }
    var text: String {
        switch self {
        case .string(let s): return s
        case .number(let n): return String(n)
        case .bool(let b): return b ? "Sí" : "No"
        case .null: return ""
        default: return ""
        }
    }
}

struct Resource: Codable, Identifiable {
    let id: String
    let kind: String
    let area: Area
    let title: String
    let description: String
    let status: String
    let priority: String
    let dueAt: String?
    let data: [String: JSONValue]
    let source: String
    let isDemo: Bool
    let version: Int
    let createdAt: String
    let updatedAt: String
    var editable: Bool { ["manual", "demo", "upload"].contains(source) }
    var kindTitle: String { ResourceKind.names[kind] ?? kind }
    var statusTitle: String { ResourceKind.statuses[status] ?? status }
}

enum ResourceKind {
    static let names = ["task":"Tarea", "event":"Evento", "document":"Documento", "email":"Correo", "order":"Pedido", "model":"Modelo 3D", "material":"Material", "news":"Noticia", "note":"Nota", "project":"Proyecto", "service":"Servicio", "reminder":"Recordatorio", "memory":"Memoria", "contact":"Contacto", "expense":"Gasto"]
    static let statuses = ["pending":"Pendiente", "active":"Activo", "done":"Completado", "archived":"Archivado", "in_transit":"En camino", "delivered":"Entregado", "cancelled":"Cancelado"]
}

struct RemoteUser: Codable { let id: String; let email: String; var aiConsent: Bool; let demo: Bool }
struct LoginResult: Codable { let accessToken: String }
struct RemoteInbox: Codable, Identifiable {
    let id: String
    let itemId: String?
    let title: String
    let reason: String
    let area: Area
    let state: String
    let snoozedUntil: String?
}
struct RemoteConnector: Codable, Identifiable {
    let id: String; let name: String; let status: String; let scopes: [String]; let lastSync: String?; let error: String?; let implemented: Bool
}
struct RemoteAutomation: Codable, Identifiable {
    let id: String; let title: String; let kind: String; let enabled: Bool; let intervalMinutes: Int; let nextRun: String
}
struct RemoteRun: Codable, Identifiable {
    let id: String; let automationId: String; let status: String; let result: String; let createdAt: String
}
struct RemoteSource: Codable, Identifiable { let id: String; let title: String; let area: Area; let kind: String }
struct RemoteMessage: Codable, Identifiable {
    let id: String; let role: String; let text: String; let sources: [RemoteSource]; let mode: String
}
struct ChatResult: Codable { let id: String; let text: String; let sources: [RemoteSource]; let mode: String }
struct RemoteHealth: Codable {
    let scope: String; let cpuPercent: Double; let ramUsed: Double; let ramTotal: Double; let diskUsed: Double; let diskTotal: Double; let uptimeSeconds: Int?; let actionsEnabled: Bool
    var metrics: [ServerMetric] { [
        ServerMetric(id: "cpu", title: "CPU", value: "\(Int(cpuPercent))%", samples: []),
        ServerMetric(id: "ram", title: "RAM", value: String(format: "%.1f / %.0f GB", ramUsed / 1e9, ramTotal / 1e9), samples: []),
        ServerMetric(id: "disk", title: "Disco", value: "\(Int(diskUsed / 1e9)) / \(Int(diskTotal / 1e9)) GB", samples: [])
    ] }
}
struct OKResult: Codable { let ok: Bool }
struct ConfirmationResult: Codable { let token: String }
struct URLResult: Codable { let url: String }

struct ResourceDraft: Encodable {
    var kind: String
    var area: Area
    var title: String
    var description: String
    var status: String
    var priority: String
    var dueAt: String?
    var data: [String: JSONValue]
    var version: Int?
    init(resource: Resource) {
        kind = resource.kind; area = resource.area; title = resource.title; description = resource.description
        status = resource.status; priority = resource.priority; dueAt = resource.dueAt; data = resource.data; version = resource.version
    }
    init(kind: String = "task", area: Area = .personal) {
        self.kind = kind; self.area = area; title = ""; description = ""; status = "pending"; priority = "normal"; dueAt = nil; data = [:]; version = nil
    }
    func body() throws -> [String: Any] {
        let encoder = JSONEncoder(); encoder.keyEncodingStrategy = .convertToSnakeCase
        var object = try JSONSerialization.jsonObject(with: encoder.encode(self)) as! [String: Any]
        // Both confirmation and write requests use an explicit null date.
        object["due_at"] = dueAt.map { $0 as Any } ?? NSNull()
        return object
    }
}

struct RemoteAgent: Decodable, Identifiable {
    let id: String
    let name: String
    let model: String
    let available: Bool
}
struct RemoteAgentStatus: Decodable {
    let reachable: Bool
    let detail: String
    let model: String
    let agents: [RemoteAgent]
}
struct RemoteAgentAction: Decodable {
    let type: String
    let payload: Payload
    let appliedId: String?
    let setupRequired: Bool?
    let detail: String?
    struct Payload: Decodable {
        let title: String?
        let description: String?
        let id: String?
        let brief: String?
        let assetId: String?
        let model: String?
    }
}
struct RemoteAppliedAction: Decodable { let id: String }
struct RemoteAgentJob: Decodable, Identifiable {
    struct Event: Decodable { let type: String; let text: String; let at: String }
    struct Source: Decodable, Identifiable { let id: String; let title: String; let area: Area?; let kind: String? }
    struct Execution: Decodable {
        struct Step: Decodable {
            let tool: String?
            let status: String?
            let summary: String
            let artifacts: [String]?
        }
        struct Budget: Decodable { let modelCalls: Int?; let modelCallLimit: Int?; let toolCalls: Int? }
        struct Artifact: Decodable { let id: String; let title: String; let verified: Bool? }
        let capability: String?
        let state: String?
        let outcome: String?
        let steps: [Step]?
        let budget: Budget?
        let artifacts: [Artifact]?
        let replyMessageId: String?
    }
    let execution: Execution?
    let events: [Event]?
    let contextSources: [Source]?
    let sources: [Source]?
    let id: String
    let agentId: String
    let threadId: String
    let actions: [RemoteAgentAction]
    let prompt: String
    let status: String
    let reply: String
    let error: String
    let createdAt: String
}

struct RemoteCalendarPlan: Decodable {
    let date: String
    let timezone: String
    let events: [CalendarEntry]
    let conflicts: [CalendarConflict]
    let freeSlots: [CalendarSlot]
    let note: String
    struct CalendarEntry: Decodable, Identifiable {
        let id: String
        let title: String
        let area: Area
        let start: Date
        let end: Date
        let allDay: Bool
        let estimatedDuration: Bool
        let location: String?
        let category: String?
        let source: String?
        let isDemo: Bool?
    }
    struct CalendarConflict: Decodable {
        let ids: [String]
        let start: Date
        let end: Date
    }
    struct CalendarSlot: Decodable {
        let start: Date
        let end: Date
        let minutes: Int
    }
}

struct RemoteModelPreferences: Decodable {
    let model: String
    let models: [Option]
    let freellmDetail: String
    let scope: String
    struct Option: Decodable, Identifiable {
        let id: String
        let name: String
        let provider: String
        let detail: String
    }
}

struct RemotePrintingFeed: Decodable {
    let items: [RemotePrintRecommendation]
    let status: Status
    let pagination: Pagination?
    struct Pagination: Decodable { let offset: Int; let limit: Int; let total: Int; let nextOffset: Int? }
    struct Status: Decodable {
        let lastRefresh: String?
        let nextRefresh: String?
        let discovered: Int?
        let deficit: Int?
        let error: String?
        let enabled: Bool?
        let running: Bool?
        let queued: Bool?
        let total: Int?
        let target: Int?
        var updating: Bool { running == true || queued == true }
    }
}
struct RemotePrintingRefresh: Decodable { let queued: Bool?; let running: Bool?; let runId: String? }
struct RemotePrintingFavorite: Decodable { let id: String; let favorite: Bool }
struct RemotePrintRecommendation: Decodable, Identifiable {
    let id: String
    let title: String
    let description: String
    let sourceUrl: String
    let imageUrl: String?
    let imageUrls: [String]?
    let license: String?
    let source: String
    let publishedAt: String?
    let recommendedAt: String
    let usage: String
    let profileTags: [String]
    let favorite: Bool?
    let licenseUrl: String?
    let licenseState: String?
    var spanishSummary: String {
        let tags = Set(profileTags.map { $0.folding(options: [.caseInsensitive, .diacriticInsensitive], locale: Locale(identifier: "es_ES")) })
        if tags.contains("quimica") { return "Ideas relacionadas con química y laboratorio." }
        if tags.contains("organizacion") { return "Para explorar soluciones de organización." }
        if tags.contains("prisma") { return "Ideas para tus proyectos y eventos de PRISMA." }
        if tags.contains("tecnologia") { return "Ideas para proyectos de tecnología y electrónica." }
        return "Explora su utilidad en la ficha del autor."
    }
    func matches(_ query: String, tag: String = "all") -> Bool {
        let text = ([title, description, usage, source, spanishSummary] + profileTags).joined(separator: " ")
        return (tag == "all" || profileTags.contains(tag)) && (query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || text.localizedStandardContains(query.trimmingCharacters(in: .whitespacesAndNewlines)))
    }
    static func safeURL(_ value: String?) -> URL? {
        guard let value, let url = URL(string: value), url.scheme == "https", url.host != nil, url.user == nil, url.password == nil else { return nil }
        return url
    }
    var photos: [URL] {
        var seen = Set<String>()
        return ((imageUrls ?? []) + [imageUrl].compactMap { $0 }).compactMap { value in
            guard seen.insert(value).inserted, let url = Self.safeURL(value) else { return nil }
            return url
        }
    }
}
struct RemoteWeather: Decodable {
    let configured: Bool
    let location: City?
    let forecast: Forecast?
    let stale: Bool
    let lastSync: String?
    let error: String?
    let status: String
    struct City: Decodable { let label: String; let latitude: Double; let longitude: Double }
    struct Forecast: Decodable {
        let temperature: Double
        let feelsLike: Double
        let description: String
        let code: Int
        let windKmh: Double
        let maximum: Double
        let minimum: Double
        let rainProbability: Double
        var advice: String {
            if rainProbability >= 50 { return "Puede llover: lleva paraguas si vas a salir." }
            if windKmh >= 30 { return "Se espera viento: tenlo en cuenta al salir." }
            if feelsLike <= 10 { return "La sensación es fresca: una capa extra puede resultar útil." }
            if feelsLike >= 30 { return "La sensación es cálida: busca sombra en las horas de más calor." }
            return "Condiciones suaves para tus planes al aire libre."
        }
        var compact: String { "Sensación \(Int(feelsLike.rounded()))° · \(Int(minimum.rounded()))–\(Int(maximum.rounded()))° · Lluvia \(Int(rainProbability.rounded()))% · Viento \(Int(windKmh.rounded())) km/h" }
    }
}

struct RemotePrismaLibrary: Decodable {
    let assets: [Resource]
    let canva: Canva
    struct Canva: Decodable { let configured: Bool; let connected: Bool; let designModel: String?; let designAvailable: Bool?; let redirectUri: String?; let scopes: [String]?; let error: String? }
}
enum PrismaHomeSummary {
    static func rows(_ resources: [Resource]) -> [SummaryRow] {
        let active = resources.filter { $0.area == .prisma && $0.status != "archived" }
        return [
            SummaryRow(id: "prisma-originals", title: "\(active.filter { $0.source == "prisma-file" }.count) originales", subtitle: "Carteles, rascas, pegatinas y logos", symbol: "doc.text"),
            SummaryRow(id: "prisma-designs", title: "\(active.filter { ["canva", "canva-codex"].contains($0.source) }.count) diseños en Canva", subtitle: "Acceso a tus diseños guardados", symbol: "square.stack"),
            SummaryRow(id: "prisma-context", title: "\(active.filter { $0.source == "prisma-context" }.count) conversaciones de referencia", subtitle: "Contexto para tus proyectos", symbol: "text.bubble")
        ]
    }
}

enum PrismaAssetOrder {
    static func ordered(_ resources: [Resource]) -> [Resource] {
        func priority(_ item: Resource) -> Int {
            if item.source == "prisma-file" { return 0 }
            let ext = (item.data["filename"]?.text ?? "").split(separator: ".").last.map(String.init)?.lowercased() ?? ""
            if ["pdf", "png", "jpg", "jpeg"].contains(ext) || item.data["preview_item_id"]?.text.isEmpty == false { return 1 }
            if item.data["filename"]?.text.isEmpty == false { return 2 }
            return item.data["category"]?.text == "context" ? 3 : 4
        }
        return resources.sorted { priority($0) == priority($1) ? $0.updatedAt > $1.updatedAt : priority($0) < priority($1) }
    }
}
struct RemoteAuthorization: Decodable { let url: String }
struct RemoteUniversityCalendar: Decodable {
    let configured: Bool
    let count: Int
    let lastSync: String?
    let sourceUrl: String?
    let group: String
    let academicYear: String
    let readOnly: Bool
    let detail: String?
    let status: String?
    let error: String?
}

enum UniversityResourceOrder {
    static func date(_ value: String?) -> Date? {
        guard let value else { return nil }
        let formatter = ISO8601DateFormatter(); formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = formatter.date(from: value) { return date }
        formatter.formatOptions = [.withInternetDateTime]; return formatter.date(from: value)
    }
    private static func timings(_ resources: [Resource]) -> [String: (start: Date, end: Date)] {
        var result: [String: (start: Date, end: Date)] = [:]
        for resource in resources where resource.kind == "event" {
            guard let start = date(resource.dueAt) else { continue }
            result[resource.id] = (start, date(resource.data["end_at"]?.text) ?? start)
        }
        return result
    }
    static func upcoming(_ resources: [Resource], now: Date = .now) -> [Resource] {
        let times = timings(resources)
        return resources.filter { resource in
            resource.area == .university && resource.kind == "event" && !["archived", "cancelled"].contains(resource.status) &&
            (times[resource.id]?.end ?? .distantPast) > now
        }.sorted { (times[$0.id]?.start ?? .distantFuture) < (times[$1.id]?.start ?? .distantFuture) }
    }
    static func ordered(_ resources: [Resource], now: Date = .now) -> [Resource] {
        let times = timings(resources)
        return resources.sorted { a,b in
            if a.kind == "event" && b.kind == "event" {
                let aUpcoming = (times[a.id]?.end ?? .distantPast) > now
                let bUpcoming = (times[b.id]?.end ?? .distantPast) > now
                if aUpcoming != bUpcoming { return aUpcoming }
                let aDate = times[a.id]?.start ?? .distantPast, bDate = times[b.id]?.start ?? .distantPast
                return aUpcoming ? aDate < bDate : aDate > bDate
            }
            if a.kind == "document" && b.kind != "document" { return true }
            if b.kind == "document" && a.kind != "document" { return false }
            return a.updatedAt > b.updatedAt
        }
    }

}

struct RemoteCalendarRange: Decodable {
    let start: String
    let end: String
    let timezone: String
    let events: [RemoteCalendarPlan.CalendarEntry]
}

struct NativeCalendarDates {
    var calendar: Calendar
    init(timeZone: TimeZone = .current) {
        var value = Calendar(identifier: .gregorian)
        value.locale = Locale(identifier: "es_ES"); value.timeZone = timeZone
        value.firstWeekday = 2; value.minimumDaysInFirstWeek = 4
        calendar = value
    }
    func label(_ date: Date, format: String) -> String {
        let formatter = DateFormatter(); formatter.locale = Locale(identifier: "es_ES")
        formatter.calendar = calendar; formatter.timeZone = calendar.timeZone; formatter.dateFormat = format
        return formatter.string(from: date)
    }
    func timeRange(_ start: Date, _ end: Date) -> String {
        var format = calendar.isDate(start, inSameDayAs: end) ? "HH:mm" : "d MMM HH:mm"
        if calendar.timeZone.secondsFromGMT(for: start) != calendar.timeZone.secondsFromGMT(for: end) { format += " XXXXX" }
        return label(start, format: format) + " – " + label(end, format: format)
    }
    func key(_ date: Date) -> String { label(date, format: "yyyy-MM-dd") }
    func monthStart(_ date: Date) -> Date { calendar.dateInterval(of: .month, for: date)!.start }
    func weekDays(_ date: Date) -> [Date] {
        let start = calendar.dateInterval(of: .weekOfYear, for: date)!.start
        return (0..<7).compactMap { calendar.date(byAdding: .day, value: $0, to: start) }
    }
    func monthDays(_ date: Date) -> [Date] {
        let first = monthStart(date), start = weekDays(first)[0]
        let count = calendar.range(of: .day, in: .month, for: date)!.count
        let leading = calendar.dateComponents([.day], from: start, to: first).day ?? 0
        let cells = ((leading + count + 6) / 7) * 7
        return (0..<cells).compactMap { calendar.date(byAdding: .day, value: $0, to: start) }
    }
    func monthBounds(_ date: Date) -> (start: Date, end: Date) {
        let days = monthDays(date)
        return (days[0], calendar.date(byAdding: .day, value: 1, to: days.last!)!)
    }
    func overlaps(_ event: RemoteCalendarPlan.CalendarEntry, day: Date) -> Bool {
        let start = calendar.startOfDay(for: day), end = calendar.date(byAdding: .day, value: 1, to: start)!
        return event.start < end && event.end > start
    }
}

struct PrintingCollectionQuery: Equatable {
    let text: String
    let category: String
    let favorite: Bool
    init(text: String = "", category: String = "all", favorite: Bool = false) {
        self.text = text.trimmingCharacters(in: .whitespacesAndNewlines)
        self.category = category; self.favorite = favorite
    }
    func path(offset: Int = 0, limit: Int = 60) -> String {
        var parts = URLComponents()
        var items = [URLQueryItem(name: "offset", value: String(max(0, offset))), URLQueryItem(name: "limit", value: String(min(100, max(1, limit))))]
        if !text.isEmpty { items.append(URLQueryItem(name: "q", value: text)) }
        if category != "all" { items.append(URLQueryItem(name: "category", value: category)) }
        if favorite { items.append(URLQueryItem(name: "favorite", value: "true")) }
        parts.queryItems = items
        return "/printing/recommendations?" + (parts.percentEncodedQuery ?? "")
    }
}
