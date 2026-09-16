import socket
import json
import os
import time
import argparse
import ast

def send_mcp_command(command_type, params):
    """发送命令到MCP服务器"""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect(('localhost', 13377))

        command = {
            "type": "RoadAuto",
            "params": {
                "command_type": command_type,
                **params
            }
        }
        
        # 发送命令
        json_data = json.dumps(command).encode('utf-8')
        sock.sendall(json_data)
        
        # 优化的接收部分
        response = b""
        sock.settimeout(5)
        max_total_timeout = 300
        start_time = time.time()
        
        while time.time() - start_time < max_total_timeout:
            try:
                data = sock.recv(4096)
                if not data:
                    break
                response += data
                start_time = time.time()
            except socket.timeout:
                if response:
                    break
        
        sock.close()
        return response.decode('utf-8') if response else "No response received"
    
    except Exception as e:
        return f"Error: {str(e)}"

def create_road(actor_path, layer_path, positions):
    """创建河流"""
    # 1. 设置道路模型"/Game/city.city_C"
    print("设置道路模型...")
    result1 = send_mcp_command("Actor", {"actor_path": actor_path})
    print(f"设置道路模型结果: {result1}")
    # 2. 设置材质图层"/Game/test2_sharedassets/Jagged_Rock_0_LayerInfo.Jagged_Rock_0_LayerInfo"
    print("设置材质图层...")
    result2 = send_mcp_command("Paint_Layer",{"layer_path": layer_path})
    print(f"设置材质图层结果: {result2}")

    # 3. 设置道路点位置
    try:
        # 将字符串转换为列表（支持单引号/双引号字典）
        position = ast.literal_eval(positions)
        # 验证格式是否为列表，且元素为包含X/Y/Z的字典
        if not isinstance(position, list):
            raise ValueError("positions必须是列表格式")
        for pos in position:
            if not isinstance(pos, dict) or not all(k in pos for k in ["X", "Y", "Z"]):
                raise ValueError(f"位置元素格式错误：{pos}（需包含X/Y/Z键）")
    except Exception as e:
        print(f"位置参数解析失败：{e}")
        return "解析位置失败"
    print("设置道路点位置...")
    # positions = [
    #     {"X": 273141.46, "Y": 309156.01, "Z": 817.14},
    #     {"X": 273143.87, "Y": 303428.29, "Z": 817.14}
    # ]
    result3 = send_mcp_command("Points", {"positions": position})
    print(f"设置河流点位置结果: {result3}")

    # # 4. 设置河流宽度
    # print("设置河流宽度...")
    # width = 4200
    # result4 = send_mcp_command("Width", {"width": width})
    # print(f"设置河流宽度结果: {result4}")

    # 4. 执行道路创建
    print("执行道路创建...")
    result5 = send_mcp_command("Execute", {})
    print(f"执行道路创建结果: {result5}")

    return "道路创建流程完成"

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="传入参数")
    parser.add_argument("--actor-path", required=True)
    parser.add_argument("--layer-path", required=True)
    parser.add_argument("--positions", required=True)
    args = parser.parse_args()
    result = create_road(actor_path = args.actor_path, layer_path = args.layer_path, positions = args.positions)
    print("最终结果:", result)