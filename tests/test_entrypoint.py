import unittest
from unittest.mock import Mock, patch

import app as entrypoint


class EntrypointTests(unittest.TestCase):
    def application(self):
        app = Mock()
        runtime = Mock()
        app.extensions = {'wealth_manager': runtime}
        return app, runtime

    def test_main_starts_and_closes_runtime_without_browser(self):
        app, runtime = self.application()
        with patch.object(entrypoint, 'create_app', return_value=app), \
             patch.dict('os.environ', {'NO_BROWSER': '1', 'PORT': '5051'}), \
             patch.object(entrypoint.threading, 'Timer') as timer:
            entrypoint.main()
        runtime.start.assert_called_once()
        runtime.close.assert_called_once()
        timer.assert_not_called()
        app.run.assert_called_once_with(host='127.0.0.1', port=5051, debug=False, threaded=False, use_reloader=False)

    def test_server_failure_cancels_browser_timer_and_closes_runtime(self):
        app, runtime = self.application()
        app.run.side_effect = OSError('port busy')
        with patch.object(entrypoint, 'create_app', return_value=app), \
             patch.dict('os.environ', {'NO_BROWSER': '', 'PORT': '5050'}), \
             patch.object(entrypoint.threading, 'Timer') as timer, \
             patch.object(entrypoint.webbrowser, 'open') as browser:
            with self.assertRaises(OSError): entrypoint.main()
            timer.call_args.args[1]()
            browser.assert_called_once_with('http://127.0.0.1:5050')
            timer.return_value.start.assert_called_once()
            timer.return_value.cancel.assert_called_once()
            timer.return_value.join.assert_called_once()
        runtime.close.assert_called_once()

    def test_runtime_start_failure_still_attempts_cleanup(self):
        app, runtime = self.application()
        runtime.start.side_effect = OSError('thread unavailable')
        with patch.object(entrypoint, 'create_app', return_value=app):
            with self.assertRaises(OSError): entrypoint.main()
        app.run.assert_not_called()
        runtime.close.assert_called_once()
