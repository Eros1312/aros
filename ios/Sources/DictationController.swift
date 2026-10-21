import SwiftUI
import Observation
import AVFoundation
#if os(iOS)
import Speech
#endif

@MainActor @Observable
final class DictationController {
    var transcript = ""
    var isRecording = false
    var isPreparing = false
    var errorMessage: String?
    private var generation = 0
    #if os(iOS)
    private let engine = AVAudioEngine()
    private let recognizer = SFSpeechRecognizer(locale: Locale(identifier: "es_ES"))
    private var request: SFSpeechAudioBufferRecognitionRequest?
    private var task: SFSpeechRecognitionTask?
    private var hasTap = false
    #endif

    func start() async {
        guard !isRecording && !isPreparing else { return }
        errorMessage = nil
        isPreparing = true
        generation += 1
        let revision = generation
        #if os(iOS)
        let speechStatus = await withCheckedContinuation { continuation in
            SFSpeechRecognizer.requestAuthorization { continuation.resume(returning: $0) }
        }
        guard revision == generation else { return }
        guard speechStatus == .authorized else {
            isPreparing = false
            errorMessage = "El reconocimiento de voz no está autorizado. Puedes habilitarlo en Ajustes de iOS o escribir tu mensaje."
            return
        }
        let microphone = await withCheckedContinuation { continuation in
            AVAudioSession.sharedInstance().requestRecordPermission { continuation.resume(returning: $0) }
        }
        guard revision == generation else { return }
        guard microphone, let recognizer, recognizer.isAvailable else {
            isPreparing = false
            errorMessage = microphone ? "El dictado no está disponible ahora. Puedes escribir tu mensaje." : "El micrófono no está autorizado. Puedes escribir o activar el permiso en Ajustes de iOS."
            return
        }
        do {
            let session = AVAudioSession.sharedInstance()
            try session.setCategory(.record, mode: .measurement, options: .duckOthers)
            try session.setActive(true, options: .notifyOthersOnDeactivation)
            let request = SFSpeechAudioBufferRecognitionRequest()
            request.shouldReportPartialResults = true
            request.requiresOnDeviceRecognition = recognizer.supportsOnDeviceRecognition
            self.request = request
            let input = engine.inputNode
            let format = input.outputFormat(forBus: 0)
            guard format.sampleRate > 0, format.channelCount > 0 else {
                stop()
                errorMessage = "No se ha podido acceder al micrófono. Inténtalo de nuevo o escribe tu mensaje."
                return
            }
            transcript = ""
            input.installTap(onBus: 0, bufferSize: 1024, format: format) { buffer, _ in request.append(buffer) }
            hasTap = true
            engine.prepare()
            try engine.start()
            isRecording = true
            isPreparing = false
            task = recognizer.recognitionTask(with: request) { [weak self] result, error in
                Task { @MainActor in
                    guard let self, self.generation == revision else { return }
                    if let result { self.transcript = result.bestTranscription.formattedString }
                    if result?.isFinal == true { self.stop() }
                    else if error != nil {
                        self.stop()
                        self.errorMessage = "El dictado se ha interrumpido. Puedes revisar el texto y continuar escribiendo."
                    }
                }
            }
        } catch {
            stop()
            errorMessage = "No se ha podido iniciar el dictado. Inténtalo de nuevo o escribe tu mensaje."
        }
        #else
        isPreparing = false
        errorMessage = "El dictado de esta interfaz está disponible en iPhone."
        #endif
    }

    func stop() {
        generation += 1
        #if os(iOS)
        engine.stop()
        if hasTap { engine.inputNode.removeTap(onBus: 0); hasTap = false }
        request?.endAudio()
        task?.cancel()
        task = nil
        request = nil
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
        #endif
        isPreparing = false
        isRecording = false
    }
}

struct NewReplyVoiceGate {
    private var seen = Set<String>()
    mutating func accept(id: String, enabled: Bool) -> Bool {
        guard seen.insert(id).inserted else { return false }
        return enabled
    }
}

/// Receives only the result of a new send, never the loaded conversation history.
@MainActor @Observable
final class VoicePlaybackController: NSObject, AVSpeechSynthesizerDelegate {
    var isSpeaking = false
    var status = "La lectura está desactivada hasta que tú la permitas."
    private let synthesizer = AVSpeechSynthesizer()
    private var current: AVSpeechUtterance?
    private var gate = NewReplyVoiceGate()

    override init() {
        super.init()
        synthesizer.delegate = self
    }

    func readNewReply(id: String, text: String, enabled: Bool) {
        guard gate.accept(id: id, enabled: enabled), !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
        stop()
        #if os(iOS)
        do {
            let session = AVAudioSession.sharedInstance()
            try session.setCategory(.playback, mode: .spokenAudio, options: .duckOthers)
            try session.setActive(true)
        } catch {
            status = "No se pudo activar la lectura. La respuesta sigue disponible como texto."
            return
        }
        #endif
        let utterance = AVSpeechUtterance(string: text)
        utterance.voice = AVSpeechSynthesisVoice(language: "es-ES")
        current = utterance
        isSpeaking = true
        status = "Leyendo la respuesta nueva. El micrófono está detenido."
        synthesizer.speak(utterance)
    }

    func stop() {
        let hadAudio = current != nil
        current = nil
        synthesizer.stopSpeaking(at: .immediate)
        isSpeaking = false
        status = "Voz detenida. Puedes seguir escribiendo."
        #if os(iOS)
        if hadAudio { try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation) }
        #endif
    }

    func configureReading(enabled: Bool) {
        if enabled && !isSpeaking { status = "Solo se leerán las respuestas nuevas que envíes desde esta pantalla." }
        if !enabled { stop(); status = "Lectura silenciada. Puedes seguir dictando o escribiendo." }
    }
    func reset() { stop(); gate = NewReplyVoiceGate() }

    nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didFinish utterance: AVSpeechUtterance) {
        Task { @MainActor [weak self] in
            guard let self, self.current === utterance else { return }
            self.stop()
            self.status = "Lectura terminada. Puedes dictar o escribir tu siguiente mensaje."
        }
    }
    nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didCancel utterance: AVSpeechUtterance) {
        Task { @MainActor [weak self] in
            guard let self, self.current === utterance else { return }
            self.stop()
        }
    }
}
