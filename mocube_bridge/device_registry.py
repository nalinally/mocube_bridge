"""
モジュール (AtomS3) の台帳.

MAC アドレス -> ID の対応を JSON ファイルに永続化し、
オンライン状態と赤外線で見えている隣接モジュールを保持する。
ROS に依存しないので単体テストできる。
"""

from dataclasses import dataclass, field
import json
import os
import time

from mocube_bridge.protocol import FACES

# 隣接情報の address の初期値 (まだ何も受信していない)
NO_NEIGHBOR = -1


def _empty_neighbors():
    return {face: [NO_NEIGHBOR, 0.0] for face in FACES}


@dataclass
class Device:
    """1 モジュール分の状態."""

    mac: str
    id: int  # noqa: A003  (Module.msg に合わせる)
    addr: str                 # 最後にパケットを受けた送信元 IP
    last_seen: float          # 最後に HEARTBEAT を受けた時刻 (UNIX time)
    online: bool
    # face -> [相手の address, 受信時刻]。一度入った値はタイムアウトしない
    neighbors: dict = field(default_factory=_empty_neighbors)

    def to_json(self):
        return {
            'id': self.id,
            'addr': self.addr,
            'last_seen': self.last_seen,
            'online': self.online,
            'neighbor': self.neighbors,
        }


class DeviceRegistry:
    """MAC -> Device の台帳と ID の払い出し."""

    def __init__(self, path, timeout, clock=time.time):
        self.path = path
        self.timeout = timeout
        self._clock = clock
        self.devices = {}  # MAC -> Device (登録順)
        self._next_id = 0

    def load(self):
        """
        台帳ファイルを読み込み、読み込んだ台数を返す.

        ファイルが無ければ空のまま 0 を返す。壊れている場合は例外を投げる
        (空の台帳で起動すると ID 0 から払い出して既存の対応を上書きしてしまうため)。
        ID と addr 以外 (online など) は保存されていても読み捨て、オフラインから始める。
        """
        if not os.path.exists(self.path):
            return 0

        with open(self.path, 'r') as f:
            raw = json.load(f)

        now = self._clock()
        self.devices = {
            str(mac): Device(
                mac=str(mac),
                id=int(data['id']),
                addr=str(data['addr']),
                last_seen=now,
                online=False,
            )
            for mac, data in raw.items()
        }
        if self.devices:
            self._next_id = max(d.id for d in self.devices.values()) + 1
        return len(self.devices)

    def save(self):
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(self.path, 'w') as f:
            json.dump(
                {mac: d.to_json() for mac, d in self.devices.items()},
                f,
                indent=4,
            )

    def get_or_register(self, mac, addr):
        """
        MAC に対応する Device を返す. 未登録なら新しい ID を払い出して保存する.

        戻り値は (device, 新規登録されたか)。
        """
        device = self.devices.get(mac)
        if device is not None:
            device.addr = addr
            return device, False

        device = Device(
            mac=mac,
            id=self._next_id,
            addr=addr,
            last_seen=self._clock(),
            online=True,
        )
        self.devices[mac] = device
        self._next_id += 1
        self.save()
        return device, True

    def mark_heartbeat(self, mac):
        device = self.devices[mac]
        device.last_seen = self._clock()
        device.online = True

    def set_neighbor(self, mac, face, address):
        self.devices[mac].neighbors[face] = [address, self._clock()]

    def update_online(self):
        """HEARTBEAT が timeout 秒以上来ていないモジュールをオフラインにする."""
        now = self._clock()
        for device in self.devices.values():
            if now - device.last_seen > self.timeout:
                device.online = False
