"""Real loopback, synthetic wire pipes, and private Copilot fixture identity."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
import fcntl
import hashlib
from http.client import HTTPConnection
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Event, Thread
import time
from unittest import TestCase
from unittest.mock import patch

from v2.copilot_catalog import CopilotCatalog
from v2.copilot_profile import IdentityChanged, RegisteredFile
from v2.copilot_transport import CatalogFault, _stop, query_catalog
from v2.server import create_server
from v2.store import DraftStore


PATH = '/api/v2/provider-catalog/copilot'
PROFILE = 'a' * 20
LOGIN = 'synthetic-account'
TOKEN = 'synthetic-private-token-0123'
ELF_HEADER = b'\x7fELF\x02\x01\x01' + b'\x00' * 9 + b'\x02\x00\x3e\x00'
MODELS = [{'id': 'auto', 'name': 'Auto'}, {'id': 'vendor/model:1'}]
FIELDS = {'provider', 'profile_id', 'source', 'availability', 'auth_status', 'models',
          'observed_at', 'fresh', 'stale', 'effort_source', 'execution_available',
          'inference_verified', 'code', 'error'}

WIRE_FIXTURE = r"""
import json, sys, time
case, trace = sys.argv[1:]
def emit(value):
    raw = json.dumps(value, separators=(',', ':')).encode() if not isinstance(value, bytes) else value
    sys.stdout.buffer.write(f'Content-Length: {len(raw)}\r\n\r\n'.encode() + raw)
    sys.stdout.buffer.flush()

def receive(method):
    first = sys.stdin.buffer.readline(128)
    if not first:
        raise EOFError()
    assert first.startswith(b'Content-Length: ') and first.endswith(b'\r\n')
    assert sys.stdin.buffer.readline() == b'\r\n'
    value = json.loads(sys.stdin.buffer.read(int(first.split(b':')[1])))
    assert set(value) == {'jsonrpc','id','method','params'} and value['jsonrpc'] == '2.0'
    assert value['method'] == method, value['method']
    with open(trace, 'a') as file:
        file.write(method+'\n')
    return value

def result(request, value):
    emit(dict(jsonrpc='2.0', id=request['id'], result=value))

def run():
    request = receive('connect')
    assert request['params']['supportedTaskKinds'] == []
    if case == 'hang':
        time.sleep(30)
        return
    if case == 'callback':
        emit(dict(jsonrpc='2.0', id=1, method='session.create', params={}))
        return
    if case == 'duplicate':
        emit(b'{"jsonrpc":"2.0","id":1,"id":1,"result":{"protocolVersion":3}}')
        return
    if case == 'oversize':
        sys.stdout.buffer.write(b'Content-Length: 262145\r\n\r\n')
        sys.stdout.buffer.flush()
        return
    if case == 'header':
        sys.stdout.buffer.write(b'Content-Length: 1\n\n'+b'x'*128)
        sys.stdout.buffer.flush()
        return
    if case == 'wrong-id':
        emit(dict(jsonrpc='2.0', id=True, result={'protocolVersion':3}))
        return
    connect = {'protocolVersion':2 if case == 'version' else 3}
    if case == 'cumulative':
        connect['extra'] = 'x' * 240000
    result(request, connect)
    request = receive('auth.getStatus')
    assert request['params'] == {}
    auth = dict(isAuthenticated=True, login='SYNTHETIC-ACCOUNT', host='https://github.com/')
    if case == 'no-auth':
        auth['isAuthenticated'] = False
    if case == 'bad-auth':
        auth['isAuthenticated'] = 1
    if case == 'wrong-account':
        auth['login'] = 'another-account'
    if case == 'wrong-host':
        auth['host'] = 'https://github.com/other'
    if case == 'no-host':
        del auth['host']
    if case == 'cumulative':
        auth['extra'] = 'x' * 240000
    result(request, auth)
    request = receive('models.list')
    assert request['params'] == {}
    rows = [
        {'id':'auto','name':'Auto','ignored':'synthetic-private-token-0123'},
        {'id':'vendor/model:1','name':'/etc/passwd'},
    ]
    if case == 'empty':
        rows = []
    if case == 'duplicate-id':
        rows.append(rows[0])
    if case == 'wide':
        rows = [{'id':f'model{i}'} for i in range(201)]
    if case == 'secret-id':
        rows = [{'id':'synthetic-private-token-0123'}]
    if case == 'account-id':
        rows = [{'id':'vendor/synthetic-account'}]
    if case == 'malformed-id':
        rows = [{'id':'file:/etc/passwd'}]
    if case == 'private-names':
        rows = [{'id':'one','name':'synthetic-account'},
                {'id':'two','name':'synthetic-private-token-0123'},
                {'id':'three','name':'bad\u202eformat'}, {'id':'four','name':'<b>literal</b>'}]
    if case == 'cumulative':
        rows = [{'id':'one','extra':'x'*60000}]
    result(request, {'models':rows})
    if case == 'extra-notification':
        emit(dict(jsonrpc='2.0', method='session.event', params={}))
    assert not sys.stdin.buffer.read(1), 'unexpected additional protocol data'

try:
    run()
except EOFError:
    pass
"""


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def make_profile(root, *, gh=None, copilot=None, credential=b'synthetic hosts fixture\n'):
    private = root / 'operator'
    private.mkdir()
    hosts = private / 'hosts.yml'
    hosts.write_bytes(credential)
    hosts.chmod(0o600)
    images = []
    for name, source in (('gh-image', gh), ('copilot-image', copilot)):
        image = private / name
        if source is None:
            image.write_bytes(ELF_HEADER + b'synthetic fixture')
        else:
            shutil.copyfile(source, image)
        image.chmod(0o700)
        images.append(image)
    config = private / 'catalog.json'
    value = dict(version=1, profile_id=PROFILE, github_host='github.com', github_login=LOGIN,
                 github_hosts_file=str(hosts), github_hosts_sha256=sha(credential),
                 gh_executable=str(images[0]), gh_sha256=sha(images[0].read_bytes()),
                 copilot_executable=str(images[1]), copilot_sha256=sha(images[1].read_bytes()))
    config.write_text(json.dumps(value), encoding='utf-8')
    config.chmod(0o600)
    return config, value


class CatalogTests(TestCase):
    def setUp(self):
        directory = TemporaryDirectory(prefix='df-provider-catalog-')
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.home = self.root / 'home'
        self.home.mkdir()
        env = patch.dict(os.environ, {'HOME':str(self.home), 'XDG_CONFIG_HOME':str(self.home/'config'),
                                      'GH_CONFIG_DIR':str(self.home/'gh'), 'CODEX_HOME':str(self.home/'.codex'),
                                      'CLAUDE_CONFIG_DIR':str(self.home/'.claude'), 'COPILOT_HOME':str(self.home/'.copilot')})
        env.start()
        self.addCleanup(env.stop)
        projects = self.root / 'projects'
        projects.mkdir()
        self.store = DraftStore(self.root / 'state', [projects])
        self.config, self.values = make_profile(self.root)
        self.calls = []

    def observation(self, profile):
        self.calls.append(profile.profile_id)
        return MODELS

    def catalog(self, transport=None, path=None):
        return CopilotCatalog.from_path(self.config if path is None else path, self.store,
                                        transport=self.observation if transport is None else transport)

    @contextmanager
    def service(self, catalog):
        server, token = create_server(self.store, copilot_catalog=catalog)
        thread = Thread(target=server.serve_forever, kwargs={'poll_interval':.03}, daemon=True)
        thread.start()
        try:
            yield server.server_port, token
        finally:
            server.shutdown()
            server.server_close()
            thread.join(5)

    def http(self, endpoint, method='GET', value=None, *, path=PATH, headers=None):
        port, token = endpoint
        if value is not None and not isinstance(value, bytes):
            value = json.dumps(value).encode()
        fields = {'Authorization':'Bearer ' + token, 'Content-Type':'application/json'}
        fields.update(headers or {})
        with closing(HTTPConnection('127.0.0.1', port, timeout=10)) as client:
            client.request(method, path, body=value, headers=fields)
            response = client.getresponse()
            return response.status, json.loads(response.read())

    def fixed(self, response, status, code):
        actual, value = response
        self.assertEqual(actual, status)
        self.assertEqual(set(value), FIELDS)
        self.assertEqual(value['code'], code)
        self.assertFalse(value['execution_available'])
        self.assertFalse(value['inference_verified'])
        self.assertEqual(value['effort_source'], 'unknown')
        return value

    def test_startup_only_reads_config_and_get_never_reads_payload_or_spawns(self):
        reads = []
        original = RegisteredFile.reader

        def reader(file):
            reads.append(file.kind)
            return original(file)

        before = (self.store.state / 'v2.sqlite').read_bytes()
        with patch.object(RegisteredFile, 'reader', reader):
            catalog = self.catalog()
            with self.service(catalog) as endpoint:
                self.fixed(self.http(endpoint), 200, 'COPILOT_NOT_DISCOVERED')
                self.assertEqual(self.http(endpoint, path='/api/v2/status')[0], 200)
                self.assertEqual(self.http(endpoint, path='/api/v2/providers/discover', method='POST', value={})[0], 404)
                self.assertEqual(self.calls, [])
        self.assertEqual(reads, ['config'])
        self.assertEqual((self.store.state / 'v2.sqlite').read_bytes(), before)

    def test_unconfigured_invalid_and_authentication_are_closed(self):
        with self.service(CopilotCatalog.from_path(None, self.store)) as endpoint:
            self.fixed(self.http(endpoint), 200, 'COPILOT_NOT_CONFIGURED')
            self.fixed(self.http(endpoint, 'POST', {'profile_id':PROFILE}), 404, 'COPILOT_PROFILE_UNKNOWN')
        self.config.chmod(0o644)
        with self.service(self.catalog()) as endpoint:
            self.fixed(self.http(endpoint), 200, 'COPILOT_UNAVAILABLE')
            self.assertEqual(self.http(endpoint, 'POST', {'profile_id':PROFILE}, headers={'Authorization':'bad'})[0], 401)
            self.assertEqual(self.http(endpoint, 'POST', {'profile_id':PROFILE}, headers={'Origin':'https://wrong.invalid'})[0], 403)
        self.assertEqual(self.calls, [])

    def test_success_in_memory_and_neutral_rejected_requests_after_cache(self):
        before = (self.store.state / 'v2.sqlite').read_bytes()
        with self.service(self.catalog()) as endpoint:
            result = self.fixed(self.http(endpoint, 'POST', {'profile_id':PROFILE}), 200, None)
            self.assertEqual(result['models'], MODELS)
            self.assertEqual(result['auth_status'], 'authenticated')
            self.assertEqual(result['source'], 'copilot-cli-models-list')
            self.assertTrue(result['fresh'])
            self.assertEqual(self.http(endpoint), (200, result))
            bad = [(self.http(endpoint, 'POST', {'profile_id':'b'*20}),404,'COPILOT_PROFILE_UNKNOWN')]
            for raw in (b'{}', b'{"profile_id":true}', b'{"profile_id":"'+PROFILE.encode()+b'","profile_id":"'+PROFILE.encode()+b'"}',
                        b'\xff', b' '*257, json.dumps({'profile_id':PROFILE,'workspace':'x'}).encode()):
                bad.append((self.http(endpoint,'POST',raw),400,'COPILOT_INVALID_REQUEST'))
            bad.append((self.http(endpoint,path=PATH+'?'),400,'COPILOT_INVALID_REQUEST'))
            for response, status, code in bad:
                value = self.fixed(response,status,code)
                self.assertEqual((value['profile_id'],value['models'],value['observed_at'],value['source']), (None,[],None,None))
            self.assertEqual(self.http(endpoint), (200, result))
        self.assertEqual(self.calls,[PROFILE])
        self.assertEqual((self.store.state / 'v2.sqlite').read_bytes(), before)

    def test_profile_schema_paths_permissions_and_forbidden_default(self):
        original = self.config.read_bytes()
        wrong = [dict(self.values,version=True), dict(self.values,extra='x'),
                 dict(self.values,github_host='other'), dict(self.values,github_login='-bad'),
                 dict(self.values,gh_executable='relative'), dict(self.values,copilot_sha256='?'),
                 dict(self.values,github_hosts_file=str(self.store.state/'hosts.yml'))]
        for value in wrong:
            self.config.write_text(json.dumps(value))
            self.assertEqual(self.catalog().get()['code'], 'COPILOT_UNAVAILABLE')
        for raw in (original[:-1]+b',"version":1}',b' '*8193,b'\xff'):
            self.config.write_bytes(raw)
            self.assertEqual(self.catalog().get()['code'], 'COPILOT_UNAVAILABLE')
        self.config.write_bytes(original)
        sym = self.root / 'catalog-link.json'
        sym.symlink_to(self.config)
        self.assertEqual(self.catalog(path=sym).get()['code'], 'COPILOT_UNAVAILABLE')
        hosts = Path(self.values['github_hosts_file'])
        hardlink = self.root / 'linked'
        os.link(hosts, hardlink)
        self.assertEqual(self.catalog().get()['code'], 'COPILOT_UNAVAILABLE')
        hardlink.unlink()
        actual = self.home / '.config/gh/hosts.yml'
        actual.parent.mkdir(parents=True)
        actual.write_bytes(hosts.read_bytes())
        actual.chmod(0o600)
        self.config.write_text(json.dumps(dict(self.values, github_hosts_file=str(actual))))
        self.assertEqual(self.catalog().get()['code'], 'COPILOT_UNAVAILABLE')
        self.assertEqual(self.calls, [])

    def test_every_identity_change_clears_existing_data_permanently(self):
        for name in ('github_hosts_file','gh_executable','copilot_executable', 'configuration'):
            with self.subTest(name=name):
                catalog = self.catalog()
                self.assertEqual(catalog.refresh(PROFILE)[0], 200)
                path = self.config if name == 'configuration' else Path(self.values[name])
                raw, mode = path.read_bytes(), path.stat().st_mode & 0o777
                swap = self.root / 'same-bytes'
                swap.write_bytes(raw)
                swap.chmod(mode)
                os.replace(swap, path)
                value = catalog.get()
                self.assertEqual((value['code'], value['models'], value['auth_status']), ('COPILOT_IDENTITY_CHANGED', [], 'unknown'))
                self.assertEqual(catalog.refresh(PROFILE)[0],409)
        self.assertEqual(self.calls, [PROFILE]*4)

    def test_sealed_read_integrity_permissions_and_digest(self):
        profile = self.catalog().profile
        with profile.gh.sealed(time.monotonic()+2) as fd:
            self.assertEqual(os.lseek(fd,0,os.SEEK_CUR),0)
            self.assertTrue(os.read(fd,20).startswith(b'\x7fELF'))
            seals = fcntl.fcntl(fd,fcntl.F_GET_SEALS)
            self.assertEqual(seals, fcntl.F_SEAL_SEAL | fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK)
            with self.assertRaises(PermissionError):
                os.write(fd,b'x')
        self.config.write_text(json.dumps(dict(self.values,gh_sha256='0'*64)))
        with self.assertRaises(IdentityChanged), self.catalog().profile.gh.sealed(time.monotonic()+2):
            self.fail('wrong digest exposed an fd')

    def test_ttl_and_failed_refresh_never_make_old_data_fresh(self):
        fail = False

        def transport(profile):
            if fail:
                raise CatalogFault('COPILOT_TIMEOUT')
            return MODELS

        catalog = self.catalog(transport)
        with patch('v2.copilot_catalog.time.monotonic', return_value=100):
            self.assertEqual(catalog.refresh(PROFILE)[0], 200)
        with patch('v2.copilot_catalog.time.monotonic', return_value=399.999):
            self.assertTrue(catalog.get()['fresh'])
        with patch('v2.copilot_catalog.time.monotonic', return_value=400):
            value = catalog.get()
            self.assertEqual((value['code'],value['auth_status'],value['models'],value['stale']),
                             ('COPILOT_STALE','unknown',MODELS,True))
        fail = True
        status, value = catalog.refresh(PROFILE)
        self.assertEqual((status,value['code'],value['fresh'],value['models']), (503,'COPILOT_TIMEOUT',False,MODELS))
        self.assertEqual(catalog.get(),value)
        fail = False
        self.assertTrue(catalog.refresh(PROFILE)[1]['fresh'])

    def test_concurrent_refresh_busy_and_identity_change_while_in_flight(self):
        entered, release = Event(), Event()

        def hold(profile):
            self.calls.append(profile.profile_id)
            entered.set()
            self.assertTrue(release.wait(6))
            return MODELS

        with self.service(self.catalog(hold)) as endpoint, ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(self.http,endpoint,'POST',{'profile_id':PROFILE})
            try:
                self.assertTrue(entered.wait(6))
                self.fixed(self.http(endpoint,'POST',{'profile_id':PROFILE}),409,'COPILOT_BUSY')
                self.fixed(self.http(endpoint),200,'COPILOT_NOT_DISCOVERED')
                os.chmod(self.values['github_hosts_file'],0o644)
                self.fixed(self.http(endpoint),200,'COPILOT_IDENTITY_CHANGED')
            finally:
                release.set()
            self.fixed(pending.result(timeout=8),409,'COPILOT_IDENTITY_CHANGED')
        self.assertEqual(self.calls,[PROFILE])

    def wire(self, case, seconds=3):
        trace = self.root / 'methods'
        trace.write_text('')
        profile = self.catalog().profile
        process = subprocess.Popen([sys.executable,'-I','-B','-c',WIRE_FIXTURE,case,str(trace)],
                                   env={'HOME':str(self.home)}, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, start_new_session=True, bufsize=0)
        try:
            return query_catalog(process,profile,TOKEN,time.monotonic()+seconds)
        finally:
            _stop(process)
            process.stdin.close()
            process.stdout.close()
            self.assertIsNotNone(process.returncode)
            self.methods = trace.read_text().splitlines()

    def test_real_wire_three_frames_and_private_names_are_filtered(self):
        self.assertEqual(self.wire('ok'),MODELS)
        self.assertEqual(self.methods,['connect','auth.getStatus','models.list'])
        self.assertEqual(self.wire('private-names'),[{'id':'one'},{'id':'two'},{'id':'three'},
                                                     {'id':'four','name':'<b>literal</b>'}])

    def test_real_wire_protocol_faults_and_auth_gate(self):
        for case in ('version','callback','duplicate','oversize','header','wrong-id','bad-auth','extra-notification','cumulative'):
            with self.subTest(case=case), self.assertRaises(CatalogFault) as raised:
                self.wire(case)
            self.assertEqual(raised.exception.code,'COPILOT_PROTOCOL_ERROR')
            if case not in ('extra-notification','cumulative'):
                self.assertNotIn('models.list',self.methods)
        for case, auth in (('no-auth','unauthenticated'),('wrong-account','unknown'),
                           ('wrong-host','unknown'),('no-host','unknown')):
            with self.subTest(case=case), self.assertRaises(CatalogFault) as raised:
                self.wire(case)
            self.assertEqual((raised.exception.code,raised.exception.auth_status),('COPILOT_AUTH_REQUIRED',auth))
            self.assertEqual(self.methods,['connect','auth.getStatus'])

    def test_real_wire_empty_duplicate_overbudget_and_private_id(self):
        for case in ('empty','duplicate-id','wide','secret-id','account-id','malformed-id'):
            with self.subTest(case=case), self.assertRaises(CatalogFault) as raised:
                self.wire(case)
            self.assertEqual(raised.exception.code,'COPILOT_DIRECTORY_INVALID')
            self.assertEqual(self.methods,['connect','auth.getStatus','models.list'])

    def test_real_wire_deadline_reaps_owned_synthetic_child(self):
        with self.assertRaises(TimeoutError):
            self.wire('hang',.25)
        self.assertEqual(self.methods,['connect'])
