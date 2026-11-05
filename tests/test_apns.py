"""iPhone notifications (apns.py): the signed provider token, the phones registered, and what a send
does with Apple's answer. A throwaway EC key stands in for the APNs key and curl is faked, so
nothing ever reaches Apple."""
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import apns  # noqa: E402

TOKEN = "ab" * 32


def unb64(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


@unittest.skipUnless(shutil.which("openssl"), "needs openssl")
class Pusher(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.key = os.path.join(self.dir, "AuthKey_TEST.p8")
        subprocess.run(["openssl", "genpkey", "-algorithm", "EC", "-pkeyopt", "ec_paramgen_curve:P-256", "-out", self.key],
                       check=True, capture_output=True)
        self.p = apns.Pusher(self.key, "KEYID12345", "TEAMID1234", "com.example.office", os.path.join(self.dir, "devices.json"))
        apns._JWT.update(token="", at=0.0)

    def tearDown(self):
        shutil.rmtree(self.dir)

    def test_the_provider_token_is_an_es256_jwt_that_verifies_against_the_key(self):
        jwt = self.p._jwt()
        head, body, sig = jwt.split(".")
        self.assertEqual(json.loads(unb64(head)), {"alg": "ES256", "kid": "KEYID12345"})
        self.assertEqual(json.loads(unb64(body))["iss"], "TEAMID1234")
        raw = unb64(sig)
        self.assertEqual(len(raw), 64)
        # back to DER and checked by openssl with the public half
        def integer(b):
            b = b.lstrip(b"\0")
            if not b or b[0] & 0x80:
                b = b"\0" + b
            return b"\x02" + bytes([len(b)]) + b
        seq = integer(raw[:32]) + integer(raw[32:])
        der = b"\x30" + bytes([len(seq)]) + seq
        pub, sigf = os.path.join(self.dir, "pub.pem"), os.path.join(self.dir, "sig.der")
        subprocess.run(["openssl", "pkey", "-in", self.key, "-pubout", "-out", pub], check=True, capture_output=True)
        with open(sigf, "wb") as f:
            f.write(der)
        r = subprocess.run(["openssl", "dgst", "-sha256", "-verify", pub, "-signature", sigf],
                           input=f"{head}.{body}".encode(), capture_output=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.p._jwt(), jwt)          # reused, not re-signed for every push

    def test_only_a_hex_token_registers(self):
        self.assertTrue(self.p.register(TOKEN.upper(), "sandbox"))
        self.assertFalse(self.p.register("short", "production"))
        self.assertEqual(list(self.p.devices()), [TOKEN])
        self.assertEqual(self.p.devices()[TOKEN]["env"], "sandbox")

    def _send(self, code, reason=""):
        sent = []
        real = subprocess.run

        def fake(cmd, **kw):
            if cmd[0] != "curl":
                return real(cmd, **kw)
            sent.append((cmd, json.loads(kw["input"])))
            out = (json.dumps({"reason": reason}) if reason else "") + "\n" + str(code)
            return subprocess.CompletedProcess(cmd, 0, out.encode(), b"")
        with mock.patch.object(apns.subprocess, "run", fake), mock.patch.object(apns.sys, "stderr"):
            self.p.send("Zed", "needs you: ship it?", "zed-alpha")
        return sent

    def test_a_send_goes_to_the_phones_apple_service_and_carries_the_session(self):
        self.p.register(TOKEN, "sandbox")
        (cmd, payload), = self._send(200)
        self.assertEqual(cmd[-1], f"https://api.sandbox.push.apple.com/3/device/{TOKEN}")
        self.assertIn("apns-topic: com.example.office", cmd)
        self.assertEqual(payload["session"], "zed-alpha")
        self.assertEqual(payload["aps"]["alert"], {"title": "Zed", "body": "needs you: ship it?"})
        self.assertIn(TOKEN, self.p.devices())

    def test_a_phone_apple_no_longer_knows_is_forgotten(self):
        self.p.register(TOKEN, "production")
        self._send(410, "Unregistered")
        self.assertEqual(self.p.devices(), {})

    def test_nothing_is_sent_without_the_key(self):
        p = apns.Pusher("", "", "", "com.example.office", os.path.join(self.dir, "d.json"))
        self.assertFalse(p.ready)
        with mock.patch.object(apns.subprocess, "run") as run:
            p.send("x", "y", "z")
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
