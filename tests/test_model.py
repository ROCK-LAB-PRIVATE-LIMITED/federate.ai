from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool

@tool
def add(a: int, b: int) -> int:
    """Adds two integers."""
    return a + b

llm = ChatGoogleGenerativeAI(model="gemini-exp-1206") # Or whatever model mk5 is using, maybe let's just see how to get the model.
