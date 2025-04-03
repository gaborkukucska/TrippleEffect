import psutil
import resource
from typing import Dict

class ResourceMonitor:
    """System resource monitoring and enforcement"""
    
    def __init__(self):
        self.agent_limits = {
            'cpu_percent': 25,
            'memory_mb': 256,
            'file_descriptors': 100
        }
        
    def check_system_health(self) -> Dict:
        """Get current system resource utilization"""
        return {
            'cpu': psutil.cpu_percent(),
            'memory': psutil.virtual_memory().percent,
            'disk': psutil.disk_usage('/').percent
        }
    
    def enforce_limits(self):
        """Apply resource limits to current process"""
        # Set memory limit
        memory_limit = self.agent_limits['memory_mb'] * 1024 * 1024
        resource.setrlimit(
            resource.RLIMIT_AS, 
            (memory_limit, memory_limit)
            
        # Set file descriptor limit
        resource.setrlimit(
            resource.RLIMIT_NOFILE,
            (self.agent_limits['file_descriptors'], 
             self.agent_limits['file_descriptors'])
        )
