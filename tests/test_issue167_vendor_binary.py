"""#167: resolve the npm package's vendored static Codex binary.

The layout rule is exercised with synthetic files; the real npm install is an
optional integration check on this machine (skipped elsewhere).
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'tools'), str(ROOT/'tests')]

from orchestration.common import Blocked  # noqa: E402
from studio import native_codex_image as image  # noqa: E402

VENDOR = Path('node_modules/@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl/bin/codex')


def fake_static(path):
    """The JS launcher is a script; only the vendored file is a static ELF."""
    if str(path).endswith('codex.js'):
        raise Blocked(image.SCRIPT_HINT)


class VendorLayoutTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)/'lib/node_modules/@openai/codex'
        (self.base/'bin').mkdir(parents=True)
        (self.base/'package.json').write_text(json.dumps({'name':'@openai/codex','version':'0.0.0'}))
        self.launcher = self.base/'bin/codex.js'
        self.launcher.write_text('#!/usr/bin/env node\n')
        self.launcher.chmod(0o755)
        self.vendor = self.base/VENDOR
        self.vendor.parent.mkdir(parents=True)
        self.vendor.write_bytes(b'\x7fELF' + b'\0'*60)
        self.vendor.chmod(0o755)
        patcher = patch.object(image, '_static_ok', fake_static)
        patcher.start(); self.addCleanup(patcher.stop)
        image._cached_digest.cache_clear()

    def resolve(self):
        with patch.object(image, 'binary_digest', lambda fd: 'a'*64):
            return image.resolve(self.launcher)

    def test_npm_launcher_resolves_to_the_vendored_binary(self):
        value = self.resolve()
        self.assertEqual(value['path'], str(self.vendor.resolve()))
        self.assertEqual(value['source'], 'npm package vendor binary')
        self.assertEqual(image.incompatibility(self.launcher), None)

    def test_symlinked_vendor_binary_is_refused(self):
        target = Path(self.vendor.parent.parent.parent)/'elsewhere'
        target.write_bytes(b'\x7fELF' + b'\0'*60)
        self.vendor.unlink(); self.vendor.symlink_to(target)
        self.assertIsNone(image.vendor_binary(self.launcher))

    def test_vendor_outside_the_package_is_refused(self):
        outside = Path(self.vendor.parent.parent.parent.parent.parent)/'outside'
        outside.parent.mkdir(parents=True, exist_ok=True)
        outside.write_bytes(b'\x7fELF' + b'\0'*60)
        self.vendor.unlink(); self.vendor.symlink_to(outside)
        self.assertIsNone(image.vendor_binary(self.launcher))

    def test_missing_vendor_keeps_the_actionable_refusal(self):
        self.vendor.unlink()
        with self.assertRaises(Blocked) as caught:
            image.resolve(self.launcher)
        self.assertIn('Claude Code', str(caught.exception))
        self.assertIn('脚本启动器', image.incompatibility(self.launcher))

    def test_launcher_without_package_json_is_not_resolved(self):
        (self.base/'package.json').unlink()
        self.assertIsNone(image.vendor_binary(self.launcher))


@unittest.skipUnless(Path('/home/silver/.local/lib/node_modules/@openai/codex/package.json').is_file(),
                     'real npm Codex install not present')
class InstalledNpmCodexTests(unittest.TestCase):
    def test_real_npm_codex_resolves_and_passes_static_elf(self):
        # Integration check on this machine only: never executes anything.
        value = image.resolve('/home/silver/.local/bin/codex')
        self.assertEqual(value['source'], 'npm package vendor binary')
        self.assertIn('/vendor/x86_64-unknown-linux-musl/bin/codex', value['path'])
        self.assertEqual(len(value['sha256']), 64)
        self.assertTrue(Path(value['path']).is_file())
        self.assertFalse(Path(value['path']).is_symlink())


if __name__ == '__main__':
    unittest.main()
