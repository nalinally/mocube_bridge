"""
servo_teleop の状態.

選択中のモジュール・サーボ、各サーボが角度モードか回転モードか、
各サーボに最後に送った指令を持ち、操作に応じて送るべき指令 (TeleopCommand のリスト) を返す。
ROS にも curses にも依存しないので単体テストできる。

角度モードと回転モードで送る指令は同じ (角度 [deg])。回転モードのサーボは
0 で停止し、0 から離れるほど速く回る。どちらのモードかはサーボ側の設定 (EEPROM) で決まるので、
ここでのモードは表示と停止操作のためだけのもの。
"""

from dataclasses import dataclass

from mocube_bridge.protocol import SERVO_COUNT, SERVO_MAX_DEG

# 1 回のキー操作で動かす量 [deg] の候補
STEPS = (1.0, 5.0, 10.0, 30.0)
DEFAULT_STEP = 5.0


@dataclass(frozen=True)
class TeleopCommand:
    """送る指令 1 つ."""

    module_id: int
    servo: int        # None なら全サーボ (脱力のときだけ)
    angle: float      # [deg]。None なら脱力


class TeleopState:
    """カーソル位置と、(module_id, servo) ごとのモードと最後に送った角度 (None = 脱力)."""

    def __init__(self, rotation=False):
        self.modules = []  # [(id, online)] /modules の並び順
        self.module_index = 0
        self.servo = 0
        self.step = DEFAULT_STEP
        self._default_rotation = rotation
        self._rotation = {}
        self._sent = {}

    # ------------------------------------------------------------
    # 表示用
    # ------------------------------------------------------------

    @property
    def module_id(self):
        """選択中のモジュールの ID. モジュールが 1 台も無ければ None."""
        if not self.modules:
            return None
        return self.modules[self.module_index][0]

    def sent_angle(self, module_id, servo):
        """最後に送った角度. まだ送っていないか脱力なら None."""
        return self._sent.get((module_id, servo))

    def is_rotation(self, module_id, servo):
        return self._rotation.get((module_id, servo), self._default_rotation)

    # ------------------------------------------------------------
    # カーソル・モード
    # ------------------------------------------------------------

    def set_modules(self, modules):
        """/modules を受けたら呼ぶ. 選択中のモジュールは ID で追いかける."""
        selected = self.module_id
        self.modules = list(modules)
        ids = [module_id for module_id, _ in self.modules]
        if selected in ids:
            self.module_index = ids.index(selected)
        else:
            self.module_index = min(self.module_index, max(len(ids) - 1, 0))

    def move_module(self, delta):
        if self.modules:
            self.module_index = (self.module_index + delta) % len(self.modules)

    def move_servo(self, delta):
        self.servo = (self.servo + delta) % SERVO_COUNT

    def change_step(self, delta):
        index = STEPS.index(self.step) + delta
        self.step = STEPS[max(0, min(index, len(STEPS) - 1))]

    def toggle_rotation(self):
        """選択中のサーボの角度モード / 回転モードを切り替える (指令は送らない)."""
        if self.module_id is not None:
            key = (self.module_id, self.servo)
            self._rotation[key] = not self.is_rotation(*key)

    # ------------------------------------------------------------
    # 指令 (モジュールが無ければ空のリストを返す)
    # ------------------------------------------------------------

    def nudge(self, direction):
        """選択中のサーボを step だけ動かす (回転モードなら速くする). 脱力中なら 0 を基準にする."""
        if self.module_id is None:
            return []
        current = self.sent_angle(self.module_id, self.servo)
        base = 0.0 if current is None else current
        return self.set_angle(base + direction * self.step)

    def set_angle(self, angle):
        """選択中のサーボを angle [deg] にする (範囲内に丸める). 0 なら中央 / 停止."""
        if self.module_id is None:
            return []
        angle = max(-SERVO_MAX_DEG, min(float(angle), SERVO_MAX_DEG))
        return [self._send(self.module_id, self.servo, angle)]

    def stop_module(self):
        """選択中のモジュールの回転モードのサーボを全部止める (角度 0 を送る)."""
        if self.module_id is None:
            return []
        return [
            self._send(self.module_id, servo, 0.0)
            for servo in range(SERVO_COUNT)
            if self.is_rotation(self.module_id, servo)
        ]

    def stop_all_rotating(self):
        """全モジュールで、回転中 (0 以外を送った回転モード) のサーボを止める. 終了時用."""
        return [
            self._send(module_id, servo, 0.0)
            for (module_id, servo), angle in list(self._sent.items())
            if angle and self.is_rotation(module_id, servo)
        ]

    def free(self):
        """選択中のサーボを脱力."""
        if self.module_id is None:
            return []
        return [self._send(self.module_id, self.servo, None)]

    def free_module(self):
        """選択中のモジュールの全サーボを脱力."""
        if self.module_id is None:
            return []
        for servo in range(SERVO_COUNT):
            self._sent[(self.module_id, servo)] = None
        return [TeleopCommand(self.module_id, None, None)]

    def _send(self, module_id, servo, angle):
        self._sent[(module_id, servo)] = angle
        return TeleopCommand(module_id, servo, angle)
