"""Explicit host-only proof. Run in a private user/mount namespace."""
from functools import partial
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from v2.copilot_catalog import CopilotCatalog
from v2.copilot_transport import refresh_metadata
from v2.store import DraftStore
from test_v2_provider_catalog import PROFILE, make_profile


FIXTURE = r'''
#define _GNU_SOURCE
#include <fcntl.h>
#include <net/if.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

static void require(int condition) {
    if (!condition) exit(11);
}

static void env_is(const char *key, const char *value) {
    const char *actual = getenv(key);
    require(actual != NULL && strcmp(actual, value) == 0);
}

static void private_namespace(void) {
    char cwd[256];
    struct stat info;
    struct if_nameindex *names = if_nameindex();
    require(names != NULL && names[0].if_index && strcmp(names[0].if_name, "lo") == 0 && !names[1].if_index);
    if_freenameindex(names);
    require(getcwd(cwd, sizeof(cwd)) != NULL && strcmp(cwd, "/work") == 0);
    require(access(DENIED_PATH, F_OK) != 0);
    require(getenv("GH_TOKEN") == NULL && getenv("SSH_AUTH_SOCK") == NULL && getenv("DBUS_SESSION_BUS_ADDRESS") == NULL);
    env_is("HOME", "/home/catalog");
    require(stat("/tool/program", &info) == 0 && (info.st_mode & 0777) == 0500);
}

static void message(const char *method, int id, const char *result) {
    char header[129], separator[4], input[1024], output[1024], expected[64];
    size_t length = 0;
    require(fgets(header, sizeof(header), stdin) != NULL && sscanf(header, "Content-Length: %zu", &length) == 1);
    require(fgets(separator, sizeof(separator), stdin) != NULL && !strcmp(separator, "\r\n"));
    require(length > 0 && length < sizeof(input) && fread(input, 1, length, stdin) == length);
    input[length] = 0;
    snprintf(expected, sizeof(expected), "\"method\":\"%s\"", method);
    require(strstr(input, expected) != NULL && strstr(input, "session.") == NULL);
    snprintf(expected, sizeof(expected), "\"id\":%d", id);
    require(strstr(input, expected) != NULL);
    int size = snprintf(output, sizeof(output), "{\"jsonrpc\":\"2.0\",\"id\":%d,\"result\":%s}", id, result);
    require(size > 0 && (size_t)size < sizeof(output));
    printf("Content-Length: %d\r\n\r\n%s", size, output);
    fflush(stdout);
}

int main(int argc, char **argv) {
    private_namespace();
    if (argc > 1 && !strcmp(argv[1], "auth")) {
        const char *expected[] = {"auth", "token", "--hostname", "github.com", "--user", "synthetic-account", "--no-refresh"};
        require(argc == 8);
        for (int i=0; i<7; ++i) require(!strcmp(argv[i+1], expected[i]));
        env_is("GH_CONFIG_DIR", "/auth");
        require(getenv("COPILOT_GITHUB_TOKEN") == NULL);
        struct stat info;
        require(stat("/auth/hosts.yml", &info) == 0 && (info.st_mode & 0777) == 0400);
        require(open("/auth/hosts.yml", O_WRONLY) == -1);
        FILE *file = fopen("/auth/hosts.yml", "r");
        char marker[40];
        require(file != NULL && fgets(marker, sizeof(marker), file) != NULL &&
                !strcmp(marker, "synthetic hosts fixture\n"));
        fclose(file);
        puts("synthetic-private-token-0123");
        return 0;
    }
    require(argc == 13 && getenv("GH_CONFIG_DIR") == NULL && access("/auth/hosts.yml", F_OK) != 0);
    env_is("COPILOT_GITHUB_TOKEN", "synthetic-private-token-0123");
    env_is("COPILOT_HOME", "/home/catalog/copilot");
    const char *expected[] = {"--headless", "--stdio", "--no-auto-update", "--log-level", "none", "--no-auto-login",
        "--no-custom-instructions", "--disable-builtin-mcps", "--auth-token-env", "COPILOT_GITHUB_TOKEN",
        "--secret-env-vars", "COPILOT_GITHUB_TOKEN"};
    for (int i=0; i<12; ++i) require(!strcmp(argv[i+1], expected[i]));
    FILE *scratch = fopen("/home/catalog/local", "w");
    require(scratch != NULL);
    fclose(scratch);
    message("connect", 1, "{\"protocolVersion\":3}");
    message("auth.getStatus", 2, "{\"isAuthenticated\":true,\"login\":\"synthetic-account\",\"host\":\"github.com\"}");
    message("models.list", 3, "{\"models\":[{\"id\":\"auto\",\"name\":\"Auto\"}]}");
    require(getchar() == EOF);
    return 0;
}
'''


def exact_mount(source, target):
    subprocess.run(['/usr/bin/mount', '--bind', str(source), str(target)], check=True, timeout=5)


def main():
    with TemporaryDirectory(prefix='df-copilot-host-') as name:
        root = Path(name)
        home, projects = root/'synthetic-home', root/'projects'
        home.mkdir()
        projects.mkdir()
        store = DraftStore(root/'state', [projects])
        denied = projects/'denied'
        denied.write_text('synthetic denied')
        source, image = root/'capsule.c', root/'capsule'
        source.write_text(FIXTURE, encoding='utf-8')
        subprocess.run(['/usr/bin/cc', '-std=c11', '-O2', '-static', '-Werror',
                        '-DDENIED_PATH=' + json.dumps(str(denied)), str(source), '-o', str(image)],
                       check=True, timeout=15)
        env = {'HOME':str(home),'XDG_CONFIG_HOME':str(home/'config'),
               'GH_CONFIG_DIR':str(home/'gh'),'GH_TOKEN':'synthetic-inherited-must-not-pass',
               'SSH_AUTH_SOCK':'/synthetic','DBUS_SESSION_BUS_ADDRESS':'/synthetic'}
        with patch.dict(os.environ, env, clear=True):
            config, value = make_profile(root, gh=image, copilot=image)
            catalog = CopilotCatalog.from_path(config,store,transport=partial(refresh_metadata,network=False))
            assert catalog.get()['code'] == 'COPILOT_NOT_DISCOVERED'
            result = catalog.refresh(PROFILE)
            assert result[0] == 200 and result[1]['models'] == [{'id':'auto','name':'Auto'}], (result[0],result[1]['code'])
            assert catalog.get() == result[1]
            print('PASS sealed synthetic ELF and credential, broker and RPC both in actual loopback-only namespaces')
            print('PASS exact three real stdio methods, fixed no-refresh, private scratch and no project access')

            default = home/'.config/gh'
            default.mkdir(parents=True)
            hidden = projects/'nested'
            hidden.mkdir()
            state_child = store.state/'nested'
            state_child.mkdir()
            for origin in (default, hidden, state_child):
                file = origin/'hosts.yml'
                file.write_bytes(b'synthetic alias\n')
                file.chmod(0o600)
                alias = root/'alias'
                alias.mkdir()
                exact_mount(origin, alias)
                try:
                    altered = dict(value, github_hosts_file=str(alias/'hosts.yml'),
                                   github_hosts_sha256=hashlib.sha256(b'synthetic alias\n').hexdigest())
                    config.write_text(json.dumps(altered), encoding='utf-8')
                    refused = CopilotCatalog.from_path(config,store)
                    assert refused.get()['code']=='COPILOT_UNAVAILABLE'
                finally:
                    subprocess.run(['/usr/bin/umount',str(alias)], check=True, timeout=5)
                    alias.rmdir()
            print('PASS real default and nested protected directory bind aliases rejected')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
