import AppKit

let arguments = CommandLine.arguments
if let index = arguments.firstIndex(of: "--render") {
    exit(RenderTest.run(Array(arguments[(index + 1)...])))
}

MainActor.assumeIsolated {  // top-level code runs on the main thread
    let app = NSApplication.shared
    let delegate = AppDelegate()
    app.delegate = delegate
    app.setActivationPolicy(.accessory)  // menu-bar only, no Dock icon
    app.run()
}
