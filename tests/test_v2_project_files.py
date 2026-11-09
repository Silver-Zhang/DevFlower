"""V2 project file HTTP and filesystem boundaries using disposable data."""
from contextlib import contextmanager
from http.client import HTTPConnection
import os
from pathlib import Path
import socket
from unittest import TestCase
from unittest.mock import patch
from urllib.parse import urlencode

import test_v2_drafts as fixtures
from v2 import project_files


class ProjectFilesTests(TestCase):
    setUp = fixtures.HTTPTests.setUp
    request = fixtures.HTTPTests.request

    def read(self, path='', *, workspace=None, preview=False, **request):
        endpoint = 'project-file' if preview else 'project-files'
        query = urlencode({'workspace': workspace or self.wa, 'path': path})
        return self.request(f'/api/v2/{endpoint}?{query}', **request)

    def test_http_scope_auth_and_explicit_other_registered_workspace(self):
        (self.a / 'readme.txt').write_text('A 中文 <b>literal</b>', encoding='utf-8')
        (self.b / 'only-b.txt').write_text('B value', encoding='utf-8')
        self.assertEqual(self.read('readme.txt', preview=True), (200, {
            'workspace': self.wa, 'path': 'readme.txt', 'text': 'A 中文 <b>literal</b>', 'readonly': True}))
        self.assertEqual(self.read('only-b.txt', workspace=self.wb, preview=True)[1]['text'], 'B value')
        for status, body in (self.read('only-b.txt', preview=True), self.read(workspace='1' * 64)):
            self.assertEqual(status, 400)
            for private in (str(self.root), 'only-b.txt', 'B value'):
                self.assertNotIn(private, str(body))
        self.assertEqual(self.read(token=False)[0], 401)
        self.assertEqual(self.read(origin='https://foreign.invalid')[0], 403)
        connection = HTTPConnection('127.0.0.1', self.server.server_port)
        try:
            connection.request('GET', f'/api/v2/project-files?workspace={self.wa}',
                               headers={'Authorization': 'Bearer ' + self.token, 'Host': 'foreign.invalid'})
            self.assertEqual(connection.getresponse().status, 403)
        finally:
            connection.close()
        self.assertEqual(self.request(f'/api/v2/project-files?workspace={self.wa}&home=arbitrary')[0], 400)
        self.assertEqual(self.request(f'/api/v2/project-files?workspace={self.wa}&path=&path=again')[0], 400)
        self.assertEqual(self.request('/api/v2/project-file', {'workspace': self.wa, 'path': 'readme.txt'})[0], 404)

    def test_malformed_paths_are_400_before_workspace_lookup(self):
        with patch.object(self.store, 'workspace', side_effect=AssertionError('filesystem lookup')) as lookup:
            for value in ('/etc/passwd', '../B', 'folder/../B', 'folder\\file', 'x' * 4097, 'a\0b'):
                with self.subTest(value=value[:80]):
                    self.assertEqual(self.read(value, preview=True)[0], 400)
            self.assertEqual(self.request(f'/api/v2/project-files?workspace={self.wa}&path=%2e%2e%2fB')[0], 400)
            self.assertEqual(self.request(f'/api/v2/project-files?workspace={self.wa}&path=%FF')[0], 400)
            lookup.assert_not_called()

    def test_listing_and_preview_refuse_special_files_and_links(self):
        outside = self.b / 'outside.txt'
        outside.write_text('foreign content', encoding='utf-8')
        (self.a / 'leaf').symlink_to(outside)
        (self.a / 'parent').symlink_to(self.b, target_is_directory=True)
        (self.a / 'directory').mkdir()
        (self.a / 'plain.txt').write_text('positive', encoding='utf-8')
        os.link(outside, self.a / 'hard')
        os.mkfifo(self.a / 'pipe')
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as local_socket:
            local_socket.bind(str(self.a / 'socket'))
            self.assertEqual([entry['name'] for entry in self.read()[1]['entries']], ['directory', 'plain.txt'])
            for name in ('leaf', 'parent/outside.txt', 'hard', 'pipe', 'socket', 'directory'):
                with self.subTest(name=name):
                    self.assertIn(self.read(name, preview=True)[0], (400, 409))
        self.assertEqual(self.read('plain.txt', preview=True)[1]['text'], 'positive')

    def test_mount_identity_fails_closed_even_when_device_is_the_same(self):
        (self.a / 'foreign').mkdir()
        (self.a / 'foreign' / 'text.txt').write_text('foreign mount', encoding='utf-8')
        normal_mount = project_files.mount_id

        def different_mount(fd):
            path = Path(os.readlink(f'/proc/self/fd/{fd}'))
            return normal_mount(fd) + int(path.is_relative_to(self.a / 'foreign'))

        with patch.object(project_files, 'mount_id', side_effect=different_mount):
            self.assertEqual(self.read()[1]['entries'], [])
            self.assertEqual(self.read('foreign')[0], 409)
            self.assertEqual(self.read('foreign/text.txt', preview=True)[0], 409)
        self.assertEqual(self.read('foreign/text.txt', preview=True)[0], 200)

    def test_replaced_root_is_refused_before_and_after_pinning(self):
        (self.a / 'old.txt').write_text('old workspace content', encoding='utf-8')
        original = project_files.opened
        moved = self.spaces / 'moved'
        calls = 0

        @contextmanager
        def replace_before_first_rule(path, **kwargs):
            nonlocal calls
            with original(path, **kwargs) as fd:
                calls += 1
                if calls == 1:
                    self.a.rename(moved)
                    self.a.mkdir()
                yield fd

        with patch.object(project_files, 'opened', side_effect=replace_before_first_rule):
            code, body = self.read('old.txt', preview=True)
        self.assertEqual((code, calls), (409, 2))
        self.assertNotIn('old workspace content', str(body))
        self.assertEqual(self.read()[0], 409)

    def test_preview_limits_redaction_readonly_and_file_change(self):
        self.assertEqual(self.read('', preview=True)[0], 400)
        for name, data, status in (
                ('exact.txt', b'x' * 16000, 200), ('large.txt', b'x' * 16001, 400),
                ('binary.txt', b'a\0b', 400), ('encoding.txt', b'\xff', 400)):
            with self.subTest(name=name):
                (self.a / name).write_bytes(data)
                self.assertEqual(self.read(name, preview=True)[0], status)
        normal = self.a / 'normal.txt'
        normal.write_text('Authorization: Bearer ghp_SYNTHETIC_DO_NOT_DISCLOSE\n<div>read-only</div>', encoding='utf-8')
        normal.chmod(0o444)
        state_before = self.store.db.list('workspace')
        bytes_before = normal.read_bytes()
        mtime_before = normal.stat().st_mtime_ns
        self.a.chmod(0o555)
        try:
            status, body = self.read('normal.txt', preview=True)
        finally:
            self.a.chmod(0o755)
        self.assertEqual(status, 200)
        self.assertIn('[REDACTED]', body['text'])
        self.assertIn('<div>read-only</div>', body['text'])
        self.assertNotIn('DO_NOT_DISCLOSE', body['text'])
        self.assertEqual((normal.read_bytes(), normal.stat().st_mtime_ns), (bytes_before, mtime_before))
        self.assertEqual(self.store.db.list('workspace'), state_before)
        normal.chmod(0)
        try:
            self.assertIn('权限', self.read('normal.txt', preview=True)[1]['error'])
        finally:
            normal.chmod(0o644)
        fopen = os.fdopen

        def change_after_open(fd, *args, **kwargs):
            normal.write_text('changed after opening', encoding='utf-8')
            return fopen(fd, *args, **kwargs)

        with patch.object(project_files.os, 'fdopen', side_effect=change_after_open):
            self.assertEqual(self.read('normal.txt', preview=True)[0], 409)

    def test_ignore_rules_hidden_names_and_protected_configuration(self):
        for name in ('.env', 'secret.txt', 'a\u202eb.txt', 'a\u200fb.txt', 'ghp_SYNTHETIC1234.txt',
                     'ignored.txt', 'good.txt'):
            (self.a / name).write_text('safe fixture text', encoding='utf-8')
        (self.a / '.git').mkdir()
        (self.a / '.claude').mkdir()
        (self.a / '.claude' / 'auth.json').write_text('synthetic credential sentinel', encoding='utf-8')
        (self.a / 'private-provider').mkdir()
        (self.a / 'private-provider' / 'normal.txt').write_text('credential fixture sentinel', encoding='utf-8')
        rule = self.a / '.gitignore'
        rule.write_text('ignored.txt\n!ignored.txt\n!secret.txt\n', encoding='utf-8')
        with (patch.dict(os.environ, {'CODEX_HOME': str(self.a / 'private-provider')}),
              patch.object(project_files.os, 'open', wraps=os.open) as opened_file):
            status, listing = self.read()
            self.assertEqual(status, 200)
            self.assertEqual(listing['entries'], [{'name': 'good.txt', 'path': 'good.txt', 'directory': False}])
            self.assertEqual(self.read('private-provider/normal.txt', preview=True)[0], 409)
            self.assertNotIn('auth.json', str(opened_file.call_args_list))
        for name in ('.env', 'secret.txt', 'ignored.txt', 'a\u202eb.txt', '.claude'):
            self.assertEqual(self.read(name, preview=True)[0], 409)
        for invalid_rules in ('x' * 16001, ''.join(f'rule-{n}\n' for n in range(257))):
            rule.write_text(invalid_rules, encoding='utf-8')
            self.assertEqual(self.read()[0], 409)
        rule.write_text('before\n', encoding='utf-8')
        real_list = project_files.list_files

        def replace_rule(*args):
            result = real_list(*args)
            rule.write_text('after\n', encoding='utf-8')
            return result

        with patch.object(project_files, 'list_files', side_effect=replace_rule):
            self.assertEqual(self.read()[0], 409)

    def test_protect_running_source_even_if_registered(self):
        source = Path(project_files.__file__).resolve().parents[2]
        self.store.roots.append(source)
        workspace = self.store.open_workspace(str(source))['id']
        self.assertEqual(self.read(workspace=workspace)[0], 409)
        self.assertEqual(self.read('README.md', workspace=workspace, preview=True)[0], 409)
        self.store.roots.append(self.store.state)
        state_id = self.store.open_workspace(str(self.store.state))['id']
        self.assertEqual(self.read(workspace=state_id)[0], 409)

    def test_directory_results_are_lazy_empty_and_bounded(self):
        folder = self.a / '目录'
        folder.mkdir()
        (folder / 'empty').mkdir()
        self.assertEqual(self.read('目录/empty')[1]['entries'], [])
        self.assertEqual(self.read()[1]['entries'], [{'name': '目录', 'path': '目录', 'directory': True}])
        for i in range(520):
            (folder / f'item-{i:03}.txt').touch()
        status, listing = self.read('目录')
        self.assertEqual((status, listing['truncated'], len(listing['entries'])), (200, True, 512))
        self.assertTrue(all(entry['path'].startswith('目录/') for entry in listing['entries']))
