"""Group what the phone shares into one message per agent.

A share of several items (WhatsApp messages and photos, a handful of screenshots) reaches
/api/drop as one upload per item, one after another. Typed into the agent one by one, each
would be its own turn. Instead every item joins a pending batch for that agent, and the batch
goes out as ONE message, in order:

    📎 shared from phone (3 items) — <note>
    1. "first text"
    2. inbox/IMG_1.jpg
    3. "second text"

When is a batch complete?
    exact      the Shortcut sends i (Repeat Index) and n (Count): item n of n closes it
    fallback   no n (an older Shortcut): nothing new for QUIET seconds closes it
    safety     an incomplete batch (an upload failed) goes out MAXWAIT seconds after its last item

Delivery can be refused (the agent shows a question, or has an unsent draft in its box): the
batch stays and is retried on every tick; after STUCK seconds on_stuck() is called once.
Pure apart from the callables passed in, so it is tested with a fake clock.
"""
import os
import re
import threading
import time


class Batches:
    QUIET, MAXWAIT, STUCK = 5.0, 60.0, 120.0

    def __init__(self, deliver, on_stuck=None, now=time.time):
        self.deliver = deliver          # deliver(session, message) -> True when typed in
        self.on_stuck = on_stuck or (lambda session, count: None)
        self.now = now
        self.lock = threading.Lock()
        self.pending = {}               # session -> [batch, …] oldest first; only the last can be open

    def add(self, session, kind, value, note="", i=None, n=None):
        """kind "text" (value = the text) or "file" (value = path relative to the agent's folder).
        Returns True when the batch it completed was delivered right away."""
        t = self.now()
        with self.lock:
            q = self.pending.setdefault(session, [])
            if not q or q[-1]["closed"]:                # a batch still waiting to be delivered stays as it is
                q.append({"items": [], "note": "", "n": None, "last": t,
                          "blocked": None, "warned": False, "closed": False})
            b = q[-1]
            b["items"].append((i if i is not None else 10**6 + len(b["items"]), kind, value))
            b["note"] = b["note"] or (note or "").strip()
            b["n"] = n or b["n"]
            b["last"] = t
            if b["n"] is not None and len(b["items"]) >= b["n"]:
                b["closed"] = True
            done = b["closed"]
        return self._flush(session) if done else False

    def tick(self):
        """Close batches whose time is up and retry refused ones. Call about once a second."""
        t = self.now()
        due = []
        with self.lock:
            for s, q in self.pending.items():
                b = q[-1]
                if not b["closed"] and ((b["n"] is None and t - b["last"] >= self.QUIET) or t - b["last"] >= self.MAXWAIT):
                    b["closed"] = True
                if q[0]["closed"]:
                    due.append(s)
        for s in due:
            self._flush(s)

    def _flush(self, session):
        """Deliver this agent's closed batches, oldest first; stop at the first refusal."""
        delivered = False
        while True:
            with self.lock:
                q = self.pending.get(session) or []
                if not q or not q[0]["closed"]:
                    if not q:
                        self.pending.pop(session, None)
                    return delivered
                b = q[0]
                msg = self.message(b)
            try:
                ok = bool(self.deliver(session, msg))
            except Exception:
                ok = False
            stuck = 0
            with self.lock:
                if ok:
                    q.pop(0)
                    delivered = True
                    continue
                b["blocked"] = b["blocked"] or self.now()
                if not b["warned"] and self.now() - b["blocked"] >= self.STUCK:
                    b["warned"] = True
                    stuck = len(b["items"])
            if stuck:
                self.on_stuck(session, stuck)
            return delivered

    @staticmethod
    def message(b):
        items = [(k, v) for _, k, v in sorted(b["items"], key=lambda x: x[0])]
        note = b["note"]
        if len(items) == 1:                       # a single share reads exactly as it always did
            k, v = items[0]
            if k == "text":
                return f"📝 shared from phone: {v}" + (f"  — {note}" if note else "")
            return f"📎 shared from phone: {v}" + (f"  — {note}" if note else "  — have a look")
        lines = [f"📎 shared from phone ({len(items)} items)" + (f" — {note}" if note else "")]
        for j, (k, v) in enumerate(items, 1):
            if k == "text":
                body = v.replace("\r\n", "\n").strip().replace("\n", "\n   ")
                lines.append(f'{j}. "{body}"')
            else:
                lines.append(f"{j}. {v}")
        return "\n".join(lines)


def shared_text(name, body):
    """A WhatsApp/Notes text arrives as a small plain-text FILE named after its own first words.
    That is a message, not a document: pass it on as text. A real .txt with its own name stays a file."""
    if len(body) > 16384 or b"\x00" in body:
        return False
    try:
        text = body.decode("utf-8").strip()
    except UnicodeDecodeError:
        return False
    if not text:
        return False
    stem, ext = os.path.splitext(name)
    real_ext = bool(re.fullmatch(r"\.[A-Za-z0-9]{1,5}", ext))
    if real_ext and ext.lower() != ".txt":
        return False
    head = re.sub(r"\s+", " ", text)[:24].lower()
    named_after_it = re.sub(r"\s+", " ", (stem if real_ext else name)).lower().startswith(head[:min(len(head), 16)])
    return named_after_it or not name
