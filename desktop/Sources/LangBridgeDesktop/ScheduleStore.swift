import Foundation

@MainActor
final class ScheduleStore: ObservableObject {
    @Published private(set) var schedules: [ScheduleItem] = []
    @Published private(set) var runs: [String: [ScheduleRun]] = [:]
    @Published private(set) var isLoading = false
    @Published var errorMessage: String?

    private let runtime: RuntimeLocator

    init(runtime: RuntimeLocator) {
        self.runtime = runtime
    }

    func installAndRefresh() {
        run(arguments: ["install"], showLoading: false) { [weak self] _ in
            self?.refresh()
        }
    }

    func refresh() {
        run(arguments: ["list"]) { [weak self] result in
            guard let self, case let .success(object) = result,
                  let values = object["schedules"] as? [[String: Any]]
            else { return }
            schedules = values.compactMap(ScheduleItem.init(dictionary:))
        }
    }

    func loadRuns(for scheduleID: String) {
        run(arguments: ["runs", scheduleID], showLoading: false) { [weak self] result in
            guard let self, case let .success(object) = result,
                  let values = object["runs"] as? [[String: Any]]
            else { return }
            runs[scheduleID] = values.compactMap(ScheduleRun.init(dictionary:))
        }
    }

    func pause(_ item: ScheduleItem) {
        mutate(["pause", item.id])
    }

    func resume(_ item: ScheduleItem) {
        mutate(["resume", item.id])
    }

    func runNow(_ item: ScheduleItem) {
        mutate(["run-now", item.id])
    }

    func delete(_ item: ScheduleItem) {
        mutate(["delete", item.id])
    }

    func save(_ draft: ScheduleDraft, existing: ScheduleItem?) {
        do {
            let spec = try Self.jsonString(draft.scheduleSpec)
            let tools = try Self.jsonString(draft.tools)
            var arguments = [existing == nil ? "create" : "update"]
            if let existing { arguments.append(existing.id) }
            arguments += [
                "--name", draft.name,
                "--prompt", draft.prompt,
                "--workspace", draft.workspace,
                "--recurrence", draft.recurrence,
                "--schedule-spec", spec,
                "--tools", tools,
                "--output-subdirectory", draft.outputSubdirectory,
            ]
            mutate(arguments)
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func mutate(_ arguments: [String]) {
        run(arguments: arguments) { [weak self] result in
            if case .success = result {
                self?.refresh()
            }
        }
    }

    private func run(
        arguments: [String],
        showLoading: Bool = true,
        completion: @escaping (Result<[String: Any], Error>) -> Void
    ) {
        let configuration: BridgeLaunchConfiguration
        do {
            configuration = try runtime.schedulerConfiguration(arguments: arguments)
        } catch {
            errorMessage = error.localizedDescription
            completion(.failure(error))
            return
        }
        if showLoading { isLoading = true }
        DispatchQueue.global(qos: .userInitiated).async {
            let result = Self.execute(configuration)
            DispatchQueue.main.async { [weak self] in
                if showLoading { self?.isLoading = false }
                switch result {
                case .success:
                    self?.errorMessage = nil
                case let .failure(error):
                    self?.errorMessage = error.localizedDescription
                }
                completion(result)
            }
        }
    }

    nonisolated private static func execute(
        _ configuration: BridgeLaunchConfiguration
    ) -> Result<[String: Any], Error> {
        let process = Process()
        let output = Pipe()
        let errors = Pipe()
        process.executableURL = configuration.executable
        process.arguments = configuration.arguments
        process.currentDirectoryURL = configuration.workingDirectory
        process.environment = configuration.environment
        process.standardOutput = output
        process.standardError = errors
        do {
            try process.run()
            process.waitUntilExit()
            let stdout = output.fileHandleForReading.readDataToEndOfFile()
            let stderr = errors.fileHandleForReading.readDataToEndOfFile()
            guard process.terminationStatus == 0 else {
                let detail = String(data: stderr, encoding: .utf8) ?? "Scheduler command failed."
                throw ScheduleStoreError.command(detail.trimmingCharacters(in: .whitespacesAndNewlines))
            }
            guard let value = try JSONSerialization.jsonObject(with: stdout) as? [String: Any] else {
                throw ScheduleStoreError.invalidResponse
            }
            return .success(value)
        } catch {
            return .failure(error)
        }
    }

    private static func jsonString(_ value: Any) throws -> String {
        let data = try JSONSerialization.data(withJSONObject: value, options: [.sortedKeys])
        guard let string = String(data: data, encoding: .utf8) else {
            throw ScheduleStoreError.invalidResponse
        }
        return string
    }
}

private enum ScheduleStoreError: LocalizedError {
    case command(String)
    case invalidResponse

    var errorDescription: String? {
        switch self {
        case let .command(message): message.isEmpty ? "Scheduler command failed." : message
        case .invalidResponse: "The scheduler returned an invalid response."
        }
    }
}
