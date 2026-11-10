"""Transport contracts without remote writes or real credentials."""
from io import BytesIO
import json
import os
from unittest import TestCase
from unittest.mock import Mock, patch

from studio.graph import Invalid
from studio.server import Conflict
from v2.git_platform_adapters import GitHubAdapter, GitLabAdapter, NoRedirect, remote_identity

SHA = 'a' * 40


class PlatformAdaptersTests(TestCase):
    def test_literal_remote_routes_with_no_credentials_aliases_or_traversal(self):
        self.assertEqual(remote_identity('git@github.com:owner/repo.git'), ('github','owner/repo'))
        self.assertEqual(remote_identity('ssh://git@gitlab.com/group/sub/project.git'), ('gitlab','group/sub/project'))
        for value in ('https://token@github.com/owner/repo.git','https://github.com.example/owner/repo',
                      'https://gitlab.com/group/../project','https://gitlab.com/group/%2f/project',
                      'https://github.com/group/sub/repo','https://github.com/owner/repo?write=yes',
                      'file:///workspace/test.git','git@local-alias:owner/repo.git'):
            with self.subTest(value=value), self.assertRaises(Invalid):
                remote_identity(value)

    def test_github_pulls_issues_and_jobs_bind_the_real_head(self):
        client = Mock(executable='/usr/bin/gh', config_dir='/unused-fixture')
        client.environment.return_value = {}
        base = 'repos/owner/repo'
        job = dict(id=3, run_id=5, head_sha=SHA, status='completed', conclusion='failure', name='tests')
        response = {
            base: dict(full_name='owner/repo', default_branch='main', name='repo', private=False),
            base+'/issues?state=all&per_page=20': [
                dict(number=1, title='Issue', state='open'), dict(number=2, pull_request={})],
            base+'/pulls/2': dict(number=2, title='Change', state='open', head=dict(ref='fix/example',sha=SHA),
                                  base=dict(ref='main'), user=dict(login='author'),draft=True),
            base+'/actions/runs?head_sha='+SHA+'&per_page=20': dict(workflow_runs=[
                dict(id=5, head_sha=SHA,status='completed',conclusion='failure'),
                dict(id=9,head_sha='b'*40,status='completed',conclusion='failure')]),
            base+'/actions/runs/5': dict(id=5,head_sha=SHA),
            base+'/actions/runs/5/jobs?per_page=20': dict(jobs=[job, dict(job,id=4,run_id=6)]),
        }
        client.api.side_effect = lambda path, **_: response[path]
        adapter = GitHubAdapter('owner/repo', client=client)
        self.assertEqual(adapter.repo()['default_branch'],'main')
        self.assertEqual([row['id'] for row in adapter.issues()],[1])
        self.assertEqual(adapter.change(2)['head_sha'],SHA)
        self.assertEqual([row['id'] for row in adapter.checks(SHA)],[5])
        with patch('v2.git_platform_adapters.execute',return_value=(
            dict(exit=0),b'AssertionError token=synthetic-value\n',b'')) as execute:
            with self.assertRaises(Conflict):
                adapter.log(4,5,SHA)
            result = adapter.log(3,5,SHA)
        execute.assert_called_once()
        self.assertIn('AssertionError',result['text'])
        self.assertNotIn('synthetic-value',result['text'])
        with self.assertRaises(Conflict):
            adapter.jobs(5,'b'*40)

    def test_gitlab_encoded_namespace_pipeline_and_trace_without_invented_conclusion(self):
        opener = Mock()
        prefix = 'https://gitlab.com/api/v4/projects/group%2Fsub%2Fproject'
        job = dict(id=7,pipeline=dict(id=4),commit=dict(id=SHA),status='failed')
        answers = {
            '':dict(path_with_namespace='group/sub/project',default_branch='main',name='project',visibility='public'),
            '/repository/branches/fix%2Fa':dict(name='fix/a',protected=False,commit=dict(id=SHA)),
            '/merge_requests/1':dict(iid=1,title='Change',state='opened',sha=SHA,source_branch='fix/a',
                                     target_branch='main',author=dict(username='author')),
            '/pipelines?sha='+SHA+'&per_page=20':[dict(id=4,sha=SHA,status='failed')],
            '/pipelines/4':dict(id=4,sha=SHA),
            '/pipelines/4/jobs?per_page=20':[job,dict(job,id=8,pipeline=dict(id=5))],
            '/jobs/7/trace':b'pytest failed\nGLPAT-test-syntheticsecret\n',
        }
        def open_(request, **_):
            self.assertTrue(request.full_url.startswith(prefix))
            self.assertEqual(request.get_header('Private-token'),'configured-fixture-token')
            result = answers[request.full_url[len(prefix):]]
            return BytesIO(result if isinstance(result,bytes) else json.dumps(result).encode())
        opener.open.side_effect = open_
        with patch.dict(os.environ,DEVFLOWER_GITLAB_TOKEN='configured-fixture-token'):
            adapter = GitLabAdapter('group/sub/project',opener=opener)
            self.assertEqual(adapter.repo()['repository'],'group/sub/project')
            self.assertFalse(adapter.branch_info('fix/a')['protected'])
            self.assertEqual(adapter.change(1)['id'],1)
            self.assertEqual(adapter.checks(SHA)[0]['conclusion'],None)
            with self.assertRaises(Conflict):
                adapter.log(8,4,SHA)
            result = adapter.log(7,4,SHA)
        self.assertIn('pytest failed',result['text'])
        self.assertNotIn('syntheticsecret',result['text'])
        self.assertIsNone(NoRedirect().redirect_request(None,None,302,None,{},'https://other.example/'))
