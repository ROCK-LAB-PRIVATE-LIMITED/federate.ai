import os
import sys
import glob
import threading
import subprocess
import requests
import fnmatch
import re
from markdownify import markdownify as md

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.binding import Binding
from textual.widgets import RichLog, Input, Label, Button, Select, Static, ProgressBar, Checkbox, ListView, ListItem, TextArea
from textual.screen import ModalScreen
from textual import work, on
from textual.message import Message
from textual import events

from commands import ChatSuggester, process_shell_command, process_slash_command, handle_ampersand_commands

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage, AIMessageChunk, ToolMessage, messages_to_dict, messages_from_dict
# --- GEMINI THOUGHT SIGNATURE MONKEY-PATCH FOR OPENAI COMPATIBILITY ---
try:
    import langchain_openai.chat_models.base as langchain_openai_base
    
    _orig_convert_message_to_dict = langchain_openai_base._convert_message_to_dict

    def _patched_convert_message_to_dict(message, *args, **kwargs):
        msg_dict = _orig_convert_message_to_dict(message, *args, **kwargs)
        if isinstance(message, AIMessage) or msg_dict.get("role") == "assistant":
            tool_calls = msg_dict.get("tool_calls")
            if tool_calls:
                sig_map = {}
                raw_tool_calls = message.additional_kwargs.get("tool_calls", [])
                for rtc in raw_tool_calls:
                    rtc_id = rtc.get("id")
                    extra = rtc.get("extra_content") or {}
                    google = extra.get("google") or {}
                    sig = google.get("thought_signature")
                    if sig and rtc_id:
                        sig_map[rtc_id] = sig
                
                for tc in tool_calls:
                    tc_id = tc.get("id")
                    # Fallback to satisfy the validation schema if standard signatures are lost
                    sig = sig_map.get(tc_id) or "skip_thought_signature_validator"
                    tc["extra_content"] = {
                        "google": {
                            "thought_signature": sig
                        }
                    }
        return msg_dict

    langchain_openai_base._convert_message_to_dict = _patched_convert_message_to_dict
except Exception:
    pass
# ----------------------------------------------------------------------------
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent

from rich.markup import escape
from rich.markdown import Markdown
from rich.text import Text
from rich.rule import Rule
from rich.spinner import Spinner

from typing import Any, List, Dict, Optional

from pathlib import Path

import json
import glob
from datetime import datetime

try:
    import tiktoken
    HAS_TIKTOKEN = True
except ImportError:
    HAS_TIKTOKEN = False

from toolbox import (
    get_storage_path,
    read_file, 
    save_file, 
    edit_file, 
    list_files, 
    run_terminal_command, 
    curl_url, 
    search_web,
    perform_research,
    manage_agenda,
    get_user_clarification,
    search_episodic_memory,
    retrieve_episodic_memory,
    load_dynamic_tools,
    prepare_active_skill,
    finalize_active_skill,
    manage_active_skill,
    fix_active_skill,
    take_screenshot,
    click_at_current_location,
    move_cursor_absolute,
    move_cursor_relative,
    send_scroll,
    inject_keyboard_input
)
from toolbox import shared_memory, update_core_memory, save_skill, read_skill, distill_journey, mark_quagmire
import time
import toolbox
from subagents import dispatch_subagent

from audio_handler import TTSManager, STTManager, AudioConfigModal
from telegram_handler import TelegramManager
from orchestration import AgentManager, SessionManager, AgentConfig, HistoryMessage, ScheduleManager

# --- MODAL SCREENS ---
class ToolConfirmationModal(ModalScreen[bool]):
    DEFAULT_CSS = """
    ToolConfirmationModal { align: center middle; background: $background 60%; }
    #confirm_dialog { width: 70; max-height: 80%; border: thick $warning; background: $surface; padding: 1 2; }
    #args_scroll { margin: 1 0; height: auto; max-height: 15; border: round $primary; background: $boost; padding: 1 2; }
    .buttons { height: auto; align: right middle; margin-top: 1; }
    .buttons Button { margin-left: 1; }
    """
    def __init__(self, tool_name: str, arguments: dict, agent_name: str = "Agent"):
        super().__init__()
        self.tool_name = tool_name
        self.arguments = arguments
        self.agent_name = agent_name

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm_dialog"):
            yield Label(f"⚠️ Tool Authorization: [bold yellow]{self.tool_name}[/] requested by [bold cyan]{self.agent_name}[/]", classes="pane_title")
            yield Label("[dim]Verify the requested arguments before executing:[/dim]")
            
            with VerticalScroll(id="args_scroll"):
                try:
                    formatted_args = json.dumps(self.arguments, indent=4)
                except Exception:
                    formatted_args = str(self.arguments)
                yield Static(formatted_args)
                
            with Horizontal(classes="buttons"):
                yield Button("Approve (Execute)", id="approve", variant="success")
                yield Button("Reject (Abort)", id="reject", variant="error")

    def on_mount(self):
        self.query_one("#approve").focus()

    @on(Button.Pressed, "#approve")
    def on_approve(self):
        self.dismiss(True)

    @on(Button.Pressed, "#reject")
    def on_reject(self):
        self.dismiss(False)
        
class ClarificationModal(ModalScreen[str]):
    DEFAULT_CSS = """
    ClarificationModal { align: center middle; background: $background 60%; }
    #clarify_dialog { width: 60; max-height: 80%; border: thick $primary; background: $surface; padding: 1 2; }
    #options_list { margin: 1 0; height: auto; max-height: 15; border: round $primary; background: $boost; }
    #options_list ListItem { padding: 1; border-bottom: solid $primary 10%; }
    #options_list ListItem:hover { background: $accent 20%; }
    #clarify_input { margin-top: 1; border: tall $primary; }
    .buttons { height: auto; align: right middle; margin-top: 1; }
    """
    def __init__(self, options: Optional[List[str]] = None, agent_name: str = "Agent"):
        super().__init__()
        self.options = options or []
        self.agent_name = agent_name

    def compose(self) -> ComposeResult:
        with Vertical(id="clarify_dialog"):
            yield Label(f"🤔 Clarification: [bold cyan]{self.agent_name}[/]", classes="pane_title")
            if self.options:
                yield Label(f"[dim]Select an option for {self.agent_name} or type below:[/dim]")
                with ListView(id="options_list"):
                    for opt in self.options:
                        yield ListItem(Label(opt))
            else:
                yield Label(f"[dim]{self.agent_name} needs more information:[/dim]")
            
            yield Input(placeholder="Type your response and press Enter...", id="clarify_input")
            
            with Horizontal(classes="buttons"):
                yield Button("Cancel (Abort)", id="cancel", variant="error")

    def on_mount(self):
        self.query_one("#clarify_input").focus()

    @on(ListView.Selected)
    def on_option_selected(self, event: ListView.Selected):
        try:
            idx = event.list_view.index
            if idx is not None and 0 <= idx < len(self.options):
                self.dismiss(self.options[idx])
        except:
            pass

    @on(Input.Submitted, "#clarify_input")
    def on_input_submitted(self, event: Input.Submitted):
        if event.value.strip():
            self.dismiss(event.value.strip())

    @on(Button.Pressed, "#cancel")
    def on_cancel(self):
        self.dismiss("")

class ChatLoadModal(ModalScreen[str]):
    DEFAULT_CSS = """
    ChatLoadModal { align: center middle; background: $background 60%; }
    #chat_load_dialog { width: 60; height: 70%; border: thick $primary; background: $surface; padding: 1 2; }
    #chat_list { margin: 1 0; height: 1fr; border: round $primary; background: $boost; }
    #chat_list Button { width: 100%; margin-bottom: 0; border: none; content-align: left middle; }
    """
    def compose(self) -> ComposeResult:
        files = sorted(glob.glob(get_storage_path("sessions", "*.json")), key=os.path.getmtime, reverse=True)
        self.file_map = {f"c_{i}": f for i, f in enumerate(files)}
        with Vertical(id="chat_load_dialog"):
            yield Label("📂 Load Session History", classes="pane_title")
            with VerticalScroll(id="chat_list"):
                if not files: yield Label("  No sessions found.")
                for btn_id, path in self.file_map.items():
                    yield Button(f"📜 {os.path.basename(path)}", id=btn_id)
            yield Button("Cancel", id="cancel", variant="error")

    @on(Button.Pressed)
    def handle_click(self, event: Button.Pressed):
        if event.button.id == "cancel": self.dismiss(None)
        else: self.dismiss(self.file_map.get(event.button.id))

class SwitchAgentModal(ModalScreen[dict]):
    DEFAULT_CSS = """
    SwitchAgentModal { align: center middle; background: $background 60%; }
    #switch_dialog { width: 45; max-height: 70%; border: thick $primary; background: $surface; padding: 1 2; }
    #agent_list { margin: 1 0; height: auto; max-height: 12; overflow-y: scroll; border: round $primary; background: $boost; }
    #agent_list Button { width: 100%; margin-bottom: 0; border: none; content-align: left middle; }
    """
    def __init__(self, agents: List[str], current_default: str):
        super().__init__()
        self.agents = agents
        self.current_default = current_default

    def compose(self) -> ComposeResult:
        with Vertical(id="switch_dialog"):
            yield Label("👥 Switch Active Agent", classes="pane_title")
            yield Checkbox("Set as Default Agent", id="set_default")
            yield Label(f"[dim]Default: {self.current_default}[/dim]", classes="status_center")
            with VerticalScroll(id="agent_list"):
                for name in self.agents:
                    yield Button(f"👤 {name}", id=f"sel_{name}")
            yield Button("Cancel", id="cancel", variant="error")

    @on(Button.Pressed)
    def handle_click(self, event: Button.Pressed):
        if event.button.id == "cancel": self.dismiss(None)
        elif event.button.id.startswith("sel_"):
            self.dismiss({"name": event.button.id[4:], "default": self.query_one("#set_default", Checkbox).value})

class ChatManagerModal(ModalScreen[str]):
    DEFAULT_CSS = """
    ChatManagerModal { align: center middle; background: $background 60%; }
    #chat_mgr_dialog { width: 40; height: auto; border: thick $primary; background: $surface; padding: 1 2; }
    #chat_mgr_dialog Button { width: 100%; margin-bottom: 1; }
    """
    def compose(self) -> ComposeResult:
        with Vertical(id="chat_mgr_dialog"):
            yield Label("💬 Session Manager", classes="pane_title")
            yield Button("🆕 New Chat Session", id="new_session", variant="success")
            yield Button("📂 Load Saved Session", id="load_chat", variant="primary")
            yield Button("❌ Cancel", id="cancel", variant="error")
    @on(Button.Pressed)
    def handle_click(self, event: Button.Pressed): self.dismiss(event.button.id)


class KeyringUnlockModal(ModalScreen[str]):
    DEFAULT_CSS = """
    KeyringUnlockModal { align: center middle; background: $background 60%; }
    #unlock_dialog { width: 50; height: auto; border: thick $primary; background: $surface; padding: 1 2; }
    #unlock_dialog Input { margin-bottom: 1; }
    #unlock_dialog .pane_title { background: $primary; color: $text; padding: 0 1; margin-bottom: 1; text-style: bold; width: 100%; text-align: center; }
    """
    def compose(self) -> ComposeResult:
        with Vertical(id="unlock_dialog"):
            yield Label("🔐 Keyring Locked", classes="pane_title")
            yield Label("Please enter your Master Password to unlock the encrypted keyring:")
            yield Input(placeholder="Master Password", id="master_pwd", password=True)
            with Horizontal():
                yield Button("Unlock", id="unlock_btn", variant="primary")
                yield Button("Cancel", id="cancel_btn", variant="error")

    @on(Button.Pressed, "#unlock_btn")
    def unlock(self):
        self.dismiss(self.query_one("#master_pwd").value)

    @on(Button.Pressed, "#cancel_btn")
    def cancel(self):
        self.dismiss(None)


class ConfigModal(ModalScreen[str]):
    DEFAULT_CSS = """
    ConfigModal { align: center middle; background: $background 60%; }
    #config_dialog { width: 75; max-height: 90vh; border: round $primary; background: $surface; padding: 0 0; }
    #config_scroll { padding: 1 2; }
    #actions_container {
        height: 6; 
        align: center middle;
        margin-bottom: 1;
        border-top: solid $primary;
    }
    .details_box { padding: 0 1; margin-bottom: 0; height: auto; background: $surface;}
    .config_row { layout: horizontal; height: auto; margin-top: 1; }
    .config_row Checkbox { width: 45%; }
    .section_label { background: $primary; color: $text; padding: 0 1; margin-top: 1; text-style: bold; }
    #abilities_container {
        border: round $primary;
        height: 10;
        padding: 0 1;
        margin-top: 1;
        background: $boost;
    }
    """
    def __init__(self, agent_config: AgentConfig, agent_manager: AgentManager):
            super().__init__()
            self.agent_config = agent_config
            self.agent_manager = agent_manager
            self.high_priv_tools = ["read_file", "curl_url", "save_file", "edit_file", "dispatch_subagent", "run_terminal_command", "visual_computer_operation"]

    def compose(self) -> ComposeResult:
        with Vertical(id="config_dialog"):
            yield Label(f"Agent Editor: {self.agent_config.name}", classes="pane_title")
            with VerticalScroll(id="config_scroll"):
                with Vertical(classes="details_box"):
                    yield Label("Primary Settings", classes="section_label")
                    yield Label("Agent Name:")
                    yield Input(value=self.agent_config.name, id="ai_name")
                    yield Label("Backstory:")
                    yield Input(value=self.agent_config.backstory, id="ai_backstory")
                    yield Label("Model:")
                    yield Input(value=self.agent_config.model, id="ai_model")
                    yield Label("Base URL:")
                    yield Input(value=self.agent_config.base_url, id="ai_base_url")
                    yield Label("API Key:")
                    yield Input(value=self.agent_config.get_api_key(), id="ai_api_key", password=True)
                    yield Label("Agent Color (Hex):")
                    yield Input(value=self.agent_config.color, id="ai_color")
                    yield Label("TTS Voice:") # <-- NEW
                    yield Input(value=self.agent_config.tts_voice, id="ai_tts_voice", placeholder="e.g. af_sarah, am_adam") # <-- NEW
                    yield Label("Pronouns:") # <-- NEW
                    yield Select([("He/Him", "he/him"), ("She/Her", "she/her")], value=self.agent_config.pronouns, id="ai_pronouns") # <-- NEW
                    with Horizontal(classes="config_row"):
                        yield Checkbox("Vision Capable", id="ai_vision_capable", value=self.agent_config.is_capable_vision)

                    yield Label("Agent Abilities (Enabled in SAFE mode)", classes="section_label")
                    with VerticalScroll(id="abilities_container"):
                        for tool_name in self.high_priv_tools:
                            is_enabled = tool_name in self.agent_config.enabled_tools
                            label = "Autonomous Visual Computer Operation" if tool_name == "visual_computer_operation" else tool_name.replace("_", " ").title()
                            yield Checkbox(label, id=f"ability_{tool_name}", value=is_enabled)

                    yield Label("Backup Inference", classes="section_label")
                    agent_options = [("Manual Entry", "manual")] + [(name, name) for name in self.agent_manager.agents.keys()]
                    yield Label("Copy from Agent:")
                    yield Select(agent_options, value="manual", id="ai_copy_from")

                    yield Label("Backup Model:")
                    yield Input(value=self.agent_config.backup_model, id="ai_backup_model")
                    yield Label("Backup Base URL:")
                    yield Input(value=self.agent_config.backup_base_url, id="ai_backup_base_url")
                    yield Label("Backup API Key:")
                    yield Input(value=self.agent_config.get_backup_api_key(), id="ai_backup_api_key", password=True)

                    with Horizontal(classes="config_row"):
                        yield Checkbox("Use Backup Provider", id="ai_use_backup", value=self.agent_config.use_backup)

            with Horizontal(id="actions_container"):
                yield Button("Update Active", id="ai_save_btn", variant="primary")
                yield Button("Save As New", id="ai_save_new_btn", variant="success")
                yield Button("Delete Agent", id="ai_delete_btn", variant="error")
                yield Button("Close", id="ai_cancel_btn")

    @on(Select.Changed, "#ai_copy_from")
    def on_copy_from_changed(self, event: Select.Changed):
        if event.value == "manual" or not event.value:
            return

        agent_name = str(event.value)
        agent = self.agent_manager.get_agent(agent_name)
        if agent:
            self.query_one("#ai_backup_model", Input).value = agent.model
            self.query_one("#ai_backup_base_url", Input).value = agent.base_url
            self.query_one("#ai_backup_api_key", Input).value = agent.get_api_key()

    def _get_current_fields(self):
        enabled_tools = []
        for tool_name in self.high_priv_tools:
            if self.query_one(f"#ability_{tool_name}", Checkbox).value:
                enabled_tools.append(tool_name)

        return {
            "name": self.query_one("#ai_name", Input).value.strip(),
            "backstory": self.query_one("#ai_backstory", Input).value.strip(),
            "model": self.query_one("#ai_model", Input).value.strip(),
            "base_url": self.query_one("#ai_base_url", Input).value.strip(),
            "is_capable_vision": self.query_one("#ai_vision_capable", Checkbox).value,
            "api_key": self.query_one("#ai_api_key", Input).value.strip(),
            "color": self.query_one("#ai_color", Input).value.strip() or "#00FFFF",
            "backup_model": self.query_one("#ai_backup_model", Input).value.strip(),
            "backup_base_url": self.query_one("#ai_backup_base_url", Input).value.strip(),
            "backup_api_key": self.query_one("#ai_backup_api_key", Input).value.strip(),
            "use_backup": self.query_one("#ai_use_backup", Checkbox).value,
            "enabled_tools": enabled_tools,
            "tts_voice": self.query_one("#ai_tts_voice", Input).value.strip() or "af_sarah", # <-- NEW
            "pronouns": self.query_one("#ai_pronouns", Select).value or "she/her" # <-- NEW
        }

    def _apply_save(self, is_new: bool):
        fields = self._get_current_fields()
        if not fields["name"]:
            return

        # Handle Termux/EncryptedKeyring blocking
        from toolbox import get_locked_keyring, unlock_keyring
        locked_keyring = get_locked_keyring()
        if locked_keyring:
            def handle_unlock(password):
                if password:
                    if unlock_keyring(password):
                        self.notify("Keyring unlocked. Saving...", severity="information")
                        # Re-run save now that it's unlocked
                        self._apply_save(is_new)
                    else:
                        self.notify("Unlock failed.", severity="error")
            
            self.app.push_screen(KeyringUnlockModal(), handle_unlock)
            return

        new_config = AgentConfig(
            name=fields["name"],
            backstory=fields["backstory"],
            model=fields["model"],
            base_url=fields["base_url"],
            is_capable_vision=fields["is_capable_vision"],
            color=fields["color"],
            backup_model=fields["backup_model"],
            backup_base_url=fields["backup_base_url"],
            use_backup=fields["use_backup"],
            enabled_tools=fields["enabled_tools"],
            tts_voice=fields["tts_voice"], # <-- NEW
            pronouns=fields["pronouns"] # <-- NEW
        )


        # Save keys to .env
        self._update_env(new_config.name, fields["api_key"], fields["backup_api_key"])

        self.dismiss(("save", new_config, is_new))

    def _update_env(self, agent_name: str, primary_key: str, backup_key: str):
        # We keep the function name '_update_env' to avoid changing other callers
        import keyring
        primary_user = f"agent_key_{agent_name.lower().replace(' ', '_')}"
        backup_user = f"agent_backup_key_{agent_name.lower().replace(' ', '_')}"

        try:
            # 1. Update Primary Credential
            if primary_key:
                keyring.set_password("Federate", primary_user, primary_key)
                os.environ[f"AGENT_KEY_{agent_name.upper().replace(' ', '_')}"] = primary_key
            else:
                try: keyring.delete_password("Federate", primary_user)
                except Exception: pass

            # 2. Update Backup Credential
            if backup_key:
                keyring.set_password("Federate", backup_user, backup_key)
                os.environ[f"AGENT_BACKUP_KEY_{agent_name.upper().replace(' ', '_')}"] = backup_key
            else:
                try: keyring.delete_password("Federate", backup_user)
                except Exception: pass

        except Exception as e:
            # Gracefully fail and natively notify the user that storage was rejected
            self.notify(
                f"Storage Error: Could not save credentials securely to OS Keyring.\nDetail: {e}",
                severity="error", 
                title="Keychain Access Failed"
            )

    @on(Button.Pressed, "#ai_save_btn")
    def save_btn(self):
        self._apply_save(is_new=False)

    @on(Button.Pressed, "#ai_save_new_btn")
    def save_new_btn(self):
        self._apply_save(is_new=True)

    @on(Button.Pressed, "#ai_delete_btn")
    def delete_btn(self):
        self.dismiss(("delete", self.agent_config))

    @on(Button.Pressed, "#ai_cancel_btn")
    def cancel_btn(self):
        self.dismiss(("cancel", None))

# --- TEXTUAL AI UI WIDGET ---

# Mapping of slash command descriptions for the table
SLASH_COMMAND_DESCS = {
    "/tools": "List status of all available AI tools",
    "/tools desc": "List tools with detailed descriptions",
    "/tools nodesc": "List tools without descriptions",
    "/arm": "Toggle ARM/SAFE (Execute/Plan) mode",
    "/config": "Open active agent configuration",
    "/safe": "Lock system to SAFE (Plan, read-only) mode",
    "/ide": "Switch focus to the integrated Text IDE",
    "/ide disable": "Disable IDE integration",
    "/ide enable": "Enable IDE integration",
    "/ide install": "Install IDE environment packages",
    "/ide status": "Show IDE integration status",
    "/init": "Create GEMINI.md project instructions file",
    "/compress": "Compress chat context to save tokens",
    "/copy": "Copy last AI response to system clipboard",
    "/directory": "Open interactive directory picker",
    "/dir": "Open interactive directory picker",
    "/tts": "Toggle Text-to-Speech (TTS) voice output",
    "/stt": "Toggle Speech-to-Text (STT) hotword listening",
    "/readback": "Read back the last AI response with TTS",
    "/speech": "Open Audio/Voice configuration modal",
    "/telegram": "Configure Telegram Bot integration",
    "/select_agent": "Switch the active host agent",
    "/clear_all": "Wipe memory and history of all agents",
    "/help": "Show this detailed help menu"
}

class ScheduleModal(ModalScreen[None]):
    DEFAULT_CSS = """
    ScheduleModal { align: center middle; background: $background 60%; }
    #sched_dialog { width: 75; height: 85%; border: thick $primary; background: $surface; padding: 1 2; }
    #task_list { height: 1fr; border: round $accent; overflow-y: auto; margin-bottom: 1; background: $boost; }
    .task_item { layout: horizontal; height: auto; padding: 1; border-bottom: solid $primary 50%; }
    .task_info { width: 1fr; }
    .task_del_btn { width: 10; margin-left: 1; margin-top: 1; }
    .task_edit_btn { width: 10; margin-left: 1; margin-top: 1; }
    #add_form { border-top: solid $primary; padding-top: 1; height: auto; }
    .form_row { layout: horizontal; height: auto; margin-bottom: 1; }
    #new_agent { width: 40%; }
    #new_time { width: 60%; margin-left: 1; }
    """
    def __init__(self, agent_view):
        super().__init__()
        self.agent_view = agent_view
        
    def compose(self) -> ComposeResult:
        with Vertical(id="sched_dialog"):
            yield Label("⏰ Scheduled Tasks", classes="pane_title")
            yield VerticalScroll(id="task_list")
            
            with Vertical(id="add_form"):
                yield Label("Add New Daily Task:")
                with Horizontal(classes="form_row"):
                    agents = [(a, a) for a in self.agent_view.agent_manager.agents.keys()]
                    yield Select(agents, id="new_agent", prompt="Select Agent")
                    yield Input(placeholder="HH:MM (24h format, e.g., 14:30)", id="new_time")
                yield Input(placeholder="Task Prompt...", id="new_prompt", classes="form_row")
                with Horizontal(classes="form_row"):
                    yield Button("Add Task", id="add_task_btn", variant="success")
                    yield Button("Close", id="close_btn", variant="error")

    def on_mount(self):
        self.refresh_list()
        
    def refresh_list(self):
        container = self.query_one("#task_list")
        container.query("*").remove()
        for t in self.agent_view.schedule_manager.tasks:
            info = f"[bold cyan]{t.agent_name}[/bold cyan] @ [bold yellow]{t.time_str}[/bold yellow]\n[dim]{t.prompt}[/dim]"
            row = Horizontal(
                Label(info, classes="task_info"),
                Button("Edit", id=f"edit_{t.id}", variant="primary", classes="task_edit_btn"),
                Button("Delete", id=f"del_{t.id}", variant="error", classes="task_del_btn"),
                classes="task_item"
            )
            container.mount(row)

    @on(Button.Pressed)
    def handle_buttons(self, event: Button.Pressed):
        btn_id = event.button.id
        if btn_id == "close_btn":
            self.dismiss()
        elif btn_id == "add_task_btn":
            agent = self.query_one("#new_agent", Select).value
            time_str = self.query_one("#new_time", Input).value.strip()
            prompt = self.query_one("#new_prompt", Input).value.strip()
            
            if agent and Select.BLANK != agent and time_str and prompt:
                # Basic validation for HH:MM
                if len(time_str) == 5 and ":" in time_str:
                    self.agent_view.schedule_manager.add_task(agent, time_str, prompt)
                    self.query_one("#new_time", Input).value = ""
                    self.query_one("#new_prompt", Input).value = ""
                    self.refresh_list()
                    self.notify("Task added successfully.", severity="information")
                else:
                    self.notify("Time must be in HH:MM format (e.g., 08:30 or 15:00)", severity="error")
        elif btn_id and btn_id.startswith("del_"):
            task_id = btn_id[4:]
            self.agent_view.schedule_manager.delete_task(task_id)
            self.refresh_list()
        elif btn_id and btn_id.startswith("edit_"):
            task_id = btn_id[5:]
            task = next((t for t in self.agent_view.schedule_manager.tasks if t.id == task_id), None)
            if task:
                # 1. Populate the input widgets with the existing properties
                self.query_one("#new_agent", Select).value = task.agent_name
                self.query_one("#new_time", Input).value = task.time_str
                self.query_one("#new_prompt", Input).value = task.prompt
                
                # 2. Delete the old task and update the list
                self.agent_view.schedule_manager.delete_task(task_id)
                self.refresh_list()
                self.notify("Loaded task into inputs for modification.", severity="information")

class ChatInput(TextArea):
    """Custom Multiline TextArea that supports cycling suggestions and Enter-to-submit."""
    
    BINDINGS = [
        Binding("ctrl+a", "abort", "Abort", show=True),
    ]

    class AbortRequest(Message):
        pass

    class Submitted(Message):
        """Internal message to trigger chat submission."""
        def __init__(self, input_widget, value: str):
            super().__init__()
            self.input = input_widget
            self.value = value

    def action_abort(self) -> None:
        self.post_message(self.AbortRequest())

    def __init__(self, *args, **kwargs):
        # TextArea doesn't use the suggester or placeholder kwargs, so we pop them
        kwargs.pop("suggester", None)
        kwargs.pop("placeholder", None)
        super().__init__(*args, **kwargs)
        self.show_line_numbers = False
        self._suggestion_matches = []
        self._suggestion_index = 0
        self._base_val = ""
        self._mode = "file"

    async def _on_message(self, message: Message) -> None:
        """Low-level message listener to reliably capture changes on self."""
        await super()._on_message(message)
        if message.__class__.__name__ == "Changed":
            self.handle_text_changed()

    def on_key(self, event: events.Key) -> None:
        """Raw keyboard intercept to override Textual's stubborn TextArea defaults."""
        
        # 1. If we have active suggestions, hijack Up/Down/Right arrows completely
        if self._suggestion_matches:
            if event.key == "up":
                event.prevent_default()
                event.stop() # Stops the cursor_up action from firing
                self.cycle_suggestions(-1)
                return
            elif event.key == "down":
                event.prevent_default()
                event.stop() # Stops the cursor_down action from firing
                self.cycle_suggestions(1)
                return
            elif event.key == "right":
                event.prevent_default()
                event.stop() # Stops cursor movement after inserting
                self.commit_suggestion()
                return
        
        # 2. Check for Newline combos FIRST
        if event.key in ("shift+enter", "alt+enter", "ctrl+j"):
            event.prevent_default()
            self.insert("\n")
            
        # 3. Check for standard Enter LAST to send the message
        elif event.key == "enter":
            event.prevent_default()
            val = self.text.strip()
            self.post_message(self.Submitted(self, val))

    def handle_text_changed(self) -> None:
        """Processes text changes to find autocompletion matches."""
        val = self.text
        
        # Handle / for slash commands
        if val.startswith("/"):
            from commands import SLASH_COMMANDS
            matches = [cmd for cmd in SLASH_COMMANDS if cmd.startswith(val)]
            matches.sort()
            self._suggestion_matches = matches
            self._suggestion_index = 0
            self._base_val = ""
            self._mode = "command"
            self.update_suggestions_ui()
            return

        # Handle & for files
        last_amp = val.rfind("&")
        if last_amp != -1:
            partial_path = val[last_amp + 1:]
            if partial_path.replace(r"\ ", "").count(" ") == 0:
                try:
                    app = self.app
                    base_dir = str(app.query_one("#dir_tree").path) if app else os.getcwd()
                except:
                    base_dir = os.getcwd()
                search_str = partial_path.replace(r"\ ", " ")
                search_pattern = os.path.join(base_dir, search_str + "*")
                import glob
                matches = glob.glob(search_pattern)
                matches.sort()
                self._suggestion_matches = matches
                self._suggestion_index = 0
                self._base_val = val[:last_amp + 1]
                self._mode = "file"
                self.update_suggestions_ui()
                return

        # Handle @ for agents
        last_at = val.rfind("@")
        if last_at != -1:
            partial_agent = val[last_at + 1:]
            if " " not in partial_agent:
                try:
                    agent_view = self.app.query_one("AIAgentView")
                    agent_names = list(agent_view.agent_manager.agents.keys()) + ["team", "room"]
                    matches = [name for name in agent_names if name.lower().startswith(partial_agent.lower())]
                    matches.sort()
                    self._suggestion_matches = matches
                    self._suggestion_index = 0
                    self._base_val = val[:last_at + 1]
                    self._mode = "agent"
                    self.update_suggestions_ui()
                    return
                except: pass

        self._suggestion_matches = []
        self.update_suggestions_ui()

    def _get_suggestion_desc(self, match: str, mode: str) -> str:
        """Retrieves helpful contextual info for each completion choice."""
        if mode == "command":
            return SLASH_COMMAND_DESCS.get(match, "Slash Command")
        elif mode == "agent":
            if match == "team":
                return "Broadcast message to all registered agents"
            elif match == "room":
                return "Broadcast message to agents active in this session"
            try:
                agent_view = self.app.query_one("AIAgentView")
                agent = agent_view.agent_manager.get_agent(match)
                if agent:
                    backstory = agent.backstory
                    return backstory[:60] + "..." if len(backstory) > 60 else backstory
            except: pass
            return "AI Agent Persona"
        elif mode == "file":
            try:
                if os.path.isdir(match):
                    return "Directory"
                ext = os.path.splitext(match)[1].lower()
                desc = f"{ext.upper()[1:]} File" if ext else "File"
                try:
                    size_kb = os.path.getsize(match) / 1024
                    desc += f" ({size_kb:.1f} KB)"
                except: pass
                return desc
            except: pass
            return "File Path"
        return ""

    def update_suggestions_ui(self) -> None:
        """Visual table displaying scrollable suggestions below the input widget."""
        try:
            preview = self.app.query_one("#ai_suggestions_preview")
            if self._suggestion_matches:
                preview.styles.display = "block"
                
                # Sliding 4-item scroll window centered around selection
                total = len(self._suggestion_matches)
                curr = self._suggestion_index
                if total <= 4:
                    start = 0
                    end = total
                else:
                    start = max(0, curr - 1)
                    end = start + 4
                    if end > total:
                        end = total
                        start = end - 4
                
                lines = []
                for i in range(start, end):
                    match = self._suggestion_matches[i]
                    desc = self._get_suggestion_desc(match, self._mode)
                    match_disp = os.path.basename(match) if self._mode == "file" else match
                    
                    if i == curr:
                        lines.append(f"[bold reverse]   {match_disp:<24} │ {desc:<50} [/bold reverse]")
                    else:
                        lines.append(f"   [dim]{match_disp:<24}[/dim] │ [dim]{desc:<50}[/dim]")
                
                preview.update("\n".join(lines))
            else:
                preview.styles.display = "none"
        except Exception:
            pass

    def cycle_suggestions(self, direction: int):
        if not self._suggestion_matches:
            return
        self._suggestion_index = (self._suggestion_index + direction) % len(self._suggestion_matches)
        self.update_suggestions_ui()

    def commit_suggestion(self) -> None:
        """Commits the highlighted suggestion when pressing the Right Arrow."""
        if not self._suggestion_matches:
            return
            
        match = self._suggestion_matches[self._suggestion_index]
        
        if self._mode == "file":
            try:
                app = self.app
                base_dir = str(app.query_one("#dir_tree").path) if app else os.getcwd()
            except:
                base_dir = os.getcwd()
                
            rel_path = os.path.relpath(match, base_dir).replace("\\", "/")
            if os.path.isdir(match):
                rel_path += "/"
            rel_path = rel_path.replace(" ", r"\ ")
            suggestion = rel_path
        else:
            suggestion = match
        
        with self.prevent(TextArea.Changed):
            self.text = self._base_val + suggestion
            # Move cursor to end of the newly completed text
            lines = self.text.split("\n")
            self.cursor_location = (len(lines)-1, len(lines[-1]))
            
        # Wipe selection window on insert
        self._suggestion_matches = []
        self.update_suggestions_ui()

def get_welcome_banner(agent_view) -> str:
    from datetime import datetime, timedelta
    import glob
    import os
    import json
    try:
        from tiktoken import get_encoding
        HAS_TIKTOKEN = True
    except ImportError:
        HAS_TIKTOKEN = False

    def estimate_tokens(text: str) -> int:
        if HAS_TIKTOKEN:
            try:
                enc = get_encoding("cl100k_base")
                return len(enc.encode(text))
            except Exception:
                pass
        return len(text) // 4

    # 1. Determine last calendar month
    now = datetime.now()
    first_day_current_month = now.replace(day=1)
    last_day_last_month = first_day_current_month - timedelta(days=1)
    target_year = last_day_last_month.year
    target_month = last_day_last_month.month
    
    # 2. Scan sessions directory
    sessions_dir = get_storage_path("sessions")
    if not os.path.exists(sessions_dir):
        os.makedirs(sessions_dir, exist_ok=True)
        
    session_files = glob.glob(os.path.join(sessions_dir, "*.json"))
    
    agent_input_tokens = {}
    agent_output_tokens = {}
    agent_total_tools = {}
    agent_successful_tools = {}
    agent_collabs = {}
    total_conversations_all_time = set()
    total_conversations_target_month = set()
    
    valid_agents = set(agent_view.agent_manager.agents.keys())
    
    for filepath in session_files:
        base = os.path.basename(filepath).replace(".json", "")
        parts = base.split("_")
        if len(parts) < 3:
            continue
            
        session_id = f"{parts[0]}_{parts[1]}"
        total_conversations_all_time.add(session_id)
        
        agent_name = "_".join(parts[2:])
        matched_agent = None
        for a in valid_agents:
            if a.replace(" ", "_") == agent_name or a.lower() == agent_name.lower():
                matched_agent = a
                break
        if not matched_agent:
            matched_agent = agent_name.replace("_", " ").title()
            
        try:
            ts = int(parts[1])
            file_dt = datetime.fromtimestamp(ts)
        except (ValueError, IndexError):
            try:
                mtime = os.path.getmtime(filepath)
                file_dt = datetime.fromtimestamp(mtime)
            except Exception:
                continue
                
        is_target_month = (file_dt.year == target_year and file_dt.month == target_month)
        
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                history = json.load(f)
        except Exception:
            continue
            
        for msg in history:
            if not isinstance(msg, dict):
                continue
            role = msg.get("role")
            content = msg.get("content") or ""
            
            msg_tokens = estimate_tokens(content)
            
            # Count tools called and inputs/outputs in target month
            if is_target_month:
                total_conversations_target_month.add(session_id)
                t_calls = msg.get("tool_calls") or []
                t_outs = msg.get("tool_outputs") or []
                
                # Accumulate tools
                agent_total_tools[matched_agent] = agent_total_tools.get(matched_agent, 0) + len(t_calls)
                for out in t_outs:
                    if isinstance(out, dict):
                        content_lower = str(out.get("content", "")).lower()
                        if not any(term in content_lower for term in ["error:", "exception:", "failed:", "failed to"]):
                            agent_successful_tools[matched_agent] = agent_successful_tools.get(matched_agent, 0) + 1
                
                # Accumulate Input vs Output tokens
                if role == "human" or role == "system":
                    agent_input_tokens[matched_agent] = agent_input_tokens.get(matched_agent, 0) + msg_tokens
                    for out in t_outs:
                        if isinstance(out, dict):
                            agent_input_tokens[matched_agent] = agent_input_tokens.get(matched_agent, 0) + estimate_tokens(out.get("content", ""))
                elif role == "ai":
                    agent_output_tokens[matched_agent] = agent_output_tokens.get(matched_agent, 0) + msg_tokens
                    
                # Collaborators check
                if role == "ai" and content:
                    mentions = agent_view.agent_manager.get_mentions(content)
                    if mentions:
                        if matched_agent not in agent_collabs:
                            agent_collabs[matched_agent] = set()
                        for m in mentions:
                            if m != matched_agent:
                                agent_collabs[matched_agent].add(m)

    # Fallback logic if last calendar month had no activity
    used_month_name = last_day_last_month.strftime("%B")
    if not agent_input_tokens and not agent_output_tokens:
        target_year = now.year
        target_month = now.month
        used_month_name = now.strftime("%B")
        
        for filepath in session_files:
            base = os.path.basename(filepath).replace(".json", "")
            parts = base.split("_")
            if len(parts) < 3:
                continue
            session_id = f"{parts[0]}_{parts[1]}"
            agent_name = "_".join(parts[2:])
            matched_agent = None
            for a in valid_agents:
                if a.replace(" ", "_") == agent_name or a.lower() == agent_name.lower():
                    matched_agent = a
                    break
            if not matched_agent:
                matched_agent = agent_name.replace("_", " ").title()
                
            try:
                ts = int(parts[1])
                file_dt = datetime.fromtimestamp(ts)
            except (ValueError, IndexError):
                try:
                    mtime = os.path.getmtime(filepath)
                    file_dt = datetime.fromtimestamp(mtime)
                except Exception:
                    continue
                    
            is_target_month = (file_dt.year == target_year and file_dt.month == target_month)
            if not is_target_month:
                continue
                
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    history = json.load(f)
                # Ensure the history parses correctly as a list of dicts
                if not isinstance(history, list):
                    history = []
            except Exception:
                continue
                
            for msg in history:
                if not isinstance(msg, dict):
                    continue
                role = msg.get("role")
                content = msg.get("content") or ""
                
                msg_tokens = estimate_tokens(content)
                t_calls = msg.get("tool_calls") or []
                t_outs = msg.get("tool_outputs") or []
                
                # Accumulate tools
                agent_total_tools[matched_agent] = agent_total_tools.get(matched_agent, 0) + len(t_calls)
                for out in t_outs:
                    if isinstance(out, dict):
                        content_lower = str(out.get("content", "")).lower()
                        if not any(term in content_lower for term in ["error:", "exception:", "failed:", "failed to"]):
                            agent_successful_tools[matched_agent] = agent_successful_tools.get(matched_agent, 0) + 1
                
                # Accumulate Input vs Output tokens
                if role == "human" or role == "system":
                    agent_input_tokens[matched_agent] = agent_input_tokens.get(matched_agent, 0) + msg_tokens
                    for out in t_outs:
                        if isinstance(out, dict):
                            agent_input_tokens[matched_agent] = agent_input_tokens.get(matched_agent, 0) + estimate_tokens(out.get("content", ""))
                elif role == "ai":
                    agent_output_tokens[matched_agent] = agent_output_tokens.get(matched_agent, 0) + msg_tokens
                
                if role == "ai" and content:
                    mentions = agent_view.agent_manager.get_mentions(content)
                    if mentions:
                        if matched_agent not in agent_collabs:
                            agent_collabs[matched_agent] = set()
                        for m in mentions:
                            if m != matched_agent:
                                agent_collabs[matched_agent].add(m)

    # 3. Scan research directory for deep research inputs/outputs
    research_input_tokens = 0
    research_output_tokens = 0
    try:
        workspace_dir = agent_view.query_one("#dir_tree").path if agent_view else os.getcwd()
        research_dir = os.path.join(workspace_dir, "research")
        if os.path.exists(research_dir):
            for root, dirs, files in os.walk(research_dir):
                # Count successful fetches in research_status.log
                log_path = os.path.join(root, "research_status.log")
                if os.path.exists(log_path):
                    try:
                        with open(log_path, "r", encoding="utf-8", errors="ignore") as lf:
                            for line in lf:
                                if "[FETCH]" in line:
                                    # Each successful fetch averages around 6,000 tokens of input webpage content
                                    research_input_tokens += 6000
                    except Exception:
                        pass
                
                # Count tokens of generated markdown annexures/reports
                for file in files:
                    if file.endswith(".md") and file != "research_status.log":
                        file_path = os.path.join(root, file)
                        try:
                            with open(file_path, "r", encoding="utf-8", errors="ignore") as mf:
                                md_content = mf.read()
                                research_output_tokens += estimate_tokens(md_content)
                        except Exception:
                            pass
    except Exception:
        pass

    # Calculate best agent
    agent_total_tokens = {}
    for agent in set(list(agent_input_tokens.keys()) + list(agent_output_tokens.keys())):
        agent_total_tokens[agent] = agent_input_tokens.get(agent, 0) + agent_output_tokens.get(agent, 0)

    if agent_total_tokens:
        best_agent = max(agent_total_tokens, key=agent_total_tokens.get)
        inp_tokens = agent_input_tokens.get(best_agent, 0) + research_input_tokens
        out_tokens = agent_output_tokens.get(best_agent, 0) + research_output_tokens
        collabs = list(agent_collabs.get(best_agent, []))
    else:
        best_agent = agent_view.active_agent.name if getattr(agent_view, "active_agent", None) else "Rita"
        inp_tokens = research_input_tokens
        out_tokens = research_output_tokens
        collabs = []
        
    total_convs = len(total_conversations_all_time)

    # Retrieve specific agent colors
    def get_agent_color(name: str) -> str:
        agent = agent_view.agent_manager.get_agent(name)
        return agent.color if agent else "#00FFFF"

    best_agent_color = get_agent_color(best_agent)
    
    colored_collabs = []
    for c in collabs:
        c_color = get_agent_color(c)
        colored_collabs.append(f"[bold {c_color}]{c}[/]")
    collaborations = ", ".join(colored_collabs) if colored_collabs else "None"

    # Fetch skills count for the best agent
    safe_name = best_agent.replace(" ", "_")
    skills_dir = get_storage_path("agents", "skills", safe_name)
    passive_count = 0
    active_count = 0
    if os.path.exists(skills_dir):
        try:
            passive_count = sum(1 for f in os.listdir(skills_dir) if f.endswith(".md"))
            active_count = sum(1 for f in os.listdir(skills_dir) if f.endswith(".json"))
        except Exception:
            pass

    # Dynamic metrics calculations for tool call success rates
    total_tools_run = sum(agent_total_tools.values())
    successful_tools_run = sum(agent_successful_tools.values())
    
    t_pct = int((successful_tools_run / total_tools_run) * 100) if total_tools_run > 0 else 0

    # Width configuration to perfectly align columns
    # Outer width is 70 characters
    def make_row(text: str) -> str:
        space_len = 66 - len(text)
        left_space = space_len // 2
        right_space = space_len - left_space
        return f"│ {' ' * left_space}{text}{' ' * right_space} │"

    def make_line(desc: str, value: str) -> str:
        import re
        visible_value = re.sub(r'\[/?.*?\]', '', value)
        space_len = 40 - len(visible_value)
        if space_len < 0:
            space_len = 0
        return f"│  {desc:<21} │ {value}{' ' * space_len}  │"

    lines = [
        "╭" + "─" * 68 + "╮",
        make_row("Federate.AI"),
        "├" + "─" * 68 + "┤",
        make_line("Agent of the Month", f"[bold {best_agent_color}]{best_agent}[/] ({used_month_name})"),
        make_line("Processed Tokens", f"[green]{inp_tokens:,}[/] / [blue]{out_tokens:,}[/]"),
        make_line("Total Conversations", f"{total_convs:,}"),
        make_line("Tool Calls", f"{successful_tools_run:,} / {total_tools_run:,} ({t_pct}%)"),
        make_line("Skills", f"[blue]{passive_count}[/] / [red]{active_count}[/]"),
        make_line("Collaborators", collaborations),
        "╰" + "─" * 68 + "╯"
    ]

    colors = ["#f2a813", "#ec9624", "#ec9624", "#e68435", "#e07246", "#da6057", "#d44e68", "#ce3c79", "#c82a8a", "#c2189b"]
    colored_lines = []
    for line, color in zip(lines, colors):
        colored_lines.append(f"[{color}]{line}[/]")
        
    tips = """
[#f2a813]Tips for getting started:[/]
1. Ask questions, edit files, or run commands.
2. Use & to inject files. Use @ to invoke particular agents.
3. Press F2 to configure the active agent.
4. Press Ctrl+K to start a fresh conversation.
"""
    return "\n".join(colored_lines) + "\n" + tips

def render_latex_to_unicode(text: str) -> str:
    """Parses LaTeX math blocks into Unicode for terminal rendering."""
    if "$" not in text:
        return text
    try:
        from pylatexenc.latex2text import LatexNodes2Text
        converter = LatexNodes2Text()
        
        def replace_block(match):
            try: return "\n" + converter.latex_to_text(match.group(1).strip()) + "\n"
            except: return match.group(0)
        text = re.sub(r'\$\$(.*?)\$\$', replace_block, text, flags=re.DOTALL)
        
        def replace_inline(match):
            try: return converter.latex_to_text(match.group(1).strip())
            except: return match.group(0)
        text = re.sub(r'(?<![\w\\])\$([^$\n]+?)\$(?!\w)', replace_inline, text)
        
        return text
    except ImportError:
        return text


class AIAgentView(Vertical):
    """Full-screen Chat Agent interface with Multi-Agent support."""
    
    BINDINGS =[
        Binding("f2", "open_chat_manager", "Sessions"),
        Binding("ctrl+k", "clear_all_contexts", "New Chat", priority=True),
        Binding("f4", "open_active_config", "Edit Agent"),
        Binding("f5", "switch_agent", "Switch Agent"),
        Binding("ctrl+t", "cycle_arm_mode", "Cycle Mode"),
        Binding("ctrl+g", "cycle_agents", "Cycle Agents"),
        Binding("ctrl+a", "abort", "Abort"),
    ]

    DEFAULT_CSS = """
    AIAgentView { width: 100%; height: 100%; background: $background; padding: 0 1; }
    
    #ai_chat_scroll { height: 1fr; width: 100%; overflow-y: scroll; scrollbar-gutter: stable; }
    #chat_messages { height: auto; width: 100%; }
    .chat_msg { margin-bottom: 1; height: auto; width: 100%; }
    #ai_thinking_spinner { height: 1; display: none; color: $warning; margin: 0; padding-left: 1; }
    
    #progress_container { 
        display: none; 
        height: 12;
        border: solid $primary; 
        background: $surface;
        margin: 1 0;
        layout: horizontal;
    }
    #progress_left {
        width: 50%;      
        height: 100%;
        padding: 1;
        overflow-y: auto;
    }
    #progress_right {
        width: 50%;
        height: 100%;
        border-left: solid $primary;
        background: $boost;
    }
    .task_row { height: 1; margin-bottom: 1; layout: horizontal; }
    .task_spinner { width: 3; color: $warning; text-style: bold; }
    ProgressBar { width: 1fr; }
    
    #input_container { 
        height: auto; 
        min-height: 3; 
        max-height: 7; 
        width: 100%; 
        border-top: solid #5f9ea0; 
        border-bottom: solid #5f9ea0; 
        layout: horizontal; 
    }
    #prompt_label { 
        color: #dda0dd; 
        text-style: bold; 
    }
    #ai_chat_input { 
        width: 1fr; 
        height: auto; 
        min-height: 1; 
        max-height: 5; 
        border: none; 
        background: transparent; 
        padding: 0; 
    }
    #ai_suggestions_preview {
        display: none;
        background: $boost;
        border: round $primary;
        height: auto;
        max-height: 6; /* Fits up to 4 lines of suggestions + borders cleanly */
        padding: 0 1;
        margin-top: 0;
    }
    #status_bar { height: auto; min-height: 1; width: 100%; layout: grid; grid-size: 3; }
    .status_left { color: #87cefa; }
    .status_center { 
        width: 100%;
        color: $text;
        content-align: center middle; 
        text-align: center;
    }
    .status_right { color: #dda0dd; text-align: right; }
    .tool_result_box {
        border: round $accent;
        background: $boost;
        padding: 0 1;
        margin: 1 0;
        color: #808080;
    }
    """

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="ai_chat_scroll"):
            yield Vertical(id="chat_messages")
            with Horizontal(id="progress_container"):
                yield Vertical(id="progress_left")
                yield RichLog(id="progress_right", markup=False, wrap=True, auto_scroll=True)
                
        yield Label("Agent is working...", id="ai_thinking_spinner")
        
        with Horizontal(id="input_container"):
            yield Label(">", id="prompt_label")
            yield ChatInput(placeholder=" Type your message, &path, or @agent", id="ai_chat_input", suggester=ChatSuggester(lambda: self.app))
            
        yield Static(id="ai_suggestions_preview") # Yield the preview directly below the input
            
        with Horizontal(id="status_bar"):
            yield Label(f"{os.getcwd()}", id="ai_cwd_label", classes="status_left")
            yield Label("", id="ai_config_label", classes="status_center")
            yield Label("", id="ai_token_label", classes="status_right")

    def on_mount(self):
        toolbox.CURRENT_APP = self.app
        toolbox.CURRENT_LOG_CB = self.log_to_ui
        self.current_tokens = 0
        
        # Initialize Orchestration
        self.agent_manager = AgentManager()
        self.session_manager = SessionManager()
        self.schedule_manager = ScheduleManager()
        self.current_batch_id = 0
        
        # Load the persisted default
        default_name = self.agent_manager.get_default_agent_name()
        initial_agent = self.agent_manager.get_agent(default_name) or list(self.agent_manager.agents.values())[0]
        self.select_agent(initial_agent.name)

        # STARTUP: Handle Termux/EncryptedKeyring blocking
        from toolbox import is_keyring_locked, unlock_keyring
        if is_keyring_locked():
            def handle_initial_unlock(password):
                if password:
                    if unlock_keyring(password):
                        self.notify("Keyring unlocked.", severity="information")
                        # Refresh agent so it pulls decrypted keys
                        self.select_agent(self.active_agent.name)
                        self.update_status_bar()
                    else:
                        self.notify("Initial unlock failed. Stored keys may be unavailable.", severity="error")
            
            self.app.push_screen(KeyringUnlockModal(), handle_initial_unlock)
        
        self.agent_executors = {} # Cache for react agents
        self.shell_mode = False
        self.agent_mode = "PLAN"
        self._running_task_count = 0
        self._running_agents = set()
        
        self.tts_enabled = False
        self.stt_enabled = False
        self.tts_manager = TTSManager()
        self.stt_append_history =[] 
        self.stt_manager = STTManager(
            callback=self.handle_stt_input, 
            log_callback=self.log_to_ui,
            tts_manager=self.tts_manager
        )
        
        self.telegram_manager = TelegramManager(
            callback=self.handle_telegram_input,
            log_callback=self.log_to_ui
        )

        self.turn_queue = []
        self.turn_lock = threading.Lock()

        self._write_log(get_welcome_banner(self))
        self.update_tokens()
        if "-r" in sys.argv:
            # We delay the call until the UI is fully painted and stable
            self.call_after_refresh(self.action_resume_last)
        self.query_one("#ai_chat_input").focus()
        
        self.spinner_chars =["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        self.spinner_idx = 0
        self.set_interval(0.1, self.tick_spinners)
        self.set_interval(60.0, self.tick_scheduler)

    def select_agent(self, name: str) -> bool:
        agent = self.agent_manager.get_agent(name)
        if agent:
            self.active_agent = agent
            self.session_manager.init_agent_session(agent, list(self.agent_manager.agents.values()))
            
            # Apply agent-specific color to the UI programmatically
            try:
                input_container = self.query_one("#input_container")
                prompt_label = self.query_one("#prompt_label")
                
                # Update borders and label color
                input_container.styles.border_top = ("solid", agent.color)
                input_container.styles.border_bottom = ("solid", agent.color)
                prompt_label.styles.color = agent.color
            except Exception:
                pass
            
            self.update_tokens()
            return True
        return False
    
    def confirm_tool_execution(self, tool_name: str, arguments: dict, agent_name: str = "Agent") -> bool:
        """Pushes the ToolConfirmationModal and blocks the background worker thread until approved/rejected."""
        result_event = threading.Event()
        final_result = [False]

        def handle_result(res: bool):
            final_result[0] = bool(res)
            result_event.set()

        self.app.call_from_thread(self.app.push_screen, ToolConfirmationModal(tool_name, arguments, agent_name=agent_name), handle_result)
        
        while not result_event.is_set():
            if toolbox.ABORT_EVENT.is_set():
                return False
            time.sleep(0.1)
            
        return final_result[0]
    
    def action_clear_all_contexts(self):
        self.session_manager.clear_all_contexts()
        self.clear_chat_ui()
        self._write_log(Rule(title="[bold yellow]ALL CONTEXTS CLEARED", style="dim"))
        self._write_log(get_welcome_banner(self))
        self.update_tokens()
    
    def action_resume_last(self):
        """Finds the last modified session and uses the existing load logic."""
        files = sorted(glob.glob(get_storage_path("sessions", "*.json")), key=os.path.getmtime)
        if len(files) > 1:
            self.load_chat_file(files[-2])
        
    def action_abort(self):
        """Interrupts current agent tasks, stops the spinner, and returns focus to input."""
        toolbox.ABORT_EVENT.set()
        
        # Persistent Abort: Mark the current batch as dead
        if hasattr(self, "current_batch_id"):
            self.session_manager.abort_batch(self.current_batch_id)

        self._running_agents.clear()
        if self.workers:
            self.workers.cancel_all()
            self._toggle_spinner(False)
            
            # Ensure Research Progress UI dies
            try:
                self.query_one("#progress_container").styles.display = "none"
            except: pass

            self.log_to_ui("[bold red]⚠️ Operation Aborted by User.[/bold red]")
            self.query_one("#ai_chat_input").focus()
        
    
    def action_open_chat_manager(self):
        """F2 triggers the Session menu."""
        def handle_chat_mgr(action):
            if action == "new_session":
                self.action_clear_all_contexts()
            elif action == "load_chat":
                self.app.push_screen(ChatLoadModal(), self.load_chat_file)
        self.app.push_screen(ChatManagerModal(), handle_chat_mgr)

    def action_switch_agent(self):
        """Ctrl+M shows scrollable buttons + Default checkbox."""
        names = list(self.agent_manager.agents.keys())
        default = self.agent_manager.get_default_agent_name()
        def handle_switch(res):
            if res:
                self.select_agent(res["name"])
                if res["default"]:
                    self.agent_manager.set_default_agent_name(res["name"])
                    self.log_to_ui(f"[bold #f2a813]Default agent set to: {res['name']}[/bold #f2a813]")
        self.app.push_screen(SwitchAgentModal(names, default), handle_switch)

    
    def load_chat_file(self, filepath: str):
        """Loads a multi-agent session file and reconstructs the UI accurately."""
        if not filepath: return
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # Reconstruct HistoryMessage objects
            messages = [HistoryMessage(**m) for m in data]
            
            # Extract agent name from filename (Standard: sess_TIMESTAMP_AgentName.json)
            base = os.path.basename(filepath).replace(".json", "")
            agent_name = base.split("_")[-1]
            sess_id = "_".join(base.split("_")[:-1])

            # Update the global session state
            self.session_manager.current_session_id = sess_id
            self.session_manager.active_sessions[agent_name] = messages
            
            # Switch the UI to the agent whose file we just loaded
            if self.select_agent(agent_name):
                self.replay_chat(messages, agent_name)
                self.log_to_ui(f"[bold green]Restored Session: {sess_id} (agent: {agent_name})[/bold green]")
        except Exception as e:
            self.log_to_ui(f"[bold red]Load Error:[/bold red] {e}")

    def replay_chat(self, history: List[HistoryMessage], owner_name: str):
        """Processes tags to rename labels and clean content during UI reconstruction."""
        self.query_one("#chat_messages").query("*").remove()
        self._write_log(Rule(title=f"[bold #f2a813]SESSION RESTORED: {owner_name.upper()}", style="dim"))

        for hm in history:
            if hm.role == "system": continue

            content = hm.content
            label = "User"
            color = "blue"

            if hm.role == "ai":
                # This was the agent whose file we are reading
                label = owner_name
                color = "magenta"
            else: # role == "human"
                # Check for synced intercom tags from other agents
                intercom_match = re.search(r'<AGENT_INTERCOM sender="([^"]+)">([\s\S]*?)</AGENT_INTERCOM>', content)
                tool_match = re.search(r'<AGENT_INTERCOM_TOOL_RESPONSE agent="([^"]+)" tool="([^"]+)">([\s\S]*?)</AGENT_INTERCOM_TOOL_RESPONSE>', content)

                if intercom_match:
                    label = intercom_match.group(1) # The synced agent's name
                    content = intercom_match.group(2).strip()
                    color = "cyan"
                elif tool_match:
                    label = f"{tool_match.group(1)} (Tool: {tool_match.group(2)})"
                    content = tool_match.group(3).strip()
                    color = "bright_black" # Dim/Gray for tool output
                else:
                    # Genuine user message
                    label = "User"
                    color = "blue"

            self._write_log(Rule(style="dim"))
            self._write_log(f"[bold {color}]{label}:[/bold {color}]", is_markdown=False)
            self._write_log(content, is_markdown=True)
    
    def action_open_active_config(self):
        """F4: Opens the editor. Distinguishes between Renaming and Cloning."""
        def handle_modal_result(result_tuple):
            if not result_tuple or result_tuple[0] != "save":
                return
            
            # Extract the new config and the 'is_new' flag
            _, new_config, is_new = result_tuple
            old_name = self.active_agent.name
            
            # 1. Always save the new name
            self.agent_manager.save_agent(new_config)
            
            # 2. Only delete the old one if it's an UPDATE (Rename), NOT if it's SAVE AS NEW (Clone)
            if not is_new and new_config.name != old_name:
                self.agent_manager.delete_agent(old_name)
                self.agent_executors.pop(old_name, None)
            
            # 3. Switch to the agent (whether it's the renamed one or the brand new clone)
            self.select_agent(new_config.name)
            self.agent_executors.pop(new_config.name, None) 
            
            status_msg = "created and activated" if is_new else "updated"
            self.log_to_ui(f"[bold green]Agent '{new_config.name}' {status_msg}.[/bold green]")
            self.update_status_bar()

        self.app.push_screen(ConfigModal(self.active_agent, self.agent_manager), handle_modal_result)
    
    def action_open_config(self):
        """Now handles F2 Chat Management."""
        def handle_chat_mgr(action):
            if action == "new_session":
                self.action_clear_all_contexts()
            elif action == "load_chat":
                # Re-use your existing ChatLoadModal logic
                def handle_load(filepath):
                    if filepath:
                        # You need to implement load_chat_file logic or 
                        # adapt it to multi-agent sessions here
                        self.log_to_ui(f"Loading {filepath}...") 
                self.app.push_screen(ChatLoadModal(), handle_load)

        self.app.push_screen(ChatManagerModal(), handle_chat_mgr)

    def log_to_ui(self, msg: Any, is_markdown: bool = False):
        try:
            self.app.call_from_thread(self._write_log, msg, is_markdown)
        except RuntimeError:
            self._write_log(msg, is_markdown)
    
    def _write_log(self, msg: Any, is_markdown: bool = False):
        try:
            chat_body = self.query_one("#chat_messages")
            
            # If msg is markup like "[bold blue]User:[/bold blue]", extract label to try and find agent color
            if isinstance(msg, str) and not is_markdown:
                match = re.search(r'\[bold ([^\]]+)\]([^:]+):\[/bold', msg)
                if match:
                    label_text = match.group(2).strip()
                    # Try to find agent by name
                    agent = self.agent_manager.get_agent(label_text)
                    if agent:
                        # Override the hardcoded color with the agent's actual color
                        msg = msg.replace(f"[bold {match.group(1)}]", f"[bold {agent.color}]")

            if isinstance(msg, str):
                if is_markdown:
                    msg_to_render = render_latex_to_unicode(msg)
                    widget = Static(Markdown(msg_to_render), classes="chat_msg")
                else:
                    widget = Static(Text.from_markup(msg), classes="chat_msg")
            elif isinstance(msg, (Rule, Text, Markdown)):
                widget = Static(msg, classes="chat_msg")
            else:
                widget = msg
            chat_body.mount(widget)
            self.app.call_after_refresh(lambda: self.query_one("#ai_chat_scroll").scroll_end(animate=False))
            self.query_one("#ai_chat_scroll").scroll_end(animate=False)
            self.update_status_bar()
        except Exception: pass

    def update_status_bar(self):
        try:
            mode = getattr(self, "agent_mode", "PLAN")
            if mode == "PLAN":
                mode_str = "[bold green]SAFE[/bold green]"
            elif mode == "INTERMEDIATE":
                mode_str = "[bold tomato]SEMI-AUTO[/bold tomato]"
            else:
                mode_str = "[bold red]FULL-AUTO[/bold red]"
                
            backup_str = " [bold yellow][B][/]" if self.active_agent.use_backup else ""
            agent_info = f"[bold {self.active_agent.color}]{self.active_agent.name}{backup_str}[/] ({self.active_agent.model}) [bold magenta]({self.current_tokens})[/bold magenta]"
            
            try:
                app = self.app
                base_dir = str(app.query_one("#dir_tree").path) if app else os.getcwd()
            except Exception:
                base_dir = os.getcwd()
            self.query_one("#ai_cwd_label", Label).update(base_dir)
            self.query_one("#ai_config_label", Label).update(f"[F3] {mode_str}")
            self.query_one("#ai_token_label", Label).update(agent_info)
        except Exception: pass
        self.update_prompt_label()

    def update_prompt_label(self):
        try:
            label = self.query_one("#prompt_label", Label)
            backup_str = " [B]" if self.active_agent.use_backup else ""
            if self.shell_mode:
                folder = os.path.basename(os.getcwd()) or os.getcwd()
                label.update(f"[bold red]shell@{folder} %[/bold red]")
            else:
                label.update(f"[bold {self.active_agent.color}]{self.active_agent.name}{backup_str}>[/bold {self.active_agent.color}]")
        except Exception: pass

    def action_cycle_arm_mode(self):
        """Cycles through safety modes: PLAN -> INTERMEDIATE -> EXECUTE."""
        ARM_MODES = ["PLAN", "INTERMEDIATE", "EXECUTE"]
        current_idx = ARM_MODES.index(self.agent_mode) if self.agent_mode in ARM_MODES else 0
        next_idx = (current_idx + 1) % len(ARM_MODES)
        self.agent_mode = ARM_MODES[next_idx]
        self.agent_executors = {} # Force re-init of all executors
        self.log_to_ui(f"System operating mode cycled to: {self.agent_mode}")
        self.update_status_bar()

    def action_cycle_agents(self):
        """Cycles the active host agent sequentially through all configured personas."""
        names = list(self.agent_manager.agents.keys())
        if not names:
            return
            
        try:
            current_idx = names.index(self.active_agent.name)
        except ValueError:
            current_idx = 0
            
        next_idx = (current_idx + 1) % len(names)
        next_name = names[next_idx]
        
        if self.select_agent(next_name):
            self.log_to_ui(f"[bold green]Cycled active agent to: {next_name}[/bold green]")

    def toggle_plan_mode(self):
        """Maintains backwards compatibility for any slash command hooks using the old toggle."""
        self.action_cycle_arm_mode()

    def clear_chat_ui(self):
        try:
            self.query_one("#chat_messages").query("*").remove()
            self.query_one("#progress_left").query("*").remove()
            self.query_one("#progress_right", RichLog).clear()
            self.query_one("#progress_container").styles.display = "none"
        except Exception: pass

    def get_executor(self, agent_config: AgentConfig):
        if agent_config.name in self.agent_executors:
            return self.agent_executors[agent_config.name]
        
        if agent_config.use_backup and agent_config.backup_model:
            model = agent_config.backup_model
            base_url = agent_config.backup_base_url or agent_config.base_url
            api_key = agent_config.get_backup_api_key()
        else:
            model = agent_config.model
            base_url = agent_config.base_url
            api_key = agent_config.get_api_key()

        if not api_key:
            return None
            
        llm = ChatOpenAI(
            model=model, 
            temperature=0,
            api_key=api_key,
            base_url=base_url,
            max_retries=5,
            timeout=120,
            model_kwargs={"reasoning_effort": "high"}
        )
        
        tools =[list_files, search_web, perform_research, manage_agenda, update_core_memory, save_skill, read_skill, distill_journey, mark_quagmire, get_user_clarification, search_episodic_memory, retrieve_episodic_memory, prepare_active_skill, finalize_active_skill, manage_active_skill, fix_active_skill]

        # Load dynamic tools from Skills (Active Skills)
        dynamic_tools = load_dynamic_tools(agent_config.name)
        tools.extend(dynamic_tools)

        # Add high-privilege tools if in EXECUTE mode OR if explicitly enabled for this agent

        # High-privilege tool mapping
        high_priv_map = {
            "read_file": read_file,
            "curl_url": curl_url,
            "save_file": save_file,
            "edit_file": edit_file,
            "dispatch_subagent": dispatch_subagent,
            "run_terminal_command": run_terminal_command,
            "take_screenshot": take_screenshot,        
            "click_at_current_location": click_at_current_location,
            "move_cursor_absolute": move_cursor_absolute, 
            "move_cursor_relative": move_cursor_relative,       
            "send_scroll": send_scroll,                    
            "inject_keyboard_input": inject_keyboard_input
        }
        
        from langchain_core.tools import StructuredTool

        # Helper function to wrap any tool in a user-authorization guardrail
        def make_wrapped_tool(t_obj):
            def wrapped_func(*args, **kwargs):
                agent_name = getattr(toolbox.thread_context, "agent_name", "Agent")
                confirmed = self.confirm_tool_execution(t_obj.name, kwargs, agent_name=agent_name)
                if not confirmed:
                    return f"Error: Tool execution of '{t_obj.name}' was rejected by the user."
                return t_obj.func(*args, **kwargs)
            return StructuredTool(
                name=t_obj.name,
                description=t_obj.description,
                args_schema=t_obj.args_schema,
                func=wrapped_func
            )

        if self.agent_mode == "EXECUTE":
            # Fully Armed: All high-privilege tools are present and execute automatically.
            tools.extend(list(high_priv_map.values()))
            
        elif self.agent_mode == "INTERMEDIATE":
            # Semi-Armed: All high-privilege tools are present, but every execution requires approval.
            for tname, tool_obj in high_priv_map.items():
                tools.append(make_wrapped_tool(tool_obj))
                
        else: # PLAN (SAFE) Mode
                # Safe Mode: Only high-privilege tools explicitly allowed in the agent's config are present,
                # but they must also go through the confirmation modal before running.
                for tname in agent_config.enabled_tools:
                    if tname == "visual_computer_operation":
                        for ct in ["take_screenshot", "click_at_current_location", "move_cursor_absolute", "move_cursor_relative", "send_scroll", "inject_keyboard_input"]:
                            tools.append(make_wrapped_tool(high_priv_map[ct]))
                    elif tname in high_priv_map:
                        tools.append(make_wrapped_tool(high_priv_map[tname]))
        
        executor = create_react_agent(llm, tools, checkpointer=shared_memory)
        self.agent_executors[agent_config.name] = executor
        return executor

    @on(ChatInput.AbortRequest)
    def on_abort_request(self, event: ChatInput.AbortRequest):
        self.action_abort()

    @on(Input.Submitted, "#ai_chat_input")
    @on(ChatInput.Submitted)
    def on_input_submitted(self, event: ChatInput.Submitted):
        prompt = event.value
        if not prompt.strip(): return
        event.input.text = "" # Clears the TextArea
        
        if prompt.strip() == "!":
            self.shell_mode = not self.shell_mode
            self.update_prompt_label()
            return

        if self.shell_mode or prompt.startswith("!"):
            cmd = prompt[1:].strip() if prompt.startswith("!") else prompt.strip()
            self._write_log(Rule(style="dim"))
            self._write_log(f"[bold red]Shell:[/bold red] {escape(cmd)}")
            out = process_shell_command(cmd, self)
            self._write_log(escape(out))
            return

        if prompt.startswith("/"):
            self._write_log(Rule(style="dim"))
            process_slash_command(prompt, self)
            return

        # Multi-Agent Routing
        acting_agent = self.active_agent
        clean_prompt = prompt
        is_team = False
        
        # --- INTERRUPT SYSTEM ---
        is_interrupt = False
        if self._running_agents:
            self.action_abort()
            is_interrupt = True
        
        self.current_batch_id += 1
        batch_id = self.current_batch_id
        # ------------------------

        # Handle @team or @room command
        if prompt.strip().lower().startswith("@team") or prompt.strip().lower().startswith("@room"):
            is_team = prompt.strip().lower().startswith("@team")
            is_room = not is_team
            prefix_len = 5 if is_team else 5
            clean_prompt = prompt.strip()[prefix_len:].strip()
            
            if is_interrupt:
                clean_prompt = f"the User interrupted to say this: {clean_prompt}"

            if not clean_prompt:
                self.log_to_ui(f"[bold red]Usage: @{'team' if is_team else 'room'} <message>[/bold red]")
                return
            
            if is_team:
                # Ensure everyone is initialized and synced BEFORE broadcasting
                all_agents_list = list(self.agent_manager.agents.values())
                for agent in all_agents_list:
                    self.session_manager.init_agent_session(agent, all_agents_list)
                    self.session_manager.join_conversation(self.active_agent.name, agent, all_agents_list)
                acting_agents = list(self.agent_manager.agents.values())
            else:
                # @room: Only agents already in the active_sessions
                active_names = list(self.session_manager.active_sessions.keys())
                acting_agents = []
                all_agents_list = list(self.agent_manager.agents.values())
                for name in active_names:
                    agent = self.agent_manager.get_agent(name)
                    if agent:
                        # Standard sync for consistency
                        self.session_manager.join_conversation(self.active_agent.name, agent, all_agents_list)
                        acting_agents.append(agent)
            
            # Reset turn queue for team/room parallel turns
            with self.turn_lock:
                self.turn_queue = []

        else:
            # Handle sequential @ mentions
            mentions = self.agent_manager.get_mentions(prompt)
            clean_prompt = prompt
            if is_interrupt:
                clean_prompt = f"the User interrupted to say this: {clean_prompt}"

            if mentions:
                target_agents = []
                for name in mentions:
                    agent = self.agent_manager.get_agent(name)
                    if agent:
                        target_agents.append(agent)
                
                if target_agents:
                    first_agent = target_agents[0]
                    with self.turn_lock:
                        self.turn_queue = target_agents[1:]
                    
                    acting_agents = [first_agent]
                    # Ensure first agent session is ready
                    self.session_manager.join_conversation(self.active_agent.name, first_agent, list(self.agent_manager.agents.values()))
                else:
                    # Fallback to active agent if mentions failed
                    acting_agents = [self.active_agent]
                    self.session_manager.init_agent_session(self.active_agent, list(self.agent_manager.agents.values()))
            else:
                # Default active agent
                acting_agents = [self.active_agent]
                self.session_manager.init_agent_session(self.active_agent, list(self.agent_manager.agents.values()))
                with self.turn_lock:
                    self.turn_queue = []

        self._write_log(Rule(style="dim"))
        if is_team:
            self._write_log("[bold blue]User (to Team):[/bold blue]", is_markdown=False)
        elif 'is_room' in locals() and is_room:
            self._write_log("[bold blue]User (to Room):[/bold blue]", is_markdown=False)
        elif len(acting_agents) == 1:
            self._write_log(f"[bold blue]User (to {acting_agents[0].name}):[/bold blue]", is_markdown=False)
        else:
            self._write_log("[bold blue]User:[/bold blue]", is_markdown=False)
        
        self._write_log(clean_prompt, is_markdown=True)
        
        # Process & context injection
        time_stamp = f"[Time: {datetime.now().strftime('%H:%M')}]\n"
        processed_prompt = time_stamp + handle_ampersand_commands(clean_prompt, self)
        
        # Broadcast user message to all active agents (now they all definitely have sessions)
        self.session_manager.broadcast_message("User", processed_prompt, is_ai=False)
        self.update_tokens()
        # Reset the global abort flag before starting tasks
        toolbox.ABORT_EVENT.clear()
        
        for agent in acting_agents:
            self.run_agent_task(agent, processed_prompt, batch_id=batch_id)


    @work(thread=True)
    def run_agent_task(self, agent: AgentConfig, prompt: str, override_thread_id: str = None, batch_id: int = 0):
        if agent.name in self._running_agents:
            self.log_to_ui(f"[dim yellow]Agent {agent.name} is already working on a task.[/dim yellow]")
            return

        self._running_agents.add(agent.name)
        try:
            executor = self.get_executor(agent)
            if not executor:
                self.log_to_ui(f"[bold red]Agent {agent.name} not configured (Key missing).[/bold red]")
                return
            
            toolbox.thread_context.agent_name = agent.name    
            toolbox.thread_context.batch_id = batch_id
            self.app.call_from_thread(self._toggle_spinner, True, agent.name, agent.color)
            # --- NEW: Prepare the TTS voice stream for this specific agent ---
            if getattr(self, "tts_enabled", False):
                self.tts_manager.start_stream(voice=agent.tts_voice)
            # Use a unique thread_id per agent/session
            thread_id = override_thread_id or f"{self.session_manager.current_session_id}_{agent.name}"
            run_config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 5000}

            current_ai_text = ""
            full_ai_response = ""
            current_ai_widget = None
            tool_outputs = []
            tool_calls = []

            try:
                # Check if we have an existing state for this thread in the checkpointer
                state = executor.get_state(run_config)

                import base64
                import mimetypes

                def _format_vision_content(text: str, is_vision: bool):
                    if isinstance(text, list):
                        return text
                    if not isinstance(text, str):
                        return text
                    if not is_vision or "[Attached Image:" not in text:
                        return text
                    
                    parts = re.split(r'\[Attached Image: (.*?)\]', text)
                    if len(parts) == 1: return text
                    
                    content_list = []
                    for i, part in enumerate(parts):
                        if i % 2 == 0:
                            if part.strip(): content_list.append({"type": "text", "text": part.strip()})
                        else:
                            file_path = part.strip()
                            try:
                                mime = mimetypes.guess_type(file_path)[0] or "image/jpeg"
                                if file_path.lower().endswith(".pdf"):
                                    try:
                                        from pdf2image import convert_from_path
                                        import io
                                        # Convert PDF pages to PIL images using the dynamically configured DPI
                                        # Load persisted DPI if not yet set in memory
                                        dpi_val = getattr(self, "pdf_dpi", None)
                                        if dpi_val is None:
                                            try:
                                                from commands import load_pdf_dpi
                                                dpi_val = load_pdf_dpi()
                                            except Exception:
                                                dpi_val = 150
                                            self.pdf_dpi = dpi_val
                                            
                                        images = convert_from_path(file_path, dpi=dpi_val)
                                        for img in images:
                                            buffered = io.BytesIO()
                                            img.save(buffered, format="PNG")
                                            b64 = base64.b64encode(buffered.getvalue()).decode('utf-8')
                                            content_list.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}})
                                    except ImportError:
                                        content_list.append({"type": "text", "text": "[PDF conversion failed: 'pdf2image' not installed. In Termux run: pkg install poppler && pip install pdf2image]"})
                                    except Exception as pdf_e:
                                        content_list.append({"type": "text", "text": f"[PDF processing error: {pdf_e}. Make sure 'poppler' is installed in Termux.]"})
                                else:
                                    with open(file_path, "rb") as f:
                                        b64 = base64.b64encode(f.read()).decode('utf-8')
                                    content_list.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})
                            except Exception as e:
                                content_list.append({"type": "text", "text": f"[Failed to load attached image: {file_path} - {e}]"})
                    return content_list

                # Prepare messages from history
                history = self.session_manager.active_sessions.get(agent.name, [])
                langchain_messages = []
                for hm in history:
                    content = hm.content
                    if not agent.is_capable_vision:
                        if "data:image" in content or "data:application/pdf" in content:
                            content = re.sub(r'data:(?:image|application/pdf);base64,[A-Za-z0-9+/=]+', '[Attachment stripped: Agent not vision capable]', content)

                    if hm.role == "system": langchain_messages.append(SystemMessage(content=content))
                    elif hm.role == "human": langchain_messages.append(HumanMessage(content=_format_vision_content(content, agent.is_capable_vision)))
                    elif hm.role == "ai": 
                        langchain_messages.append(AIMessageChunk(content=content, tool_calls=hm.tool_calls or []))
                        if hm.tool_calls:
                            # Self-Heal: Ensure every tool call has a response
                            outputs_by_name = {o.get("name"): o for o in (hm.tool_outputs or [])}
                            for tc in hm.tool_calls:
                                tc_name = tc.get("name")
                                if tc_name in outputs_by_name:
                                    output = outputs_by_name[tc_name]
                                    tool_content = str(output.get("content", ""))
                                    
                                    # 1. Standard plain-text ToolMessage (Satisfies strict API schemas)
                                    langchain_messages.append(ToolMessage(
                                        content=tool_content,
                                        name=tc_name,
                                        tool_call_id=tc.get("id", "unknown")
                                    ))
                                    
                                    # 2. Global Companion Injection: 
                                    # If ANY tool response contains an image, append a companion HumanMessage
                                    if "[Attached Image:" in tool_content:
                                        match = re.search(r'\[Attached Image: (.*?)\]', tool_content)
                                        if match and agent.is_capable_vision:
                                            filepath = match.group(1).strip()
                                            langchain_messages.append(HumanMessage(
                                                content=_format_vision_content(f"[Attached Image: {filepath}]", True)
                                            ))
                                else:
                                    # Placeholder for unfinished calls to satisfy LangGraph validation
                                    self.log_to_ui(f"[dim yellow]🛠️ Healing interrupted tool call: {tc_name}[/dim yellow]")
                                    langchain_messages.append(ToolMessage(
                                        content="[Tool execution was interrupted or cancelled during session transition.]",
                                        name=tc_name,
                                        tool_call_id=tc.get("id", "unknown")
                                    ))
                """
                # ---------------------------------------------------------------------
                # --- OPTIMIZATION PASS: Strip Older Automated Screenshots Only ---
                # ---------------------------------------------------------------------
                # 1. Locate the index of the absolute last HumanMessage containing an automated screenshot
                last_screenshot_msg_idx = -1
                for msg_idx, msg in enumerate(langchain_messages):
                    if isinstance(msg, HumanMessage) and isinstance(msg.content, list):
                        # Determine if this message is an automated screenshot
                        is_screenshot = False
                        if msg_idx < len(history):
                            is_screenshot = "screenshots/screen_" in (history[msg_idx].content or "")
                        else:
                            is_screenshot = True  # Dynamically injected companion message is always a screenshot

                        if is_screenshot and any(block.get("type") == "image_url" for block in msg.content):
                            last_screenshot_msg_idx = msg_idx
                
                # 2. Strip only older automated screenshots, leaving your user-uploaded files fully intact
                if last_screenshot_msg_idx != -1:
                    for msg_idx, msg in enumerate(langchain_messages):
                        if msg_idx < last_screenshot_msg_idx and isinstance(msg, HumanMessage) and isinstance(msg.content, list):
                            # Verify if this older message is indeed an automated screenshot
                            is_screenshot = False
                            if msg_idx < len(history):
                                is_screenshot = "screenshots/screen_" in (history[msg_idx].content or "")
                            else:
                                is_screenshot = True

                            if is_screenshot:
                                for block in msg.content:
                                    if block.get("type") == "image_url":
                                        block.clear()
                                        block.update({
                                            "type": "text",
                                            "text": "[Historical screen state omitted to ensure focus on the latest state]"
                                        })
                # ---------------------------------------------------------------------
                """
                # If no state exists, we must provide the initial input to the graph
                if not state.values:
                    stream_input = {"messages": langchain_messages}
                else:
                    # IMPORTANT: LangGraph checkpointer is independent of SessionManager.
                    # We must sync any "intercom" messages from SessionManager history 
                    # that LangGraph hasn't seen yet.
                    existing_messages = state.values.get("messages", [])

                    # SELF-HEAL CHECKPOINTER: LangGraph's internal state might be invalid
                    # if it contains an AIMessage with tool_calls that has no ToolMessages.
                    if existing_messages:
                        # Collect all tool calls and all responses across the entire history
                        needed_responses = {} # call_id -> tool_name
                        for m in existing_messages:
                            # Register tool calls
                            tcs = getattr(m, "tool_calls", None)
                            if tcs:
                                for tc in tcs:
                                    if "id" in tc:
                                        needed_responses[tc["id"]] = tc.get("name", "tool")

                            # Remove those that have responses (ToolMessage has tool_call_id)
                            tcid = getattr(m, "tool_call_id", None)
                            if tcid and tcid in needed_responses:
                                del needed_responses[tcid]

                        if needed_responses:
                            self.log_to_ui(f"[dim yellow]🛠️ Healing {len(needed_responses)} incomplete tool calls in checkpointer...[/dim yellow]")
                            healing_messages = []
                            for tid, tname in needed_responses.items():
                                healing_messages.append(ToolMessage(
                                    content="[Tool execution was interrupted or cancelled during session transition.]",
                                    name=tname,
                                    tool_call_id=tid
                                ))

                            try:
                                # Attempt to patch the existing state
                                executor.update_state(run_config, {"messages": healing_messages})
                                # Refresh state after healing
                                state = executor.get_state(run_config)
                                existing_messages = state.values.get("messages", [])
                            except Exception as he:
                                # If we can't even patch it, we'll hit the reset logic in the main except block
                                self.log_to_ui(f"[dim red]Checkpoint patching failed: {he}[/dim red]")
                                raise he

                    existing_contents = set()
                    for m in existing_messages:
                        existing_contents.add(str(m.content) if isinstance(m.content, list) else m.content)

                    missing_messages = []
                    for m in langchain_messages:
                        if isinstance(m, SystemMessage): continue
                        m_val = str(m.content) if isinstance(m.content, list) else m.content
                        if m_val not in existing_contents:
                            missing_messages.append(m)

                    if missing_messages:
                        # Update state with missing messages first
                        executor.update_state(run_config, {"messages": missing_messages})

                    # Now provide the NEW prompt
                    stream_input = {"messages": [HumanMessage(content=_format_vision_content(prompt, agent.is_capable_vision))]}

                # Stream execution
                consecutive_fail_count = 0
                MAX_CONSECUTIVE_FAILS = 5
                
                while consecutive_fail_count < MAX_CONSECUTIVE_FAILS:
                    try:
                        for event_type, event_data in executor.stream(stream_input, config=run_config, stream_mode=["messages", "updates"]):
                            if toolbox.ABORT_EVENT.is_set() or (batch_id != 0 and batch_id != self.current_batch_id):
                                raise Exception("Operation forcefully aborted or interrupted by user.")

                            if event_type == "messages":
                                chunk, metadata = event_data
                                if metadata.get("langgraph_node") == "agent" and isinstance(chunk, AIMessageChunk) and chunk.content:
                                    text_chunk = str(chunk.content)
                                    current_ai_text += text_chunk
                                    full_ai_response += text_chunk
                                    # --- NEW: Stream text chunk to TTS engine ---
                                    if getattr(self, "tts_enabled", False):
                                        self.tts_manager.stream_text(text_chunk)
                                    if not current_ai_widget:
                                        self.log_to_ui(Rule(style="dim"))
                                        self.log_to_ui(f"[bold {agent.color}]{agent.name}:[/bold {agent.color}]", is_markdown=False)
                                        current_ai_widget = Static(Markdown(""), classes="chat_msg")
                                        self.app.call_from_thread(self.query_one("#chat_messages").mount, current_ai_widget)
                                        
                                    display_text = render_latex_to_unicode(current_ai_text)
                                    self.app.call_from_thread(current_ai_widget.update, Markdown(display_text))
                                    self.app.call_after_refresh(lambda: self.query_one("#ai_chat_scroll").scroll_end(animate=False))

                            elif event_type == "updates":
                                # Progress Made: Reset consecutive fail counter
                                consecutive_fail_count = 0
                                
                                for node_name, node_data in event_data.items():
                                    messages = node_data.get("messages", [])
                                    if not isinstance(messages, list):
                                        messages = [messages]

                                    if node_name == "agent":
                                        for msg in messages:
                                            if hasattr(msg, 'additional_kwargs') and 'thought' in msg.additional_kwargs:
                                                self.log_to_ui(f"[dim]Thought:[/dim] {escape(msg.additional_kwargs['thought'])}")

                                            # Telegram integration
                                            if getattr(self, "current_telegram_chat_id", None) and current_ai_text.strip():
                                                # --- NEW: Pass agent.tts_voice ---
                                                self.telegram_manager.send_message(
                                                    self.current_telegram_chat_id, 
                                                    current_ai_text.strip(), 
                                                    title=agent.name, 
                                                    voice=agent.tts_voice
                                                )

                                            # Print outgoing tool calls
                                            if hasattr(msg, "tool_calls") and msg.tool_calls:
                                                self.log_to_ui(Rule(style="dim"))
                                                for tc in msg.tool_calls:
                                                    tool_calls.append(tc)
                                                    self.log_to_ui(f"[#808080]Calling Tool: {escape(tc['name'])} with args: {escape(str(tc['args']))}[/#808080]")

                                            # Clean up streaming variables for the next turn
                                            if getattr(self, "tts_enabled", False):
                                                self.tts_manager.flush_stream()
                                            current_ai_widget = None
                                            current_ai_text = ""

                                    # Print incoming tool results
                                    elif node_name == "tools":
                                        for msg in messages:
                                            tool_name = getattr(msg, 'name', 'tool')
                                            tool_outputs.append({"name": tool_name, "content": msg.content})
                                            
                                            # Intercept and hide raw search results from the UI to comply with DuckDuckGo terms
                                            if tool_name in ["search_web", "SearchWeb"]:
                                                summary = "[Search results successfully parsed and delivered to active agent context]"
                                            else:
                                                summary = (str(msg.content) + '...') if len(str(msg.content)) > 200 else str(msg.content)
                                            
                                            # Build the boxed widget
                                            box_content = f"[bold]Tool Result ({agent.name}):[/bold]\n{escape(summary)}"
                                            box_widget = Static(Text.from_markup(box_content), classes="tool_result_box")
                                            
                                            self.log_to_ui(box_widget)
                        
                        # Success: exit retry loop
                        break

                    except Exception as stream_e:
                        err_msg = str(stream_e).lower()
                        consecutive_fail_count += 1
                        
                        if ("connection" in err_msg or "reset" in err_msg or "timeout" in err_msg or "429" in err_msg) and consecutive_fail_count < MAX_CONSECUTIVE_FAILS:
                            self.log_to_ui(f"[yellow]⚠️ Stream interrupted ({escape(str(stream_e))}). Retrying {consecutive_fail_count}/{MAX_CONSECUTIVE_FAILS}...[/yellow]")
                            time.sleep(3)
                            stream_input = None # LangGraph resumes from checkpoint
                            continue
                        raise stream_e

                # Broadcast AI response and tool outputs to others
                if full_ai_response.strip() or tool_outputs or tool_calls:
                    ai_response = full_ai_response.strip()
                    # 1. Save to session manager so others can see it
                    self.session_manager.broadcast_message(agent.name, ai_response, is_ai=True, tool_outputs=tool_outputs, tool_calls=tool_calls)
                    self.app.call_from_thread(self.update_tokens)
                    
                    # 2. Check for new mentions in AI response and add to queue
                    new_mentions = self.agent_manager.get_mentions(ai_response)
                    with self.turn_lock:
                        for m_name in new_mentions:
                            m_agent = self.agent_manager.get_agent(m_name)
                            # Avoid duplicates in queue and don't re-queue self immediately
                            if m_agent and m_agent.name != agent.name and m_agent not in self.turn_queue:
                                self.turn_queue.append(m_agent)

                # 3. Check queue for the next agent to respond
                next_agent = None
                with self.turn_lock:
                    if self.turn_queue:
                        next_agent = self.turn_queue.pop(0)

                if next_agent:
                    self.log_to_ui(f"[bold cyan]>> Sequential hand-off to {next_agent.name}...[/bold cyan]")
                    self.session_manager.join_conversation(agent.name, next_agent, list(self.agent_manager.agents.values()))
                    self.run_agent_task(next_agent, prompt, batch_id=batch_id)

            except Exception as e:
                error_str = str(e)
                # DEEP RESET: If the checkpointer is so corrupted that healing fails,
                # we force a fresh thread and reconstruct from the (healed) JSON history.
                if ("ToolMessage" in error_str or "tool_calls" in error_str) and "_rst_" not in thread_id:
                    self.log_to_ui("[bold yellow]⚠️ State Corruption Found. Performing Deep Reset...[/bold yellow]")
                    # Timestamped reset suffix to bypass the broken SQLite thread
                    new_thread_id = f"{thread_id}_rst_{int(time.time())}"
                    # Remove from running agents so it can re-trigger
                    self._running_agents.discard(agent.name)
                    return self.run_agent_task(agent, prompt, override_thread_id=new_thread_id)
                
                # Silence abort/interrupt errors
                if "aborted" in error_str.lower() or "interrupted" in error_str.lower():
                    return

                self.log_to_ui(f"[bold red]Execution Error ({agent.name}):[/bold red] {e}")
        finally:
            self._running_agents.discard(agent.name)
            self.app.call_from_thread(self._toggle_spinner, False, agent.name, agent.color)    
    def _toggle_spinner(self, show: bool, agent_name: str = "Agent", agent_color: str = "#00FFFF"):
        if show:
            self._running_task_count += 1
        else:
            self._running_task_count = max(0, self._running_task_count - 1)
            
        try:
            self.query_one("#ai_thinking_spinner").display = (self._running_task_count > 0)
        except Exception:
            pass

    def tick_spinners(self):
        """Animates all active spinners (research tasks and active agents)."""
        try:
            self.spinner_idx = (self.spinner_idx + 1) % len(self.spinner_chars)
            char = self.spinner_chars[self.spinner_idx]

            # 1. Animate Research Tasks (if container is visible)
            try:
                container = self.query_one("#progress_container")
                if container.styles.display != "none":
                    for row in self.query(".task_row"):
                        try:
                            prog = row.query_one(ProgressBar)
                            spin_label = row.query_one(".task_spinner")
                            if prog.progress < prog.total:
                                spin_label.update(char)
                            else:
                                spin_label.update("✅")
                        except Exception: continue
            except Exception: pass
            
            # 2. Animate Main Thinking Indicator
            try:
                spinner_label = self.query_one("#ai_thinking_spinner")
                if self._running_agents:
                    agents_list = sorted(list(self._running_agents))
                    if len(agents_list) == 1:
                        a_name = agents_list[0]
                        a_cfg = self.agent_manager.get_agent(a_name)
                        a_color = a_cfg.color if a_cfg else "white"
                        spinner_label.update(Text.from_markup(f"[bold {a_color}]{char} {a_name} is working...[/]"))
                    elif 1 < len(agents_list) <= 5:
                        parts = []
                        for i, name in enumerate(agents_list):
                            cfg = self.agent_manager.get_agent(name)
                            color = cfg.color if cfg else "white"
                            parts.append(f"[bold {color}]{name}[/]")
                        
                        if len(parts) == 2:
                            names_str = f"{parts[0]} and {parts[1]}"
                        else:
                            names_str = ", ".join(parts[:-1]) + f", and {parts[-1]}"
                        spinner_label.update(Text.from_markup(f"{char} {names_str} are working..."))
                    else:
                        spinner_label.update(Text.from_markup(f"{char} [bold #dda0dd]{len(agents_list)} agents[/] are working..."))
                else:
                    spinner_label.update(f"{char} Agent is working...")
            except Exception: pass

        except Exception:
            pass
    
    def tick_scheduler(self):
        """Checks the clock and fires off scheduled tasks natively as a Ghost User."""
        if getattr(self, "shell_mode", False) or not hasattr(self, "schedule_manager"):
            return
            
        now = datetime.now()
        current_time = now.strftime("%H:%M")
        current_date = now.strftime("%Y-%m-%d")
        
        for task in self.schedule_manager.tasks:
            if not task.is_active: continue
            
            # If the current time is reached, and it hasn't fired today
            if current_time >= task.time_str and task.last_run_date != current_date:
                # IMPORTANT: If the system is currently busy doing work for you, 
                # it will wait for the next 30-second tick until the agents are free.
                if self._running_agents:
                    continue 
                
                # Mark it as fired today so it doesn't trigger again
                task.last_run_date = current_date
                self.schedule_manager.save()
                
                # 1. Create a NEW CHAT (Wipes memory, starts a fresh session ID)
                self.action_clear_all_contexts()
                self.log_to_ui(Rule(title="[bold yellow]⏰ INITIATING AUTOMATED SCHEDULED TASK", style="dim"))
                
                # 2. Construct the Ghost User prompt
                # We prepend the assigned agent's name so it routes to them first.
                # If your prompt contains other @mentions, the router will queue them automatically!
                full_prompt = f"@{task.agent_name} [Automated Scheduled Task]:\n{task.prompt}"
                
                # 3. Fetch the actual chat input widget and fire a simulated submission
                from agent import ChatInput
                chat_input = self.query_one("#ai_chat_input", ChatInput)
                
                # Submit the event exactly as if the user pressed Enter
                event = ChatInput.Submitted(chat_input, full_prompt)
                self.on_input_submitted(event)
    
    def update_tokens(self):
        try:
            history = self.session_manager.active_sessions.get(self.active_agent.name, [])
            text = "".join((m.content or "") + "".join(str(out.get("content", "")) for out in (m.tool_outputs or [])) for m in history)
            count = 0
            if HAS_TIKTOKEN:
                try:
                    enc = tiktoken.get_encoding("cl100k_base")
                    count = len(enc.encode(text))
                except Exception:
                    count = len(text) // 4
            else:
                count = len(text) // 4
            self.current_tokens = count
            self.update_status_bar()
        except Exception:
            pass

    def request_clarification(self, options: Optional[List[str]] = None, agent_name: str = "Agent") -> str:
        """Pushes the ClarificationModal and waits for the result."""
        result_event = threading.Event()
        final_result = [""]

        def handle_result(res: str):
            final_result[0] = res or ""
            result_event.set()

        self.app.call_from_thread(self.app.push_screen, ClarificationModal(options, agent_name=agent_name), handle_result)
        
        # Wait for the user to submit
        while not result_event.is_set():
            if toolbox.ABORT_EVENT.is_set():
                return ""
            time.sleep(0.1)
            
        return final_result[0]

    def handle_telegram_input(self, chat_id: int, text: str):
        """Processes incoming Telegram messages exactly like UI chat."""
        def _process():
            try:
                self.current_telegram_chat_id = chat_id
                
                self._write_log(Rule(style="dim"))
                self._write_log(f"[bold blue]Telegram User ({chat_id}):[/bold blue]", is_markdown=False)
                self._write_log(text, is_markdown=True)
                
                # --- INTERRUPT SYSTEM ---
                is_interrupt = False
                if self._running_agents:
                    self.action_abort()
                    is_interrupt = True
                
                self.current_batch_id += 1
                batch_id = self.current_batch_id
                # ------------------------

                # Determine routing and clean prompt
                acting_agent = self.active_agent
                clean_prompt = text
                acting_agents = []
                is_team = False
                is_room = False

                if text.strip().lower().startswith("@team") or text.strip().lower().startswith("@room"):
                    is_team = text.strip().lower().startswith("@team")
                    is_room = not is_team
                    prefix_len = 5
                    clean_prompt = text.strip()[prefix_len:].strip()
                    
                    if is_interrupt:
                        clean_prompt = f"the User interrupted to say this: {clean_prompt}"
                    
                    if is_team:
                        all_agents_list = list(self.agent_manager.agents.values())
                        for agent in all_agents_list:
                            self.session_manager.init_agent_session(agent, all_agents_list)
                            self.session_manager.join_conversation(self.active_agent.name, agent, all_agents_list)
                        acting_agents = all_agents_list
                    else:
                        active_names = list(self.session_manager.active_sessions.keys())
                        all_agents_list = list(self.agent_manager.agents.values())
                        for name in active_names:
                            agent = self.agent_manager.get_agent(name)
                            if agent:
                                self.session_manager.join_conversation(self.active_agent.name, agent, all_agents_list)
                                acting_agents.append(agent)
                else:
                    match = re.match(r'^@([^\s/]+)\s+(.*)', text)
                    if match:
                        # Strip trailing punctuation so Telegram users can type '@Ron,'
                        target_name = match.group(1).rstrip(",.:!?()[]{}")
                        target_agent = self.agent_manager.get_agent(target_name)
                        if target_agent:
                            acting_agent = target_agent
                            clean_prompt = match.group(2)
                            if is_interrupt:
                                clean_prompt = f"the User interrupted to say this: {clean_prompt}"
                            self.session_manager.join_conversation(self.active_agent.name, acting_agent, list(self.agent_manager.agents.values()))
                        else:
                            if is_interrupt:
                                clean_prompt = f"the User interrupted to say this: {clean_prompt}"
                    else:
                        if is_interrupt:
                            clean_prompt = f"the User interrupted to say this: {clean_prompt}"
                        self.session_manager.init_agent_session(acting_agent, list(self.agent_manager.agents.values()))
                    
                    acting_agents = [acting_agent]

                if not clean_prompt and (is_team or is_room):
                    return

                time_stamp = f"[Today's date is {datetime.now().strftime('%A, %B %d, %Y')} and the time now is {datetime.now().strftime('%H:%M')}]\n"
                processed_prompt = time_stamp + handle_ampersand_commands(clean_prompt, self)
                
                # Broadcast user message
                self.session_manager.broadcast_message(f"Telegram User ({chat_id})", processed_prompt, is_ai=False)
                self.update_tokens()
                # --- START THE TYPING LOOP ---
                self.telegram_manager.start_chat_action(chat_id, "typing")
                
                toolbox.ABORT_EVENT.clear()
                for agent in acting_agents:
                    self.run_agent_task(agent, processed_prompt, batch_id=batch_id)

            except Exception as e:
                self.log_to_ui(f"[bold red]Telegram Input Error:[/bold red] {e}")
                
        self.app.call_from_thread(_process)
    
    def handle_stt_input(self, text: str, action: str = "append"):
        """Handles pause-appends, auto-submits, and hotword deletions in the UI."""
        def _process():
            try:
                chat_input = self.query_one("#ai_chat_input", ChatInput)
                current_val = chat_input.text.strip() # CHANGED

                if action == "append":
                    if text:
                        new_val = (current_val + " " + text).strip()
                        chat_input.text = new_val # CHANGED
                        self.stt_append_history.append(text)
                
                elif action == "delete":
                    if self.stt_append_history:
                        last_text = self.stt_append_history.pop()
                        if chat_input.text.endswith(last_text): # CHANGED
                            chat_input.text = chat_input.text[:-len(last_text)].strip() # CHANGED
                        else:
                            chat_input.text = chat_input.text.replace(last_text, "").strip() # CHANGED

                elif action == "submit":
                    if text:
                        chat_input.text = (current_val + " " + text).strip() # CHANGED
                    
                    self.stt_append_history = [] 
                    
                    if chat_input.text.strip(): # CHANGED
                        self.on_input_submitted(ChatInput.Submitted(chat_input, chat_input.text)) # CHANGED

                chat_input.focus()
                
                # Move cursor to end
                lines = chat_input.text.split("\n")
                chat_input.cursor_location = (len(lines)-1, len(lines[-1])) # CHANGED
                
            except Exception as e:
                self.log_to_ui(f"[bold red]STT UI Binding Error:[/bold red] {e}")
                
        self.app.call_from_thread(_process)

    def mount_progress(self, tasks: list[str]):
        """Integrated Progress Mounting."""
        def _mount():
            try:
                # Log to the main chat
                self._write_log(Rule(title="[bold]Deep Research Modules Dispatched[/]", style="dim"))

                # Show the container
                container = self.query_one("#progress_container")
                container.styles.display = "block"

                # GET references to the panes
                left_pane = self.query_one("#progress_left")
                right_log = self.query_one("#progress_right", RichLog)

                # Only clear the inside of the left pane, not the whole dashboard!
                left_pane.query("*").remove()
                right_log.clear()

                from textual.containers import Horizontal
                for t in tasks:
                    # Construct ID (toolbox.py expects 'task_...')
                    safe_id = "task_" + "".join(c if c.isalnum() else "_" for c in t)

                    bar = ProgressBar(total=100, show_eta=False, id=f"prog_{safe_id}")

                    row = Horizontal(
                        Label(self.spinner_chars[0], id=f"spin_{safe_id}", classes="task_spinner"),
                        bar,
                        classes="task_row", id=f"row_{safe_id}"
                    )
                    left_pane.mount(row)

                # Ensure we scroll the main chat to reveal the new dashboard
                self.app.call_after_refresh(
                    lambda: self.query_one("#ai_chat_scroll").scroll_end(animate=False)
                )
            except Exception as e:
                self.log_to_ui(f"[bold red]UI Error:[/bold red] {e}")

        self.app.call_from_thread(_mount)

    def update_progress(self, task: str, percent: float, msg: str):
        safe_id = "task_" + "".join(c if c.isalnum() else "_" for c in task)
        def _update():
            try:
                # 1. Stream the raw log directly to the Right Pane
                log_pane = self.query_one("#progress_right", RichLog)
                log_pane.write(msg)

                # 2. Update the Left Pane progress bar and spinner
                prog = self.query_one(f"#prog_{safe_id}", ProgressBar)
                spin = self.query_one(f"#spin_{safe_id}", Label)

                if percent is not None:
                    prog.update(progress=percent)
                    if percent >= 100:
                        spin.update("✅") # Stop animating when done

                self.app.call_after_refresh(
                    lambda: self.query_one("#ai_chat_scroll").scroll_end(animate=False)
                )
            except Exception: pass

        self.app.call_from_thread(_update)

    def hide_progress(self):
        """Removes progress bars on completion."""
        def _hide():
            try:
                self.query_one("#progress_container").styles.display = "none"
            except Exception:
                pass
        self.app.call_from_thread(_hide)