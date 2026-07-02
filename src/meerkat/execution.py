import os
import sys
import re
import subprocess
from rich.markup import escape
from textual import on, work, events
from textual.app import ComposeResult
from textual.containers import Vertical, Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Label, Select, Input, Button, Static, TabbedContent, TabPane

# Unix PTY Support
try:
    import pty
    import fcntl
    import termios
    import struct
    HAS_PTY = True
except ImportError:
    HAS_PTY = False

# Windows PTY Support
try:
    import pywinpty
    HAS_PYWINPTY = True
except ImportError:
    HAS_PYWINPTY = False

# Strip ANSI codes so rendering doesn't print garbage. We manually intercept specific cursor commands.
ANSI_ESCAPE = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')

DEFAULT_RUN_CONFIGS = {
    "python": {"executable": "python", "flags": "-u \"{file}\""},
    "go": {"executable": "go", "flags": "run \"{file}\""},
    "c": {"executable": "gcc", "flags": "\"{file}\" -o \"{file_no_ext}\" && \"{file_no_ext}\""},
    "cpp": {"executable": "g++", "flags": "\"{file}\" -o \"{file_no_ext}\" && \"{file_no_ext}\""},
    "rust": {"executable": "rustc", "flags": "\"{file}\" && \"{file_no_ext}\""},
    "javascript": {"executable": "node", "flags": "\"{file}\""},
    "html": {"executable": "echo", "flags": "\"HTML execution is currently not supported via CLI IDE\""},
    "julia": {"executable": "julia", "flags": "\"{file}\""},
    "fortran": {"executable": "gfortran", "flags": "\"{file}\" -o \"{file_no_ext}\" && \"{file_no_ext}\""},
    "nim": {"executable": "nim", "flags": "c -r \"{file}\""},
    "zig": {"executable": "zig", "flags": "run \"{file}\""},
}

class SettingsModal(ModalScreen[dict]):
    """Modal to configure run parameters and executables for supported languages."""
    
    DEFAULT_CSS = """
    SettingsModal { align: center middle; background: $background 60%; }
    #settings_dialog { width: 60; height: auto; border: thick $primary; background: $surface; padding: 1 2; }
    .form-row { margin-bottom: 1; }
    #btn_container { width: 100%; align: right middle; margin-top: 1; }
    Button { margin-left: 1; }
    """

    def __init__(self, configs: dict):
        super().__init__()
        self.configs = {k: v.copy() for k, v in configs.items()}
        self.current_lang = "python"

    def compose(self) -> ComposeResult:
        with Vertical(id="settings_dialog"):
            yield Label("⚙️ Execution Settings", classes="pane_title")
            yield Select(((k, k) for k in self.configs.keys()), value=self.current_lang, id="lang_select")
            yield Label("Executable Path:")
            yield Input(self.configs[self.current_lang].get("executable", ""), id="exec_input", classes="form-row")
            yield Label("Parameters / Flags (Variables: {file}, {file_no_ext}):")
            yield Input(self.configs[self.current_lang].get("flags", ""), id="flags_input", classes="form-row")
            with Horizontal(id="btn_container"):
                yield Button("Save", id="save_btn", variant="success")
                yield Button("Cancel", id="cancel_btn", variant="error")

    @on(Select.Changed, "#lang_select")
    def on_lang_changed(self, event: Select.Changed):
        self.current_lang = event.value
        if self.current_lang in self.configs:
            self.query_one("#exec_input", Input).value = self.configs[self.current_lang].get("executable", "")
            self.query_one("#flags_input", Input).value = self.configs[self.current_lang].get("flags", "")

    @on(Input.Changed, "#exec_input")
    def on_exec_changed(self, event: Input.Changed):
        if self.current_lang:
            self.configs[self.current_lang]["executable"] = event.value

    @on(Input.Changed, "#flags_input")
    def on_flags_changed(self, event: Input.Changed):
        if self.current_lang:
            self.configs[self.current_lang]["flags"] = event.value

    @on(Button.Pressed, "#save_btn")
    def save(self):
        self.dismiss(self.configs)

    @on(Button.Pressed, "#cancel_btn")
    def cancel(self):
        self.dismiss(None)


class TerminalEmulator(VerticalScroll, can_focus=True):
    """A seamless single-view terminal emulator capable of tracking cursor movement (\r, \b) natively."""
    
    DEFAULT_CSS = """
    TerminalEmulator {
        background: $surface;
        color: $text;
        width: 100%;
        height: 1fr; /* Force fill */
        border: none;
        padding: 0 1;
    }
    #render_text { 
        width: 100%; 
        height: auto; /* Allow text to grow */
    }
    """

    def __init__(self, file_path: str, executable: str, flags: str, **kwargs):
        super().__init__(**kwargs)
        self.file_path = file_path
        self.executable = executable
        self.flags = flags
        
        self.process = None
        self.pty_master = None
        self.winpty_proc = None
        
        # Virtual Buffer State
        self.lines =[]
        self.current_line = ""
        self.cursor_col = 0
        self.max_lines = 1000
        self._process_active = True

    def compose(self) -> ComposeResult:
        yield Static(id="render_text", markup=False)

    def on_mount(self):
        self.execute_and_read()

    @work(exclusive=True, thread=True)
    def execute_and_read(self):
        file_no_ext = os.path.splitext(self.file_path)[0]
        try:
            formatted_flags = self.flags.format(file=self.file_path, file_no_ext=file_no_ext)
            cmd = f"{self.executable} {formatted_flags}"
        except KeyError as e:
            self.app.call_from_thread(self._write_raw, f"Configuration error: invalid variable {e}\n")
            return

        self.app.call_from_thread(self._write_raw, f"🚀 Executing: {cmd}\n{'-'*40}\n")
        
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1" # Ensures python output streams instantly

        try:
            if sys.platform == "win32" and HAS_PYWINPTY:
                self.winpty_proc = pywinpty.PTY(120, 30)
                self.winpty_proc.spawn(None, cmdline=cmd, cwd=os.path.dirname(self.file_path), env=env)
                
                while self._process_active:
                    try:
                        data = self.winpty_proc.read(1024)
                        if not data: break
                        self.app.call_from_thread(self.process_output, data)
                    except pywinpty.PTYError:
                        break
            
            elif HAS_PTY:
                master, slave = pty.openpty()
                self.pty_master = master
                winsize = struct.pack("HHHH", 30, 120, 0, 0) # Force dimension so tqdm calculates bars!
                fcntl.ioctl(master, termios.TIOCSWINSZ, winsize)
                
                self.process = subprocess.Popen(
                    cmd, shell=True, stdin=slave, stdout=slave, stderr=slave, 
                    cwd=os.path.dirname(self.file_path), env=env
                )
                os.close(slave) 

                while self._process_active:
                    try:
                        data = os.read(master, 1024)
                        if not data: break
                        text = data.decode('utf-8', errors='replace')
                        self.app.call_from_thread(self.process_output, text)
                    except OSError:
                        break
                self.process.wait()

            else:
                self.app.call_from_thread(self._write_raw, "[WARNING: PTY libraries missing. Interactive execution limited.]\n")
                self.process = subprocess.Popen(
                    cmd, shell=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, 
                    cwd=os.path.dirname(self.file_path), env=env
                )
                self._stdin = self.process.stdin
                while self._process_active:
                    data = self.process.stdout.read(1)
                    if not data: break
                    text = data.decode('utf-8', errors='replace')
                    self.app.call_from_thread(self.process_output, text)
                self.process.wait()

            return_code = self.process.returncode if self.process else getattr(self.winpty_proc, 'get_exitstatus', lambda: 0)()
            self.app.call_from_thread(self._write_raw, f"\n{'-'*40}\nProcess finished with exit code {return_code}\n")

        except Exception as e:
            self.app.call_from_thread(self._write_raw, f"\nExecution error: {e}\n")
        finally:
            self._process_active = False
            self.app.call_from_thread(self.update_display)
            if self.pty_master is not None:
                try: os.close(self.pty_master)
                except OSError: pass

    def _write_raw(self, text: str):
        self.process_output(text)

    def process_output(self, text: str):
        text = text.replace('\x1b[K', '\x00').replace('\033[K', '\x00') # Convert Clear to EOL to Null byte
        text = ANSI_ESCAPE.sub('', text)

        for char in text:
            if char == '\x00':
                self.current_line = self.current_line[:self.cursor_col]
            elif char == '\r':
                self.cursor_col = 0
            elif char == '\n':
                self.lines.append(self.current_line)
                self.current_line = ""
                self.cursor_col = 0
            elif char == '\b':
                self.cursor_col = max(0, self.cursor_col - 1)
            elif char == '\t':
                spaces = 4 - (self.cursor_col % 4)
                for _ in range(spaces): self._insert_char(' ')
            else:
                self._insert_char(char)
                
        if len(self.lines) > self.max_lines:
            self.lines = self.lines[-self.max_lines:]
            
        self.update_display()

    def _insert_char(self, char: str):
        if len(self.current_line) < self.cursor_col:
            self.current_line += " " * (self.cursor_col - len(self.current_line))
        self.current_line = self.current_line[:self.cursor_col] + char + self.current_line[self.cursor_col+1:]
        self.cursor_col += 1

    def update_display(self):
        display = "\n".join(self.lines)
        if display: display += "\n"
        
        if self._process_active:
            current = self.current_line[:self.cursor_col] + "█" + self.current_line[self.cursor_col+1:]
            self.query_one("#render_text", Static).update(display + current)
        else:
            self.query_one("#render_text", Static).update(display + self.current_line)
        self.scroll_end(animate=False)

    @on(events.Key)
    def on_key(self, event: events.Key):
        if not self._process_active: return
        
        char_map = {"enter": '\r', "backspace": '\x7f', "tab": '\t'}
        byte_map = {"enter": b'\r', "backspace": b'\x7f', "tab": b'\t'}

        if HAS_PYWINPTY and self.winpty_proc:
            if event.character: self.winpty_proc.write(event.character)
            elif event.key in char_map: self.winpty_proc.write(char_map[event.key])
            event.prevent_default()
            
        elif HAS_PTY and self.pty_master is not None:
            if event.character: os.write(self.pty_master, event.character.encode('utf-8'))
            elif event.key in byte_map: os.write(self.pty_master, byte_map[event.key])
            event.prevent_default()

    def terminate(self):
        if self._process_active:
            self._process_active = False
            try:
                if self.process: self.process.terminate()
                if self.winpty_proc: del self.winpty_proc
                self._write_raw("\n[⚠️ Process terminated by user.]\n")
            except Exception: pass


class ExecutionManager(Vertical):
    """A Dashboard view managing multiple running scripts inside Tabs."""

    def compose(self) -> ComposeResult:
        yield Label("⚙️ ACTIVE EXECUTIONS", classes="pane_title")
        with TabbedContent(id="exec_tabs"):
            yield TabPane("Idle", Label("\n   No active executions.\n   Press Ctrl+R in the editor to run a file.", id="idle_label"), id="idle_pane")

    def start_run(self, file_path: str, executable: str, flags: str, run_id: int):
        tabs = self.query_one("#exec_tabs", TabbedContent)
        
        try: tabs.remove_pane("idle_pane")
        except Exception: pass
        
        pane_id = f"run_pane_{run_id}"
        term = TerminalEmulator(file_path, executable, flags, id=f"term_{run_id}")
        pane = TabPane(f"{os.path.basename(file_path)} #{run_id}", term, id=pane_id)
        
        tabs.add_pane(pane)
        tabs.active = pane_id
        term.focus()

    def terminate_active(self) -> bool:
        """Terminates active tab. Returns True if there are still other runs alive."""
        tabs = self.query_one("#exec_tabs", TabbedContent)
        active = tabs.active
        
        if active and active.startswith("run_pane_"):
            try:
                term = self.query_one(f"#{active}", TabPane).query_children(TerminalEmulator).first()
                term.terminate()
                tabs.remove_pane(active)
            except Exception: pass
            
            # If no Tabs are left, show idle screen again
            if len(tabs.query(TabPane)) == 0:
                tabs.add_pane(TabPane("Idle", Label("\n   No active executions.\n   Press Ctrl+R in the editor to run a file.", id="idle_label"), id="idle_pane"))
                tabs.active = "idle_pane"
                return False
            return True
        return False