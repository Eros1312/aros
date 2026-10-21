import SwiftUI
import UniformTypeIdentifiers

@MainActor struct ChatView: View {
    @Environment(AppStore.self) private var store
    @Environment(\.dismiss) private var dismiss
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency
    @State private var composer = ""
    @State private var attachments: [String] = []
    @State private var selectedResources: [Resource] = []
    @State private var importing = false
    @State private var importerPresented = false
    @State private var attachmentError: String?
    @State private var dictation = DictationController()
    @State private var dictationPrefix = ""
    @State private var playback = VoicePlaybackController()
    @State private var readResponses = false
    @State private var voiceRevision = 0
    @State private var visible = false
    @FocusState private var focused: Bool
    let launch: ChatLaunch

    var body: some View {
        NavigationStack {
            ScrollViewReader { proxy in
                ScrollView {
                    VStack(alignment: .leading, spacing: 18) {
                        Label(store.connected ? "Con tu espacio · puede gestionar los cambios internos que le pidas" : "Asistente de demostración · sin IA conectada", systemImage: store.connected ? "checkmark.icloud" : "flask")
                            .font(.caption).foregroundStyle(ArosTheme.secondary)
                        if store.connected ? store.remoteMessages.isEmpty : store.messages.isEmpty { welcome }
                        if store.connected {
                            ForEach(store.remoteMessages) { message in
                                RemoteMessageBubble(message: message) { id in dismiss(); store.open(.resource(id)) }.id(message.id)
                            }
                            if let job = store.remoteChatJob { executionProgress(job) }
                            else if store.isSending { ProgressView("Recibiendo tu mensaje…") }
                        }
                        ForEach(store.connected ? [] : store.messages) { message in
                            MessageBubble(message: message) { area in
                                dismiss()
                                store.open(.area(area))
                            }.id(message.id)
                        }
                        Color.clear.frame(height: 1).id("bottom")
                    }.padding(20)
                }
                .onChange(of: store.messages.count + store.remoteMessages.count) { _, _ in proxy.scrollTo("bottom", anchor: .bottom) }
                .onChange(of: store.remoteChatJob?.execution?.steps?.count) { _, _ in proxy.scrollTo("bottom", anchor: .bottom) }
            }
            .background { ArosBackground() }
            .safeAreaInset(edge: .bottom) { inputBar }
            .navigationTitle("Aros.").inlineTitle()
            .toolbar {
                ToolbarItem(placement: .confirmationAction) { Button("Cerrar") { dismiss() } }
            }
        }
        .fileImporter(isPresented: $importerPresented, allowedContentTypes: [.item], allowsMultipleSelection: true) { result in
            switch result {
            case .success(let urls):
                if store.connected {
                    Task {
                        guard selectedResources.count + urls.count <= 20 else { attachmentError = "Selecciona como máximo 20 archivos."; return }
                        importing = true
                        let account = voiceAccount
                        defer { importing = false }
                        do {
                            try await store.importFiles(urls, area: .personal) { resource in
                                if account == voiceAccount { selectedResources.append(resource) }
                            }
                            guard account == voiceAccount else { return }
                            if composer.isEmpty { composer = "Resume los archivos seleccionados" }
                        }
                        catch { attachmentError = error.localizedDescription }
                    }
                } else { attachments.append(contentsOf: urls.map(\.lastPathComponent)) }
            case .failure: attachmentError = "No se ha podido seleccionar el archivo. Inténtalo de nuevo."
            }
        }
        .alert("Archivo no disponible", isPresented: Binding(get: { attachmentError != nil }, set: { if !$0 { attachmentError = nil } })) {
            Button("Entendido", role: .cancel) { attachmentError = nil }
        } message: { Text(attachmentError ?? "") }
        .task {
            visible = true
            loadVoicePreference()
            switch launch {
            case .write: focused = true
            case .attach: importerPresented = true
            case .dictate: await beginDictation()
            }
        }
        .onChange(of: dictation.transcript) { _, value in
            composer = dictationPrefix + (dictationPrefix.isEmpty ? "" : " ") + value
        }
        .onChange(of: scenePhase) { _, phase in
            // Permission dialogs make the scene inactive; let pending authorization finish.
            if phase != .active { stopVoice(cancelDictation: phase == .background || dictation.isRecording) }
        }
        .onChange(of: voiceAccount) { _, _ in stopVoice(); playback.reset(); selectedResources = []; attachments = []; composer = ""; loadVoicePreference() }
        .onChange(of: readResponses) { _, value in
            UserDefaults.standard.set(value, forKey: voicePreferenceKey)
            playback.configureReading(enabled: value)
        }
        .onDisappear { visible = false; stopVoice() }
    }

    private var welcome: some View {
        VStack(alignment: .leading, spacing: 16) {
            ArosOrb(size: 55)
            Text("¿Qué necesitas\nsaber ahora?").font(.largeTitle.bold()).tracking(-1)
            Text("Explora tu día, tus proyectos y lo que requiere atención.")
                .foregroundStyle(ArosTheme.secondary)
            ForEach(["Prepárame el día", "¿Qué me falta de PRISMA?", "¿Cómo va el servidor?"], id: \.self) { suggestion in
                Button { focused = false; sendMessage(suggestion) } label: {
                    HStack { Text(suggestion); Spacer(); Image(systemName: "arrow.up.right") }
                        .font(.subheadline).padding(15).glass(radius: 16)
                }.buttonStyle(.plain).foregroundStyle(ArosTheme.ink)
            }
        }.padding(.vertical, 14)
    }

    private var inputBar: some View {
        VStack(alignment: .leading, spacing: 8) {
            voiceControls
            if importing { ProgressView("Importando los archivos seleccionados…").font(.caption) }
            if !selectedResources.isEmpty {
                Text("Contexto de esta conversación · \(selectedResources.count) archivos").font(.caption).foregroundStyle(ArosTheme.secondary)
                ForEach(selectedResources) { resource in
                    HStack {
                        Label(resource.title, systemImage: "doc.text").font(.caption)
                        Spacer()
                        Button { selectedResources.removeAll { $0.id == resource.id } } label: {
                            Image(systemName: "xmark.circle").frame(width: 44, height: 44)
                        }.accessibilityLabel("Quitar \(resource.title) del contexto")
                    }
                }
            }
            if let error = dictation.errorMessage {
                Text(error).font(.caption).foregroundStyle(ArosTheme.secondary).padding(.horizontal, 6)
            }
            if dictation.isRecording || dictation.isPreparing {
                Label(dictation.isRecording ? "Escuchando… Toca el micrófono para terminar." : "Preparando dictado…", systemImage: "waveform")
                    .font(.caption).foregroundStyle(ArosTheme.blue).padding(.horizontal, 6)
            }
            if !attachments.isEmpty {
                ScrollView(.horizontal) {
                    HStack(spacing: 10) {
                        ForEach(Array(attachments.enumerated()), id: \.offset) { index, name in
                            HStack {
                                Label(name, systemImage: "doc").font(.caption).lineLimit(1)
                                Button { attachments.remove(at: index) } label: {
                                    Image(systemName: "xmark.circle.fill").frame(width: 44, height: 44)
                                }.accessibilityLabel("Quitar \(name)")
                            }.padding(.leading, 10).background(.white.opacity(0.8), in: Capsule())
                        }
                    }
                }
            }
            HStack(alignment: .bottom, spacing: 3) {
                Button { stopVoice(); importerPresented = true } label: {
                    Image(systemName: "paperclip").frame(width: 44, height: 44)
                }.accessibilityLabel("Adjuntar un archivo").disabled(importing || store.isSending)
                TextField("Pregunta a Aros…", text: $composer, axis: .vertical)
                    .lineLimit(1...5).focused($focused).padding(.vertical, 12)
                    .accessibilityLabel("Mensaje para Aros")
                    .disabled(dictation.isRecording || dictation.isPreparing)
                Button {
                    if dictation.isRecording || dictation.isPreparing { dictation.stop() }
                    else { Task { await beginDictation() } }
                } label: {
                    Image(systemName: dictation.isRecording ? "stop.circle.fill" : "mic")
                        .frame(width: 44, height: 44)
                }.accessibilityLabel(dictation.isRecording || dictation.isPreparing ? "Detener dictado" : "Dictar mensaje")
                Button {
                    sendMessage(composer)
                } label: {
                    Image(systemName: "arrow.up").font(.headline).foregroundStyle(.white)
                        .frame(width: 44, height: 44).background(ArosTheme.blue, in: Circle())
                }.disabled(importing || store.isSending || (composer.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && attachments.isEmpty))
                    .accessibilityLabel(store.connected ? "Enviar mensaje" : "Enviar mensaje de demostración")
            }.padding(7).glass(radius: 25)
        }.padding(.horizontal, 14).padding(.vertical, 10).background {
            if reduceTransparency { Color.white }
            else { Rectangle().fill(.regularMaterial) }
        }
    }

    private var voiceAccount: String { store.remoteUser?.id ?? "demonstration" }
    private var voicePreferenceKey: String { "aros.voice.read.v1." + voiceAccount }

    private var voiceControls: some View {
        VStack(alignment: .leading, spacing: 3) {
            Toggle("Leer respuestas nuevas", isOn: $readResponses).font(.subheadline).frame(minHeight: 44)
            HStack {
                Button { readResponses = false; playback.stop() } label: { Label("Silenciar", systemImage: "speaker.slash").frame(minHeight: 44) }
                    .disabled(!readResponses && !playback.isSpeaking)
                Spacer()
                Button { stopVoice() } label: { Label("Parar voz", systemImage: "stop.circle").frame(minHeight: 44) }
                    .disabled(!playback.isSpeaking && !dictation.isRecording && !dictation.isPreparing && !store.isSending)
            }.font(.subheadline)
            if playback.isSpeaking || readResponses { Text(playback.status).font(.caption).foregroundStyle(ArosTheme.secondary) }
            DisclosureGroup("Voz y privacidad") {
                Text("El dictado solo rellena el mensaje: revísalo y pulsa Enviar. La lectura usa la voz del sistema en español. Solo se recuerda en este dispositivo tu preferencia de lectura por cuenta; el micrófono no se reactiva automáticamente. El reconocimiento puede usar los servicios de Apple si no hay reconocimiento local disponible. Parar la voz no cancela un mensaje ya enviado.")
                    .font(.caption).foregroundStyle(ArosTheme.secondary)
            }.font(.caption)
        }
    }

    private func loadVoicePreference() { readResponses = UserDefaults.standard.bool(forKey: voicePreferenceKey); playback.configureReading(enabled: readResponses) }
    private func stopVoice(cancelDictation: Bool = true) {
        voiceRevision += 1
        playback.stop()
        if cancelDictation { dictation.stop() }
    }
    private func sendMessage(_ text: String) {
        guard !store.isSending, !importing else { return }
        stopVoice()
        let revision = voiceRevision, account = voiceAccount
        if store.connected {
            composer = ""
            let itemIDs = selectedResources.map(\.id)
            Task {
                let result = await store.sendRemote(text, itemIDs: itemIDs)
                guard visible, scenePhase == .active, revision == voiceRevision, account == voiceAccount else { return }
                guard let result else { if store.remoteChatJob == nil { composer = text }; return }
                dictation.stop()
                playback.readNewReply(id: result.id, text: result.text, enabled: readResponses)
            }
        } else {
            store.send(text, attachments: attachments)
            composer = ""; attachments = []
            if let result = store.messages.last, !result.isUser {
                playback.readNewReply(id: result.id.uuidString, text: result.text, enabled: readResponses)
            }
        }
    }

    private func executionProgress(_ job: RemoteAgentJob) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            if job.execution?.outcome == "partial" || job.execution?.state == "partially_completed" {
                Label("Resuelto en parte · consulta los pasos pendientes", systemImage: "exclamationmark.circle").font(.subheadline)
            }
            if ["pending", "running"].contains(job.status) {
                HStack {
                    ProgressView()
                    Text(job.status == "pending" ? "Mensaje registrado en Aros" : "Trabajando con tu espacio")
                    Spacer()
                    Button("Cancelar") { stopVoice(); Task { await store.cancelRemoteChat() } }
                }.font(.subheadline)
            }
            if let steps = job.execution?.steps, !steps.isEmpty {
                DisclosureGroup("Pasos realizados · \(steps.count)") {
                    ForEach(Array(steps.enumerated()), id: \.offset) { _, step in
                        VStack(alignment: .leading, spacing: 5) {
                            Label(step.summary, systemImage: step.status == "error" ? "exclamationmark.circle" : "checkmark.circle")
                                .font(.caption)
                            ForEach((step.artifacts ?? []).filter { id in job.execution?.artifacts?.contains { $0.id == id && $0.verified == true } == true }, id: \.self) { id in
                                Button("Abrir resultado") { dismiss(); store.open(.resource(id)) }.font(.caption)
                            }
                        }.padding(.vertical, 4)
                    }
                }.font(.subheadline)
            }
            if !job.error.isEmpty { Text(job.error).font(.caption).foregroundStyle(ArosTheme.secondary) }
            if let error = store.resourceError { Text(error).font(.caption).foregroundStyle(ArosTheme.secondary) }
        }.padding(14).glass(radius: 18)
    }

    private func beginDictation() async {
        stopVoice()
        focused = false
        dictationPrefix = composer.trimmingCharacters(in: .whitespacesAndNewlines)
        await dictation.start()
    }
}

struct MessageBubble: View {
    let message: ChatMessage
    let openSource: (Area) -> Void
    var body: some View {
        HStack {
            if message.isUser { Spacer(minLength: 35) }
            VStack(alignment: .leading, spacing: 12) {
                if !message.isUser {
                    HStack(spacing: 7) { ArosOrb(size: 20); Text("Aros · ejemplo").font(.caption.weight(.semibold)) }
                }
                if !message.text.isEmpty { Text(message.text).font(.subheadline).textSelection(.enabled) }
                ForEach(Array(message.attachmentNames.enumerated()), id: \.offset) { _, name in
                    Label(name, systemImage: "doc").font(.caption)
                }
                ForEach(message.sources) { area in
                    Button { openSource(area) } label: {
                        Label("Abrir \(area.title)", systemImage: area.symbol).font(.caption.weight(.medium))
                            .frame(minHeight: 44)
                    }.buttonStyle(.bordered).tint(area.tint)
                }
            }.padding(16)
                .background(message.isUser ? ArosTheme.blue.opacity(0.09) : .white.opacity(0.8), in: RoundedRectangle(cornerRadius: 20))
            if !message.isUser { Spacer(minLength: 15) }
        }.foregroundStyle(ArosTheme.ink)
    }
}

struct RemoteMessageBubble: View {
    let message: RemoteMessage
    let openSource: (String) -> Void
    var body: some View {
        HStack {
            if message.role == "user" { Spacer(minLength: 30) }
            VStack(alignment: .leading, spacing: 12) {
                Text(message.role == "user" ? "Tú" : message.mode != "local" ? "Aros · IA externa" : "Aros · resumen local").font(.caption.weight(.semibold)).foregroundStyle(ArosTheme.secondary)
                Text(message.text).font(.subheadline).textSelection(.enabled)
                ForEach(message.sources) { source in
                    Button { openSource(source.id) } label: { Label(source.title, systemImage: "doc.text").font(.caption).frame(minHeight: 44) }.buttonStyle(.bordered)
                }
            }.padding(16).background(message.role == "user" ? ArosTheme.blue.opacity(0.09) : .white.opacity(0.9), in: RoundedRectangle(cornerRadius: 20))
            if message.role != "user" { Spacer(minLength: 30) }
        }
    }
}
