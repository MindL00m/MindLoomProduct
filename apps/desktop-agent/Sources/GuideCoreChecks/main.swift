import Foundation
import GuideCore

var failures = 0

func check(_ condition: @autoclosure () -> Bool, _ message: String, line: Int = #line) {
    if !condition() {
        failures += 1
        print("FAIL line \(line): \(message)")
    }
}

let app = "com.vendor.app"
let amount = StepAnchor(bundleId: app, action: .fieldFocus, role: "AXTextField", title: "Amount")
let submit = StepAnchor(bundleId: app, action: .click, role: "AXButton", identifier: "submit-btn", title: "Submit")
let review = StepAnchor(bundleId: app, action: .navigate, role: "AXWindow", windowTitle: "Review Invoice")

// Matcher
check(GuideMatcher.matches(expected: amount, observed: amount), "identical anchors match")
check(
    GuideMatcher.matches(
        expected: amount,
        observed: StepAnchor(bundleId: app, action: .fieldFocus, role: "AXTextField", title: "  amount ")
    ),
    "titles match case/whitespace-insensitively"
)
check(
    !GuideMatcher.matches(
        expected: amount,
        observed: StepAnchor(bundleId: "com.other", action: .fieldFocus, role: "AXTextField", title: "Amount")
    ),
    "different bundle never matches"
)
check(
    !GuideMatcher.matches(
        expected: submit,
        observed: StepAnchor(bundleId: app, action: .click, role: "AXButton", identifier: "cancel-btn", title: "Submit")
    ),
    "identifier mismatch wins over equal titles"
)
check(
    GuideMatcher.matches(
        expected: submit,
        observed: StepAnchor(bundleId: app, action: .click, role: "AXButton", title: "Submit")
    ),
    "falls back to title when observed has no identifier"
)
check(
    !GuideMatcher.matches(
        expected: submit,
        observed: StepAnchor(bundleId: app, action: .fieldFocus, role: "AXButton", title: "Submit")
    ),
    "incompatible action does not match"
)
check(
    GuideMatcher.matches(
        expected: review,
        observed: StepAnchor(bundleId: app, action: .navigate, role: "AXWindow", windowTitle: "Review Invoice — #123")
    ),
    "navigate matches when window title contains the expected title"
)

// Session
let guide = Guide(
    skillId: "g1",
    title: "Submit invoice",
    steps: [
        GuideStep(text: "Enter amount", anchor: amount),
        GuideStep(text: "Check totals"),
        GuideStep(text: "Open review", anchor: review),
        GuideStep(text: "Submit", anchor: submit),
    ]
)

var session = GuideSession(guide: guide, autoAdvance: true)
check(session.observe(amount), "current step auto-advances")
check(session.index == 1 && session.lastAdvanceWasAuto, "index moves to manual step")
check(!session.observe(StepAnchor(bundleId: app, action: .click, role: "AXButton", title: "Nope")), "unrelated action ignored")
check(session.observe(StepAnchor(bundleId: app, action: .navigate, role: "AXWindow", windowTitle: "Review Invoice")), "look-ahead skips a manual step")
check(session.index == 3, "index jumps past matched look-ahead step")
session.back()
check(session.index == 2 && !session.lastAdvanceWasAuto, "back moves one step")
session.next()
check(session.observe(submit), "last step auto-advances")
check(session.isFinished && session.currentStep == nil, "session finishes")
check(!session.observe(submit), "finished session ignores events")

var tooFar = GuideSession(guide: guide, autoAdvance: true)
check(!tooFar.observe(submit), "steps beyond look-ahead do not match")

var manual = GuideSession(guide: guide, autoAdvance: false)
check(!manual.observe(amount), "manual-only session ignores observed actions")
manual.next()
check(manual.index == 1, "manual next works")

// Per-step hints
let amountStep = GuideStep(text: "Enter the invoice amount", anchor: amount)
check(GuideHints.fields(["Amount (AXTextField)", "Vendor"], for: amountStep) == ["Amount (AXTextField)"], "fields match by label")
check(
    GuideHints.warnings(["Invoice amounts over $10k need approval", "Never share passwords"], for: amountStep)
        == ["Invoice amounts over $10k need approval"],
    "warnings match on shared significant words"
)

// Decoding the API payload
let json = """
[{"skill_id":"s1","title":"T","purpose":"P","application":"Vendor","bundle_ids":["com.vendor.app"],
  "steps":[{"text":"Click Submit","anchor":{"bundleId":"com.vendor.app","action":"click","role":"AXButton",
  "identifier":null,"title":"Submit","windowTitle":null}},{"text":"Manual","anchor":null}],
  "warnings":["Careful"],"important_fields":["Amount"],"updated_at":"2026-08-02T12:00:00Z"}]
""".data(using: .utf8)!
do {
    let decoded = try JSONDecoder().decode([Guide].self, from: json)
    check(decoded.count == 1 && decoded[0].autoStepCount == 1, "decodes guide payload")
    check(decoded[0].steps[0].anchor?.title == "Submit", "decodes camelCase anchor")
} catch {
    check(false, "decode failed: \(error)")
}

// Run decisions: perform only what the step stores; anything needing a value waits.
let saveButton = BrowserAnchor(url: "https://app.test/invoices", role: "button", name: "Save", selector: "#save")
let amountBox = BrowserAnchor(url: "https://app.test/invoices", role: "textbox", name: "Amount", needsValue: true)
let menuAnchor = StepAnchor(bundleId: app, action: .menu, role: "AXMenuItem", title: "Export…")
check(RunDecision.decide(GuideStep(text: "Submit", anchor: submit)) == .performAccessibility(submit), "click anchor performs")
check(RunDecision.decide(GuideStep(text: "Export", anchor: menuAnchor)) == .performAccessibility(menuAnchor), "menu anchor performs")
check(RunDecision.decide(GuideStep(text: "Review", anchor: review)) == .performAccessibility(review), "navigate anchor performs")
check(RunDecision.decide(GuideStep(text: "Amount", anchor: amount)) == .wait(.needsValue), "field_focus waits for a value")
check(
    RunDecision.decide(GuideStep(text: "Leave", anchor: StepAnchor(bundleId: app, action: .fieldBlur, role: "AXTextField")))
        == .wait(.needsValue),
    "field_blur waits for a value"
)
check(RunDecision.decide(GuideStep(text: "Save", browserAnchor: saveButton)) == .performBrowser(saveButton), "browser click performs in Chrome")
check(RunDecision.decide(GuideStep(text: "Amount", browserAnchor: amountBox)) == .wait(.needsValue), "browser text field waits")
check(RunDecision.decide(GuideStep(text: "Check totals")) == .wait(.noAnchor), "step without anchor waits")

let runJSON = """
{"run_id":"r1","org_id":"o","user_id":"u","skill_id":"s1","status":"waiting","step_index":1,
 "wait_reason":"needs_value","error":null,"created_at":"2026-10-01T12:00:00Z","updated_at":"2026-10-01T12:00:00Z",
 "guide":{"skill_id":"s1","title":"T","purpose":"","application":"Web","bundle_ids":[],
  "steps":[{"text":"Save","anchor":null,"browser_anchor":{"url":"https://app.test/invoices","role":"button",
  "name":"Save","href":null,"selector":"#save","needsValue":false}},
  {"text":"Amount","anchor":null,"browser_anchor":{"url":"https://app.test/invoices","role":"textbox",
  "name":"Amount","href":null,"selector":"input[name=\\"amount\\"]","needsValue":true}}],
  "warnings":[],"important_fields":[],"updated_at":"2026-10-01T12:00:00Z"}}
""".data(using: .utf8)!
do {
    let run = try JSONDecoder().decode(SkillRun.self, from: runJSON)
    check(run.status == .waiting && run.waitReason == .needsValue && run.stepIndex == 1, "decodes run state")
    check(run.guide?.steps[0].browserAnchor == saveButton, "decodes browser anchor")
    check(run.guide?.steps[1].browserAnchor?.needsValue == true, "decodes needsValue")
    check(run.guide?.autoStepCount == 0, "browser anchors don't count as desktop auto steps")
    check(run.createdDate == Date(timeIntervalSince1970: 1_790_856_000), "parses created_at")
} catch {
    check(false, "run decode failed: \(error)")
}

let micro = SkillRun(runId: "r", skillId: "s", status: .pending, createdAt: "2026-10-01T12:00:00.123456Z")
check(
    micro.createdDate.map { abs($0.timeIntervalSince1970 - 1_790_856_000.123) < 0.01 } == true,
    "parses microsecond created_at from the API"
)

if failures == 0 {
    print("GuideCoreChecks: all checks passed")
    exit(0)
}
print("GuideCoreChecks: \(failures) failure(s)")
exit(1)
