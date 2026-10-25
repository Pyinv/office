import UIKit
import UniformTypeIdentifiers

/// Share → Office: everything you shared (texts, links, photos, files, in the order the app hands them
/// over) goes to one agent as ONE message, in one upload (POST /api/share, multipart).
///
///   ┌ Cancel      Send to…         Done ┐
///   │ 📎 3 items · 1 photo, 2 texts       │  status strip: always visible (sending… / Sent ✓ / waiting / error)
///   │ 🔍 Search agents                    │
///   │ ─ agents, most recent first ─       │  the only part that scrolls; tap one to send
///   │ ✏️ Add a note (optional)            │  pinned to the keyboard / bottom edge
///   └─────────────────────────────────────┘
final class ShareViewController: UIViewController {
    override func viewDidLoad() {
        super.viewDidLoad()
        let sheet = ShareSheetController()
        sheet.context = extensionContext
        let nav = UINavigationController(rootViewController: sheet)
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

private struct Agent: Decodable {
    let label: String       // "Pyinv Office | Sara" — what /api/share takes as the session
    let person: String
    let project: String
    let emoji: String
}

final class ShareSheetController: UIViewController, UITableViewDataSource, UITableViewDelegate,
                                  UISearchBarDelegate, UITextFieldDelegate {
    weak var context: NSExtensionContext?
    private var agents: [Agent] = []
    private var shown: [Agent] = []
    private var items: [SharedItem] = []
    private var meta = ""                       // see loadItems()
    private var sendingTo: String?              // label of the agent being sent to
    private var sent = false
    private let tmp = FileManager.default.temporaryDirectory.appendingPathComponent("share-" + UUID().uuidString)

    private let status = StatusStrip()
    private let waitNote = StatusStrip()
    private let search = UISearchBar()
    private let table = UITableView(frame: .zero, style: .insetGrouped)
    private let noteBox = UIView()
    private let note = UITextField()

    override func viewDidLoad() {
        super.viewDidLoad()
        title = "Send to…"
        view.backgroundColor = .systemGroupedBackground
        navigationItem.leftBarButtonItem = UIBarButtonItem(barButtonSystemItem: .cancel, target: self, action: #selector(cancel))
        try? FileManager.default.createDirectory(at: tmp, withIntermediateDirectories: true)

        search.placeholder = "Search agents"
        search.searchBarStyle = .minimal
        search.autocapitalizationType = .none
        search.delegate = self

        table.dataSource = self
        table.delegate = self
        table.register(AgentCell.self, forCellReuseIdentifier: "a")
        table.keyboardDismissMode = .onDrag
        table.backgroundColor = .clear

        noteBox.backgroundColor = .secondarySystemGroupedBackground
        noteBox.layer.cornerRadius = 12
        note.placeholder = "✏️  Add a note (optional)"
        note.clearButtonMode = .whileEditing
        note.returnKeyType = .done
        note.delegate = self
        waitNote.isHidden = true

        let stack = UIStackView(arrangedSubviews: [status, waitNote, search])
        stack.axis = .vertical
        stack.spacing = 6
        for v in [stack, table, noteBox, note] as [UIView] { v.translatesAutoresizingMaskIntoConstraints = false }
        view.addSubview(stack); view.addSubview(table); view.addSubview(noteBox); noteBox.addSubview(note)
        let g = view.safeAreaLayoutGuide
        NSLayoutConstraint.activate([
            stack.topAnchor.constraint(equalTo: g.topAnchor, constant: 6),
            stack.leadingAnchor.constraint(equalTo: g.leadingAnchor, constant: 12),
            stack.trailingAnchor.constraint(equalTo: g.trailingAnchor, constant: -12),
            table.topAnchor.constraint(equalTo: stack.bottomAnchor),
            table.leadingAnchor.constraint(equalTo: g.leadingAnchor),
            table.trailingAnchor.constraint(equalTo: g.trailingAnchor),
            table.bottomAnchor.constraint(equalTo: noteBox.topAnchor, constant: -6),
            noteBox.leadingAnchor.constraint(equalTo: g.leadingAnchor, constant: 16),
            noteBox.trailingAnchor.constraint(equalTo: g.trailingAnchor, constant: -16),
            noteBox.bottomAnchor.constraint(equalTo: view.keyboardLayoutGuide.topAnchor, constant: -8),
            note.leadingAnchor.constraint(equalTo: noteBox.leadingAnchor, constant: 12),
            note.trailingAnchor.constraint(equalTo: noteBox.trailingAnchor, constant: -8),
            note.topAnchor.constraint(equalTo: noteBox.topAnchor),
            note.bottomAnchor.constraint(equalTo: noteBox.bottomAnchor),
            note.heightAnchor.constraint(equalToConstant: 44),
        ])
        view.keyboardLayoutGuide.followsUndockedKeyboard = true
        status.show(.info, icon: "⏳", "Loading…")
        Task { await start() }
    }

    func textFieldShouldReturn(_ textField: UITextField) -> Bool { textField.resignFirstResponder(); return true }

    @objc private func cancel() {
        try? FileManager.default.removeItem(at: tmp)
        context?.cancelRequest(withError: NSError(domain: "Office", code: 0))
    }

    @objc private func done() {
        try? FileManager.default.removeItem(at: tmp)
        context?.completeRequest(returningItems: nil)
    }

    // MARK: loading

    private func start() async {
        guard let base = OfficeAddress.url else {
            status.show(.bad, icon: "!", "Open the Office app once and enter its address, then share again.")
            return
        }
        async let list = loadAgents(base)
        items = await loadItems()
        do {
            agents = try await list
            filter(search.text ?? "")
            status.show(.info, icon: "📎", items.isEmpty ? "Nothing to send" : summary())
            if agents.isEmpty { status.show(.bad, icon: "!", "No agents are running.") }
        } catch {
            status.show(.bad, icon: "!", "Can't reach the Office. Is Tailscale on? (\(error.localizedDescription))")
        }
    }

    private func summary() -> String {
        var photos = 0, files = 0, texts = 0
        for i in items {
            switch i {
            case .text: texts += 1
            case .file(_, _, let mime): if mime.hasPrefix("image/") { photos += 1 } else { files += 1 }
            }
        }
        var parts: [String] = []
        if photos > 0 { parts.append("\(photos) photo\(photos == 1 ? "" : "s")") }
        if files > 0 { parts.append("\(files) file\(files == 1 ? "" : "s")") }
        if texts > 0 { parts.append("\(texts) text\(texts == 1 ? "" : "s")") }
        return "\(items.count) item\(items.count == 1 ? "" : "s") · " + parts.joined(separator: ", ")
    }

    private func loadAgents(_ base: URL) async throws -> [Agent] {
        var req = URLRequest(url: base.appendingPathComponent("api/drop").appending(queryItems: [URLQueryItem(name: "rich", value: "1")]))
        req.timeoutInterval = 15
        let (data, resp) = try await URLSession.shared.data(for: req)
        guard (resp as? HTTPURLResponse)?.statusCode == 200 else { throw URLError(.badServerResponse) }
        struct List: Decodable { let agents: [Agent] }
        return try JSONDecoder().decode(List.self, from: data).agents
    }

    private func loadItems() async -> [SharedItem] {
        var out: [SharedItem] = []
        var n = 0
        var shape: [[String: Any]] = []
        for ext in context?.inputItems as? [NSExtensionItem] ?? [] {
            var kinds: [String] = []
            for p in ext.attachments ?? [] {
                kinds.append(p.registeredTypeIdentifiers.joined(separator: ","))
                if let item = await load(p, index: n) { out.append(item) }
                n += 1
            }
            // a caption ("text below the photo") travels with the item, not as an attachment:
            // it goes right after its photo, unless the app also sent it as a text of its own
            let caption = ext.attributedContentText?.string.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            // WhatsApp also puts all the selected texts, joined, in here: only add what isn't sent already
            var rest = caption
            for case .text(let t) in out {
                let t = t.trimmingCharacters(in: .whitespacesAndNewlines)
                if !t.isEmpty { rest = rest.replacingOccurrences(of: t, with: "") }
            }
            if !rest.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { out.append(.text(caption)) }
            shape.append(["attachments": kinds, "caption": caption.count])
        }
        // what the sending app handed over (types and order only, no content), so the server log can
        // show how e.g. WhatsApp orders a selection
        if let data = try? JSONSerialization.data(withJSONObject: shape), let s = String(data: data, encoding: .utf8) { meta = s }
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
                    // HEIC (iPhone photos and screenshots) can't be opened by the agents: send a JPEG
                    if let t = UTType(type), t.conforms(to: .heic) || t.conforms(to: .heif),
                       let img = UIImage(contentsOfFile: url.path), let jpg = img.jpegData(compressionQuality: 0.9) {
                        let jdest = dest.appendingPathExtension("jpg")
                        try jpg.write(to: jdest)
                        let base = (name as NSString).deletingPathExtension
                        cont.resume(returning: .file(name: base + ".jpg", url: jdest, mime: "image/jpeg"))
                        return
                    }
                    try FileManager.default.copyItem(at: url, to: dest)
                    let mime = UTType(type)?.preferredMIMEType ?? "application/octet-stream"
                    cont.resume(returning: .file(name: name, url: dest, mime: mime))
                } catch {
                    cont.resume(returning: nil)
                }
            }
        }
    }

    // MARK: search + list

    func searchBar(_ searchBar: UISearchBar, textDidChange searchText: String) { filter(searchText) }
    func searchBarSearchButtonClicked(_ searchBar: UISearchBar) { searchBar.resignFirstResponder() }

    private func filter(_ q: String) {
        let q = q.trimmingCharacters(in: .whitespaces).lowercased()
        shown = q.isEmpty ? agents : agents.filter { $0.person.lowercased().contains(q) || $0.project.lowercased().contains(q) }
        table.reloadData()
    }

    func tableView(_ tableView: UITableView, numberOfRowsInSection section: Int) -> Int { shown.count }

    func tableView(_ tableView: UITableView, titleForHeaderInSection section: Int) -> String? {
        shown.isEmpty ? nil : ((search.text ?? "").isEmpty ? "Recent" : nil)
    }

    func tableView(_ tableView: UITableView, cellForRowAt indexPath: IndexPath) -> UITableViewCell {
        let cell = tableView.dequeueReusableCell(withIdentifier: "a", for: indexPath) as! AgentCell
        let a = shown[indexPath.row]
        let state: AgentCell.State = sendingTo == nil ? .idle : (sendingTo == a.label ? (sent ? .done : .sending) : .dimmed)
        cell.set(a.emoji, a.person, a.project, query: search.text ?? "", state: state)
        return cell
    }

    func tableView(_ tableView: UITableView, didSelectRowAt indexPath: IndexPath) {
        tableView.deselectRow(at: indexPath, animated: true)
        guard sendingTo == nil, !items.isEmpty, let base = OfficeAddress.url else { return }
        let a = shown[indexPath.row]
        sendingTo = a.label
        view.endEditing(true)
        status.show(.info, icon: "⏳", "Sending \(items.count == 1 ? "1 item" : "\(items.count) items") to \(a.person)…", spinning: true)
        table.reloadData()
        Task { await send(to: a, base: base) }
    }

    // MARK: sending

    private func send(to a: Agent, base: URL) async {
        do {
            let boundary = "office-" + UUID().uuidString
            let body = try multipart(boundary: boundary, session: a.label, note: note.text ?? "")
            var req = URLRequest(url: base.appendingPathComponent("api/share"))
            req.httpMethod = "POST"
            req.timeoutInterval = 300
            req.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
            let (data, resp) = try await URLSession.shared.upload(for: req, fromFile: body)
            let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
            let answer = (try? JSONSerialization.jsonObject(with: data) as? [String: Any]) ?? [:]
            guard code == 200 else {
                throw NSError(domain: "Office", code: code,
                              userInfo: [NSLocalizedDescriptionKey: (answer["error"] as? String) ?? "the Office answered \(code)"])
            }
            sent = true
            status.show(.ok, icon: "✓", "Sent to \(a.person) · \(items.count == 1 ? "1 item" : "\(items.count) items")")
            switch answer["why"] as? String {
            case "question": waitNote.show(.warn, icon: "⏳", "\(a.person) is asking you something. It goes in once you answer.")
            case "draft": waitNote.show(.warn, icon: "⏳", "There is unsent text in \(a.person)'s box. It goes in once that is sent or cleared.")
            case "busy": waitNote.show(.warn, icon: "⏳", "It goes in in a moment.")
            default: break
            }
            table.reloadData()
            navigationItem.leftBarButtonItem = nil
            navigationItem.rightBarButtonItem = UIBarButtonItem(barButtonSystemItem: .done, target: self, action: #selector(done))
            if waitNote.isHidden {                          // nothing to read: close by itself
                try? await Task.sleep(nanoseconds: 900_000_000)
                done()
            }
        } catch {
            sendingTo = nil
            status.show(.bad, icon: "!", "Not sent: \(error.localizedDescription). Tap the agent to retry.")
            table.reloadData()
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
        if !meta.isEmpty { try field("meta", meta) }
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

/// The strip at the top: what's being shared, then sending / sent / waiting / error. Never scrolls away.
private final class StatusStrip: UIView {
    enum Kind { case info, ok, warn, bad }
    private let icon = UILabel()
    private let text = UILabel()
    private let spin = UIActivityIndicatorView(style: .medium)

    init() {
        super.init(frame: .zero)
        layer.cornerRadius = 12
        text.numberOfLines = 0
        text.font = .preferredFont(forTextStyle: .subheadline)
        icon.font = .preferredFont(forTextStyle: .body)
        icon.setContentHuggingPriority(.required, for: .horizontal)
        spin.hidesWhenStopped = true
        let row = UIStackView(arrangedSubviews: [spin, icon, text])
        row.spacing = 8
        row.alignment = .center
        row.translatesAutoresizingMaskIntoConstraints = false
        addSubview(row)
        NSLayoutConstraint.activate([
            row.topAnchor.constraint(equalTo: topAnchor, constant: 9),
            row.bottomAnchor.constraint(equalTo: bottomAnchor, constant: -9),
            row.leadingAnchor.constraint(equalTo: leadingAnchor, constant: 12),
            row.trailingAnchor.constraint(equalTo: trailingAnchor, constant: -12),
        ])
    }
    required init?(coder: NSCoder) { fatalError() }

    func show(_ kind: Kind, icon i: String, _ s: String, spinning: Bool = false) {
        isHidden = false
        text.text = s
        icon.text = spinning ? nil : i
        icon.isHidden = spinning
        if spinning { spin.startAnimating() } else { spin.stopAnimating() }
        switch kind {
        case .info: backgroundColor = .secondarySystemGroupedBackground; text.textColor = .label
        case .ok: backgroundColor = .systemGreen.withAlphaComponent(0.16); text.textColor = .systemGreen
        case .warn: backgroundColor = .systemOrange.withAlphaComponent(0.16); text.textColor = .systemOrange
        case .bad: backgroundColor = .systemRed.withAlphaComponent(0.14); text.textColor = .systemRed
        }
        icon.textColor = text.textColor
    }
}

/// An agent row: emoji avatar, name (search match in the accent colour), project.
private final class AgentCell: UITableViewCell {
    enum State { case idle, sending, done, dimmed }
    private let avatar = UILabel()
    private let name = UILabel()
    private let project = UILabel()
    private let spin = UIActivityIndicatorView(style: .medium)

    override init(style: UITableViewCell.CellStyle, reuseIdentifier: String?) {
        super.init(style: style, reuseIdentifier: reuseIdentifier)
        avatar.font = .systemFont(ofSize: 17)
        avatar.textAlignment = .center
        avatar.layer.cornerRadius = 8
        avatar.clipsToBounds = true
        name.font = .systemFont(ofSize: 16, weight: .semibold)
        project.font = .preferredFont(forTextStyle: .footnote)
        project.textColor = .secondaryLabel
        let text = UIStackView(arrangedSubviews: [name, project])
        text.axis = .vertical
        let row = UIStackView(arrangedSubviews: [avatar, text, spin])
        row.spacing = 12
        row.alignment = .center
        row.translatesAutoresizingMaskIntoConstraints = false
        contentView.addSubview(row)
        NSLayoutConstraint.activate([
            avatar.widthAnchor.constraint(equalToConstant: 32),
            avatar.heightAnchor.constraint(equalToConstant: 32),
            row.topAnchor.constraint(equalTo: contentView.topAnchor, constant: 8),
            row.bottomAnchor.constraint(equalTo: contentView.bottomAnchor, constant: -8),
            row.leadingAnchor.constraint(equalTo: contentView.layoutMarginsGuide.leadingAnchor),
            row.trailingAnchor.constraint(equalTo: contentView.layoutMarginsGuide.trailingAnchor),
        ])
    }
    required init?(coder: NSCoder) { fatalError() }

    func set(_ emoji: String, _ person: String, _ proj: String, query: String, state: State) {
        avatar.text = emoji
        // a soft colour per name, like the Office cards
        var h = 0
        for u in person.unicodeScalars { h = (h * 31 + Int(u.value)) % 360 }
        avatar.backgroundColor = UIColor(hue: CGFloat(h) / 360, saturation: 0.25, brightness: 0.95, alpha: 1)
        name.attributedText = highlight(person, query)
        project.attributedText = highlight(proj, query, base: .secondaryLabel)
        contentView.alpha = state == .dimmed ? 0.4 : 1
        selectionStyle = state == .idle ? .default : .none
        accessoryType = state == .done ? .checkmark : .none
        if state == .sending { spin.startAnimating() } else { spin.stopAnimating() }
    }

    private func highlight(_ s: String, _ q: String, base: UIColor = .label) -> NSAttributedString {
        let out = NSMutableAttributedString(string: s, attributes: [.foregroundColor: base])
        let q = q.trimmingCharacters(in: .whitespaces)
        if !q.isEmpty, let r = s.range(of: q, options: .caseInsensitive) {
            out.addAttribute(.foregroundColor, value: UIColor.tintColor, range: NSRange(r, in: s))
        }
        return out
    }
}
