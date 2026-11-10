"""Claude standalone startup profile. Claude Code applies its own config precedence."""
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

from orchestration.common import canonical, cli_probe_env, digest
from studio.graph import Invalid
from studio.native_codex_image import binary_digest

# Only documented Claude/Anthropic invocation settings enter the child.
# clean_env independently handles non-authenticated system proxies.
CLI_ENV = (
    'ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_BASE_URL',
    'ANTHROPIC_CUSTOM_HEADERS', 'ANTHROPIC_MODEL', 'ANTHROPIC_DEFAULT_HAIKU_MODEL',
    'ANTHROPIC_DEFAULT_SONNET_MODEL', 'ANTHROPIC_DEFAULT_OPUS_MODEL',
    'ANTHROPIC_DEFAULT_FABLE_MODEL', 'ANTHROPIC_SMALL_FAST_MODEL',
    'CLAUDE_CODE_OAUTH_TOKEN', 'CLAUDE_CODE_EFFORT_LEVEL',
    'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC', 'NODE_EXTRA_CA_CERTS',
)
MAX_ENV_BYTES = 32 * 1024
SUPPORTED_CLI = b'2.1.319 (Claude Code)'
REQUIRED_FLAGS = (
    b'--safe-mode', b'--disable-slash-commands', b'--no-chrome', b'--tools',
    b'--strict-mcp-config', b'--setting-sources', b'--settings', b'--session-id', b'--model', b'--effort',
)


def startup_environment(environment, legacy_endpoint=None):
    values = {key: environment[key] for key in CLI_ENV if key in environment}
    if legacy_endpoint is not None:
        # The legacy field can attest an already configured CLI address. It
        # cannot introduce a new route for credentials inherited by Claude.
        if values.get('ANTHROPIC_BASE_URL') != legacy_endpoint:
            raise Invalid('旧 Claude 地址字段需要与启动环境 ANTHROPIC_BASE_URL 一致。请核对原生配置或删除该字段。')
    if sum(len(key) + len(value.encode()) for key, value in values.items()) > MAX_ENV_BYTES:
        raise Invalid('Claude Code 启动环境超过核验上限。')
    return values


def legacy_endpoint_valid(value):
    if not isinstance(value, str) or len(value) > 2048 or any(char.isspace() for char in value):
        return False
    try:
        url = urlsplit(value)
        return url.scheme in ('http', 'https') and bool(url.hostname) and url.username is None and url.password is None
    except ValueError:
        return False


def environment_identity(values):
    return digest(canonical(values))


def discover_cli():
    executable = shutil.which('claude')
    if not executable:
        raise Invalid('未找到 Claude Code。')
    executable = str(Path(executable).resolve(strict=True))
    with os.fdopen(os.open(executable, os.O_RDONLY | os.O_NOFOLLOW), 'rb') as image:
        fd = image.fileno()
        sha = binary_digest(fd)
        options = dict(executable=f'/proc/self/fd/{fd}', pass_fds=(fd,),
                       capture_output=True, timeout=12, check=False)
        try:
            with TemporaryDirectory(prefix='df-claude-capability-') as scratch:
                env = cli_probe_env(scratch)
                version = subprocess.run([executable, '--version'], env=env, **options)
                if version.returncode or version.stdout.strip() != SUPPORTED_CLI:
                    raise Invalid('此 Claude Code 版本的原生配置与扩展隔离尚未验证。当前会话写入支持 2.1.319。')
                help_result = subprocess.run([executable, '--help'], env=env, **options)
        except subprocess.SubprocessError:
            raise Invalid('Claude Code 启动能力无法核验。') from None
        if help_result.returncode or any(flag not in help_result.stdout for flag in REQUIRED_FLAGS):
            raise Invalid('Claude Code 缺少原生配置与扩展隔离所需的启动选项。')
        if binary_digest(fd) != sha:
            raise Invalid('Claude Code 映像在启动核验期间变化。')
    return {'executable': executable, '_executable_sha256': sha, 'cli_version': SUPPORTED_CLI.decode()}
