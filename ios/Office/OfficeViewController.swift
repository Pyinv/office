import UIKit
import WebKit
import SafariServices

/// Full-screen view of your Office. The page does everything (terminals, pull-to-refresh,
/// the message box); this just hosts it without a browser around it.
final class OfficeViewController: UIViewController, WKNavigationDelegate, WKUIDelegate {
    private var web: NoAccessoryWebView!
    private var officeHost = ""

    override var preferredStatusBarStyle: UIStatusBarStyle { .default }

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .systemBackground
        let cfg = WKWebViewConfiguration()
        cfg.allowsInlineMediaPlayback = true
        cfg.websiteDataStore = .default()
        web = NoAccessoryWebView(frame: view.bounds, configuration: cfg)
        web.navigationDelegate = self
        web.uiDelegate = self
        web.allowsBackForwardNavigationGestures = true
        web.scrollView.contentInsetAdjustmentBehavior = .never
        web.scrollView.bounces = false
        web.isOpaque = false
        web.backgroundColor = .clear
        view.addSubview(web)
        // below the status bar, down to the very bottom (the page handles the keyboard itself)
        web.translatesAutoresizingMaskIntoConstraints = false
        NSLayoutConstraint.activate([
            web.topAnchor.constraint(equalTo: view.safeAreaLayoutGuide.topAnchor),
            web.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            web.trailingAnchor.constraint(equalTo: view.trailingAnchor),
            web.bottomAnchor.constraint(equalTo: view.bottomAnchor),
        ])
        if let url = savedURL() { load(url) }
    }

    // an alert can only be presented once the view is on screen — not from viewDidLoad
    override func viewDidAppear(_ animated: Bool) {
        super.viewDidAppear(animated)
        if savedURL() == nil && presentedViewController == nil { askForURL() }
    }

    // MARK: where is the Office
    private func savedURL() -> URL? {
        OfficeAddress.url
    }

    private func askForURL(message: String = "The address of your Office, e.g. https://office.example.com") {
        let a = UIAlertController(title: "Office address", message: message, preferredStyle: .alert)
        a.addTextField { tf in
            tf.placeholder = "https://…"
            tf.keyboardType = .URL
            tf.autocapitalizationType = .none
            tf.autocorrectionType = .no
            tf.text = OfficeAddress.url?.absoluteString
        }
        a.addAction(UIAlertAction(title: "Open", style: .default) { [weak self] _ in
            guard let self else { return }
            var s = a.textFields?.first?.text?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            if !s.contains("://") { s = "https://" + s }
            guard let u = URL(string: s), u.host != nil else { self.askForURL(message: "That isn't a valid address."); return }
            OfficeAddress.save(u)
            self.load(u)
        })
        present(a, animated: true)
    }

    private func load(_ url: URL) {
        officeHost = url.host ?? ""
        web.load(URLRequest(url: url))
    }

    // shake the phone to change the address
    override var canBecomeFirstResponder: Bool { true }
    override func motionEnded(_ motion: UIEvent.EventSubtype, with event: UIEvent?) {
        if motion == .motionShake { askForURL(message: "Change the Office address?") }
    }

    // MARK: navigation
    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = action.request.url, let host = url.host else { decisionHandler(.allow); return }
        // links to other sites open in an in-app Safari sheet; the Office itself stays here
        if host != officeHost, ["http", "https"].contains(url.scheme ?? "") {
            present(SFSafariViewController(url: url), animated: true)
            decisionHandler(.cancel)
            return
        }
        decisionHandler(.allow)
    }

    /// target=_blank (file links, "open in new tab"): open on top as a sheet
    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = action.request.url {
            if url.host == officeHost {
                let vc = SheetWebViewController(url: url, configuration: configuration)
                present(UINavigationController(rootViewController: vc), animated: true)
            } else {
                present(SFSafariViewController(url: url), animated: true)
            }
        }
        return nil
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        showError(error)
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        showError(error)
    }

    private func showError(_ error: Error) {
        let e = error as NSError
        if e.code == NSURLErrorCancelled { return }
        let a = UIAlertController(title: "Can't reach the Office",
                                  message: e.localizedDescription + "\n\nIs the VPN on?",
                                  preferredStyle: .alert)
        a.addAction(UIAlertAction(title: "Retry", style: .default) { [weak self] _ in
            if let u = self?.savedURL() { self?.load(u) }
        })
        a.addAction(UIAlertAction(title: "Change address", style: .default) { [weak self] _ in self?.askForURL() })
        present(a, animated: true)
    }
}

/// A second Office page on top (a terminal popped out, a file from the browser) with a Done button.
final class SheetWebViewController: UIViewController {
    private let url: URL
    private let configuration: WKWebViewConfiguration

    init(url: URL, configuration: WKWebViewConfiguration) {
        self.url = url
        self.configuration = configuration
        super.init(nibName: nil, bundle: nil)
    }

    required init?(coder: NSCoder) { fatalError("not used") }

    override func viewDidLoad() {
        super.viewDidLoad()
        let web = NoAccessoryWebView(frame: view.bounds, configuration: configuration)
        web.autoresizingMask = [.flexibleWidth, .flexibleHeight]
        view.addSubview(web)
        web.load(URLRequest(url: url))
        navigationItem.rightBarButtonItem = UIBarButtonItem(barButtonSystemItem: .done, target: self, action: #selector(close))
        title = url.lastPathComponent
    }

    @objc private func close() { dismiss(animated: true) }
}
