from mocube_bridge.protocol import (
    format_ack_reply,
    format_id_reply,
    parse_neighbor_args,
    parse_packet,
    ProtocolError,
)
import pytest


def test_parse_packet_normalizes_mac():
    packet = parse_packet(b'HEARTBEAT,dc:54:75:cb:bf:2c\n')
    assert packet.command == 'HEARTBEAT'
    assert packet.mac == 'DC:54:75:CB:BF:2C'
    assert packet.args == ()


def test_parse_packet_keeps_args():
    packet = parse_packet(b'NEIGHBOR,AA,2,7')
    assert packet.args == ('2', '7')


@pytest.mark.parametrize('data', [b'', b'REGISTER', b'\xff\xfe'])
def test_parse_packet_rejects_short(data):
    assert parse_packet(data) is None


def test_parse_neighbor_args():
    assert parse_neighbor_args(('0', '3')) == ('u', 3)
    assert parse_neighbor_args(('2', '255')) == ('f', 255)


@pytest.mark.parametrize('args', [(), ('1',), ('x', '1'), ('3', '1'), ('-1', '1')])
def test_parse_neighbor_args_rejects_bad(args):
    with pytest.raises(ProtocolError):
        parse_neighbor_args(args)


def test_replies():
    assert format_id_reply(3) == b'ID,3'
    assert format_ack_reply(12) == b'ACK,12'
