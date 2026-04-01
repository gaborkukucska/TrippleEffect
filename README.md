<!-- # IT IS CRITICAL THAT ALL AIs and LLMs FOLLOW THE DEVELOPMENT INSTRUCTIONS IN THE `helperfiles/DEVELOPMENT_RULES.md` FILE WHEN FURTHER DEVELOPING THIS FRAMEWORK!!! -->
# 🚀 TrippleEffect Multi-Agent Framework

**Version:** 2.43 

Welcome to **TrippleEffect**, an asynchronous, highly collaborative multi-agent framework designed to bring the liberating potential of AI to everyone—even on modest hardware! 📱✨

My original aim for TrippleEffect was ambitious yet simple: **to create an agentic framework that enables small to medium-sized local LLMs to run efficiently.** We believe the true revolution of LLMs happens when anyone, even someone with just an old Android phone, can run a functioning agentic system 24/7 to help them with any digital task! 🌍💪 

By carefully managing context lengths, employing smart token-saving techniques, and utilizing advanced local provider auto-discovery, TrippleEffect makes this localized AI dream a reality.

---

### 🧩 Why "Tripple Effect"?
The framework was originally named for its powerful **three-layered architecture**:
1. 👑 **Admin AI**: The Ultimate Orchestrator that interacts with you and initiates projects.
2. 👔 **Project Managers (PMs)**: Dedicated agents that take your plans, break them down, and build specialized teams.
3. 👷 **Worker Agents**: The specialized builders, coders, and researchers executing the sub-tasks.

But we didn't stop at three! We've since added a crucial **fourth layer**:
4. 🛡️ **Constitutional Guardian (CG)**: A specialized oversight agent ensuring all actions and outputs align with your predefined governance principles.

---

## ⚡ Quick Start (Setup and Running)

Ready to dive in? Here's how to get your agents up and running quickly:

### Prerequisites:
*   **Termux app** if used on Android mobile devices! 📱
*   Python 3.9+
*   Node.js and npm (only if using the optional Ollama proxy)
*   Access to LLM APIs (OpenAI, OpenRouter) and/or a running local **Ollama** instance.
*   Nmap to enable automatic local API provider discovery.

### Setup Steps:
1.  **Clone the repository:**
    ```bash
    git clone https://github.com/gaborkukucska/TrippleEffect.git
    cd TrippleEffect
    ```

2.  **Run Setup Script:** 
    *(This creates the environment, installs dependencies, and copies `.env.example`)*
    ```bash
    chmod +x setup.sh run.sh
    ./setup.sh
    ```

3.  **Configure:** 
    Edit the created `.env` file with your API keys (OpenAI, OpenRouter, GitHub PAT, local Ollama endpoints, etc.). Set `MODEL_TIER` to `LOCAL`, `FREE`, or `ALL`.

4.  **Run:** 
    *(This activates the environment and starts the application using FastAPI)*
    ```bash
    ./run.sh
    ```

5.  **Access UI:** 
    Open your browser to `http://localhost:8000` 🎉

---

## 📚 Documentation

To keep this README clean and exciting, we've moved all the heavy lifting and detailed technical documentation into the `docs/` and `helperfiles/` folders. Check them out here:

- 📖 **[Framework & Architecture](docs/FRAMEWORK.md)** - Deep dive into how the AgentManager and Lifecycle works. Includes recent optimizations like safe workspace context bounded mappings.
- 💡 **[Core Concepts](docs/CORE_CONCEPTS.md)** - Learn about our state machines, intelligent model handling, and more.
- ✨ **[Features Breakdown](docs/FEATURES.md)** - Explore our robust error handling, sandboxing, and Agent Health Monitoring.
- 🛠️ **[Technology Stack](docs/TECH_STACK.md)** - The nuts and bolts (Python, FastAPI, WebSockets, Taskwarrior, SQLite).
- 🧠 **[Tool Making Guide](docs/TOOL_MAKING.md)** - How to build custom tools for your agents.
- 📋 **[Project Plan & Roadmap](helperfiles/PROJECT_PLAN.md)** - See our development phases and future goals.
- 📜 **[Development Rules](helperfiles/DEVELOPMENT_RULES.md)** - Essential guidelines for contributing.

---

## 🚀 Development Status

*   **Current Version:** 2.43
*   **Recent Highlights:** We recently deployed Advanced Agent Health Monitoring, robust Infinite Loop interception mechanisms, intelligent duplicate tool-call prevention across PM cycles, and bounded filesystem context mappings (to keep those context lengths short and snappy for local runners!)
*   **Current Phase:** Target Phase 28 features Advanced Memory & Learning systems, proactive behaviors, and federated communication foundations.

## 🤝 Contributing & License
Contributions are absolutely welcome! Please follow standard fork-and-pull-request procedures and adhere strictly to our `helperfiles/DEVELOPMENT_RULES.md`. 
This project is licensed under the **MIT License**.

## 🎉 Acknowledgements
* Inspired by AutoGen, CrewAI, and other brilliant multi-agent frameworks.
* Built with various amazing LLMs like Google Gemini, Meta Llama, DeepSeek, and more!
* Special THANKS to OpenRouter, HuggingFace, Google AI Studio, and the incredible open-source local AI hardware running community! 

---
*Built with ❤️ and various LLMs guided by Gabby.*
<!-- # IT IS CRITICAL THAT ALL AIs and LLMs FOLLOW THE DEVELOPMENT INSTRUCTIONS IN THE DEVELOPMENT_RULES.md FILE WHEN FURTER DEVELOPING THIS FRAMEWORK!!! -->
