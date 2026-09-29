"""Deterministic reference-plugin contract tests; no sleeps or real CPU required."""
from __future__ import annotations
import io
import json
from pathlib import Path
import sys
import unittest
from uuid import UUID

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cpu"))
from cpu_usage import (CpuPlugin, RpcError, MAX_FRAME_BYTES, parse_cpu_rows, utilization,
                       handle, serve, settings, strict_loads)


def uid(n):
    return str(UUID(int=n))


def target(index=0):
    return {"deviceId": "test-deck", "keyIndex": index, "width": 96, "height": 96,
            "locale": "en", "displayFields": ["text", "progress"]}


def initialize(plugin, **limits):
    return plugin.dispatch("plugin.initialize", {"apiVersion": "1.1", "hostVersion": "test",
                           "pluginId": "org.fnord.cpu", "pluginVersion": "0.2.0",
                           "fingerprint": "0" * 64, "locale": "en", "limits": {
                               "minRefreshIntervalMs": 1000, "maxInstances": 128,
                               "maxMessageBytes": 262144, **limits}})


class Fixture:
    def __init__(self):
        self.now = 0.0
        self.reads = 0
        self.fail = False
        self.rows = {"cpu": (100, 0, 0, 900, 0, 0, 0, 0),
                     "cpu0": (20, 0, 0, 80, 0, 0, 0, 0)}
        self.plugin = CpuPlugin(self.read, lambda: self.now)
        initialize(self.plugin)

    def read(self):
        self.reads += 1
        if self.fail:
            raise PermissionError("sensitive internal path")
        return self.rows.copy()

    def create(self, n=1, options=None, interval=2000):
        context = {"instanceId": uid(n), "instanceEpoch": uid(1000 + n)}
        self.plugin.dispatch("instance.create", {**context, "contributionId": "usage",
                             "settingsVersion": 1, "settings": options or {}, "secretNames": [],
                             "target": target(n-1), "effectiveRefreshIntervalMs": interval})
        return {**context, "activationId": uid(2000 + n)}

    def show(self, ctx, visible=True):
        return self.plugin.dispatch("instance.visibility", {**ctx, "visible": visible,
                                    "activationId": ctx["activationId"] if visible else None,
                                    "target": target()})

    def refresh(self, ctx):
        return self.plugin.dispatch("instance.refresh", {**ctx, "deadlineMs": 1000})

    def batch(self, contexts):
        return self.plugin.dispatch("instance.refreshMany", {"instances": contexts, "deadlineMs": 1000})["results"]

    def advance(self, seconds=2, busy=25, idle=75):
        self.now += seconds
        self.rows = {k: (v[0]+busy, v[1], v[2], v[3]+idle, *v[4:]) for k, v in self.rows.items()}


class CpuTests(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()

    def test_no_reads_on_initialize_create_visibility_ping_destroy(self):
        c = self.f.create()
        self.f.show(c)
        self.f.plugin.dispatch("plugin.ping", {})
        self.f.show(c, False)
        self.f.plugin.dispatch("instance.destroy", {"instanceId": c["instanceId"],
                               "instanceEpoch": c["instanceEpoch"], "reason": "test"})
        self.assertEqual(self.f.reads, 0)

    def test_hidden_refresh_rejected_without_io(self):
        c = self.f.create()
        with self.assertRaises(RpcError) as cm:
            self.f.refresh(c)
        self.assertEqual(cm.exception.code, -32004)
        self.assertEqual(self.f.reads, 0)

    def test_first_sample_null_then_percentage(self):
        c = self.f.create()
        self.f.show(c)
        self.assertIsNone(self.f.refresh(c)["state"]["value"])
        self.f.advance()
        response = self.f.refresh(c)
        self.assertEqual(response["state"]["value"], 25)
        self.assertEqual(response["state"]["progress"], .25)
        self.assertEqual(response["sequence"], 2)
        self.assertEqual(response["activationId"], c["activationId"])

    def test_32_buttons_one_read_per_batch(self):
        cs = [self.f.create(n, {"label": str(n)}) for n in range(1,33)]
        for c in cs:
            self.f.show(c)
        self.f.batch(cs)
        self.assertEqual(self.f.reads, 1)
        self.f.advance()
        results = self.f.batch(cs)
        self.assertEqual(self.f.reads, 2)
        self.assertEqual({r["state"]["label"] for r in results}, {str(n) for n in range(1,33)})
        self.assertTrue(all(r["state"]["value"] == 25 for r in results))

    def test_total_and_logical_cpu_different_values(self):
        a = self.f.create(1)
        b = self.f.create(2, {"scope": "logicalCpu", "logicalCpu": 0})
        for c in (a,b): self.f.show(c)
        self.f.batch([a,b])
        self.f.advance()
        self.f.rows["cpu0"] = (120,0,0,80,0,0,0,0)
        results = self.f.batch([a,b])
        self.assertEqual([r["state"]["value"] for r in results], [25,100])

    def test_slow_instance_has_its_own_window(self):
        fast = self.f.create(1, interval=1000)
        slow = self.f.create(2, interval=2000)
        for c in (fast,slow): self.f.show(c)
        self.f.batch([fast,slow])
        self.f.advance(1, busy=0, idle=100)
        self.assertEqual(self.f.refresh(fast)["state"]["value"], 0)
        self.f.advance(1, busy=100, idle=0)
        result = self.f.batch([fast,slow])
        self.assertEqual([r["state"]["value"] for r in result], [100,50])

    def test_hidden_fast_instance_does_not_drive_slow_sampling(self):
        fast = self.f.create(1, interval=1000)
        slow = self.f.create(2, interval=5000)
        for c in (fast,slow): self.f.show(c)
        self.f.batch([fast,slow])
        self.f.show(fast, False)
        self.f.advance(5)
        self.assertEqual(self.f.refresh(slow)["state"]["value"], 25)
        self.assertEqual(self.f.reads,2)

    def test_rate_limit_no_source_read(self):
        c=self.f.create(); self.f.show(c); self.f.refresh(c)
        self.f.advance(.1)
        with self.assertRaises(RpcError) as cm: self.f.refresh(c)
        self.assertEqual(cm.exception.code,-32005)
        self.assertGreater(cm.exception.data["retryAfterMs"],0)
        self.assertEqual(self.f.reads,1)

    def test_last_hidden_clears_cache_without_reads(self):
        c=self.f.create(); self.f.show(c); self.f.refresh(c); self.f.show(c,False)
        self.assertIsNone(self.f.plugin.snapshot)
        self.assertIsNone(self.f.plugin.instances[c["instanceId"]].baseline)
        self.assertEqual(self.f.reads,1)

    def test_reshow_rebaselines_not_hidden_period_average(self):
        c=self.f.create(); self.f.show(c); self.f.refresh(c); self.f.show(c,False)
        self.f.advance(100,10000,0); c={**c,"activationId":uid(9999)}; self.f.show(c)
        self.assertIsNone(self.f.refresh(c)["state"]["value"])
        self.f.advance(2,0,100)
        self.assertEqual(self.f.refresh(c)["state"]["value"],0)

    def test_churn_cannot_bypass_source_floor(self):
        c=self.f.create(); self.f.show(c); self.f.refresh(c)
        self.f.show(c,False)
        self.f.plugin.dispatch("instance.destroy", {"instanceId":c["instanceId"],
                               "instanceEpoch":c["instanceEpoch"],"reason":"replacement"})
        c=self.f.create(2); self.f.show(c)
        self.f.refresh(c)
        self.assertEqual(self.f.reads,1)

    def test_obsolete_epoch_and_activation_rejected(self):
        c=self.f.create(); self.f.show(c)
        for field in ("instanceEpoch","activationId"):
            with self.assertRaises(RpcError): self.f.refresh({**c,field:uid(9999)})
        self.assertEqual(self.f.reads,0)

    def test_one_bad_instance_does_not_fail_batch(self):
        a=self.f.create(1); b=self.f.create(2); self.f.show(a)
        results=self.f.batch([a,b])
        self.assertIn("state",results[0]); self.assertEqual(results[1]["error"]["code"],-32004)
        self.assertEqual(self.f.reads,1)

    def test_invalid_batch_is_transactionally_rejected(self):
        a=self.f.create(); self.f.show(a)
        for contexts in ([a,a],[],[a,{"instanceId":"bad"}]):
            with self.assertRaises(RpcError): self.f.batch(contexts)
        self.assertEqual(self.f.reads,0)

    def test_errors_cached_across_instances_and_recover(self):
        a=self.f.create(1); b=self.f.create(2)
        for c in (a,b): self.f.show(c)
        self.f.fail=True
        results=self.f.batch([a,b]); self.assertEqual(self.f.reads,1)
        self.assertNotIn("sensitive",str(results))
        self.f.advance(); self.f.fail=False
        self.assertIsNone(self.f.refresh(a)["state"]["value"])
        self.f.advance(); self.assertEqual(self.f.refresh(a)["state"]["value"],25)

    def test_missing_cpu_and_hotplug_rebaseline(self):
        c=self.f.create(options={"scope":"logicalCpu","logicalCpu":1}); self.f.show(c)
        self.assertIsNone(self.f.refresh(c)["state"]["value"])
        self.f.advance(); self.f.rows["cpu1"]=(10,0,0,90,0,0,0,0)
        self.assertIsNone(self.f.refresh(c)["state"]["value"])
        self.f.advance(); self.assertEqual(self.f.refresh(c)["state"]["value"],25)
        self.f.advance(); del self.f.rows["cpu1"]
        self.assertIsNone(self.f.refresh(c)["state"]["value"])

    def test_aggregate_hotplug_rebaseline(self):
        c=self.f.create(); self.f.show(c); self.f.refresh(c)
        self.f.advance(); self.f.rows["cpu9"]=(1,0,0,1,0,0,0,0)
        self.assertIsNone(self.f.refresh(c)["state"]["value"])

    def test_counter_regression_and_zero_delta(self):
        c=self.f.create(); self.f.show(c); self.f.refresh(c)
        self.f.now=2
        self.assertIsNone(self.f.refresh(c)["state"]["value"])
        self.f.now=4; self.f.rows["cpu"]=(1,0,0,1,0,0,0,0)
        self.assertIsNone(self.f.refresh(c)["state"]["value"])
        self.f.advance()
        self.assertEqual(self.f.refresh(c)["state"]["value"],25)

    def test_warning_rounding_and_unchanged_semantics(self):
        c=self.f.create(options={"decimals":1,"warningPercent":20,"label":"Load"})
        self.f.show(c); self.f.refresh(c); self.f.advance(2,1,2)
        first=self.f.refresh(c); self.assertEqual(first["state"]["value"],33.3)
        self.assertEqual(first["state"]["status"],"warning")
        self.f.advance(2,1,2); second=self.f.refresh(c)
        self.assertEqual(first["state"],second["state"])
        self.assertGreater(second["sequence"],first["sequence"])

    def test_higher_host_minimum_enforced(self):
        p=CpuPlugin(); initialize(p,minRefreshIntervalMs=5000)
        self.assertEqual(p.minimum_ms,5000)
        self.f.plugin.minimum_ms=5000
        with self.assertRaises(RpcError): self.f.create(interval=2000)

    def test_settings_invalid_and_no_unknown_executable_fields(self):
        for value in ({"scope":"bad"},{"logicalCpu":True},{"decimals":2},{"label":"x\n"},
                      {"warningPercent":float('nan')},{"path":"/tmp/file"},{"interval":.01},
                      {"label":"x"*33},{"warningPercent":True},{"label":"\x7f"},{"warningPercent":10**400}):
            with self.subTest(value=value):
                with self.assertRaises(RpcError): settings(value)
        self.assertEqual(settings({"label":"Processeur"})["label"],"Processeur")

    def test_instance_limit_duplicate_and_initialize_rules(self):
        p=CpuPlugin()
        with self.assertRaises(RpcError): p.dispatch("plugin.ping",{})
        initialize(p,maxInstances=1)
        with self.assertRaises(RpcError): initialize(p)
        self.f.plugin.maximum_instances=1
        self.f.create()
        with self.assertRaises(RpcError): self.f.create()
        with self.assertRaises(RpcError): self.f.create(2)

    def test_shutdown_stops_and_no_invocation_support(self):
        self.f.create()
        with self.assertRaises(RpcError) as cm: self.f.plugin.dispatch("instance.invoke",{})
        self.assertEqual(cm.exception.code,-32601)
        self.f.plugin.dispatch("plugin.shutdown",{"reason":"test"})
        self.assertTrue(self.f.plugin.stopping); self.assertEqual(self.f.plugin.instances,{})


    def test_new_visible_instance_rejects_pre_activation_cache(self):
        a=self.f.create(1, interval=1000); self.f.show(a); self.f.refresh(a)
        self.f.advance(1); self.f.refresh(a)
        self.f.now=1.5
        b=self.f.create(2,interval=1000); self.f.show(b)
        self.assertIsNone(self.f.refresh(b)["state"]["value"])
        self.assertIsNone(self.f.plugin.instances[b["instanceId"]].baseline)
        self.assertEqual(self.f.reads,2)
        self.f.advance(1)
        self.assertIsNone(self.f.refresh(b)["state"]["value"])
        self.f.advance(1)
        self.assertEqual(self.f.refresh(b)["state"]["value"],25)

    def test_many_hidden_attempts_do_not_increase_reads(self):
        a=self.f.create(); self.f.show(a); self.f.refresh(a); self.f.show(a,False)
        for n in range(1000):
            self.f.now+=10
            with self.assertRaises(RpcError): self.f.refresh(a)
        self.assertEqual(self.f.reads,1)

    def test_instances_have_independent_thresholds(self):
        a=self.f.create(1,{"warningPercent":10,"label":"A"})
        b=self.f.create(2,{"warningPercent":90,"label":"B"})
        for c in (a,b): self.f.show(c)
        self.f.batch([a,b]); self.f.advance()
        results=self.f.batch([a,b])
        self.assertEqual([x["state"]["status"] for x in results],["warning","ok"])

    def test_duplicate_visibility_does_not_reset_baseline(self):
        a=self.f.create(); self.f.show(a); self.f.refresh(a)
        self.f.show(a); self.f.advance()
        self.assertEqual(self.f.refresh(a)["state"]["value"],25)

    def test_wire_unsupported_api(self):
        result=handle(CpuPlugin(),{"jsonrpc":"2.0","id":"h-version","method":"plugin.initialize","params":{
            "apiVersion":"1.0","hostVersion":"test","pluginId":"org.fnord.cpu","pluginVersion":"0.2.0",
            "fingerprint":"0"*64,"locale":"en","limits":{"minRefreshIntervalMs":1000,"maxInstances":128,"maxMessageBytes":262144}}})
        self.assertEqual(result["error"]["code"],-32602)


class CounterTests(unittest.TestCase):
    def test_percent_cases(self):
        for busy,idle,expected in [(0,100,0),(100,0,100),(25,75,25)]:
            self.assertEqual(utilization((0,)*8,(busy,0,0,idle,0,0,0,0)),expected)

    def test_iowait_idle_steal_busy(self):
        self.assertEqual(utilization((0,)*8,(10,0,0,50,30,0,0,10)),20)

    def test_guest_not_added_twice(self):
        parsed=parse_cpu_rows(["cpu 100 20 30 800 10 5 5 30 99 19\n"])
        self.assertEqual(sum(parsed["cpu"]),1000)

    def test_iowait_decrease(self):
        self.assertIsNone(utilization((10,0,0,10,10,0,0,0),(20,0,0,20,9,0,0,0)))

    def test_stop_before_non_cpu_and_future_fields(self):
        def rows():
            yield "cpu 1 0 0 9 0 0 0 0 0 0 0 0\n"
            yield "intr 999\n"
            raise AssertionError("read beyond CPU prefix")
        self.assertEqual(parse_cpu_rows(rows())["cpu"][0],1)

    def test_invalid_counter_inputs(self):
        for text in ["", "cpu -1 0 0 1 0 0 0 0", "cpu 0 0", "cpu 1 x 0 1 0 0 0 0", "cpu0 0 0 0 0 0 0 0 0", "cpu 9999999999999999999999999 0 0 0 0 0 0 0"]:
            with self.subTest(text=text):
                with self.assertRaises(ValueError): parse_cpu_rows(text.splitlines())
        with self.assertRaises(ValueError): parse_cpu_rows(["cpu 1 0 0 1 0 0 0 0\n"]*2)


class FramingTests(unittest.TestCase):
    def test_strict_json(self):
        for raw in [b'{"x":1,"x":2}',b'{"x":NaN}',b'{"x":Infinity}',b'{"x":1e400}',b'\xff']:
            with self.assertRaises((ValueError,UnicodeError)): strict_loads(raw)

    def test_no_notification_side_effect(self):
        p=CpuPlugin()
        self.assertIsNone(handle(p,{"jsonrpc":"2.0","method":"plugin.initialize","params":{}}))
        self.assertFalse(p.initialized)

    def test_invalid_envelope_id_and_batch(self):
        for value in [[],{}, {"jsonrpc":"2.0","id":1,"method":"plugin.ping"}]:
            response=handle(CpuPlugin(),value)
            self.assertEqual(response["error"]["code"],-32600)
            self.assertIsNone(response["id"])

    def test_bad_json_fatal_single_error(self):
        out=io.BytesIO()
        self.assertEqual(serve(io.BytesIO(b'{bad\n'),out),2)
        self.assertEqual(json.loads(out.getvalue())["error"]["code"],-32700)

    def test_oversized_and_unterminated_frames(self):
        for raw in (b'x'*(MAX_FRAME_BYTES+1),b'{}'):
            out=io.BytesIO()
            self.assertEqual(serve(io.BytesIO(raw),out),2)
            self.assertEqual(out.getvalue(),b'')

    def test_eof_no_work(self):
        self.assertEqual(serve(io.BytesIO(),io.BytesIO()),0)


if __name__ == '__main__':
    unittest.main()
