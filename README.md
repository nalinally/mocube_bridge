# mocube_bridge

mocube のモジュール (M5Stack AtomS3) と ROS 2 をつなぐ UDP ブリッジです。
`device_manager` ノードがモジュールからの UDP を受けて ID を払い出し、全モジュールの状態を `/modules` に publish します。
`/servo_commands` を受けると、中身を検査して指定されたモジュールに KRS サーボの指令を UDP で送ります。

メッセージ定義は [mocube_msgs](https://github.com/nalinally/mocube_msgs)、
通信プロトコルの一覧は mocube_ws の README にあります。

## 使い方

```bash
ros2 run mocube_bridge device_manager
ros2 run mocube_bridge device_manager --ros-args -p device_file:=/abs/path/devices.json
```

### パラメータ

| 名前 | 既定値 | 説明 |
|---|---|---|
| `port` | `5000` | 受信する UDP ポート。ファームの `Config.h` の `UDP_PORT` と合わせる |
| `device_file` | `~/.ros/mocube/devices.json` | MAC -> ID の台帳。`~` は展開する。相対パスは ros2 run したカレントディレクトリ基準。起動時に `Loaded N devices from <絶対パス>` と出る |
| `timeout` | `3.0` | この秒数 HEARTBEAT が無いと offline |
| `publish_period` | `0.01` | 受信処理と `/modules` publish の周期 [s] |

### トピック

| トピック | 型 | 周期 |
|---|---|---|
| `/modules` (publish) | `mocube_msgs/Modules` | `publish_period` ごと (既定 100 Hz)。変化が無くても流れる |
| `/servo_commands` (subscribe) | `mocube_msgs/ServoCommands` | 受けたらすぐ、各指令を `KRS,...` にしてモジュールへ送る |

## ファイル構成

```
mocube_bridge/
├── protocol.py         # パケットの分解・応答/KRS 指令の組み立て (ROS 非依存)
├── device_registry.py  # MAC -> Device の台帳, ID 払い出し, 永続化, タイムアウト (ROS 非依存)
└── device_manager.py   # ROS ノード: UDP ソケット, コマンド振り分け, /modules への変換, /servo_commands の転送
test/
├── test_protocol.py
├── test_device_registry.py
└── test_flake8.py / test_pep257.py / test_copyright.py (ament 標準)
```

## 処理の流れ

`DeviceManager.__init__`:

1. パラメータ読み込み
2. `DeviceRegistry.load()` — 台帳ファイルを読む。ファイルが無ければ空で開始、**壊れていたら例外で起動失敗**
3. UDP ソケットを `0.0.0.0:<port>` に bind (SO_BROADCAST, ノンブロッキング)
4. `/servo_commands` の subscription と `publish_period` のタイマーを作る

タイマー (`_on_timer`) ごとに:

1. ソケットから受信できるだけ (最大 100 パケット) 読み、`_handle_datagram()` へ
2. `registry.update_online()` — `timeout` を超えたモジュールを offline に
3. 全モジュールを `Modules` にして publish

`_handle_datagram()`:

1. `protocol.parse_packet()` — `COMMAND,<MAC>,...` に分解。MAC は大文字化。2 フィールド未満は捨てる
2. `self._handlers[command]` を呼ぶ。未知のコマンドは debug ログだけ出して無視
3. ハンドラ内で `ProtocolError` が出たら warning ログを出して捨てる (ノードは落ちない)

| コマンド | ハンドラ | 処理 |
|---|---|---|
| `REGISTER` | `_on_register` | `get_or_register()` → `ID,<id>` を送信元に返す、info ログ |
| `HEARTBEAT` | `_on_heartbeat` | `get_or_register()` → `ACK,<id>` を返す → `last_seen` 更新・online |
| `NEIGHBOR` | `_on_neighbor` | 引数を検証 (`ch` は 0..2) → `get_or_register()` → 面 `FACES[ch]` を `[address, 今]` に更新 |

どのコマンドも未登録の MAC なら新規登録します (REGISTER を取りこぼしても HEARTBEAT で登録される)。

## DeviceRegistry (台帳)

- `devices`: `MAC -> Device` の dict (登録順 = `/modules` の並び順)
- `Device`: `mac`, `id`, `addr`, `last_seen`, `online`, `neighbors` (`{"u": [address, t], "r": ..., "f": ...}`)
- ID の払い出し: 新しい MAC が来たら `_next_id` を付けて +1。`_next_id` は起動時に「台帳の最大 ID + 1」(空なら 0)。
  ID は再利用しない (台帳から消さない限り)
- 保存: **新規登録したときだけ** `device_file` に JSON で書く。HEARTBEAT ごとには書かない
- 読み込み: 使うのは `id` と `addr` だけ。全台 offline、`last_seen` = 起動時刻、隣接は未受信 (-1) から始まる
- `online`: HEARTBEAT を受けたら true、`now - last_seen > timeout` で false
- `neighbors`: NEIGHBOR で上書きされるだけで、**時間が経っても消えない**。意図した仕様で、
  古い情報を無視する判断は `/modules` を読む側 (接続状態を推定するノードを別に作る予定) で `t` を見て行う

### devices.json の形式

```json
{
    "DC:54:75:CB:BF:2C": {
        "id": 0,
        "addr": "192.168.97.179",
        "last_seen": 1791420976.28,
        "online": true,
        "neighbor": {"u": [-1, 0.0], "r": [1, 1791420976.1], "f": [-1, 0.0]}
    }
}
```

ID を振り直したいときはノードを止めてから手で編集します (`id` と `addr` は必須、他は省略可)。
ID を変えたモジュールは、次の HEARTBEAT への `ACK,<新ID>` で自動的に新しい ID に切り替わります (リセット不要)。

`/servo_commands` を受けたとき (`_on_servo_commands`)、`commands` を先頭から順に:

1. `to_servo_packet()` でパケットにする

   | `mode` | `servo` | パケット |
   |---|---|---|
   | `POSITION` | 0..5 | `KRS,setPos,<servo>,<angle>` (`angle` は -135..135 [deg]、ICS の値への変換はファーム) |
   | `FREE` | 0..5 | `KRS,setFree,<servo>` |
   | `FREE` | `ALL_SERVOS` (-1) | `KRS,setFreeAll` |

   範囲外や未知の `mode` は `ProtocolError` になり、warning ログを出してその指令だけ捨てる
2. `_send_to_module()` — `registry.find_by_id()` でモジュールを引き、`(device.addr, port)` に送る。
   台帳に無い ID は warning ログを出して捨てる

- 送り先は台帳の `addr` (最後にパケットを受けた IP) なので、offline のモジュールにも送る (届くかは分からない)
- 範囲の定数 (`SERVO_COUNT` など) は `protocol.py`。ファームの `Config.h` を変えたらこちらも合わせる

## テスト

```bash
colcon build --packages-select mocube_bridge
colcon test --packages-select mocube_bridge && colcon test-result --verbose
```

`protocol.py` と `device_registry.py` は ROS 無しで単体テストできます (時計は `clock` 引数で差し替え)。

実機無しで動作を見るには、ノードを起動して別ターミナルから疑似パケットを送ります:

```bash
python3 -c "
import socket; s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.settimeout(1)
s.sendto(b'REGISTER,AA:BB:CC:DD:EE:FF', ('127.0.0.1', 5000)); print(s.recv(64))
s.sendto(b'NEIGHBOR,AA:BB:CC:DD:EE:FF,1,3', ('127.0.0.1', 5000))"
ros2 topic echo --once /modules
```

## デバッグ

| 症状 | 原因の候補 |
|---|---|
| `OSError: [Errno 98] Address already in use` で起動失敗 | 別の device_manager (や他のプロセス) が同じ `port` を使っている。`ss -ulpn \| grep 5000` |
| `json.decoder.JSONDecodeError` / `KeyError: 'addr'` で起動失敗 | `device_file` が壊れている / 必須キーが無い。空の台帳で起動すると既存モジュールと ID が衝突するので、わざと落としている |
| `Loaded 0 devices` なのに既存モジュールがある | ログのパスを確認。`device_file` で別の (または存在しない) 台帳を指定している |
| ログに何も出ない (REGISTER が来ない) | パケットが届いていない。`sudo tcpdump -i any -n udp port 5000`。同じサブネットか、FW、WSL2 なら mirrored モード |
| `Bad packet from ...: NEIGHBOR ...` | ファームと PC でプロトコルがずれている (ch が 0..2 外、引数不足) |
| online が勝手に false になる | HEARTBEAT が `timeout` 秒来ていない。Wi-Fi の取りこぼし、モジュールのループ停止 (サーボ応答待ちなど) |
| モジュールの ID が勝手に変わった | 台帳上の ID とモジュールが持っていた ID が違ったので、ACK で PC 側に合わせた (台帳を消した/別の台帳で起動した)。モジュールのシリアルに `ID changed by ACK` が出る |
| `/modules` の `addr` が古い | `addr` は最後にパケットを受けた送信元。DHCP で変わっても次のパケットで更新される |

ログ出力は `rclpy` のロガー (`[device_manager]`)。未知コマンドのログは debug レベルなので、
見たいときは `--ros-args --log-level device_manager:=debug`。
