import os
from unittest.mock import MagicMock
# Import the tools from your agent file
import agent 

# 1. Mock the IDE App context so tools don't crash when looking for CURRENT_APP
class MockApp:
    def __init__(self):
        self.run_configs = {
            "python": {"executable": "python3", "flags": "{file}"}
        }
    def query_one(self, selector):
        # Pretend the "current directory" is the one this script is in
        mock_tree = MagicMock()
        mock_tree.path = os.getcwd()
        return mock_tree

# Initialize the mock
agent.CURRENT_APP = MockApp()
agent.CURRENT_LOG_CB = lambda msg: print(f"[UI LOG]: {msg}")

def test_tools():
    print("--- Testing Tool: list_files ---")
    print(agent.list_files.invoke({}))

    print("\n--- Testing Tool: read_file (this file) ---")
    # Reading itself
    print(agent.read_file.invoke({"filepath": "test_tools.py"}))

    print("\n--- Testing Tool: save_file ---")
    print(agent.save_file.invoke({"filepath": "test_temp.txt", "content": "Hello World!"}))

    print("\n--- Testing Tool: edit_file ---")
    print(agent.edit_file.invoke({
        "filepath": "test_temp.txt", 
        "start_line": 1, 
        "end_line": 1, 
        "new_content": "Hello AI Agent!"
    }))

    print("\n--- Testing Tool: curl_url (testing kittysuite) ---")
    # Note: Curl will return a lot of text, limiting output
    result = agent.curl_url.invoke({"url": "https://www.rocklab.in/kittysuite"})
    print(result[:200] + "...")

    print("\n--- Testing Tool: search_web ---")
    print(agent.search_web.invoke({"query": "what is langgraph"}))

    print("\n--- Security Sandbox Test (Should show Error) ---")
    # Try to access a file outside the directory
    print(agent.read_file.invoke({"filepath": "/etc/passwd"}))
    
    print("\n--- Testing Tool: execute_code (Add Two Numbers) ---")
    # 1. Create a script that adds two numbers
    test_code_path = "test_calc.py"
    test_code = "a = 5\nb = 10\nprint(f'Sum: {a + b}')"
    
    print(agent.save_file.invoke({"filepath": test_code_path, "content": test_code}))
    
    # 2. Execute it using the agent's tool
    exec_result = agent.execute_code.invoke({"filepath": test_code_path})
    print(exec_result)
    
    # 3. Clean up
    if os.path.exists(test_code_path):
        os.remove(test_code_path)
    
    
    # Clean up
    if os.path.exists("test_temp.txt"):
        os.remove("test_temp.txt")
        print("\nCleaned up test files.")

if __name__ == "__main__":
    print("Starting Tool Inspector...")
    try:
        test_tools()
    except Exception as e:
        print(f"Test script failed: {e}")