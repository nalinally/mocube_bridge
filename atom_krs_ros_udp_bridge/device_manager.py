import os
import json
import socket
import time

import rclpy
from rclpy.node import Node

from std_msgs.msg import String
from atom_krs_ros_udp_bridge_msg.msg import Module, Modules

next_id = 0


PORT = 5000

# Heartbeatがこの秒数来なければOFFLINE
TIMEOUT = 3.0

# MAC -> ID の保存ファイル
DEVICE_FILE = "devices.json"


class DeviceManager(Node):
    
    
    def __init__(self):
        super().__init__('device_manager')
        self.publisher_ = self.create_publisher(Modules, 'modules', 10)
        timer_period = 0.01
        self.timer = self.create_timer(timer_period, self.timer_callback_func())
        self.state = {}
        self.load_from_json()
        return


    def load_from_json(self):
        
        global next_id
        
        if os.path.exists(DEVICE_FILE):

            try:
                with open(DEVICE_FILE, "r") as f:
                    devices = json.load(f)

                # JSONではIDが数字になっていることを保証
                self.state = ({
                    str(mac): {"id": int(data["id"]), "addr": str(data["addr"]), "last_seen": time.time(), "online": False}
                    for mac, data in devices.items()
                })
                if devices:
                    next_id = max([self.state[mac]["id"] for mac in self.state]) + 1
                    print(f"nextid {next_id}")
                print(f"Loaded {len(devices)} devices.")

            except Exception as e:
                print(f"Could not load {DEVICE_FILE}: {e}")
            
            
    def save_devices(self):

        with open(DEVICE_FILE, "w") as f:
            json.dump(
                self.state,
                f,
                indent=4
            )
        
        
    def get_device_id(self, mac, addr):

        global next_id
        # 既に登録されている
        if mac in self.state:
            self.state[mac]["addr"] = addr
            return self.state[mac]["id"]

        # 新しいデバイス
        device_id = next_id

        self.state[mac] = {"id": device_id, "addr": addr, "last_seen": time.time(), "online": True}

        next_id += 1
        self.save_devices()
        print()
        print("================================")
        print(" New AtomS3 detected")
        print(f" MAC : {mac}")
        print(f" ID  : {device_id}")
        print("================================")

        return device_id


    def heartbeat(self, mac):
        
        self.state[mac]["last_seen"] = time.time()
        self.state[mac]["online"] = True


    def publish_state(self):
        
        msg = Modules()
        msg.modules = []
        
        for mac in self.state.keys():
            module = Module()
            module.id = int(self.state[mac]["id"])
            module.mac = mac
            module.addr = self.state[mac]["addr"]
            module.online = self.state[mac]["online"]
            module.last_seen = self.state[mac]["last_seen"]
            msg.modules.append(module)
        
        msg.t = time.time()
        self.publisher_.publish(msg)
    
    def update_state(self):
        
        for mac in self.state.keys():
            if time.time() - self.state[mac]["last_seen"] > TIMEOUT:
                self.state[mac]["online"] = False
    

    def timer_callback_func(self):
        
        sock = socket.socket(
            socket.AF_INET,
            socket.SOCK_DGRAM
        )
    
        # UDP broadcastを許可
        sock.setsockopt(
            socket.SOL_SOCKET,
            socket.SO_BROADCAST,
            1
        )
    
        # 全てのネットワークインターフェースで受信
        sock.bind(
            ("0.0.0.0", PORT)
        )
        
        sock.setblocking(False)
    
        print(
            f"AtomS3 server started "
            f"(UDP port {PORT})"
        )
        
        def f():

            try:
                data, addr = sock.recvfrom(1024)

                message = data.decode(
                    "utf-8",
                    errors="ignore"
                ).strip()

                parts = message.split(",")

                if len(parts) < 2:
                    return

                command = parts[0]
                mac = parts[1].upper()

                # ------------------------------------------------
                # REGISTER
                # ------------------------------------------------

                if command == "REGISTER":

                    device_id = self.get_device_id(mac, addr[0])                    

                    response = f"ID,{device_id}"

                    sock.sendto(
                        response.encode(),
                        addr
                    )

                    print(
                        f"REGISTER: ID={device_id}, "
                        f"MAC={mac}, IP={addr[0]}"
                    )

                # ------------------------------------------------
                # HEARTBEAT
                # ------------------------------------------------

                elif command == "HEARTBEAT":

                    device_id = self.get_device_id(mac, addr[0])

                    response = f"ACK,{device_id}"

                    sock.sendto(
                        response.encode(),
                        addr
                    )
                    
                    self.heartbeat(mac)
                
            except BlockingIOError:
                pass
            
            self.update_state()
            self.publish_state()
                
        return f

def main(args=None):
    rclpy.init(args=args)
    
    device_manager = DeviceManager()
    
    rclpy.spin(device_manager)
    
    device_manager.destroy_node()
    rclpy.shutdown()
    
if __name__=='__main__':
    main()
