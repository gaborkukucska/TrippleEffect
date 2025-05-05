~/TrippleEffect$ tree --gitignore
.
├── helperfiles
│   ├── DEVELOPMENT_RULES.md
│   ├── FUNCTIONS_INDEX_002.md
│   ├── FUNCTIONS_INDEX.md
│   ├── PROJECT_PLAN.md
│   ├── PROJECT_TREE.md
│   ├── SUGGESTIONS.md
│   ├── TASKWARRIOR.md
│   └── TOOL_MAKING.md
├── LICENSE
├── prompts.json
├── README.md
├── requirements.txt
├── run.sh
├── setup.sh
├── src
│   ├── agents
│   │   ├── agent_lifecycle.py
│   │   ├── agent_tool_parser.py
│   │   ├── constants.py
│   │   ├── core.py
│   │   ├── cycle_handler.py
│   │   ├── failover_handler.py
│   │   ├── __init__.py
│   │   ├── interaction_handler.py
│   │   ├── manager.py
│   │   ├── performance_tracker.py
│   │   ├── prompt_utils.py
│   │   ├── provider_key_manager.py
│   │   ├── __pycache__
│   │   │   ├── agent_lifecycle.cpython-312.pyc
│   │   │   ├── agent_tool_parser.cpython-312.pyc
│   │   │   ├── constants.cpython-312.pyc
│   │   │   ├── core.cpython-312.pyc
│   │   │   ├── cycle_handler.cpython-312.pyc
│   │   │   ├── failover_handler.cpython-312.pyc
│   │   │   ├── __init__.cpython-312.pyc
│   │   │   ├── interaction_handler.cpython-312.pyc
│   │   │   ├── manager.cpython-312.pyc
│   │   │   ├── performance_tracker.cpython-312.pyc
│   │   │   ├── prompt_utils.cpython-312.pyc
│   │   │   ├── provider_key_manager.cpython-312.pyc
│   │   │   ├── session_manager.cpython-312.pyc
│   │   │   ├── state_manager.cpython-312.pyc
│   │   │   └── workflow_manager.cpython-312.pyc
│   │   ├── session_manager.py
│   │   ├── state_manager.py
│   │   └── workflow_manager.py
│   ├── api
│   │   ├── http_routes.py
│   │   ├── __init__.py
│   │   ├── __pycache__
│   │   │   ├── http_routes.cpython-312.pyc
│   │   │   ├── __init__.cpython-312.pyc
│   │   │   └── websocket_manager.cpython-312.pyc
│   │   └── websocket_manager.py
│   ├── config
│   │   ├── config_manager.py
│   │   ├── __init__.py
│   │   ├── model_registry.py
│   │   ├── __pycache__
│   │   │   ├── config_manager.cpython-312.pyc
│   │   │   ├── __init__.cpython-312.pyc
│   │   │   ├── model_registry.cpython-312.pyc
│   │   │   └── settings.cpython-312.pyc
│   │   └── settings.py
│   ├── core
│   │   ├── database_manager.py
│   │   ├── __init__.py
│   │   └── __pycache__
│   │       ├── database_manager.cpython-312.pyc
│   │       └── __init__.cpython-312.pyc
│   ├── __init__.py
│   ├── llm_providers
│   │   ├── base.py
│   │   ├── __init__.py
│   │   ├── ollama_provider.py
│   │   ├── openai_provider.py
│   │   ├── openrouter_provider.py
│   │   └── __pycache__
│   │       ├── base.cpython-312.pyc
│   │       ├── __init__.cpython-312.pyc
│   │       ├── ollama_provider.cpython-312.pyc
│   │       ├── openai_provider.cpython-312.pyc
│   │       └── openrouter_provider.cpython-312.pyc
│   ├── main.py
│   ├── __pycache__
│   │   ├── __init__.cpython-312.pyc
│   │   └── main.cpython-312.pyc
│   ├── tools
│   │   ├── base.py
│   │   ├── executor.py
│   │   ├── file_system.py
│   │   ├── github_tool.py
│   │   ├── __init__.py
│   │   ├── knowledge_base.py
│   │   ├── manage_team.py
│   │   ├── project_management.py
│   │   ├── __pycache__
│   │   │   ├── base.cpython-312.pyc
│   │   │   ├── executor.cpython-312.pyc
│   │   │   ├── file_system.cpython-312.pyc
│   │   │   ├── github_tool.cpython-312.pyc
│   │   │   ├── __init__.cpython-312.pyc
│   │   │   ├── knowledge_base.cpython-312.pyc
│   │   │   ├── manage_team.cpython-312.pyc
│   │   │   ├── project_management.cpython-312.pyc
│   │   │   ├── send_message.cpython-312.pyc
│   │   │   ├── system_help.cpython-312.pyc
│   │   │   ├── tool_information.cpython-312.pyc
│   │   │   └── web_search.cpython-312.pyc
│   │   ├── send_message.py
│   │   ├── system_help.py
│   │   ├── tool_information.py
│   │   └── web_search.py
│   ├── ui
│   │   └── __init__.py
│   └── utils
│       ├── __init__.py
│       ├── network_utils.py
│       └── __pycache__
│           ├── __init__.cpython-312.pyc
│           └── network_utils.cpython-312.pyc
├── static
│   ├── css
│   │   └── style.css
│   └── js
│       ├── api.js
│       ├── config.js
│       ├── configView.js
│       ├── domElements.js
│       ├── handlers.js
│       ├── main.js
│       ├── session.js
│       ├── state.js
│       ├── ui.js
│       ├── utils.js
│       └── websocket.js
└── templates
    └── index.html
