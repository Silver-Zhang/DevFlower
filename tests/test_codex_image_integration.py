"""Actual CLI + synthetic provider; own static helpers and own package copies."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest
from unittest.mock import patch

from orchestration.common import Blocked
from studio.local_codex_adapter import LocalCodex
from studio.native_codex_image import NativeImage,binary_digest,EXECUTABLE_ALIAS
from studio.shared_codex_config import shared_conversation_config
from studio import codex_narrow_probe
import test_codex_narrow_integration as narrow_fixture
from test_native_codex_image import compile_static


class ImageIntegrationTests(unittest.TestCase):
    setUp=narrow_fixture.NarrowCodexTests.setUp
    record_synthetic_evidence=narrow_fixture.NarrowCodexTests.record_synthetic_evidence
    run_read=narrow_fixture.NarrowCodexTests.run_read

    def test_real_npm_shim_is_rejected_before_probe_credentials_or_spawn(self):
        shim=Path('/home/silver/.local/lib/node_modules/@openai/codex/bin/codex.js')
        # Read-only inspection of the installed launcher, never execute it.
        self.assertTrue(shim.is_file())
        own=Path(self.tmp.name)/'launcher.js';shutil.copy2(shim,own)
        with patch('studio.codex_narrow_probe._probe',side_effect=AssertionError('no probe')),patch('studio.local_codex_adapter.credentials',side_effect=AssertionError('no credentials')),patch('subprocess.Popen',side_effect=AssertionError('no spawn')):
            with self.assertRaisesRegex(Blocked,'启动器'):self.run_read('allowed.txt',executable=str(own))
            with self.assertRaisesRegex(Blocked,'启动器'):
                LocalCodex(dict(self.binding,executable=str(own),shared_conversation=True,no_tools=True),self.limits).start('SYNTHETIC',self.cwd,self.artifacts)
        self.assertEqual(self.calls,[])

    def swapped_after_probe(self,inplace):
        original=Path(self.binding['executable']);own=Path(self.tmp.name)/'native-copy';shutil.copy2(original,own)
        stub=Path(self.tmp.name)/'substitute';compile_static(stub,'#include <stdio.h>\nint main(){fputs("SYNTHETIC_CHILD_EXECUTED\\n",stderr);return 2;}\n')
        original_probe=codex_narrow_probe._probe;proof=[];real_spawn=subprocess.Popen
        err=self.artifacts/'swap-stderr.log'
        def swap(*args,**kwargs):
            result=original_probe(*args,**kwargs);proof.append(result)
            if inplace:own.write_bytes(stub.read_bytes())
            else:
                other=own.with_suffix('.new');shutil.copy2(stub,other);other.replace(own)
            return result
        with err.open('wb') as log:
            def spawn(*args,**kwargs):
                if kwargs.get('stderr')==subprocess.DEVNULL:kwargs['stderr']=log
                return real_spawn(*args,**kwargs)
            with patch.object(codex_narrow_probe,'_probe',side_effect=swap),patch('studio.native_codex_rpc.subprocess.Popen',side_effect=spawn):
                with self.assertRaisesRegex(Blocked,'配置.*变化'):self.run_read('allowed.txt',executable=str(own))
        self.assertEqual(len(proof),1);self.assertTrue(proof[0]['actual_dynamic_read'])
        self.assertNotIn('SYNTHETIC_CHILD_EXECUTED',err.read_text());self.assertEqual(self.calls,[])

    def test_post_probe_atomic_native_replacement_never_executes_replacement(self):
        self.swapped_after_probe(False)

    def test_post_probe_in_place_native_rewrite_never_executes_replacement(self):
        self.swapped_after_probe(True)

    def test_rpc_metadata_uses_snapshot_even_if_path_swaps_at_popen(self):
        from studio.native_codex_rpc import CodexRPC
        original=Path(self.binding['executable']);own=Path(self.tmp.name)/'metadata-copy';shutil.copy2(original,own)
        with NativeImage(own) as image:
            real=subprocess.Popen;seen=[]
            def spawn(*args,**kwargs):
                own.write_bytes(b'#!/bin/sh\necho SYNTHETIC_METADATA_EXECUTED >&2\nexit 91\n')
                seen.append(kwargs.get('executable'));return real(*args,**kwargs)
            with patch('studio.native_codex_rpc.subprocess.Popen',side_effect=spawn):
                with CodexRPC(str(own),self.home,self.config,self.cwd,15,executable_image=image) as rpc:
                    result=rpc.request('config/read',dict(cwd=str(self.cwd),includeLayers=False))
            self.assertEqual(result['config']['model_provider'],'fixture')
            self.assertEqual(seen,['/proc/self/fd/'+str(image.fd)])
        self.assertEqual(self.calls,[])

    def test_namespace_self_helper_uses_private_readonly_snapshot(self):
        exe=Path(self.tmp.name)/'self-helper'
        compile_static(exe,r'''#include <stdio.h>
#include <unistd.h>
#include <string.h>
#include <errno.h>
#include <sys/statvfs.h>
int main(int argc,char **argv){
char self[4096];ssize_t n=readlink("/proc/self/exe",self,sizeof(self)-1);if(n<0)return 10;self[n]=0;
if(strcmp(self,"/run/devflower-cli/codex"))return 11;
FILE *f=fopen(self,"r+");if(f){fclose(f);return 12;}
if(errno!=EROFS && errno!=ETXTBSY && errno!=EACCES)return 13;
struct statvfs fs;if(statvfs(self,&fs) || !(fs.f_flag&ST_RDONLY))return 15;
if(argc==2 && !strcmp(argv[1],"--helper")){puts("SYNTHETIC_PINNED_SELF_HELPER");return 0;}
execl(self,self,"--helper",NULL);return 14;}
''')
        b=dict(self.binding,executable=str(exe),shared_conversation=True,no_tools=True)
        with NativeImage(exe) as image,shared_conversation_config(b,self.cwd,self.artifacts,executable_image=image) as pinned:
            exe.write_bytes(b'#!/bin/sh\necho SYNTHETIC_BAD_HELPER\n')
            result=subprocess.run(pinned.wrap([str(exe)]),pass_fds=pinned.pass_fds,capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr.decode())
            self.assertEqual(result.stdout.decode().strip(),'SYNTHETIC_PINNED_SELF_HELPER')
        self.assertEqual(self.calls,[])
