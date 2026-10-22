import json

from mocube_bridge.device_registry import DeviceRegistry, NO_NEIGHBOR
import pytest


class FakeClock:

    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return FakeClock()


def make_registry(tmp_path, clock):
    return DeviceRegistry(str(tmp_path / 'devices.json'), timeout=3.0, clock=clock)


def test_register_assigns_sequential_ids_and_saves(tmp_path, clock):
    registry = make_registry(tmp_path, clock)

    a, a_new = registry.get_or_register('AA', '10.0.0.1')
    b, b_new = registry.get_or_register('BB', '10.0.0.2')
    a2, a2_new = registry.get_or_register('AA', '10.0.0.9')

    assert (a.id, b.id) == (0, 1)
    assert (a_new, b_new, a2_new) == (True, True, False)
    assert a2 is a and a.addr == '10.0.0.9'

    saved = json.loads((tmp_path / 'devices.json').read_text())
    assert saved['AA']['id'] == 0
    assert saved['BB']['neighbor']['u'] == [NO_NEIGHBOR, 0.0]


def test_load_continues_ids_and_starts_offline(tmp_path, clock):
    (tmp_path / 'devices.json').write_text(json.dumps({
        'AA': {'id': 4, 'addr': '10.0.0.1', 'online': True},
        'BB': {'id': 1, 'addr': '10.0.0.2'},
    }))
    registry = make_registry(tmp_path, clock)

    assert registry.load() == 2
    assert not registry.devices['AA'].online

    c, _ = registry.get_or_register('CC', '10.0.0.3')
    assert c.id == 5


def test_load_missing_file(tmp_path, clock):
    assert make_registry(tmp_path, clock).load() == 0


def test_load_broken_file_raises(tmp_path, clock):
    (tmp_path / 'devices.json').write_text('{broken')
    with pytest.raises(ValueError):
        make_registry(tmp_path, clock).load()


def test_heartbeat_timeout(tmp_path, clock):
    registry = make_registry(tmp_path, clock)
    registry.get_or_register('AA', '10.0.0.1')
    registry.mark_heartbeat('AA')

    clock.now += 3.0
    registry.update_online()
    assert registry.devices['AA'].online

    clock.now += 0.1
    registry.update_online()
    assert not registry.devices['AA'].online


def test_set_neighbor(tmp_path, clock):
    registry = make_registry(tmp_path, clock)
    registry.get_or_register('AA', '10.0.0.1')
    registry.set_neighbor('AA', 'r', 2)
    assert registry.devices['AA'].neighbors['r'] == [2, clock.now]


def test_save_creates_parent_directory(tmp_path, clock):
    path = tmp_path / 'nested' / 'dir' / 'devices.json'
    registry = DeviceRegistry(str(path), timeout=3.0, clock=clock)

    registry.get_or_register('AA', '10.0.0.1')

    assert json.loads(path.read_text())['AA']['id'] == 0
