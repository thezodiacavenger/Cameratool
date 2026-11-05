"""CI: hammer the Windows USB (WPD) path from many threads, the way the app's web server does.
A native crash kills this process; faulthandler writes the stack to usb_stress.log first."""
import faulthandler
import gc
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
log = open("usb_stress.log", "w", encoding="utf-8")
faulthandler.enable(file=log, all_threads=True)
from camcheck import usb  # noqa: E402

print("backend:", usb.backend())
results = []


def one(i):
    r = usb.read_camera(lambda name, data: ("0" * 32, name, os.devnull))
    results.append(r)
    if "TypeError" in str(r):
        print(f"::error title=usb stress::marshalling error on call {i}: {r}")


for i in range(40):
    t = threading.Thread(target=one, args=(i,))
    t.start()
    t.join()
    if i % 5 == 0:
        threading.Thread(target=gc.collect).start()  # collect on a thread without COM, like the server
gc.collect()
devices = sorted({d for r in results for d in r.get("devices", [])})
print(f"::notice title=usb stress::40 calls completed without crashing. Devices seen: {devices or 'none'}. "
      f"Last result: {str(results[-1])[:400]}")
sys.exit(1 if any("TypeError" in str(r) for r in results) else 0)
