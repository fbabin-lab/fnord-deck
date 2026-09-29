from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from uuid import uuid4

sys.dont_write_bytecode = True
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from verify_package import verify
from cpu_demo import Client


class PackageTests(unittest.TestCase):
    def test_hash_inventory(self):
        manifest,fingerprint=verify(ROOT/'cpu')
        self.assertEqual(manifest['id'],'org.fnord.cpu')
        self.assertEqual(len(fingerprint),64)
        self.assertEqual(manifest['api'],{'min':'1.1','max':'1.1'})
        self.assertEqual(manifest['contributions'][0]['updateMode'],'poll')

    def test_tamper_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'cpu'; shutil.copytree(ROOT/'cpu',root)
            with (root/'cpu_usage.py').open('a') as f: f.write('\n# changed\n')
            with self.assertRaises(ValueError): verify(root)

    def test_unlisted_files_and_symlinks_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'cpu'; shutil.copytree(ROOT/'cpu',root)
            extra=root/'extra.py'; extra.write_text('')
            with self.assertRaises(ValueError): verify(root)
            extra.unlink(); extra.symlink_to('/etc/passwd')
            with self.assertRaises(ValueError): verify(root)

    def test_manifest_byte_change_changes_fingerprint(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'cpu'; shutil.copytree(ROOT/'cpu',root)
            before=verify(root)[1]
            with (root/'manifest.json').open('a') as f: f.write('\n')
            self.assertNotEqual(before,verify(root)[1])


class ProcessTests(unittest.TestCase):
    def test_real_multi_instance_protocol_and_shutdown(self):
        c=Client()
        try:
            c.initialize()
            contexts=[]
            target={'deviceId':'test','keyIndex':0,'width':96,'height':96,'locale':'en','displayFields':['text']}
            for label in ('Total A','Total B'):
                ctx={'instanceId':str(uuid4()),'instanceEpoch':str(uuid4())}
                c.call('instance.create',{**ctx,'contributionId':'usage','settingsVersion':1,
                       'settings':{'label':label},'secretNames':[],'target':target,'effectiveRefreshIntervalMs':1000})
                ctx['activationId']=str(uuid4())
                c.call('instance.visibility',{**ctx,'visible':True,'target':target})
                contexts.append(ctx)
            first=c.call('instance.refreshMany',{'instances':contexts,'deadlineMs':1000})
            self.assertTrue(all(x['state']['value'] is None for x in first['results']))
            time.sleep(1.05)
            second=c.call('instance.refreshMany',{'instances':contexts,'deadlineMs':1000})
            for r in second['results']:
                self.assertIsInstance(r['state']['value'],(int,float))
                self.assertGreaterEqual(r['state']['value'],0); self.assertLessEqual(r['state']['value'],100)
            self.assertEqual(second['results'][0]['state']['value'],second['results'][1]['state']['value'])
            for ctx in contexts:
                c.call('instance.visibility',{**ctx,'visible':False,'activationId':None,'target':target})
            c.call('plugin.shutdown',{'reason':'test'})
            self.assertEqual(c.process.wait(timeout=2),0)
        finally:
            c.close()

    def test_parent_pipe_closure_exits(self):
        c=Client()
        try:
            c.initialize(); c.process.stdin.close()
            self.assertEqual(c.process.wait(timeout=2),0)
        finally:
            c.close()

    def test_idle_process_has_no_periodic_output_or_cpu_loop(self):
        c=Client()
        try:
            c.initialize()
            stat=Path(f'/proc/{c.process.pid}/stat')
            # /proc/PID/stat fields 14,15 after the parenthesized comm field.
            def ticks():
                fields=stat.read_text().rsplit(')',1)[1].split()
                return int(fields[11])+int(fields[12])
            before=ticks()
            self.assertFalse(c.selector.select(.25))
            self.assertLessEqual(ticks()-before,1) # noise allowance; not a system performance certification
            c.call('plugin.shutdown',{'reason':'idle-test'})
        finally:
            c.close()

    def test_invalid_utf8_fatal(self):
        result=subprocess.run([sys.executable,'-I','-B',str(ROOT/'cpu/cpu_usage.py')],input=b'\xff\n',
                              stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=3)
        self.assertEqual(result.returncode,2)
        self.assertEqual(json.loads(result.stdout)['error']['code'],-32700)

    def test_oversize_fatal(self):
        result=subprocess.run([sys.executable,'-I','-B',str(ROOT/'cpu/cpu_usage.py')],input=b'x'*262145,
                              stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=3)
        self.assertEqual(result.returncode,2)
        self.assertEqual(result.stdout,b'')


if __name__=='__main__':
    unittest.main()
