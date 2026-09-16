# 测试脚本：test_actor_path.py
import socket
import json


def test_actor_path(actor_path):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect(('localhost', 13377))
    command = {
        "type": "RoadAuto",
        "params": {
            "command_type": "Actor",
            "actor_path": actor_path
        }
    }
    json_data = json.dumps(command).encode('utf-8')
    sock.sendall(json_data)

    # 接收响应
    response = b""
    sock.settimeout(5)
    try:
        data = sock.recv(4096)
        response += data
    except socket.timeout:
        pass
    sock.close()
    print("响应结果：", response.decode('utf-8'))


# 替换为你从Unreal复制的正确路径
test_actor_path("/Game/ThirdParty/Road_Creator_Pro/Blueprints/BP_Road_Creator_C.BP_Road_Creator_C")
