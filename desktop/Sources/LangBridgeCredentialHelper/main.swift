import Darwin
import Foundation
import LangBridgeKeychain

private func fail(_ message: String, code: Int32 = 1) -> Never {
    FileHandle.standardError.write(Data((message + "\n").utf8))
    exit(code)
}

let arguments = CommandLine.arguments
guard arguments.count == 4 else {
    fail("Usage: LangBridgeCredentialHelper <get|set|delete> <service> <account>")
}

let operation = arguments[1]
let service = arguments[2]
let account = arguments[3]

do {
    switch operation {
    case "get":
        guard let data = try KeychainStore.data(service: service, account: account) else {
            exit(44)
        }
        FileHandle.standardOutput.write(data)
    case "set":
        let data = FileHandle.standardInput.readDataToEndOfFile()
        guard !data.isEmpty else { fail("Credential data is empty.") }
        try KeychainStore.set(data, service: service, account: account)
    case "delete":
        try KeychainStore.delete(service: service, account: account)
    default:
        fail("Unknown credential operation: \(operation)")
    }
} catch {
    fail(error.localizedDescription)
}
