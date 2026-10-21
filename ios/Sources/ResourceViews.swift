import SwiftUI
import UniformTypeIdentifiers

struct ResourceListView: View {
    @Environment(AppStore.self) private var store
    let area: Area?
    var kinds: [String] = []
    var title: String = "Tu espacio"
    @State private var query = ""
    @State private var selectedKind = "all"
    @State private var creating = false
    @State private var importing = false
    private var learningSummary: Bool { area == .learning && selectedKind == "all" && query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
    private var learningResources: [Resource] { store.resources.filter { $0.area == .learning } }
    private var upcoming: [Resource] { LearningResourceOrder.upcoming(learningResources) }
    private var records: [Resource] {
        let filtered = store.resources.filter { resource in
            (area == nil || resource.area == area) && (kinds.isEmpty || kinds.contains(resource.kind)) &&
            (selectedKind == "all" || resource.kind == selectedKind) &&
            (!learningSummary || resource.kind != "event") &&
            (query.isEmpty || (resource.title + " " + resource.description + " " + (resource.data["course_title"]?.text ?? "")).range(of: query, options: [.caseInsensitive,.diacriticInsensitive]) != nil)
        }
        return area == .learning ? LearningResourceOrder.ordered(filtered) : filtered
    }
    var body: some View {
        List {
            Section { DemoLabel() }
            if area == nil && kinds.isEmpty {
                Section("Organizar") {
                    NavigationLink(value: Destination.calendar) { Label("Calendario", systemImage: "calendar") }
                    NavigationLink(value: Destination.files) { Label("Archivos", systemImage: "folder") }
                    NavigationLink(value: Destination.automations) { Label("Automatizaciones", systemImage: "arrow.triangle.branch") }
                    NavigationLink(value: Destination.connections) { Label("Conexiones", systemImage: "link") }
                }
            }
            Section {
                Picker("Tipo", selection: $selectedKind) {
                    Text(area == .learning ? "Resumen y recursos" : "Todo").tag("all")
                    ForEach(ResourceKind.names.keys.sorted(), id: \.self) { kind in Text(ResourceKind.names[kind]!).tag(kind) }
                }
            }
            if learningSummary {
                Section("Ahora y a continuación") {
                    Text("\(learningResources.filter { $0.kind == "event" }.count) sesiones de aprendizaje · \(learningResources.filter { $0.kind == "document" }.count) documentos").font(.caption).foregroundStyle(ArosTheme.secondary)
                    NavigationLink(value: Destination.calendar) { Label("Ver calendario", systemImage: "calendar") }
                    ForEach(Array(upcoming.prefix(6))) { event in
                        NavigationLink(value: Destination.resource(event.id)) {
                            VStack(alignment: .leading, spacing: 5) {
                                Text(event.title).font(.subheadline.weight(.medium))
                                if let date = event.dueAt { Text(displayDate(date)).font(.caption).foregroundStyle(ArosTheme.secondary) }
                                if let location = event.data["location"]?.text { Text(location).font(.caption2).foregroundStyle(ArosTheme.secondary) }
                            }
                        }
                    }
                    if upcoming.isEmpty { Text("No hay sesiones próximas en tu calendario.").font(.caption) }
                    Button { selectedKind = "document" } label: { Label("Ver documentos", systemImage: "doc.text") }
                    Text("Los recursos están debajo. La búsqueda incluye todo el horario, también el historial.").font(.caption).foregroundStyle(ArosTheme.secondary)
                }
            }
            if store.state == .offline || store.state == .stale {
                Section { StateNotice(state: store.state) { Task { await store.refreshRemote() } } }
            }
            if records.isEmpty {
                ContentUnavailableView(query.isEmpty ? "Nada por aquí todavía" : "No hay resultados", systemImage: "tray", description: Text("Añade un recurso o conecta una fuente."))
            }
            ForEach(records) { resource in
                NavigationLink(value: Destination.resource(resource.id)) {
                    HStack(spacing: 12) {
                        AreaIcon(area: resource.area)
                        VStack(alignment: .leading, spacing: 5) {
                            Text(resource.title).font(.subheadline.weight(.medium)).strikethrough(resource.status == "done")
                            Text("\(resource.kindTitle) · \(resource.statusTitle)").font(.caption).foregroundStyle(ArosTheme.secondary)
                            if let course = resource.data["course_title"]?.text { Text(course).font(.caption2).foregroundStyle(ArosTheme.secondary) }
                            if let date = resource.dueAt { Text(displayDate(date)).font(.caption2).foregroundStyle(ArosTheme.secondary) }
                        }
                    }.padding(.vertical, 5)
                }.swipeActions(edge: .leading) {
                    if ["task","reminder"].contains(resource.kind) && resource.editable {
                        Button { Task { await store.performRemote { try await store.completeResource(resource) } } } label: {
                            Label(resource.status == "done" ? "Pendiente" : "Completar", systemImage: "checkmark")
                        }.tint(.green)
                    }
                }
            }
        }.scrollContentBackground(.hidden).background { ArosBackground() }
            .navigationTitle(area?.title ?? title).inlineTitle()
            .searchable(text: $query, prompt: "Busca en tus datos")
            .refreshable { await store.refreshRemote() }
            .toolbar {
                ToolbarItem(placement: .primaryAction) {
                    Menu {
                        Button("Añadir recurso", systemImage: "plus") { creating = true }
                        Button("Importar archivo", systemImage: "paperclip") { importing = true }
                    } label: { Image(systemName: "plus.circle.fill") }.accessibilityLabel("Añadir o importar")
                }
            }
            .sheet(isPresented: $creating) {
                ResourceEditor(area: area ?? .personal, initialKind: kinds.count == 1 ? kinds[0] : area == .resources ? "note" : "task")
            }
            .fileImporter(isPresented: $importing, allowedContentTypes: [.item], allowsMultipleSelection: true) { result in
                switch result {
                case .success(let urls): Task { await store.performRemote { try await store.importFiles(urls, area: area ?? .personal) } }
                case .failure: store.resourceError = "No se pudo seleccionar el archivo"
                }
            }
    }
}

func displayDate(_ value: String) -> String {
    let format = ISO8601DateFormatter(); format.formatOptions = [.withInternetDateTime,.withFractionalSeconds]
    let fractional = format.date(from: value); format.formatOptions = [.withInternetDateTime]
    guard let date = fractional ?? format.date(from: value) else { return value }
    return date.formatted(.dateTime.day().month(.abbreviated).hour().minute().locale(Locale(identifier: "es_ES")))
}

struct ResourceDetailView: View {
    @Environment(AppStore.self) private var store
    @Environment(\.dismiss) private var dismiss
    let id: String
    @State private var editing = false
    @State private var confirmDelete = false
    @State private var downloaded: URL?
    @State private var downloading = false
    var body: some View {
        if let resource = store.resources.first(where: { $0.id == id }) {
            ScrollView {
                VStack(alignment: .leading, spacing: 22) {
                    HStack(spacing: 14) {
                        AreaIcon(area: resource.area, size: 48)
                        VStack(alignment: .leading, spacing: 5) {
                            Text(resource.title).font(.title2.bold())
                            Text(resource.kindTitle + " · " + resource.area.title).font(.caption).foregroundStyle(ArosTheme.secondary)
                        }
                    }
                    if resource.isDemo { DemoLabel() }
                    Text(resource.description.isEmpty ? "Sin descripción" : resource.description)
                        .font(.body).foregroundStyle(ArosTheme.secondary).textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading).padding(18).glass()
                    LabeledContent("Estado", value: resource.statusTitle)
                    if let date = resource.dueAt { LabeledContent("Fecha", value: displayDate(date)) }
                    ForEach(resource.data.keys.sorted(), id: \.self) { key in
                        if !["url","mime","size","partial","extracted","sha256","source_sha256","source_url","source_label","verified_source","indexed","thread_id"].contains(key), !(key == "exam" && resource.data[key] == .bool(false)), let value = resource.data[key], !value.text.isEmpty {
                            LabeledContent(["end_at":"Fin","location":"Lugar","filename":"Archivo","source_page":"Página del PDF","group":"Grupo","academic_year":"Curso académico","language":"Idioma","timezone":"Zona horaria","exam":"Examen","course_title":"Curso"][key] ?? key.replacingOccurrences(of: "_", with: " "), value: key == "end_at" ? displayDate(value.text) : value.text)
                        }
                    }
                    if let raw = resource.data["url"]?.text, let url = URL(string: raw), ["http","https"].contains(url.scheme) {
                        Link(destination: url) { Label("Abrir fuente original", systemImage: "arrow.up.right.square") }.frame(minHeight: 44)
                    }
                    if ["upload", "events-file"].contains(resource.source) && resource.data["filename"] != nil {
                        if let downloaded { ShareLink(item: downloaded) { Label("Compartir archivo descargado", systemImage: "square.and.arrow.up") } }
                        else {
                            Button {
                                downloading = true
                                Task {
                                    await store.performRemote { if let client = store.apiClient { downloaded = try await client.download(resource) } }
                                    downloading = false
                                }
                            } label: { Label(downloading ? "Descargando…" : "Descargar archivo", systemImage: "arrow.down.doc") }
                                .disabled(downloading).frame(minHeight: 44)
                        }
                    }
                    if ["task","reminder"].contains(resource.kind) && resource.editable {
                        Button(resource.status == "done" ? "Marcar pendiente" : "Completar tarea") { Task { await store.performRemote { try await store.completeResource(resource) } } }
                            .buttonStyle(.borderedProminent)
                    }
                    Button { store.openChat(); store.send("Resume " + resource.title, attachments: []) } label: {
                        Label("Preguntar a Aros", systemImage: "sparkles")
                    }.buttonStyle(.bordered)
                    Text("Fuente: " + (["upload":"Archivo importado","events-file":"Material de evento","canva-codex":"Canva","events-context":"Conversación de referencia"][resource.source] ?? resource.source)).font(.caption).foregroundStyle(ArosTheme.secondary)
                }.padding(20)
            }.navigationTitle(resource.kindTitle).inlineTitle()
                .toolbar {
                    ToolbarItem(placement: .primaryAction) {
                        Menu {
                            if resource.editable { Button("Editar", systemImage: "pencil") { editing = true } }
                            Button("Eliminar", systemImage: "trash", role: .destructive) { confirmDelete = true }
                        } label: { Image(systemName: "ellipsis.circle") }
                    }
                }
                .sheet(isPresented: $editing) { ResourceEditor(resource: resource, area: resource.area) }
                .confirmationDialog("¿Eliminar «\(resource.title)» de Aros?", isPresented: $confirmDelete, titleVisibility: .visible) {
                    Button("Eliminar recurso y archivo", role: .destructive) {
                        Task {
                            do { try await store.deleteResource(resource); dismiss() }
                            catch { store.resourceError = error.localizedDescription }
                        }
                    }
                } message: { Text("El original en servicios externos se conserva.") }
        } else { ContentUnavailableView("Recurso no disponible", systemImage: "doc", description: Text("Puede haberse eliminado. Recarga tu espacio.")) }
    }
}

struct ResourceEditor: View {
    @Environment(AppStore.self) private var store
    @Environment(\.dismiss) private var dismiss
    let resource: Resource?
    @State private var draft: ResourceDraft
    @State private var hasDate: Bool
    @State private var date: Date
    @State private var confirmingEvent = false
    @State private var saving = false
    @State private var errorMessage: String?
    init(resource: Resource? = nil, area: Area = .personal, initialKind: String = "task") {
        self.resource = resource
        _draft = State(initialValue: resource.map(ResourceDraft.init) ?? ResourceDraft(kind: initialKind, area: area))
        _hasDate = State(initialValue: resource?.dueAt != nil || initialKind == "event")
        let format = ISO8601DateFormatter(); format.formatOptions = [.withInternetDateTime,.withFractionalSeconds]
        let raw = resource?.dueAt ?? ""
        let parsed = format.date(from: raw); format.formatOptions = [.withInternetDateTime]
        _date = State(initialValue: parsed ?? format.date(from: raw) ?? .now)
    }
    var body: some View {
        NavigationStack {
            Form {
                if let errorMessage { Section { Text(errorMessage).foregroundStyle(.red) } }
                Section("Recurso") {
                    Picker("Tipo", selection: $draft.kind) {
                        ForEach(ResourceKind.names.keys.sorted(), id: \.self) { Text(ResourceKind.names[$0]!).tag($0) }
                    }.disabled(resource != nil)
                    Picker("Área", selection: $draft.area) { ForEach(Area.allCases) { Text($0.title).tag($0) } }
                    TextField("Título", text: $draft.title)
                    TextField("Descripción", text: $draft.description, axis: .vertical).lineLimit(3...8)
                }
                Section("Organizar") {
                    Toggle("Fecha", isOn: $hasDate)
                    if hasDate { DatePicker("Fecha y hora", selection: $date) }
                    Picker("Estado", selection: $draft.status) {
                        ForEach(ResourceKind.statuses.keys.sorted(), id: \.self) { Text(ResourceKind.statuses[$0]!).tag($0) }
                    }
                    Picker("Prioridad", selection: $draft.priority) {
                        Text("Normal").tag("normal"); Text("Alta").tag("high"); Text("Urgente").tag("urgent")
                    }
                }
                Section("Detalles") {
                    ForEach(detailFields, id: \.self) { key in
                        TextField(fieldLabel(key), text: Binding(get: { draft.data[key]?.text ?? "" }, set: { value in
                            if value.isEmpty { draft.data.removeValue(forKey: key) }
                            else if key == "amount", let number = Double(value.replacingOccurrences(of: ",", with: ".")) { draft.data[key] = .number(number) }
                            else { draft.data[key] = .string(value) }
                        }))
                    }
                }
            }.navigationTitle(resource == nil ? "Añadir recurso" : "Editar").inlineTitle()
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) { Button("Cancelar") { dismiss() }.disabled(saving) }
                    ToolbarItem(placement: .confirmationAction) {
                        Button(saving ? "Guardando…" : "Guardar") {
                            if draft.kind == "event" { confirmingEvent = true }
                            else { Task { await save() } }
                        }.disabled(saving || draft.title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || (draft.kind == "event" && !hasDate))
                    }
                }
                .confirmationDialog("Confirmar evento", isPresented: $confirmingEvent, titleVisibility: .visible) {
                    Button("Guardar evento en Aros") { Task { await save(confirmed: true) } }
                } message: { Text("\(draft.title)\n\(date.formatted())\nEl calendario externo no se modifica.") }
        }.interactiveDismissDisabled(saving)
    }
    private var detailFields: [String] {
        switch draft.kind {
        case "event": return ["location"]
        case "order": return ["store","amount","tracking_url"]
        case "expense": return ["amount"]
        case "contact": return ["role"]
        case "news","document","project","link": return ["url"]
        default: return []
        }
    }
    private func fieldLabel(_ key: String) -> String {
        ["location":"Lugar","url":"Enlace","material":"Material","store":"Tienda","amount":"Importe (€)","tracking_url":"Seguimiento","role":"Función","quantity":"Cantidad"][key] ?? key
    }
    private func save(confirmed: Bool = false) async {
        saving = true; errorMessage = nil; defer { saving = false }
        draft.dueAt = hasDate ? ISO8601DateFormatter().string(from: date) : nil
        do { try await store.saveResource(draft, id: resource?.id, confirmed: confirmed); dismiss() }
        catch { errorMessage = error.localizedDescription }
    }
}

struct RemoteInboxView: View {
    @Environment(AppStore.self) private var store
    @State private var showResolved = false
    var body: some View {
        List {
            Section { DemoLabel(); Toggle("Mostrar todos los estados", isOn: $showResolved) }
            let records = store.remoteInbox.filter { showResolved || ["new","seen"].contains($0.state) }
            if records.isEmpty { ContentUnavailableView("Todo al día", systemImage: "checkmark.circle") }
            ForEach(records) { item in
                VStack(alignment: .leading, spacing: 8) {
                    if let id = item.itemId {
                        NavigationLink(value: Destination.resource(id)) { Text(item.title).font(.headline) }
                    } else { Text(item.title).font(.headline) }
                    Text(item.reason).font(.caption).foregroundStyle(ArosTheme.secondary)
                    HStack {
                        Button(item.state == "resolved" ? "Reabrir" : "Resolver") {
                            Task { await store.performRemote { try await store.updateInbox(item.id, state: item.state == "resolved" ? "new" : "resolved") } }
                        }
                        Button("24 h") { Task { await store.performRemote { try await store.updateInbox(item.id, state: "snoozed") } } }
                        Button("Archivar") { Task { await store.performRemote { try await store.updateInbox(item.id, state: "archived") } } }
                    }.font(.caption).buttonStyle(.bordered)
                    Text(item.state).font(.caption2).foregroundStyle(ArosTheme.secondary)
                }.padding(.vertical, 7)
            }
        }.navigationTitle("Inbox").inlineTitle().refreshable { await store.refreshRemote() }
    }
}

@MainActor struct RemoteCalendarView: View {
    @Environment(AppStore.self) private var store
    @Environment(\.dynamicTypeSize) private var textSize
    @State private var day = Date()
    @State private var month = Date()
    @State private var plan: RemoteCalendarPlan?
    @State private var range: RemoteCalendarRange?
    @State private var error: String?
    @State private var monthError: String?
    @State private var loading = false
    @State private var monthLoading = false
    @State private var creating = false
    @State private var availabilityOpen = false
    private var dates: NativeCalendarDates { NativeCalendarDates() }
    private var dayKey: String { dates.key(day) }
    private var monthKey: String { dates.key(dates.monthStart(month)) }
    private var agenda: [RemoteCalendarPlan.CalendarEntry] {
        (plan?.events ?? range?.events.filter { dates.overlaps($0, day: day) } ?? []).sorted { $0.start < $1.start }
    }
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                DemoLabel()
                monthPanel
                weekStrip
                agendaPanel
                if let plan {
                    DisclosureGroup(isExpanded: $availabilityOpen) {
                        VStack(alignment: .leading, spacing: 14) {
                            Text("Solapamientos").font(.subheadline.weight(.semibold))
                            if plan.conflicts.isEmpty { Text("No se cruzan los eventos guardados.").foregroundStyle(ArosTheme.secondary) }
                            ForEach(Array(plan.conflicts.enumerated()), id: \.offset) { _, conflict in
                                VStack(alignment: .leading, spacing: 8) {
                                    Text(timeRange(conflict.start, conflict.end)).font(.subheadline.weight(.medium))
                                    ForEach(conflict.ids, id: \.self) { id in
                                        if let event = plan.events.first(where: { $0.id == id }) {
                                            NavigationLink(value: Destination.resource(id)) { Label(event.title, systemImage: "arrow.up.right") }.frame(minHeight: 44)
                                        }
                                    }
                                }
                            }
                            Divider()
                            Text("Huecos · 08:00 a 22:00").font(.subheadline.weight(.semibold))
                            if plan.freeSlots.isEmpty { Text("No quedan huecos de 30 minutos.").foregroundStyle(ArosTheme.secondary) }
                            ForEach(Array(plan.freeSlots.enumerated()), id: \.offset) { _, slot in
                                HStack { Text(timeRange(slot.start, slot.end)).monospacedDigit(); Spacer(); Text("\(slot.minutes) min").foregroundStyle(ArosTheme.secondary) }
                            }
                            Text(plan.note).font(.caption).foregroundStyle(ArosTheme.secondary)
                        }.padding(.top, 12)
                    } label: { Label("Disponibilidad del día", systemImage: "clock").font(.subheadline.weight(.semibold)).frame(minHeight: 44) }
                    .padding(16).glass()
                }
                Text("Horario local · \(TimeZone.current.identifier). Los eventos se guardan en tu espacio de trabajo.").font(.caption).foregroundStyle(ArosTheme.secondary)
            }.padding(18)
        }.background { ArosBackground() }
            .navigationTitle("Calendario").inlineTitle()
            .task(id: dayKey + "-\(store.remoteRevision)") { await loadPlan() }
            .task(id: monthKey + "-\(store.remoteRevision)") { await loadMonth() }
            .onChange(of: day) { _, value in month = value }
            .refreshable { await store.refreshRemote(); await reload() }
            .toolbar { ToolbarItem(placement: .primaryAction) { Button("Nuevo evento", systemImage: "plus") { creating = true } } }
            .sheet(isPresented: $creating, onDismiss: { Task { await reload() } }) { ResourceEditor(area: .personal, initialKind: "event") }
    }
    private var monthPanel: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text(dates.label(month, format: "MMMM yyyy").capitalized).font(.title3.weight(.semibold)).fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 4)
                Button { selectMonth(-1) } label: { Image(systemName: "chevron.left").frame(width: 44, height: 44) }.accessibilityLabel("Mes anterior")
                Button { selectMonth(1) } label: { Image(systemName: "chevron.right").frame(width: 44, height: 44) }.accessibilityLabel("Mes siguiente")
            }
            HStack {
                DatePicker("Fecha", selection: $day, displayedComponents: .date).labelsHidden().accessibilityLabel("Elegir fecha del calendario")
                Spacer(); Button("Hoy") { day = .now; month = .now }.buttonStyle(.bordered).frame(minHeight: 44)
            }
            if textSize.isAccessibilitySize {
                Text("Selecciona una fecha con el selector para consultar tu agenda.").font(.caption).foregroundStyle(ArosTheme.secondary)
            } else {
                LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 0), count: 7), spacing: 2) {
                    ForEach(Array(["L", "M", "X", "J", "V", "S", "D"].enumerated()), id: \.offset) { _, label in Text(label).font(.caption.weight(.semibold)).foregroundStyle(ArosTheme.secondary).accessibilityHidden(true) }
                    ForEach(dates.monthDays(month), id: \.self) { date in dayCell(date) }
                }
            }
            if monthLoading { HStack { ProgressView(); Text("Actualizando el mes…").font(.caption).foregroundStyle(ArosTheme.secondary) } }
            if let monthError { Text(monthError).font(.caption).foregroundStyle(.red); Button("Reintentar el mes") { Task { await loadMonth() } }.frame(minHeight: 44) }
        }.padding(14).glass()
    }
    private func dayCell(_ date: Date) -> some View {
        let selected = dates.calendar.isDate(date, inSameDayAs: day)
        let today = dates.calendar.isDateInToday(date)
        let entries = range?.events.filter { dates.overlaps($0, day: date) } ?? []
        return Button { day = date } label: {
            VStack(spacing: 4) {
                Text(dates.label(date, format: "d")).font(.subheadline.weight(selected || today ? .bold : .regular)).monospacedDigit()
                HStack(spacing: 3) {
                    ForEach(Array(Set(entries.map(\.area))).sorted { $0.rawValue < $1.rawValue }.prefix(3), id: \.self) { area in Circle().fill(selected ? Color.white : area.tint).frame(width: 4, height: 4) }
                }.frame(height: 4)
            }.frame(maxWidth: .infinity, minHeight: 44)
                .foregroundStyle(selected ? Color.white : dates.calendar.isDate(date, equalTo: month, toGranularity: .month) ? ArosTheme.ink : ArosTheme.secondary)
                .background(selected ? Area.learning.tint : today ? Area.learning.tint.opacity(0.12) : Color.clear, in: RoundedRectangle(cornerRadius: 12))
        }.buttonStyle(.plain)
            .accessibilityLabel(dates.label(date, format: "EEEE d MMMM") + (today ? ", hoy" : "") + (monthLoading ? ", actualizando eventos" : ", \(entries.count) eventos"))
            .accessibilityAddTraits(selected ? .isSelected : [])
    }
    private var weekStrip: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                ForEach(dates.weekDays(day), id: \.self) { date in
                    let selected = dates.calendar.isDate(date, inSameDayAs: day)
                    Button { day = date } label: {
                        VStack(spacing: 6) { Text(dates.label(date, format: "EEE")).font(.caption); Text(dates.label(date, format: "d")).font(.headline).monospacedDigit() }
                            .frame(minWidth: textSize.isAccessibilitySize ? 70 : 44, minHeight: 64).padding(.horizontal, 4)
                            .foregroundStyle(selected ? Color.white : ArosTheme.ink)
                            .background(selected ? Area.learning.tint : Color.white.opacity(0.8), in: RoundedRectangle(cornerRadius: 14))
                    }.buttonStyle(.plain).accessibilityLabel(dates.label(date, format: "EEEE d MMMM")).accessibilityAddTraits(selected ? .isSelected : [])
                }
            }
        }.accessibilityLabel("Semana seleccionada")
    }
    private var agendaPanel: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(dates.label(day, format: "EEEE d 'de' MMMM").capitalized).font(.title3.weight(.semibold)).fixedSize(horizontal: false, vertical: true)
            if loading { ProgressView("Consultando tu día…") }
            else if let error {
                Label(error, systemImage: "exclamationmark.circle").font(.subheadline).foregroundStyle(.red)
                Button("Reintentar") { Task { await loadPlan() } }.buttonStyle(.bordered).frame(minHeight: 44)
            } else {
                HStack { Label("\(agenda.count) eventos", systemImage: "calendar"); if let plan { Spacer(); Text("\(plan.conflicts.count) cruces").foregroundStyle(ArosTheme.secondary) } }.font(.caption)
                if agenda.isEmpty {
                    VStack(alignment: .leading, spacing: 8) { Label("Un día sin eventos", systemImage: "sun.max").font(.headline); Text("Tu agenda está libre para esta fecha. Puedes añadir un evento o explorar otros días.").font(.subheadline).foregroundStyle(ArosTheme.secondary) }.padding(.vertical, 12)
                }
                ForEach(agenda) { event in agendaRow(event) }
            }
        }.padding(16).glass()
    }
    private func agendaRow(_ event: RemoteCalendarPlan.CalendarEntry) -> some View {
        let metadata = range?.events.first { $0.id == event.id } ?? event
        let exam = metadata.category.map { $0 == "exam" } ?? metadata.title.localizedCaseInsensitiveContains("examen")
        let tint = exam ? Color(red: 196 / 255.0, green: 92 / 255.0, blue: 74 / 255.0) : event.area.tint
        return NavigationLink(value: Destination.resource(event.id)) {
            HStack(alignment: .top, spacing: 12) {
                RoundedRectangle(cornerRadius: 3).fill(tint).frame(width: 4)
                VStack(alignment: .leading, spacing: 6) {
                    Text(event.allDay ? "Todo el día" : timeRange(event.start, event.end)).font(.caption.weight(.medium)).monospacedDigit().foregroundStyle(ArosTheme.secondary)
                    Text(event.title).font(.subheadline.weight(.semibold)).foregroundStyle(ArosTheme.ink).fixedSize(horizontal: false, vertical: true)
                    Label(exam ? "Examen · \(event.area.title)" : event.area.title, systemImage: exam ? "pencil.and.outline" : event.area.symbol).font(.caption).foregroundStyle(tint)
                    if let location = metadata.location, !location.isEmpty { Label(location, systemImage: "mappin.and.ellipse").font(.caption).foregroundStyle(ArosTheme.secondary) }
                    if event.estimatedDuration { Text("Duración estimada: 60 minutos").font(.caption2).foregroundStyle(ArosTheme.secondary) }
                }
                Spacer(minLength: 0); Image(systemName: "chevron.right").font(.caption).foregroundStyle(ArosTheme.secondary)
            }.padding(12).frame(minHeight: 44).background(tint.opacity(0.06), in: RoundedRectangle(cornerRadius: 14))
        }.buttonStyle(.plain)
    }
    private func selectMonth(_ offset: Int) {
        if let next = dates.calendar.date(byAdding: .month, value: offset, to: day) { day = next; month = next }
    }
    private func timeRange(_ start: Date, _ end: Date) -> String {
        dates.timeRange(start, end)
    }
    private func reload() async { async let daily: Void = loadPlan(); async let monthly: Void = loadMonth(); _ = await (daily, monthly) }
    private func loadPlan() async {
        guard let client = store.apiClient else { return }
        let revision = store.remoteRevision, requestedDay = dayKey
        var query = URLComponents(); query.queryItems = [URLQueryItem(name: "day", value: requestedDay), URLQueryItem(name: "zone", value: TimeZone.current.identifier)]
        loading = true; error = nil; plan = nil
        do {
            let result: RemoteCalendarPlan = try await client.request("/calendar/day?" + (query.percentEncodedQuery ?? ""))
            guard !Task.isCancelled, revision == store.remoteRevision, requestedDay == dayKey else { return }
            plan = result; loading = false
        } catch {
            guard !Task.isCancelled, revision == store.remoteRevision, requestedDay == dayKey else { return }
            self.error = error.localizedDescription; loading = false
        }
    }
    private func loadMonth() async {
        guard let client = store.apiClient else { return }
        let revision = store.remoteRevision, requestedMonth = monthKey, bounds = dates.monthBounds(month)
        var query = URLComponents(); query.queryItems = [URLQueryItem(name: "start", value: dates.key(bounds.start)), URLQueryItem(name: "end", value: dates.key(bounds.end)), URLQueryItem(name: "zone", value: TimeZone.current.identifier)]
        monthLoading = true; monthError = nil; range = nil
        do {
            let result: RemoteCalendarRange = try await client.request("/calendar/range?" + (query.percentEncodedQuery ?? ""))
            guard !Task.isCancelled, revision == store.remoteRevision, requestedMonth == monthKey else { return }
            range = result; monthLoading = false
        } catch {
            guard !Task.isCancelled, revision == store.remoteRevision, requestedMonth == monthKey else { return }
            monthError = error.localizedDescription; monthLoading = false
        }
    }
}

struct EventsLibraryView: View {
    @Environment(AppStore.self) private var store
    @Environment(\.openURL) private var openURL
    @State private var library: RemoteEventsLibrary?
    @State private var applicationOpen = false
    @State private var clientID = ""
    @State private var clientSecret = ""
    @State private var brief = ""
    @State private var reference = ""
    @State private var busy = false
    @State private var error: String?
    @State private var notice: String?
    private var pendingDesign: Bool { library?.assets.contains { $0.source == "canva-request" && ["pending", "active"].contains($0.status) } == true }
    var body: some View {
        List {
            Section("Canva") {
                Text("Canva conectado en Codex autoriza a Codex. Aros necesita su propia aplicación OAuth y tu autorización.").font(.caption).foregroundStyle(ArosTheme.secondary)
                Text("La autorización se completa en el navegador. Inicia sesión en Aros web con tu misma cuenta antes de conectar Canva.").font(.caption).foregroundStyle(ArosTheme.secondary)
                DisclosureGroup("Configurar aplicación Canva", isExpanded: $applicationOpen) {
                    Link("Portal de Canva Developers", destination: URL(string: "https://www.canva.com/developers/apps")!)
                    Text("Activa las API REST en Outside Canva y registra la dirección de retorno que muestra Aros.").font(.caption)
                    if let callback = library?.canva.redirectUri { Text(callback).font(.caption).textSelection(.enabled) }
                    if let scopes = library?.canva.scopes, !scopes.isEmpty { Text("Permisos: \(scopes.joined(separator: ", "))").font(.caption) }
                    TextField("Client ID", text: $clientID).autocorrectionDisabled()
                    SecureField("Client secret", text: $clientSecret).autocorrectionDisabled()
                    Text("Introduce el secreto aquí, nunca en el chat. No se guarda en el dispositivo y se limpia después de cada intento.").font(.caption).foregroundStyle(ArosTheme.secondary)
                    Button("Guardar aplicación OAuth") { Task { await saveApplication() } }.disabled(busy || clientID.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || clientSecret.count < 8 || library?.canva.redirectUri == nil)
                }.onChange(of: applicationOpen) { _, open in if !open { clientSecret = "" } }

                Text(library?.canva.connected == true ? "Canva autorizado" : library?.canva.configured == true ? "Falta autorizar tu cuenta de Canva" : "Configura una aplicación Canva autorizada en Aros web")
                if library?.canva.configured == true && library?.canva.connected == false { Button("Autorizar Canva en Aros web") { Task { await authorize() } }.disabled(busy) }
                Button("Sincronizar diseños") { Task { await synchronize() } }.disabled(busy || library?.canva.connected != true)
            }
            Section("Diseños, documentos y referencias") {
                if library?.assets.isEmpty == true { Text("Todavía no hay materiales importados.").foregroundStyle(ArosTheme.secondary) }
                ForEach(EventsAssetOrder.ordered(library?.assets ?? [])) { asset in
                    VStack(alignment: .leading, spacing: 8) {
                        if asset.data["category"]?.text == "context" {
                            Label("Referencia histórica", systemImage: "text.book.closed").font(.caption).foregroundStyle(ArosTheme.secondary)
                            DisclosureGroup("Leer referencia completa") { Text(asset.description).font(.subheadline).textSelection(.enabled) }
                        } else if asset.data["preview_item_id"]?.text.isEmpty == false || ["pdf", "png", "jpg", "jpeg"].contains((asset.data["filename"]?.text ?? "").split(separator: ".").last.map(String.init)?.lowercased() ?? "") { EventsAssetPreview(id: asset.id).frame(height: 140) }
                        if asset.data["filename"]?.text.isEmpty == false { EventsOriginalActions(asset: asset) }
                        NavigationLink(value: Destination.resource(asset.id)) { Text(asset.title).font(.headline) }
                        if asset.data["review_state"]?.text == "needs_review" { Label("Pendiente de revisar", systemImage: "exclamationmark.circle").font(.caption).foregroundStyle(Color(red: 0.5, green: 0.35, blue: 0.1)) }
                        if asset.data["review_state"]?.text == "approved" { Label("Referencia aprobada", systemImage: "checkmark.seal").font(.caption).foregroundStyle(.green) }
                        Text(asset.description).font(.caption).lineLimit(3)
                        if let state = asset.data["state"]?.text { Text("Estado: \(state)").font(.caption).foregroundStyle(ArosTheme.secondary) }
                        if let issue = asset.data["error"]?.text, !issue.isEmpty { Text(issue).font(.caption).foregroundStyle(.red) }
                        if let value = asset.data["canva_edit_url"]?.text, let url = URL(string: value), url.scheme == "https" { Link("Editar en Canva", destination: url) }
                    }.padding(.vertical, 6)
                }
            }
            Section("Preparar un diseño") {
                if pendingDesign { Text("Diseño en preparación · estado actualizado cada 3 segundos").font(.caption) }
                TextField("Formato, objetivo, textos y estilo…", text: $brief, axis: .vertical).lineLimit(3...8)
                Picker("Referencia", selection: $reference) {
                    Text("Sin referencia").tag("")
                    ForEach((library?.assets ?? []).filter { $0.source != "canva-request" }) { Text($0.title).tag($0.id) }
                }
                Text("Esta solicitud usa el modelo de diseño configurado por el operador. El resto de Aros mantiene tu modelo de Ajustes.").font(.caption).foregroundStyle(ArosTheme.secondary)
                Button("Solicitar diseño avanzado") { Task { await design() } }.disabled(busy || pendingDesign || library?.canva.connected != true || library?.canva.designAvailable != true || store.remoteUser?.aiConsent != true || brief.trimmingCharacters(in: .whitespacesAndNewlines).count < 10)
                if library?.canva.connected != true { Text("Autoriza Canva para crear una propuesta editable.").font(.caption) }
                if library?.canva.designAvailable != true { Text("El operador debe configurar una conexión de agentes y un modelo de diseño para habilitar esta función.").font(.caption) }
                if store.remoteUser?.aiConsent != true { Text("Autoriza el uso de IA externa en Ajustes para solicitar un diseño.").font(.caption) }
            }
            Section { NavigationLink { ResourceListView(area: .events) } label: { Label("Todos mis recursos de eventos", systemImage: "folder") } }
            if busy { ProgressView("Consultando Canva…") }
            if let notice { Text(notice).font(.caption) }
            if let issue = error ?? library?.canva.error { Text(issue).foregroundStyle(.red) }
        }.navigationTitle("Eventos").inlineTitle().task { await load() }.refreshable { await load() }
            .task(id: pendingDesign) {
                guard pendingDesign else { return }
                while !Task.isCancelled && pendingDesign {
                    do { try await Task.sleep(for: .seconds(3)) } catch { return }
                    await load()
                }
            }
    }
    private func load() async { guard let client = store.apiClient else { return }; do { library = try await client.request("/events/library") } catch { self.error = error.localizedDescription } }
    private func saveApplication() async {
        guard let client = store.apiClient else { return }; busy = true; error = nil; defer { busy = false; clientSecret = "" }
        do {
            let _: JSONValue = try await client.request("/events/canva/application", method: "POST", body: ["client_id":clientID.trimmingCharacters(in: .whitespacesAndNewlines),"client_secret":clientSecret.trimmingCharacters(in: .whitespacesAndNewlines)])
            clientID = ""; applicationOpen = false; notice = "Aplicación guardada. Autoriza ahora tu cuenta de Canva en Aros."
            await load()
        } catch { self.error = error.localizedDescription }
    }
    private func authorize() async {
        guard let client = store.apiClient else { return }
        var components = URLComponents(url: client.baseURL, resolvingAgainstBaseURL: false)
        components?.fragment = "events"
        if let url = components?.url { openURL(url) }
    }
    private func synchronize() async {
        guard let client = store.apiClient else { return }; busy = true; error = nil; defer { busy = false }
        do { let _: JSONValue = try await client.request("/events/canva/sync", method: "POST", body: [:]); await load() } catch { self.error = error.localizedDescription }
    }
    private func design() async {
        guard let client = store.apiClient else { return }; busy = true; error = nil; defer { busy = false }
        do {
            var body: [String: Any] = ["brief":brief]
            if !reference.isEmpty { body["asset_id"] = reference }
            let result: Resource = try await client.request("/events/canva/design", method: "POST", body: body)
            notice = result.data["state"]?.text == "blocked" ? "Solicitud bloqueada: revisa Canva." : "Solicitud en cola. El enlace editable aparecerá cuando termine."
            brief = ""; await load()
        } catch { self.error = error.localizedDescription }
    }
}
struct EventsAssetPreview: View {
    @Environment(AppStore.self) private var store
    let id: String
    @State private var content: Data?
    var body: some View {
        Group {
            #if os(iOS)
            if let content, let image = UIImage(data: content) { Image(uiImage: image).resizable().scaledToFit() } else { placeholder }
            #else
            if let content, let image = NSImage(data: content) { Image(nsImage: image).resizable().scaledToFit() } else { placeholder }
            #endif
        }.accessibilityLabel("Vista previa del material")
            .task(id: id) {
                guard let client = store.apiClient else { return }
                var request = URLRequest(url: client.baseURL.appendingPathComponent("api/v1/events/assets/\(id)/preview"))
                request.setValue("Bearer \(client.token)", forHTTPHeaderField: "Authorization")
                if let (data,response) = try? await URLSession.shared.data(for: request), let response = response as? HTTPURLResponse, response.statusCode == 200 { content = data }
            }
    }
    private var placeholder: some View { Label("Vista previa no disponible", systemImage: "photo").font(.caption).foregroundStyle(ArosTheme.secondary).frame(maxWidth: .infinity, maxHeight: .infinity) }
}
struct EventsOriginalActions: View {
    @Environment(AppStore.self) private var store
    let asset: Resource
    @State private var downloaded: URL?
    @State private var busy = false
    @State private var error: String?
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Button(busy ? "Descargando…" : "Descargar original") { Task {
                guard let client = store.apiClient else { return }; busy = true; error = nil; defer { busy = false }
                do { downloaded = try await client.download(asset) } catch { self.error = error.localizedDescription }
            } }.disabled(busy)
            if let downloaded { ShareLink(item: downloaded) { Label("Abrir o compartir original", systemImage: "square.and.arrow.up") } }
            if let error { Text(error).font(.caption).foregroundStyle(.red) }
        }
    }
}
