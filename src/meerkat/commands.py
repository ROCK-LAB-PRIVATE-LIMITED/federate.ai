import os
import glob
import subprocess
import platform
import re
from textual.suggester import Suggester

SLASH_COMMANDS =[
    "/tools", "/tools desc", "/tools nodesc",
    "/arm", "/config", "/safe",
    "/ide", "/ide disable", "/ide enable", "/ide install", "/ide status",
    "/init",
    "/compress",
    "/copy",
    "/directory", "/dir",
    "/tts", "/stt", "/readback", "/speech",
    "/telegram",
    "/select_agent", "/clear_all",
    "/dpi", "/schedule",
    "/help"
]

class ChatSuggester(Suggester):
    def __init__(self, get_app_cb):
        # We disable cache since files/directories change dynamically
        super().__init__(use_cache=False, case_sensitive=True)
        self.get_app_cb = get_app_cb

    async def get_suggestion(self, value: str) -> str | None:
        if not value:
            return None
        
        # 1. Provide Autocompletion for Slash Commands
        if value.startswith("/"):
            for cmd in SLASH_COMMANDS:
                if cmd.startswith(value):
                    return cmd
            return None
            
        # 2. Provide Autocompletion for & Paths
        last_amp_idx = value.rfind("&")
        if last_amp_idx != -1:
            prefix = value[:last_amp_idx + 1]
            partial_path = value[last_amp_idx + 1:]
            
            # If there's an unescaped space, stop suggesting
            parts = partial_path.split(" ")
            if len(parts) > 1:
                if not partial_path.replace(r"\ ", "").count(" ") == 0:
                     pass
            
            try:
                app = self.get_app_cb()
                base_dir = str(app.query_one("#dir_tree").path) if app else os.getcwd()
            except:
                base_dir = os.getcwd()
                
            search_str = partial_path.replace(r"\ ", " ")
            search_pattern = os.path.join(base_dir, search_str + "*")
            
            matches = glob.glob(search_pattern)
            if matches:
                matches.sort()
                match = matches[0]
                rel_path = os.path.relpath(match, base_dir)
                rel_path = rel_path.replace("\\", "/")
                if os.path.isdir(match):
                    rel_path += "/"
                rel_path = rel_path.replace(" ", r"\ ")
                return prefix + rel_path
        
        # 3. Provide Autocompletion for @ Agents
        last_at_idx = value.rfind("@")
        if last_at_idx != -1:
            prefix = value[:last_at_idx + 1]
            partial_agent = value[last_at_idx + 1:]
            
            try:
                app = self.get_app_cb()
                agent_view = app.query_one("AIAgentView")
                agent_names = list(agent_view.agent_manager.agents.keys()) + ["team", "room"]
                for name in agent_names:
                    if name.lower().startswith(partial_agent.lower()):
                        return prefix + name
            except:
                pass
                
        return None

def process_shell_command(command: str, agent_view) -> str:
    """Execute !shell commands passthrough."""
    try:
        app = agent_view.app
        base_dir = str(app.query_one("#dir_tree").path) if app else os.getcwd()
    except:
        base_dir = os.getcwd()
        
    # Intercept 'cd' to change the stateful environment directory
    if command.strip().startswith("cd ") or command.strip() == "cd":
        target = command.strip()[3:].strip()
        if not target:
            target = os.path.expanduser("~")
        try:
            if not os.path.isabs(target):
                target = os.path.join(base_dir, target)
            target = os.path.abspath(target)
            os.chdir(target)
            
            if app:
                try:
                    tree = app.query_one("#dir_tree")
                    tree.path = target
                    tree.reload()
                except Exception:
                    pass
            return f"Changed directory to {target}"
        except Exception as e:
            return f"cd error: {e}"
        
    try:
        sys_os = platform.system()
        if sys_os == "Windows":
            proc = subprocess.run(["powershell.exe", "-NoProfile", "-Command", command], cwd=base_dir, capture_output=True, text=True)
        else:
            proc = subprocess.run(command, shell=True, executable="/bin/bash", cwd=base_dir, capture_output=True, text=True)
            
        output = ""
        if proc.stdout:
            output += proc.stdout
        if proc.stderr:
            output += f"\nError:\n{proc.stderr}"
        if not output:
            output = ""
        return output
    except Exception as e:
        return f"Shell execution error: {e}"

def copy_to_clipboard(text: str):
    """Platform-independent clipboard logic."""
    sys_os = platform.system()
    try:
        if sys_os == "Darwin":
            proc = subprocess.Popen("pbcopy", env={"LANG": "en_US.UTF-8"}, stdin=subprocess.PIPE)
            proc.communicate(text.encode("utf-8"))
        elif sys_os == "Windows":
            proc = subprocess.Popen("clip", stdin=subprocess.PIPE)
            proc.communicate(text.encode("utf-16"))
        else:
            proc = subprocess.Popen(["xclip", "-selection", "clipboard"], stdin=subprocess.PIPE)
            proc.communicate(text.encode("utf-8"))
            if proc.returncode != 0:
                proc = subprocess.Popen(["xsel", "--clipboard", "--input"], stdin=subprocess.PIPE)
                proc.communicate(text.encode("utf-8"))
    except Exception:
        pass

def process_slash_command(command: str, agent_view):
    """Handle the interactive / commands."""
    parts = command.strip().split()
    cmd = parts[0]
    args = parts[1:]
    
    if cmd == "/safe":
        # Force the mode to PLAN
        agent_view.agent_mode = "PLAN"
        # Re-initialize the agent to strip EXECUTE-only tools
        #agent_view.setup_agent()
        # Update UI indicators
        agent_view.update_status_bar()
        agent_view.log_to_ui("[bold green]🔒 System locked to SAFE (PLAN) mode. Restricted tools disabled.[/bold green]")
        return
        
    elif cmd == "/config":
        # Redirect to the new F2 Chat Manager instead of Agent Editor
        agent_view.action_open_config() 
        return
    elif cmd == "/tools":
        desc = True
        if args and args[0] in["nodesc", "nodescriptions"]:
            desc = False
            
        agent_mode = getattr(agent_view, "agent_mode", "PLAN")
        active_agent = getattr(agent_view, "active_agent", None)
        enabled_tools = getattr(active_agent, "enabled_tools", []) if active_agent else []
        
        tools_list = {
            "read_file": "EXECUTE",
            "list_files": "ALWAYS",
            "curl_url": "EXECUTE",
            "search_web": "ALWAYS",
            "perform_research": "ALWAYS",
            "save_file": "EXECUTE",
            "edit_file": "EXECUTE",
            "run_terminal_command": "EXECUTE",
            "dispatch_subagent": "EXECUTE"
        }
        
        output = "[bold cyan]🛠️ Available Tools Status:[/bold cyan]\n\n"
        for t, req_mode in tools_list.items():
            if req_mode == "ALWAYS":
                status = "[bold green]ACTIVE[/bold green]"
            elif agent_mode == "EXECUTE":
                status = "[bold green]ACTIVE[/bold green]"
            elif agent_mode == "INTERMEDIATE":
                status = "[bold yellow]CONFIRMATION REQUIRED[/bold yellow]"
            elif agent_mode == "PLAN" and t in enabled_tools:
                status = "[bold yellow]CONFIRMATION REQUIRED[/bold yellow]"
            else:
                status = "[dim red]INACTIVE (Requires SEMI-ARMED or ARMED mode)[/dim red]"
                
            if desc:
                output += f" - [bold]{t}[/bold]: {status}\n"
            else:
                output += f" - {t} - {status}\n"
                
        agent_view.log_to_ui(output, is_markdown=False)
        
    elif cmd == "/arm":
        agent_view.toggle_plan_mode()
            
    elif cmd == "/ide":
        if hasattr(agent_view.app, "views") and "code_view" in agent_view.app.views:
            agent_view.log_to_ui("Switching to Text IDE...", is_markdown=False)
            agent_view.app.current_view_index = agent_view.app.views.index("code_view")
            agent_view.app.query_one("#main_switcher").current = "code_view"
            try:
                agent_view.app.query_one("#dir_tree").focus()
            except Exception:
                pass
        else:
            agent_view.log_to_ui("[bold red]IDE integration is not active in this environment.[/bold red]")
                
    elif cmd == "/init":
        try:
            app = agent_view.app
            base_dir = str(app.query_one("#dir_tree").path) if app else os.getcwd()
        except:
            base_dir = os.getcwd()
            
        init_file = os.path.join(base_dir, "TriloByte.md")
        try:
            with open(init_file, "w") as f:
                f.write("# TriloByte CLI Context\n\nProvide your project specific instructions here.")
            agent_view.log_to_ui(f"Successfully initialized `{init_file}`", is_markdown=True)
        except Exception as e:
            agent_view.log_to_ui(f"Failed to create TriloByte.md: {e}")
            
    elif cmd == "/compress":
        agent_view.log_to_ui("Analyzing chat history for technical compression...", is_markdown=False)
        from orchestration import HistoryMessage
        from langchain_openai import ChatOpenAI
        from langchain_core.messages import SystemMessage
        
        history = agent_view.session_manager.active_sessions.get(agent_view.active_agent.name, [])
        # Ensure there is enough history to compress safely (system prompt + more than 5 dialogue turns)
        if len(history) <= 6:
            agent_view.log_to_ui("Chat history is too short to compress safely.", is_markdown=False)
            return

        # Keep the system prompt (history[0]) and the last 4 messages verbatim
        keep_verbatim_count = 4
        to_summarize = history[1:-keep_verbatim_count]
        verbatim_suffix = history[-keep_verbatim_count:]
        
        # Format the intermediate history to feed to the LLM
        formatted_history = []
        for msg in to_summarize:
            role_disp = "User" if msg.role == "human" else "Agent"
            formatted_history.append(f"[{role_disp}]: {msg.content}")
        history_text = "\n".join(formatted_history)
        
        # Instantiate the active agent's LLM credentials
        agent = agent_view.active_agent
        if agent.use_backup and agent.backup_model:
            model = agent.backup_model
            base_url = agent.backup_base_url or agent.base_url
            api_key = agent.get_backup_api_key()
        else:
            model = agent.model
            base_url = agent.base_url
            api_key = agent.get_api_key()
            
        if not api_key:
            agent_view.log_to_ui("[bold red]Error: Active agent API key is missing. Compression aborted.[/bold red]")
            return
            
        try:
            llm = ChatOpenAI(model=model, api_key=api_key, base_url=base_url, temperature=0)
            
            # Instruct the LLM to build a technically precise, dense summary
            comp_prompt = f"""
            You are a system context compressor. Analyze the intermediate conversation history below.
            Generate a dense, technical, and precise Markdown state summary.
            
            The summary MUST capture:
            1. Active project paths, files being edited, and exact workspace parameters.
            2. Hard technical decisions made and agreed-upon designs/architectures.
            3. Discovered issues, constraints, errors, or dependencies.
            4. User preferences, goals, and pending tasks.
            5. Any facts established in the conversation so far.
            
            Do not lose technical specificity (such as exact filenames, functions, paths, or keys).
            
            CONVERSATION TO SUMMARIZE:
            {history_text}
            """
            
            res = llm.invoke([SystemMessage(content=comp_prompt)])
            summary_content = f"### [SYSTEM HISTORICAL RECALL SUMMARY]\n{res.content}"
            
            # Package the summary
            summary_message = HistoryMessage(role="ai", content=summary_content)
            
            # Reconstruct the history: [System Prompt] + [Summary] + [Verbatim Suffix]
            new_history = [history[0], summary_message] + verbatim_suffix
            
            # Save shrunken history
            agent_view.session_manager.active_sessions[agent_view.active_agent.name] = new_history
            agent_view.session_manager.save_session(agent_view.active_agent.name)
            
            # Sync LangGraph SQLite checkpointer by clearing the stale entries
            try:
                thread_id = f"{agent_view.session_manager.current_session_id}_{agent_view.active_agent.name}"
                from toolbox import shared_db_conn
                cursor = shared_db_conn.cursor()
                cursor.execute("DELETE FROM checkpoints WHERE thread_id = ?", (thread_id,))
                cursor.execute("DELETE FROM writes WHERE thread_id = ?", (thread_id,))
                shared_db_conn.commit()
            except Exception as e:
                agent_view.log_to_ui(f"[dim red]Checkpointer sync error: {e}[/dim red]")
                
            agent_view.log_to_ui("[bold green]Chat context successfully compressed semantically.[/bold green]")
            agent_view.update_tokens()
            
        except Exception as e:
            agent_view.log_to_ui(f"[bold red]Inference compression error: {e}[/bold red]")

    elif cmd == "/copy":
        last_ai_msg = ""
        history = agent_view.session_manager.active_sessions.get(agent_view.active_agent.name,[])
        for msg in reversed(history):
            if getattr(msg, "role", "") == "ai" and msg.content:
                last_ai_msg = msg.content
                break
                
        if last_ai_msg:
            copy_to_clipboard(last_ai_msg)
            agent_view.log_to_ui("Copied last AI message to clipboard.")
        else:
            agent_view.log_to_ui("No AI message found to copy.")

    elif cmd in["/directory", "/dir"]:
        try:
            agent_view.app.action_change_directory()
        except AttributeError:
            agent_view.log_to_ui("[bold red]Directory picker not supported in this context.[/bold red]")

    elif cmd == "/readback":
        last_ai_msg = ""
        history = agent_view.session_manager.active_sessions.get(agent_view.active_agent.name,[])
        for msg in reversed(history):
            if getattr(msg, "role", "") == "ai" and msg.content:
                last_ai_msg = msg.content
                break
                
        if last_ai_msg:
            agent_view.log_to_ui("[dim cyan]Reading back last message...[/dim cyan]")
            # --- NEW: Pass the active agent's voice configuration ---
            agent_view.tts_manager.speak(last_ai_msg, voice=agent_view.active_agent.tts_voice)
        else:
            agent_view.log_to_ui("[bold red]No AI message found to read back.[/bold red]")
    
    elif cmd == "/tts":
        agent_view.tts_enabled = not getattr(agent_view, "tts_enabled", False)
        status = "ON" if agent_view.tts_enabled else "OFF"
        agent_view.log_to_ui(f"[bold cyan]🔊 Text-to-Speech (TTS) is now {status}.[/bold cyan]")

    elif cmd == "/stt":
        # Toggle HOTWORD mode
        if getattr(agent_view.stt_manager, "mode", None) == "hotword":
            agent_view.stt_manager.stop()
            agent_view.log_to_ui("[bold yellow]🔇 Hotword STT is now OFF.[/bold yellow]")
        else:
            started = agent_view.stt_manager.start_hotword()
            if started:
                agent_view.log_to_ui("[bold green]🎙️ Hotword STT is ON. Say your trigger word to begin dictation...[/bold green]")
            else:
                agent_view.log_to_ui("[bold red]Failed to start Hotword STT. Check logs/dependencies.[/bold red]")

    elif cmd == "/speech":
        def handle_audio_config(result):
            if result == "update":
                agent_view.tts_manager.reload_config()
                agent_view.stt_manager.reload_config()
                agent_view.log_to_ui("[bold green]✅ Audio configuration successfully updated.[/bold green]")
                
        from audio_handler import AudioConfigModal
        agent_view.app.push_screen(AudioConfigModal(), handle_audio_config)
    
    elif cmd == "/telegram":
        def handle_tele_config(result):
            if result == "update":
                agent_view.telegram_manager.reload_config()
                agent_view.log_to_ui("[bold green]✅ Telegram configuration successfully updated.[/bold green]")
                
        from telegram_handler import TelegramConfigModal
        agent_view.app.push_screen(TelegramConfigModal(), handle_tele_config)
    
    elif cmd == "/schedule":
        from agent import ScheduleModal
        agent_view.app.push_screen(ScheduleModal(agent_view))
    
    elif cmd == "/select_agent":
        if not args:
            agent_view.log_to_ui("[bold red]Usage: /select_agent <agent_name>[/bold red]")
            return
        agent_name = args[0]
        if agent_view.select_agent(agent_name):
            agent_view.log_to_ui(f"[bold green]Switched active agent to: {agent_name}[/bold green]")
        else:
            agent_view.log_to_ui(f"[bold red]Agent '{agent_name}' not found.[/bold red]")

    elif cmd == "/clear_all":
        agent_view.session_manager.clear_all_contexts()
        agent_view.log_to_ui("[bold green]All agent contexts cleared.[/bold green]")
    
    elif cmd == "/dpi":
        if not args:
            # Lazily load from disk if not yet initialized in memory
            current_dpi = getattr(agent_view, "pdf_dpi", None)
            if current_dpi is None:
                current_dpi = load_pdf_dpi()
                agent_view.pdf_dpi = current_dpi
            agent_view.log_to_ui(f"[bold cyan]Current PDF rendering DPI: {current_dpi}[/bold cyan]")
            return
        try:
            dpi_val = int(args[0])
            agent_view.pdf_dpi = dpi_val
            save_pdf_dpi(dpi_val)
            agent_view.log_to_ui(f"[bold green]PDF rendering DPI dynamically set and persisted to: {dpi_val}[/bold green]")
        except ValueError:
            agent_view.log_to_ui("[bold red]Usage: /dpi <number>[/bold red]")
                
    elif cmd == "/help":
        help_text = """
### 🛠️ Available Chat Commands

| Command | Description |
|---|---|
| `/help` | Show this detailed help menu. |
| `/arm` | **Toggle tools state:** Switches between `PLAN` mode (Safe, Read-Only) and `EXECUTE` mode (Write/Execute enabled). |
| `/safe` | Takes the system into `PLAN` (Read-Only) mode. |
| `/directory` or `/dir` | Open the interactive workspace directory picker. |
| `/tools[desc]` | List all currently available AI tools. Add `desc` or `nodesc` to toggle descriptions. |
| `/ide [status]` | Manage or check the status of IDE integration. |
| `/init` | Initialize a base `GEMINI.md` project context file in the active workspace. |
| `/compress` | Compress the current chat history to save tokens while retaining key context. |
| `/copy` | Copy the last AI message directly to your system clipboard. |
| `/config` | Setup the model, provider and other details. |
| `/select_agent <name>` | Permanently switch the active host agent. |
| `/clear_all` | Wipe memory for all agents. |
| `/telegram` | Configure and activate the Telegram Bot integration. |
| `/schedule` | Open the automated daily task scheduler menu. |

### ⚡ Interactive Features
- **File Injection:** Type `&` followed by a file or directory path (e.g. `&src/main.py`). Press **`UP/DOWN`** to dynamically cycle through available files! Hit `ENTER` to inject their content into the AI's prompt context.
- **Agent Mention:** Type `@AgentName` (e.g. `@Maven tell me about...`) to route your message to a specific agent.
- **Team Mention:** Type `@team` (e.g. `@team what do you think?`) to trigger all agents in the session simultaneously.
- **Room Mention:** Type `@room` (e.g. `@room sync our progress.`) to trigger only the agents already active in this session.
- **Shell Commands:** Type `!<command>` (e.g. `!git status`) to pass a command directly to your local terminal and print the output. 
- **Shell Toggle:** Type `!` by itself and press enter to toggle your input bar directly into **Shell Mode**.
        """
        agent_view.log_to_ui(help_text, is_markdown=True)

    else:
        agent_view.log_to_ui(f"Unknown command: {cmd}. Type `/help` for a list of available commands.")

def handle_ampersand_commands(prompt: str, agent_view) -> str:
    """Inject file contents directly into the prompt and strip out ignored files."""
    try:
        app = agent_view.app
        base_dir = str(app.query_one("#dir_tree").path) if app else os.getcwd()
    except:
        base_dir = os.getcwd()
        
    # Regex to match & followed by one or more non-space or escaped space characters.
    pattern = r'&((?:\\ |\S)+)'
    
    IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.pdf'}
    
    def replacer(match):
        token = match.group(0)
        if token == "&": return "&"
        
        path_str = match.group(1).replace(r"\ ", " ")
        full_path = os.path.join(base_dir, path_str)
        
        if os.path.isfile(full_path):
            ext = os.path.splitext(full_path)[1].lower()
            if ext in IMAGE_EXTENSIONS:
                agent_view.log_to_ui(f"[#808080]Tool Result: Attached image `{path_str}`[/]")
                return f"\n[Attached Image: {full_path}]\n"
                
            try:
                lines = []
                with open(full_path, "r", encoding="utf-8", errors='replace') as f:
                    for i, line in enumerate(f):
                        lines.append(f"{i+1}: {line.rstrip('\n')}")
                content = "\n".join(lines)
                agent_view.log_to_ui(f"[#808080]Tool Result: read_many_files read `{path_str}`[/]")
                return f"\n\n--- Content of {path_str} ---\n{content}\n--- End of {path_str} ---\n\n"
            except Exception as e:
                agent_view.log_to_ui(f"[#808080]Tool Error: failed to read `{path_str}`: {e}[/]")
                return token
        elif os.path.isdir(full_path):
            try:
                content_accum = []
                processed_files = []
                ignore_dirs = {'.git', 'node_modules', '__pycache__', 'venv', '.venv', 'dist', 'build'}
                
                for root, dirs, files in os.walk(full_path):
                    dirs[:] = [d for d in dirs if d not in ignore_dirs]
                    for file in files:
                        if file == '.env': continue
                        fpath = os.path.join(root, file)
                        ext = os.path.splitext(fpath)[1].lower()
                        rel_p = os.path.relpath(fpath, base_dir).replace("\\", "/")
                        
                        # Route images and PDFs inside directories through the visual pipeline
                        if ext in IMAGE_EXTENSIONS:
                            content_accum.append(f"\n[Attached Image: {fpath}]\n")
                            processed_files.append(rel_p)
                            continue
                            
                        try:
                            lines = []
                            with open(fpath, "r", encoding="utf-8", errors='replace') as f:
                                for i, line in enumerate(f):
                                    lines.append(f"{i+1}: {line.rstrip('\n')}")
                                c = "\n".join(lines)
                                content_accum.append(f"\n--- Content of {rel_p} ---\n{c}\n--- End of {rel_p} ---")
                                processed_files.append(rel_p)
                        except:
                            pass
                            
                agent_view.log_to_ui(f"[#808080]Tool Result: read_many_files processed {len(processed_files)} files in `{path_str}`[/]")
                return "\n".join(content_accum) if content_accum else f"No readable files in {path_str}."
            except Exception as e:
                agent_view.log_to_ui(f"[#808080]Tool Error: failed to read dir `{path_str}`: {e}[/]")
                return token
        else:
            agent_view.log_to_ui(f"[#808080]Tool Error: Path not found for `{path_str}`[/]")
            return token
            
    new_prompt = re.sub(pattern, replacer, prompt)
    return new_prompt


def save_pdf_dpi(dpi: int):
    """Persists PDF DPI configuration to .meerkat folder."""
    from toolbox import get_storage_path
    path = get_storage_path("pdf_config.json")
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"pdf_dpi": dpi}, f)
    except Exception:
        pass

def load_pdf_dpi() -> int:
    """Loads persisted PDF DPI configuration from .meerkat folder."""
    from toolbox import get_storage_path
    path = get_storage_path("pdf_config.json")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f).get("pdf_dpi", 150)
        except Exception:
            pass
    return 150