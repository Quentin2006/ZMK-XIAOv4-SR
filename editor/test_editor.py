import copy
import http.client
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

import model
import server

BASE_CONFIG = json.loads((model.ROOT / model.CONFIG).read_text())
BASE_KEYMAP = (model.ROOT / model.KEYMAP).read_text()


class ModelTests(unittest.TestCase):
    def test_all_310_bindings_round_trip(self):
        values = model.parse_keymap(BASE_KEYMAP)
        self.assertEqual(values, [layer["bindings"] for layer in BASE_CONFIG["firmware"]["layers"]])
        self.assertEqual(model.parse_keymap(model.render_keymap(BASE_KEYMAP, BASE_CONFIG["firmware"]["layers"])), values)

    def test_edit_changes_exactly_one_physical_binding(self):
        config = copy.deepcopy(BASE_CONFIG)
        config["firmware"]["layers"][3]["bindings"][54] = "&mkp MCLK"
        before = model.parse_keymap(BASE_KEYMAP)
        after_source = model.render_keymap(BASE_KEYMAP, config["firmware"]["layers"])
        after = model.parse_keymap(after_source)
        differences = [(layer, key) for layer in range(5) for key in range(62) if before[layer][key] != after[layer][key]]
        self.assertEqual(differences, [(3, 54)])
        def outside(source):
            spans = model.binding_spans(source)
            return "".join(source[a:b] for a,b in zip([0]+[x[1] for x in spans], [x[0] for x in spans]+[len(source)]))
        self.assertEqual(outside(BASE_KEYMAP), outside(after_source))

    def test_geometry_uses_all_unique_matrix_positions(self):
        geometry = json.loads((model.ROOT / "editor/static/geometry.json").read_text())["keys"]
        self.assertEqual(sorted(x["i"] for x in geometry), list(range(62)))
        self.assertEqual([sum(x["side"]==side for x in geometry) for side in [0,1]], [32,30])
        matrix = (model.ROOT / model.MATRIX).read_text().split("map = <",1)[1].split(">;",1)[0]
        coordinates = re.findall(r"RC\((\d+),(\d+)\)", matrix)
        self.assertEqual(len(coordinates), 62)
        self.assertEqual(len(set(coordinates)), 62)

    def test_source_injection_and_invalid_bindings_rejected(self):
        for bad in ["&kp A>; / { injected = <1>; };", "&kp A\n&bootloader", "&mo 10", "&mkp BAD", "&bt BT_SEL 8", "&exec touch", "&kp LC(C))"]:
            with self.subTest(binding=bad), self.assertRaises(model.Invalid):
                model.validate_binding(bad)
        self.assertEqual(model.validate_binding("&kp LC(LS(C))"), "&kp LC(LS(C))")

    def test_hardware_identity_and_sensor_ranges_protected(self):
        for path,value in [(('hardware','keyCount'),60), (('firmware','trackball','cpi'),650), (('firmware','trackball','autoMouseTimeoutMs'),0), (('hyprland','scrollFactor'),float('nan')), (('hyprland','swapSlashes'),1)]:
            config=copy.deepcopy(BASE_CONFIG)
            target=config
            for field in path[:-1]: target=target[field]
            target[path[-1]]=value
            with self.subTest(path=path), self.assertRaises(model.Invalid): model.validate(config)

    def test_missing_or_ambiguous_layer_does_not_render(self):
        for source in [BASE_KEYMAP.replace("default_layer {", "other {"), BASE_KEYMAP + BASE_KEYMAP]:
            with self.assertRaises(model.Invalid): model.render_keymap(source, BASE_CONFIG["firmware"]["layers"])

    def test_json_integer_normalization_does_not_change_desktop_source(self):
        reference=copy.deepcopy(BASE_CONFIG)
        reference['hyprland'].update(scrollFactor=1.0,sensitivity=0.0)
        config=copy.deepcopy(reference)
        config['hyprland']['scrollFactor']=1
        config['hyprland']['sensitivity']=0
        self.assertEqual(model.generated(config)[model.HOST], model.generated(reference)[model.HOST])

    def test_historical_desktop_profile_migrates_without_assuming_flashed_cpi(self):
        config=copy.deepcopy(BASE_CONFIG)
        del config['hyprland']['declaredCpi'];del config['hyprland']['accelProfile']
        migrated=model.validate(config)
        self.assertEqual(migrated['hyprland']['declaredCpi'],0)
        self.assertEqual(migrated['hyprland']['accelProfile'],'adaptive')
        self.assertNotIn('MOUSE_DPI=',model.render_hwdb(migrated))

    def test_dpi_and_acceleration_reject_invalid_values(self):
        for key,value in [('declaredCpi',True),('declaredCpi',650),('declaredCpi',3300),('accelProfile','flat"; os.execute("bad")')]:
            config=copy.deepcopy(BASE_CONFIG);config['hyprland'][key]=value
            with self.subTest(key=key,value=value),self.assertRaises(model.Invalid):model.validate(config)

    @unittest.skipUnless(shutil.which('systemd-hwdb'),'systemd-hwdb is not installed')
    def test_dpi_hwdb_compiles_and_matches_only_skree_pointers(self):
        config=copy.deepcopy(BASE_CONFIG);config['hyprland']['declaredCpi']=3200
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);target=root/'etc/udev/hwdb.d/99-skree-control.hwdb'
            target.parent.mkdir(parents=True);target.write_text(model.render_hwdb(config))
            subprocess.run(['systemd-hwdb','--root',folder,'update'],check=True,capture_output=True)
            for match in model.HWDB_MATCHES:
                query=match.removesuffix('*')
                result=subprocess.run(['systemd-hwdb','--root',folder,'query',query],text=True,capture_output=True)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertIn('MOUSE_DPI=3200',result.stdout)
            for query in ['mouse:usb:v1234p5678:name:Other Mouse:', 'mouse:bluetooth:v0000p615e:name:Skree Custom Mouse:']:
                result=subprocess.run(['systemd-hwdb','--root',folder,'query',query],text=True,capture_output=True)
                self.assertNotIn('MOUSE_DPI=',result.stdout)


class LocalGitWorkspace(server.Workspace):
    """Execute real commits and pushes against a disposable local bare remote."""
    def git(self,*args,timeout=30):
        if args and args[0]=='push':
            if getattr(self,'fail_push',False): raise model.Invalid('Simulated network failure')
            result=subprocess.run(['git','-C',str(self.root),'push',str(self.bare),'HEAD:refs/heads/'+server.BRANCH],capture_output=True,text=True,timeout=timeout)
            if result.returncode: raise model.Invalid(result.stderr)
            super().git('update-ref','refs/remotes/origin/'+server.BRANCH,self.head())
            return result.stdout
        return super().git(*args,timeout=timeout)


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)/'repo';self.root.mkdir()
        for relative in model.MANAGED+[model.KEYMAP_XKB]:
            path=self.root/relative;path.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(model.ROOT/relative,path)
        (self.root/'.gitignore').write_text('.local-state/\n')
        self.run_git('init','-b',server.BRANCH)
        self.run_git('config','user.name','Editor Test');self.run_git('config','user.email','test@example.invalid')
        self.run_git('add','.');self.run_git('commit','-m','Initial test configuration')
        self.run_git('remote','add','origin',server.REPO_URL)
        self.workspace=LocalGitWorkspace(self.root)
        self.workspace.bare=Path(self.temp.name)/'remote.git'
        subprocess.run(['git','init','--bare',str(self.workspace.bare)],check=True,capture_output=True)

    def tearDown(self): self.temp.cleanup()

    def run_git(self,*args):
        result=subprocess.run(['git','-C',str(self.root),*args],capture_output=True,text=True,check=True)
        return result.stdout.strip()

    def payload(self):
        config=copy.deepcopy(BASE_CONFIG);config['firmware']['layers'][3]['bindings'][54]='&mkp MCLK'
        return {'config':config,'revision':self.workspace.revision(),'message':'Add middle click to Mouse layer Backspace'}

    def test_save_commits_generated_files_and_pushes_same_commit(self):
        before=self.workspace.head()
        result=self.workspace.save(self.payload())
        self.assertTrue(result['pushed']);self.assertNotEqual(before,result['commit'])
        remote=subprocess.check_output(['git','--git-dir',str(self.workspace.bare),'rev-parse','refs/heads/'+server.BRANCH],text=True).strip()
        self.assertEqual(remote,result['commit'])
        self.assertEqual(model.parse_keymap((self.root/model.KEYMAP).read_text())[3][54],'&mkp MCLK')
        self.assertFalse(self.workspace.state()['dirty'])
        self.assertFalse(self.workspace.state()['sourceDrift'])

    def test_stale_revision_is_rejected_without_overwriting_newer_files(self):
        payload=self.payload()
        original=(self.root/model.CONFIG).read_bytes()
        self.run_git('commit','--allow-empty','-m','Concurrent update')
        with self.assertRaises(server.Conflict): self.workspace.save(payload)
        self.assertEqual((self.root/model.CONFIG).read_bytes(),original)

    def test_uncommitted_files_and_wrong_remote_are_rejected(self):
        (self.root/'other.txt').write_text('Unrelated work')
        with self.assertRaises(model.Invalid): self.workspace.save(self.payload())
        (self.root/'other.txt').unlink()
        self.run_git('remote','set-url','origin','https://github.com/other/repo')
        with self.assertRaises(model.Invalid): self.workspace.save(self.payload())

    def test_failed_push_preserves_local_commit_for_retry(self):
        self.workspace.fail_push=True
        result=self.workspace.save(self.payload())
        self.assertFalse(result['pushed']);self.assertEqual(result['commit'],self.workspace.head())
        self.assertIn('network',result['pushError'])
        self.assertTrue(self.workspace.state()['pendingPush'])
        self.workspace.fail_push=False
        self.assertTrue(self.workspace.push({'revision':self.workspace.revision()})['pushed'])
        self.assertFalse(self.workspace.state()['pendingPush'])

    def test_save_and_apply_reports_partial_failures_without_losing_commit(self):
        home=Path(self.temp.name)/'home'
        hypr=home/'.config/hypr';hypr.mkdir(parents=True)
        inputs=hypr/'input.lua';inputs.write_text('-- preserve user settings\n')
        module=hypr/'skree-control.lua'
        actual_run=subprocess.run
        for fail_push, fail_apply in [(False,False),(True,False),(False,True),(True,True)]:
            with self.subTest(fail_push=fail_push,fail_apply=fail_apply):
                module.write_text('-- previous working desktop\n')
                old_inputs=inputs.read_bytes()
                self.workspace.fail_push=fail_push
                payload=self.payload();payload['applyDesktop']=True
                current=json.loads((self.root/model.CONFIG).read_text())
                payload['config']=copy.deepcopy(current)
                payload['config']['hyprland']['naturalScroll']=not current['hyprland']['naturalScroll']
                def run(command,*args,**kwargs):
                    if command[0]=='xkbcli':return subprocess.CompletedProcess(command,0,'','')
                    if command[0]=='hyprctl':return subprocess.CompletedProcess(command,0,'invalid profile' if fail_apply and command[1]=='configerrors' else '', '')
                    return actual_run(command,*args,**kwargs)
                with patch.object(model,'ROOT',self.root),patch.object(Path,'home',return_value=home),patch.object(server.subprocess,'run',side_effect=run):
                    result=self.workspace.save(payload)
                self.assertEqual(result['pushed'],not fail_push)
                self.assertEqual(bool(result['applyError']),fail_apply)
                self.assertEqual(result['state']['desktopApplied'],not fail_apply)
                self.assertEqual(result['commit'],self.workspace.head())
                self.assertFalse(result['state']['dirty'])
                self.assertEqual(json.loads((self.root/model.CONFIG).read_text())['hyprland'],payload['config']['hyprland'])
                if fail_apply:
                    self.assertEqual(module.read_text(),'-- previous working desktop\n')
                    self.assertEqual(inputs.read_bytes(),old_inputs)
                else:
                    self.assertEqual(module.read_text(),model.render_host(payload['config']))

    def test_firmware_only_save_skips_reload_if_desktop_already_applied(self):
        home=Path(self.temp.name)/'home';hypr=home/'.config/hypr';hypr.mkdir(parents=True)
        (hypr/'input.lua').write_text('require("hypr.skree-control")\n')
        (hypr/'skree-control.lua').write_text(model.render_host(BASE_CONFIG))
        (hypr/'keymaps').mkdir()
        shutil.copyfile(self.root/model.KEYMAP_XKB,hypr/'keymaps/skree-swapped-slashes.xkb')
        payload=self.payload();payload['applyDesktop']=True
        with patch.object(model,'ROOT',self.root),patch.object(Path,'home',return_value=home),patch.object(self.workspace,'apply',side_effect=AssertionError('Unnecessary reload')):
            result=self.workspace.save(payload)
        self.assertTrue(result['state']['desktopApplied'])
        self.assertIsNone(result['applyError'])

    def test_invalid_apply_flag_is_rejected_before_commit(self):
        payload=self.payload();payload['applyDesktop']='yes'
        before=self.workspace.head()
        with self.assertRaises(model.Invalid):self.workspace.save(payload)
        self.assertEqual(before,self.workspace.head())
        self.assertFalse(self.workspace.state()['dirty'])

    def test_failed_commit_restores_original_files_and_index(self):
        original={name:(self.root/name).read_bytes() for name in model.MANAGED}
        hook=self.root/'.git/hooks/pre-commit';hook.write_text('#!/bin/sh\nexit 1\n');hook.chmod(0o700)
        with self.assertRaises(model.Invalid): self.workspace.save(self.payload())
        self.assertEqual(original,{name:(self.root/name).read_bytes() for name in model.MANAGED})
        self.assertEqual(self.run_git('status','--porcelain'),'')

    def test_committed_manual_source_drift_cannot_be_silently_overwritten(self):
        path=self.root/model.KEYMAP;path.write_text(path.read_text().replace('&kp ESC','&kp F2',1))
        self.run_git('add','.');self.run_git('commit','-m','Manual keymap edit')
        self.assertIn(model.KEYMAP,self.workspace.state()['sourceDrift'])
        with self.assertRaises(model.Invalid):self.workspace.save(self.payload())

    def test_restore_only_accepts_known_configuration_history(self):
        result=self.workspace.restore({'sha':self.workspace.head()})
        self.assertEqual(result['config'],BASE_CONFIG)
        with self.assertRaises(model.Invalid): self.workspace.restore({'sha':'../../etc/passwd'})

    def test_desktop_apply_rolls_back_when_hyprland_rejects_profile(self):
        home=Path(self.temp.name)/'home'
        hypr=home/'.config/hypr';hypr.mkdir(parents=True)
        inputs=hypr/'input.lua';inputs.write_text('-- unrelated setting\nhl.gesture({ fingers = 4 })\n')
        module=hypr/'skree-control.lua';module.write_text('-- previous working profile\n')
        xkb=hypr/'keymaps/skree-swapped-slashes.xkb';xkb.parent.mkdir();xkb.write_text('old keymap')
        original={p:p.read_bytes() for p in [inputs,module,xkb]}
        actual_run=subprocess.run
        def run(command,*args,**kwargs):
            if command[0]=='xkbcli':return subprocess.CompletedProcess(command,0,'','')
            if command[0]=='hyprctl':return subprocess.CompletedProcess(command,0,'invalid test setting' if command[1]=='configerrors' else 'ok','')
            return actual_run(command,*args,**kwargs)
        revision=self.workspace.revision()
        with patch.object(model,'ROOT',self.root),patch.object(Path,'home',return_value=home),patch.object(server.subprocess,'run',side_effect=run):
            with self.assertRaises(model.Invalid): self.workspace.apply({'revision':revision})
        self.assertEqual(original,{p:p.read_bytes() for p in original})

    def test_desktop_apply_migrates_only_skree_blocks_and_keeps_user_settings(self):
        home=Path(self.temp.name)/'home'
        hypr=home/'.config/hypr';hypr.mkdir(parents=True)
        unrelated='-- user setting before\nhl.gesture({ fingers = 4 })\n'
        skree='''-- Skree workaround: the working backslash key types slash.
local skree_slash_keymap = "existing.xkb"
hl.device({ name = "skree-custom-keyboard", kb_file = skree_slash_keymap })
hl.device({ name = "zmk-project-skree-custom-keyboard", kb_file = skree_slash_keymap })
-- Skree trackball: hold right-click and roll to scroll.
for _, trackball_name in ipairs({ "skree-custom-mouse" }) do
  hl.device({ name = trackball_name, scroll_button = 273 })
end
'''
        after='-- user setting after\nhl.gesture({ fingers = 3 })\n'
        inputs=hypr/'input.lua';inputs.write_text(unrelated+skree+after)
        actual_run=subprocess.run
        def run(command,*args,**kwargs):
            if command[0] in {'xkbcli','hyprctl'}:return subprocess.CompletedProcess(command,0,'','')
            return actual_run(command,*args,**kwargs)
        revision=self.workspace.revision()
        with patch.object(model,'ROOT',self.root),patch.object(Path,'home',return_value=home),patch.object(server.subprocess,'run',side_effect=run):
            result=self.workspace.apply({'revision':revision})
            self.assertTrue(result['applied'])
            self.assertTrue(result['state']['desktopApplied'])
            first=inputs.read_bytes()
            self.workspace.apply({'revision':revision})
            self.assertEqual(inputs.read_bytes(),first)
        self.assertTrue(inputs.read_text().startswith(unrelated+after))
        self.assertEqual(inputs.read_text().count('require("hypr.skree-control")'),1)
        self.assertEqual((hypr/'skree-control.lua').read_text(),model.render_host(BASE_CONFIG))

    def test_http_requires_local_host_origin_and_csrf(self):
        workspace=self.workspace
        class TestHandler(server.Handler):
            def log_message(self,*args):pass
        TestHandler.workspace=workspace
        http_server=server.ThreadingHTTPServer(('127.0.0.1',0),TestHandler)
        thread=threading.Thread(target=http_server.serve_forever,daemon=True);thread.start()
        port=http_server.server_port
        def request(method,path,body=None,headers=None):
            connection=http.client.HTTPConnection('127.0.0.1',port)
            connection.request(method,path,body,headers or {})
            response=connection.getresponse();data=response.read();status=response.status;connection.close();return status,json.loads(data)
        try:
            self.assertEqual(request('GET','/api/state',headers={'Host':'evil.invalid'})[0],403)
            self.assertEqual(request('POST','/api/save','{}',{'Origin':'https://evil.invalid','Content-Type':'application/json','X-Skree-CSRF':workspace.csrf})[0],403)
            self.assertEqual(request('POST','/api/save','{}',{'Origin':f'http://127.0.0.1:{port}','Content-Type':'application/json'})[0],403)
            headers={'Origin':f'http://127.0.0.1:{port}','Content-Type':'application/json','X-Skree-CSRF':workspace.csrf}
            result=request('POST','/api/preview',json.dumps(self.payload()),headers)
            self.assertEqual(result[0],200)
            self.assertEqual(len(result[1]['changes']),1)
            self.assertFalse(workspace.state()['dirty'])
        finally:http_server.shutdown();http_server.server_close();thread.join()


if __name__=='__main__':unittest.main()
