"""Synthetic protocol mechanics behind the disabled #57 public read entry.

These helpers do not establish that a capability is enabled or accepted.
Public read/terminal refusal is tested separately in test_issue_remediation.
"""
from studio.local_codex_adapter import LocalCodex as PublicLocalCodex
from studio.native_terminals import NativeTerminals as PublicNativeTerminals
from devflower_native_terminal import _native_cli_argv as native_cli_argv
class LocalCodex(PublicLocalCodex):
    start=PublicLocalCodex._start_unshared
class NativeTerminals(PublicNativeTerminals):
    prepare=PublicNativeTerminals._prepare_supported
