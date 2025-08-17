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
    Can list repositories, list files/directories within a repository (optionally recursively),
    and read file contents.
    Requires a GITHUB_ACCESS_TOKEN with 'repo' scope in the .env file.
    """
    name: str = "github_tool"
    auth_level: str = "worker" # Accessible by all
    summary: Optional[str] = "Lists repos/files or reads file content from GitHub."
    description: str = ( # Modified description
        "Accesses GitHub repositories using the REST API and a Personal Access Token (PAT). "
        "Allows listing repositories ('list_repos' for a user or the authenticated user), "
        "listing files/directories in a repo path ('list_files', supports recursive listing), "
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
            description="The full name of the target repository (e.g., 'username/repo-name'). Required for 'list_files' and 'read_file'. For 'list_repos', if provided, specifies the user whose public repos to list (e.g., 'username'). If omitted for 'list_repos', lists repos accessible by the token.",
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
        # --- NEW Parameter for recursive list ---
        ToolParameter(
            name="recursive",
            type="boolean",
            description="For 'list_files' action only: If true, lists all files/directories recursively under the specified path. Defaults to false.",
            required=False, # Defaults to False
        ),
        # --- End NEW Parameter ---
    ]

    def __init__(self):
        """Initializes the tool and checks for the GitHub token."""
        self.token = settings.GITHUB_ACCESS_TOKEN
        if not self.token:
            logger.warning("GitHubTool initialized, but GITHUB_ACCESS_TOKEN is not set in the environment. Tool execution will fail.")
        else:
            logger.info("GitHubTool initialized with access token.")

    # --- Detailed Usage Method ---
    def get_detailed_usage(self) -> str:
        """Returns detailed usage instructions for the GitHubTool."""
        usage = """
        **Tool Name:** github_tool

        **Description:** Accesses GitHub repositories using the REST API and a Personal Access Token (PAT) configured in the environment (GITHUB_ACCESS_TOKEN).

        **Actions & Parameters:**

        1.  **list_repos:** Lists repositories.
            *   `<repo_full_name>` (string, optional): If provided as just a username (e.g., 'octocat'), lists that user's public repositories. If omitted, lists repositories accessible by the authenticated user's token.
            *   Example (List authenticated user's repos): `<github_tool><action>list_repos</action></github_tool>`
            *   Example (List public repos of 'octocat'): `<github_tool><action>list_repos</action><repo_full_name>octocat</repo_full_name></github_tool>`

        2.  **list_files:** Lists files and directories within a repository path.
            *   `<repo_full_name>` (string, required): Full repository name (e.g., 'username/repo-name').
            *   `<path>` (string, optional): Path to list within the repo. Defaults to the root ('/') if omitted.
            *   `<branch_or_ref>` (string, optional): Branch, tag, or commit SHA. Defaults to the repo's default branch.
            *   `<recursive>` (boolean, optional): Set to 'true' to list recursively. Defaults to 'false'.
            *   Example (List root of main branch): `<github_tool><action>list_files</action><repo_full_name>octocat/Spoon-Knife</repo_full_name></github_tool>`
            *   Example (List 'src' dir recursively on 'dev' branch): `<github_tool><action>list_files</action><repo_full_name>my_user/my_repo</repo_full_name><path>src</path><branch_or_ref>dev</branch_or_ref><recursive>true</recursive></github_tool>`

        3.  **read_file:** Reads the content of a specific file in a repository.
            *   `<repo_full_name>` (string, required): Full repository name (e.g., 'username/repo-name').
            *   `<path>` (string, required): Path to the file within the repo (e.g., 'README.md', 'src/main.py'). Cannot be '/'.
            *   `<branch_or_ref>` (string, optional): Branch, tag, or commit SHA. Defaults to the repo's default branch.
            *   Example: `<github_tool><action>read_file</action><repo_full_name>octocat/Spoon-Knife</repo_full_name><path>README.md</path></github_tool>`

        **Important Notes:**
        *   Requires a `GITHUB_ACCESS_TOKEN` with 'repo' scope set in the environment.
        *   Subject to GitHub API rate limits.
        """
        return usage.strip()

    async def _make_github_request(self, method: str, endpoint: str, params: Optional[Dict] = None) -> Optional[Any]:
        """ Helper function to make authenticated requests to the GitHub API. """
        if not self.token: return None

        url = f"{GITHUB_API_BASE_URL}{endpoint}"
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": "2022-11-28"
        }
        logger.debug(f"Making GitHub request: {method} {url} Params: {params}")
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
                         error_message = f"GitHub API Error {response.status}"
                         error_details = ""
                         try:
                             error_body = await response.text()
                             try:
                                 error_data = json.loads(error_body)
                                 error_details = error_data.get("message", error_body)
                             except json.JSONDecodeError: error_details = error_body[:500]
                             error_message += f": {error_details}"
                         except Exception as read_err: logger.warning(f"Could not read error body for status {response.status}: {read_err}")

                         logger.error(f"GitHub API Error: Status {response.status} for {method} {endpoint}. Message: {error_details}")
                         if response.status == 404: raise FileNotFoundError(f"GitHub resource not found ({method} {endpoint}): {error_details}")
                         elif response.status == 403: raise PermissionError(f"GitHub API Forbidden (403) for {method} {endpoint}. Check token permissions or rate limits. Message: {error_details}")
                         else: raise Exception(f"GitHub API Error {response.status} for {method} {endpoint}: {error_details}")

                    if response.status == 204: return None
                    try: return await response.json()
                    except (json.JSONDecodeError, aiohttp.ContentTypeError) as json_err:
                         logger.error(f"Could not parse successful GitHub response as JSON for {method} {endpoint} (Status: {response.status}): {json_err}")
                         raw_text = await response.text()
                         logger.warning(f"Raw response text (first 500 chars): {raw_text[:500]}")
                         raise ValueError(f"Expected JSON response but received non-JSON content (Status: {response.status})")

        except FileNotFoundError as e: raise e
        except PermissionError as e: raise e
        except aiohttp.ClientError as e: logger.error(f"HTTP Client Error making GitHub request to {url}: {e}"); raise Exception(f"Network error accessing GitHub API: {e}") from e
        except asyncio.TimeoutError: logger.error(f"Timeout error making GitHub request to {url}"); raise Exception("Timeout connecting to GitHub API.") from e
        except Exception as e:
             if not isinstance(e, (FileNotFoundError, PermissionError, ValueError)):
                 logger.error(f"Unexpected error during GitHub request to {url}: {e}", exc_info=True)
                 raise Exception(f"Unexpected error during GitHub API request: {type(e).__name__}") from e
             else: raise e

    async def _list_repo_recursively(self, repo_full_name: str, current_path: str, ref: Optional[str]) -> List[Dict[str, str]]:
        """Recursively lists files and directories starting from current_path."""
        all_items = []
        repo_endpoint_base = f"/repos/{repo_full_name}"
        endpoint = f"{repo_endpoint_base}/contents/{current_path.lstrip('/')}"
        params = {"ref": ref} if ref else {}
        logger.debug(f"Recursive list: Fetching {endpoint} (Ref: {ref or 'default'})")

        try:
            contents = await self._make_github_request("GET", endpoint, params=params)

            if not contents:
                logger.warning(f"Recursive list: Path '{current_path}' not found or empty in repo '{repo_full_name}'.")
                return []

            if isinstance(contents, list):
                tasks = []
                for item in contents:
                    item_name = item.get("name")
                    item_type = item.get("type")
                    item_path = item.get("path")

                    if not item_name or not item_type or not item_path: continue

                    all_items.append({"path": item_path, "type": item_type})

                    if item_type == "dir":
                        tasks.append(asyncio.create_task(
                            self._list_repo_recursively(repo_full_name, item_path, ref)
                        ))

                if tasks:
                    results = await asyncio.gather(*tasks, return_exceptions=True)
                    for res in results:
                        if isinstance(res, list):
                            all_items.extend(res)
                        elif isinstance(res, Exception):
                            logger.error(f"Recursive list error during gather for path '{current_path}': {res}")
            elif isinstance(contents, dict) and contents.get("type") == "file":
                 all_items.append({"path": contents.get("path"), "type": "file"})
            else:
                 logger.warning(f"Recursive list: Unexpected content type for {endpoint}: {type(contents)}")

        except FileNotFoundError:
            logger.warning(f"Recursive list: Path '{current_path}' not found in repo '{repo_full_name}' (ref: {ref or 'default'}).")
        except Exception as e:
            logger.error(f"Recursive list: Error processing path '{current_path}' in repo '{repo_full_name}': {e}", exc_info=True)

        return all_items

    async def execute(
        self,
        agent_id: str,
        agent_sandbox_path: Path,
        project_name: Optional[str] = None,
        session_name: Optional[str] = None,
        **kwargs: Any
        ) -> Dict[str, Any]:
        if not self.token:
            return {"status": "error", "message": "GitHubTool cannot execute because GITHUB_ACCESS_TOKEN is not configured correctly."}

        action = kwargs.get("action")
        repo_full_name_or_username = kwargs.get("repo_full_name")
        path = kwargs.get("path", "/")
        ref = kwargs.get("branch_or_ref")
        recursive_str = kwargs.get("recursive", "false")
        recursive = str(recursive_str).lower() == 'true'

        logger.info(f"Agent {agent_id} attempting GitHub action '{action}' (Target: {repo_full_name_or_username}, Path: {path}, Ref: {ref}, Recursive: {recursive})")

        if not action or action not in ["list_repos", "list_files", "read_file"]:
            return {"status": "error", "message": "Invalid or missing 'action'. Must be 'list_repos', 'list_files', or 'read_file'."}

        try:
            if action == "list_repos":
                endpoint = "/user/repos"; list_target_user = None
                if repo_full_name_or_username and '/' not in repo_full_name_or_username:
                    list_target_user = repo_full_name_or_username
                    endpoint = f"/users/{list_target_user}/repos"
                elif repo_full_name_or_username:
                     parts = repo_full_name_or_username.split('/', 1)
                     list_target_user = parts[0]
                     endpoint = f"/users/{list_target_user}/repos"

                params = {"type": "all", "per_page": 100}
                repo_data = await self._make_github_request("GET", endpoint, params=params)

                if not repo_data or not isinstance(repo_data, list):
                    target_desc = f"user '{list_target_user}'" if list_target_user else "authenticated user"
                    return {"status": "success", "message": f"No repositories found or accessible for {target_desc}.", "repositories": []}

                repo_details = [{"full_name": repo.get("full_name"), "description": repo.get("description", "No description."), "url": repo.get("html_url")} for repo in repo_data if repo.get("full_name")]

                if not repo_details:
                    target_desc = f"user '{list_target_user}'" if list_target_user else "authenticated user"
                    return {"status": "success", "message": f"No repositories found or accessible for {target_desc} with the provided token.", "repositories": []}

                target_desc_title = f" for {list_target_user}" if list_target_user else " Accessible by Token"
                message = f"Repositories{target_desc_title} (first {len(repo_details)}):\n\n" + "\n".join([f"*   **[{repo['full_name']}]({repo['url']}):** {repo['description']}" for repo in repo_details])
                return {"status": "success", "message": message, "repositories": repo_details}

            if not repo_full_name_or_username or '/' not in repo_full_name_or_username:
                return {"status": "error", "message": f"'repo_full_name' in the format 'username/repo-name' is required for action '{action}'."}

            repo_endpoint_base = f"/repos/{repo_full_name_or_username}"

            if action == "list_files":
                list_path = path.lstrip('/')
                ref_display = ref or 'default branch'

                if recursive:
                    all_items = await self._list_repo_recursively(repo_full_name_or_username, list_path, ref)
                    if not all_items:
                         return {"status": "success", "message": f"Path '{path}' in repository '{repo_full_name_or_username}' (ref: {ref_display}) not found or is empty (recursive search).", "items": []}
                    sorted_items = sorted(all_items, key=lambda x: x.get('path', 'z'))
                    message = f"Recursive listing of '{repo_full_name_or_username}' at path '{path}' (ref: {ref_display}):\n" + "\n".join([f"- {item.get('path')} ({item.get('type')})" for item in sorted_items])
                    return {"status": "success", "message": message, "items": sorted_items}
                else:
                    endpoint = f"{repo_endpoint_base}/contents/{list_path}"
                    params = {"ref": ref} if ref else {}
                    try:
                        contents_data = await self._make_github_request("GET", endpoint, params=params)
                        if not contents_data:
                            return {"status": "success", "message": f"Path '{path}' in repository '{repo_full_name_or_username}' (ref: {ref_display}) not found or is empty.", "items": []}
                        if isinstance(contents_data, list):
                            items = sorted(contents_data, key=lambda x: (x.get('type', 'z'), x.get('name', '')))
                            item_details = [{"name": item.get("name"), "type": item.get("type"), "path": item.get("path")} for item in items]
                            message = f"Contents of '{repo_full_name_or_username}' at path '{path}' (ref: {ref_display}):\n" + "\n".join([f"- {item['name']} ({item['type']})" for item in item_details])
                            return {"status": "success", "message": message, "items": item_details}
                        elif isinstance(contents_data, dict) and contents_data.get("type") == "file":
                            return {"status": "success", "message": f"Path '{path}' points to a single file: {contents_data.get('name')}. Use 'read_file' action to get content.", "items": [{"name": contents_data.get("name"), "type": "file", "path": contents_data.get("path")}]}
                        else:
                            return {"status": "error", "message": f"Unexpected response format when listing path '{path}' in repository '{repo_full_name_or_username}'."}
                    except FileNotFoundError:
                        return {"status": "error", "message": f"Path '{path}' not found in repository '{repo_full_name_or_username}' (ref: {ref_display})."}

            elif action == "read_file":
                if not path or path == '/':
                    return {"status": "error", "message": "A specific file 'path' is required for action 'read_file'."}
                read_path = path.lstrip('/')
                endpoint = f"{repo_endpoint_base}/contents/{read_path}"
                params = {"ref": ref} if ref else {}
                try:
                    file_data = await self._make_github_request("GET", endpoint, params=params)
                    if not isinstance(file_data, dict) or file_data.get("type") != "file":
                        type_found = file_data.get("type", "unknown") if isinstance(file_data, dict) else "unexpected format"
                        return {"status": "error", "message": f"Path '{path}' in repository '{repo_full_name_or_username}' is not a file (type: {type_found})."}

                    content_base64 = file_data.get("content")
                    file_size = file_data.get("size", 0)
                    content_bytes = b''

                    if content_base64 is None:
                        download_url = file_data.get("download_url")
                        if download_url:
                             MAX_DOWNLOAD_SIZE = 1 * 1024 * 1024
                             if file_size > MAX_DOWNLOAD_SIZE:
                                 return {"status": "error", "message": f"File '{path}' is too large ({file_size} bytes) to download directly."}
                             async with aiohttp.ClientSession() as dl_session:
                                 async with dl_session.get(download_url, timeout=30) as dl_response:
                                     if dl_response.status == 200:
                                         content_bytes = await dl_response.read()
                                     else:
                                         return {"status": "error", "message": f"Could not retrieve content for file '{path}'. Download failed (Status: {dl_response.status})."}
                        else:
                            return {"status": "error", "message": f"Could not retrieve content for file '{path}'. No content or download URL found."}
                    else:
                        content_bytes = base64.b64decode(content_base64.replace('\n', ''))

                    try:
                        decoded_content = content_bytes.decode('utf-8')
                    except UnicodeDecodeError:
                        return {"status": "success", "message": f"Note: Content of '{path}' could not be decoded as UTF-8 text. It might be a binary file.", "content_base64": base64.b64encode(content_bytes).decode()}

                    logger.info(f"Successfully read file '{path}' from repo '{repo_full_name_or_username}' for agent {agent_id}.")
                    MAX_FILE_READ_CHARS = 50000
                    ref_display = ref or 'default branch'

                    if len(decoded_content) > MAX_FILE_READ_CHARS:
                         message = f"Content of '{repo_full_name_or_username}/{path}' (ref: {ref_display}) [TRUNCATED]:\n\n{decoded_content[:MAX_FILE_READ_CHARS]}\n\n[... File content truncated due to size limit ({MAX_FILE_READ_CHARS} chars) ...]"
                         return {"status": "success", "message": message, "content": decoded_content[:MAX_FILE_READ_CHARS], "truncated": True}
                    else:
                        message = f"Content of '{repo_full_name_or_username}/{path}' (ref: {ref_display}):\n\n{decoded_content}"
                        return {"status": "success", "message": message, "content": decoded_content, "truncated": False}
                except FileNotFoundError:
                    return {"status": "error", "message": f"File path '{path}' not found in repository '{repo_full_name_or_username}' (ref: {ref or 'default'})."}

        except (FileNotFoundError, PermissionError) as known_err:
            logger.warning(f"GitHub tool execution failed for agent {agent_id}: {known_err}")
            return {"status": "error", "message": str(known_err)}
        except Exception as e:
            logger.error(f"Unexpected error executing GitHub tool ({action}) for agent {agent_id}: {e}", exc_info=True)
            return {"status": "error", "message": f"Error executing GitHub tool ({action}): {type(e).__name__} - {e}"}
