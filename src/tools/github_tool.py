# START OF FILE src/tools/github_tool.py
import asyncio
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
import aiohttp
import base64
import json
import time # Added for rate limit logging

from src.tools.base import BaseTool, ToolParameter
from src.config.settings import settings

logger = logging.getLogger(__name__)

GITHUB_API_BASE_URL = "https://api.github.com"

class GitHubTool(BaseTool):
    """
    Interacts with GitHub repositories using the GitHub REST API.
    Can list repositories, list files/directories within a repository, and read file contents.
    Requires a GITHUB_ACCESS_TOKEN with 'repo' scope in the .env file.
    """
    name: str = "github_tool"
    description: str = ( # Modified description
        "Accesses GitHub repositories using the REST API and a Personal Access Token (PAT). "
        "This is the primary tool for interacting with GitHub content. "
        "Allows listing repositories ('list_repos' for a user or the authenticated user), listing files/directories in a repo path ('list_files'), "
        "and reading the content of a specific file ('read_file'). "
        "Requires GITHUB_ACCESS_TOKEN with 'repo' scope to be set in the environment."
    )
    parameters: List[ToolParameter] = [
        ToolParameter(
            name="action",
            type="string",
            description="The operation to perform: 'list_repos', 'list_files', or 'read_file'.",
            required=True,
        ),
        ToolParameter(
            name="repo_full_name",
            type="string",
            # Modified description for clarity
            description="The full name of the target repository (e.g., 'username/repo-name'). Required for 'list_files' and 'read_file'. For 'list_repos', if provided, it specifies the user whose public repos to list (e.g., 'username'). If omitted for 'list_repos', lists repos accessible by the token.",
            required=False,
        ),
        ToolParameter(
            name="path",
            type="string",
            description="The path to a directory or file within the repository. Defaults to the root ('/'). Required for 'read_file', optional for 'list_files'.",
            required=False,
        ),
         ToolParameter(
            name="branch_or_ref",
            type="string",
            description="The branch, tag, or commit SHA to use. Defaults to the repository's default branch (if omitted). Optional for 'list_files' and 'read_file'.",
            required=False,
        ),
    ]

    def __init__(self):
        """Initializes the tool and checks for the GitHub token."""
        self.token = settings.GITHUB_ACCESS_TOKEN
        if not self.token:
            logger.warning("GitHubTool initialized, but GITHUB_ACCESS_TOKEN is not set in the environment. Tool execution will fail.")
        else:
            logger.info("GitHubTool initialized with access token.")


    async def _make_github_request(self, method: str, endpoint: str, params: Optional[Dict] = None) -> Optional[Any]:
        """ Helper function to make authenticated requests to the GitHub API. """
        if not self.token: return None # Added early exit if no token

        url = f"{GITHUB_API_BASE_URL}{endpoint}"
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": "2022-11-28"
        }
        logger.debug(f"Making GitHub request: {method} {url} Params: {params}") # Log request details
        try:
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.request(method, url, params=params, timeout=20) as response:
                    remaining = response.headers.get("X-RateLimit-Remaining")
                    reset_time = response.headers.get("X-RateLimit-Reset")
                    if remaining and reset_time:
                        try: reset_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(int(reset_time)))
                        except: reset_str = reset_time
                        logger.debug(f"GitHub Rate Limit: {remaining} remaining. Resets at {reset_str}")

                    # Check status code BEFORE attempting to read body
                    if response.status >= 400:
                         error_message = f"GitHub API Error {response.status}"
                         error_details = ""
                         try:
                             # Attempt to read details, but don't fail if it doesn't work
                             error_body = await response.text()
                             try:
                                 error_data = json.loads(error_body)
                                 error_details = error_data.get("message", error_body)
                             except json.JSONDecodeError:
                                 error_details = error_body[:500] # Show start of raw body on JSON error
                             error_message += f": {error_details}"
                         except Exception as read_err:
                             logger.warning(f"Could not read error body for status {response.status}: {read_err}")

                         logger.error(f"GitHub API Error: Status {response.status} for {method} {endpoint}. Message: {error_details}")
                         if response.status == 404:
                             raise FileNotFoundError(f"GitHub resource not found ({method} {endpoint}): {error_details}")
                         elif response.status == 403: # Forbidden, often rate limit or permissions
                              raise PermissionError(f"GitHub API Forbidden (403) for {method} {endpoint}. Check token permissions or rate limits. Message: {error_details}")
                         else:
                             raise Exception(f"GitHub API Error {response.status} for {method} {endpoint}: {error_details}")

                    # Handle potentially empty successful responses (e.g., 204 No Content)
                    if response.status == 204:
                         return None

                    # Attempt to parse JSON for successful responses
                    try:
                         return await response.json()
                    except (json.JSONDecodeError, aiohttp.ContentTypeError) as json_err:
                         logger.error(f"Could not parse successful GitHub response as JSON for {method} {endpoint} (Status: {response.status}): {json_err}")
                         # Read raw text as fallback? Or return None/Error?
                         raw_text = await response.text()
                         logger.warning(f"Raw response text (first 500 chars): {raw_text[:500]}")
                         # Decide what to return: Raising an error might be better if JSON is expected
                         raise ValueError(f"Expected JSON response but received non-JSON content (Status: {response.status})")

        except FileNotFoundError as e: raise e # Re-raise specific errors
        except PermissionError as e: raise e # Re-raise specific errors
        except aiohttp.ClientError as e:
            logger.error(f"HTTP Client Error making GitHub request to {url}: {e}")
            raise Exception(f"Network error accessing GitHub API: {e}") from e
        except asyncio.TimeoutError:
            logger.error(f"Timeout error making GitHub request to {url}")
            raise Exception("Timeout connecting to GitHub API.") from e
        except Exception as e: # Catch other unexpected errors
             if not isinstance(e, (FileNotFoundError, PermissionError, ValueError)): # Don't re-wrap known errors
                 logger.error(f"Unexpected error during GitHub request to {url}: {e}", exc_info=True)
                 raise Exception(f"Unexpected error during GitHub API request: {type(e).__name__}") from e
             else:
                 raise e # Re-raise known/expected errors

    # --- MODIFIED execute method ---
    async def execute(
        self,
        agent_id: str,
        agent_sandbox_path: Path,
        project_name: Optional[str] = None, # Context args now expected by BaseTool
        session_name: Optional[str] = None,
        **kwargs: Any
        ) -> Any:
        """
        Executes the GitHub operation based on the provided action.
        Uses correct endpoints for listing user repos vs. authenticated user repos.
        """
        if not self.token:
            return "Error: GitHubTool cannot execute because GITHUB_ACCESS_TOKEN is not configured correctly."

        action = kwargs.get("action")
        # repo_full_name can now mean 'username/repo' OR just 'username' for list_repos
        repo_full_name_or_username = kwargs.get("repo_full_name")
        path = kwargs.get("path", "/")
        ref = kwargs.get("branch_or_ref")

        logger.info(f"Agent {agent_id} attempting GitHub action '{action}' (Target: {repo_full_name_or_username}, Path: {path}, Ref: {ref})")

        if not action or action not in ["list_repos", "list_files", "read_file"]:
            return f"Error: Invalid or missing 'action'. Must be 'list_repos', 'list_files', or 'read_file'."

        try:
            if action == "list_repos":
                endpoint = "/user/repos" # Default: lists repos accessible by the token
                list_target_user = None
                # Check if repo_full_name_or_username looks like just a username
                if repo_full_name_or_username and '/' not in repo_full_name_or_username:
                    list_target_user = repo_full_name_or_username
                    endpoint = f"/users/{list_target_user}/repos" # Switch endpoint for specific user
                    logger.info(f"Listing public repos for user: {list_target_user}")
                elif repo_full_name_or_username:
                     # If it contains '/', assume it's intended for user but might be mistyped
                     parts = repo_full_name_or_username.split('/', 1)
                     list_target_user = parts[0]
                     endpoint = f"/users/{list_target_user}/repos"
                     logger.warning(f"Parameter 'repo_full_name' contained '/', interpreting '{list_target_user}' as the target user for listing public repos.")
                else:
                    logger.info("Listing repositories accessible by the authenticated user token.")

                # Set common parameters for listing repos
                params = {"type": "all", "per_page": 100} # Get up to 100

                repo_data = await self._make_github_request("GET", endpoint, params=params)

                if not repo_data or not isinstance(repo_data, list):
                    target_desc = f"user '{list_target_user}'" if list_target_user else "authenticated user"
                    return f"No repositories found or accessible for {target_desc}, or error retrieving list."

                repo_details = []
                for repo in repo_data:
                     full_name = repo.get("full_name")
                     description = repo.get("description") or "No description." # Add description
                     if full_name:
                         repo_details.append(f"*   **[{full_name}](https://github.com/{full_name}):** {description}")

                if not repo_details:
                    target_desc = f"user '{list_target_user}'" if list_target_user else "authenticated user"
                    return f"No repositories found or accessible for {target_desc} with the provided token."

                target_desc_title = f" for {list_target_user}" if list_target_user else " Accessible by Token"
                return f"Repositories{target_desc_title} (first {len(repo_details)}):\n\n" + "\n".join(repo_details)

            # --- Actions requiring repo_full_name ('username/repo') ---
            if not repo_full_name_or_username or '/' not in repo_full_name_or_username:
                return f"Error: 'repo_full_name' in the format 'username/repo-name' is required for action '{action}'."

            repo_endpoint_base = f"/repos/{repo_full_name_or_username}" # Use the full name

            if action == "list_files":
                list_path = path.lstrip('/') # Ensure path doesn't start with / for endpoint
                endpoint = f"{repo_endpoint_base}/contents/{list_path}"
                params = {"ref": ref} if ref else {}
                try:
                    contents_data = await self._make_github_request("GET", endpoint, params=params)
                    if not contents_data:
                        return f"Path '{path}' in repository '{repo_full_name_or_username}' (ref: {ref or 'default'}) not found or is empty."
                    if isinstance(contents_data, list): # It's a directory listing
                        # Sort by type (dirs first) then name
                        items = sorted(contents_data, key=lambda x: (x.get('type', 'z'), x.get('name', '')))
                        file_list = [f"- {item.get('name')} ({item.get('type')})" for item in items]
                        if not file_list:
                             return f"Directory '{path}' in repository '{repo_full_name_or_username}' (ref: {ref or 'default'}) is empty."
                        ref_display = ref or 'default branch'
                        return f"Contents of '{repo_full_name_or_username}' at path '{path}' (ref: {ref_display}):\n" + "\n".join(file_list)
                    elif isinstance(contents_data, dict) and contents_data.get("type") == "file": # It's a single file
                        return f"Path '{path}' points to a single file: {contents_data.get('name')}. Use 'read_file' action to get content."
                    else: # Unexpected format
                         logger.warning(f"Unexpected response format when listing {endpoint}: {contents_data}")
                         return f"Unexpected response format when listing path '{path}' in repository '{repo_full_name_or_username}'."
                except FileNotFoundError:
                     return f"Error: Path '{path}' not found in repository '{repo_full_name_or_username}' (ref: {ref or 'default'})."

            elif action == "read_file":
                if not path or path == '/':
                     return f"Error: A specific file 'path' is required for action 'read_file'."
                read_path = path.lstrip('/')
                endpoint = f"{repo_endpoint_base}/contents/{read_path}"
                params = {"ref": ref} if ref else {}
                try:
                    file_data = await self._make_github_request("GET", endpoint, params=params)
                    if not isinstance(file_data, dict) or file_data.get("type") != "file":
                        type_found = file_data.get("type", "unknown") if isinstance(file_data, dict) else "unexpected format"
                        return f"Error: Path '{path}' in repository '{repo_full_name_or_username}' is not a file (type: {type_found})."

                    content_base64 = file_data.get("content")
                    file_size = file_data.get("size", 0)

                    if content_base64 is None:
                        download_url = file_data.get("download_url")
                        if download_url:
                             # Attempt to download if content is missing (e.g., large file)
                             logger.warning(f"File content missing for '{path}', attempting download from {download_url} (Size: {file_size} bytes)")
                             # Simple download attempt, might need more robust handling for huge files
                             MAX_DOWNLOAD_SIZE = 1 * 1024 * 1024 # 1 MB limit for direct download
                             if file_size > MAX_DOWNLOAD_SIZE:
                                 return f"Error: File '{path}' is too large ({file_size} bytes) to download directly."
                             async with aiohttp.ClientSession() as dl_session: # No auth needed for download_url
                                 async with dl_session.get(download_url, timeout=30) as dl_response:
                                     if dl_response.status == 200:
                                          content_bytes = await dl_response.read()
                                          # Fall through to decode logic
                                     else:
                                          return f"Error: Could not retrieve content for file '{path}'. Download failed (Status: {dl_response.status})."
                        else:
                            return f"Error: Could not retrieve content for file '{path}'. No content or download URL found."
                    else:
                        # Decode base64 content
                         content_bytes = base64.b64decode(content_base64.replace('\n', ''))

                    # Decode bytes to string
                    try:
                        decoded_content = content_bytes.decode('utf-8')
                    except UnicodeDecodeError:
                         logger.warning(f"Failed to decode UTF-8 content of '{path}' from repo '{repo_full_name_or_username}'. Assuming binary.")
                         return f"Note: Content of '{path}' could not be decoded as UTF-8 text. It might be a binary file."

                    logger.info(f"Successfully read file '{path}' from repo '{repo_full_name_or_username}' for agent {agent_id}.")

                    # Truncate large files
                    MAX_FILE_READ_CHARS = 50000 # Limit characters instead of bytes
                    if len(decoded_content) > MAX_FILE_READ_CHARS:
                         logger.warning(f"Read large file '{path}' from GitHub ({len(decoded_content)} chars), truncating.")
                         return f"Content of '{repo_full_name_or_username}/{path}' (ref: {ref or 'default'}) [TRUNCATED]:\n\n{decoded_content[:MAX_FILE_READ_CHARS]}\n\n[... File content truncated due to size limit ({MAX_FILE_READ_CHARS} chars) ...]"
                    else:
                        ref_display = ref or 'default branch'
                        return f"Content of '{repo_full_name_or_username}/{path}' (ref: {ref_display}):\n\n{decoded_content}"
                except FileNotFoundError:
                     return f"Error: File path '{path}' not found in repository '{repo_full_name_or_username}' (ref: {ref or 'default'})."

        except (FileNotFoundError, PermissionError) as known_err:
            logger.warning(f"GitHub tool execution failed for agent {agent_id}: {known_err}")
            return str(known_err) # Return the specific error message
        except Exception as e:
            logger.error(f"Unexpected error executing GitHub tool ({action}) for agent {agent_id}: {e}", exc_info=True)
            return f"Error executing GitHub tool ({action}): {type(e).__name__} - {e}"
