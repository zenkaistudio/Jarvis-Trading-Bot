"""
Jarvis standalone runner — starts monitoring + Telegram chat loop directly.
Use this when the MCP server isn't needed.
Run: python3 run_jarvis.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from tradelocker_client import TradeLockerClient
from monitor import JarvisMonitor


if __name__ == "__main__":
    print("[Jarvis] Connecting to TradeLocker...")
    client = TradeLockerClient()
    print("[Jarvis] Connection established.")

    jarvis = JarvisMonitor(client)
    result = jarvis.start()
    print(f"[Jarvis] {result}")
    print("[Jarvis] Running. Ctrl+C to stop.\n")

    try:
        # Keep alive — monitoring + Telegram loop run in daemon threads
        import time
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        jarvis.stop()
        print("\n[Jarvis] Stopped.")
