"""Real disposable Linux installation and service lifecycle."""
from concurrent.futures import ThreadPoolExecutor
import http.client
import json
import os
from pathlib import Path
import shutil
import socket
import stat
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import unittest
from urllib.parse import urlencode, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))

from orchestration.store import process_identity


@unittest.skipUnless(sys.platform == 'linux' and hasattr(os, 'pidfd_open'), 'requires Linux pidfd')
class LinuxDesktopTests(unittest.TestCase):
    def setUp(self):
        base = Path(self.enterContext(TemporaryDirectory()))
        self.home, self.project = base / 'fixture home %', base / 'disposable project'
        self.home.mkdir(mode=0o700)
        self.project.mkdir()
        self.env = dict(PATH='/usr/bin:/bin', LANG='C.UTF-8', HOME=str(self.home),
                        XDG_CONFIG_HOME=str(base / 'xdg config'), XDG_STATE_HOME=str(base / 'xdg state'),
                        XDG_DATA_HOME=str(base / 'xdg data'), PYTHONDONTWRITEBYTECODE='1')
        self.config = Path(self.env['XDG_CONFIG_HOME']) / 'devflower-v2'
        self.state = Path(self.env['XDG_STATE_HOME']) / 'devflower-v2'
        self.command = self.home / '.local/bin/devflower-v2'
        self.desktop = Path(self.env['XDG_DATA_HOME']) / 'applications/DevFlower-V2.desktop'
        self.installer = ROOT / 'launcher/linux/install-v2.sh'
        self.addCleanup(self.stop)

    def run_command(self, program, *args, expected=0):
        result = subprocess.run([str(program), *map(str, args)], env=self.env, cwd='/',
                                capture_output=True, text=True, timeout=50)
        self.assertEqual(result.returncode, expected, result.stderr)
        return result.stdout.strip() if expected == 0 else result.stderr.strip()

    def install(self, *extra):
        self.run_command(self.installer, '--allow-root', self.project, '--codex-cli', '/nonexistent-codex', *extra)

    def stop(self):
        if self.command.exists():
            subprocess.run([str(self.command), 'stop'], env=self.env, cwd='/', capture_output=True, timeout=20)

    def request(self, url, route, data=None, expected=200):
        address = urlsplit(url)
        connection = http.client.HTTPConnection(address.hostname, address.port, timeout=3)
        try:
            connection.request('GET' if data is None else 'POST', '/api/v2/' + route,
                               None if data is None else json.dumps(data),
                               {'Authorization': 'Bearer ' + address.fragment, 'Content-Type': 'application/json'})
            response = connection.getresponse()
            self.assertEqual(response.status, expected)
            return json.loads(response.read())
        finally:
            connection.close()

    def test_install_concurrent_start_restart_upgrade_and_draft(self):
        v1 = self.desktop.with_name('DevFlower.desktop')
        v1.parent.mkdir(parents=True)
        v1.write_text('existing V1 entry')
        self.install()
        self.assertEqual(v1.read_text(), 'existing V1 entry')
        self.assertIn('Name=DevFlower V2', self.desktop.read_text())
        self.assertIn('%%', self.desktop.read_text())
        validator = shutil.which('desktop-file-validate')
        if validator:
            self.run_command(validator, self.desktop)
        self.assertEqual(stat.S_IMODE(self.config.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((self.config/'desktop.json').stat().st_mode), 0o600)
        self.assertIn('已停止', self.run_command(self.command, 'status'))

        def start():
            return self.run_command(self.command, 'start', '--no-browser')

        with ThreadPoolExecutor(max_workers=2) as pool:
            first, repeat = [future.result() for future in (pool.submit(start), pool.submit(start))]
        self.assertEqual(first, repeat)
        address = urlsplit(first)
        status = self.request(first, 'status')
        self.assertEqual(status['roots'], [str(self.project)])
        self.assertFalse(status['native_history_available'])
        self.assertEqual(status['native_session_writes'], [])
        receipt_path = self.state / 'desktop-service.json'
        receipt = json.loads(receipt_path.read_text())
        self.assertEqual(stat.S_IMODE(receipt_path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.state.stat().st_mode), 0o700)
        service = self.run_command(self.command, 'status')
        self.assertIn(str(receipt['process']['pid']), service)
        self.assertNotIn(address.fragment, service)
        self.assertNotIn(address.fragment, (self.state / 'desktop.log').read_text())
        self.assertNotIn(address.fragment, self.desktop.read_text())
        self.assertIn('停止服务', self.run_command(self.installer, 'upgrade', expected=1))
        original_env = self.env
        foreign_state = self.state.parent.with_name('other state') / 'devflower-v2'
        foreign_state.mkdir(parents=True, mode=0o700)
        foreign_receipt = foreign_state / receipt_path.name
        foreign_receipt.write_bytes(receipt_path.read_bytes())
        foreign_receipt.chmod(0o600)
        self.env = dict(original_env, XDG_STATE_HOME=str(foreign_state.parent))
        try:
            self.assertIn('进程身份', self.run_command(self.command, 'stop', expected=1))
        finally:
            self.env = original_env
        self.assertEqual(self.request(first, 'status')['roots'], [str(self.project)])
        if launcher := shutil.which('gio'):
            browser = self.project.with_name('browser-fixture')
            browser.write_text('#!/bin/sh\numask 077\nprintf "%s" "$1" > "$BROWSER_RECEIPT"\n')
            browser.chmod(0o700)
            opened = browser.with_name('browser-receipt')
            self.env = dict(original_env, BROWSER=str(browser), BROWSER_RECEIPT=str(opened))
            try:
                self.run_command(launcher, 'launch', self.desktop)
                deadline = time.monotonic() + 8
                while not opened.exists() and time.monotonic() < deadline:
                    time.sleep(.05)
                self.assertTrue(opened.exists(), 'installed desktop entry did not launch the workbench')
                self.assertEqual(opened.read_text(), first)
            finally:
                self.env = original_env

        workspace = self.request(first, 'workspaces/open', {'path':str(self.project)})['id']
        document = dict(version=1,name='Survives restart',nodes=[],edges=[])
        draft = self.request(first, 'drafts/save', dict(workspace=workspace,id=None,expected=None,document=document))
        self.run_command(self.command, 'stop')
        self.assertIn('已停止', self.run_command(self.command, 'status'))
        original_config = (self.config/'desktop.json').read_bytes()
        self.run_command(self.installer)
        upgrade = self.project.with_name('new source checkout')
        for folder in ('tools', 'launcher'):
            shutil.copytree(ROOT/folder, upgrade/folder, ignore=shutil.ignore_patterns('__pycache__'))
        self.run_command(upgrade/'launcher/linux/install-v2.sh', 'upgrade')
        self.assertEqual((self.config/'desktop.json').read_bytes(), original_config)
        second = start()
        self.assertEqual(json.loads(receipt_path.read_text())['argv'][2], str(upgrade/'tools/devflower_v2_launch.py'))
        self.assertEqual(address.netloc, urlsplit(second).netloc)
        self.assertNotEqual(address.fragment, urlsplit(second).fragment)
        self.request(first, 'status', expected=401)
        self.assertEqual(self.request(second,'status')['environment']['id'], status['environment']['id'])
        saved = self.request(second, 'draft?' + urlencode(dict(workspace=workspace,id=draft['id'])))
        self.assertEqual(saved['graph'], document)

    def test_native_opt_in_and_root_replacement_are_explicit(self):
        self.assertIn('首次安装', self.run_command(self.installer, expected=1))
        self.assertIn('项目根目录', self.run_command(self.installer, '--allow-root', self.home, expected=1))
        alias = self.project.with_name('linked project')
        alias.symlink_to(self.project, target_is_directory=True)
        self.run_command(self.installer, '--allow-root', alias, expected=1)
        self.assertIn('原生历史', self.run_command(self.installer, '--allow-root', self.project, '--managed-codex', expected=1))
        self.assertIn('受管 Codex', self.run_command(self.installer, '--allow-root', self.project, '--codex-terminal',
                                                   expected=1))
        self.install('--native-history')
        native = self.home / '.claude.json'
        native.write_bytes(b'{"native":"unchanged"}')
        self.project.rename(self.project.with_name('old project'))
        self.project.mkdir()
        self.assertIn('身份已改变', self.run_command(self.command, 'start', '--no-browser', expected=1))
        self.run_command(self.command, 'configure', '--allow-root', self.project)
        url = self.run_command(self.command, 'start', '--no-browser')
        self.assertTrue(self.request(url, 'status')['native_history_available'])
        self.assertEqual(json.loads((self.state/'default-native-history.json').read_text()),
                         dict(version=1,discover_defaults=True))
        self.assertEqual(native.read_bytes(), b'{"native":"unchanged"}')
        self.assertFalse((self.state/'default-native-writes.json').exists())
        self.run_command(self.command, 'stop')
        self.run_command(self.command, 'configure', '--no-native-history')
        url = self.run_command(self.command, 'start', '--no-browser')
        self.assertFalse(self.request(url, 'status')['native_history_available'])

    def test_advanced_enrollment_is_forwarded_and_can_be_cleared(self):
        self.install()
        codex = self.home/'.codex'
        codex.mkdir(mode=0o700)
        named = codex/'named.config.toml'
        named.write_text('model = "fixture"\n')
        named.chmod(0o600)
        separate = self.project.with_name('isolated native home')
        separate.mkdir(mode=0o700)
        claude = separate/'.claude'
        claude.mkdir(mode=0o700)
        settings = claude/'settings.json'
        native_settings = b'{"model":"fixture","env":{"ANTHROPIC_API_KEY":"synthetic-private-test-value"}}'
        settings.write_bytes(native_settings)
        settings.chmod(0o600)
        target = self.project.with_name('local target.git')
        target.mkdir(mode=0o700)
        self.run_command(self.command, 'configure', '--native-agent-profile', 'codex:named',
                         '--native-agent-home', f'claude:separate={separate}', '--git-delivery-target', f'local={target}')
        self.assertIn('未能启动', self.run_command(self.command, 'start', '--no-browser', expected=1))
        self.assertNotIn('synthetic-private-test-value', (self.state/'desktop.log').read_text())
        self.run_command('/usr/bin/git', 'init', '--bare', '--quiet', target)
        url = self.run_command(self.command, 'start', '--no-browser')
        profiles = self.request(url, 'cli-providers')['profiles']
        self.assertEqual({(item['provider'],item['id']) for item in profiles},
                         {('codex','named'), ('claude','separate')})
        self.assertNotIn(b'synthetic-private-test-value', (self.config/'desktop.json').read_bytes())
        self.assertEqual(settings.read_bytes(), native_settings)
        self.run_command(self.command, 'stop')
        self.run_command(self.command, 'configure', '--clear-native-agent-profiles',
                         '--clear-native-agent-homes', '--clear-git-delivery-targets')
        target.rename(target.with_name('no longer registered.git'))
        url = self.run_command(self.command, 'start', '--no-browser')
        self.assertEqual(self.request(url, 'cli-providers')['profiles'], [])

    def test_occupied_port_and_unrelated_process_are_not_adopted(self):
        with socket.socket() as other:
            other.bind(('127.0.0.1', 0))
            other.listen()
            self.install('--port', other.getsockname()[1])
            self.assertIn('未能启动', self.run_command(self.command, 'start', '--no-browser', expected=1))
        self.assertIn('已停止', self.run_command(self.command, 'status'))
        process = subprocess.Popen(['/usr/bin/sleep', '30'], start_new_session=True)
        try:
            image = os.stat(f'/proc/{process.pid}/exe')
            row = dict(version=1,port=8000,token='x'*43,process=process_identity(process.pid),
                       image=[image.st_dev,image.st_ino],
                       argv=['/usr/bin/python3','-B',str(ROOT/'tools/devflower_v2_launch.py'),'_serve',
                             str(self.config),str(self.state)])
            path = self.state/'desktop-service.json'
            path.write_text(json.dumps(row))
            path.chmod(0o600)
            self.assertIn('进程身份', self.run_command(self.command, 'stop', expected=1))
            self.assertIsNone(process.poll())
        finally:
            process.terminate()
            process.wait(timeout=5)
