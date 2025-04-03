import os
import re
import uuid
import shutil
import signal
import subprocess
from pathlib import Path
from typing import Dict, Optional
import resource
import psutil
import magic
from .security_layer import SecurityViolation

class VenvManager:
    """Secure virtual environment management system"""
    
    def __init__(self, base_path: Path):
        self.base_path = base_path
        self.tools_dir = base_path / 'tools'
        self.manifest_path = base_path / 'tool_manifest.json'
        self._setup_infrastructure()
        
    def _setup_infrastructure(self):
        """Initialize required directories and files"""
        self.base_path.mkdir(parents=True, exist_ok=True)
        self.tools_dir.mkdir(exist_ok=True)
        
        if not self.manifest_path.exists():
            with open(self.manifest_path, 'w') as f:
                json.dump({"tools": {}}, f)

    def create_venv(self, agent_id: str) -> Path:
        """Create new Python virtual environment"""
        venv_path = self.base_path / f"{agent_id}_venv"
        
        if venv_path.exists():
            shutil.rmtree(venv_path)
            
        result = subprocess.run(
            [sys.executable, "-m", "venv", str(venv_path)],
            capture_output=True,
            text=True
        )
        
        if result.returncode != 0:
            raise EnvironmentError(f"Venv creation failed: {result.stderr}")
            
        self._install_core_packages(venv_path)
        return venv_path

    def _install_core_packages(self, venv_path: Path):
        """Install essential packages in new environment"""
        pip = venv_path / 'bin' / 'pip'
        subprocess.run(
            [str(pip), "install", "--upgrade", "pip", "setuptools", "wheel"],
            check=True
        )

    def execute_in_venv(self, venv_path: Path, code: str, timeout: int = 30) -> Dict:
        """Execute code in sandboxed environment with resource limits"""
        python_exe = venv_path / 'bin' / 'python'
        temp_script = venv_path / 'tmp' / f'temp_{uuid.uuid4()}.py'
        
        try:
            # Security checks
            self._validate_code(code)
            self._check_resources()
            
            # Prepare execution environment
            temp_script.parent.mkdir(exist_ok=True)
            temp_script.write_text(code)
            
            # Set resource limits
            def set_limits():
                resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024, 256 * 1024 * 1024))
                resource.setrlimit(resource.RLIMIT_CPU, (timeout, timeout))
                
            # Execute with limits
            result = subprocess.run(
                [str(python_exe), str(temp_script)],
                capture_output=True,
                text=True,
                preexec_fn=set_limits,
                timeout=timeout
            )
            
            return {
                "exit_code": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "duration": result.time
            }
            
        except subprocess.TimeoutExpired:
            raise TimeoutError("Execution timed out")
        finally:
            if temp_script.exists():
                temp_script.unlink()

    def create_tool(self, tool_code: str, dependencies: List[str] = None) -> str:
        """Register new tool in sandbox environment"""
        tool_id = f"tool_{uuid.uuid4().hex[:8]}"
        tool_path = self.tools_dir / f"{tool_id}.py"
        
        # Validate tool signature
        file_type = magic.from_buffer(tool_code)
        if 'text' not in file_type:
            raise SecurityViolation("Invalid file type for tool")
            
        # Check for prohibited operations
        prohibited_patterns = [
            r"os\.system", r"subprocess\.[^Popen]", r"__import__",
            r"open\(.*[w|a].*\)", r"shutil\."
        ]
        for pattern in prohibited_patterns:
            if re.search(pattern, tool_code):
                raise SecurityViolation(f"Prohibited operation detected: {pattern}")
        
        # Install dependencies
        if dependencies:
            self._install_packages(dependencies)
            
        # Save tool
        tool_path.write_text(tool_code)
        self._update_manifest(tool_id, dependencies)
        
        return tool_id

    def _install_packages(self, packages: List[str]):
        """Install required packages in virtual environment"""
        pip = self.base_path / 'bin' / 'pip'
        subprocess.run(
            [str(pip), "install"] + packages,
            check=True
        )

    def _update_manifest(self, tool_id: str, dependencies: List[str]):
        """Maintain tool registry"""
        with open(self.manifest_path, 'r+') as f:
            manifest = json.load(f)
            manifest['tools'][tool_id] = {
                "dependencies": dependencies,
                "created": datetime.now().isoformat()
            }
            f.seek(0)
            json.dump(manifest, f)

    def _validate_code(self, code: str):
        """Static code analysis for security"""
        # Check for unsafe imports
        unsafe_imports = {'os', 'sys', 'subprocess', 'shutil'}
        parsed = ast.parse(code)
        for node in ast.walk(parsed):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in unsafe_imports:
                        raise SecurityViolation(f"Prohibited import: {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                if node.module in unsafe_imports:
                    raise SecurityViolation(f"Prohibited from-import: {node.module}")

    def _check_resources(self):
        """Enforce system resource limits"""
        process = psutil.Process()
        mem_usage = process.memory_info().rss / 1024 / 1024  # MB
        
        if mem_usage > 256:
            raise ResourceWarning("Memory limit exceeded")
            
        if psutil.cpu_percent() > 90:
            raise ResourceWarning("CPU limit exceeded")

    def clean_venv(self, venv_path: Path):
        """Reset virtual environment to clean state"""
        lib_path = venv_path / 'lib'
        if lib_path.exists():
            shutil.rmtree(lib_path)
        self.create_venv(venv_path.name)
