import AppKit

/// Menu-bar app: captures the screen 4x a second, sends it to the local board API and draws
/// pins / attacked pieces over the board it finds.
@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate {
    static let interval: TimeInterval = 0.25
    static let regionMargin: CGFloat = 0.05

    private var statusItem: NSStatusItem!
    private var overlayWindow: NSWindow?
    private var overlayView: OverlayView?
    private let capturer = ScreenCapturer()
    private let server = ServerProcess()
    private var config: AppConfig?
    private var api: BoardAPI!

    private var enabled = true
    private var flip = false
    private var turnMode = "auto"          // "auto", "white" or "black"
    private var options = OverlayOptions()
    private var lastAnalysis: Analysis?
    private var infoItems: [NSMenuItem] = []
    private var region: CGRect?            // last board area (display points), nil = search whole screen
    private var candidateFEN: String?
    private var shownFEN: String?
    private var status = "Starting…"
    private var permissionMissing = false
    private var retryAfter = Date.distantPast

    // hover tracker
    private var hoverTimer: Timer?
    private var hoverKey: String?          // "<fen> <square>" currently shown or being fetched
    private var dragSquare: String?        // piece square where the mouse button went down
    private var hoverTask: Task<Void, Never>?
    private var loop: Task<Void, Never>?

    private let statusMenuItem = NSMenuItem(title: "", action: nil, keyEquivalent: "")
    private let fenMenuItem = NSMenuItem(title: "", action: #selector(copyFEN), keyEquivalent: "c")

    func applicationDidFinishLaunching(_ notification: Notification) {
        config = AppConfig.load()
        api = BoardAPI(port: config?.port ?? 8765)
        setUpOverlay()
        setUpMenu()
        if !CGPreflightScreenCaptureAccess() {
            // ask once per launch; after that the loop only checks silently (see tick())
            CGRequestScreenCaptureAccess()
        }
        loop = Task { await run() }
        hoverTimer = Timer.scheduledTimer(withTimeInterval: 0.05, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.updateHover() }
        }
    }

    // MARK: - Hover tracker

    /// Follows the mouse (no permission needed for its position). Over a piece, or while dragging
    /// one, shows how safe each square it can move to is.
    private func updateHover() {
        guard enabled, options.showMoveSafety, let view = overlayView, let window = overlayWindow else {
            clearHover()
            return
        }
        let buttonDown = NSEvent.pressedMouseButtons & 1 != 0
        if !buttonDown { dragSquare = nil }
        // while a piece is being dragged its square looks empty on screen: keep showing its moves
        if buttonDown, dragSquare != nil, view.hover != nil { return }

        guard let state = view.state, let fen = state.analysis.fen else { clearHover(); return }
        let point = view.convert(window.convertPoint(fromScreen: NSEvent.mouseLocation), from: nil)
        let pieces = pieceMap(fen)
        var square = OverlayDrawing.square(at: point, in: state.boardRect, flip: state.flip)
        if let s = square, pieces[s] == nil { square = nil }
        if buttonDown, dragSquare == nil { dragSquare = square }
        guard let square else { clearHover(); return }

        let key = "\(fen) \(square)"
        if key == hoverKey { return }
        hoverKey = key
        hoverTask?.cancel()
        let board = state.boardRect, flip = state.flip
        hoverTask = Task { [weak self] in
            guard let self, let safety = try? await self.api.moves(fen: fen, square: square),
                  !Task.isCancelled, self.hoverKey == key else { return }
            self.overlayView?.hover = HoverState(fen: fen, boardRect: board, flip: flip, safety: safety)
        }
    }

    private func clearHover() {
        hoverKey = nil
        hoverTask?.cancel()
        if overlayView?.hover != nil { overlayView?.hover = nil }
    }

    func applicationWillTerminate(_ notification: Notification) {
        loop?.cancel()
        server.stop()
    }

    // MARK: - Setup

    private func setUpOverlay() {
        let mainID = CGMainDisplayID()
        let screen = NSScreen.screens.first {
            ($0.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? NSNumber)?.uint32Value == mainID
        } ?? NSScreen.main
        guard let screen else { return }
        let (window, view) = makeOverlayWindow(for: screen)
        view.options = options
        overlayWindow = window
        overlayView = view
    }

    private func setUpMenu() {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        statusItem.button?.title = "♞"
        let menu = NSMenu()
        menu.delegate = self
        statusMenuItem.isEnabled = false
        menu.addItem(statusMenuItem)
        fenMenuItem.target = self
        menu.addItem(fenMenuItem)
        menu.addItem(.separator())
        menu.addItem(toggle("Overlay enabled", #selector(toggleEnabled), "e"))
        menu.addItem(toggle("Black at bottom", #selector(toggleFlip), "f"))
        let turnItem = NSMenuItem(title: "Side to move", action: nil, keyEquivalent: "")
        let turnMenu = NSMenu()
        for (title, mode) in [("Auto (from check or last-move highlight)", "auto"), ("White", "white"), ("Black", "black")] {
            let item = toggle(title, #selector(setTurnMode(_:)), "")
            item.representedObject = mode
            turnMenu.addItem(item)
        }
        turnItem.submenu = turnMenu
        menu.addItem(turnItem)
        menu.addItem(.separator())
        menu.addItem(toggle("Show move safety when hovering a piece", #selector(toggleMoveSafety), ""))
        menu.addItem(toggle("Show mates in 1 and 2", #selector(toggleMates), ""))
        menu.addItem(toggle("Show mate threats", #selector(toggleThreats), ""))
        menu.addItem(toggle("Show pins", #selector(togglePins), ""))
        menu.addItem(toggle("Show pieces in danger", #selector(toggleDanger), ""))
        menu.addItem(toggle("Show attacked but defended pieces", #selector(toggleDefended), ""))
        menu.addItem(toggle("Show attacker arrows", #selector(toggleArrows), ""))
        menu.addItem(.separator())
        menu.addItem(legend("● Hover a piece: green safe, yellow equal trade (=), red loses material (−N)", OverlayDrawing.safe))
        menu.addItem(legend("➜ Green arrow: forced mate (M1 / M2) for the side to move", OverlayDrawing.mate))
        menu.addItem(legend("➜ Dashed red arrow: mate the opponent threatens", OverlayDrawing.threat))
        menu.addItem(legend("■ Red: your piece in danger (check, undefended, or attacked by a cheaper piece)", OverlayDrawing.threatened))
        menu.addItem(legend("■ Blue: opponent piece you can win", OverlayDrawing.canWin))
        menu.addItem(legend("■ Orange: attacked but defended", OverlayDrawing.defended))
        menu.addItem(legend("■ Purple: pinned to its king", OverlayDrawing.pin))
        menu.addItem(.separator())
        let settings = NSMenuItem(title: "Open Screen Recording Settings…", action: #selector(openSettings), keyEquivalent: "")
        settings.target = self
        menu.addItem(settings)
        menu.addItem(NSMenuItem(title: "Quit", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q"))
        statusItem.menu = menu
    }

    private func toggle(_ title: String, _ action: Selector, _ key: String) -> NSMenuItem {
        let item = NSMenuItem(title: title, action: action, keyEquivalent: key)
        item.target = self
        return item
    }

    private func legend(_ title: String, _ color: NSColor) -> NSMenuItem {
        let item = NSMenuItem(title: "", action: nil, keyEquivalent: "")
        let text = NSMutableAttributedString(string: title, attributes: [.font: NSFont.menuFont(ofSize: 12)])
        text.addAttribute(.foregroundColor, value: color, range: NSRange(location: 0, length: 1))
        item.attributedTitle = text
        item.isEnabled = false
        return item
    }

    func menuWillOpen(_ menu: NSMenu) {
        guard menu === statusItem.menu else { return }
        statusMenuItem.title = status
        fenMenuItem.title = shownFEN.map { "Copy FEN: \($0)" } ?? "Copy FEN (no position yet)"
        fenMenuItem.isEnabled = shownFEN != nil

        // what the current position holds: side to move, mates, threats
        infoItems.forEach { menu.removeItem($0) }
        infoItems = infoLines().map { line in
            let item = NSMenuItem(title: line, action: nil, keyEquivalent: "")
            item.isEnabled = false
            return item
        }
        let at = menu.index(of: fenMenuItem) + 1
        for (offset, item) in infoItems.enumerated() { menu.insertItem(item, at: at + offset) }

        for item in menu.items + (menu.items.compactMap(\.submenu).flatMap(\.items)) {
            switch item.action {
            case #selector(toggleEnabled): item.state = enabled ? .on : .off
            case #selector(toggleFlip): item.state = flip ? .on : .off
            case #selector(setTurnMode(_:)): item.state = (item.representedObject as? String) == turnMode ? .on : .off
            case #selector(toggleMoveSafety): item.state = options.showMoveSafety ? .on : .off
            case #selector(toggleMates): item.state = options.showMates ? .on : .off
            case #selector(toggleThreats): item.state = options.showThreats ? .on : .off
            case #selector(togglePins): item.state = options.showPins ? .on : .off
            case #selector(toggleDanger): item.state = options.showDanger ? .on : .off
            case #selector(toggleDefended): item.state = options.showDefended ? .on : .off
            case #selector(toggleArrows): item.state = options.showAttackerArrows ? .on : .off
            default: break
            }
        }
    }

    private func infoLines() -> [String] {
        guard let a = lastAnalysis, a.found, shownFEN != nil else { return [] }
        var lines: [String] = []
        if let turn = a.turn {
            lines.append("\(turn.capitalized) to move (\(a.turnSource ?? ""))")
        } else {
            lines.append("Side to move unknown until a move is played (or set it under Side to move)")
        }
        for m in a.mates ?? [] {
            lines.append("Mate in \(m.n) for \(m.side.capitalized): \(m.line.joined(separator: " "))")
        }
        if (a.mates ?? []).isEmpty {
            lines.append(a.matesComplete == false ? "No mate in 1 or 2 found (search cut short)" : "No mate in 1 or 2")
        }
        for t in a.threats ?? [] {
            lines.append("Threat: \(t.side.capitalized) mates with \(t.line.joined(separator: " "))")
        }
        return lines
    }

    // MARK: - Menu actions

    @objc private func toggleEnabled() {
        enabled.toggle()
        if !enabled { clearOverlay() }
    }

    @objc private func toggleFlip() {
        flip.toggle()
        clearOverlay()
    }

    @objc private func setTurnMode(_ sender: NSMenuItem) {
        turnMode = (sender.representedObject as? String) ?? "auto"
        clearOverlay()
    }

    @objc private func toggleMoveSafety() { options.showMoveSafety.toggle(); overlayView?.options = options }
    @objc private func toggleMates() { options.showMates.toggle(); overlayView?.options = options }
    @objc private func toggleThreats() { options.showThreats.toggle(); overlayView?.options = options }
    @objc private func togglePins() { options.showPins.toggle(); overlayView?.options = options }
    @objc private func toggleDanger() { options.showDanger.toggle(); overlayView?.options = options }
    @objc private func toggleDefended() { options.showDefended.toggle(); overlayView?.options = options }
    @objc private func toggleArrows() { options.showAttackerArrows.toggle(); overlayView?.options = options }

    @objc private func copyFEN() {
        guard let fen = shownFEN else { return }
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString("\(fen) w - - 0 1", forType: .string)
    }

    @objc private func openSettings() {
        if let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture") {
            NSWorkspace.shared.open(url)
        }
    }

    // MARK: - Loop

    private func run() async {
        await ensureServer()
        while !Task.isCancelled {
            let start = Date()
            await tick()
            let wait = max(0, Self.interval - Date().timeIntervalSince(start))
            try? await Task.sleep(nanoseconds: UInt64(wait * 1_000_000_000))
        }
    }

    private func ensureServer() async {
        if await api.isHealthy() { return }
        guard let config else {
            status = "Board API not running on port \(api.baseURL.port ?? 0) (no config in app bundle)"
            return
        }
        if !server.isRunning {
            do {
                try server.start(config)
            } catch {
                status = "Could not start board API: \(error.localizedDescription)"
                return
            }
        }
        status = "Starting board API…"
        for _ in 0..<120 {  // model loading takes a few seconds
            if await api.isHealthy() { status = "Board API ready"; return }
            try? await Task.sleep(nanoseconds: 500_000_000)
        }
        status = "Board API did not start, see \(server.logURL.path)"
    }

    private func tick() async {
        guard enabled, Date() >= retryAfter else { return }
        // Never call ScreenCaptureKit without permission: every attempt makes macOS show its
        // "would like to record this computer's screen" dialog again. The preflight check is silent.
        guard CGPreflightScreenCaptureAccess() else {
            setPermissionMissing(true)
            return
        }
        setPermissionMissing(false)
        do {
            var (image, rect) = try await capturer.capture(region: region)
            var analysis = try await analyze(image, region: region != nil)
            if !analysis.found && region != nil {
                // the board left its old spot: search the whole screen again
                region = nil
                (image, rect) = try await capturer.capture(region: nil)
                analysis = try await analyze(image, region: false)
            }
            guard analysis.found, let bbox = analysis.bbox, bbox.count == 4, let fen = analysis.fen else {
                region = nil
                status = "No board on screen"
                clearOverlay()
                return
            }
            let scale = CGFloat(image.width) / rect.width
            let board = CGRect(x: rect.minX + bbox[0] / scale, y: rect.minY + bbox[1] / scale,
                               width: (bbox[2] - bbox[0]) / scale, height: (bbox[3] - bbox[1]) / scale)
            let margin = max(8, board.width * Self.regionMargin)
            region = board.insetBy(dx: -margin, dy: -margin).intersection(capturer.displayBounds)
            status = String(format: "Board at %.0f,%.0f (%.0f pt) · %.0f ms", board.minX, board.minY, board.width, analysis.ms ?? 0)

            // show a position once it has been seen twice in a row (skips mid-animation frames);
            // while it's unconfirmed, hide the old annotations rather than show stale ones
            if fen == shownFEN || fen == candidateFEN {
                shownFEN = fen
                lastAnalysis = analysis
                overlayView?.state = OverlayState(boardRect: board, flip: flip, analysis: analysis)
            } else {
                candidateFEN = fen
                overlayView?.state = nil
            }
        } catch {
            region = nil
            capturer.reset()
            clearOverlay()
            retryAfter = Date().addingTimeInterval(2)  // don't hammer a failing capture or API
            if error is URLError {
                status = "Board API not reachable, restarting it…"
                await ensureServer()
            } else {
                status = "Screen capture failed: \(error.localizedDescription). If you just allowed Screen Recording, quit and reopen the app."
            }
        }
    }

    private func setPermissionMissing(_ missing: Bool) {
        guard missing != permissionMissing else { return }
        permissionMissing = missing
        statusItem.button?.title = missing ? "♞⚠︎" : "♞"
        if missing {
            clearOverlay()
            status = "No Screen Recording permission: turn on ChessVision Overlay in System Settings > Privacy & Security > Screen & System Audio Recording, then quit and reopen this app"
        } else {
            status = "Screen Recording allowed"
        }
    }

    private func analyze(_ image: CGImage, region: Bool) async throws -> Analysis {
        let jpeg = await Task.detached(priority: .userInitiated) { jpegData(from: image) }.value
        guard let jpeg else { throw CaptureError.noDisplay }
        return try await api.analyze(jpeg: jpeg, flip: flip, region: region, turn: turnMode)
    }

    private func clearOverlay() {
        overlayView?.state = nil
        if dragSquare == nil { clearHover() }  // keep a dragged piece's squares until it's dropped
        candidateFEN = nil
        shownFEN = nil
        lastAnalysis = nil
    }
}
