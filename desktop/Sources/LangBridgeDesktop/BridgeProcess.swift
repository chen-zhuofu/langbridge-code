import Foundation

struct UTF8LineBuffer {
    private var data = Data()

    mutating func append(_ chunk: Data) -> [String] {
        data.append(chunk)
        var lines: [String] = []
        while let newline = data.firstIndex(of: 0x0A) {
            let lineData = Data(data[..<newline])
            data.removeSubrange(...newline)
            guard let line = String(data: lineData, encoding: .utf8) else { continue }
            let trimmed = line.trimmingCharacters(in: .whitespacesAndNewlines)
            if !trimmed.isEmpty { lines.append(trimmed) }
        }
        return lines
    }
}

final class BridgeProcess {
    private static let gracefulShutdownTimeout: TimeInterval = 55
    var onEvent: (([String: Any]) -> Void)?
    var onError: ((String) -> Void)?
    var onExit: ((Int32) -> Void)?

    private let configuration: BridgeLaunchConfiguration
    private let process = Process()
    private let inputPipe = Pipe()
    private let outputPipe = Pipe()
    private let errorPipe = Pipe()
    private let parsingQueue = DispatchQueue(label: "com.langbridge.desktop.bridge-parser")
    private var outputBuffer = UTF8LineBuffer()
    private var errorBuffer = UTF8LineBuffer()
    private var didStart = false

    init(configuration: BridgeLaunchConfiguration) {
        self.configuration = configuration
    }

    func start() throws {
        guard !didStart else { return }
        didStart = true
        process.executableURL = configuration.executable
        process.arguments = configuration.arguments
        process.currentDirectoryURL = configuration.workingDirectory
        process.environment = configuration.environment
        process.standardInput = inputPipe
        process.standardOutput = outputPipe
        process.standardError = errorPipe

        outputPipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty else { return }
            self?.parsingQueue.async {
                self?.consumeOutput(data)
            }
        }
        errorPipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty else { return }
            self?.parsingQueue.async {
                self?.consumeError(data)
            }
        }
        process.terminationHandler = { [weak self] process in
            DispatchQueue.main.async {
                self?.onExit?(process.terminationStatus)
            }
        }
        try process.run()
    }

    func send(_ message: [String: Any]) {
        guard process.isRunning,
              JSONSerialization.isValidJSONObject(message),
              let data = try? JSONSerialization.data(withJSONObject: message),
              var line = String(data: data, encoding: .utf8)
        else { return }
        line.append("\n")
        guard let lineData = line.data(using: .utf8) else { return }
        do {
            try inputPipe.fileHandleForWriting.write(contentsOf: lineData)
        } catch {
            DispatchQueue.main.async { [weak self] in
                self?.onError?("Could not write to the LangBridge engine: \(error.localizedDescription)")
            }
        }
    }

    func shutdown() {
        guard process.isRunning else { return }
        send(["type": "quit"])
        let runningProcess = process
        DispatchQueue.global().asyncAfter(deadline: .now() + Self.gracefulShutdownTimeout) {
            if runningProcess.isRunning {
                runningProcess.terminate()
            }
        }
    }

    private func consumeOutput(_ data: Data) {
        for line in outputBuffer.append(data) {
            guard let event = BridgeEventDecoder.decode(line: line) else { continue }
            DispatchQueue.main.async { [weak self] in
                self?.onEvent?(event)
            }
        }
    }

    private func consumeError(_ data: Data) {
        for line in errorBuffer.append(data) {
            DispatchQueue.main.async { [weak self] in
                self?.onError?(line)
            }
        }
    }

    deinit {
        outputPipe.fileHandleForReading.readabilityHandler = nil
        errorPipe.fileHandleForReading.readabilityHandler = nil
        shutdown()
    }
}
