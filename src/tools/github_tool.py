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
    description: str = (
        "Accesses GitHub repositories using the REST API and a Personal Access Token (PAT). "
        "Allows listing accessible repositories ('list_repos'), listing files/directories in a repo path ('list_files'), "
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
            description="The full name of the repository (e.g., 'username/repo-name'). Required for 'list_files' and 'read_file'.",
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
        if not self.token: return None

        url = f"{GITHUB_API_BASE_URL}{endpoint}"
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": "2022-11-28"
        }
        try:
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.request(method, url, params=params, timeout=20) as response:
                    remaining = response.headers.get("X-RateLimit-Remaining")
                    reset_time = response.headers.get("X-RateLimit-Reset")
                    if remaining and reset_time:
                        try: reset_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(int(reset_time)))
                        except: reset_str = reset_time
                        logger.debug(f"GitHub Rate Limit: {remaining} remaining. Resets at {reset_str}")

                    if response.status >= 400:
                         error_message = await response.text() # Get raw text first
                         error_data = {}
                         try:
                             error_data = await response.json() # Try to parse JSON
                             error_message = error_data.get("message", error_message) # Use JSON message if available
                         except (json.JSONDecodeError, aiohttp.ContentTypeError):
                              logger.warning(f"Could not parse GitHub error response as JSON for status {response.status}")
                              # Keep the raw text as error_message

                         logger.error(f"GitHub API Error {response.status} for {method} {endpoint}: {error_message}")
                         if response.status == 404:
                             raise FileNotFoundError(f"GitHub resource not found ({endpoint}): {error_message}")
                         else:
                             raise Exception(f"GitHub API Error {response.status}: {error_message}")

                    # Handle potentially empty successful responses (e.g., 204 No Content)
                    if response.status == 204:
                         return None # Or return an empty dict/list based on context? None is safer.

                    # Attempt to parse JSON for successful responses
                    try:
                         return await response.json()
                    except (json.JSONDecodeError, aiohttp.ContentTypeError):
                         logger.error(f"Could not parse successful GitHub response as JSON for {method} {endpoint} (Status: {response.status})")
                         # Decide what to return: None, empty dict, or raise error?
                         # Returning None might be safest if JSON is expected.
                         return None

        except FileNotFoundError as e:
            raise e
        except aiohttp.ClientError as e:
            logger.error(f"HTTP Client Error making GitHub request to {url}: {e}")
            raise Exception(f"Network error accessing GitHub API: {e}") from e
        except asyncio.TimeoutError:
            logger.error(f"Timeout error making GitHub request to {url}")
            raise Exception("Timeout connecting to GitHub API.") from e
        except Exception as e:
            if not isinstance(e, FileNotFoundError):
                 logger.error(f"Unexpected error during GitHub request to {url}: {e}", exc_info=True)
                 raise Exception(f"Unexpected error during GitHub API request: {type(e).__name__}") from e
            else:
                 raise e


    async def execute(self, agent_id: str, agent_sandbox_path: Path, **kwargs: Any) -> Any:
        """
        Executes the GitHub operation based on the provided action.
        """
        if not self.token:
            return "Error: GitHubTool cannot execute because GITHUB_ACCESS_TOKEN is not configured correctly."

        action = kwargs.get("action")
        repo_full_name = kwargs.get("repo_full_name")
        path = kwargs.get("path", "/")
        ref = kwargs.get("branch_or_ref")

        logger.info(f"Agent {agent_id} attempting GitHub action '{action}' (Repo: {repo_full_name}, Path: {path}, Ref: {ref})")

        if not action or action not in ["list_repos", "list_files", "read_file"]:
            return f"Error: Invalid or missing 'action'. Must be 'list_repos', 'list_files', or 'read_file'."

        try:
            if action == "list_repos":
                repo_data = await self._make_github_request("GET", "/user/repos", params={"type": "all", "per_page": 100})
                if not repo_data or not isinstance(repo_data, list):
                    return "No repositories found or accessible, or error retrieving list."
                repo_names = [repo.get("full_name") for repo in repo_data if repo.get("full_name")]
                if not repo_names:
                    return "No repositories found or accessible with the provided token."
                return f"Accessible Repositories (first {len(repo_names)}):\n- " + "\n- ".join(repo_names)

            if not repo_full_name:
                return f"Error: 'repo_full_name' (e.g., 'username/repo-name') is required for action '{action}'."

            repo_endpoint_base = f"/repos/{repo_full_name}"

            if action == "list_files":
                list_path = path.lstrip('/')
                endpoint = f"{repo_endpoint_base}/contents/{list_path}"
                params = {"ref": ref} if ref else {}
                try:
                    contents_data = await self._make_github_request("GET", endpoint, params=params)
                    if not contents_data:
                        return f"Path '{path}' in repository '{repo_full_name}' (ref: {ref or 'default'}) not found or is empty."
                    if isinstance(contents_data, list):
                        file_list = [f"- {item.get('name')} ({item.get('type')})" for item in sorted(contents_data, key=lambda x: (x.get('type','z'), x.get('name','')))]
                        if not file_list:
                             return f"Directory '{path}' in repository '{repo_full_name}' (ref: {ref or 'default'}) is empty."
                        ref_display = ref or 'default branch'
                        return f"Contents of '{repo_full_name}' at path '{path}' (ref: {ref_display}):\n" + "\n".join(file_list)
                    elif isinstance(contents_data, dict) and contents_data.get("type") == "file":
                        return f"Path '{path}' points to a single file: {contents_data.get('name')}. Use 'read_file' action to get content."
                    else:
                         return f"Unexpected response format when listing path '{path}' in repository '{repo_full_name}'."
                except FileNotFoundError:
                     return f"Error: Path '{path}' not found in repository '{repo_full_name}' (ref: {ref or 'default'})."

            elif action == "read_file":
                if not path or path == '/':
                     return f"Error: 'path' specifying a valid file is required for action 'read_file'."
                read_path = path.lstrip('/')
                endpoint = f"{repo_endpoint_base}/contents/{read_path}"
                params = {"ref": ref} if ref else {}
                try:
                    file_data = await self._make_github_request("GET", endpoint, params=params)
                    if not isinstance(file_data, dict) or file_data.get("type") != "file":
                        type_found = file_data.get("type", "unknown") if isinstance(file_data, dict) else "unexpected format"
                        return f"Error: Path '{path}' in repository '{repo_full_name}' is not a file (type: {type_found})."
                    content_base64 = file_data.get("content")
                    if content_base64 is None:
                        return f"Error: Could not retrieve content for file '{path}' (maybe it's too large or binary?)."
                    try:
                        content_bytes = base64.b64decode(content_base64.replace('\n', ''))
                        decoded_content = content_bytes.decode('utf-8')
                    except (base64.binascii.Error, UnicodeDecodeError) as decode_err:
                         logger.warning(f"Failed to decode content of '{path}' from repo '{repo_full_name}': {decode_err}")
                         return f"Error: Failed to decode file content for '{path}'. It might be binary or corrupted."
                    logger.info(f"Successfully read file '{path}' from repo '{repo_full_name}' for agent {agent_id}.")
                    MAX_FILE_READ_SIZE = 50 * 1024
                    if len(decoded_content) > MAX_FILE_READ_SIZE:
                         logger.warning(f"Read large file '{path}' from GitHub ({len(decoded_content)} bytes), truncating.")
                         return f"Content of '{repo_full_name}/{path}' (ref: {ref or 'default'}) [TRUNCATED]:\n\n{decoded_content[:MAX_FILE_READ_SIZE]}\n\n[... File content truncated due to size limit ...]"
                    else:
                        ref_display = ref or 'default branch'
                        return f"Content of '{repo_full_name}/{path}' (ref: {ref_display}):\n\n{decoded_content}"
                except FileNotFoundError:
                     return f"Error: File path '{path}' not found in repository '{repo_full_name}' (ref: {ref or 'default'})."

        except Exception as e:
            logger.error(f"Unexpected error executing GitHub tool ({action}) for agent {agent_id}: {e}", exc_info=True)
            # Check if the error was FileNotFoundError re-raised from the helper
            if isinstance(e, FileNotFoundError):
                 # Return the specific FileNotFoundError message
                 return str(e)
            else:
                # Return a generic error message for other exceptions
                return f"Error executing GitHub tool ({action}): {type(e).__name__} - {e}"
