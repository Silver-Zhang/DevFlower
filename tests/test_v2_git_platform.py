"""Real workspace Git scope and V2 HTTP over independent synthetic platform records."""
import os
from pathlib import Path
import subprocess
from threading import Thread
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

from orchestration.common import Blocked
from v2.git_platform_adapters import remote_identity
from v2.git_workspace import ENV, GIT, bind_workspace
from v2.server import create_server

import test_v2_execution as fixtures
import test_v2_execution_http as http

HEAD = 'a'*40
NEXT = 'b'*40


def git(cwd,*args):
    return subprocess.run([GIT,'-C',str(cwd),*args],env=ENV,capture_output=True,text=True,check=True).stdout.strip()


def initialize_repo(root, origin):
    root.mkdir(parents=True,exist_ok=True)
    git(root,'init','-b','work/fixture')
    git(root,'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','--allow-empty','-m','fixture')
    git(root,'remote','add','origin',origin)


class FixturePlatform:
    def __init__(self, origin):
        self.platform,self.repository = remote_identity(origin)
        self.url = f'https://{self.platform}.com/{self.repository}'
        self.head = HEAD
        self.calls = []

    def repo(self):
        self.calls.append('repo')
        return dict(platform=self.platform,repository=self.repository,name=self.repository.split('/')[-1],
                    default_branch='main',url=self.url,private=False)

    def branches(self):
        return [self.branch_info('work/fixture'),self.branch_info('main')]

    def branch_info(self,ref):
        return dict(name=ref,sha=self.head,protected=ref=='main',url=self.url+'/tree/'+ref)

    def issues(self):
        return [self.issue(7)]

    def issue(self,number):
        return dict(id=number,title='Fixture Issue',state='open',body='Bound Issue evidence',
                    updated_at='2026-11-11T00:00:00Z',url=self.url+'/issues/'+str(number))

    def changes(self):
        return [self.change(11)]

    def change(self,number):
        kind='PR' if self.platform=='github' else 'MR'
        return dict(id=number,title='Fixture '+kind,state='open',body='Observed change',head_sha=self.head if number!=12 else None,
                    source_branch='work/fixture',target_branch='main',author='fixture',merged=False,draft=False,
                    url=self.url+'/changes/'+str(number))

    def checks(self,sha):
        self.calls.append(('checks',sha))
        return [dict(id=21,name='Fixture CI',sha=sha,state='completed',conclusion='failure',url=self.url+'/checks/21')]

    def jobs(self,check,sha):
        self.calls.append(('jobs',check,sha))
        return [dict(id=31,name='Run tests',state='completed',conclusion='failure',url=self.url+'/jobs/31')]

    def log(self,job,check,sha):
        self.calls.append(('log',job,check,sha))
        return dict(job_id=job,check_id=check,sha=sha,text='FAILED_PUBLIC_TEST\nGH_TOKEN=PRIVATE_SYNTHETIC_ENV',
                    truncated=False,raw={'token':'PRIVATE_SYNTHETIC_ENV'})


class GitPlatformTests(unittest.TestCase):
    setUp = fixtures.ExecutionTests.setUp
    request = http.ExecutionHTTPTests.request

    def setup_platform(self):
        self.project = self.root/'spaces'/'A'
        self.origin = 'https://github.com/example/one.git'
        initialize_repo(self.project,self.origin)
        self.adapter = FixturePlatform(self.origin)
        fake = patch('v2.git_platform_adapters.adapter_for',lambda url: self.adapter if url==self.origin else FixturePlatform(url))
        fake.start()
        self.addCleanup(fake.stop)
        server,self.token = create_server(self.store,runtime=self.rt)
        Thread(target=server.serve_forever,daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.url = f'http://127.0.0.1:{server.server_port}/api/v2/'

    def get(self,route,**params):
        scope = dict(workspace=self.workspace)|params
        if route!='repository' and 'binding' not in scope:
            scope['binding'] = bind_workspace(self.store,scope['workspace']).identity
        return self.request('git-platform/'+route+'?'+urlencode(scope))

    def test_exact_repo_root_and_literal_config_ignore_environment_and_rewrites(self):
        self.setup_platform()
        git(self.project,'config','url.https://github.com/other/repo.insteadOf','https://github.com/example/one.git')
        with patch.dict(os.environ,{'GIT_DIR':'/does/not/exist','GIT_CONFIG_COUNT':'1',
                                    'GIT_CONFIG_KEY_0':'remote.origin.url','GIT_CONFIG_VALUE_0':'https://evil.invalid'}):
            code,value = self.get('repository')
        self.assertEqual(code,200,value)
        self.assertEqual(value['repository'],'example/one')
        self.assertEqual(value['local']['head'],git(self.project,'rev-parse','HEAD'))
        self.assertEqual(self.get('repository',repository='other/repo')[0],400)
        child = self.project/'nested'
        child.mkdir()
        foreign = self.store.open_workspace(str(child))['id']
        self.assertEqual(self.get('repository',workspace=foreign)[0],409)
        git(self.project,'config','--add','remote.origin.url','https://github.com/other/two.git')
        self.assertEqual(self.get('repository')[0],409)

    def test_linked_worktree_valid_but_symlinked_git_metadata_refused(self):
        self.setup_platform()
        alternate = self.root/'spaces'/'B'
        git(self.project,'worktree','add','-b','work/linked',str(alternate))
        linked = self.store.open_workspace(str(alternate))['id']
        binding = bind_workspace(self.store,linked)
        self.assertEqual((binding.origin,binding.branch),(self.origin,'work/linked'))
        alternate.joinpath('.git').unlink()
        alternate.joinpath('.git').symlink_to(self.project/'.git',target_is_directory=True)
        with self.assertRaises(Blocked):
            bind_workspace(self.store,linked)

    def test_default_and_current_remote_branches_survive_bounded_first_page(self):
        self.setup_platform()
        values = [self.adapter.branch_info(f'work/other-{i}') for i in range(50)]
        with patch.object(self.adapter,'branches',return_value=values):
            code, result = self.get('branches')
        self.assertEqual(code,200,result)
        self.assertEqual(len(result['items']),50)
        self.assertEqual([row['name'] for row in result['items'][:2]],['main','work/fixture'])

    def test_ci_trace_exact_selected_commit_job_and_project(self):
        self.setup_platform()
        scope = dict(target_type='change',target=11,sha=HEAD)
        self.assertEqual(self.get('issue',number=7)[1]['item']['body'],'Bound Issue evidence')
        self.assertIsNone(self.get('change',number=12)[1]['item']['head_sha'])
        self.assertEqual(self.get('checks',**scope)[1]['items'][0]['sha'],HEAD)
        self.assertEqual(self.get('jobs',**scope,check=21)[1]['items'][0]['id'],31)
        self.assertEqual(self.get('log',**scope,check=21,job=99)[0],409)
        code, result = self.get('log',**scope,check=21,job=31)
        self.assertEqual(code,200,result)
        self.assertIn('FAILED_PUBLIC_TEST',result['log']['text'])
        self.assertNotIn('PRIVATE_SYNTHETIC_ENV',str(result))
        self.assertEqual([call for call in self.adapter.calls if isinstance(call,tuple) and call[0]=='log'],
                         [('log',31,21,HEAD)])
        self.adapter.head = NEXT
        self.assertEqual(self.get('checks',**scope)[0],409)

    def test_approval_creates_one_local_exact_review_and_never_publishes(self):
        self.setup_platform()
        route='git-platform/review'
        data=dict(workspace=self.workspace,binding=bind_workspace(self.store,self.workspace).identity,change=11,head_sha=HEAD,intent='1'*32,authorization='create-local-review')
        self.assertEqual(self.request(route,dict(data,authorization='approve'))[0],400)
        self.assertEqual(self.request(route,dict(data,change=12))[0],409)
        code,first = self.request(route,data)
        self.assertEqual(code,200,first)
        self.assertEqual(first['state'],'AWAITING_INDEPENDENT_REVIEW')
        self.assertEqual(self.request(route,data),(200,first))
        self.assertEqual(self.request(route,dict(data,intent='2'*32)),(200,first))
        self.assertEqual(self.request(route,dict(data,head_sha=NEXT))[0],409)
        self.assertEqual(len(self.store.db.list('git-review-task')),1)
        self.assertEqual(len(self.store.db.list('git-review-intent')),2)
        self.assertEqual(self.request('git-platform/publish',data)[0],405)
        self.assertEqual(self.request('git-platform/merge',data)[0],405)
        self.assertFalse(self.get('reviews')[1]['delivery']['publish_available'])
        git(self.project,'remote','set-url','origin','https://gitlab.com/group/two.git')
        self.assertEqual(self.get('repository')[1]['platform'],'gitlab')
        self.assertEqual(self.get('changes',binding=data['binding'])[0],409)
        self.assertEqual(self.get('reviews')[1]['items'],[])
        self.assertEqual(self.request(route,data)[0],409)
