TrippleEffect
├── config.yaml
├── data
├── helperfiles
│   ├── DEVELOPMENT_RULES.md
│   ├── FUNCTIONS_INDEX_002.md
│   ├── FUNCTIONS_INDEX.md
│   ├── PROJECT_PLAN.md
│   ├── PROJECT_TREE.md
│   └── TOOL_MAKING.md
├── LICENSE
├── logs
├── ollama-proxy
│   ├── package.json
│   ├── package-lock.json
│   └── server.js
├── projects
├── prompts_injected.txt
├── prompts.json
├── README.md
├── requirements.txt
├── run.sh
├── sandboxes
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
│   │   ├── session_manager.py
│   │   └── state_manager.py
│   ├── api
│   │   ├── http_routes.py
│   │   ├── __init__.py
│   │   └── websocket_manager.py
│   ├── config
│   │   ├── config_manager.py
│   │   ├── __init__.py
│   │   ├── model_registry.py
│   │   └── settings.py
│   ├── core
│   │   ├── database_manager.py
│   │   ├── __init__.py
│   ├── __init__.py
│   ├── llm_providers
│   │   ├── base.py
│   │   ├── __init__.py
│   │   ├── ollama_provider.py
│   │   ├── openai_provider.py
│   │   ├── openrouter_provider.py
│   ├── main.py
│   ├── tools
│   │   ├── base.py
│   │   ├── executor.py
│   │   ├── file_system.py
│   │   ├── github_tool.py
│   │   ├── __init__.py
│   │   ├── knowledge_base.py
│   │   ├── manage_team.py
│   │   ├── project_management.py
│   │   ├── send_message.py
│   │   ├── system_help.py
│   │   └── web_search.py
│   ├── ui
│   │   └── __init__.py
│   └── utils
│       ├── __init__.py
│       └── network_utils.py
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