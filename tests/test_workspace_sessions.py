"""M0 native-session adapters, bounded IO, attribution and real HTTP boundaries."""
from contextlib import closing
import hashlib
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from studio.server import App, handler
from studio.graph import Invalid
from studio.session_sources import scan

SID = '11111111-2222-4333-8444-555555555555'
OTHER = 'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee'
PRIVATE = 'PRIVATE_PROMPT_NEVER_INDEX_OR_DISPLAY'


def native(root, provider, cwd, sid=SID):
    if provider == 'codex':
        path = root/'sessions/2026/10/20'/('rollout-2026-10-20T01-00-00-'+sid+'.jsonl')
        records = [dict(type='session_meta', timestamp='2026-10-20T01:00:00Z',
                        payload=dict(id=sid, cwd=str(cwd), cli_version='0.163.0')),
                   dict(type='response_item', payload=dict(content=PRIVATE))]
    else:
        path = root/'projects'/'-synthetic-project'/(sid+'.jsonl')
        records = [dict(type='queue-operation', sessionId=sid, content=PRIVATE),
                   dict(type='user', sessionId=sid, cwd=str(cwd), timestamp='2026-10-20T01:00:00Z',
                        version='2.1.303', message={'content': PRIVATE})]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(''.join(json.dumps(r)+'\n' for r in records))
    return path


def tree_hash(root):
    return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file() and not p.is_symlink()}


def fixture(base):
    a=base/'project-A'; b=base/'project-B'; a.mkdir(); b.mkdir()
    home=base/'default-home'; home.mkdir()
    codex=home/'.codex'; nested=a/'nested/.claude'; external=base/'review-home/.claude'
    paths=[native(codex,'codex',a),native(nested,'claude',a),native(external,'claude',a)]
    other=native(codex,'codex',b,OTHER)
    for root in [codex,nested,external]:
        (root/'auth.json').write_text('{"secret":"SYNTHETIC_AUTH_NO_READ"}\n')
    cfg=base/'profiles.json'
    profiles=[dict(provider='codex',home=str(home),config_root=str(codex)),
              dict(provider='claude',home=str(a/'nested'),config_root=str(nested),
                   workspace=str(a),original_project_root=str(a)),
              dict(provider='claude',home=str(external.parent),config_root=str(external),
                   workspace=str(a),original_project_root=str(a))]
    cfg.write_text(json.dumps(dict(defaults=False,profiles=profiles)))
    return dict(a=a,b=b,home=home,roots=[codex,nested,external],paths=paths,other=other,cfg=cfg)


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.base=Path(self.tmp.name)
        self.f=fixture(self.base)
        self.app=App(self.base/'studio',project_roots=[self.f['a'],self.f['b']],session_profiles=self.f['cfg'])
        self.ids={x['path']:x['id'] for x in self.app.sessions.listing()['workspaces']}
        self.a=self.ids[str(self.f['a'])];self.b=self.ids[str(self.f['b'])]

    def tearDown(self):
        self.tmp.cleanup()

    def snapshot(self):
        return self.app.sessions.snapshot(self.a)

    def config(self,edit):
        cfg=json.loads(self.f['cfg'].read_text());edit(cfg);self.f['cfg'].write_text(json.dumps(cfg))
        return App(self.base/'other-studio',project_roots=[self.f['a'],self.f['b']],session_profiles=self.f['cfg'])

    def test_three_profiles_same_project_and_duplicate_ids_are_preserved(self):
        rows=self.snapshot()['sessions']
        self.assertEqual(len(rows),3);self.assertEqual(len({x['identity'] for x in rows}),3)
        self.assertEqual(len({x['profile_id'] for x in rows}),3)
        self.assertEqual({x['session_id'] for x in rows},{SID})
        self.assertEqual({x['provider'] for x in rows},{'codex','claude'})
        self.assertTrue(all(x['workspace_id']==self.a and x['cwd_verified'] for x in rows))

    def test_separate_workspace_no_previous_records(self):
        self.snapshot(); data=self.app.sessions.snapshot(self.b)
        self.assertEqual([x['session_id'] for x in data['sessions']],[OTHER])
        self.assertTrue(any(x['status']=='not_scanned' for x in data['coverage']))

    def test_discovery_and_index_leave_native_libraries_and_auth_unchanged(self):
        before=[tree_hash(x) for x in self.f['roots']]
        for _ in range(3):self.snapshot()
        self.assertEqual(before,[tree_hash(x) for x in self.f['roots']])

    def test_index_and_response_do_not_contain_raw_messages_or_credentials(self):
        text=json.dumps(self.snapshot())
        self.assertNotIn(PRIVATE,text);self.assertNotIn('SYNTHETIC_AUTH',text)
        with closing(sqlite3.connect(self.base/'studio/sessions.sqlite')) as db:
            dump='\n'.join(db.iterdump())
        self.assertNotIn(PRIVATE,dump);self.assertNotIn('SYNTHETIC_AUTH',dump)

    def test_resume_never_inferred_from_metadata_and_command_uses_original_environment(self):
        for row in self.snapshot()['sessions']:
            self.assertEqual(row['capabilities']['resume'],'unverified')
            self.assertIn('HOME=',row['resume_command'])
            self.assertIn('CODEX_HOME=' if row['provider']=='codex' else 'CLAUDE_CONFIG_DIR=',row['resume_command'])
            self.assertIn(SID,row['resume_command']);self.assertNotIn('--last',row['resume_command'])

    def test_default_claude_resume_command_uses_native_home_preferences(self):
        from studio.workspace_sessions import WorkspaceSessions
        home = self.f['home']
        native(home/'.claude', 'claude', self.f['a'])
        sessions = WorkspaceSessions(self.base/'studio', [self.f['a']],
                                     environ={'HOME': str(home)}, persist=False)
        workspace = next(iter(sessions.roots))
        profile = next(p for p in sessions.profiles if p['provider'] == 'claude' and p['origin'] == 'default')
        from studio.session_sources import scan
        rows, _ = scan(str(home/'.claude/projects'), 'claude', sessions.limits)
        command = sessions._row(rows[0], profile, workspace)['resume_command']
        self.assertIn('HOME=', command)
        self.assertNotIn('CLAUDE_CONFIG_DIR=', command)
        self.assertIn('-u CLAUDE_CONFIG_DIR', command)
        self.assertIn(SID, command)

    def test_default_claude_explicit_config_root_remains_in_resume_command(self):
        from studio.workspace_sessions import WorkspaceSessions
        from studio.session_sources import scan
        home = self.f['home']
        root = self.base/'explicit-claude'
        native(root, 'claude', self.f['a'])
        sessions = WorkspaceSessions(self.base/'studio', [self.f['a']],
                                     environ={'HOME': str(home), 'CLAUDE_CONFIG_DIR': str(root)}, persist=False)
        workspace = next(iter(sessions.roots))
        profile = next(p for p in sessions.profiles if p['provider'] == 'claude' and p['origin'] == 'default')
        rows, _ = scan(str(root/'projects'), 'claude', sessions.limits)
        command = sessions._row(rows[0], profile, workspace)['resume_command']
        self.assertIn('CLAUDE_CONFIG_DIR='+str(root), command)
        self.assertNotIn('-u CLAUDE_CONFIG_DIR', command)

    def test_wrong_registered_origin_is_explicit_and_has_no_command(self):
        path=self.f['paths'][2]
        path.write_text(path.read_text().replace(str(self.f['a']),'/private/token-SENTINEL'))
        rows=self.snapshot()['sessions']; mismatch=[x for x in rows if x['association']=='mapping_mismatch']
        self.assertEqual(len(mismatch),1);self.assertIsNone(mismatch[0]['resume_command'])
        self.assertFalse(mismatch[0]['cwd_verified']);self.assertNotIn('SENTINEL',json.dumps(rows))

    def test_changed_cwd_is_unverified(self):
        with self.f['paths'][2].open('a') as f:
            f.write(json.dumps(dict(type='user',sessionId=SID,cwd=str(self.f['b'])))+'\n')
        row=next(x for x in self.snapshot()['sessions'] if x['association']=='cwd_changed')
        self.assertFalse(row['cwd_verified']);self.assertIsNone(row['resume_command'])

    def test_missing_original_cwd_remains_explicitly_unverified(self):
        self.f['paths'][2].write_text(self.f['paths'][2].read_text().replace(str(self.f['a']),str(self.f['a']/'deleted')))
        row=next(x for x in self.snapshot()['sessions'] if not x['cwd_verified'])
        self.assertEqual(row['association'],'registered_origin');self.assertIsNone(row['resume_command'])

    def test_corrupt_and_partial_writes_fail_closed(self):
        for suffix,reason in [('{broken}\n','corrupt_jsonl'),('{"type":"user"','partial_write')]:
            with self.subTest(reason=reason):
                p=self.f['paths'][2];old=p.read_bytes();p.write_bytes(old+suffix.encode())
                data=self.snapshot();self.assertEqual(len(data['sessions']),2)
                self.assertIn(reason,data['coverage'][2]['rejected']);p.write_bytes(old)

    def test_oversized_file_and_line_are_refused(self):
        for limits,reason in [({'file_bytes':20},'oversized'),({'line_bytes':20},'oversized_line')]:
            with self.subTest(reason=reason):
                rows,coverage=scan(self.f['roots'][2]/'projects','claude',limits)
                self.assertEqual(rows,[]);self.assertIn(reason,coverage['rejected'])

    def test_budget_is_reported_not_complete(self):
        for limits,reason in [({'entries':1},'entry_budget'),({'depth':1},'depth_budget'),
                              ({'bytes':1},'byte_budget'),({'files':1},'file_budget')]:
            with self.subTest(reason=reason):
                _,coverage=scan(self.f['roots'][0]/'sessions','codex',limits)
                self.assertEqual(coverage['status'],'partial');self.assertIn(reason,coverage['rejected'])

    def test_symlink_file_and_ancestor_are_refused(self):
        path=self.f['paths'][2];outside=self.base/'outside.jsonl';outside.write_bytes(path.read_bytes())
        path.unlink();path.symlink_to(outside)
        data=self.snapshot();self.assertEqual(len(data['sessions']),2)
        self.assertIn('symlink',data['coverage'][2]['rejected'])
        link=self.base/'alias';link.symlink_to(self.f['roots'][0],target_is_directory=True)
        _,coverage=scan(link/'sessions','codex')
        self.assertEqual(coverage['status'],'refused')

    def test_permission_error_has_distinct_coverage(self):
        path=self.f['roots'][2]/'projects';path.chmod(0)
        try:self.assertEqual(self.snapshot()['coverage'][2]['status'],'permission_denied')
        finally:path.chmod(0o700)

    def test_missing_and_unsupported_are_distinct(self):
        _,missing=scan(self.base/'missing','claude');_,unsupported=scan(self.base,'unknown-provider')
        self.assertEqual(missing['status'],'not_found');self.assertEqual(unsupported['status'],'unsupported')

    def test_config_folder_is_not_a_session_library(self):
        root=self.base/'config-only/.claude';root.mkdir(parents=True)
        (root/'settings.json').write_text('{"message":"PRIVATE"}')
        rows,coverage=scan(root/'projects','claude')
        self.assertFalse(rows);self.assertEqual(coverage['status'],'not_found')

    def test_unknown_layout_is_not_silently_complete(self):
        path=self.f['roots'][0]/'sessions/new-paginated-format.json';path.write_text('{}')
        self.assertIn('unscanned_file',self.snapshot()['coverage'][0]['rejected'])

    def test_malicious_identity_and_nonabsolute_cwd_fail_closed(self):
        path=self.f['paths'][2];old=path.read_text()
        for search,replace in [(SID,'../../evil'),(str(self.f['a']),'relative/project')]:
            path.write_text(old.replace(search,replace))
            self.assertEqual(len(self.snapshot()['sessions']),2)
        path.write_text(old)

    def test_control_characters_in_path_are_refused(self):
        p=self.f['paths'][2]
        records=[json.loads(line) for line in p.read_text().splitlines()]
        records[1]['cwd']='/home/A'+chr(0)+'bad'
        p.write_text(''.join(json.dumps(row)+'\n' for row in records))
        data=self.snapshot()
        self.assertEqual(len(data['sessions']),2)
        self.assertIn('invalid_path',data['coverage'][2]['rejected'])
        self.assertNotIn('partial_write',data['coverage'][2]['rejected'])

    def test_special_files_are_reported_without_opening_them(self):
        import socket
        from unittest.mock import patch
        path=self.f['paths'][2]
        fifo=path.with_name(OTHER+'.jsonl')
        sockpath=path.with_name('bbbbbbbb-cccc-4ddd-8eee-ffffffffffff.jsonl')
        os.mkfifo(fifo)
        sock=socket.socket(socket.AF_UNIX)
        real_open=os.open
        def guarded_open(name,*args,**kwargs):
            self.assertNotIn(os.fspath(name),[fifo.name,sockpath.name,str(fifo),str(sockpath)])
            return real_open(name,*args,**kwargs)
        try:
            sock.bind(str(sockpath))
            with patch('studio.session_sources.os.open',side_effect=guarded_open):
                data=self.snapshot()
                self.assertEqual(len(data['sessions']),3)
                self.assertEqual(data['coverage'][2]['status'],'partial')
                self.assertEqual(data['coverage'][2]['rejected'].get('special_file'),2)
                path.unlink()
                data=self.snapshot()
                self.assertEqual(data['coverage'][2]['status'],'partial')
                self.assertEqual(data['coverage'][2]['rejected'].get('special_file'),2)
        finally:
            sock.close()

    def test_surrogate_cwd_rejects_only_bad_file_over_real_http(self):
        path=self.f['paths'][2]
        old=path.read_text()
        server=ThreadingHTTPServer(('127.0.0.1',0),handler(self.app))
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            for codepoint in (0xd800,0xdc80,0xdfff):
                with self.subTest(codepoint=codepoint):
                    records=[json.loads(line) for line in old.splitlines()]
                    records[1]['cwd']=str(self.f['a'])+'/'+chr(codepoint)
                    path.write_text(''.join(json.dumps(row)+'\n' for row in records))
                    req=Request(f'http://127.0.0.1:{server.server_port}/api/workspaces/{self.a}/sessions',
                                headers={'Authorization':'Bearer '+self.app.token})
                    with urlopen(req) as response:
                        self.assertEqual(response.status,200)
                        data=json.load(response)
                    self.assertEqual(len(data['sessions']),2)
                    self.assertEqual(data['coverage'][2]['status'],'partial')
                    self.assertEqual(data['coverage'][2]['rejected'].get('invalid_path'),1)
        finally:
            server.shutdown();server.server_close();thread.join()

    def test_non_utf8_native_directory_does_not_break_good_sessions(self):
        root=self.f['roots'][2]/'projects'
        bad=os.fsdecode(os.fsencode(root)+b'/bad-\xff')
        native(Path(bad).parent.parent,'claude',self.f['a'],OTHER)
        normal=root/'-synthetic-project'/(OTHER+'.jsonl')
        Path(bad).mkdir()
        normal.rename(Path(bad)/(OTHER+'.jsonl'))
        data=self.snapshot()
        self.assertEqual(len(data['sessions']),3)
        self.assertEqual(data['coverage'][2]['status'],'partial')
        self.assertIn('invalid_path',data['coverage'][2]['rejected'])

    def test_growing_transcript_reads_only_initial_snapshot_budget(self):
        from unittest.mock import patch
        path=self.f['paths'][2];initial=path.stat().st_size
        consumed=[];requested=[];real_read=os.read;changed=[]
        def growing_read(fd,size):
            if not changed:
                with path.open('ab') as f:
                    f.write((json.dumps(dict(type='user',sessionId=SID,cwd=str(self.f['a'])))+'\n').encode()*100)
                changed.append(True)
            requested.append(size)
            data=real_read(fd,size);consumed.append(len(data));return data
        with patch('studio.session_sources.os.read',side_effect=growing_read):
            rows,coverage=scan(path.parent.parent,'claude')
        self.assertTrue(changed)
        self.assertEqual(rows,[])
        self.assertIn('changed_during_scan',coverage['rejected'])
        self.assertLessEqual(sum(consumed),initial)
        self.assertLessEqual(sum(requested),initial)


    def test_growth_and_truncation_after_accounting_are_rejected(self):
        from unittest.mock import patch
        import studio.session_sources as sources
        path=self.f['paths'][2];original=path.read_bytes()
        real_read=sources.read_session
        for change in ('growth','truncate'):
            with self.subTest(change=change):
                path.write_bytes(original)
                def changed_before_read(*args,**kwargs):
                    if change=='growth':
                        path.write_bytes(original+(json.dumps(dict(type='user',sessionId=SID,cwd=str(self.f['a'])))+'\n').encode()*100)
                    else:
                        path.write_bytes((json.dumps(dict(type='user',sessionId=SID,cwd=str(self.f['a'])))+'\n').encode())
                    return real_read(*args,**kwargs)
                with patch.object(sources,'read_session',side_effect=changed_before_read):
                    rows,coverage=scan(path.parent.parent,'claude',limits={'bytes':len(original)})
                self.assertEqual(rows,[])
                self.assertEqual(coverage['status'],'partial')
                self.assertIn('changed_during_scan',coverage['rejected'])
                self.assertLessEqual(coverage['bytes'],len(original))

    def test_regular_file_swapped_to_fifo_never_opens_data_stream(self):
        from unittest.mock import patch
        path=self.f['paths'][2];real_open=os.open
        swapped=[];data_opens=[]
        def swap_on_open(name,flags,*args,**kwargs):
            if os.fspath(name)==path.name and not swapped:
                path.unlink();os.mkfifo(path);swapped.append(True)
                if not flags & os.O_PATH:data_opens.append(name)
            return real_open(name,flags,*args,**kwargs)
        with patch('studio.session_sources.os.open',side_effect=swap_on_open):
            rows,coverage=scan(path.parent.parent,'claude')
        self.assertTrue(swapped)
        self.assertEqual(data_opens,[])
        self.assertEqual(rows,[])
        self.assertEqual(coverage['status'],'partial')
        self.assertIn('not_private_regular',coverage['rejected'])

    def test_character_device_metadata_never_opens_data_stream(self):
        import stat
        from unittest.mock import patch
        from studio.session_sources import opened, Refused
        real_open=os.open;data_opens=[]
        def observe_open(name,flags,*args,**kwargs):
            fd=real_open(name,flags,*args,**kwargs)
            if stat.S_ISCHR(os.fstat(fd).st_mode) and not flags & os.O_PATH:
                data_opens.append(os.fspath(name))
            return fd
        with patch('studio.session_sources.os.open',side_effect=observe_open):
            with self.assertRaises(Refused):
                with opened('/dev/null'):
                    self.fail('a character device is not a regular registration file')
        self.assertEqual(data_opens,[])


    def test_unknown_workspace_is_not_arbitrary_filesystem_read(self):
        with self.assertRaises(Invalid):self.app.sessions.snapshot('../anything')

    def test_observed_codex_rollout_suffix_retains_metadata_identity(self):
        p=self.f['paths'][0]
        p.rename(p.with_name(p.stem+'_'+OTHER+'.jsonl'))
        rows=self.snapshot()['sessions']
        self.assertEqual(len(rows),3)
        self.assertEqual(next(x for x in rows if x['provider']=='codex')['session_id'],SID)

    def test_explicit_session_filter_does_not_read_unrelated_corrupt_files(self):
        self.f['other'].write_text('{not json}\n')
        app=self.config(lambda cfg:cfg['profiles'][0].update(session_ids=[SID]))
        data=app.sessions.snapshot(self.a)
        self.assertEqual(len(data['sessions']),3)
        self.assertNotIn('corrupt_jsonl',data['coverage'][0]['rejected'])

    def test_original_environment_settings_binding_is_copy_only(self):
        settings=self.base/'review-settings.json';settings.write_text('{"credential":"NEVER_READ"}')
        before=settings.read_bytes()
        app=self.config(lambda cfg:cfg['profiles'][2].update(settings_file=str(settings)))
        data=app.sessions.snapshot(self.a)
        self.assertTrue(any('--settings' in (x['resume_command'] or '') for x in data['sessions']))
        self.assertNotIn('NEVER_READ',json.dumps(data));self.assertEqual(before,settings.read_bytes())

    def test_browser_cannot_register_profiles_or_supply_commands(self):
        class Server(ThreadingHTTPServer):pass
        server=Server(('127.0.0.1',0),handler(self.app))
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            base=f'http://127.0.0.1:{server.server_port}'
            for path in ['/api/workspaces','/api/sessions/resume','/api/sessions/register']:
                req=Request(base+path,b'{"command":"touch /tmp/never"}',headers={'Content-Type':'application/json','Authorization':'Bearer '+self.app.token})
                with self.assertRaises(HTTPError) as caught:urlopen(req)
                self.assertEqual(caught.exception.code,404)
            with urlopen(Request(base+'/api/workspaces/'+self.a+'/sessions',headers={'Authorization':'Bearer '+self.app.token})) as response:
                self.assertEqual(len(json.load(response)['sessions']),3)
            with self.assertRaises(HTTPError) as caught:urlopen(base+'/api/workspaces')
            self.assertEqual(caught.exception.code,401)
        finally:server.shutdown();server.server_close();thread.join()

    def test_index_native_overlap_rejected_before_any_native_write(self):
        storage=self.f['roots'][0]/'new-studio'
        with self.assertRaises(Invalid):
            App(storage,project_roots=[self.f['a'],self.f['b']],session_profiles=self.f['cfg'])
        self.assertFalse(storage.exists())

    def test_default_and_direct_nested_sources_are_discovered_without_home_recursion(self):
        native(self.f['home']/'.claude','claude',self.f['a'],OTHER)
        native(self.f['a']/'.claude','claude',self.f['a'],OTHER)
        app=App(self.base/'default-studio',project_roots=[self.f['a']],session_environ={'HOME':str(self.f['home'])})
        data=app.sessions.snapshot(next(iter(app.sessions.roots)))
        self.assertEqual(len(data['sessions']),3)
        self.assertEqual({x['origin'] for x in data['coverage']},{'default','nested'})


if __name__=='__main__':unittest.main()
