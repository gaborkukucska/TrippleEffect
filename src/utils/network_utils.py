# START OF FILE src/utils/network_utils.py
import asyncio
import socket
import ipaddress
import netifaces
import logging
from typing import List, Dict, Any, Optional, Set, Tuple
import nmap # Import python-nmap

logger = logging.getLogger(__name__)

async def _check_port(ip: str, port: int, timeout: float) -> Optional[str]:
    """Attempts to open a connection to a specific IP and port. (Used by ModelRegistry for localhost check)"""
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

def _get_local_network_cidr() -> Optional[str]:
    """Gets the CIDR notation for the primary local IPv4 network using netifaces."""
    logger.debug("Attempting to determine local network CIDR using netifaces...")
    try:
        for interface in netifaces.interfaces():
            addresses = netifaces.ifaddresses(interface)
            if netifaces.AF_INET in addresses:
                for addr_info in addresses[netifaces.AF_INET]:
                    ip = addr_info.get('addr')
                    netmask = addr_info.get('netmask')
                    # Ensure we have both IP and netmask, and it's not loopback
                    if ip and netmask and not ip.startswith("127."):
                        try:
                            network = ipaddress.IPv4Network(f"{ip}/{netmask}", strict=False)
                            # Exclude link-local addresses (e.g., 169.254.x.x)
                            if not network.is_link_local:
                                cidr = str(network)
                                logger.info(f"Determined local network CIDR: {cidr} from interface {interface} ({ip}/{netmask})")
                                return cidr
                        except ValueError as parse_err:
                            logger.debug(f"Ignoring address {ip}/{netmask} on interface {interface} due to parsing error: {parse_err}")
                            continue # Ignore parsing errors for this address
        logger.warning("Could not automatically determine a non-loopback, non-link-local network CIDR using netifaces.")
        return None
    except ImportError:
         logger.error("The 'netifaces' library is required for network detection but is not installed. Please install it (`pip install netifaces`). Cannot perform network scan.")
         return None
    except Exception as e:
        logger.error(f"Error getting local network CIDR using netifaces: {e}", exc_info=True)
        return None

async def scan_for_local_apis(ports: List[int], timeout: float) -> List[str]:
    """
    Scans the automatically detected local network using nmap to find devices
    with specified open TCP ports.

    Requires 'nmap' command-line tool to be installed and accessible in PATH,
    and the 'python-nmap' library to be installed.

    Args:
        ports: List of ports to scan for (e.g., [11434, 8000]).
        timeout: Base timeout value in seconds (used to calculate nmap --host-timeout).

    Returns:
        List of potential base URLs (e.g., "http://192.168.1.10:11434").
    """
    candidate_urls: List[str] = []
    ports_str = ",".join(map(str, ports))
    if not ports_str:
        logger.warning("No ports specified for nmap scan.")
        return []

    # 1. Determine Network Range using netifaces
    # Run _get_local_network_cidr in a thread as netifaces can block
    network_range = await asyncio.to_thread(_get_local_network_cidr)
    if not network_range:
        logger.error("Cannot perform nmap scan: Failed to determine local network range via netifaces.")
        return []

    logger.info(f"Starting nmap scan for ports {ports_str} on automatically detected network {network_range}...")

    try:
        # 2. Run nmap scan in a separate thread
        def run_scan():
            nm = None
            try:
                nm = nmap.PortScanner()
            except nmap.PortScannerError as init_err:
                 logger.error(f"Failed to initialize nmap.PortScanner: {init_err}. Is nmap installed and in PATH?")
                 return None # Indicate failure

            # Construct nmap arguments
            timeout_ms = int(timeout * 1000) # Convert seconds to ms for nmap
            nmap_args = f'-p T:{ports_str} --open -T4 -n --host-timeout {timeout_ms}ms'
            logger.debug(f"Executing nmap command: nmap {nmap_args} {network_range}")
            try:
                scan_result = nm.scan(hosts=network_range, arguments=nmap_args)
                logger.debug(f"Nmap raw scan result: {scan_result}") # Log raw result for debugging
                return nm # Return the scanner object with results
            except Exception as scan_exec_err:
                 logger.error(f"Exception during nm.scan(): {scan_exec_err}", exc_info=True)
                 return None # Indicate failure

        scanner = await asyncio.to_thread(run_scan)

        if scanner is None:
             logger.error("Nmap scan execution failed or scanner initialization failed.")
             return []

        # 3. Parse Results
        scanned_hosts = scanner.all_hosts() # Returns list of IP strings
        logger.info(f"Nmap scan completed. Found {len(scanned_hosts)} hosts with potentially open ports: {scanned_hosts}")

        # --- CORRECTED PARSING LOOP (Ensure host_ip string is used) ---
        for host_ip in scanned_hosts: # Iterate through the list of IP strings
            # Skip localhost, it's handled separately by ModelRegistry's direct check
            if host_ip == '127.0.0.1':
                logger.debug(f"Skipping localhost ({host_ip}) found by nmap scan.")
                continue

            logger.debug(f"Checking nmap results for host: {host_ip} (Type: {type(host_ip)})")
            # --- MODIFIED: Access underlying dictionary directly ---
            host_data = scanner._scan_result.get('scan', {}).get(host_ip)
            if host_data and 'tcp' in host_data:
            # --- END MODIFICATION ---
                for port in ports:
                    port_info = host_data['tcp'].get(port) # Use host_data
                    # Check if port exists in results and its state is 'open'
                    if port_info and port_info.get('state') == 'open':
                        url = f"http://{host_ip}:{port}"
                        candidate_urls.append(url)
                        logger.info(f"Found open port {port} on host {host_ip}. Added URL: {url}")
                    elif port_info:
                         logger.debug(f"Port {port} on host {host_ip} is not open (state: {port_info.get('state')}).")
                    else:
                         logger.debug(f"Port {port} not found in nmap results for host {host_ip}.")
            else:
                 logger.debug(f"No TCP port information found for host {host_ip} in nmap results (host_data: {host_data is not None}).")
        # --- END CORRECTED PARSING LOOP ---

    except FileNotFoundError:
         logger.error("Nmap scan failed: 'nmap' command not found. Please ensure nmap is installed and in your system's PATH.")
    except ImportError:
         logger.error("Nmap scan failed: 'python-nmap' library not found. Please install it (`pip install python-nmap`).")
    except Exception as e:
        logger.error(f"Error during nmap scan or processing: {e}", exc_info=True)

    logger.info(f"Nmap-based local API scan finished. Found {len(candidate_urls)} potential non-localhost endpoints.")
    return candidate_urls

# END OF FILE src/utils/network_utils.py
