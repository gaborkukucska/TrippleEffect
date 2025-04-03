class SecurityPolicy:
    """Containerization security controls"""
    
    def __init__(self):
        self.default_policy = {
            "network_access": False,
            "max_memory": "256MB",
            "disk_quota": "500MB",
            "allowed_ports": [],
            "read_only": True,
            "allowed_modules": ["math", "datetime", "json"]
        }
        
    def validate_config(self, config: dict):
        """Ensure agent configuration complies with security rules"""
        required_checks = {
            "api_key": self._validate_key_pattern,
            "venv_path": self._validate_path_safety,
            "system_messages": self._validate_content_policy
        }
        
        for field, validator in required_checks.items():
            if field in config:
                validator(config[field])

    def _validate_key_pattern(self, key: str):
        if not re.match(r"^sk-[A-Za-z0-9]{32,}$", key):
            raise SecurityViolation("Invalid API key format")

    def _validate_path_safety(self, path: str):
        if "../" in path:
            raise SecurityViolation("Path traversal attempt detected")

    def _validate_content_policy(self, messages: list):
        prohibited_terms = ["root", "sudo", "install", "rm -rf"]
        for msg in messages:
            if any(term in msg.lower() for term in prohibited_terms):
                raise SecurityViolation("Prohibited content in system message")

class SecurityViolation(Exception):
    """Custom exception for security breaches"""
    def __init__(self, message, severity="high"):
        super().__init__(message)
        self.severity = severity
        self.timestamp = datetime.now().isoformat()
        self.log_violation()

    def log_violation(self):
        with open("/var/log/tripple_sec.log", "a") as f:
            log_entry = f"{self.timestamp} | {self.severity} | {str(self)}\n"
            f.write(log_entry)
