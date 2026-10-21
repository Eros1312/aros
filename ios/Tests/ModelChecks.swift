import Foundation

struct FailingProvider: DashboardProviding {
    func load() async throws -> Dashboard { throw URLError(.notConnectedToInternet) }
}

@main
struct ModelChecks {
    @MainActor static func main() async {
        let integratedJobJSON = #"{"id":"job-test","agent_id":"aros","thread_id":"chat","prompt":"Consulta y prepara mi día","status":"completed","reply":"Creado y verificado","error":"","created_at":"2026-10-02T10:00:00Z","actions":[],"sources":[{"id":"source-test","title":"Nota de prueba","area":"personal","kind":"note"}],"execution":{"capability":"conversation.integrated","state":"completed","reply_message_id":"reply-test","budget":{"model_calls":4,"model_call_limit":12,"tool_calls":3},"steps":[{"tool":"artifact.verify","status":"success","summary":"Resultado verificado","artifacts":["artifact-test"]}]}}"#
        let integratedJob = try! APIClient.decode(RemoteAgentJob.self, from: Data(integratedJobJSON.utf8))
        precondition(integratedJob.execution?.replyMessageId == "reply-test" && integratedJob.execution?.budget?.modelCalls == 4, "Async chat must decode its fresh reply marker and budget")
        precondition(integratedJob.execution?.steps?.first?.artifacts == ["artifact-test"] && integratedJob.sources?.first?.area == .personal, "Verified artifacts and scoped citations must remain accessible to the native conversation")
        let legacyJobJSON = #"{"id":"old-job","agent_id":"aros","thread_id":"main","prompt":"Hola","status":"completed","reply":"Hola","error":"","created_at":"2026-10-02T10:00:00Z","actions":[]}"#
        let legacyJob = try! APIClient.decode(RemoteAgentJob.self, from: Data(legacyJobJSON.utf8))
        precondition(legacyJob.execution == nil, "Existing agent histories must remain compatible")
        var voiceGate = NewReplyVoiceGate()
        precondition(!voiceGate.accept(id: "seen-while-muted", enabled: false), "Voice is opt-in")
        precondition(!voiceGate.accept(id: "seen-while-muted", enabled: true), "Enabling voice cannot replay an old reply")
        precondition(voiceGate.accept(id: "new-post-result", enabled: true), "Only a fresh reply can be read")
        precondition(!voiceGate.accept(id: "new-post-result", enabled: true), "Duplicate callbacks cannot replay a reply")
        let printingJSON = #"{"items":[{"id":"verified-1","title":"Soporte","description":"Objeto útil","source_url":"https://example.org/model","image_url":"https://example.org/photo.jpg","image_urls":["https://example.org/photo.jpg","http://example.org/unsafe.jpg","https://example.org/second.jpg"],"license":null,"source":"Fuente","published_at":null,"recommended_at":"2026-10-01T12:00:00Z","usage":"Sujetar objetos","profile_tags":[]}],"status":{"last_refresh":null,"next_refresh":null,"discovered":1,"error":null}}"#
        let printing = try! APIClient.decode(RemotePrintingFeed.self, from: Data(printingJSON.utf8))
        precondition(printing.items[0].photos.count == 2, "Photographs must be HTTPS and deduplicated")
        precondition(printing.items[0].license == nil && printing.status.discovered == 1)
        precondition(printing.items[0].favorite == nil && !printing.status.updating, "Older feeds must decode without inventing favorites or active jobs")
        let savedPrintingJSON = printingJSON.replacingOccurrences(of: "\"profile_tags\":[]", with: "\"favorite\":true,\"profile_tags\":[\"química\",\"organización\"]").replacingOccurrences(of: "\"discovered\":1", with: "\"enabled\":true,\"running\":false,\"queued\":true,\"total\":1,\"discovered\":1")
        let savedPrinting = try! APIClient.decode(RemotePrintingFeed.self, from: Data(savedPrintingJSON.utf8))
        precondition(savedPrinting.status.updating && savedPrinting.items[0].favorite == true)
        precondition(savedPrinting.items[0].matches("QUIMICA", tag: "química") && !savedPrinting.items[0].matches("", tag: "tecnología"), "Search must match translated interest tags and respect category")
        precondition(savedPrinting.items[0].spanishSummary == "Ideas relacionadas con química y laboratorio.")
        precondition(RemotePrintRecommendation.safeURL("https://user:secret@example.org/photo") == nil && RemotePrintRecommendation.safeURL("javascript:alert(1)") == nil)
        let queue = try! APIClient.decode(RemotePrintingRefresh.self, from: Data(#"{"queued":true,"running":false,"run_id":"run-1"}"#.utf8))
        precondition(queue.queued == true && queue.runId == "run-1")
        let saved = try! APIClient.decode(RemotePrintingFavorite.self, from: Data(#"{"id":"verified-1","favorite":true}"#.utf8))
        precondition(saved.favorite && saved.id == printing.items[0].id)
        let pagedJSON = printingJSON.dropLast() + ",\"pagination\":{\"offset\":60,\"limit\":60,\"total\":135,\"next_offset\":120}}"
        let paged = try! APIClient.decode(RemotePrintingFeed.self, from: Data(pagedJSON.utf8))
        precondition(paged.pagination?.nextOffset == 120 && paged.pagination?.total == 135, "Page count must represent all matching server records")
        let pagePath = PrintingCollectionQuery(text: "  LED & soporte  ", category: "química", favorite: true).path(offset: 60)
        let pageQuery = URLComponents(string: "https://aros.example" + pagePath)!.queryItems!
        precondition(pageQuery.contains(URLQueryItem(name: "q", value: "LED & soporte")) && pageQuery.contains(URLQueryItem(name: "favorite", value: "true")) && pageQuery.contains(URLQueryItem(name: "offset", value: "60")) && pageQuery.contains(URLQueryItem(name: "category", value: "química")), "Server search, category, favorite and offset must remain independent encoded parameters")


        let weatherJSON = #"{"configured":true,"location":{"label":"Valencia","latitude":39.4,"longitude":-0.3},"forecast":{"temperature":22,"feels_like":21,"description":"Nublado","code":3,"wind_kmh":15,"maximum":24,"minimum":18,"rain_probability":60},"stale":true,"last_sync":null,"error":null,"status":"connected"}"#
        let weather = try! APIClient.decode(RemoteWeather.self, from: Data(weatherJSON.utf8))
        precondition(weather.stale && weather.forecast?.rainProbability == 60 && weather.forecast?.feelsLike == 21)
        let prismaJSON = #"{"assets":[],"canva":{"configured":true,"connected":false},"counts":{}}"#
        let prisma = try! APIClient.decode(RemotePrismaLibrary.self, from: Data(prismaJSON.utf8))
        precondition(prisma.canva.configured && !prisma.canva.connected && prisma.canva.designAvailable != true, "Configured app must not imply OAuth consent or model availability")
        let unconfigured = try! APIClient.decode(RemotePrismaLibrary.self, from: Data(#"{"assets":[],"canva":{"configured":false,"connected":false,"redirect_uri":"https://aros.example/api/v1/prisma/canva/callback","scopes":["design:read"]}}"#.utf8))
        precondition(!unconfigured.canva.configured && !unconfigured.canva.connected && unconfigured.canva.redirectUri?.hasSuffix("/api/v1/prisma/canva/callback") == true)

        let calendarJSON = #"{"start":"2026-10-01","end":"2026-11-02","timezone":"Europe/Madrid","events":[{"id":"overnight","title":"Examen","area":"university","start":"2026-10-01T21:30:00Z","end":"2026-10-01T23:30:00Z","all_day":false,"estimated_duration":false,"location":"Aula 4","category":"exam","source":"uv-calendar","is_demo":false},{"id":"all-day","title":"Día completo","area":"personal","start":"2026-10-01T22:00:00Z","end":"2026-10-02T22:00:00Z","all_day":true,"estimated_duration":false}]}"#
        let calendarRange = try! APIClient.decode(RemoteCalendarRange.self, from: Data(calendarJSON.utf8))
        let calendarDates = NativeCalendarDates(timeZone: TimeZone(identifier: "Europe/Madrid")!)
        let october2 = UniversityResourceOrder.date("2026-10-02T10:00:00Z")!
        precondition(calendarRange.events[0].category == "exam" && calendarRange.events[0].location == "Aula 4")
        precondition(calendarDates.overlaps(calendarRange.events[0], day: october2), "Crossing midnight must appear on both local dates")
        precondition(!calendarDates.overlaps(calendarRange.events[1], day: calendarDates.calendar.date(byAdding: .day, value: 1, to: october2)!), "All-day exclusive end must not appear the following day")
        precondition(calendarDates.calendar.component(.weekday, from: calendarDates.monthDays(october2)[0]) == 2, "Spanish calendar must start on Monday")
        let foldStart = UniversityResourceOrder.date("2026-10-25T00:30:00Z")!, foldEnd = UniversityResourceOrder.date("2026-10-25T01:30:00Z")!
        precondition(calendarDates.timeRange(foldStart, foldEnd) == "02:30 +02:00 – 02:30 +01:00", "Repeated autumn hour must show both UTC offsets")
        let march = UniversityResourceOrder.date("2026-03-29T10:00:00Z")!
        let bounds = calendarDates.monthBounds(march)
        precondition(calendarDates.calendar.dateComponents([.day], from: bounds.start, to: bounds.end).day == calendarDates.monthDays(march).count && calendarDates.monthDays(march).count <= 42, "Month range must include every displayed day across DST with an exclusive end")

        func timingResource(_ id: String, kind: String = "event", start: String? = nil, data: [String: JSONValue] = [:], updated: String = "2026-10-01T12:00:00Z") -> Resource {
            Resource(id: id, kind: kind, area: .university, title: id, description: "", status: "active", priority: "normal", dueAt: start, data: data, source: "fixture", isDemo: false, version: 1, createdAt: updated, updatedAt: updated)
        }
        let instant = UniversityResourceOrder.date("2026-10-02T12:00:00Z")!
        let past = timingResource("past", start: "2026-10-01T09:00:00Z", data: ["end_at":.string("2026-10-01T10:00:00Z")])
        let current = timingResource("current", start: "2026-10-02T11:00:00Z", data: ["end_at":.string("2026-10-02T13:00:00Z")])
        let soon = timingResource("soon", start: "2026-10-03T09:00:00Z")
        let far = timingResource("exam", start: "2027-06-01T09:00:00Z")
        let document = timingResource("notes", kind: "document", data: ["filename":.string("notes.pdf")])
        let resources = [far,past,document,soon,current]
        precondition(UniversityResourceOrder.upcoming(resources, now: instant).map(\.id) == ["current","soon","exam"], "Current class must survive when its start is in the past")
        precondition(UniversityResourceOrder.ordered(resources, now: instant).map(\.id) == ["notes","current","soon","exam","past"], "Documents and nearby events must outrank distant exams")
        let metadata = timingResource("canva-metadata", kind: "project", updated: "2026-10-02T13:00:00Z")
        precondition(PrismaAssetOrder.ordered([metadata,document]).first?.id == "notes", "Local original must precede newer metadata-only Canva record")
        let original = Resource(id: "original", kind: "document", area: .prisma, title: "Original", description: "", status: "active", priority: "normal", dueAt: nil, data: ["filename":.string("original.pdf"),"review_state":.string("original")], source: "prisma-file", isDemo: false, version: 1, createdAt: "2026-10-01T12:00:00Z", updatedAt: "2026-10-01T12:00:00Z")
        let candidate = Resource(id: "candidate", kind: "project", area: .prisma, title: "Candidate", description: "", status: "active", priority: "normal", dueAt: nil, data: ["preview_item_id":.string("preview"),"review_state":.string("needs_review")], source: "canva-codex", isDemo: false, version: 1, createdAt: "2026-10-02T14:00:00Z", updatedAt: "2026-10-02T14:00:00Z")
        precondition(PrismaAssetOrder.ordered([candidate,original]).first?.id == "original", "Original must precede a newer Canva candidate with a preview")
        let homeRows = PrismaHomeSummary.rows([original,candidate])
        precondition(homeRows.map(\.title) == ["1 originales", "1 diseños en Canva", "0 conversaciones de referencia"], "Home PRISMA counts must come from loaded sources without leaking filenames or internal context")

        let dashboard = DemoData.dashboard()
        precondition(dashboard.summaries.map(\.area) == Area.allCases, "The six areas must follow reference order")
        precondition(dashboard.unresolved.count == 3)
        precondition(Set(dashboard.summaries.flatMap(\.rows).map(\.id)).count == dashboard.summaries.flatMap(\.rows).count)
        precondition(DashboardSearch.results(in: dashboard, query: "quimica").first?.area == .university)
        precondition(DashboardSearch.results(in: dashboard, query: "  PRISMA ").contains { $0.area == .prisma })
        precondition(DashboardSearch.results(in: dashboard, query: "this-does-not-exist").isEmpty)
        precondition(!DashboardSearch.results(in: dashboard, query: " ").isEmpty)
        let encoded = try! JSONEncoder().encode(dashboard)
        let decoded = try! JSONDecoder().decode(Dashboard.self, from: encoded)
        precondition(decoded.summaries.count == 6 && decoded.unresolved.count == 3)
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(secondsFromGMT: 0)!
        for (hour, expected) in [(5, "Buenas noches,"), (6, "Buenos días,"), (12, "Buenos días,"), (13, "Buenas tardes,"), (20, "Buenas tardes,"), (21, "Buenas noches,")] {
            let date = calendar.date(from: DateComponents(year: 2026, month: 10, day: 1, hour: hour))!
            precondition(DayGreeting.text(at: date, calendar: calendar) == expected)
        }
        let store = AppStore()
        store.resolve("organic-task")
        precondition(store.dashboard.unresolved.count == 2)
        await store.refresh()
        precondition(store.dashboard.unresolved.count == 2, "Refresh must preserve resolved demo items")
        store.resolve("organic-task")
        precondition(store.dashboard.unresolved.count == 3)
        store.open(.area(.personal))
        store.select(.server)
        precondition(store.path.isEmpty && store.selectedTab == .server)
        store.openChat(.attach)
        precondition(store.chatPresented)
        store.send("  ", attachments: [])
        precondition(store.messages.isEmpty)
        store.send("¿Qué me falta de PRISMA?", attachments: [])
        precondition(store.messages.count == 2 && store.messages.last?.sources == [.prisma])
        store.send("", attachments: ["notes.pdf"])
        precondition(store.messages.last?.text.contains("no se leen") == true)
        store.simulate(.empty)
        store.send("Prepárame el día", attachments: [])
        precondition(store.messages.last?.sources.isEmpty == true, "Empty data must never produce a fabricated summary")
        let offline = AppStore(provider: FailingProvider())
        await offline.refresh()
        precondition(offline.state == .offline && offline.dashboard.summaries.count == 6)
        let racing = AppStore()
        let loading = Task { await racing.refresh() }
        await Task.yield()
        racing.simulate(.empty)
        await loading.value
        precondition(racing.state == .empty, "An old load must not replace a newer state selection")
        print("PASS: demo data, search, greeting boundaries, coding, resolution, refresh, navigation, chat, attachments, offline and loading race")
    }
}
