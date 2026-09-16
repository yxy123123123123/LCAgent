def register_tools(mcp, utils):    
    send_command = utils['send_command']

    @mcp.tool()
    def my_custom_tool(ctx) -> str:
        """Test"""
        return "Hello from custom tool!"
    
    @mcp.tool()
    def get_actor_count(ctx) -> str:
        """Get the number of actors in the current Unreal Engine scene."""
        try:
            response = send_command("get_scene_info")
            print(f"Response: {response}")
            if response["status"] == "success":
                result = response["result"]
                total_actor_count = result["actor_count"]
                returned_actor_count = result.get("returned_actor_count", len(result["actors"]))
                limit_reached = result.get("limit_reached", False)
                
                response_text = f"Total number of actors: {total_actor_count}\n"
                
                if limit_reached:
                    response_text += f"WARNING: Actor limit reached! Only {returned_actor_count} actors were returned in the response.\n"
                    response_text += f"The remaining {total_actor_count - returned_actor_count} actors are not included in the response.\n"

                return response_text
            else:
                return f"Error: {response['message']}"
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool()
    def create_landscape(command_type: str, file_paths: list = None, material_path: str = None) -> str:
        """
        在Unreal Engine中执行地形相关操作（对接C++ Landscaping命令处理器）
        type: The type of object to create (e.g., 'StaticMeshActor', 'PointLight', etc.)
        :param command_type: 操作类型，支持三种：DEM（导入TIF高程数据）、Set_Material（设置地形材质）、Execute（执行最终地形导入）
        :param file_paths: 仅DEM类型需传，TIF高程数据文件路径（如"D:/dem1.tif"）
        :param material_path: 仅Set_Material类型需传，地形材质的Unreal引用路径（如'/Game/Materials/TerrainMat'）
        :return: 操作结果信息
        """
        try:
            # 构造请求参数（与C++侧Params字段对应）
            Params = {"command_type": command_type}
            # 根据操作类型补充对应参数
            if command_type == "DEM":
                if not file_paths:
                    return "Error: DEM操作缺少必填参数 'file_paths'（TIF高程文件路径列表）"
                Params["file_paths"] = file_paths
            elif command_type == "Set_Material":
                if not material_path:
                    return "Error: Set_Material操作缺少必填参数 'material_path'（地形材质引用路径）"
                Params["material_path"] = material_path

            # Execute类型无需额外参数，仅需command_type

            # 发送命令到C++ MCP服务器（对应create_landscape命令处理器）
            response = send_command("Ladnscaping", Params)
            print(f"Landscape Operation Response: {response}")  # 调试用：打印原始响应

            # 处理成功响应
            if response["status"] == "success":
                result = response["result"]  # 提取C++侧返回的结果数据
                operation_msg = response.get("message", "操作执行成功")  # 优先取响应中的描述信息

                # 构建详细响应文本（按操作类型补充关键信息）
                response_text = f"Landscape {command_type} Operation: {operation_msg}\n"
                if command_type == "DEM":
                    imported_count = result.get("imported_files", 0)
                    response_text += f"成功导入高程文件数量：{imported_count}\n"
                elif command_type == "Set_Material":
                    used_material = result.get("material_path", "未知材质")
                    response_text += f"已应用的地形材质路径：{used_material}\n"

                return response_text.strip()  # 去除末尾多余换行

            # 处理失败响应（C++侧返回的错误信息）
            else:
                return f"Error: {response['message']}"

        # 捕获所有异常（通信失败、参数错误、UE侧异常等）
        except Exception as e:
            return f"Error: 地形操作执行失败 - {str(e)}"
        
