from mocube_bridge.teleop_state import (
    DEFAULT_STEP,
    STEPS,
    TeleopCommand,
    TeleopState,
)


def make_state(rotation=False):
    state = TeleopState(rotation=rotation)
    state.set_modules([(0, True), (3, False)])
    return state


def test_no_modules_sends_nothing():
    state = TeleopState()
    assert state.module_id is None
    assert state.nudge(1) == []
    assert state.set_angle(0.0) == []
    assert state.free() == []
    assert state.free_module() == []
    assert state.stop_module() == []


def test_nudge_starts_from_zero_and_accumulates():
    state = make_state()

    assert state.nudge(1) == [TeleopCommand(0, 0, DEFAULT_STEP)]
    assert state.nudge(1) == [TeleopCommand(0, 0, 2 * DEFAULT_STEP)]
    assert state.nudge(-1) == [TeleopCommand(0, 0, DEFAULT_STEP)]
    assert state.sent_angle(0, 0) == DEFAULT_STEP


def test_set_angle_clamps():
    state = make_state()
    assert state.set_angle(200) == [TeleopCommand(0, 0, 135.0)]
    assert state.set_angle(-200) == [TeleopCommand(0, 0, -135.0)]


def test_free_and_free_module():
    state = make_state()
    state.move_servo(2)
    state.set_angle(30)

    assert state.free() == [TeleopCommand(0, 2, None)]
    assert state.sent_angle(0, 2) is None

    state.set_angle(30)
    assert state.free_module() == [TeleopCommand(0, None, None)]
    assert state.sent_angle(0, 2) is None


def test_toggle_rotation_is_per_servo():
    state = make_state()
    state.toggle_rotation()
    assert state.is_rotation(0, 0)
    assert not state.is_rotation(0, 1)
    assert not state.is_rotation(3, 0)

    state = make_state(rotation=True)
    state.toggle_rotation()
    assert not state.is_rotation(0, 0)
    assert state.is_rotation(0, 1)


def test_stop_module_stops_only_rotation_servos():
    state = make_state()
    state.move_servo(1)
    state.toggle_rotation()
    state.move_servo(2)
    state.toggle_rotation()

    assert state.stop_module() == [TeleopCommand(0, 1, 0.0), TeleopCommand(0, 3, 0.0)]


def test_stop_all_rotating_skips_stopped_and_angle_servos():
    state = make_state(rotation=True)
    state.nudge(1)                      # ID 0 servo 0: rotating
    state.move_servo(1)
    state.set_angle(0.0)                # ID 0 servo 1: already stopped
    state.move_servo(1)
    state.toggle_rotation()
    state.nudge(1)                      # ID 0 servo 2: angle mode
    state.move_module(1)
    state.move_servo(1)
    state.nudge(-1)                     # ID 3 servo 3: rotating

    assert state.stop_all_rotating() == [TeleopCommand(0, 0, 0.0), TeleopCommand(3, 3, 0.0)]
    assert state.stop_all_rotating() == []


def test_cursor_wraps():
    state = make_state()
    state.move_module(-1)
    assert state.module_id == 3
    state.move_servo(-1)
    assert state.servo == 5


def test_set_modules_keeps_selection_by_id():
    state = make_state()
    state.move_module(1)
    state.set_modules([(3, True), (0, True), (5, True)])
    assert state.module_id == 3

    state.set_modules([(0, True)])
    assert state.module_id == 0


def test_change_step_stops_at_ends():
    state = make_state()
    for _ in range(10):
        state.change_step(1)
    assert state.step == STEPS[-1]
    for _ in range(10):
        state.change_step(-1)
    assert state.step == STEPS[0]
