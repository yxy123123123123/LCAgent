import socket
import json
import time
import argparse


def send_mcp_command(command_type, params):
    """
    通过MCP协议向Unreal Engine发送命令
    
    Args:
        command_type: 命令类型字符串
        params: 参数字典
    
    Returns:
        响应字符串
    """
    try:
        # 创建TCP连接
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect(('localhost', 13377))
        
        # 构建命令JSON
        command = {
            "type": "Landscaping",
            "params": {
                "command_type": command_type,
                **params
            }
        }
        
        # 发送命令
        json_data = json.dumps(command).encode('utf-8')
        sock.sendall(json_data)
        
        # 接收响应
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


def create_terrain(file_paths, material_path):
    """
    根据高程数据文件创建地形并应用材质
    
    Args:
        file_paths: 高程数据文件路径列表
        material_path: 材质路径字符串
    
    Returns:
        执行结果字符串
    """
    print("开始创建地形...")
    
    # 关键修改：将文件路径列表转为分号分隔的字符串
    file_paths_str = ";".join(file_paths)
    # 导入高程数据（传递字符串而非列表）
    dem_result = send_mcp_command("DEM", {"file_paths": file_paths_str})
    print(f"高程数据导入结果: {dem_result}")
    
    # 设置材质
    material_result = send_mcp_command("Set_Material", {"material_path": material_path})
    print(f"材质设置结果: {material_result}")
    
    # 执行地形创建
    execute_result = send_mcp_command("Execute", {})
    print(f"地形创建执行结果: {execute_result}")

    return "地形创建流程完成"


if __name__ == "__main__":
    # 解析命令行参数
    parser = argparse.ArgumentParser(description="根据高程数据文件创建地形并应用材质")
    parser.add_argument("--file-paths", required=True, nargs='+', 
                       help="高程数据文件路径，支持多个文件")
    parser.add_argument("--material-path", required=True, 
                       help="材质路径")
    
    args = parser.parse_args()
    
    # 执行地形创建
    result = create_terrain(file_paths=args.file_paths, 
                           material_path=args.material_path)
    print(result)