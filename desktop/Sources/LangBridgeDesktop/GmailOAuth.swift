import AppKit
import AuthenticationServices
import CryptoKit
import Foundation
import Network
import Security

struct GoogleDesktopOAuthClient: Equatable {
    let clientID: String
    let clientSecret: String
    let authURI: String
    let tokenURI: String

    init(data: Data) throws {
        guard let root = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let installed = root["installed"] as? [String: Any]
        else {
            throw GmailOAuthError.configuration(
                "Choose an OAuth JSON created as a Google Desktop app, not a Web app."
            )
        }
        let clientID = (installed["client_id"] as? String ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        let authURI = (installed["auth_uri"] as? String ?? "https://accounts.google.com/o/oauth2/v2/auth")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        let tokenURI = (installed["token_uri"] as? String ?? "https://oauth2.googleapis.com/token")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        guard !clientID.isEmpty,
              let authURL = URL(string: authURI),
              authURL.scheme == "https",
              authURL.host == "accounts.google.com",
              authURL.user == nil,
              authURL.password == nil,
              authURL.port == nil,
              let tokenURL = URL(string: tokenURI),
              tokenURL.scheme == "https",
              tokenURL.host == "oauth2.googleapis.com",
              tokenURL.path == "/token",
              tokenURL.user == nil,
              tokenURL.password == nil,
              tokenURL.port == nil
        else {
            throw GmailOAuthError.configuration(
                "The Desktop OAuth JSON must use Google's official authorization endpoints."
            )
        }
        self.clientID = clientID
        self.clientSecret = installed["client_secret"] as? String ?? ""
        self.authURI = authURI
        self.tokenURI = tokenURI
    }

    init(dictionary: [String: Any]) throws {
        let wrapper = ["installed": dictionary]
        let data = try JSONSerialization.data(withJSONObject: wrapper)
        try self.init(data: data)
    }
}

enum GmailOAuthError: LocalizedError {
    case configuration(String)
    case authentication(String)

    var errorDescription: String? {
        switch self {
        case .configuration(let message), .authentication(let message): return message
        }
    }
}

private final class LoopbackCallbackServer: @unchecked Sendable {
    private let listener: NWListener
    private let queue = DispatchQueue(label: "com.langbridge.app.gmail-oauth-loopback")
    private let lock = NSLock()
    private var readyContinuation: CheckedContinuation<UInt16, Error>?
    private var callbackContinuation: CheckedContinuation<URL, Error>?
    private var readyPort: UInt16?
    private var callbackResult: Result<URL, Error>?
    private var stopped = false

    init() throws {
        let parameters = NWParameters.tcp
        parameters.requiredLocalEndpoint = .hostPort(host: "127.0.0.1", port: .any)
        listener = try NWListener(using: parameters)
        listener.stateUpdateHandler = { [weak self] state in self?.handle(state) }
        listener.newConnectionHandler = { [weak self] connection in self?.receive(connection) }
        listener.start(queue: queue)
    }

    func port() async throws -> UInt16 {
        try await withCheckedThrowingContinuation { continuation in
            lock.lock()
            if let readyPort {
                lock.unlock()
                continuation.resume(returning: readyPort)
                return
            }
            readyContinuation = continuation
            lock.unlock()
        }
    }

    func callbackURL() async throws -> URL {
        try await withCheckedThrowingContinuation { continuation in
            lock.lock()
            if let callbackResult {
                lock.unlock()
                continuation.resume(with: callbackResult)
                return
            }
            callbackContinuation = continuation
            lock.unlock()
        }
    }

    func cancel(with error: Error) {
        finish(.failure(error))
    }

    func resolve(_ url: URL) {
        finish(.success(url))
    }

    private func handle(_ state: NWListener.State) {
        switch state {
        case .ready:
            guard let value = listener.port?.rawValue else {
                failReady(GmailOAuthError.authentication("Could not open the local OAuth callback."))
                return
            }
            lock.lock()
            readyPort = value
            let continuation = readyContinuation
            readyContinuation = nil
            lock.unlock()
            continuation?.resume(returning: value)
        case .failed(let error):
            failReady(error)
            finish(.failure(error))
        default:
            break
        }
    }

    private func failReady(_ error: Error) {
        lock.lock()
        let continuation = readyContinuation
        readyContinuation = nil
        lock.unlock()
        continuation?.resume(throwing: error)
    }

    private func receive(_ connection: NWConnection) {
        connection.start(queue: queue)
        receiveChunk(connection, accumulated: Data())
    }

    private func receiveChunk(_ connection: NWConnection, accumulated: Data) {
        connection.receive(minimumIncompleteLength: 1, maximumLength: 65_536) { [weak self] data, _, complete, error in
            guard let self else { return }
            var buffer = accumulated
            if let data { buffer.append(data) }
            if buffer.range(of: Data("\r\n\r\n".utf8)) != nil || complete || error != nil {
                self.handleRequest(buffer, connection: connection)
            } else {
                self.receiveChunk(connection, accumulated: buffer)
            }
        }
    }

    private func handleRequest(_ data: Data, connection: NWConnection) {
        let request = String(decoding: data, as: UTF8.self)
        let firstLine = request.split(separator: "\n", maxSplits: 1).first.map(String.init) ?? ""
        let target = firstLine.split(separator: " ").dropFirst().first.map(String.init) ?? ""
        let callback = URL(string: "http://127.0.0.1\(target)")
        let body = """
        <!doctype html><meta charset="utf-8"><title>LangBridge</title>
        <style>body{font:16px -apple-system;margin:48px;background:#151515;color:#f3f3f3}main{max-width:520px;margin:auto}</style>
        <main><h2>Gmail connected to LangBridge</h2><p>You can close this page and return to the app.</p></main>
        """
        let response = "HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: \(body.utf8.count)\r\nConnection: close\r\n\r\n\(body)"
        connection.send(content: Data(response.utf8), completion: .contentProcessed { _ in
            connection.cancel()
        })
        if let callback {
            finish(.success(callback))
        } else {
            finish(.failure(GmailOAuthError.authentication("Google returned an invalid OAuth callback.")))
        }
    }

    private func finish(_ result: Result<URL, Error>) {
        lock.lock()
        guard !stopped else {
            lock.unlock()
            return
        }
        stopped = true
        callbackResult = result
        let continuation = callbackContinuation
        callbackContinuation = nil
        lock.unlock()
        listener.cancel()
        continuation?.resume(with: result)
    }
}

@MainActor
final class GmailOAuthCoordinator: NSObject, ASWebAuthenticationPresentationContextProviding {
    static let scope = "https://www.googleapis.com/auth/gmail.readonly"
    private var session: ASWebAuthenticationSession?

    func presentationAnchor(for session: ASWebAuthenticationSession) -> ASPresentationAnchor {
        NSApplication.shared.keyWindow
            ?? NSApplication.shared.windows.first
            ?? NSWindow()
    }

    func authorize(client: GoogleDesktopOAuthClient) async throws -> [String: Any] {
        let server = try LoopbackCallbackServer()
        let port = try await server.port()
        let redirectURI = "http://127.0.0.1:\(port)"
        let state = Self.randomURLSafe(bytes: 24)
        let verifier = Self.randomURLSafe(bytes: 48)
        let challenge = Self.pkceChallenge(verifier)

        guard var components = URLComponents(string: client.authURI) else {
            throw GmailOAuthError.configuration("The Google authorization URL is invalid.")
        }
        components.queryItems = [
            URLQueryItem(name: "client_id", value: client.clientID),
            URLQueryItem(name: "redirect_uri", value: redirectURI),
            URLQueryItem(name: "response_type", value: "code"),
            URLQueryItem(name: "scope", value: Self.scope),
            URLQueryItem(name: "access_type", value: "offline"),
            URLQueryItem(name: "prompt", value: "consent"),
            URLQueryItem(name: "state", value: state),
            URLQueryItem(name: "code_challenge", value: challenge),
            URLQueryItem(name: "code_challenge_method", value: "S256"),
        ]
        guard let authorizationURL = components.url else {
            throw GmailOAuthError.configuration("Could not create the Google authorization URL.")
        }

        let authSession = ASWebAuthenticationSession(
            url: authorizationURL,
            callbackURLScheme: nil
        ) { callbackURL, error in
            if let callbackURL {
                server.resolve(callbackURL)
            } else if let error {
                server.cancel(with: error)
            }
        }
        authSession.presentationContextProvider = self
        authSession.prefersEphemeralWebBrowserSession = false
        session = authSession
        guard authSession.start() else {
            throw GmailOAuthError.authentication("macOS could not start the Google sign-in window.")
        }

        let callback = try await server.callbackURL()
        authSession.cancel()
        session = nil
        let query = URLComponents(url: callback, resolvingAgainstBaseURL: false)?.queryItems ?? []
        let grouped = Dictionary(grouping: query, by: \.name)
        guard grouped.values.allSatisfy({ $0.count == 1 }) else {
            throw GmailOAuthError.authentication("Google returned duplicate OAuth callback values.")
        }
        let values = grouped.mapValues { $0[0].value ?? "" }
        guard values["state"] == state else {
            throw GmailOAuthError.authentication("Google sign-in state did not match. Please try again.")
        }
        if let error = values["error"], !error.isEmpty {
            throw GmailOAuthError.authentication("Google sign-in failed: \(error)")
        }
        guard let code = values["code"], !code.isEmpty else {
            throw GmailOAuthError.authentication("Google did not return an authorization code.")
        }
        return try await exchange(
            code: code,
            verifier: verifier,
            redirectURI: redirectURI,
            client: client
        )
    }

    private func exchange(
        code: String,
        verifier: String,
        redirectURI: String,
        client: GoogleDesktopOAuthClient
    ) async throws -> [String: Any] {
        guard let url = URL(string: client.tokenURI) else {
            throw GmailOAuthError.configuration("The Google token URL is invalid.")
        }
        var fields = [
            "client_id": client.clientID,
            "code": code,
            "code_verifier": verifier,
            "grant_type": "authorization_code",
            "redirect_uri": redirectURI,
        ]
        if !client.clientSecret.isEmpty { fields["client_secret"] = client.clientSecret }
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")
        request.httpBody = Self.formBody(fields)
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode),
              var token = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              token["access_token"] as? String != nil
        else {
            let detail = String(data: data, encoding: .utf8) ?? "unknown response"
            throw GmailOAuthError.authentication("Google token exchange failed: \(detail)")
        }
        let expiresIn = (token["expires_in"] as? NSNumber)?.doubleValue ?? 3600
        if let scope = token["scope"] as? String {
            let granted = Set(scope.split(whereSeparator: \.isWhitespace).map(String.init))
            guard granted == Set([Self.scope]) else {
                throw GmailOAuthError.authentication(
                    "Google granted permissions other than gmail.readonly; connection was refused."
                )
            }
        }
        token["expires_at"] = Date().timeIntervalSince1970 + expiresIn
        token["scope"] = token["scope"] ?? Self.scope
        token["scope_requested"] = Self.scope
        return token
    }

    nonisolated static func formBody(_ fields: [String: String]) -> Data {
        let allowed = CharacterSet(charactersIn: "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._~")
        let body = fields.sorted(by: { $0.key < $1.key }).map { key, value in
            let escapedKey = key.addingPercentEncoding(withAllowedCharacters: allowed) ?? key
            let escapedValue = value.addingPercentEncoding(withAllowedCharacters: allowed) ?? value
            return "\(escapedKey)=\(escapedValue)"
        }.joined(separator: "&")
        return Data(body.utf8)
    }

    private static func randomURLSafe(bytes count: Int) -> String {
        var bytes = [UInt8](repeating: 0, count: count)
        _ = SecRandomCopyBytes(kSecRandomDefault, bytes.count, &bytes)
        return Data(bytes).base64EncodedString()
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
    }

    private static func pkceChallenge(_ verifier: String) -> String {
        // CryptoKit is part of macOS and keeps the PKCE implementation local.
        let digest = SHA256.hash(data: Data(verifier.utf8))
        return Data(digest).base64EncodedString()
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
    }
}
