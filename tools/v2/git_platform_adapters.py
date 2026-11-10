"""Read exact GitHub and GitLab repository facts for a literal Git origin."""
import json
import os
from pathlib import Path
import re
from shutil import which
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from orchestration.common import Blocked
from orchestration.github_delivery import GitHub
from orchestration.process import execute
from studio.agent_sessions import safe_text
from studio.graph import Invalid
from studio.server import Conflict

HOSTS = {'github.com': 'github', 'gitlab.com': 'gitlab'}
REMOTE = re.compile(r'(?:https://(?P<https>[^/]+)/|ssh://git@(?P<ssh>[^/]+)/|git@(?P<scp>[^:]+):)(?P<path>[^?#\s]+)')
PART = re.compile(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}')
REF = re.compile(r'[^\s~^:?*\[\\]{1,255}')
SHA = re.compile(r'[0-9a-f]{40}')
PAGE = 20
MAX_JSON = 2_000_000
LOG_BYTES = 80_000


def remote_identity(url):
    match = REMOTE.fullmatch(url) if isinstance(url, str) and len(url) <= 1024 else None
    if not match:
        raise Invalid('origin 必须是 GitHub.com 或 GitLab.com 的明确 HTTPS 或 Git SSH 地址。')
    host = (match['https'] or match['ssh'] or match['scp']).lower()
    repository = match['path'].removesuffix('.git')
    parts = repository.split('/')
    if (host not in HOSTS or not 2 <= len(parts) <= 12
            or host == 'github.com' and len(parts) != 2 or any(not PART.fullmatch(item) for item in parts)):
        raise Invalid('origin 无法绑定到受支持的平台仓库。')
    return HOSTS[host], repository


def checked_ref(value):
    if (not isinstance(value, str) or not REF.fullmatch(value) or not value.isprintable()
            or value.startswith(('-', '/')) or value == '@' or '@{' in value or '..' in value or '//' in value
            or any(part.startswith('.') or part.endswith(('.lock', '.')) for part in value.split('/'))
            or value.endswith('/')):
        raise Invalid('分支身份无效。')
    return value


def optional_sha(value):
    return value if isinstance(value, str) and SHA.fullmatch(value) else None


def checked_sha(value):
    if not isinstance(value, str) or not SHA.fullmatch(value):
        raise Invalid('平台记录缺少完整的 Git 提交身份。')
    return value


def checked_id(value):
    if type(value) is not int or not 0 < value < 2**63:
        raise Invalid('平台记录编号无效。')
    return value


def clean_text(value, limit=1000):
    if not isinstance(value, str):
        return None
    value = re.sub(r'(?i)\b(?:glpat|glrt|gldt|glcbt)-[a-z0-9_-]{8,}\b', '[REDACTED]', value)
    for key in ('DEVFLOWER_GITLAB_TOKEN', 'GH_TOKEN', 'GITHUB_TOKEN'):
        token = os.environ.get(key)
        if token and len(token) >= 8:
            value = value.replace(token, '[REDACTED]')
    return safe_text(value)[:limit]


def branch_view(value, url, sha):
    name = checked_ref(value['name'])
    protected = value.get('protected')
    return dict(name=name, sha=checked_sha(sha), protected=protected if type(protected) is bool else None,
                url=url + quote(name, safe=''))


def text_log(raw, truncated, job_id, check_id, sha):
    if truncated:
        raw = raw[:LOG_BYTES].rpartition(b'\n')[0]
    text = clean_text(raw.decode('utf-8', errors='replace'), LOG_BYTES)
    return dict(job_id=job_id, check_id=check_id, sha=sha, text=text, truncated=truncated)


class GitHubAdapter:
    platform = 'github'

    def __init__(self, repository, client=None):
        executable = which('gh') if client is None else None
        if client is None and not executable:
            raise Blocked('本机缺少 GitHub CLI。')
        configured = os.environ.get('GH_CONFIG_DIR') or str(Path.home() / '.config' / 'gh')
        self.client = client if client is not None else GitHub(executable, configured, repository)
        self.repository = repository
        self.url = 'https://github.com/' + repository
        self.prefix = 'repos/' + repository

    def api(self, suffix='', *, missing=False):
        return self.client.api(self.prefix + suffix, missing=missing)

    def repo(self):
        item = self.api()
        if item.get('full_name', '').casefold() != self.repository.casefold():
            raise Conflict('GitHub 返回的仓库身份发生变化。')
        private = item.get('private')
        return dict(platform=self.platform, repository=self.repository, name=clean_text(item.get('name')),
                    default_branch=checked_ref(item['default_branch']), url=self.url,
                    private=private if type(private) is bool else None)

    def _branch(self, item):
        return branch_view(item, self.url+'/tree/', item['commit']['sha'])

    def branches(self):
        return [self._branch(item) for item in self.api(f'/branches?per_page={PAGE}')[:PAGE]]

    def branch_info(self, ref):
        value = self.api('/branches/' + quote(checked_ref(ref), safe=''), missing=True)
        branch = self._branch(value) if value else None
        if branch and branch['name'] != ref:
            raise Conflict('GitHub 返回的分支身份发生变化。')
        return branch

    def _issue(self, item):
        ident = checked_id(item['number'])
        return dict(id=ident, title=clean_text(item.get('title')), state=clean_text(item.get('state')),
                    url=f'{self.url}/issues/{ident}', updated_at=clean_text(item.get('updated_at')),
                    body=clean_text(item.get('body'), 12000))

    def issues(self):
        values = self.api(f'/issues?state=all&per_page={PAGE}')
        return [self._issue(item) for item in values[:PAGE] if 'pull_request' not in item]

    def issue(self, number):
        item = self.api(f'/issues/{checked_id(number)}')
        if 'pull_request' in item or item.get('number') != number:
            raise Conflict('该编号无法核验为本仓库的 Issue。')
        return self._issue(item)

    def _change(self, item):
        view = self._issue(item)
        return dict(view, url=f'{self.url}/pull/{view["id"]}', head_sha=optional_sha(item['head'].get('sha')),
                    source_branch=clean_text(item['head'].get('ref')), target_branch=clean_text(item['base'].get('ref')),
                    author=clean_text((item.get('user') or {}).get('login')), merged=item.get('merged'), draft=item.get('draft'))

    def changes(self):
        return [self._change(item) for item in self.api(f'/pulls?state=all&per_page={PAGE}')[:PAGE]]

    def change(self, number):
        item = self.api(f'/pulls/{checked_id(number)}')
        if item.get('number') != number:
            raise Conflict('GitHub 返回的 PR 编号发生变化。')
        return self._change(item)

    def checks(self, sha):
        checked_sha(sha)
        rows = self.api('/actions/runs?' + urlencode(dict(head_sha=sha, per_page=PAGE)))['workflow_runs']
        return [dict(id=checked_id(item['id']), name=clean_text(item.get('name')), state=clean_text(item.get('status')),
                     conclusion=clean_text(item.get('conclusion')), sha=sha, url=f'{self.url}/actions/runs/{item["id"]}')
                for item in rows[:PAGE] if item.get('head_sha') == sha]

    def jobs(self, check_id, sha):
        checked_id(check_id)
        checked_sha(sha)
        endpoint = f'/actions/runs/{check_id}'
        run = self.api(endpoint)
        if run.get('id') != check_id or run.get('head_sha') != sha:
            raise Conflict('GitHub Actions 运行与所选提交不同。')
        rows = self.api(endpoint + f'/jobs?per_page={PAGE}')['jobs']
        return [dict(id=checked_id(item['id']), name=clean_text(item.get('name')), state=clean_text(item.get('status')),
                     conclusion=clean_text(item.get('conclusion')), url=f'{self.url}/actions/runs/{check_id}/job/{item["id"]}')
                for item in rows[:PAGE] if item.get('run_id') == check_id and item.get('head_sha') == sha]

    def log(self, job_id, check_id, sha):
        checked_id(job_id)
        if not any(item['id'] == job_id for item in self.jobs(check_id, sha)):
            raise Conflict('日志作业不属于该 GitHub Actions 运行和提交。')
        argv = [str(self.client.executable), 'run', 'view', str(check_id), '--repo', self.repository,
                '--job', str(job_id), '--log']
        result, stdout, _ = execute(argv, '/', self.client.config_dir, timeout=25, cap=LOG_BYTES,
                                    isolate=False, env_extra=self.client.environment(), backend='native-local')
        if result.get('timeout') or result.get('exit') != 0 and not result.get('overflow'):
            raise Blocked('GitHub Actions 日志暂不可读，请在对应作业页面核对权限和保留期。')
        return text_log(stdout, bool(result.get('overflow')), job_id, check_id, sha)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class GitLabAdapter:
    platform = 'gitlab'

    def __init__(self, repository, opener=None):
        self.repository = repository
        self.url = 'https://gitlab.com/' + repository
        self.prefix = 'https://gitlab.com/api/v4/projects/' + quote(repository, safe='')
        self.opener = opener if opener is not None else build_opener(NoRedirect())
        self.token = os.environ.get('DEVFLOWER_GITLAB_TOKEN')

    def api(self, suffix='', *, missing=False, trace=False):
        headers = {'Accept': 'text/plain' if trace else 'application/json', 'User-Agent': 'DevFlower-V2'}
        if self.token:
            headers['PRIVATE-TOKEN'] = self.token
        cap = LOG_BYTES if trace else MAX_JSON
        try:
            with self.opener.open(Request(self.prefix+suffix, headers=headers), timeout=16) as response:
                raw = response.read(cap+1)
        except HTTPError as error:
            error.close()
            if missing and error.code == 404:
                return None
            raise Blocked('GitLab 读取失败。请核对项目可见性、令牌权限或平台状态。') from None
        except (URLError, OSError, ValueError):
            raise Blocked('GitLab 连接或响应未确认。') from None
        if trace:
            return raw, len(raw)>cap
        if len(raw)>cap:
            raise Blocked('GitLab 响应超过读取上限。')
        try:
            return json.loads(raw)
        except (ValueError, RecursionError):
            raise Blocked('GitLab 响应不是可核验的 JSON。') from None

    def repo(self):
        item = self.api()
        if item.get('path_with_namespace') != self.repository:
            raise Conflict('GitLab 返回的项目身份发生变化。')
        visibility = item.get('visibility')
        return dict(platform=self.platform, repository=self.repository, name=clean_text(item.get('name')),
                    default_branch=checked_ref(item['default_branch']), url=self.url,
                    private={'public': False, 'internal': True, 'private': True}.get(visibility))

    def _branch(self, item):
        return branch_view(item, self.url+'/-/tree/', item['commit']['id'])

    def branches(self):
        return [self._branch(item) for item in self.api(f'/repository/branches?per_page={PAGE}')[:PAGE]]

    def branch_info(self, ref):
        value = self.api('/repository/branches/'+quote(checked_ref(ref),safe=''),missing=True)
        branch = self._branch(value) if value else None
        if branch and branch['name'] != ref:
            raise Conflict('GitLab 返回的分支身份发生变化。')
        return branch

    def _issue(self, item):
        ident = checked_id(item['iid'])
        return dict(id=ident, title=clean_text(item.get('title')), state=clean_text(item.get('state')),
                    url=f'{self.url}/-/issues/{ident}', updated_at=clean_text(item.get('updated_at')),
                    body=clean_text(item.get('description'),12000))

    def issues(self):
        return [self._issue(item) for item in self.api(f'/issues?per_page={PAGE}')[:PAGE]]

    def issue(self, number):
        item = self.api(f'/issues/{checked_id(number)}')
        if item.get('iid') != number:
            raise Conflict('GitLab 返回的 Issue 编号发生变化。')
        return self._issue(item)

    def _change(self, item):
        view = self._issue(item)
        return dict(view,url=f'{self.url}/-/merge_requests/{view["id"]}', head_sha=optional_sha(item.get('sha')),
                    source_branch=clean_text(item.get('source_branch')), target_branch=clean_text(item.get('target_branch')),
                    author=clean_text((item.get('author') or {}).get('username')), merged=item.get('state')=='merged',
                    draft=item.get('draft'))

    def changes(self):
        return [self._change(item) for item in self.api(f'/merge_requests?per_page={PAGE}')[:PAGE]]

    def change(self, number):
        item = self.api(f'/merge_requests/{checked_id(number)}')
        if item.get('iid') != number:
            raise Conflict('GitLab 返回的 MR 编号发生变化。')
        return self._change(item)

    def checks(self, sha):
        checked_sha(sha)
        rows = self.api('/pipelines?' + urlencode(dict(sha=sha, per_page=PAGE)))
        return [dict(id=checked_id(item['id']), name=clean_text(item.get('name') or item.get('ref')),
                     state=clean_text(item.get('status')), conclusion=None, sha=sha,
                     url=f'{self.url}/-/pipelines/{item["id"]}') for item in rows[:PAGE] if item.get('sha')==sha]

    def jobs(self, check_id, sha):
        checked_id(check_id)
        checked_sha(sha)
        endpoint = f'/pipelines/{check_id}'
        pipeline = self.api(endpoint)
        if pipeline.get('id') != check_id or pipeline.get('sha') != sha:
            raise Conflict('GitLab Pipeline 与所选提交不同。')
        rows = self.api(endpoint + f'/jobs?per_page={PAGE}')
        return [dict(id=checked_id(item['id']), name=clean_text(item.get('name')), state=clean_text(item.get('status')),
                     conclusion=None,url=f'{self.url}/-/jobs/{item["id"]}') for item in rows[:PAGE]
                if (item.get('pipeline') or {}).get('id')==check_id and (item.get('commit') or {}).get('id')==sha]

    def log(self, job_id, check_id, sha):
        checked_id(job_id)
        if not any(item['id']==job_id for item in self.jobs(check_id,sha)):
            raise Conflict('日志作业不属于该 GitLab Pipeline 和提交。')
        raw,truncated = self.api(f'/jobs/{job_id}/trace',trace=True)
        return text_log(raw,truncated,job_id,check_id,sha)


def adapter_for(remote_url):
    platform, repository = remote_identity(remote_url)
    return (GitHubAdapter if platform=='github' else GitLabAdapter)(repository)
