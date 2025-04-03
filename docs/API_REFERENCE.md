# TrippleEffect API Reference

## 1. Agent Configuration API
### Schema
```json
{
  "name": "AgentName",
  "api_config": {
    "provider": "openai|anthropic|openrouter|ollama",
    "api_key": "string",
    "model": "string",
    "base_url": "string (optional)"
  },
  "model_params": {
    "temperature": 0.7,
    "top_p": 0.9,
    "max_tokens": 2000,
    "system_messages": ["string"]
  },
  "sandbox": {
    "venv_path": "/path",
    "disk_quota": "500MB",
    "allowed_modules": ["math", "datetime"]
  }
}
```

### Validation Rules
- `name`: 3-20 chars, unique
- `temperature`: 0.0-2.0
- `system_messages`: Max 5 items
- `disk_quota`: Format \d+[MB|GB]

---

## 2. API Clients
### OpenAI
```python
{
  "provider": "openai",
  "api_key": "sk-...",
  "model": "gpt-4-turbo"
}
```

### Anthropic
```python
{
  "provider": "anthropic", 
  "api_key": "sk-ant-...",
  "model": "claude-3-opus-20240229"
}
```

### OpenRouter
```python
{
  "provider": "openrouter",
  "api_key": "sk-or-...",
  "model": "google/palm-2"
}
```

### Ollama
```python
{
  "provider": "ollama",
  "model": "llama2:13b"
}
```

---

## 3. Sandbox API
### Methods
```python
execute_code(code: str) -> dict
create_tool(code: str, deps: list) -> str
install_packages(packages: list) -> bool
```

### Security Limits
| Resource | Limit |
|----------|-------|
| Memory   | 256MB |
| CPU Time | 30s   |
| Disk     | 500MB |
| Network  | Blocked |

---

## 4. WebSocket API
### Events
**→ user_request**  
```json
{
  "prompt": "string",
  "files": ["base64_data"],
  "context": {}
}
```

**← agent_response**  
```json
{
  "timestamp": "ISO8601",
  "results": [{
    "agent": "string",
    "response": "string",
    "usage": {"tokens": int}
  }]
}
```

**← error**  
```json
{
  "code": "400-599",
  "message": "string",
  "agent": "string"
}
```

---

## 5. Tool Development
### Template
```python
def tool_entrypoint(args: dict) -> dict:
    '''Must return JSON-serializable dict'''
    # Tool logic
    return {"result": ...}
```

### Validation Rules
1. No file system access
2. Max 3 nested function calls
3. No external network requests
4. Execution time < 5s

---

## 6. Error Codes
| Code | Meaning |
|------|---------|
| 401  | Invalid API Key |
| 429  | Rate Limited |
| 500  | Sandbox Error |
| 503  | Model Unavailable |
| 507  | Storage Limit |

---

## 7. Rate Limits
| Provider | RPM* |
|----------|------|
| OpenAI   | 20   |
| Anthropic | 15  |
| OpenRouter | 30 |
| Ollama   | None |

*Requests per minute per agent

