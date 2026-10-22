"""
servo_teleop ノード.

端末 (curses) のキー操作で KRS サーボを動かす。/modules でモジュールの一覧を受け、
操作するたびに /servo_commands に publish する (device_manager が UDP で送る)。

サーボごとに角度モード / 回転モードを切り替えられる (m キー、または -p rotation:=true で全部回転モード)。
送る値はどちらも角度 [deg] で、回転モードのサーボでは 0 で停止、0 から離れるほど速く回る。

画面の値は「このノードが最後に送った指令」で、サーボから読み返した値ではない。
起動直後は全サーボが脱力 (F) 扱いで、動かすと 0 を基準に動き出す。
終了時は回転中のサーボに 0 を送って止める (角度モードのサーボはそのまま)。
"""

import curses
import time

from mocube_bridge.protocol import SERVO_COUNT
from mocube_bridge.teleop_state import TeleopState
from mocube_msgs.msg import Modules, ServoCommand, ServoCommands
import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node

# キー入力を待つ時間 [ms] (この周期で /modules も処理する)
INPUT_TIMEOUT_MS = 50

# 終了時の停止指令が DDS から出ていくのを待つ時間 [s]
FLUSH_WAIT_S = 0.2

KEY_ESC = 27
KEY_CTRL_C = 3  # raw モードなので Ctrl+C もキーとして届く

HELP = [
    'up/down (k/j): module   left/right (h/l): servo   m: angle <-> rotation mode',
    'a/d (-/+): move (rotation: speed) by step   [/]: change step',
    'space/c: angle 0 (rotation: stop)   s: stop all rotation servos of module',
    'f: free servo   F: free all servos of module   q: quit (stops rotating servos)',
]


def to_servo_command(command):
    """1 つの TeleopCommand を ServoCommand にする."""
    msg = ServoCommand()
    msg.module_id = command.module_id
    msg.servo = ServoCommand.ALL_SERVOS if command.servo is None else command.servo
    if command.angle is None:
        msg.mode = ServoCommand.FREE
    else:
        msg.mode = ServoCommand.POSITION
        msg.angle = command.angle
    return msg


def describe(command):
    servo = 'all' if command.servo is None else command.servo
    action = 'free' if command.angle is None else f'-> {command.angle:+g} deg'
    return f'ID={command.module_id} servo={servo} {action}'


def format_cell(state, module_id, servo):
    """表の 1 マス. 回転モードは r を付ける ("r+10", 停止は "r0")."""
    angle = state.sent_angle(module_id, servo)
    prefix = 'r' if state.is_rotation(module_id, servo) else ''
    if angle is None:
        return prefix + 'F'
    if angle == 0:
        return prefix + '0'
    return f'{prefix}{angle:+g}'


class ServoTeleop(Node):

    def __init__(self):
        super().__init__('servo_teleop')
        rotation = self.declare_parameter('rotation', False).value
        self.state = TeleopState(rotation=rotation)
        self.last_sent = '-'

        # 最新の一覧だけあればよいので depth 1
        self._subscription = self.create_subscription(
            Modules, 'modules', self._on_modules, 1)
        self._publisher = self.create_publisher(ServoCommands, 'servo_commands', 10)

    def _on_modules(self, msg):
        self.state.set_modules([(m.id, m.online) for m in msg.modules])

    def send(self, commands):
        if not commands:
            return
        msg = ServoCommands()
        msg.commands = [to_servo_command(command) for command in commands]
        self._publisher.publish(msg)
        self.last_sent = ', '.join(describe(command) for command in commands)

    def stop_rotating(self):
        """回転中のサーボを止める. 終了前に呼ぶ."""
        commands = self.state.stop_all_rotating()
        if commands:
            self.send(commands)
            time.sleep(FLUSH_WAIT_S)

    def handle_key(self, key):
        """キーを処理する. 終了なら False を返す."""
        state = self.state
        if key in (ord('q'), KEY_ESC, KEY_CTRL_C):
            return False
        elif key in (curses.KEY_UP, ord('k')):
            state.move_module(-1)
        elif key in (curses.KEY_DOWN, ord('j')):
            state.move_module(1)
        elif key in (curses.KEY_LEFT, ord('h')):
            state.move_servo(-1)
        elif key in (curses.KEY_RIGHT, ord('l')):
            state.move_servo(1)
        elif key == ord('m'):
            state.toggle_rotation()
        elif key in (ord('a'), ord('-')):
            self.send(state.nudge(-1))
        elif key in (ord('d'), ord('+'), ord('=')):
            self.send(state.nudge(1))
        elif key == ord('['):
            state.change_step(-1)
        elif key == ord(']'):
            state.change_step(1)
        elif key in (ord(' '), ord('c')):
            self.send(state.set_angle(0.0))
        elif key == ord('s'):
            self.send(state.stop_module())
        elif key == ord('f'):
            self.send(state.free())
        elif key == ord('F'):
            self.send(state.free_module())
        return True


def draw(stdscr, node):
    state = node.state
    stdscr.erase()
    lines = [
        (f'mocube servo teleop    step: {state.step:g} deg', 0),
        ('values: angle [deg] / r = rotation mode (speed, 0 = stop) / F = free', curses.A_DIM),
        ('', 0),
    ]

    header = '               ' + ''.join(f'{servo:>8}' for servo in range(SERVO_COUNT))
    lines.append((header, 0))
    if not state.modules:
        lines.append(('  (no modules on /modules. is device_manager running?)', 0))

    for row, (module_id, online) in enumerate(state.modules):
        selected_row = row == state.module_index
        mark = '>' if selected_row else ' '
        text = f' {mark} ID {module_id:<3} {"on " if online else "off"}  '
        cells = []
        for servo in range(SERVO_COUNT):
            value = format_cell(state, module_id, servo)
            if selected_row and servo == state.servo:
                value = f'[{value}]'
            cells.append(f'{value:>8}')
        lines.append((text + ''.join(cells), 0))

    lines += [('', 0), (f'last sent: {node.last_sent}', 0), ('', 0)]
    lines += [(line, curses.A_DIM) for line in HELP]

    height, width = stdscr.getmaxyx()
    for y, (line, attr) in enumerate(lines[:height - 1]):
        stdscr.addstr(y, 0, line[:width - 1], attr)
    stdscr.refresh()


def run(stdscr, node):
    curses.curs_set(0)
    curses.raw()  # Ctrl+C でも回転中のサーボを止めてから抜けるため
    stdscr.timeout(INPUT_TIMEOUT_MS)
    while rclpy.ok():
        rclpy.spin_once(node, timeout_sec=0)
        draw(stdscr, node)
        key = stdscr.getch()
        if key != -1 and not node.handle_key(key):
            break
    node.stop_rotating()


def main(args=None):
    rclpy.init(args=args)

    servo_teleop = ServoTeleop()

    try:
        curses.wrapper(run, servo_teleop)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        servo_teleop.destroy_node()
        rclpy.try_shutdown()


if __name__ == '__main__':
    main()
