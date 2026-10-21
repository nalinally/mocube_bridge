"""
AtomS3 モジュール <-> PC 間の UDP テキストプロトコル.

1 パケット = "COMMAND,<MAC>,arg..." のカンマ区切り 1 行。
ファームウェア側の定義は firmware/mocube_main/src/Protocol.h。
ROS に依存しないので単体テストできる。
"""

from dataclasses import dataclass

DEFAULT_PORT = 5000

# 赤外線受信チャンネル番号 -> 面の名前 (Module.receivers もこの順)
FACES = ('u', 'r', 'f')

# 受信バッファサイズ (これより長いパケットは切り捨てられる)
MAX_PACKET_SIZE = 1024


class Command:
    """モジュール -> PC のコマンド名."""

    REGISTER = 'REGISTER'    # REGISTER,<MAC>
    HEARTBEAT = 'HEARTBEAT'  # HEARTBEAT,<MAC>
    NEIGHBOR = 'NEIGHBOR'    # NEIGHBOR,<MAC>,<ch>,<address>


class ProtocolError(ValueError):
    """パケットの中身が仕様に合わない."""


@dataclass(frozen=True)
class Packet:
    """受信パケットを分解したもの."""

    command: str
    mac: str      # 大文字に正規化済み
    args: tuple   # MAC より後ろのフィールド (文字列のまま)


def parse_packet(data: bytes):
    """受信バイト列を Packet にする. コマンドと MAC が無ければ None."""
    message = data.decode('utf-8', errors='ignore').strip()
    parts = message.split(',')

    if len(parts) < 2:
        return None

    return Packet(command=parts[0], mac=parts[1].upper(), args=tuple(parts[2:]))


def parse_neighbor_args(args):
    """
    NEIGHBOR の引数 (ch, address) を解釈して (face, address) を返す.

    address は相手モジュールの ID (uint8)。相手が ID 未取得なら 255。
    """
    if len(args) < 2:
        raise ProtocolError(f'NEIGHBOR needs 2 args, got {args!r}')

    try:
        channel = int(args[0])
        address = int(args[1])
    except ValueError as e:
        raise ProtocolError(f'NEIGHBOR args must be int: {args!r}') from e

    if not 0 <= channel < len(FACES):
        raise ProtocolError(f'NEIGHBOR channel out of range: {channel}')

    return FACES[channel], address


def format_id_reply(device_id: int) -> bytes:
    """REGISTER への応答."""
    return f'ID,{device_id}'.encode()


def format_ack_reply(device_id: int) -> bytes:
    """HEARTBEAT への応答."""
    return f'ACK,{device_id}'.encode()
