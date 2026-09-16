
### User Guide: Adding Custom MCP Tools

To extend the functionality of the UnrealMCP plugin with your own tools, follow these steps:

1. **Locate the `user_tools` Directory**
   - Find the `user_tools` directory in the plugin’s MCP folder (e.g., `Plugins/UnrealMCP/MCP/user_tools`).
   - If it doesn’t exist, create it manually.

2. **Create a Python Script**
   - Add a new `.py` file in the `user_tools` directory (e.g., `my_tool.py`).
   - Define a `register_tools(mcp, utils)` function in your script to register your custom tools.

3. **Define Your Tools**
   - Use the `@mcp.tool()` decorator to create tools.
   - Access the `send_command` function via `utils['send_command']` to interact with Unreal Engine.
   - Example:

     ```python
     def register_tools(mcp, utils):
         send_command = utils['send_command']
         
         @mcp.tool()
         def create_cube(ctx, location: list) -> str:
             """Create a cube at the specified location."""
             code = f"""
             import unreal
             location = unreal.Vector({location[0]}, {location[1]}, {location[2]})
             unreal.EditorLevelLibrary.spawn_actor_from_class(unreal.StaticMeshActor, location)
             """
             response = send_command("execute_python", {"code": code})
             return "Cube created" if response["status"] == "success" else f"Error: {response['message']}"
     ```

4. **Run the Bridge**
   - Start the MCP bridge as usual with `run_unreal_mcp.bat`. Your tools will be loaded automatically.

**Notes:**
- Tools run in the bridge’s Python process and communicate with Unreal Engine via the MCP server.
- Use `send_command("execute_python", {"code": "..."})` to execute Python code in Unreal Engine’s interpreter, accessing the `unreal` module.
- Ensure any additional Python packages required by your tools are installed in Unreal Engine’s Python environment (not the bridge’s virtual environment) if using `execute_python`.

用户指南：添加自定义 MCP 工具
若要使用您自己的工具扩展 UnrealMCP 插件的功能，请按照以下步骤操作：
找到 “user_tools” 目录
在插件的 MCP 文件夹中找到 “user_tools” 目录（例如，“Plugins/UnrealMCP/MCP/user_tools”）。
如果该目录不存在，请手动创建。
创建一个 Python 脚本
在 “user_tools” 目录中添加一个新的 “.py” 文件（例如，“my_tool.py”）。
在您的脚本中定义一个 “register_tools (mcp, utils)” 函数来注册您的自定义工具。
定义您的工具
使用 “@mcp.tool ()” 装饰器创建工具。
通过 “utils ['send_command']” 访问 “send_command” 函数，以便与虚幻引擎进行交互。
示例：
python
def register_tools(mcp, utils):
    send_command = utils['send_command']
    
    @mcp.tool()
    def create_cube(ctx, location: list) -> str:
        在指定位置创建一个立方体。
        code = f
        import unreal
        location = unreal.Vector({location[0]}, {location[1]}, {location[2]})
        unreal.EditorLevelLibrary.spawn_actor_from_class(unreal.StaticMeshActor, location)
        
        response = send_command("execute_python", {"code": code})
        return "Cube created" if response["status"] == "success" else f"Error: {response['message']}"
运行桥接程序
像往常一样使用 “run_unreal_mcp.bat” 启动 MCP 桥接程序。您的工具将自动加载。
注意事项：
工具在桥接程序的 Python 进程中运行，并通过 MCP 服务器与虚幻引擎进行通信。
使用 “send_command ("execute_python", {"code": "..."})” 在虚幻引擎的解释器中执行 Python 代码，以访问 “unreal” 模块。
如果使用 “execute_python”，请确保您的工具所需的任何其他 Python 包安装在虚幻引擎的 Python 环境中（而不是桥接程序的虚拟环境）。
