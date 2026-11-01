"""Synthetic native programs only: immutable execution and rejection evidence."""
import fcntl
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from orchestration.common import Blocked
from studio.native_codex_image import NativeImage,binary_digest,SEALS


def compile_static(target,source):
    c=target.with_suffix('.c');c.write_text(source)
    subprocess.run(['/usr/bin/cc','-static','-O2','-o',str(target),str(c)],check=True,capture_output=True)


class NativeImageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory();cls.addClassCleanup(cls.tmp.cleanup)
        cls.root=Path(cls.tmp.name);cls.original=cls.root/'original';cls.other=cls.root/'other'
        compile_static(cls.original,'#include <stdio.h>\nint main(){puts("SYNTHETIC_ORIGINAL");return 0;}\n')
        compile_static(cls.other,'#include <stdio.h>\nint main(){puts("SYNTHETIC_REPLACED");return 0;}\n')

    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        self.exe=Path(self.folder.name)/'cli';shutil.copy2(self.original,self.exe)

    def execute(self,image):
        image.validate()
        return subprocess.check_output(['/proc/self/fd/'+str(image.fd)],pass_fds=(image.fd,)).decode().strip()

    def test_atomic_replace_cannot_change_actual_exec(self):
        with NativeImage(self.exe) as image:
            replacement=self.exe.with_suffix('.next');shutil.copy2(self.other,replacement);replacement.replace(self.exe)
            self.assertEqual(self.execute(image),'SYNTHETIC_ORIGINAL')
            with self.exe.open('rb') as current:self.assertNotEqual(binary_digest(current.fileno()),image.sha256)

    def test_in_place_rewrite_cannot_change_actual_exec(self):
        with NativeImage(self.exe) as image:
            self.exe.write_bytes(self.other.read_bytes())
            self.assertEqual(self.execute(image),'SYNTHETIC_ORIGINAL')

    def test_unlink_after_snapshot_cannot_change_actual_exec(self):
        with NativeImage(self.exe) as image:
            self.exe.unlink();self.assertEqual(self.execute(image),'SYNTHETIC_ORIGINAL')

    def test_kernel_rejects_write_resize_and_unsealing(self):
        with NativeImage(self.exe) as image:
            self.assertEqual(fcntl.fcntl(image.fd,fcntl.F_GET_SEALS),SEALS)
            for operation in (lambda:os.pwrite(image.fd,b'X',0),lambda:os.ftruncate(image.fd,0),lambda:fcntl.fcntl(image.fd,fcntl.F_ADD_SEALS,0x10)):
                with self.assertRaises(OSError):operation()
            self.assertEqual(self.execute(image),'SYNTHETIC_ORIGINAL')

    def test_script_and_dynamic_interpreter_rejected_without_spawn(self):
        script=self.exe.with_suffix('.js');script.write_text('#!/usr/bin/node\nthrow new Error("MUST_NOT_EXECUTE")');script.chmod(0o700)
        with patch('subprocess.Popen',side_effect=AssertionError('no spawn')):
            for path in (script,'/usr/bin/true'):
                with self.subTest(path=path),self.assertRaises(Blocked):NativeImage(path)

    def test_bad_program_headers_rejected_without_spawn(self):
        raw=bytearray(self.original.read_bytes());struct.pack_into('<Q',raw,32,len(raw)+1);self.exe.write_bytes(raw)
        with patch('subprocess.Popen',side_effect=AssertionError('no spawn')),self.assertRaises(Blocked):NativeImage(self.exe)

    def test_dt_needed_even_without_interpreter_is_rejected(self):
        raw=bytearray(self.original.read_bytes());phoff=struct.unpack_from('<Q',raw,32)[0]
        # Replace one non-load program header with a bounded dynamic table.
        count=struct.unpack_from('<H',raw,56)[0];idx=next(i for i in range(count) if struct.unpack_from('<I',raw,phoff+i*56)[0] not in (1,))
        offset=len(raw);raw.extend(struct.pack('<qQqQ',1,1,0,0));struct.pack_into('<IIQQQQQQ',raw,phoff+idx*56,2,4,offset,0,0,32,32,8);self.exe.write_bytes(raw)
        with self.assertRaisesRegex(Blocked,'动态依赖'):NativeImage(self.exe)

    def test_wrong_expected_bytes_fail_before_exec(self):
        with patch('subprocess.Popen',side_effect=AssertionError('no spawn')),self.assertRaises(Blocked):NativeImage(self.exe,expected_sha256='0'*64)

    def test_source_mutation_during_snapshot_fails_closed(self):
        original=os.pread;changed=[]
        # First hash read size is bounded by this small fixture's size.
        def race(fd,size,offset):
            value=original(fd,size,offset)
            if size==self.exe.stat().st_size and not changed:
                changed.append(True);self.exe.write_bytes(self.other.read_bytes())
            return value
        with patch('studio.native_codex_image.os.pread',side_effect=race),self.assertRaises(Blocked):NativeImage(self.exe)
        self.assertTrue(changed)

    def test_closed_snapshot_cannot_be_reused(self):
        image=NativeImage(self.exe);image.close()
        with self.assertRaises(Blocked):image.validate()

    def test_missing_seal_support_fails_closed_and_closes_fd(self):
        with patch('studio.native_codex_image.os.memfd_create',side_effect=OSError('synthetic unsupported')),self.assertRaises(Blocked):NativeImage(self.exe)
