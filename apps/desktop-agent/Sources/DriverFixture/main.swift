import AppKit

/// "Loom Fixture": a window of known controls for checking LoomDriver against real
/// Accessibility (`MindLoomAgent --driver-selftest` drives it). Every change is shown
/// in the Status label, so the self-test can read the result back.
final class FixtureController: NSObject, NSApplicationDelegate {
    var window: NSWindow!
    let name = NSTextField(string: "")
    let password = NSSecureTextField(string: "")
    let notes = NSTextField(string: "")
    let urgent = NSButton(checkboxWithTitle: "Urgent", target: nil, action: nil)
    let status = NSTextField(labelWithString: "Status: idle")
    let items = NSTextField(labelWithString: "Items: none")
    var list: [String] = []

    func applicationDidFinishLaunching(_ notification: Notification) {
        buildMenu()
        name.placeholderString = "Name"
        name.setAccessibilityLabel("Name")
        password.setAccessibilityLabel("Password")
        notes.setAccessibilityLabel("Notes")
        status.setAccessibilityLabel("Status")
        items.setAccessibilityLabel("Items")
        urgent.target = self
        urgent.action = #selector(toggled)

        let add = NSButton(title: "Add Item", target: self, action: #selector(addItem))
        let send = NSButton(title: "Send Report", target: self, action: #selector(send))
        send.keyEquivalent = "\r"  // the default button, so Return presses it
        let stack = NSStackView(views: [name, password, notes, urgent, add, send, items, status])
        stack.orientation = .vertical
        stack.alignment = .leading
        stack.spacing = 8
        stack.edgeInsets = NSEdgeInsets(top: 16, left: 16, bottom: 16, right: 16)
        for field in [name, password, notes] {
            field.widthAnchor.constraint(equalToConstant: 260).isActive = true
        }

        window = NSWindow(
            contentRect: NSRect(x: 200, y: 300, width: 320, height: 320),
            styleMask: [.titled, .closable, .miniaturizable], backing: .buffered, defer: false
        )
        window.title = "Loom Fixture"
        window.contentView = stack
        window.defaultButtonCell = send.cell as? NSButtonCell
        // No activate(): the self-test opens the fixture in the background.
        window.makeKeyAndOrderFront(nil)
    }

    private func buildMenu() {
        let main = NSMenu()
        let appItem = NSMenuItem()
        let appMenu = NSMenu(title: "Loom Fixture")
        appMenu.addItem(withTitle: "Quit Loom Fixture", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu
        main.addItem(appItem)

        let listItem = NSMenuItem()
        let listMenu = NSMenu(title: "List")
        let clear = NSMenuItem(title: "Clear List", action: #selector(clearList), keyEquivalent: "k")
        clear.target = self
        listMenu.addItem(clear)
        let delete = NSMenuItem(title: "Delete Everything", action: #selector(clearList), keyEquivalent: "")
        delete.target = self
        listMenu.addItem(delete)
        listItem.submenu = listMenu
        main.addItem(listItem)

        let editItem = NSMenuItem()
        let editMenu = NSMenu(title: "Edit")
        editMenu.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editMenu.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        editMenu.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        editItem.submenu = editMenu
        main.addItem(editItem)
        NSApp.mainMenu = main
    }

    private func setStatus(_ text: String) {
        status.stringValue = "Status: \(text)"
    }

    @objc private func addItem() {
        let value = name.stringValue.trimmingCharacters(in: .whitespaces)
        guard !value.isEmpty else { return setStatus("name is empty") }
        list.append(value)
        items.stringValue = "Items: \(list.joined(separator: ", "))"
        setStatus("added \(value)")
    }

    @objc private func send() { setStatus("report sent") }
    @objc private func toggled() { setStatus(urgent.state == .on ? "urgent on" : "urgent off") }

    @objc private func clearList() {
        list.removeAll()
        items.stringValue = "Items: none"
        setStatus("cleared")
    }
}

let app = NSApplication.shared
let controller = FixtureController()
app.delegate = controller
app.setActivationPolicy(.regular)
app.run()
