"""Notifications on the iPhone app (ios/), through Apple's push service (APNs).

The app registers its device token with the Office (POST /api/push); the server sends each
notification straight to Apple, signed with your APNs auth key (.p8). Standard library only:
`openssl` signs the token, `curl` speaks the HTTP/2 that APNs requires.

    OFFICE_APNS_KEY      path to the AuthKey_XXXXXXXXXX.p8 from developer.apple.com (Keys → APNs)
    OFFICE_APNS_KEY_ID   its 10-character key id
    OFFICE_APNS_TEAM_ID  your 10-character team id
    OFFICE_APNS_TOPIC    the app's bundle id (com.pyinv.office in ios/project.yml)
"""
import base64
import json
import os
import subprocess
import sys
import threading
import time

_JWT = {"at": 0.0, "token": ""}
_LOCK = threading.Lock()
HOSTS = {"production": "https://api.push.apple.com", "sandbox": "https://api.sandbox.push.apple.com"}


def _b64(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _der_to_raw(der):
    """openssl writes an ECDSA signature as DER (SEQUENCE of two INTEGERs); a JWT wants r||s, 32 bytes each."""
    i = 2 if der[1] < 0x80 else 2 + (der[1] & 0x7F)
    out = b""
    for _ in range(2):
        assert der[i] == 0x02
        n = der[i + 1]
        v = der[i + 2:i + 2 + n]
        out += v.lstrip(b"\0").rjust(32, b"\0")
        i += 2 + n
    return out


class Pusher:
    def __init__(self, key_path, key_id, team_id, topic, devices_path):
        self.key_path, self.key_id, self.team_id, self.topic = key_path, key_id, team_id, topic
        self.devices_path = devices_path

    @property
    def ready(self):
        return bool(self.key_path and self.key_id and self.team_id and self.topic and os.path.isfile(self.key_path))

    # ── devices: every phone that opened the app, {token: {"env": "sandbox"|"production", "at": ts}} ──
    def devices(self):
        try:
            with open(self.devices_path) as f:
                return json.load(f)
        except Exception:
            return {}

    def _save(self, d):
        tmp = self.devices_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(d, f, indent=1)
        os.replace(tmp, self.devices_path)

    def register(self, token, env):
        token = "".join(c for c in str(token) if c in "0123456789abcdefABCDEF").lower()
        if not 32 <= len(token) <= 200:
            return False
        env = "sandbox" if env == "sandbox" else "production"
        with _LOCK:
            d = self.devices()
            d[token] = {"env": env, "at": int(time.time())}
            self._save(d)
        return True

    def _forget(self, token):
        with _LOCK:
            d = self.devices()
            if d.pop(token, None) is not None:
                self._save(d)

    # ── the provider token: an ES256 JWT, reused for 40 minutes (Apple wants 20 to 60) ──
    def _jwt(self):
        with _LOCK:
            if _JWT["token"] and time.time() - _JWT["at"] < 40 * 60:
                return _JWT["token"]
            head = _b64(json.dumps({"alg": "ES256", "kid": self.key_id}).encode())
            body = _b64(json.dumps({"iss": self.team_id, "iat": int(time.time())}).encode())
            msg = f"{head}.{body}".encode()
            der = subprocess.run(["openssl", "dgst", "-sha256", "-sign", self.key_path], input=msg,
                                 capture_output=True, check=True, timeout=10).stdout
            _JWT.update(token=f"{head}.{body}.{_b64(_der_to_raw(der))}", at=time.time())
            return _JWT["token"]

    def send(self, title, body, session="", subtitle=""):
        """One notification to every registered phone; a tap opens `session`'s terminal (the floor if empty)."""
        if not self.ready:
            return
        alert = {"title": title, "body": body[:900]}
        if subtitle:
            alert["subtitle"] = subtitle
        payload = json.dumps({"aps": {"alert": alert, "sound": "default", "thread-id": session or "office"},
                              "session": session})
        try:
            jwt = self._jwt()
        except Exception as e:
            sys.stderr.write(f"office/apns: can't sign with the key: {e}\n")
            return
        for token, d in self.devices().items():
            url = f"{HOSTS.get(d.get('env'), HOSTS['production'])}/3/device/{token}"
            try:
                r = subprocess.run(["curl", "-sS", "--http2", "-m", "10", "-o", "-", "-w", "\n%{http_code}",
                                    "-H", f"authorization: bearer {jwt}", "-H", f"apns-topic: {self.topic}",
                                    "-H", "apns-push-type: alert", "-H", "apns-priority: 10",
                                    "--data-binary", "@-", url],
                                   input=payload.encode(), capture_output=True, timeout=15)
                out = r.stdout.decode(errors="replace").rsplit("\n", 1)
                code, said = (out[-1].strip(), out[0]) if len(out) == 2 else ("", r.stderr.decode(errors="replace"))
            except Exception as e:
                sys.stderr.write(f"office/apns: {e}\n")
                continue
            if code == "200":
                continue
            reason = ""
            try:
                reason = json.loads(said).get("reason", "")
            except Exception:
                pass
            if code == "410" or reason in ("BadDeviceToken", "Unregistered", "DeviceTokenNotForTopic"):
                self._forget(token)                 # the app was deleted, or this token is from another build
            elif code == "403" and reason in ("ExpiredProviderToken", "InvalidProviderToken"):
                with _LOCK:
                    _JWT["token"] = ""
            sys.stderr.write(f"office/apns: {code or 'no answer'} {reason or said.strip()[:200]}\n")
