import sys
import os

# Ensure package directory is in sys.path to resolve absolute imports of submodules
package_dir = os.path.dirname(os.path.abspath(__file__))
if package_dir not in sys.path:
    sys.path.insert(0, package_dir)

import types
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv()

# ---------------------------------------------------------------------------
# Dependency Injection: 
# agent.py imports EXT_MAP from textIDE. We mock it here dynamically so 
# we don't need to modify agent.py or keep textIDE.py around.
# ---------------------------------------------------------------------------
mock_textIDE = types.ModuleType("textIDE")
mock_textIDE.EXT_MAP = {
    ".py": "python", ".go": "go", ".c": "c", ".h": "c", 
    ".cpp": "cpp", ".hpp": "cpp", ".cc": "cpp", ".rs": "rust",
    ".html": "html", ".htm": "html", ".js": "javascript",
    ".jl": "julia", ".f90": "fortran", ".f95": "fortran", ".f": "fortran",
    ".nim": "nim", ".zig": "zig"
}
sys.modules["textIDE"] = mock_textIDE

import platform
from pathlib import Path

def get_safe_starting_dir() -> str:
    """Returns a highly permissive, user-specific safe directory and creates it if missing."""
    if platform.system() == "Windows":
        # Safe, highly permissive path in the user profile (no admin rights needed)
        path = Path.home() / "MeerkatWorkspace"
    elif platform.system() == "Darwin": # macOS
        path = Path.home() / "Documents" / "MeerkatWorkspace"
    else: # Linux / Others
        path = Path.home() / "MeerkatWorkspace"
    
    # Automatically create the folder (and any parent folders) if it doesn't exist yet
    path.mkdir(parents=True, exist_ok=True)
    return str(path.absolute())

SAFE_START_DIR = get_safe_starting_dir()

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, Horizontal
from textual.widgets import Header, Footer, DirectoryTree, Label, Button
from textual.screen import ModalScreen
from textual import on

# Import the unmodified agent view
from agent import AIAgentView

# Required for agent's execute_code tool since textIDE is stripped away
DEFAULT_RUN_CONFIGS = {
    "python": {"executable": "python", "flags": "-u \"{file}\""},
    "go": {"executable": "go", "flags": "run \"{file}\""},
    "c": {"executable": "gcc", "flags": "\"{file}\" -o \"{file_no_ext}\" && \"{file_no_ext}\""},
    "cpp": {"executable": "g++", "flags": "\"{file}\" -o \"{file_no_ext}\" && \"{file_no_ext}\""},
    "rust": {"executable": "rustc", "flags": "\"{file}\" && \"{file_no_ext}\""},
    "javascript": {"executable": "node", "flags": "\"{file}\""},
}

class ExplorerTree(DirectoryTree):
    """A directory tree with bindings for modal navigation."""
    BINDINGS =[
        Binding("backspace", "go_up", "Dir Up", show=True),
        Binding("ctrl+b", "enter_dir", "Enter Dir", show=True)
    ]
    
    def action_go_up(self):
        """Navigate up one level."""
        self.path = str(Path(self.path).parent.absolute())
        
    def action_enter_dir(self):
        """Set the highlighted directory as the new visual root."""
        if self.cursor_node and self.cursor_node.data:
            path = self.cursor_node.data.path
            if path.is_dir():
                self.path = str(path.absolute())


class DirectoryModal(ModalScreen[str]):
    """Modal allowing the user to browse and select a workspace directory."""
    
    DEFAULT_CSS = """
    DirectoryModal {
        align: center middle;
        background: $background 60%;
    }
    #dir_dialog {
        width: 80;
        height: 80%;
        border: thick $primary;
        background: $surface;
        padding: 1 2;
    }
    .pane_title {
        background: $primary; 
        color: $text; 
        text-align: center; 
        text-style: bold; 
        width: 100%; 
    }
    .help_text {
        color: $text-muted;
        text-align: center;
        margin-bottom: 1;
    }
    #dir_tree_modal {
        height: 1fr;
        border: round $accent;
    }
    #dir_tree_modal:focus {
        border: round $success;
    }
    .buttons {
        height: auto;
        align: right middle;
        margin-top: 1;
    }
    Button { margin-left: 1; }
    """

    def __init__(self, current_path: str):
        super().__init__()
        self.current_path = current_path

    def compose(self) -> ComposeResult:
        with Vertical(id="dir_dialog"):
            yield Label("📂 Select Working Directory", classes="pane_title")
            yield Label("Navigate: ↑/↓ | Expand: Enter | Enter Dir: Ctrl+B | Go Up: Backspace", classes="help_text")
            yield ExplorerTree(self.current_path, id="dir_tree_modal")
            
            with Horizontal(classes="buttons"):
                yield Button("Select Current Root", id="btn_select", variant="success")
                yield Button("Cancel", id="btn_cancel", variant="error")

    def on_mount(self):
        self.query_one("#dir_tree_modal").focus()

    @on(Button.Pressed, "#btn_select")
    def select_dir(self):
        tree = self.query_one("#dir_tree_modal", ExplorerTree)
        self.dismiss(tree.path)

    @on(Button.Pressed, "#btn_cancel")
    def cancel(self):
        self.dismiss(None)


import toolbox

class MeerKat(App):
    """The main harness wrapper for agent.py"""
    
    CSS = """
    #dir_tree { 
        display: none; 
    }
    AIAgentView { 
        height: 1fr; 
        width: 100%; 
    }
    /* Force override the CSS in agent.py that hides the footer */
    Footer { 
        display: block !important; 
        visibility: visible !important; 
        height: 1 !important; 
    }
    """
    
    BINDINGS = [
        Binding("f8", "change_directory", "Change Dir", priority=True),
        Binding("ctrl+q", "quit", "Quit", priority=True),
    ]
    
    def __init__(self):
        super().__init__()
        # Satisfy agent.py's execute_code configuration dependencies
        self.run_configs = {k: v.copy() for k, v in DEFAULT_RUN_CONFIGS.items()}
    
    def on_mount(self):
        try:
            import os
            os.chdir(self.query_one("#dir_tree").path)
        except Exception:
            pass
        
    def action_quit(self):
        """Signals background threads to stop, clears the UI, and kills the process after a short delay."""
        import threading
        import os
        
        # 1. Signal all internal logic to stop
        toolbox.ABORT_EVENT.set()
        try:
            self.query_one("#ai_agent_view").action_abort()
        except:
            pass
            
        # 2. Start a 'Dead Man's Switch' timer. 
        # This gives the UI 200ms to clear the screen and restore the terminal 
        # before we forcefully terminate the process (and any hung threads).
        def hard_kill():
            print('\033[?25h', end='', flush=True)
            os._exit(0)
        
        kill_timer = threading.Timer(2.0, hard_kill)
        kill_timer.daemon = True
        kill_timer.start()
        
        # 3. Standard polite exit (restores terminal state)
        self.exit()
        
        # 4. Clear terminal after TUI closes
        os.system('cls' if os.name == 'nt' else 'clear')
        
    def compose(self) -> ComposeResult:
        yield Header()
        # Initializing with the OS-specific SAFE_START_DIR
        yield DirectoryTree(SAFE_START_DIR, id="dir_tree") 
        yield AIAgentView(id="ai_agent_view")
        yield Footer()
        
    def action_change_directory(self):
        # Fallback to SAFE_START_DIR if the tree is somehow unavailable
        try:
            current_path = self.query_one("#dir_tree").path
        except:
            current_path = SAFE_START_DIR
            
        def handle_dir_selection(new_path):
            if new_path:
                hidden_tree = self.query_one("#dir_tree", DirectoryTree)
                hidden_tree.path = new_path
                hidden_tree.reload()
                
                # Sync process directory
                try:
                    import os
                    os.chdir(new_path)
                except Exception:
                    pass
                
                agent_view = self.query_one("#ai_agent_view", AIAgentView)
                if hasattr(agent_view, "update_status_bar"):
                    agent_view.update_status_bar()
                    
        self.push_screen(DirectoryModal(current_path), handle_dir_selection)

def main():
    app = MeerKat()
    app.run()

if __name__ == "__main__":
    main()