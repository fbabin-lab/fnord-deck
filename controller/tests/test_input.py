import time

from sdl_controller.input import InputContext, InputGate, InputRecord


def prepared():
    gate = InputGate(2)
    ctx = InputContext(1, 1, 1, "page")
    gate.reset(ctx)
    gate.accept(InputRecord(ctx, (False, False), time.monotonic()), {0: "A", 1: "B"})
    gate.enable()
    return gate, ctx


def report(gate, context, states, identities=None):
    return gate.accept(InputRecord(context, states, time.monotonic()), identities or {0: "A", 1: "B"})


def test_click_only_on_release_and_no_repeat():
    gate, ctx = prepared()
    assert report(gate, ctx, (True, False)) == []
    assert report(gate, ctx, (True, False)) == []
    assert report(gate, ctx, (False, False)) == [(0, "A")]
    assert report(gate, ctx, (False, False)) == []


def test_held_key_cannot_activate_new_page():
    gate, ctx = prepared()
    report(gate, ctx, (True, False))
    new = InputContext(1, 2, 1, "other-page")
    gate.reset(new)
    gate.enable()
    assert report(gate, new, (False, False), {0: "different"}) == []
    assert report(gate, new, (True, False), {0: "different"}) == []
    assert report(gate, new, (False, False), {0: "different"}) == [(0, "different")]


def test_initial_held_state_is_not_a_click():
    gate = InputGate(2)
    ctx = InputContext(1, 1, 1, "page")
    gate.reset(ctx, disconnect=True)
    gate.enable()
    assert report(gate, ctx, (True, False)) == []
    assert report(gate, ctx, (False, False)) == []
    report(gate, ctx, (True, False))
    assert report(gate, ctx, (False, False)) == [(0, "A")]


def test_independent_keys_and_pause():
    gate, ctx = prepared()
    report(gate, ctx, (True, True))
    assert report(gate, ctx, (False, True)) == [(0, "A")]
    gate.reset(ctx)
    gate.enable()
    assert report(gate, ctx, (False, False)) == []


def test_old_epoch_and_queued_pre_redraw_events_ignored():
    gate, ctx = prepared()
    old = InputContext(0, 1, 1, "page")
    assert report(gate, old, (True, False)) == []
    assert report(gate, ctx, (False, False)) == []
    gate.accept(InputRecord(ctx, (True, False), gate.accept_after - 1), {0: "A"})
    assert report(gate, ctx, (False, False)) == []
