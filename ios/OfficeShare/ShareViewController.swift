import UIKit
import UniformTypeIdentifiers

/// Share → Office: pick an agent, add an optional note, and everything you shared (texts, links,
/// photos, files — in the order you selected them) goes to that agent as ONE message, in one upload
/// (POST /api/share, multipart). Tapping an agent sends.
final class ShareViewController: UIViewController {
    private let list = AgentListController()

    override func viewDidLoad() {
        super.viewDidLoad()
        list.context = extensionContext
        let nav = UINavigationController(rootViewController: list)
        addChild(nav)
        nav.view.frame = view.bounds
        nav.view.autoresizingMask = [.flexibleWidth, .flexibleHeight]
        view.addSubview(nav.view)
        nav.didMove(toParent: self)
    }
}

/// One shared thing, in the order it was shared.
private enum SharedItem {
    case text(String)
    case file(name: String, url: URL, mime: String)
}

final class AgentListController: UITableViewController, UITextFieldDelegate {
    weak var context: NSExtensionContext?
    private var agents: [String] = []          // labels ("Pyinv Office | Sara"), most recent first; the server accepts them
    private var items: [SharedItem] = []
    private var status = "Loading…"
    private var sending = false
    private let note = UITextField()
    private let tmp = FileManager.default.temporaryDirectory.appendingPathComponent("share-" + UUID().uuidString)

    init() { super.init(style: .insetGrouped) }
    required init?(coder: NSCoder) { fatalError() }

    override func viewDidLoad() {
        super.viewDidLoad()
        title = "Send to…"
        navigationItem.leftBarButtonItem = UIBarButtonItem(barButtonSystemItem: .cancel, target: self, action: #selector(cancel))
        tableView.register(UITableViewCell.self, forCellReuseIdentifier: "c")
        note.placeholder = "Note (optional)"
        note.clearButtonMode = .whileEditing
        note.returnKeyType = .done
        note.delegate = self
        try? FileManager.default.createDirectory(at: tmp, withIntermediateDirectories: true)
        Task { await start() }
    }

    func textFieldShouldReturn(_ textField: UITextField) -> Bool { textField.resignFirstResponder(); return true }

    @objc private func cancel() {
        try? FileManager.default.removeItem(at: tmp)
        context?.cancelRequest(withError: NSError(domain: "Office", code: 0))
    }

    // MARK: loading

    private func start() async {
        guard let base = OfficeAddress.url else {
            status = "Open the Office app once and enter its address, then share again."
            tableView.reloadData(); return
        }
        async let names = loadAgents(base)
        items = await loadItems()
        do {
            agents = try await names
            status = agents.isEmpty ? "No agents are running." : summary()
        } catch {
            status = "Can't reach the Office (is Tailscale on?) — \(error.localizedDescription)"
        }
        tableView.reloadData()
    }

    private func summary() -> String {
        let texts = items.filter { if case .text = $0 { return true }; return false }.count
        let files = items.count - texts
        var parts: [String] = []
        if files > 0 { parts.append("\(files) file\(files == 1 ? "" : "s")") }
        if texts > 0 { parts.append("\(texts) text\(texts == 1 ? "" : "s")") }
        return parts.isEmpty ? "Nothing to send" : parts.joined(separator: " · ") + " — tap an agent to send"
    }

    private func loadAgents(_ base: URL) async throws -> [String] {
        var req = URLRequest(url: base.appendingPathComponent("api/drop").appending(queryItems: [URLQueryItem(name: "lines", value: "1")]))
        req.timeoutInterval = 15
        let (data, resp) = try await URLSession.shared.data(for: req)
        guard (resp as? HTTPURLResponse)?.statusCode == 200 else { throw URLError(.badServerResponse) }
        return String(decoding: data, as: UTF8.self).split(separator: "\n").map(String.init).filter { !$0.isEmpty }
    }

    /// Every attachment of every input item, in order. Each is turned into a text or a file copy
    /// (the system deletes its own copy as soon as the load callback returns).
    private func loadItems() async -> [SharedItem] {
        var out: [SharedItem] = []
        let providers = (context?.inputItems as? [NSExtensionItem] ?? []).flatMap { $0.attachments ?? [] }
        for (n, p) in providers.enumerated() {
            if let item = await load(p, index: n) { out.append(item) }
        }
        return out
    }

    private func load(_ p: NSItemProvider, index: Int) async -> SharedItem? {
        // a web link: send it as text
        if p.hasItemConformingToTypeIdentifier(UTType.url.identifier),
           !p.hasItemConformingToTypeIdentifier(UTType.fileURL.identifier),
           let url = try? await loadItem(p, UTType.url.identifier) as? URL, !url.isFileURL {
            return .text(url.absoluteString)
        }
        // plain text (WhatsApp hands texts over as small text files — the server turns those back into text)
        if p.hasItemConformingToTypeIdentifier(UTType.plainText.identifier),
           !p.hasItemConformingToTypeIdentifier(UTType.image.identifier) {
            if let s = try? await loadItem(p, UTType.plainText.identifier) {
                if let str = s as? String { return .text(str) }
                if let data = s as? Data { return .text(String(decoding: data, as: UTF8.self)) }
                if let url = s as? URL, let data = try? Data(contentsOf: url) {
                    return .text(String(decoding: data, as: UTF8.self))
                }
            }
        }
        // anything else: the file itself, with its own name
        let type = p.registeredTypeIdentifiers.first(where: { UTType($0)?.conforms(to: .data) ?? false })
            ?? p.registeredTypeIdentifiers.first ?? UTType.data.identifier
        if let f = await copyFile(p, type, index: index) { return f }
        // last resort: an in-memory image (some apps share a UIImage, not a file)
        if let img = try? await loadItem(p, UTType.image.identifier) as? UIImage, let data = img.jpegData(compressionQuality: 0.9) {
            let url = tmp.appendingPathComponent("image-\(index).jpg")
            if (try? data.write(to: url)) != nil { return .file(name: (p.suggestedName ?? "Image") + ".jpg", url: url, mime: "image/jpeg") }
        }
        return nil
    }

    private func loadItem(_ p: NSItemProvider, _ type: String) async throws -> NSSecureCoding? {
        try await withCheckedThrowingContinuation { cont in
            p.loadItem(forTypeIdentifier: type, options: nil) { item, error in
                if let error { cont.resume(throwing: error) } else { cont.resume(returning: item) }
            }
        }
    }

    private func copyFile(_ p: NSItemProvider, _ type: String, index: Int) async -> SharedItem? {
        await withCheckedContinuation { (cont: CheckedContinuation<SharedItem?, Never>) in
            p.loadFileRepresentation(forTypeIdentifier: type) { url, _ in
                guard let url else { cont.resume(returning: nil); return }
                let name = p.suggestedName.map { n in
                    // suggestedName has no extension: take it from the file the system handed us
                    n.contains(".") || url.pathExtension.isEmpty ? n : n + "." + url.pathExtension
                } ?? url.lastPathComponent
                let dest = self.tmp.appendingPathComponent("\(index)-" + UUID().uuidString)
                do {
                    try FileManager.default.copyItem(at: url, to: dest)
                    let mime = UTType(type)?.preferredMIMEType ?? "application/octet-stream"
                    cont.resume(returning: .file(name: name, url: dest, mime: mime))
                } catch {
                    cont.resume(returning: nil)
                }
            }
        }
    }

    // MARK: table

    override func numberOfSections(in tableView: UITableView) -> Int { 2 }

    override func tableView(_ tableView: UITableView, numberOfRowsInSection section: Int) -> Int {
        section == 0 ? 1 : agents.count
    }

    override func tableView(_ tableView: UITableView, titleForHeaderInSection section: Int) -> String? {
        section == 0 ? nil : (agents.isEmpty ? nil : "Agents")
    }

    override func tableView(_ tableView: UITableView, titleForFooterInSection section: Int) -> String? {
        section == 0 ? status : nil
    }

    override func tableView(_ tableView: UITableView, cellForRowAt indexPath: IndexPath) -> UITableViewCell {
        let cell = tableView.dequeueReusableCell(withIdentifier: "c", for: indexPath)
        cell.contentView.subviews.filter { $0 === note }.forEach { $0.removeFromSuperview() }
        var conf = cell.defaultContentConfiguration()
        if indexPath.section == 0 {
            note.translatesAutoresizingMaskIntoConstraints = false
            cell.contentView.addSubview(note)
            NSLayoutConstraint.activate([
                note.leadingAnchor.constraint(equalTo: cell.contentView.layoutMarginsGuide.leadingAnchor),
                note.trailingAnchor.constraint(equalTo: cell.contentView.layoutMarginsGuide.trailingAnchor),
                note.topAnchor.constraint(equalTo: cell.contentView.topAnchor),
                note.bottomAnchor.constraint(equalTo: cell.contentView.bottomAnchor),
                note.heightAnchor.constraint(greaterThanOrEqualToConstant: 44),
            ])
            cell.contentConfiguration = nil
            cell.selectionStyle = .none
            return cell
        }
        let label = agents[indexPath.row]
        let parts = label.components(separatedBy: " | ")
        conf.text = parts.count == 2 ? parts[1] : label
        conf.secondaryText = parts.count == 2 ? parts[0] : nil
        conf.secondaryTextProperties.color = .secondaryLabel
        cell.contentConfiguration = conf
        cell.selectionStyle = .default
        cell.accessoryType = .none
        return cell
    }

    override func tableView(_ tableView: UITableView, didSelectRowAt indexPath: IndexPath) {
        tableView.deselectRow(at: indexPath, animated: true)
        guard indexPath.section == 1, !sending, !items.isEmpty, let base = OfficeAddress.url else { return }
        sending = true
        note.resignFirstResponder()
        let label = agents[indexPath.row]
        status = "Sending to \(label.components(separatedBy: " | ").last ?? label)…"
        tableView.reloadData()
        Task { await send(to: label, base: base) }
    }

    // MARK: sending

    private func send(to label: String, base: URL) async {
        do {
            let boundary = "office-" + UUID().uuidString
            let body = try multipart(boundary: boundary, session: label, note: note.text ?? "")
            var req = URLRequest(url: base.appendingPathComponent("api/share"))
            req.httpMethod = "POST"
            req.timeoutInterval = 300
            req.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
            let (data, resp) = try await URLSession.shared.upload(for: req, fromFile: body)
            let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
            guard code == 200 else {
                let msg = (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["error"] as? String
                throw NSError(domain: "Office", code: code, userInfo: [NSLocalizedDescriptionKey: msg ?? "the Office answered \(code)"])
            }
            let answer = (try? JSONSerialization.jsonObject(with: data) as? [String: Any]) ?? [:]
            let waiting = (answer["waiting"] as? Bool) ?? false
            let who = (answer["to"] as? String) ?? "the agent"
            switch answer["why"] as? String {
            case "question": status = "Sent ✓ — \(who) is asking you something; it goes in once you answer"
            case "draft": status = "Sent ✓ — there is unsent text in \(who)'s box; it goes in once that is sent or cleared"
            default: status = waiting ? "Sent ✓ — it goes in in a moment" : "Sent ✓ — \(who) has it"
            }
            tableView.reloadData()
            try? await Task.sleep(nanoseconds: waiting ? 1_400_000_000 : 600_000_000)
            try? FileManager.default.removeItem(at: tmp)
            context?.completeRequest(returningItems: nil)
        } catch {
            sending = false
            status = "Not sent: \(error.localizedDescription)"
            tableView.reloadData()
        }
    }

    /// The whole share as one multipart body, written to a file (photos and videos can be large).
    private func multipart(boundary: String, session: String, note: String) throws -> URL {
        let url = tmp.appendingPathComponent("body")
        FileManager.default.createFile(atPath: url.path, contents: nil)
        let h = try FileHandle(forWritingTo: url)
        defer { try? h.close() }
        func w(_ s: String) throws { try h.write(contentsOf: Data(s.utf8)) }
        func field(_ name: String, _ value: String) throws {
            try w("--\(boundary)\r\nContent-Disposition: form-data; name=\"\(name)\"\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n\(value)\r\n")
        }
        try field("session", session)
        if !note.isEmpty { try field("note", note) }
        for item in items {
            switch item {
            case .text(let s):
                try field("text", s)
            case .file(let name, let src, let mime):
                let safe = name.replacingOccurrences(of: "\"", with: "'").replacingOccurrences(of: "\r", with: " ").replacingOccurrences(of: "\n", with: " ")
                try w("--\(boundary)\r\nContent-Disposition: form-data; name=\"file\"; filename=\"\(safe)\"\r\nContent-Type: \(mime)\r\n\r\n")
                let r = try FileHandle(forReadingFrom: src)
                while let chunk = try r.read(upToCount: 1 << 20), !chunk.isEmpty { try h.write(contentsOf: chunk) }
                try r.close()
                try w("\r\n")
            }
        }
        try w("--\(boundary)--\r\n")
        return url
    }
}
