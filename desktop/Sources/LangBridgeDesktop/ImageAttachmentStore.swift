import AppKit
import Foundation

enum ImageAttachmentStore {
    static let maximumCount = 4

    static func importFile(_ url: URL) throws -> ImageAttachment {
        guard let image = NSImage(contentsOf: url) else {
            throw AttachmentError("Could not read \(url.lastPathComponent) as an image.")
        }
        return try persist(image, suggestedName: url.deletingPathExtension().lastPathComponent)
    }

    static func importPasteboard(
        _ pasteboard: NSPasteboard = .general,
        limit: Int = maximumCount
    ) throws -> [ImageAttachment] {
        let urls = imageFileURLs(in: pasteboard)
        if !urls.isEmpty {
            return try urls.prefix(max(1, limit)).map(importFile)
        }
        guard let image = NSImage(pasteboard: pasteboard) else {
            throw AttachmentError("The clipboard does not contain an image.")
        }
        return [try persist(image, suggestedName: "Clipboard Image")]
    }

    static func pasteboardContainsImage(_ pasteboard: NSPasteboard = .general) -> Bool {
        if !imageFileURLs(in: pasteboard).isEmpty { return true }
        return NSImage(pasteboard: pasteboard) != nil
    }

    static func persist(_ image: NSImage, suggestedName: String) throws -> ImageAttachment {
        guard let tiff = image.tiffRepresentation,
              let bitmap = NSBitmapImageRep(data: tiff),
              let png = bitmap.representation(using: .png, properties: [:])
        else {
            throw AttachmentError("Could not convert the image to PNG.")
        }

        let directory = try attachmentDirectory()
        let fileName = "\(UUID().uuidString).png"
        let destination = directory.appendingPathComponent(fileName, isDirectory: false)
        try png.write(to: destination, options: .atomic)
        return ImageAttachment(path: destination.path, name: suggestedName)
    }

    static func remove(_ attachment: ImageAttachment) {
        guard let directory = try? attachmentDirectory() else { return }
        let file = URL(fileURLWithPath: attachment.path).standardizedFileURL
        guard file.deletingLastPathComponent() == directory.standardizedFileURL else { return }
        try? FileManager.default.removeItem(at: file)
    }

    private static func attachmentDirectory() throws -> URL {
        let base = try FileManager.default.url(
            for: .applicationSupportDirectory,
            in: .userDomainMask,
            appropriateFor: nil,
            create: true
        )
        let directory = base
            .appendingPathComponent("LangBridge", isDirectory: true)
            .appendingPathComponent("Attachments", isDirectory: true)
        try FileManager.default.createDirectory(
            at: directory,
            withIntermediateDirectories: true
        )
        return directory
    }

    private static func imageFileURLs(in pasteboard: NSPasteboard) -> [URL] {
        let options: [NSPasteboard.ReadingOptionKey: Any] = [
            .urlReadingFileURLsOnly: true,
        ]
        let objects = pasteboard.readObjects(forClasses: [NSURL.self], options: options) ?? []
        return objects.compactMap { object in
            guard let url = object as? NSURL,
                  let fileURL = url.filePathURL,
                  NSImage(contentsOf: fileURL) != nil
            else { return nil }
            return fileURL
        }
    }
}

struct AttachmentError: LocalizedError {
    let message: String

    init(_ message: String) {
        self.message = message
    }

    var errorDescription: String? { message }
}
