import os
import sys
import json
from dotenv import load_dotenv

load_dotenv()
os.environ["LANGCHAIN_TRACING_V2"] = "false"

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage, AIMessage
from langchain_core.tools import tool

@tool
def dummy_tool(action: str) -> str:
    """A dummy tool."""
    return "done"

llm = ChatGoogleGenerativeAI(model="gemini-exp-1206", temperature=0)
llm_with_tools = llm.bind_tools([dummy_tool])

msg = llm_with_tools.invoke([HumanMessage(content="Use the dummy tool with action 'test'")])

print("CONTENT:", repr(msg.content))
print("ADDITIONAL KWARGS:", json.dumps(msg.additional_kwargs, indent=2))
print("TOOL CALLS:", json.dumps(msg.tool_calls, indent=2))

# Try converting it to dict and back
from langchain_core.messages import messages_to_dict, messages_from_dict
dict_msg = messages_to_dict([msg])
print("LANGCHAIN DICT:", json.dumps(dict_msg, indent=2))
