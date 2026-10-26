"""Folder navigation, image dispatch and enforced native mounts. No model claims."""
import base64
import copy
import json
import os
from pathlib import Path
import struct
import tomllib
import unittest
from unittest.mock import patch
import zlib
from test_workbench_runtime import fixture
from orchestration.common import Blocked, canonical, digest
from orchestration.native_features import native_args,skill_mounts,verify_skill
from orchestration.process import execute
from studio.agent_extensions import AgentExtensions
from studio.assistant_images import AssistantImages,inspect
from studio.devflower_agent import DevFlowerAgent
from studio.graph import Invalid
from studio.model_admission import ModelAdmission


def png():
    def chunk(k,v):return struct.pack('>I',len(v))+k+v+struct.pack('>I',zlib.crc32(k+v)&0xffffffff)
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',1,1,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(b'\x00\xff\xff\xff'))+chunk(b'IEND',b'')


class WorkspaceExtensionTests(unittest.TestCase):
    def setUp(self):
        self.base,self.rt=fixture(self);self.w=self.base.project['id'];self.ext=AgentExtensions(self.rt)
        self.profile='default-codex';self.cap=patch('studio.agent_extensions.capabilities',return_value=dict(skills=True,subagents=True));self.cap.start();self.addCleanup(self.cap.stop)
    def skill(self,text='Use the checklist.',name='checklist'):
        p=self.base.source/'.agents/skills'/name;p.mkdir(parents=True);(p/'SKILL.md').write_text('---\nname: '+name+'\ndescription: Test checklist\n---\n'+text);return p
    def binding(self,**node):return self.rt.node_binding(self.w,dict(profile=self.profile,mode='Ask',**node),admission=False)['binding']
    def test_protected_parent_opens_records_without_execution_or_native_discovery(self):
        parent=str(self.rt.storage.parent);w=self.rt.open_workspace(parent)
        self.assertFalse(w['execution_allowed']);self.assertTrue(w['protected_container']);self.assertTrue(w['project_storage'])
        with self.assertRaises(Blocked):self.rt.validate_source(parent)
        self.assertEqual(self.rt.session_center.discover(w['id'],True)['discovery']['sessions'],[])
    def test_private_location_uses_hosted_records_and_preserves_real_data(self):
        before=set(self.rt.native.iterdir());w=self.rt.open_workspace(str(self.rt.native))
        self.assertEqual(w['record_location'],'host');self.assertEqual(set(self.rt.native.iterdir()),before)
        self.assertNotEqual(Path(w['project_storage']['path']).parent,self.rt.native)
    def test_system_directory_can_be_browsed_but_never_executed(self):
        row=self.rt.folder('/etc');self.assertTrue(row['openable']);self.assertFalse(row['execution_allowed'])
    def test_directory_symlink_is_canonicalized_without_authority(self):
        link=self.base.source.parent/'link';link.symlink_to(self.rt.native,target_is_directory=True)
        w=self.rt.open_workspace(str(link));self.assertEqual(w['path'],str(self.rt.native));self.assertFalse(w['execution_allowed'])
    def test_hidden_navigation_is_explicit(self):
        (self.base.source/'.hidden').mkdir()
        self.assertNotIn('.hidden',[v['name'] for v in self.rt.folder(str(self.base.source))['folders']])
        self.assertIn('.hidden',[v['name'] for v in self.rt.folder(str(self.base.source),True)['folders']])
    def test_skill_discovery_selection_and_complete_hash(self):
        self.skill();v=self.ext.catalog(self.w,self.profile);row=next(v for v in v['skills'] if v['name']=='checklist');b=self.binding(skills=[row['id']]);self.assertEqual(b['skills'][0]['sha256'],row['sha256']);verify_skill(b['skills'][0])
    def test_skill_policy_injection_and_links_are_unavailable(self):
        a=self.skill('x');(a/'SKILL.md').write_text('---\nname: a\nallowed-tools: Bash\n---\nx')
        b=self.skill('x','linked');(b/'secret').symlink_to('/etc/passwd')
        rows=[r for r in self.ext.catalog(self.w,self.profile)['skills'] if r['name'] in ('checklist','linked')]
        self.assertEqual(len(rows),2);self.assertTrue(all(not r['available'] for r in rows))
    def test_changed_skill_requires_refresh(self):
        p=self.skill();r=next(v for v in self.ext.catalog(self.w,self.profile)['skills'] if v['name']=='checklist');(p/'SKILL.md').write_text('changed')
        with self.assertRaises(Blocked):self.binding(skills=[r['id']])
    def test_snapshot_tamper_blocks_before_spawn(self):
        self.skill();r=next(v for v in self.ext.catalog(self.w,self.profile)['skills'] if v['name']=='checklist');b=self.binding(skills=[r['id']]);(Path(b['skills'][0]['path'])/'SKILL.md').write_text('tampered')
        with self.assertRaises(Blocked):skill_mounts(b,self.base.source,self.rt.storage)
    def test_skill_workspace_and_profile_cannot_be_swapped(self):
        self.skill();r=next(v for v in self.ext.catalog(self.w,self.profile)['skills'] if v['name']=='checklist')
        with self.assertRaises(Blocked):self.rt.node_binding(self.w,dict(profile='default-claude',skills=[r['id']]),admission=False)
    def test_readonly_skill_mount_is_real_and_unselected_project_skill_hidden(self):
        self.skill();r=next(v for v in self.ext.catalog(self.w,self.profile)['skills'] if v['name']=='checklist');b=self.binding(skills=[r['id']]);mounts,masks=skill_mounts(b,self.base.source,self.rt.storage)
        dest=Path(b['home'])/'.agents/skills'/r['id']/'SKILL.md'
        code="from pathlib import Path;p=Path("+repr(str(dest))+");assert 'checklist' in p.read_text();assert not list(Path("+repr(str(self.base.source/'.agents/skills'))+").iterdir());\ntry:p.write_text('bad')\nexcept OSError:pass\nelse:raise RuntimeError('Skill writable')"
        process,out,err=execute(['/usr/bin/python3','-c',code],str(self.base.source),b['home'],read=[str(self.base.source)],backend='bwrap',readonly_dirs=mounts,hidden_paths=masks)
        self.assertEqual(process['exit'],0,err.decode())
    def test_extensions_are_bound_to_model_admission(self):
        plain=self.binding();child=self.binding(subagents='readonly');gate=ModelAdmission(self.rt)
        self.assertNotEqual(gate.key(plain,str(self.base.source)),gate.key(child,str(self.base.source)))
        selected=dict(binding=child,cwd=str(self.base.source),workspace=self.w);request=gate.pending_check(selected)['selection'];self.assertEqual(request['subagents'],'readonly');self.assertEqual(gate.select(request)['binding']['subagents'],'readonly')
    def test_write_mode_subagents_refused(self):
        with self.assertRaises(Blocked):self.rt.node_binding(self.w,dict(profile=self.profile,mode='Agent',subagents='readonly'),admission=False)
    def test_native_arguments_are_parseable_and_child_model_is_pinned(self):
        b=self.binding(subagents='readonly');args,env=native_args(b)
        config=tomllib.loads('\n'.join(args[i+1] for i in range(0,len(args),2)))
        self.assertTrue(config['features']['multi_agent']);self.assertEqual(config['agents']['max_threads'],2)
        b['provider']='claude';args,env=native_args(b);self.assertEqual(env['CLAUDE_CODE_SUBAGENT_MODEL'],b['model']);self.assertEqual(env['CLAUDE_CODE_SUBAGENT_MODEL_FORCE'],'1');self.assertIn('--agents',args)
    def upload(self):return AssistantImages(self.rt).upload(dict(workspace=self.w,name='flow.png',data=base64.b64encode(png()).decode()))
    def test_image_upload_is_project_local_and_exact(self):
        m=self.upload();r=AssistantImages(self.rt).resolve(self.w,[m['id']]);self.assertEqual(base64.b64decode(r[0]['data']),png());self.assertEqual(m['width'],1)
    def test_image_cross_workspace_and_tamper_are_refused(self):
        m=self.upload();other=self.base.source.parent/'other';other.mkdir();w=self.rt.open_workspace(str(other))
        with self.assertRaises(Blocked):AssistantImages(self.rt).resolve(w['id'],[m['id']])
        p=self.rt.project_files(self.w).root/('image-'+m['id']+'.json');p.write_text('{}')
        with self.assertRaises(Blocked):AssistantImages(self.rt).resolve(self.w,[m['id']])
    def test_non_image_crc_and_path_uploads_are_refused(self):
        for raw in (b'<svg/>',png()[:-1],png()[:30]+b'bad'+png()[33:]):
            with self.subTest(raw=raw[:8]),self.assertRaises(Invalid):inspect(raw)
        with self.assertRaises(Invalid):AssistantImages(self.rt).upload(dict(workspace=self.w,name='../secret',data=''))
    def test_images_reach_both_real_protocol_shapes_without_base64_in_receipt(self):
        m=self.upload();images=AssistantImages(self.rt).resolve(self.w,[m['id']])
        for protocol in ('anthropic','openai'):
            client=DevFlowerAgent(self.rt);client.config=dict(protocol=protocol,model='test',effort='default',api_key='',fingerprint='test');client.images=images;client.conversation='test';client.intent='chat';sent=[]
            def request(method,path,data):
                sent.append(data)
                return (dict(content=[dict(type='text',text='image received')],model='test') if protocol=='anthropic' else dict(choices=[dict(message=dict(content='image received'))],model='test')),None
            client._request=request;r=client.chat('test','inspect selected flowchart');content=sent[0]['messages'][-1]['content'];self.assertEqual(len(content),2);self.assertEqual(r['images'],[m]);self.assertNotIn(images[0]['data'],json.dumps(r))

    def test_compact_image_loop_metadata_is_normalized_without_granting_permissions(self):
        from studio.canvas_assistant import expand_plan
        value=expand_plan(dict(nodes=[dict(id='s',kind='start',name='start'),dict(id='r',kind='result',name='result')],edges=[dict(source='s',target='r')]),{})
        self.assertEqual(value['version'],1);self.assertTrue(value['summary']);self.assertNotIn('permission',value['nodes'][0])

    def test_multiple_image_endpoints_preserve_branches_and_one_final_result(self):
        from studio.canvas_assistant import expand_plan,decode_reply
        from studio.workbench_examples import examples
        g=examples(self.rt.operator['profiles'])[0]
        # Compact tool plan: two terminal outcomes, not two execution authorities.
        p=dict(nodes=[dict(id='s',kind='start',name='start'),dict(id='agent',kind='agent',name='Agent',profile='default-codex'),dict(id='c',kind='condition',name='accepted?',test=dict(field='result.accepted',op='equals',value=True)),dict(id='yes',kind='result',name='yes'),dict(id='no',kind='result',name='no')],edges=[dict(source='s',target='agent'),dict(source='agent',target='c'),dict(source='c',target='yes',when='true'),dict(source='c',target='no',when='false')])
        b=self.rt.operator['profiles']['default-codex'];choices={'default-codex':dict(provider='codex',models=[dict(id=b['model'],efforts=[b['effort']])])}
        result,graph=decode_reply(json.dumps(dict(reply='未执行的分支草稿',plan=p)),choices)
        self.assertEqual(sum(n['kind']=='result' for n in graph['nodes']),1);self.assertEqual(sum(n['kind']=='output' for n in graph['nodes']),2)
        self.assertEqual({e['when'] for e in graph['edges'] if e['source']=='c'},{'true','false'})

    def test_child_tools_do_not_restore_tools_disabled_on_parent(self):
        b=self.binding(subagents='readonly');b.update(provider='claude',tools=['Grep'])
        args,env=native_args(b);worker=json.loads(args[1])['devflower-reader']
        self.assertEqual(worker['tools'],['Grep']);self.assertNotIn('Agent',worker['tools'])

    def test_refresh_does_not_reauthorize_a_changed_selected_skill(self):
        p=self.skill();old=next(v for v in self.ext.catalog(self.w,self.profile)['skills'] if v['name']=='checklist')
        (p/'resource.txt').write_text('new business resource')
        newer=next(v for v in self.ext.catalog(self.w,self.profile)['skills'] if v['name']=='checklist')
        self.assertNotEqual(old['id'],newer['id'])
        with self.assertRaises(Blocked):self.binding(skills=[old['id']])
        self.assertEqual(self.binding(skills=[newer['id']])['skills'][0]['sha256'],newer['sha256'])

    def test_disabled_switch_sets_both_native_codex_controls(self):
        args,env=native_args(self.binding());config=tomllib.loads('\n'.join(args[i+1] for i in range(0,len(args),2)))
        self.assertIs(config['features']['multi_agent'],False);self.assertIs(config['agents']['enabled'],False)

    def test_diagnostic_keeps_scope_but_cannot_read_business_skill_bytes(self):
        p=self.skill('PRIVATE_BUSINESS_SKILL_SENTINEL_20261027');row=next(v for v in self.ext.catalog(self.w,self.profile)['skills'] if v['name']=='checklist');b=self.binding(skills=[row['id']]);gate=ModelAdmission(self.rt);original=b['skills'][0];observed={};b.update(responsibility='PRIVATE_BUSINESS_ROLE_20261027',background='PRIVATE_BUSINESS_BACKGROUND_20261027')
        def diagnostic(cli,prompt,cwd,artifacts,*args,**kw):
            probe=cli.binding;self.assertNotIn('PRIVATE_BUSINESS',probe.get('responsibility','')+probe.get('background',''));mounts,masks=skill_mounts(probe,cwd,artifacts);dest=Path(probe['home'])/'.agents/skills'/row['id']/'SKILL.md'
            code='from pathlib import Path;import json;p=Path('+repr(str(dest))+');print(json.dumps({"private_visible":"PRIVATE_BUSINESS_SKILL_SENTINEL_20261027" in p.read_text(),"placeholder":"DIAGNOSTIC_ONLY" in p.read_text(),"original_visible":Path('+repr(str(Path(original['path'])/'SKILL.md'))+').exists()}))'
            process,out,err=execute(['/usr/bin/python3','-c',code],cwd,probe['home'],read=[str(cwd),str(artifacts)],backend='bwrap',readonly_dirs=mounts,hidden_paths=masks)
            self.assertEqual(process['exit'],0,err.decode());observed.update(json.loads(out));self.assertEqual(probe['skills'][0]['id'],row['id']);self.assertNotEqual(probe['skills'][0]['sha256'],original['sha256'])
            return dict(report=dict(reply=prompt.rsplit(' ',1)[-1]),process=dict(exit=0,timeout=False,overflow=False,cancelled=False),requested_model=probe['model'],requested_effort=probe['effort'],session_id='aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee')
        scope=gate.scope(b,str(self.base.source))
        with patch('studio.model_catalog.ModelCatalog.get',return_value=dict(authentication=dict(logged_in=True),discovery_complete=False)),patch('studio.model_admission.CLI.start',diagnostic):
            result=gate._verify(dict(binding=b,cwd=str(self.base.source),workspace=self.w,scope=scope),dict(actor='SYNTHETIC nonce envelope; real filesystem test, no model'))
        self.assertTrue(result['execution_allowed']);self.assertEqual(observed,dict(private_visible=False,placeholder=True,original_visible=False))
        cert=self.rt.db.get('model-admission',gate.key(b,str(self.base.source)));self.assertEqual(cert['scope'],scope);self.assertFalse(cert['receipt']['diagnostic_inputs']['business_skill_execution_tested'])

    def test_quoted_or_merged_skill_policy_and_invalid_metadata_are_rejected(self):
        from studio.agent_extensions import skill_header
        for extra in ['"allowed-tools": Bash','"context": fork','<<: {model: other}','model: other','name: duplicate']:
            with self.subTest(extra=extra),self.assertRaises(Blocked):skill_header('---\nname: test\ndescription: Valid\n'+extra+'\n---\nBody')
        for text in ['plain text','---\nname: test\n---\nBody','---\nname: INVALID NAME\ndescription: test\n---\nBody']:
            with self.subTest(text=text),self.assertRaises(Blocked):skill_header(text)
        parsed=skill_header('---\nname: valid-skill\ndescription: >\n  A valid multiline\n  description.\nmetadata:\n  author: test\n---\nBody')
        self.assertIn('multiline description',parsed['description'])
