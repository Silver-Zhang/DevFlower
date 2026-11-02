"""Launcher entry tests.

Linux behaviour is exercised for real. The macOS and Windows entry files are
only asserted as static text here: this test suite runs on Linux and has not
executed those launchers on their native systems.
"""
import io
import json
import os
import stat
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'tools'), str(ROOT/'tests'), str(ROOT)]

import devflower_launch as launch  # noqa: E402
import devflower_start as start  # noqa: E402
from orchestration.store import process_identity  # noqa: E402


TOKEN = 'launcher-test-token'


class StatusHandler(BaseHTTPRequestHandler):
    channel = 'Development'

    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path != '/api/status' or self.headers.get('Authorization') != 'Bearer '+TOKEN:
            self.send_response(403)
            self.end_headers()
            return
        body = json.dumps({'release': {'channel': self.channel}}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def write_private(path, data):
    path.write_bytes(data)
    os.chmod(path, 0o600)


class RunningStateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name)/'state'
        (self.state/'runtime').mkdir(parents=True)
        os.chmod(self.state, 0o700)
        os.chmod(self.state/'runtime', 0o700)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), StatusHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def record(self, channel='Development', port=None):
        write_private(self.state/'runtime'/'workbench-capability', TOKEN.encode())
        write_private(self.state/'runtime'/'service.json', json.dumps(dict(
            kind=start.KIND, host='127.0.0.1', port=port or self.server.server_port,
            process=process_identity(os.getpid()))).encode())
        StatusHandler.channel = channel

    def test_recorded_service_is_reused_and_url_printed(self):
        self.record()
        url = launch.running_url(self.state, start)
        self.assertEqual(url, 'http://127.0.0.1:'+str(self.server.server_port)+'/#'+TOKEN)
        output = io.StringIO()
        with redirect_stdout(output):
            code = launch.main(['--state-dir', str(self.state), '--print-url'])
        self.assertEqual(code, 0)
        self.assertEqual(output.getvalue().strip(), url)

    def test_non_development_service_is_not_reused(self):
        self.record(channel='Stable')
        self.assertIsNone(launch.running_url(self.state, start))

    def test_missing_record_means_not_running(self):
        self.assertIsNone(launch.running_url(self.state, start))


class UnsupportedPlatformTests(unittest.TestCase):
    def test_launcher_refuses_windows_before_touching_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)/'state'
            err = io.StringIO()
            with patch.object(start.os, 'name', 'nt'), redirect_stderr(err):
                code = launch.main(['--print-url', '--state-dir', str(state)])
            self.assertEqual(code, 1)
            self.assertIn('Windows 未适配', err.getvalue())
            self.assertFalse(state.exists())


class ParserTests(unittest.TestCase):
    def test_defaults_and_flags(self):
        args = launch.parser().parse_args([])
        self.assertEqual(args.port, 0)
        self.assertIsNone(args.state_dir)
        self.assertFalse(args.no_browser)
        self.assertFalse(args.print_url)
        args = launch.parser().parse_args(['--port', '8911', '--state-dir', '/tmp/x',
                                           '--no-browser', '--print-url'])
        self.assertEqual((args.port, args.state_dir, args.no_browser, args.print_url),
                         (8911, '/tmp/x', True, True))


class EntryFileTests(unittest.TestCase):
    def test_entries_delegate_to_the_python_launcher(self):
        entries = {
            'launcher/linux/DevFlower.desktop': 'DevFlower.desktop',
            'launcher/linux/install.sh': 'install.sh',
            'launcher/macos/DevFlower.command': 'DevFlower.command',
            'launcher/macos/install.command': 'install.command',
            'launcher/windows/DevFlower.cmd': 'DevFlower.cmd',
        }
        for relative in entries:
            path = ROOT/relative
            self.assertTrue(path.is_file(), relative)
            self.assertIn('devflower_launch.py', path.read_text(), relative)

    def test_windows_installer_refuses_instead_of_installing(self):
        # Windows is unsupported (#141); a shortcut install would fake support.
        text = (ROOT/'launcher/windows/install.ps1').read_text()
        self.assertNotIn('CreateShortcut', text)
        self.assertIn('does not support Windows', text)

    def test_desktop_entry_fields(self):
        text = (ROOT/'launcher/linux/DevFlower.desktop').read_text()
        fields = dict(line.split('=', 1) for line in text.splitlines() if '=' in line)
        self.assertEqual(fields['Type'], 'Application')
        self.assertEqual(fields['Name'], 'DevFlower')
        self.assertIn('devflower_launch.py', fields['Exec'])
        self.assertTrue(fields['Icon'].endswith('launcher/assets/devflower.svg'))

    def test_icon_and_relative_paths(self):
        self.assertTrue((ROOT/'launcher/assets/devflower.svg').is_file())
        for relative in ('launcher/linux/install.sh', 'launcher/macos/DevFlower.command'):
            text = (ROOT/relative).read_text()
            self.assertNotIn('/home/silver', text)
            self.assertIn('dirname', text)

    def test_linux_install_script_is_executable_after_chmod(self):
        path = ROOT/'launcher/linux/install.sh'
        self.assertTrue(stat.S_IMODE(path.stat().st_mode) & stat.S_IXUSR, 'install.sh should be executable')


if __name__ == '__main__':
    unittest.main()
