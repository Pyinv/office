import Foundation

/// The Office address, kept where both the app and its share extension can read it
/// (an App Group's defaults). The app sets it on first launch; the share sheet only reads it.
enum OfficeAddress {
    static let group = "group.com.pyinv.office"
    private static let key = "officeURL"

    private static var shared: UserDefaults? { UserDefaults(suiteName: group) }

    static var url: URL? {
        // older installs kept it in the app's own defaults: carry it over once
        if shared?.string(forKey: key) == nil, let old = UserDefaults.standard.string(forKey: key) {
            shared?.set(old, forKey: key)
        }
        guard let s = shared?.string(forKey: key) ?? UserDefaults.standard.string(forKey: key) else { return nil }
        return URL(string: s)
    }

    static func save(_ url: URL) {
        shared?.set(url.absoluteString, forKey: key)
        UserDefaults.standard.set(url.absoluteString, forKey: key)
    }
}
