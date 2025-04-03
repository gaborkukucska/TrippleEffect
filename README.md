# TrippleEffect 🧑‍🚒🧑‍🏫👩‍🔧 ( To Be Updated )
*Collaborative LLM Agent Framework*

![TrippleEffect Architecture Diagram](docs/assets/architecture-overview.png)

A multi-agent system for Android/Termux that enables three LLM agents to collaborate on complex tasks through configurable workflows, complete with sandboxed development environments.

## Features
- 🧠 3 Configurable LLM Agents with cross-communication
- 🛠️ Python Sandbox Environment with package management
- ⚙️ Complex Model Settings (temp, top-p, system messages)
- 📁 File Attachment & Voice Input Support
- 🔒 Security-Enhanced Execution Environment
- 🧩 Tool Creation Framework

## Requirements
- Android 9+ Device
- [Termux App](https://termux.dev/en/)
- Minimum 4GB RAM (8GB recommended)
- Stable Internet Connection

## Quick Start
```bash
# Clone repository
git clone https://github.com/gaborkukucska/TrippleEffect.git
cd TrippleEffect
```

# Run setup script
```bash
bash setup.sh
```

# Start application
```bash
python main.py
```

## Documentation
- [Project Plan](PROJECT_PLAN.md)
- [API Documentation](docs/API_REFERENCE.md)
- [Security Model](docs/SECURITY.md)

## Configuration Guide
1. Access web UI at `http://localhost:5000`
2. Navigate to Agent Configuration
3. Set API keys and model parameters
4. Configure sandbox environments
5. Save and initialize agents

## Support
For issues and feature requests, please [open an issue](https://github.com/yourusername/TrippleEffect/issues).
