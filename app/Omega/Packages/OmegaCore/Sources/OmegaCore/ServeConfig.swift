import Foundation

/// `~/.omega/serve.json`, written by `omega serve` on launch: confirmed shape `{"port","token","pid"}`.
/// `host` isn't part of that file — it always defaults to `127.0.0.1` (the daemon binds loopback-only).
public struct ServeConfig: Codable, Sendable, Equatable {
    public let host: String
    public let port: Int
    public let token: String
    public let pid: Int?

    private enum CodingKeys: String, CodingKey {
        case port, token, pid
    }

    public init(host: String = "127.0.0.1", port: Int, token: String, pid: Int? = nil) {
        self.host = host
        self.port = port
        self.token = token
        self.pid = pid
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        host = "127.0.0.1"
        port = try c.decode(Int.self, forKey: .port)
        token = try c.decode(String.self, forKey: .token)
        pid = try c.decodeIfPresent(Int.self, forKey: .pid)
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(port, forKey: .port)
        try c.encode(token, forKey: .token)
        try c.encodeIfPresent(pid, forKey: .pid)
    }

    public var baseURL: URL {
        URL(string: "http://\(host):\(port)")!
    }

    public static var defaultPath: URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".omega")
            .appendingPathComponent("serve.json")
    }

    public static func load(from url: URL = defaultPath) throws -> ServeConfig {
        let data = try Data(contentsOf: url)
        return try JSONDecoder().decode(ServeConfig.self, from: data)
    }
}
