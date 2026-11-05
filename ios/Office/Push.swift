import UIKit
import UserNotifications

/// Notifications from the Office: an agent needs you, is done, or is gone. The phone's push token goes
/// to the Office (POST /api/push), which sends through Apple; a tap opens that agent's terminal.
enum Push {
    static let openSession = Notification.Name("OfficeOpenSession")
    /// a tap that arrived before the page was there (the app was closed): opened once it loads
    static var pending: String?
    private static let tokenKey = "pushToken"

    /// Ask once for permission, then register with Apple on every launch (the token can change).
    static func start(_ app: UIApplication) {
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound, .badge]) { granted, _ in
            guard granted else { return }
            DispatchQueue.main.async { app.registerForRemoteNotifications() }
        }
    }

    static func gotToken(_ data: Data) {
        let token = data.map { String(format: "%02x", $0) }.joined()
        UserDefaults.standard.set(token, forKey: tokenKey)
        sendToken()
    }

    /// Tell the Office which phone to notify. Also called when its address changes.
    static func sendToken() {
        guard let token = UserDefaults.standard.string(forKey: tokenKey),
              let base = OfficeAddress.url else { return }
        #if DEBUG
        let env = "sandbox"          // a build run from Xcode talks to Apple's test push service
        #else
        let env = "production"       // TestFlight / App Store
        #endif
        var req = URLRequest(url: base.appendingPathComponent("api/push"))
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try? JSONSerialization.data(withJSONObject: ["token": token, "env": env])
        URLSession.shared.dataTask(with: req).resume()
    }

    /// A notification was tapped: open the agent it is about.
    static func tapped(_ userInfo: [AnyHashable: Any]) {
        guard let session = userInfo["session"] as? String, !session.isEmpty else { return }
        pending = session
        NotificationCenter.default.post(name: openSession, object: session)
    }
}
