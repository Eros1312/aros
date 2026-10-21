import Foundation
import Security

struct APIError: LocalizedError {
    let message: String
    let status: Int
    var errorDescription: String? { message }
}
struct APIClient {
    let baseURL: URL
    var token: String
    static let serverKey = "aros.serverURL"
    static var savedURL: String { UserDefaults.standard.string(forKey: serverKey) ?? "" }
    static func make(url: String, token: String = "") throws -> APIClient {
        guard let parsed = URL(string: url.trimmingCharacters(in: .whitespacesAndNewlines)),
              let host = parsed.host, parsed.user == nil, parsed.password == nil, parsed.query == nil, parsed.fragment == nil,
              parsed.path.isEmpty || parsed.path == "/",
              parsed.scheme == "https" || (parsed.scheme == "http" && ["localhost", "127.0.0.1"].contains(host)) else {
            throw APIError(message: "Usa la dirección HTTPS de tu servidor. HTTP solo se admite en el simulador con localhost.", status: 0)
        }
        return APIClient(baseURL: parsed, token: token)
    }
    static func decode<T: Decodable>(_ type: T.Type, from data: Data) throws -> T {
        let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase
        decoder.dateDecodingStrategy = .custom { decoder in
            let value = try decoder.singleValueContainer().decode(String.self)
            let formatter = ISO8601DateFormatter(); formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
            if let date = formatter.date(from: value) { return date }
            formatter.formatOptions = [.withInternetDateTime]
            if let date = formatter.date(from: value) { return date }
            throw DecodingError.dataCorrupted(.init(codingPath: decoder.codingPath, debugDescription: "Fecha inválida"))
        }
        return try decoder.decode(type, from: data)
    }
    func request<T: Decodable>(_ path: String, method: String = "GET", body: [String: Any]? = nil, confirmation: String? = nil) async throws -> T {
        let url = URL(string: baseURL.absoluteString.trimmingCharacters(in: CharacterSet(charactersIn: "/")) + "/api/v1" + path)!
        var request = URLRequest(url: url); request.httpMethod = method; request.timeoutInterval = path == "/chat" ? 180 : 30
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if !token.isEmpty { request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization") }
        if let confirmation { request.setValue(confirmation, forHTTPHeaderField: "X-Confirmation") }
        if let body { request.httpBody = try JSONSerialization.data(withJSONObject: body, options: [.sortedKeys]) }
        do {
            let (data, response) = try await URLSession.shared.data(for: request)
            guard let response = response as? HTTPURLResponse else { throw APIError(message: "Respuesta no válida del servidor", status: 0) }
            guard (200..<300).contains(response.statusCode) else {
                let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
                throw APIError(message: object?["detail"] as? String ?? "No se pudo completar la petición. Revisa los datos.", status: response.statusCode)
            }
            return try Self.decode(T.self, from: data)
        } catch let error as APIError { throw error }
        catch is DecodingError { throw APIError(message: "La respuesta no es compatible. Comprueba la versión del servidor.", status: 0) }
        catch { throw APIError(message: "No se pudo contactar con Aros. Comprueba la conexión y la dirección del servidor.", status: 0) }
    }
    func confirmation(_ action: String, payload: [String: Any]) async throws -> String {
        let result: ConfirmationResult = try await request("/confirmations", method: "POST", body: ["action": action, "payload": payload])
        return result.token
    }
    func upload(_ url: URL, area: Area) async throws -> Resource {
        let permission = url.startAccessingSecurityScopedResource()
        defer { if permission { url.stopAccessingSecurityScopedResource() } }
        let attributes = try FileManager.default.attributesOfItem(atPath: url.path)
        guard ((attributes[.size] as? NSNumber)?.intValue ?? 0) <= 10_000_000 else { throw APIError(message: "El archivo supera 10 MB", status: 413) }
        let data = try Data(contentsOf: url); let boundary = UUID().uuidString
        var body = Data()
        func append(_ s: String) { body.append(Data(s.utf8)) }
        append("--\(boundary)\r\nContent-Disposition: form-data; name=\"area\"\r\n\r\n\(area.rawValue)\r\n")
        let filename = url.lastPathComponent.replacingOccurrences(of: "\"", with: "_").replacingOccurrences(of: "\r", with: "_").replacingOccurrences(of: "\n", with: "_")
        append("--\(boundary)\r\nContent-Disposition: form-data; name=\"file\"; filename=\"\(filename)\"\r\nContent-Type: application/octet-stream\r\n\r\n")
        body.append(data); append("\r\n--\(boundary)--\r\n")
        var request = URLRequest(url: baseURL.appendingPathComponent("api/v1/documents/upload"))
        request.httpMethod = "POST"; request.timeoutInterval = 45; request.httpBody = body
        request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        let (result, response) = try await URLSession.shared.data(for: request)
        guard let response = response as? HTTPURLResponse, (200..<300).contains(response.statusCode) else {
            let object = try? JSONSerialization.jsonObject(with: result) as? [String: Any]
            throw APIError(message: object?["detail"] as? String ?? "No se pudo importar el archivo", status: 0)
        }
        return try Self.decode(Resource.self, from: result)
    }
    func download(_ resource: Resource) async throws -> URL {
        var request = URLRequest(url: baseURL.appendingPathComponent("api/v1/documents/\(resource.id)/download"))
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let response = response as? HTTPURLResponse, response.statusCode == 200 else { throw APIError(message: "El archivo no está disponible", status: 0) }
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let name = URL(fileURLWithPath: resource.data["filename"]?.text ?? resource.title).lastPathComponent
        let file = directory.appendingPathComponent(name); try data.write(to: file)
        return file
    }
}

enum SessionKeychain {
    static func read(server: String) -> String? {
        let query: [String: Any] = [kSecClass as String:kSecClassGenericPassword,kSecAttrService as String:"Aros.session",kSecAttrAccount as String:server,kSecReturnData as String:true,kSecMatchLimit as String:kSecMatchLimitOne]
        var item: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &item) == errSecSuccess, let data = item as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }
    static func save(token: String, server: String) throws {
        remove(server: server)
        let query: [String: Any] = [kSecClass as String:kSecClassGenericPassword,kSecAttrService as String:"Aros.session",kSecAttrAccount as String:server,kSecValueData as String:Data(token.utf8),kSecAttrAccessible as String:kSecAttrAccessibleWhenUnlockedThisDeviceOnly]
        guard SecItemAdd(query as CFDictionary, nil) == errSecSuccess else { throw APIError(message: "No se pudo guardar la sesión de forma segura", status: 0) }
    }
    static func remove(server: String) {
        SecItemDelete([kSecClass as String:kSecClassGenericPassword,kSecAttrService as String:"Aros.session",kSecAttrAccount as String:server] as CFDictionary)
    }
}
