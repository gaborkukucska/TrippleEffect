import magic
from pathlib import Path
from config.global_settings import Settings

class FileProcessor:
    """Secure file upload and analysis system"""
    
    def __init__(self):
        self.allowed_types = Settings.ALLOWED_FILE_TYPES
        self.max_size = Settings.MAX_FILE_SIZE
        self.mime = magic.Magic(mime=True)

    def validate_file(self, file_path: Path) -> dict:
        """Perform comprehensive file validation"""
        file_info = {
            "valid": False,
            "type": None,
            "size": file_path.stat().st_size,
            "analysis": {}
        }

        if file_info['size'] > self.max_size:
            raise ValueError(f"File exceeds {self.max_size} byte limit")

        file_info['type'] = self.mime.from_buffer(file_path.read_bytes())
        
        if file_info['type'] not in self.allowed_types:
            raise ValueError(f"Unsupported file type: {file_info['type']}")

        file_info['analysis'] = self._analyze_content(file_path)
        file_info['valid'] = True
        return file_info

    def _analyze_content(self, file_path: Path) -> dict:
        """Deep content analysis for security"""
        content = file_path.read_text()
        return {
            "line_count": len(content.splitlines()),
            "suspicious_patterns": self._detect_patterns(content),
            "language": self._detect_language(content)
        }

    def _detect_patterns(self, content: str) -> list:
        """Look for potential security issues"""
        patterns = {
            'hex_encoded': re.compile(r'(\\x[0-9a-f]{2})+'),
            'executable': re.compile(r'^#!/'),
            'dangerous_keywords': re.compile(r'(system|exec|eval|shutil\.rmtree)')
        }
        return [
            name for name, pattern in patterns.items()
            if pattern.search(content)
        ]

    def _detect_language(self, content: str) -> str:
        """Simple programming language detection"""
        keywords = {
            'python': ['def ', 'import ', 'class '],
            'javascript': ['function ', 'const ', 'let '],
            'bash': ['#!/bin/bash', 'echo ', 'rm ']
        }
        for lang, markers in keywords.items():
            if any(m in content for m in markers):
                return lang
        return 'text'
