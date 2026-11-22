"""MA Touch Scanner - find stocks that recently touched or came close to key moving averages.

Run:            python main.py
Demo (offline): python main.py --demo
Self-test:      python main.py --selftest   (scans demo data, then exits 0 on success)
"""
from __future__ import annotations

import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from app_window import MainWindow, make_app_icon


def main() -> int:
    selftest = "--selftest" in sys.argv
    app = QApplication(sys.argv)
    app.setApplicationName("MATouchScanner" + ("-selftest" if selftest else ""))
    app.setOrganizationName("MATouchScanner")
    app.setWindowIcon(make_app_icon())
    window = MainWindow(demo=selftest or "--demo" in sys.argv)
    window.show()

    if selftest:
        state = {"ticks": 0}

        def check():
            state["ticks"] += 1
            if window.thread is None and window.model.rowCount() > 0:
                import yfinance  # noqa: F401  (make sure the live-data library is bundled)
                print(f"selftest ok: {window.model.rowCount()} rows", flush=True)
                window.close()
                app.exit(0)
            elif state["ticks"] > 300:
                print("selftest failed: no results", flush=True)
                app.exit(1)

        timer = QTimer()
        timer.timeout.connect(check)
        timer.start(100)
        QTimer.singleShot(300, window.start_scan)

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
