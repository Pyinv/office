import WebKit
import ObjectiveC

/// A WKWebView without the keyboard accessory bar (the ⌃ ⌄ ✓ pill iOS puts above the
/// keyboard for every web text field). WebKit draws it from its private content view, so the
/// only way to drop it is to give that view a subclass whose `inputAccessoryView` is nil —
/// the same trick Capacitor and Cordova use.
final class NoAccessoryWebView: WKWebView {
    override init(frame: CGRect, configuration: WKWebViewConfiguration) {
        super.init(frame: frame, configuration: configuration)
        removeAccessoryBar()
    }

    required init?(coder: NSCoder) { fatalError("not used") }

    override func didMoveToWindow() {
        super.didMoveToWindow()
        removeAccessoryBar()                 // WebKit may recreate the content view
    }

    private func removeAccessoryBar() {
        guard let content = scrollView.subviews.first(where: {
            String(describing: type(of: $0)).hasPrefix("WKContent")
        }) else { return }
        let base: AnyClass = type(of: content)
        let name = "NoAccessory_" + String(describing: base)
        if let done = NSClassFromString(name) {
            object_setClass(content, done)
            return
        }
        guard let sub = objc_allocateClassPair(base, name, 0) else { return }
        let sel = #selector(getter: UIResponder.inputAccessoryView)
        let imp: @convention(block) (AnyObject) -> UIView? = { _ in nil }
        class_addMethod(sub, sel, imp_implementationWithBlock(imp), "@@:")
        objc_registerClassPair(sub)
        object_setClass(content, sub)
    }
}
