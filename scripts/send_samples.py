"""Send the sample messages to the workflow and print how each one was triaged.

    python scripts/send_samples.py                                   # default URL
    python scripts/send_samples.py http://localhost:5678/webhook/inbound-message
    python scripts/send_samples.py --file my_messages.json

Standard library only, so it runs anywhere Python 3.9+ is installed.
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_URL = "http://localhost:5678/webhook/inbound-message"
SAMPLES = Path(__file__).resolve().parent.parent / "samples" / "messages.json"


def post(url: str, payload: dict) -> tuple[int, dict | str]:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=90) as res:
            body = res.read().decode()
            return res.status, json.loads(body) if body else {}
    except urllib.error.HTTPError as err:
        return err.code, err.read().decode()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("url", nargs="?", default=DEFAULT_URL)
    parser.add_argument("--file", default=str(SAMPLES))
    args = parser.parse_args()

    messages = json.loads(Path(args.file).read_text(encoding="utf-8"))
    print(f"Sending {len(messages)} messages to {args.url}\n")
    print(f"{'from':<28}{'category':<13}{'urgency':<9}{'score':>5}  {'route':<15}summary")
    print("-" * 110)
    failures = 0
    for msg in messages:
        started = time.time()
        status, body = post(args.url, msg)
        if status >= 300 or not isinstance(body, dict):
            failures += 1
            print(f"{msg['from_email']:<28}HTTP {status}: {str(body)[:70]}")
            continue
        t = body.get("triage", {})
        print(f"{msg['from_email'][:27]:<28}{t.get('category', '?'):<13}{t.get('urgency', '?'):<9}"
              f"{t.get('lead_score', 0):>5}  {t.get('route', '?'):<15}{str(t.get('summary', ''))[:45]}"
              f"  ({time.time() - started:.1f}s)")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
