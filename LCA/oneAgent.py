"""旧入口兼容层。

项目不再维护第二套 ReAct/任意 Shell 实现。所有任务统一进入 ``DecisionAgent``
状态机，避免同一工程出现两套安全边界和两套提示词。
"""

from Agent import AgentRunResult, DecisionAgent, StateMachineError, main


__all__ = ["AgentRunResult", "DecisionAgent", "StateMachineError", "main"]


if __name__ == "__main__":
    main()
