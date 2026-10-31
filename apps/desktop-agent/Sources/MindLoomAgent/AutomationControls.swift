import AppKit
import Carbon.HIToolbox
import Foundation

/// ⌃⌥⌘. stops whatever Loom is doing on this Mac, from any app. A Carbon hot key needs
/// no extra permission and works while Loom is in the background.
final class StopHotKey {
    static let keyCode = UInt32(kVK_ANSI_Period)
    static let modifiers = UInt32(controlKey | optionKey | cmdKey)

    private var hotKey: EventHotKeyRef?
    private var handler: EventHandlerRef?
    private let onPress: () -> Void

    init(onPress: @escaping () -> Void) {
        self.onPress = onPress
        var spec = EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed))
        let context = Unmanaged.passUnretained(self).toOpaque()
        InstallEventHandler(GetApplicationEventTarget(), { _, _, context in
            guard let context else { return noErr }
            let me = Unmanaged<StopHotKey>.fromOpaque(context).takeUnretainedValue()
            DispatchQueue.main.async { me.onPress() }
            return noErr
        }, 1, &spec, context, &handler)
        let id = EventHotKeyID(signature: OSType(0x4C4F_4F4D), id: 1)  // "LOOM"
        let status = RegisterEventHotKey(Self.keyCode, Self.modifiers, id, GetApplicationEventTarget(), 0, &hotKey)
        if status != noErr {
            NSLog("MindLoomAgent: couldn't register the stop hot key (%d)", status)
        }
    }

    deinit {
        if let hotKey { UnregisterEventHotKey(hotKey) }
        if let handler { RemoveEventHandler(handler) }
    }
}

/// Notices the person typing, clicking or scrolling, from the hardware event state.
/// Events Loom posts to an app's process don't count, and anything before `ignoreBefore`
/// (Loom's own actions, a click on its overlay) is ignored.
struct UserInputWatcher {
    static let types: [CGEventType] = [.keyDown, .leftMouseDown, .rightMouseDown, .otherMouseDown, .scrollWheel]
    /// Input this recent counts as "the person is using the Mac".
    static let window: TimeInterval = 1.5

    var ignoreBefore = Date()

    static var secondsSinceInput: TimeInterval {
        types.map { CGEventSource.secondsSinceLastEventType(.hidSystemState, eventType: $0) }.min() ?? .infinity
    }

    func personIsActive(now: Date = Date()) -> Bool {
        let since = Self.secondsSinceInput
        guard since < Self.window else { return false }
        return now.addingTimeInterval(-since) > ignoreBefore
    }

    /// The person typed, clicked or scrolled after `date`.
    static func inputSince(_ date: Date) -> Bool {
        let since = secondsSinceInput
        return since < window && Date().addingTimeInterval(-since) > date
    }

    mutating func reset() {
        ignoreBefore = Date()
    }
}
