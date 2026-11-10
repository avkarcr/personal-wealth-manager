"""Local entry point. Importing this module does not create an app or threads."""
import os
import threading
import webbrowser

from wealth_manager.factory import create_app


def main():
    app = create_app()
    runtime = app.extensions["wealth_manager"]
    port = int(os.environ.get("PORT", 5050))
    browser_timer = None
    try:
        runtime.start()
        if not os.environ.get("NO_BROWSER"):
            browser_timer = threading.Timer(1.0, lambda: webbrowser.open(f"http://127.0.0.1:{port}"))
            browser_timer.daemon = True
            browser_timer.start()
        print(f"\n  Капитал запущен: http://127.0.0.1:{port}\n  Остановить: Ctrl+C\n")
        app.run(host="127.0.0.1", port=port, debug=False, threaded=False, use_reloader=False)
    finally:
        if browser_timer is not None:
            browser_timer.cancel()
            browser_timer.join()
        runtime.close()


if __name__ == "__main__":
    main()
