"""
device_manager ノード.

UDP でモジュール (AtomS3) からの REGISTER / HEARTBEAT / NEIGHBOR を受け、
ID の払い出しと応答を行い、全モジュールの状態を /modules に publish する。
/servo_commands を受けると、指定されたモジュールに KRS パケットを送る。

新しいモジュール -> PC コマンドを追加するときは
  1. protocol.py の Command に名前、必要ならパーサを追加
  2. DeviceManager に _on_<command>(packet, addr) を作り self._handlers に登録

ROS から PC -> モジュールのコマンドを送れるようにするときは
  1. mocube_msgs に module_id を持つ型付きのメッセージを作る (ファームの文字列は ROS に出さない)
  2. protocol.py に format_xxx() を作り、引数の検査もそこで行う (範囲外は ProtocolError)
  3. メッセージ -> パケットの変換関数 (to_servo_packet など) を作り、
     subscription のコールバックから self._send_to_module() で送る
"""

import os
import socket
import time

from mocube_bridge.device_registry import DeviceRegistry
from mocube_bridge.protocol import (
    Command,
    DEFAULT_PORT,
    FACES,
    format_ack_reply,
    format_id_reply,
    format_servo_free,
    format_servo_free_all,
    format_servo_position,
    MAX_PACKET_SIZE,
    parse_neighbor_args,
    parse_packet,
    ProtocolError,
)
from mocube_msgs.msg import IrReceiver, Module, Modules, ServoCommand, ServoCommands
import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node

# 台帳の既定の場所。起動したディレクトリによって別の台帳を使わないよう絶対パスにする
DEFAULT_DEVICE_FILE = '~/.ros/mocube/devices.json'

# 1 回のタイマーで処理する最大パケット数 (受信が多すぎてもタイマーが詰まらないように)
MAX_PACKETS_PER_TICK = 100


def to_modules_msg(devices, stamp):
    """Device の一覧を Modules メッセージにする."""
    msg = Modules()
    msg.t = stamp
    for device in devices:
        module = Module()
        module.id = int(device.id)
        module.mac = device.mac
        module.addr = device.addr
        module.online = device.online
        module.last_seen = float(device.last_seen)
        for face in FACES:
            address, t = device.neighbors[face]
            receiver = IrReceiver()
            receiver.face = face
            receiver.address = int(address)
            receiver.t = float(t)
            module.receivers.append(receiver)
        msg.modules.append(module)
    return msg


def to_servo_packet(command):
    """1 つの ServoCommand を KRS パケットにする. 中身が不正なら ProtocolError."""
    if command.mode == ServoCommand.POSITION:
        return format_servo_position(command.servo, command.position)
    if command.mode == ServoCommand.FREE:
        if command.servo == ServoCommand.ALL_SERVOS:
            return format_servo_free_all()
        return format_servo_free(command.servo)
    raise ProtocolError(f'unknown servo mode: {command.mode}')


class DeviceManager(Node):

    def __init__(self):
        super().__init__('device_manager')

        port = self.declare_parameter('port', DEFAULT_PORT).value
        # ~ は展開する。相対パスは「ros2 run を実行したカレントディレクトリ」基準
        device_file = os.path.abspath(os.path.expanduser(
            self.declare_parameter('device_file', DEFAULT_DEVICE_FILE).value))
        timeout = self.declare_parameter('timeout', 3.0).value
        publish_period = self.declare_parameter('publish_period', 0.01).value

        self._registry = DeviceRegistry(device_file, timeout)
        count = self._registry.load()
        self.get_logger().info(f'Loaded {count} devices from {device_file}')

        self._port = port
        self._sock = self._open_socket(port)
        self.get_logger().info(f'AtomS3 server started (UDP port {port})')

        self._handlers = {
            Command.REGISTER: self._on_register,
            Command.HEARTBEAT: self._on_heartbeat,
            Command.NEIGHBOR: self._on_neighbor,
        }

        self._servo_subscription = self.create_subscription(
            ServoCommands, 'servo_commands', self._on_servo_commands, 10)
        self._publisher = self.create_publisher(Modules, 'modules', 10)
        self._timer = self.create_timer(publish_period, self._on_timer)

    @staticmethod
    def _open_socket(port):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        # 全てのネットワークインターフェースで受信 (ブロードキャストも届く)
        sock.bind(('0.0.0.0', port))
        sock.setblocking(False)
        return sock

    def destroy_node(self):
        self._sock.close()
        return super().destroy_node()

    # ------------------------------------------------------------
    # timer
    # ------------------------------------------------------------

    def _on_timer(self):
        for _ in range(MAX_PACKETS_PER_TICK):
            try:
                data, addr = self._sock.recvfrom(MAX_PACKET_SIZE)
            except BlockingIOError:
                break
            self._handle_datagram(data, addr)

        self._registry.update_online()
        self._publisher.publish(
            to_modules_msg(self._registry.devices.values(), time.time()))

    def _handle_datagram(self, data, addr):
        packet = parse_packet(data)
        if packet is None:
            return

        handler = self._handlers.get(packet.command)
        if handler is None:
            self.get_logger().debug(f'Unknown command from {addr[0]}: {data!r}')
            return

        try:
            handler(packet, addr)
        except ProtocolError as e:
            self.get_logger().warning(f'Bad packet from {addr[0]}: {e}')

    def _register(self, packet, addr):
        device, is_new = self._registry.get_or_register(packet.mac, addr[0])
        if is_new:
            self.get_logger().info(
                f'New AtomS3 detected: MAC={device.mac} ID={device.id}')
        return device

    # ------------------------------------------------------------
    # command handlers
    # ------------------------------------------------------------

    def _on_register(self, packet, addr):
        device = self._register(packet, addr)
        self._sock.sendto(format_id_reply(device.id), addr)
        self.get_logger().info(
            f'REGISTER: ID={device.id}, MAC={device.mac}, IP={addr[0]}')

    def _on_heartbeat(self, packet, addr):
        device = self._register(packet, addr)
        self._sock.sendto(format_ack_reply(device.id), addr)
        self._registry.mark_heartbeat(device.mac)

    def _on_neighbor(self, packet, addr):
        face, address = parse_neighbor_args(packet.args)
        device = self._register(packet, addr)
        self._registry.set_neighbor(device.mac, face, address)

    # ------------------------------------------------------------
    # subscription
    # ------------------------------------------------------------

    def _on_servo_commands(self, msg):
        for command in msg.commands:
            try:
                packet = to_servo_packet(command)
            except ProtocolError as e:
                self.get_logger().warning(
                    f'Bad servo command to module ID={command.module_id}: {e}')
                continue
            self._send_to_module(command.module_id, packet)

    def _send_to_module(self, module_id, data):
        """ID のモジュールに送る. 台帳に無ければ warning を出して捨てる."""
        device = self._registry.find_by_id(module_id)
        if device is None:
            self.get_logger().warning(f'Unknown module ID={module_id}')
            return
        # モジュールは port で待っている (REGISTER の送信元ポートと同じ)
        self._sock.sendto(data, (device.addr, self._port))


def main(args=None):
    rclpy.init(args=args)

    device_manager = DeviceManager()

    try:
        rclpy.spin(device_manager)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        device_manager.destroy_node()
        rclpy.try_shutdown()


if __name__ == '__main__':
    main()
