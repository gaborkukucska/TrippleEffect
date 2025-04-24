# START OF FILE src/utils/network_utils.py
import asyncio
import socket
import ipaddress
import netifaces
import logging
from typing import List, Dict, Any, Optional, Set, Tuple

logger = logging.getLogger(__name__)

async def _check_port(ip: str, port: int, timeout: float) -> Optional[str]:
    """Attempts to open a connection to a specific IP and port."""
    try:
        # Use asyncio.open_connection with a timeout
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(ip, port),
            timeout=timeout
        )
        writer.close()
        await writer.wait_closed()
        logger.debug(f"Successfully connected to {ip}:{port}")
        return f"http://{ip}:{port}"
    except asyncio.TimeoutError:
        # This is expected for non-listening ports, debug level
        logger.debug(f"Timeout connecting to {ip}:{port} within {timeout}s")
        return None
    except OSError as e:
        # Other connection errors (e.g., connection refused, host unreachable)
        logger.debug(f"OS Error connecting to {ip}:{port}: {e}")
        return None
    except Exception as e:
        # Catch any other unexpected errors
        logger.warning(f"Unexpected error checking port {ip}:{port}: {e}", exc_info=True)
        return None

def _get_local_subnets() -> Set[ipaddress.IPv4Network]:
    """Gets local IPv4 subnets using netifaces."""
    subnets: Set[ipaddress.IPv4Network] = set()
    try:
        for interface in netifaces.interfaces():
            addresses = netifaces.ifaddresses(interface)
            if netifaces.AF_INET in addresses:
                for addr_info in addresses[netifaces.AF_INET]:
                    ip = addr_info.get('addr')
                    netmask = addr_info.get('netmask')
                    if ip and netmask:
                        try:
                            # Create network object, strict=False allows host bits set
                            network = ipaddress.IPv4Network(f"{ip}/{netmask}", strict=False)
                            # Exclude loopback and link-local
                            if not network.is_loopback and not network.is_link_local:
                                subnets.add(network)
                        except ValueError as e:
                            logger.warning(f"Could not parse network for interface {interface} ({ip}/{netmask}): {e}")
    except Exception as e:
        logger.error(f"Error getting local subnets using netifaces: {e}", exc_info=True)
    if not subnets:
        logger.warning("Could not automatically determine local subnets. Falling back to checking localhost only.")
        # Fallback to localhost if auto-detection fails
        subnets.add(ipaddress.IPv4Network("127.0.0.1/32", strict=False))
    return subnets

async def scan_for_local_apis(ports: List[int], subnet_config: str, timeout: float) -> List[str]:
    """
    Scans the local network for potential OpenAI-compatible APIs.

    Args:
        ports: List of ports to scan.
        subnet_config: How to determine IPs to scan ("auto", CIDR string, or comma-separated IPs).
        timeout: Connection timeout in seconds for each port check.

    Returns:
        List of potential base URLs (e.g., "http://192.168.1.10:11434").
    """
    target_ips: Set[str] = set()

    if subnet_config.lower() == "auto":
        logger.info(f"Scanning local network (auto-detected subnets) on ports {ports}...")
        local_subnets = await asyncio.to_thread(_get_local_subnets)
        for subnet in local_subnets:
            # Add all hosts in the subnet (excluding network/broadcast if possible, though iteration handles it)
            try:
                for ip in subnet.hosts():
                    target_ips.add(str(ip))
            except Exception as e:
                 logger.warning(f"Error iterating hosts for subnet {subnet}: {e}")
        # Always include localhost explicitly
        target_ips.add("127.0.0.1")
        logger.info(f"Auto-detected {len(target_ips)} IPs to scan across {len(local_subnets)} subnets.")
    elif '/' in subnet_config: # CIDR notation
        try:
            network = ipaddress.ip_network(subnet_config, strict=False)
            logger.info(f"Scanning configured subnet {network} on ports {ports}...")
            for ip in network.hosts():
                target_ips.add(str(ip))
            # Add network address itself if relevant (e.g., /32)
            target_ips.add(str(network.network_address))
        except ValueError as e:
            logger.error(f"Invalid CIDR subnet configuration '{subnet_config}': {e}. Scanning localhost only.")
            target_ips.add("127.0.0.1")
    else: # Comma-separated IPs or single IP
        ips_to_check = [ip.strip() for ip in subnet_config.split(',') if ip.strip()]
        logger.info(f"Scanning configured IPs {ips_to_check} on ports {ports}...")
        valid_ips = set()
        for ip_str in ips_to_check:
            try:
                ipaddress.ip_address(ip_str) # Validate IP format
                valid_ips.add(ip_str)
            except ValueError:
                logger.warning(f"Invalid IP address format in configuration: '{ip_str}'. Skipping.")
        target_ips = valid_ips
        if not target_ips:
             logger.error("No valid IPs found in manual configuration. Scanning localhost only.")
             target_ips.add("127.0.0.1")

    if not target_ips:
        logger.warning("No target IPs determined for scanning.")
        return []

    logger.info(f"Starting scan of {len(target_ips)} IPs on {len(ports)} ports (Timeout: {timeout}s)...")
    tasks = []
    for ip in target_ips:
        for port in ports:
            tasks.append(_check_port(ip, port, timeout))

    results = await asyncio.gather(*tasks, return_exceptions=True)

    candidate_urls: List[str] = []
    for result in results:
        if isinstance(result, str): # Successful connection returns the URL string
            candidate_urls.append(result)
        elif isinstance(result, Exception):
            # Log exceptions that weren't handled within _check_port (should be rare)
            logger.error(f"Unexpected exception during port scan task: {result}", exc_info=result)

    logger.info(f"Local API scan finished. Found {len(candidate_urls)} potential endpoints.")
    return candidate_urls

# END OF FILE src/utils/network_utils.py
