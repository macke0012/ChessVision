import AppKit

struct OverlayOptions {
    var showPins = true
    var showDanger = true        // attacked and undefended, attacked by a cheaper piece, or check
    var showDefended = false     // attacked but defended
    var showAttackerArrows = true
    var showMates = true         // forced mates in 1 or 2 for the side to move
    var showThreats = true       // mates the opponent threatens if it were their move
    var showTurn = true          // "White to move" label above the board
    var showMoveSafety = true    // rate the squares a hovered piece can move to
}

/// The squares a hovered (or dragged) piece can move to, rated.
struct HoverState {
    var fen: String
    var boardRect: CGRect
    var flip: Bool
    var safety: MoveSafety
}

struct OverlayState {
    var boardRect: CGRect        // where the board is, in the drawing's top-left-origin coordinates
    var flip: Bool               // board shown from Black's side
    var analysis: Analysis
}

/// Draws the annotations over a board. Expects a flipped (top-left origin) graphics context,
/// which both the overlay view and the --render test provide.
enum OverlayDrawing {
    static let threatened = NSColor(calibratedRed: 0.93, green: 0.16, blue: 0.16, alpha: 0.95)  // in danger / your piece
    static let canWin = NSColor(calibratedRed: 0.10, green: 0.55, blue: 0.95, alpha: 0.95)      // opponent piece you can win
    static let defended = NSColor(calibratedRed: 1.0, green: 0.62, blue: 0.0, alpha: 0.95)
    static let pin = NSColor(calibratedRed: 0.58, green: 0.25, blue: 0.95, alpha: 0.95)
    static let mate = NSColor(calibratedRed: 0.10, green: 0.78, blue: 0.30, alpha: 0.95)
    static let threat = NSColor(calibratedRed: 0.80, green: 0.05, blue: 0.20, alpha: 0.95)

    static func squareRect(_ name: String, in board: CGRect, flip: Bool) -> CGRect? {
        let chars = Array(name.unicodeScalars)
        guard chars.count == 2 else { return nil }
        let file = Int(chars[0].value) - 97   // a...h
        let rank = Int(chars[1].value) - 49   // 1...8
        guard (0..<8).contains(file), (0..<8).contains(rank) else { return nil }
        let col = flip ? 7 - file : file
        let row = flip ? rank : 7 - rank
        let w = board.width / 8, h = board.height / 8
        return CGRect(x: board.minX + CGFloat(col) * w, y: board.minY + CGFloat(row) * h, width: w, height: h)
    }

    static let safe = NSColor(calibratedRed: 0.15, green: 0.75, blue: 0.30, alpha: 1)
    static let trade = NSColor(calibratedRed: 0.97, green: 0.78, blue: 0.10, alpha: 1)
    static let blunder = NSColor(calibratedRed: 0.93, green: 0.16, blue: 0.16, alpha: 1)

    /// Square under a point, or nil outside the board.
    static func square(at point: CGPoint, in board: CGRect, flip: Bool) -> String? {
        guard board.contains(point) else { return nil }
        let col = min(7, max(0, Int((point.x - board.minX) / (board.width / 8))))
        let row = min(7, max(0, Int((point.y - board.minY) / (board.height / 8))))
        let file = flip ? 7 - col : col
        let rank = flip ? row : 7 - row
        guard let letter = UnicodeScalar(97 + file) else { return nil }
        return "\(Character(letter))\(rank + 1)"
    }

    /// Hover tracker: tint every square the piece can move to (green safe, yellow equal trade,
    /// red loses material), with a dot (or a ring on captures) and the material change.
    static func drawHover(_ hover: HoverState) {
        let board = hover.boardRect
        let cell = board.width / 8
        let line = max(2, cell * 0.05)
        func rect(_ square: String) -> CGRect? { squareRect(square, in: board, flip: hover.flip) }

        if let from = rect(hover.safety.square) {
            let ring = NSBezierPath(roundedRect: from.insetBy(dx: line, dy: line), xRadius: cell * 0.1, yRadius: cell * 0.1)
            ring.lineWidth = line
            NSColor.white.withAlphaComponent(0.95).setStroke()
            ring.stroke()
        }
        var labels: [() -> Void] = []
        for option in hover.safety.moves {
            guard let r = rect(option.to) else { continue }
            let color: NSColor
            switch option.kind {
            case "blunder": color = blunder
            case "trade": color = trade
            default: color = safe   // safe, wins, mate
            }
            color.withAlphaComponent(0.30).setFill()
            NSBezierPath(rect: r).fill()
            if option.san.contains("x") {   // capture: ring around the piece being taken
                let ring = NSBezierPath(ovalIn: r.insetBy(dx: cell * 0.07, dy: cell * 0.07))
                ring.lineWidth = cell * 0.08
                color.withAlphaComponent(0.85).setStroke()
                ring.stroke()
            } else {
                let d = cell * 0.30
                color.withAlphaComponent(0.9).setFill()
                NSBezierPath(ovalIn: CGRect(x: r.midX - d / 2, y: r.midY - d / 2, width: d, height: d)).fill()
            }
            let text: String? = option.kind == "mate" ? "#"
                : option.delta != 0 ? String(format: "%+d", option.delta)
                : option.kind == "trade" ? "=" : nil
            if let text {
                let size = max(9, cell * 0.18)
                labels.append { label(text, at: CGPoint(x: r.maxX - size * 2.0 - line, y: r.maxY - size * 1.45 - line), size: size, color: color) }
            }
        }
        labels.forEach { $0() }
    }

    static func draw(_ state: OverlayState, options: OverlayOptions) {
        let board = state.boardRect
        let a = state.analysis
        let cell = board.width / 8
        let line = max(2, cell * 0.06)
        let labelSize = max(9, cell * 0.17)
        func rect(_ square: String) -> CGRect? { squareRect(square, in: board, flip: state.flip) }
        var labels: [() -> Void] = []  // drawn last, so arrows never cover them

        // attacked pieces. With the side to move known: its pieces in danger are threatened (red),
        // the opponent's pieces in danger can be won (blue). Unknown: all red.
        for piece in a.attacked {
            let isDanger = piece.level == "danger"
            guard isDanger ? options.showDanger : options.showDefended, let target = rect(piece.square) else { continue }
            let pieceIsWhite = piece.piece.uppercased() == piece.piece
            var color = defended
            if isDanger {
                if let turn = a.turn { color = (turn == "white") == pieceIsWhite ? threatened : canWin } else { color = threatened }
            }
            if isDanger && options.showAttackerArrows {
                for attacker in piece.attackers {
                    guard let from = rect(attacker) else { continue }
                    arrow(from: center(from), to: center(target), stopShort: cell * 0.42, width: line * 0.8,
                          color: color.withAlphaComponent(0.6))
                }
            }
            let ring = NSBezierPath(roundedRect: target.insetBy(dx: line, dy: line), xRadius: cell * 0.12, yRadius: cell * 0.12)
            ring.lineWidth = line
            color.setStroke()
            ring.stroke()
        }

        // pins: dashed line pinner -> king through the pinned piece, dashed circle and a label
        if options.showPins {
            for p in a.pins {
                guard let pinned = rect(p.pinned), let pinner = rect(p.pinner), let king = rect(p.king) else { continue }
                let path = NSBezierPath()
                path.move(to: center(pinner))
                path.line(to: center(king))
                path.lineWidth = line
                path.lineCapStyle = .round
                path.setLineDash([cell * 0.16, cell * 0.10], count: 2, phase: 0)
                pin.withAlphaComponent(0.75).setStroke()
                path.stroke()

                let circle = NSBezierPath(ovalIn: pinned.insetBy(dx: cell * 0.06, dy: cell * 0.06))
                circle.lineWidth = line
                circle.setLineDash([cell * 0.10, cell * 0.06], count: 2, phase: 0)
                pin.setStroke()
                circle.stroke()
                labels.append { label("PIN", at: CGPoint(x: pinned.minX + line, y: pinned.minY + line), size: labelSize, color: pin) }
            }
        }

        // opponent's mate threats: dashed arrow + label
        if options.showThreats {
            for t in a.threats ?? [] {
                guard let from = rect(t.from), let to = rect(t.to) else { continue }
                arrow(from: center(from), to: center(to), stopShort: cell * 0.30, width: line * 1.1,
                      color: threat.withAlphaComponent(0.85), dashed: true)
                labels.append { label("THREAT #", at: CGPoint(x: to.minX + line, y: to.maxY - labelSize * 1.5 - line), size: labelSize, color: threat) }
            }
        }

        // forced mates for the side to move (both sides when it's unknown): solid green arrow + M1/M2
        if options.showMates {
            for m in a.mates ?? [] {
                guard let from = rect(m.from), let to = rect(m.to) else { continue }
                arrow(from: center(from), to: center(to), stopShort: cell * 0.30, width: line * 1.4, color: mate)
                let side = a.turn == nil ? (m.side == "white" ? "W " : "B ") : ""
                labels.append { label("\(side)M\(m.n)", at: CGPoint(x: to.maxX - labelSize * 2.6 - line, y: to.minY + line), size: labelSize, color: mate) }
            }
        }

        // side to move, just above the board (inside its top edge if there's no room)
        if options.showTurn {
            let text: String
            let fill: NSColor
            let ink: NSColor
            switch a.turn {
            case "white": (text, fill, ink) = ("White to move", .white, .black)
            case "black": (text, fill, ink) = ("Black to move", .black, .white)
            default: (text, fill, ink) = ("Side to move unknown", NSColor(white: 0.35, alpha: 0.9), .white)
            }
            let size = max(10, cell * 0.2)
            let y = board.minY >= size * 1.8 ? board.minY - size * 1.7 : board.minY + line
            labels.append { label(text, at: CGPoint(x: board.minX, y: y), size: size, color: fill, textColor: ink) }
        }
        labels.forEach { $0() }
    }

    private static func center(_ r: CGRect) -> CGPoint { CGPoint(x: r.midX, y: r.midY) }

    private static func arrow(from a: CGPoint, to b: CGPoint, stopShort: CGFloat, width: CGFloat, color: NSColor,
                              dashed: Bool = false) {
        let dx = b.x - a.x, dy = b.y - a.y
        let length = (dx * dx + dy * dy).squareRoot()
        guard length > stopShort * 1.5 else { return }
        let ux = dx / length, uy = dy / length
        let start = CGPoint(x: a.x + ux * stopShort * 0.5, y: a.y + uy * stopShort * 0.5)
        let tip = CGPoint(x: b.x - ux * stopShort, y: b.y - uy * stopShort)
        let head = width * 3.2
        let base = CGPoint(x: tip.x - ux * head, y: tip.y - uy * head)

        let shaft = NSBezierPath()
        shaft.move(to: start)
        shaft.line(to: base)
        shaft.lineWidth = width
        shaft.lineCapStyle = .round
        if dashed { shaft.setLineDash([width * 2.5, width * 1.6], count: 2, phase: 0) }
        color.setStroke()
        shaft.stroke()

        let tri = NSBezierPath()
        tri.move(to: tip)
        tri.line(to: CGPoint(x: base.x - uy * head * 0.6, y: base.y + ux * head * 0.6))
        tri.line(to: CGPoint(x: base.x + uy * head * 0.6, y: base.y - ux * head * 0.6))
        tri.close()
        color.setFill()
        tri.fill()
    }

    private static func label(_ text: String, at origin: CGPoint, size: CGFloat, color: NSColor,
                              textColor: NSColor = .white) {
        let attrs: [NSAttributedString.Key: Any] = [
            .font: NSFont.boldSystemFont(ofSize: size),
            .foregroundColor: textColor,
        ]
        let string = NSAttributedString(string: text, attributes: attrs)
        let textSize = string.size()
        let pill = CGRect(x: origin.x, y: origin.y, width: textSize.width + size * 0.6, height: textSize.height + size * 0.1)
        color.setFill()
        NSBezierPath(roundedRect: pill, xRadius: pill.height / 2, yRadius: pill.height / 2).fill()
        string.draw(at: CGPoint(x: pill.minX + size * 0.3, y: pill.minY + size * 0.05))
    }
}

/// Transparent, click-through view covering the screen; top-left origin like the capture coordinates.
final class OverlayView: NSView {
    var state: OverlayState? { didSet { needsDisplay = true } }
    var hover: HoverState? { didSet { needsDisplay = true } }
    var options = OverlayOptions() { didSet { needsDisplay = true } }

    override var isFlipped: Bool { true }

    override func draw(_ dirtyRect: NSRect) {
        NSColor.clear.setFill()
        dirtyRect.fill(using: .copy)
        if let state { OverlayDrawing.draw(state, options: options) }
        if let hover, options.showMoveSafety { OverlayDrawing.drawHover(hover) }
    }
}

func makeOverlayWindow(for screen: NSScreen) -> (NSWindow, OverlayView) {
    let window = NSWindow(contentRect: screen.frame, styleMask: .borderless, backing: .buffered, defer: false)
    window.isOpaque = false
    window.backgroundColor = .clear
    window.hasShadow = false
    window.ignoresMouseEvents = true
    window.level = .statusBar
    window.collectionBehavior = [.canJoinAllSpaces, .stationary, .fullScreenAuxiliary, .ignoresCycle]
    window.isReleasedWhenClosed = false
    let view = OverlayView(frame: CGRect(origin: .zero, size: screen.frame.size))
    window.contentView = view
    window.setFrame(screen.frame, display: true)
    window.orderFrontRegardless()
    return (window, view)
}
