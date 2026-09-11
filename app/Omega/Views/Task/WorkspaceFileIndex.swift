import Foundation

/// Walks a workspace tree for `@` file mentions. Bounded so typing stays snappy.
enum WorkspaceFileIndex {
    private static let skipDirectoryNames: Set<String> = [
        ".git", ".hg", ".svn",
        "node_modules", ".build", "DerivedData", ".swiftpm",
        "dist", "build", "Pods", "Carthage",
        ".venv", "venv", "__pycache__", ".mypy_cache", ".pytest_cache",
        ".next", ".nuxt", "target", ".omega", ".idea", ".vscode",
        "xcuserdata",
    ]

    static let maxFiles = 8_000
    static let maxDepth = 10

    static func list(root: String) -> [String] {
        let rootURL = URL(fileURLWithPath: root, isDirectory: true).standardizedFileURL
        var isDir: ObjCBool = false
        guard FileManager.default.fileExists(atPath: rootURL.path, isDirectory: &isDir), isDir.boolValue else {
            return []
        }

        let keys: [URLResourceKey] = [.isRegularFileKey, .isDirectoryKey]
        guard let enumerator = FileManager.default.enumerator(
            at: rootURL,
            includingPropertiesForKeys: keys,
            options: [.skipsHiddenFiles, .skipsPackageDescendants]
        ) else { return [] }

        var files: [String] = []
        files.reserveCapacity(512)
        let prefix = rootURL.path.hasSuffix("/") ? rootURL.path : rootURL.path + "/"

        while let url = enumerator.nextObject() as? URL {
            let depth = enumerator.level
            if depth > maxDepth {
                enumerator.skipDescendants()
                continue
            }
            let values = try? url.resourceValues(forKeys: [.isRegularFileKey, .isDirectoryKey])
            if values?.isDirectory == true {
                if skipDirectoryNames.contains(url.lastPathComponent) {
                    enumerator.skipDescendants()
                }
                continue
            }
            guard values?.isRegularFile == true else { continue }
            let path = url.standardizedFileURL.path
            let relative: String
            if path.hasPrefix(prefix) {
                relative = String(path.dropFirst(prefix.count))
            } else {
                relative = url.lastPathComponent
            }
            files.append(relative)
            if files.count >= maxFiles { break }
        }
        return files
    }
}
