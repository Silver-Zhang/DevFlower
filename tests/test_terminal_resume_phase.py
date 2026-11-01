"""Terminal phase is a conservative UI projection, not an execution gate."""
import unittest
from studio.terminal_phase import terminal_phase, _visible_layout

class TerminalResumeTests(unittest.TestCase):
    def test_resume_requires_fresh_prompt_and_visible_footer(self):
        for text in ('Resuming session…\n› Ask Codex to do anything',
                     '› Ask Codex to do anything\nproxy-model · high · /workspace\nResuming session…',
                     'Resuming session…\nquoted: › Ask Codex to do anything\nproxy-model · high · /workspace',
                     'Resuming session…\n› Ask Codex to do anything\n/model settings',
                     'Resuming session…\n› Ask Codex to do anything\nproxy-model · high · /workspace\nSettings\nChoose an option'):
            with self.subTest(text=text): self.assertEqual(terminal_phase('codex',text,True)['phase'],'resuming')
        text='Resuming session…\n› Ask Codex to do anything\nproxy-model · high · /workspace\n'
        self.assertEqual(terminal_phase('codex',text,True)['phase'],'ready')
        self.assertEqual(terminal_phase('codex','Resuming session…\r\x1b[32m› Ask Codex to do anything\x1b[0m\rproxy-alias · xhigh · ~/项目\r',True)['phase'],'ready')
        self.assertEqual(terminal_phase('codex','Resuming session…\n› Ask Codex to do anything\nproxy/opaque xhigh · 99% context left · ~/project\n',True)['phase'],'ready')
        self.assertEqual(terminal_phase('codex',text+'\nDo you trust the files',True)['phase'],'trust')

    def test_resume_accepts_multitoken_model_display_with_effort_and_cwd(self):
        prompt='Resuming session…\n[原有历史]\nWorked for 11s · 17:05\n› Ask Codex to do anything\n'
        footer='GPT-6.1 Terra high · /home/workspace/claude_workspace/DevFlower测试/guided-stable/flows-stage27-codex-workspace'
        self.assertEqual(terminal_phase('codex',prompt+footer+'\n',True)['phase'],'ready')
        self.assertEqual(terminal_phase('codex',prompt+'another display model xhigh · ~/project\n',True)['phase'],'ready')
        for tail in ('GPT-6.1 Terra high', 'GPT-6.1 Terra · /workspace',
                     footer+'\nSettings\nChoose an option'):
            with self.subTest(tail=tail): self.assertEqual(terminal_phase('codex',prompt+tail,True)['phase'],'resuming')

    def test_csi_positioned_prompt_and_split_cjk_footer(self):
        prefix='Resuming session…\nSynthetic prior history\nWorked for 11s · 17:05'
        prompt='\x1b[32;1H \x1b[33;1H \x1b[34;1H\x1b[1m›\x1b[22m \x1b[2mAsk Codex to do anything'
        footer='\x1b[35;1H\x1b[22m \x1b[36;1H  \x1b[38;5;223;49mOpaque Multiword high\x1b[39;49m · \x1b[38;5;151;49m/home/example测\x1b[36;68H试\x1b[36;70H/workspace\x1b[39m\x1b[49m\x1b[0m\x1b[32;1H\x1b[0 q\x1b[32;1H \x1b[39m\x1b[49m\x1b[0m\x1b[34;3H\x1b[?25h\x1b[?2026l'
        self.assertEqual(terminal_phase('codex',prefix+prompt,True)['phase'],'resuming')
        self.assertEqual(terminal_phase('codex',prefix+prompt+footer,True)['phase'],'ready')
        self.assertEqual(terminal_phase('codex',prefix+prompt+footer+'\x1b[38;1HSettings\x1b[39;1HChoose an option',True)['phase'],'resuming')
        self.assertEqual(terminal_phase('codex',prefix+prompt+footer+'\x1b[38;1HDo you trust the files',True)['phase'],'trust')
        self.assertEqual(terminal_phase('codex',footer+'\nResuming session…'+prompt,True)['phase'],'resuming')
        quoted='Resuming session…\x1b[34;1Hquoted: › Ask Codex to do anything\x1b[36;1HOpaque Multiword high · /synthetic'
        self.assertEqual(terminal_phase('codex',quoted,True)['phase'],'resuming')
        self.assertEqual(_visible_layout('\x1b[36;68H测\x1b[36;2H试'), '\n测试')
