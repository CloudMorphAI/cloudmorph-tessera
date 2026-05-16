"""LangChain agent that routes MCP tool calls through Tessera."""

from __future__ import annotations

import os

from langchain.agents import AgentExecutor, create_tool_calling_agent
from langchain.prompts import ChatPromptTemplate
from langchain_anthropic import ChatAnthropic
from tessera_tool_wrapper import build_tessera_tools

TESSERA_BASE = os.environ.get("TESSERA_BASE", "http://localhost:8080")
TESSERA_BEARER_TOKEN = os.environ.get("TESSERA_BEARER_TOKEN", "<your-tessera-bearer-token>")

# Build LangChain Tool objects that route through Tessera.
# Each Tool wraps one MCP upstream; swap the list to add more upstreams.
tools = build_tessera_tools(
    upstreams=["github"],
    tessera_base=TESSERA_BASE,
    bearer_token=TESSERA_BEARER_TOKEN,
)

llm = ChatAnthropic(model="claude-opus-4-7")

prompt = ChatPromptTemplate.from_messages(
    [
        ("system", "You can call GitHub via tools. Always use them when asked."),
        ("placeholder", "{chat_history}"),
        ("human", "{input}"),
        ("placeholder", "{agent_scratchpad}"),
    ]
)

agent = create_tool_calling_agent(llm, tools, prompt)
executor = AgentExecutor(agent=agent, tools=tools, verbose=True)

if __name__ == "__main__":
    result = executor.invoke(
        {"input": "Create a GitHub issue 'test from tessera' in cloudmorph/demo"}
    )
    print(result)
