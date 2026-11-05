"""Clock: demo rate, holds, 45 s beat cap, live/approval holds, skip, speed (fake time source)."""

import pytest

from sim.clock import Clock


class FakeTime:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def _clock(mode="demo"):
    ft = FakeTime()
    c = Clock(mode, time_source=ft)
    c.start()
    return c, ft


def test_demo_runs_half_sim_minute_per_second():
    c, ft = _clock()
    ft.t = 1.0
    assert c.ticks_due(1020.0) == 5
    ft.t = 3.0
    assert c.ticks_due(1020.5) == 10


def test_paused_clock_does_not_tick():
    c, ft = _clock()
    c.pause()
    ft.t = 10
    assert c.ticks_due(1020.0) == 0


def test_beat_hold_blocks_and_expires_after_45s_max():
    c, ft = _clock()
    c.hold("agent:fleet", max_real_s=120)
    ft.t = 44
    assert c.ticks_due(1020.0) == 0
    ft.t = 45
    assert c.held is False
    c.ticks_due(1020.0)
    ft.t = 46
    assert c.ticks_due(1020.0) == 5


def test_release_resumes():
    c, ft = _clock()
    c.hold("agent:dispatch")
    ft.t = 2
    assert c.ticks_due(1020.0) == 0
    c.release("agent:dispatch")
    ft.t = 3
    assert c.ticks_due(1020.0) == 5


def test_live_mode_only_holds_on_approvals():
    c, ft = _clock("live")
    c.hold("agent:fleet")
    ft.t = 1
    assert c.ticks_due(1020.0) == 5
    c.hold("approval:A-0001", max_real_s=None, approval=True)
    ft.t = 1000
    assert c.ticks_due(1020.5) == 0


def test_skip_runs_10_sim_min_per_s_to_target_then_normal():
    c, ft = _clock()
    c.skip_to(1032.0)  # from next tick 1021.0: 111 ticks including the target tick
    ft.t = 1.0
    assert c.ticks_due(1021.0) == 100
    ft.t = 2.0
    assert c.ticks_due(1031.0) == 11
    assert c.skip_target is None
    ft.t = 3.0
    assert c.ticks_due(1032.1) == 5


def test_skip_waits_while_held():
    c, ft = _clock()
    c.hold("agent:x")
    c.skip_to(1100.0)
    ft.t = 1
    assert c.ticks_due(1020.0) == 0


def test_speed_values():
    c, ft = _clock()
    c.set_speed(2)
    ft.t = 1
    assert c.ticks_due(1020.0) == 20
    with pytest.raises(ValueError):
        c.set_speed(5)


def test_headless_batches():
    c, ft = _clock("headless")
    c.hold("agent:x")
    assert c.ticks_due(1020.0) > 0
