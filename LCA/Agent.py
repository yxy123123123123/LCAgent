import os
import re
import click
import inspect
import ast
import tempfile
from openai import OpenAI
from string import Template
from typing import List, Callable, Tuple, Optional
import platform

# 统一配置（API 密钥等）
from config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL

# 导入拆分后的提示词
from prompt_template import (
    decision_system_prompt,
    retrieval_system_prompt,
    coding_system_prompt
)
from RAG_path import retrieve as retrieve_path, VECTOR_DB_PATH as PATH_DB_PATH
from RAG_memory import retrieve as retrieve_memory, VECTOR_DB_PATH as MEMORY_DB_PATH, save_conversation_to_rag
from script_validation import validate_generated_python

# 获取Agent.py所在目录（核心新增）
AGENT_DIR = os.path.abspath(os.path.dirname(__file__))

# 父类：基础智能体（封装通用逻辑）
class BaseAgent:
    def __init__(self, model: str = LLM_MODEL):
        # Model provider, model name, and credentials are loaded from config.py.
        self.model = model
        self.client = OpenAI(
            base_url=LLM_BASE_URL,
            api_key=LLM_API_KEY,
        )
        # 记录交互历史（所有智能体都需记录）
        self.conversation_history = []

    def call_model(self, messages: List[dict]) -> str:
        """通用大模型调用方法"""
        print(f"\n\n🤔 {self.__class__.__name__} 思考中...")
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
        )
        content = response.choices[0].message.content
        # 记录交互历史
        self.conversation_history.append({
            "role": "assistant",
            "content": content
        })
        return content

    def save_conversation(self, user_input: str, agent_output: str):
        """保存交互历史到记忆库（每次交互都记录）"""
        self.conversation_history.append({
            "role": "user",
            "content": user_input
        })
        save_result = save_conversation_to_rag(user_input, agent_output)
        print(f"\n📝 {save_result}")

    @staticmethod
    def parse_action(action_str: str) -> Tuple[str, List[str]]:
        """通用Action解析方法"""
        match = re.match(r'(\w+)\((.*)\)', action_str, re.DOTALL)
        if not match:
            raise ValueError("Invalid function call syntax")

        func_name = match.group(1)
        args_str = match.group(2).strip()

        args = []
        current_arg = ""
        in_string = False
        string_char = None
        i = 0
        paren_depth = 0

        while i < len(args_str):
            char = args_str[i]

            if not in_string:
                if char in ['"', "'"]:
                    in_string = True
                    string_char = char
                    current_arg += char
                elif char == '(':
                    paren_depth += 1
                    current_arg += char
                elif char == ')':
                    paren_depth -= 1
                    current_arg += char
                elif char == ',' and paren_depth == 0:
                    args.append(BaseAgent._parse_single_arg(current_arg.strip()))
                    current_arg = ""
                else:
                    current_arg += char
            else:
                current_arg += char
                if char == string_char and (i == 0 or args_str[i - 1] != '\\'):
                    in_string = False
                    string_char = None

            i += 1

        if current_arg.strip():
            args.append(BaseAgent._parse_single_arg(current_arg.strip()))

        return func_name, args

    @staticmethod
    def _parse_single_arg(arg_str: str):
        """通用参数解析方法"""
        arg_str = arg_str.strip()

        if (arg_str.startswith('"') and arg_str.endswith('"')) or \
                (arg_str.startswith("'") and arg_str.endswith("'")):
            inner_str = arg_str[1:-1]
            inner_str = inner_str.replace('\\"', '"').replace("\\'", "'")
            inner_str = inner_str.replace('\\n', '\n').replace('\\t', '\t')
            inner_str = inner_str.replace('\\r', '\r').replace('\\\\', '\\')
            return inner_str

        try:
            return ast.literal_eval(arg_str)
        except (SyntaxError, ValueError):
            return arg_str

    @staticmethod
    def get_operating_system_name():
        """获取系统名称"""
        os_map = {
            "Darwin": "macOS",
            "Windows": "Windows",
            "Linux": "Linux"
        }
        return os_map.get(platform.system(), "Unknown")


# 检索智能体（仅调用检索工具）
class RetrievalAgent(BaseAgent):
    def __init__(self):
        super().__init__()
        # 检索智能体专属工具
        self.tools = {
            "retrieve_path_tool": self.retrieve_path_tool,
            "retrieve_memory_tool": self.retrieve_memory_tool
        }

    def retrieve_path_tool(self, query: str) -> str:
        """路径库检索工具"""
        if os.path.exists(PATH_DB_PATH):
            print("\n📚 检索智能体：正在从路径库检索...")
            path_retrieved = retrieve_path(query, top_k=3)
            print(f"\n✅ 检索智能体-路径库结果：\n{path_retrieved}")
            return f"路径检索：{path_retrieved}"
        else:
            error_msg = f"⚠️ 检索智能体：未找到路径库：{PATH_DB_PATH}"
            print(f"\n{error_msg}")
            return error_msg

    def retrieve_memory_tool(self, query: str) -> str:
        """记忆库检索工具"""
        if os.path.exists(MEMORY_DB_PATH):
            print("\n📚 检索智能体：正在从记忆库检索...")
            memory_retrieved = retrieve_memory(query, top_k=3)
            print(f"\n✅ 检索智能体-记忆库结果：\n{memory_retrieved}")
            return f"记忆检索：{memory_retrieved}"
        else:
            error_msg = f"⚠️ 检索智能体：未找到记忆库：{MEMORY_DB_PATH}"
            print(f"\n{error_msg}")
            return error_msg

    def run(self, user_input: str) -> str:
        """运行检索智能体"""
        messages = [
            {"role": "system", "content": retrieval_system_prompt},
            {"role": "user", "content": f"<question>{user_input}</question>"}
        ]

        while True:
            content = self.call_model(messages)
            # 解析Thought
            thought_match = re.search(r"<thought>(.*?)</thought>", content, re.DOTALL)
            if thought_match:
                thought = thought_match.group(1)
                print(f"\n\n💭 检索智能体-Thought: {thought}")

            # 解析Action（执行检索工具）
            action_match = re.search(r"<action>(.*?)</action>", content, re.DOTALL)
            if not action_match:
                raise RuntimeError("检索智能体未输出 <action>")
            action = action_match.group(1)
            tool_name, args = self.parse_action(action)

            print(f"\n\n🔧 检索智能体-Action: {tool_name}({', '.join(args)})")

            try:
                observation = self.tools[tool_name](*args)
            except Exception as e:
                observation = f"检索工具执行错误：{str(e)}"
            print(f"\n\n🔍 检索智能体-Observation：{observation}")
            return observation


# 编码智能体（仅调用文件写入工具）
class CodingAgent(BaseAgent):
    def __init__(self):
        super().__init__()
        # 编码智能体专属工具
        self.tools = {
            "write_to_file": self.write_to_file
        }
        # 记录Agent.py所在目录
        self.agent_dir = AGENT_DIR

    def write_to_file(self, file_path: str, content: str) -> str:
        """写入并自动校验编码智能体生成的任务脚本。"""
        try:
            # 生成文件只能位于 LCA 目录，避免模型修改工程外的任意文件。
            if not os.path.isabs(file_path):
                file_path = os.path.join(self.agent_dir, file_path)
            file_path = os.path.abspath(file_path)
            if os.path.commonpath([self.agent_dir, file_path]) != self.agent_dir:
                raise ValueError("编码智能体只能写入 LCA 目录")

            protected_files = {
                "Agent.py",
                "create_terrain.py",
                "terrain_client.py",
                "script_validation.py",
                "prompt_template.py",
            }
            if os.path.basename(file_path) in protected_files:
                raise ValueError("编码智能体不能覆盖固定客户端或智能体核心文件")
            if not os.path.basename(file_path).startswith("generated_"):
                raise ValueError("生成脚本文件名必须以 generated_ 开头")

            content = content.replace("\\n", "\n")
            if file_path.lower().endswith(".py"):
                validate_generated_python(file_path, content)

            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            # 先写临时文件再原子替换，校验或写入失败时不会破坏上一版脚本。
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=os.path.dirname(file_path),
                delete=False,
                suffix=".tmp",
            ) as temporary_file:
                temporary_file.write(content)
                temporary_path = temporary_file.name
            os.replace(temporary_path, file_path)

            print(f"\n📝 编码智能体：文件写入并通过自动校验 -> {file_path}")
            return f"文件写入并通过自动校验：{file_path}"
        except Exception as e:
            error_msg = f"编码智能体-文件写入失败：{str(e)}"
            print(f"\n❌ {error_msg}")
            return error_msg

    def run(self, user_input: str) -> str:
        """运行编码智能体"""
        template = Template(coding_system_prompt)
        custom_prompt = template.substitute(agent_dir=self.agent_dir)
        messages = [
            {"role": "system", "content": custom_prompt},
            {"role": "user", "content": f"<question>{user_input}</question>"}
        ]

        while True:
            content = self.call_model(messages)
            # 解析Thought
            thought_match = re.search(r"<thought>(.*?)</thought>", content, re.DOTALL)
            if thought_match:
                thought = thought_match.group(1)
                print(f"\n\n💭 编码智能体-Thought: {thought}")

            # 解析Action（执行编码工具）
            action_match = re.search(r"<action>(.*?)</action>", content, re.DOTALL)
            if not action_match:
                raise RuntimeError("编码智能体未输出 <action>")
            action = action_match.group(1)
            tool_name, args = self.parse_action(action)

            print(f"\n\n🔧 编码智能体-Action: {tool_name}({', '.join(args)})")

            try:
                observation = self.tools[tool_name](*args)
            except Exception as e:
                observation = f"编码工具执行错误：{str(e)}"
            print(f"\n\n🔍 编码智能体-Observation：{observation}")
            return observation


# 决策智能体（仅保留终端命令工具，通过标签调用检索/编码智能体）
class DecisionAgent(BaseAgent):
    def __init__(self, project_directory: str):
        super().__init__()
        self.project_directory = project_directory
        # 初始化检索/编码智能体（仅作为调用对象，不再加入tools）
        self.retrieval_agent = RetrievalAgent()
        self.coding_agent = CodingAgent()
        # 决策智能体仅保留终端命令执行工具
        self.tools = {
            "run_terminal_command": self.run_terminal_command
        }

    def run_terminal_command(self, command: str) -> str:
        """终端命令执行工具"""
        import subprocess
        run_result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            encoding='utf-8',
            errors='replace'
        )
        if run_result.returncode == 0:
            return f"终端命令执行成功，输出：{run_result.stdout.strip()}"
        else:
            return f"终端命令执行失败，错误信息：{run_result.stderr.strip()}"

    def get_tool_list(self) -> str:
        """获取工具列表描述"""
        tool_descriptions = []
        # 仅展示终端命令工具
        for func_name, func in self.tools.items():
            signature = str(inspect.signature(func))
            doc = inspect.getdoc(func) or "执行终端命令"
            tool_descriptions.append(f"- {func_name}{signature}: {doc}")
        # 添加检索/编码智能体的调用说明
        tool_descriptions.append(
            "- 调用检索智能体：使用<call_retrieval_agent>查询内容</call_retrieval_agent>标签，参数为查询指令字符串")
        tool_descriptions.append(
            "- 调用编码智能体：使用<call_coding_agent>查询内容</call_coding_agent>标签，参数为查询指令字符串")
        return "\n".join(tool_descriptions)

    def render_system_prompt(self) -> str:
        """渲染决策智能体提示词"""
        tool_list = self.get_tool_list()
        file_list = ", ".join(
            os.path.abspath(os.path.join(self.project_directory, f))
            for f in os.listdir(self.project_directory)
        )
        return Template(decision_system_prompt).substitute(
            operating_system=self.get_operating_system_name(),
            tool_list=tool_list,
            file_list=file_list
        )

    def run(self, user_input: str) -> str:
        """运行决策智能体"""
        # 初始化消息（仅当决策智能体要求时才添加记忆内容）
        system_prompt = self.render_system_prompt()
        retrieved_memory_content = ""

        # 检查是否要求检索记忆库（关键词匹配）
        if any(keyword in user_input for keyword in ["检索记忆", "查看历史", "记忆库"]):
            print("\n📌 决策智能体：检测到记忆库检索请求")
            retrieved_memory_content = self.retrieval_agent.retrieve_memory_tool(user_input)
            if retrieved_memory_content and "未检索到" not in retrieved_memory_content:
                system_prompt += f"""
        以下是记忆库检索内容（决策智能体要求检索）：
        【记忆库内容】
        {retrieved_memory_content}
        """

        # 构建对话消息
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"<question>{user_input}</question>"}
        ]

        # 记录交互过程
        interaction_memory = ""
        final_answer = ""

        while True:
            content = self.call_model(messages)
            interaction_memory += content + "\n"

            # 解析Thought
            thought_match = re.search(r"<thought>(.*?)</thought>", content, re.DOTALL)
            if thought_match:
                thought = thought_match.group(1)
                print(f"\n\n💭 决策智能体-Thought: {thought}")

            # 解析Final Answer（结束循环）
            if "<final_answer>" in content:
                final_answer = re.search(r"<final_answer>(.*?)</final_answer>", content, re.DOTALL).group(1)
                break

            # 第一步：解析并调用检索智能体
            retrieval_match = re.search(r"<call_retrieval_agent>(.*?)</call_retrieval_agent>", content, re.DOTALL)
            if retrieval_match:
                try:
                    retrieval_query = retrieval_match.group(1).strip()
                    print(f"\n\n📞 决策智能体：调用检索智能体，查询：{retrieval_query}")
                    # 执行检索智能体
                    retrieval_observation = self.retrieval_agent.run(retrieval_query)
                    print(f"\n\n🔍 决策智能体-检索智能体返回：{retrieval_observation}")
                    # 将结果添加到对话消息中
                    messages.append({"role": "user",
                                     "content": f"<observation>检索智能体返回：{retrieval_observation}</observation>"})
                    continue  # 继续循环处理后续逻辑
                except Exception as e:
                    retrieval_error = f"检索智能体调用失败：{str(e)}"
                    print(f"\n\n❌ 决策智能体：{retrieval_error}")
                    messages.append({"role": "user", "content": f"<observation>{retrieval_error}</observation>"})
                    continue

            # 第二步：解析并调用编码智能体
            coding_match = re.search(r"<call_coding_agent>(.*?)</call_coding_agent>", content, re.DOTALL)
            if coding_match:
                try:
                    coding_query = coding_match.group(1).strip()
                    print(f"\n\n📞 决策智能体：调用编码智能体，查询：{coding_query}")
                    # 执行编码智能体
                    coding_observation = self.coding_agent.run(coding_query)
                    print(f"\n\n🔍 决策智能体-编码智能体返回：{coding_observation}")
                    # 将结果添加到对话消息中
                    messages.append(
                        {"role": "user", "content": f"<observation>编码智能体返回：{coding_observation}</observation>"})
                    continue  # 继续循环处理后续逻辑
                except Exception as e:
                    coding_error = f"编码智能体调用失败：{str(e)}"
                    print(f"\n\n❌ 决策智能体：{coding_error}")
                    messages.append({"role": "user", "content": f"<observation>{coding_error}</observation>"})
                    continue

            # 第三步：解析并执行终端命令（仅保留的工具）
            action_match = re.search(r"<action>(.*?)</action>", content, re.DOTALL)
            if not action_match:
                raise RuntimeError("决策智能体未输出 <action>（终端命令）")
            action = action_match.group(1)
            tool_name, args = self.parse_action(action)

            print(f"\n\n🔧 决策智能体-Action: {tool_name}({', '.join(args)})")

            try:
                observation = self.tools[tool_name](*args)
            except Exception as e:
                observation = f"终端命令执行错误：{str(e)}"
            print(f"\n\n🔍 决策智能体-Observation：{observation}")
            obs_msg = f"<observation>{observation}</observation>"
            messages.append({"role": "user", "content": obs_msg})

        # 每次交互都记录到记忆库（无论是否检索）
        self.save_conversation(user_input, interaction_memory)

        return final_answer


# 主函数（启动决策智能体）
@click.command()
@click.argument('project_directory',
                type=click.Path(exists=True, file_okay=False, dir_okay=True),
                default=os.path.abspath(os.path.dirname(__file__)))
def main(project_directory):
    project_dir = os.path.abspath(project_directory)
    # 初始化决策智能体（自动包含检索/编码智能体）
    decision_agent = DecisionAgent(project_directory=project_dir)

    print("欢迎使用环境搭建助手！")
    print("📌 规则说明：")
    print("  - 仅当输入包含「检索记忆/查看历史/记忆库」时，才会检索记忆库内容")
    print("  - 所有交互都会自动记录到记忆库")
    print("  - 检索智能体负责路径/记忆检索，编码智能体负责文件写入，决策智能体负责任务调度")
    while True:
        task = input("\n👤 你: ")
        if task in ["退出", "quit", "exit"]:
            print("🤖 助手: 谢谢使用，再见！")
            break

        final_answer = decision_agent.run(task)
        print(f"\n🤖 助手: {final_answer}")
        print("-" * 80)


if __name__ == "__main__":
    main()
