import AppKit

/// `ChessVisionOverlay --render input.png output.png [--flip] [--defended] [--turn auto|white|black] [--port N]`
/// Sends a saved screenshot to the board API and draws the overlay onto it, for checking the
/// drawing without screen capture.
enum RenderTest {
    static func run(_ args: [String]) -> Int32 {
        let turn = args.firstIndex(of: "--turn").flatMap { args[safe: $0 + 1] } ?? "auto"
        let hoverSquare = args.firstIndex(of: "--hover").flatMap { args[safe: $0 + 1] }
        let positional = args.filter { !$0.hasPrefix("--") && Int($0) == nil && !["auto", "white", "black"].contains($0) && $0 != hoverSquare }
        guard positional.count >= 2 else {
            print("usage: ChessVisionOverlay --render input.png output.png [--flip] [--defended] [--turn auto|white|black] [--port N]")
            return 2
        }
        let flip = args.contains("--flip")
        var options = OverlayOptions()
        options.showDefended = args.contains("--defended")
        let port = args.firstIndex(of: "--port").flatMap { Int(args[safe: $0 + 1] ?? "") } ?? 8765

        guard let image = loadImage(at: URL(fileURLWithPath: positional[0])), let jpeg = jpegData(from: image) else {
            print("cannot read \(positional[0])")
            return 1
        }
        var outcome: Result<Analysis, Error>?
        let done = DispatchSemaphore(value: 0)
        Task.detached {
            do { outcome = .success(try await BoardAPI(port: port).analyze(jpeg: jpeg, flip: flip, region: false, turn: turn)) }
            catch { outcome = .failure(error) }
            done.signal()
        }
        done.wait()
        let analysis: Analysis
        switch outcome {
        case let .success(a): analysis = a
        case let .failure(e): print("API error: \(e.localizedDescription)"); return 1
        case .none: return 1
        }
        guard analysis.found, let b = analysis.bbox, b.count == 4 else {
            print("no board found")
            return 1
        }

        let w = image.width, h = image.height
        guard let ctx = CGContext(data: nil, width: w, height: h, bitsPerComponent: 8, bytesPerRow: 0,
                                  space: CGColorSpace(name: CGColorSpace.sRGB)!,
                                  bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { return 1 }
        ctx.draw(image, in: CGRect(x: 0, y: 0, width: w, height: h))
        ctx.translateBy(x: 0, y: CGFloat(h))
        ctx.scaleBy(x: 1, y: -1)
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = NSGraphicsContext(cgContext: ctx, flipped: true)
        let board = CGRect(x: b[0], y: b[1], width: b[2] - b[0], height: b[3] - b[1])
        OverlayDrawing.draw(OverlayState(boardRect: board, flip: flip, analysis: analysis), options: options)
        if let square = args.firstIndex(of: "--hover").flatMap({ args[safe: $0 + 1] }), let fen = analysis.fen {
            var hoverResult: MoveSafety?
            let fetched = DispatchSemaphore(value: 0)
            Task.detached {
                hoverResult = try? await BoardAPI(port: port).moves(fen: fen, square: square)
                fetched.signal()
            }
            fetched.wait()
            if let safety = hoverResult {
                OverlayDrawing.drawHover(HoverState(fen: fen, boardRect: board, flip: flip, safety: safety))
                print("hover \(safety.piece ?? "-")@\(square): " + safety.moves.map { "\($0.san)=\($0.kind)\($0.delta == 0 ? "" : String(format: "%+d", $0.delta))" }.joined(separator: ", "))
            }
        }
        NSGraphicsContext.restoreGraphicsState()
        guard let out = ctx.makeImage(), writePNG(out, to: URL(fileURLWithPath: positional[1])) else { return 1 }

        print("fen \(analysis.fen ?? "-") | turn \(analysis.turn ?? "unknown") (\(analysis.turnSource ?? "-")) | highlights \(analysis.highlights ?? [])")
        for m in analysis.mates ?? [] { print("mate in \(m.n) for \(m.side): \(m.line.joined(separator: " "))") }
        for t in analysis.threats ?? [] { print("threat: \(t.side) mates with \(t.line.joined(separator: " "))") }
        for p in analysis.pins { print("pin \(p.pinned) by \(p.pinner) to king \(p.king)") }
        for a in analysis.attacked where a.level == "danger" || options.showDefended {
            print("\(a.level) \(a.piece)@\(a.square) attacked by \(a.attackers.joined(separator: ",")) (\(a.reason))")
        }
        return 0
    }
}

extension Array {
    subscript(safe index: Int) -> Element? { indices.contains(index) ? self[index] : nil }
}
