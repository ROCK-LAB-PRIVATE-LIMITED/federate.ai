# Federate.AI

Federate is a cross-platform, terminal-based **peer to peer** AI orchestration and universal automation system. 

It turns your terminal into an interactive workbench where you can run specialized teams of AI agents who talk to and collaborate with each other, use voice input and output, execute desktop automation commands, and much more.

---

## Why We Built Federate

In many underprivileged regions of the developing world, owning a desktop computer is often a luxury, yet nearly everyone has access to a smartphone. Federate is written to bring the joy of programming to those pocket-sized screens. It is designed to run natively and comfortably inside **Termux on Android**, transforming a sub-$100 smartphone into a fully capable engineering workstation.

But Federate is also built for the most advanced power-users on high-end hardware. It runs on **any Mac or PC** you can throw at it, unlocking system-level features like desktop automation, local speech-to-text with hotword detection, and text-to-speech voice generation. 

It is designed to be the ultimate equalizer: accessible to everyone, yet incredibly powerful for those with top-tier setups.

---

## What Makes Federate Different?

### 1. Orchestration Freedom
Most multi-agent frameworks force you to write rigid, complex Python graphs and state-machine code to define how agents interact. Federate replaces this overhead with simple prompt driven system. 

You organize your team of agents **simply by writing their backstories**. Describe how they relate to one another, and Federate builds the communication network dynamically. You can create:
* **A true Peer-to-Peer hierarchy where every agent is equal to every other agent.** This is the default and exclusive to Federate. No other agentic system does this as of date (2nd July 2026). We are the first to implement it (though many will likely follow suit).
* A strict corporate hierarchy or military style chain-of-command.
* A collaborative Hub-and-Spoke swarm.
* Anything else you can think of.

### 2. Multi-Model, Cross-Provider Collaboration
Federate supports a true multi-model ecosystem. Because it abstracts the underlying LLM provider, you can bring different models into the same session. You can easily watch **ChatGPT and Gemini debate** a technical architecture decision, a code optimization, or a creative writing prompt directly inside your terminal window.

### 3. Native Desktop Vision & Speech (Computer Only)
On desktop machines, agents can physically interact with your computer, listen for input and also talk back to you, using any model not just those with speech understanding or synthesis baked in.

### 4. Pocket-Sized Software Engineering
Even on mobile, you can delegate complex multi-step coding tasks to an autonomous sub-agent. The system spawns an isolated local git worktree, writes and edits code, runs your local tests, and presents you with a clean commit diff to approve—ensuring your active development workspace remains untouched. Did you know your old Android can compile Rust?

---

## Feature Checklist

* **Context Injection (`&`):** Type `&` followed by a file or directory (e.g., `&src/app.py` or `&src/`) to instantly parse and inject that code directly into your prompt.
* **Direct Shell Passthrough (`!`):** Type `!` followed by a command (e.g., `!git diff`) to run it in your local workspace and feed the output to the AI.
* **Integrated Text IDE:** Press `F6` to instantly toggle between the chat view, a local file tree, a code editor with symbol outline, and an execution dashboard.
* **And a lot more:** Offline Persistent memory with semantic search, goal management, skills (passive and active skills, the latter again a Federate exclusive as on date), speech to text, text to speech, research orchestration, computer use, telegram integration, task scheduling etc already baked in, more features might be added in the future.
---

## Installation

Install the package via `pip`. We recommend installing with all optional extras to enable local voice/audio and serving capabilities:

```bash
pip install "federate[all]"
```
*(For a lightweight installation without audio or computer usage capabilities, run `pip install federate` instead on Termux and Raspberry Pi).*

---

## Quickstart

Start the application from your terminal:

```bash
federate
```
*(You can also open a specific project directory directly: `federate path/to/folder`)*

### Basic Setup
1. Once the interface loads, press **`F4`** to open the **Agent Editor**.
2. Set up your active agent, including your API keys and model choices (Federate is pre-configured for OpenRouter, but works with any OpenAI-compatible API). 
3. Federate will securely encrypt and save your credentials inside your native OS keychain.

### Changing Safety Modes (`Ctrl+T` / `/arm`)
To protect your workspace, the system boots in **SAFE (PLAN)** mode. In this mode, agents can search the web and read files, but they cannot edit code, run terminal commands, or control your computer.
* Press **`Ctrl+T`** or type **`/arm`** to cycle permissions:
  * **SAFE (PLAN):** Read-only. Great for planning and research.
  * **SEMI-AUTO:** Agents can edit and execute, but Federate will present a popup asking you to approve every single tool execution.
  * **FULL-AUTO:** Agents can run autonomous toolchains in the background.

### Command Reference
* **Inject Files:** Type `&` followed by the file path (e.g., `&src/main.py`). Use **`UP/DOWN`** arrow keys to cycle through autocomplete suggestions.
* **Mention Agents:** Type `@` followed by the agent name to route your message to a specific agent. Use `@team` to broadcast to everyone, or `@room` to talk to agents active in the current session. **Agents can also use @ to invoke other agents,** which is the core of the peer to peer system.
* **Key Bindings:**
  * **`F2`**: Session Manager (Create a new chat or load historical multi-agent sessions)
  * **`F4`**: Open the Active Agent configuration
  * **`F5`**: Cycle the host agent
  * **`F6`**: Cycle through UI panels (Chat ↔ IDE Editor ↔ Executions Dashboard)
  * **`F8`**: Change your workspace directory
  * **`Ctrl+K`**: Wipe the memory of all agents
  * **`Ctrl+A`**: **ABORT** (Emergency stop for any running AI tasks or terminal commands)
  * **`Ctrl+Q`**: Quit Federate
