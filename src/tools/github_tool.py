# START OF FILE src/tools/github_tool.py
import asyncio
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from github import Github, GithubException, UnknownObjectException # PyGithub library

from src.tools.base import BaseTool, ToolParameter
from src.config.settings import settings # To access GITHUB_ACCESS_TOKEN

logger = logging.getLogger(__name__)

class GitHubTool(BaseTool):
    """
    Interacts with GitHub repositories associated with the configured access token.
    Can list repositories, list files/directories within a repository, and read file contents.
    Requires a GITHUB_ACCESS_TOKEN with 'repo' scope in the .env file.
    """
    name: str = "github_tool"
    description: str = (
        "Accesses GitHub repositories linked to the configured Personal Access Token (PAT). "
        "Allows listing repositories ('list_repos'), listing files/directories in a repo path ('list_files'), "
        "and reading the content of a specific file ('read_file')."
        "Requires GITHUB_ACCESS_TOKEN to be set in the environment."
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
            description="The branch, tag, or commit SHA to use. Defaults to the repository's default branch. Optional for 'list_files' and 'read_file'.",
            required=False,
        ),
    ]

    def __init__(self):
        """Initializes the tool and checks for the GitHub token."""
        self.token = settings.GITHUB_ACCESS_TOKEN
        if not self.token:
            logger.warning("GitHubTool initialized, but GITHUB_ACCESS_TOKEN is not set in the environment. Tool execution will fail.")
            self.github_client = None
        else:
            try:
                # Initialize the GitHub client synchronously (lightweight object creation)
                self.github_client = Github(self.token)
                # Optionally, test connection immediately (might slow down startup)
                # user = self.github_client.get_user()
                # logger.info(f"GitHubTool initialized successfully for user: {user.login}")
                logger.info("GitHubTool initialized with access token.")
            except Exception as e:
                logger.error(f"Error initializing GitHub client: {e}", exc_info=True)
                self.github_client = None


    async def _execute_in_thread(self, func, *args, **kwargs):
         """ Helper function to run synchronous PyGithub calls in a thread pool. """
         loop = asyncio.get_running_loop()
         return await loop.run_in_executor(None, func, *args, **kwargs)


    async def execute(self, agent_id: str, agent_sandbox_path: Path, **kwargs: Any) -> Any:
        """
        Executes the GitHub operation based on the provided action.

        Args:
            agent_id (str): The ID of the agent calling the tool.
            agent_sandbox_path (Path): The path to the agent's sandbox (not used by this tool).
            **kwargs: Arguments containing 'action' and other relevant parameters.

        Returns:
            str: Result of the operation (list of repos/files, file content) or an error message.
        """
        if not self.github_client:
            return "Error: GitHubTool cannot execute because GITHUB_ACCESS_TOKEN is not configured correctly."

        action = kwargs.get("action")
        repo_full_name = kwargs.get("repo_full_name")
        path = kwargs.get("path", "/") # Default to root path if not provided
        ref = kwargs.get("branch_or_ref") # Can be None

        logger.info(f"Agent {agent_id} attempting GitHub action '{action}' (Repo: {repo_full_name}, Path: {path}, Ref: {ref})")

        if not action or action not in ["list_repos", "list_files", "read_file"]:
            return f"Error: Invalid or missing 'action'. Must be 'list_repos', 'list_files', or 'read_file'."

        try:
            if action == "list_repos":
                # List repositories accessible by the token
                repos = await self._execute_in_thread(self.github_client.get_user().get_repos, type='all') # List all repos user has access to
                repo_names = [repo.full_name for repo in repos]
                if not repo_names:
                    return "No repositories found or accessible with the provided token."
                return f"Accessible Repositories:\n- " + "\n- ".join(repo_names)

            # Actions requiring repo_full_name
            if not repo_full_name:
                return f"Error: 'repo_full_name' (e.g., 'username/repo-name') is required for action '{action}'."

            # Get the repository object
            try:
                repo = await self._execute_in_thread(self.github_client.get_repo, repo_full_name)
            except UnknownObjectException:
                 logger.warning(f"GitHub repo '{repo_full_name}' not found or inaccessible for agent {agent_id}.")
                 return f"Error: Repository '{repo_full_name}' not found or access denied."
            except GithubException as e:
                 logger.error(f"GitHub API error getting repo '{repo_full_name}' for agent {agent_id}: Status {e.status}, Data: {e.data}")
                 return f"Error accessing repository '{repo_full_name}': {e.data.get('message', 'GitHub API error')}"

            # Determine the ref to use (default branch if not specified)
            target_ref = ref if ref else repo.default_branch
            logger.debug(f"Using ref: {target_ref} for repo {repo_full_name}")

            if action == "list_files":
                # List files/directories at the specified path
                try:
                    contents = await self._execute_in_thread(repo.get_contents, path, ref=target_ref)
                    if isinstance(contents, list): # It's a directory listing
                        file_list = [f"- {item.name} ({item.type})" for item in sorted(contents, key=lambda x: x.type + x.name)] # Sort by type then name
                        if not file_list:
                             return f"Directory '{path}' in repository '{repo_full_name}' (ref: {target_ref}) is empty."
                        return f"Contents of '{repo_full_name}' at path '{path}' (ref: {target_ref}):\n" + "\n".join(file_list)
                    else: # It's likely a single file if get_contents doesn't return list for a file path
                        return f"Path '{path}' points to a single file: {contents.name}. Use 'read_file' action to get content."
                except UnknownObjectException:
                    return f"Error: Path '{path}' not found in repository '{repo_full_name}' (ref: {target_ref})."
                except GithubException as e:
                     logger.error(f"GitHub API error listing files in '{repo_full_name}/{path}' for agent {agent_id}: Status {e.status}, Data: {e.data}")
                     return f"Error listing files in '{repo_full_name}/{path}': {e.data.get('message', 'GitHub API error')}"


            elif action == "read_file":
                if not path or path == '/':
                     return f"Error: 'path' specifying a file is required for action 'read_file'."
                # Read content of the specified file
                try:
                    file_content_obj = await self._execute_in_thread(repo.get_contents, path, ref=target_ref)
                    # Check if it's actually a file
                    if file_content_obj.type != 'file':
                        return f"Error: Path '{path}' in repository '{repo_full_name}' is not a file (it's a {file_content_obj.type})."

                    # Decode content (it's Base64 encoded)
                    decoded_content = file_content_obj.decoded_content.decode('utf-8')
                    logger.info(f"Successfully read file '{path}' from repo '{repo_full_name}' for agent {agent_id}.")
                    # Optional: Limit content size?
                    MAX_FILE_READ_SIZE = 50 * 1024 # 50 KB limit example
                    if len(decoded_content) > MAX_FILE_READ_SIZE:
                         logger.warning(f"Read large file '{path}' from GitHub ({len(decoded_content)} bytes), truncating.")
                         return f"Content of '{repo_full_name}/{path}' (ref: {target_ref}) [TRUNCATED]:\n\n{decoded_content[:MAX_FILE_READ_SIZE]}\n\n[... File content truncated due to size limit ...]"
                    else:
                        return f"Content of '{repo_full_name}/{path}' (ref: {target_ref}):\n\n{decoded_content}"

                except UnknownObjectException:
                     return f"Error: File path '{path}' not found in repository '{repo_full_name}' (ref: {target_ref})."
                except GithubException as e:
                     # Handle potential API errors like rate limits or other issues
                     logger.error(f"GitHub API error reading file '{repo_full_name}/{path}' for agent {agent_id}: Status {e.status}, Data: {e.data}")
                     return f"Error reading file '{path}': {e.data.get('message', 'GitHub API error')}"
                except Exception as e: # Catch potential decoding errors or others
                     logger.error(f"Error decoding or processing file '{repo_full_name}/{path}' for agent {agent_id}: {e}", exc_info=True)
                     return f"Error processing file '{path}': {type(e).__name__}"

        except Exception as e:
            # Catch unexpected errors during execution
            logger.error(f"Unexpected error executing GitHub tool ({action}) for agent {agent_id}: {e}", exc_info=True)
            return f"Error executing GitHub tool ({action}): {type(e).__name__} - {e}"
