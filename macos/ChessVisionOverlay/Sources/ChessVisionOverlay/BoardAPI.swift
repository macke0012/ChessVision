import Foundation

/// Response of `POST /analyze` from scripts/board_api.py.
struct Analysis: Decodable {
    struct Pin: Decodable {
        let pinned: String
        let pinner: String
        let king: String
        let color: String
    }

    struct AttackedPiece: Decodable {
        let square: String
        let piece: String
        let attackers: [String]
        let defenders: Int
        let level: String   // "danger" or "defended"
        let reason: String
    }

    /// A forced mate for `side` (n = 1 or 2), or with kind "mate" an opponent's mate threat.
    struct Mate: Decodable {
        let side: String
        let n: Int
        let from: String
        let to: String
        let line: [String]
        let kind: String?
    }

    let found: Bool
    let image: [Int]
    let bbox: [Double]?     // x0, y0, x1, y1 in image pixels
    let fen: String?
    let uncertain: Int?
    let reused: Bool?
    let ms: Double?
    let pins: [Pin]
    let attacked: [AttackedPiece]
    let turn: String?       // "white", "black" or nil when unknown
    let turnSource: String?
    let highlights: [String]?
    let mates: [Mate]?
    let matesComplete: Bool?
    let threats: [Mate]?

    static func decode(_ data: Data) throws -> Analysis {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try decoder.decode(Analysis.self, from: data)
    }
}

/// Response of `GET /moves`: every legal move of one piece, rated for the hover tracker.
struct MoveSafety: Decodable {
    struct Option: Decodable {
        let to: String
        let san: String
        let kind: String   // "safe", "trade", "blunder", "wins" or "mate"
        let delta: Int     // material change in pawns (negative = you lose material)
    }

    let square: String
    let piece: String?
    let moves: [Option]
    let note: String?
}

/// Piece placement of a FEN as square name -> piece letter.
func pieceMap(_ fen: String) -> [String: Character] {
    var map: [String: Character] = [:]
    let rows = fen.split(separator: " ").first?.split(separator: "/") ?? []
    for (i, row) in rows.enumerated() {
        var file = 0
        for ch in row {
            if let n = ch.wholeNumberValue {
                file += n
            } else {
                if let letter = UnicodeScalar(97 + file) { map["\(Character(letter))\(8 - i)"] = ch }
                file += 1
            }
        }
    }
    return map
}

enum APIError: LocalizedError {
    case badStatus(Int, String)

    var errorDescription: String? {
        switch self {
        case let .badStatus(code, body): return "board API returned \(code): \(body)"
        }
    }
}

/// Client for the local board recognition API.
final class BoardAPI {
    let baseURL: URL
    private let session: URLSession

    init(port: Int) {
        baseURL = URL(string: "http://127.0.0.1:\(port)")!
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 10
        session = URLSession(configuration: config)
    }

    func isHealthy() async -> Bool {
        do {
            let (_, response) = try await session.data(from: baseURL.appendingPathComponent("health"))
            return (response as? HTTPURLResponse)?.statusCode == 200
        } catch {
            return false
        }
    }

    func moves(fen: String, square: String) async throws -> MoveSafety {
        var components = URLComponents(url: baseURL.appendingPathComponent("moves"), resolvingAgainstBaseURL: false)!
        components.queryItems = [URLQueryItem(name: "fen", value: fen), URLQueryItem(name: "square", value: square)]
        let (data, response) = try await session.data(from: components.url!)
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        guard status == 200 else {
            throw APIError.badStatus(status, String(data: data, encoding: .utf8) ?? "")
        }
        return try JSONDecoder().decode(MoveSafety.self, from: data)
    }

    /// turn: "auto", "white" or "black"
    func analyze(jpeg: Data, flip: Bool, region: Bool, turn: String = "auto") async throws -> Analysis {
        var components = URLComponents(url: baseURL.appendingPathComponent("analyze"), resolvingAgainstBaseURL: false)!
        components.queryItems = [
            URLQueryItem(name: "flip", value: flip ? "1" : "0"),
            URLQueryItem(name: "region", value: region ? "1" : "0"),
            URLQueryItem(name: "turn", value: turn),
        ]
        var request = URLRequest(url: components.url!)
        request.httpMethod = "POST"
        request.setValue("image/jpeg", forHTTPHeaderField: "Content-Type")
        let (data, response) = try await session.upload(for: request, from: jpeg)
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        guard status == 200 else {
            throw APIError.badStatus(status, String(data: data, encoding: .utf8) ?? "")
        }
        return try Analysis.decode(data)
    }
}

/// Where to find Python and the repo, written into the app bundle by macos/build_app.sh.
struct AppConfig: Decodable {
    let python: String
    let repo: String
    let port: Int

    static func load() -> AppConfig? {
        guard let url = Bundle.main.url(forResource: "config", withExtension: "json"),
              let data = try? Data(contentsOf: url) else { return nil }
        return try? JSONDecoder().decode(AppConfig.self, from: data)
    }
}

/// Runs scripts/board_api.py as a child process, logging to ~/Library/Logs/ChessVisionOverlay/board_api.log.
final class ServerProcess {
    private var process: Process?

    var logURL: URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Logs/ChessVisionOverlay/board_api.log")
    }

    func start(_ config: AppConfig) throws {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: config.python)
        process.arguments = ["scripts/board_api.py", "--port", String(config.port)]
        process.currentDirectoryURL = URL(fileURLWithPath: config.repo)
        try FileManager.default.createDirectory(at: logURL.deletingLastPathComponent(), withIntermediateDirectories: true)
        FileManager.default.createFile(atPath: logURL.path, contents: nil)
        let log = try FileHandle(forWritingTo: logURL)
        process.standardOutput = log
        process.standardError = log
        try process.run()
        self.process = process
    }

    var isRunning: Bool { process?.isRunning ?? false }

    func stop() {
        process?.terminate()
        process = nil
    }
}
