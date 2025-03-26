# TrippleEffect

A collaborative multi-agent system with browser-based UI for Termux.

## Features
- 3 configurable LLM agents
- Browser-based interface
- Sandboxed environments
- Real-time collaboration

## Installation
```bash
pkg install python git -y
git clone https://github.com/gaborkukucska/TrippleEffect.git
cd TrippleEffect
source venv/bin/activate
pip install -r requirements.txt
python src/main.py
```

## Usage
1. Access the UI at `http://localhost:8000`
2. Configure agents in Settings
3. Submit tasks via text/voice/files
