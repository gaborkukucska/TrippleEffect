#!/usr/bin/env python3
"""
Comprehensive cleanup utility to remove contaminated message history from ALL agents.

This script addresses the issue where agents accumulated failed tool calls with the error:
"Invalid or missing 'action'. Must be 'list_tools' or 'get_info'."

The script cleans:
1. Session files (agent_session_data.json) - agent_histories section
2. Database interactions table - interaction records

Usage:
    python clear_contaminated_history.py [--dry-run] [--project PROJECT_NAME] [--session SESSION_NAME]
    
Examples:
    python clear_contaminated_history.py --dry-run  # Show what would be cleaned
    python clear_contaminated_history.py           # Clean all contaminated history
    python clear_contaminated_history.py --project "MyProject" --session "session_123"  # Clean specific session
"""

import asyncio
import json
import logging
import argparse
import re
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime

# Import framework components
from src.config.settings import settings
from src.core.database_manager import db_manager, Interaction
from sqlalchemy import select, delete
from sqlalchemy.orm import selectinload

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('cleanup_contaminated_history.log')
    ]
)
logger = logging.getLogger(__name__)

class ContaminatedHistoryCleanup:
    """Handles cleanup of contaminated agent message histories."""
    
    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.stats = {
            'session_files_processed': 0,
            'session_files_modified': 0,
            'total_messages_removed': 0,
            'database_interactions_removed': 0,
            'agents_affected': set()
        }
        
        # Patterns to identify contaminated messages
        self.contaminated_patterns = [
            # Failed tool_information calls with nested tool names
            r'<tool_information><action>execute</action><tool_name>.*?</tool_name>.*?</tool_information>',
            # Error messages about invalid action
            r'Invalid or missing \'action\'\. Must be \'list_tools\' or \'get_info\'',
            # Tool execution failed messages
            r'Tool Execution Failed.*?Invalid or missing \'action\'',
            # Specific failed call patterns from the logs
            r'<tool_information><action>execute</action><tool_name>(file_system|github_tool)</tool_name><parameters>.*?</parameters></tool_information>',
        ]
        
        self.compiled_patterns = [re.compile(pattern, re.DOTALL | re.IGNORECASE) for pattern in self.contaminated_patterns]
    
    def is_contaminated_message(self, message: Dict[str, Any]) -> bool:
        """Check if a message contains contaminated content."""
        if not isinstance(message, dict) or 'content' not in message:
            return False
            
        content = str(message.get('content', ''))
        
        # Check for contaminated patterns
        for pattern in self.compiled_patterns:
            if pattern.search(content):
                return True
                
        # Check for specific tool execution failure sequences
        if ('tool_information' in content and 
            'action' in content and 
            'execute' in content and 
            'Invalid or missing' in content):
            return True
            
        return False
    
    def clean_agent_history(self, history: List[Dict[str, Any]], agent_id: str) -> Tuple[List[Dict[str, Any]], int]:
        """Clean contaminated messages from an agent's history."""
        if not isinstance(history, list):
            logger.warning(f"Invalid history format for agent {agent_id}: {type(history)}")
            return history, 0
            
        cleaned_history = []
        removed_count = 0
        
        for message in history:
            if self.is_contaminated_message(message):
                removed_count += 1
                self.stats['agents_affected'].add(agent_id)
                logger.debug(f"Removing contaminated message from {agent_id}: {str(message)[:100]}...")
            else:
                cleaned_history.append(message)
        
        if removed_count > 0:
            logger.info(f"Cleaned {removed_count} contaminated messages from agent {agent_id}")
            
        return cleaned_history, removed_count
    
    async def clean_session_files(self, project_filter: Optional[str] = None, 
                                session_filter: Optional[str] = None) -> None:
        """Clean contaminated history from session files."""
        logger.info("Starting session file cleanup...")
        
        projects_dir = settings.PROJECTS_BASE_DIR
        if not projects_dir.exists():
            logger.warning(f"Projects directory does not exist: {projects_dir}")
            return
            
        # Find session files
        session_files = []
        
        if project_filter and session_filter:
            # Clean specific session
            session_file = projects_dir / project_filter / session_filter / "agent_session_data.json"
            if session_file.exists():
                session_files.append(session_file)
        elif project_filter:
            # Clean all sessions in specific project
            project_dir = projects_dir / project_filter
            if project_dir.exists():
                session_files.extend(project_dir.glob("*/agent_session_data.json"))
        else:
            # Clean all session files
            session_files.extend(projects_dir.glob("*/*/agent_session_data.json"))
        
        logger.info(f"Found {len(session_files)} session files to process")
        
        for session_file in session_files:
            await self.clean_single_session_file(session_file)
    
    async def clean_single_session_file(self, session_file: Path) -> None:
        """Clean contaminated history from a single session file."""
        self.stats['session_files_processed'] += 1
        
        try:
            # Load session data
            with open(session_file, 'r', encoding='utf-8') as f:
                session_data = json.load(f)
            
            logger.debug(f"Processing session file: {session_file}")
            
            # Clean agent histories
            agent_histories = session_data.get('agent_histories', {})
            modified = False
            
            for agent_id, history in agent_histories.items():
                cleaned_history, removed_count = self.clean_agent_history(history, agent_id)
                
                if removed_count > 0:
                    agent_histories[agent_id] = cleaned_history
                    self.stats['total_messages_removed'] += removed_count
                    modified = True
            
            # Save cleaned data if modified
            if modified and not self.dry_run:
                # Create backup
                backup_file = session_file.with_suffix('.json.backup')
                session_file.rename(backup_file)
                logger.info(f"Created backup: {backup_file}")
                
                # Save cleaned data
                with open(session_file, 'w', encoding='utf-8') as f:
                    json.dump(session_data, f, indent=2)
                
                self.stats['session_files_modified'] += 1
                logger.info(f"Cleaned session file: {session_file}")
            elif modified:
                logger.info(f"[DRY RUN] Would clean session file: {session_file}")
                self.stats['session_files_modified'] += 1
                
        except Exception as e:
            logger.error(f"Error processing session file {session_file}: {e}")
    
    async def clean_database_interactions(self, project_filter: Optional[str] = None,
                                        session_filter: Optional[str] = None) -> None:
        """Clean contaminated interactions from the database."""
        logger.info("Starting database interaction cleanup...")
        
        try:
            await db_manager._initialize_db()
            
            async with db_manager.get_session() as session:
                # Build query to find contaminated interactions
                stmt = select(Interaction)
                
                # Apply filters if specified
                if project_filter and session_filter:
                    # Get specific session
                    from src.core.database_manager import Session as DBSession, Project
                    project_stmt = select(Project).where(Project.name == project_filter)
                    project_result = await session.execute(project_stmt)
                    project = project_result.scalars().first()
                    
                    if not project:
                        logger.warning(f"Project '{project_filter}' not found in database")
                        return
                    
                    session_stmt = select(DBSession).where(
                        DBSession.project_id == project.id,
                        DBSession.name == session_filter
                    )
                    session_result = await session.execute(session_stmt)
                    db_session = session_result.scalars().first()
                    
                    if not db_session:
                        logger.warning(f"Session '{session_filter}' not found in project '{project_filter}'")
                        return
                    
                    stmt = stmt.where(Interaction.session_id == db_session.id)
                
                # Execute query
                result = await session.execute(stmt)
                interactions = result.scalars().all()
                
                logger.info(f"Found {len(interactions)} database interactions to check")
                
                # Find contaminated interactions
                contaminated_ids = []
                
                for interaction in interactions:
                    # Check content for contaminated patterns
                    content = interaction.content or ""
                    tool_calls = interaction.tool_calls_json or []
                    tool_results = interaction.tool_results_json or []
                    
                    # Convert to strings for pattern matching
                    full_content = f"{content} {json.dumps(tool_calls)} {json.dumps(tool_results)}"
                    
                    # Check for contaminated patterns
                    is_contaminated = False
                    for pattern in self.compiled_patterns:
                        if pattern.search(full_content):
                            is_contaminated = True
                            break
                    
                    # Additional specific checks
                    if not is_contaminated:
                        if ('Invalid or missing' in full_content and 
                            'action' in full_content and 
                            'list_tools' in full_content):
                            is_contaminated = True
                    
                    if is_contaminated:
                        contaminated_ids.append(interaction.id)
                        self.stats['agents_affected'].add(interaction.agent_id)
                        logger.debug(f"Found contaminated interaction ID {interaction.id} for agent {interaction.agent_id}")
                
                logger.info(f"Found {len(contaminated_ids)} contaminated database interactions")
                
                # Delete contaminated interactions
                if contaminated_ids and not self.dry_run:
                    delete_stmt = delete(Interaction).where(Interaction.id.in_(contaminated_ids))
                    result = await session.execute(delete_stmt)
                    self.stats['database_interactions_removed'] = result.rowcount
                    logger.info(f"Deleted {result.rowcount} contaminated database interactions")
                elif contaminated_ids:
                    logger.info(f"[DRY RUN] Would delete {len(contaminated_ids)} contaminated database interactions")
                    self.stats['database_interactions_removed'] = len(contaminated_ids)
                    
        except Exception as e:
            logger.error(f"Error cleaning database interactions: {e}")
    
    async def run_cleanup(self, project_filter: Optional[str] = None,
                         session_filter: Optional[str] = None) -> None:
        """Run the complete cleanup process."""
        start_time = datetime.now()
        
        logger.info("=" * 60)
        logger.info("CONTAMINATED HISTORY CLEANUP STARTING")
        logger.info("=" * 60)
        
        if self.dry_run:
            logger.info("DRY RUN MODE - No actual changes will be made")
        
        if project_filter:
            logger.info(f"Filtering to project: {project_filter}")
        if session_filter:
            logger.info(f"Filtering to session: {session_filter}")
        
        logger.info("")
        
        # Clean session files
        await self.clean_session_files(project_filter, session_filter)
        
        # Clean database interactions
        await self.clean_database_interactions(project_filter, session_filter)
        
        # Print summary
        end_time = datetime.now()
        duration = end_time - start_time
        
        logger.info("")
        logger.info("=" * 60)
        logger.info("CLEANUP COMPLETE")
        logger.info("=" * 60)
        logger.info(f"Duration: {duration}")
        logger.info(f"Session files processed: {self.stats['session_files_processed']}")
        logger.info(f"Session files modified: {self.stats['session_files_modified']}")
        logger.info(f"Session messages removed: {self.stats['total_messages_removed']}")
        logger.info(f"Database interactions removed: {self.stats['database_interactions_removed']}")
        logger.info(f"Total agents affected: {len(self.stats['agents_affected'])}")
        
        if self.stats['agents_affected']:
            logger.info(f"Affected agents: {', '.join(sorted(self.stats['agents_affected']))}")
        
        logger.info("")
        
        if not self.dry_run and (self.stats['session_files_modified'] > 0 or self.stats['database_interactions_removed'] > 0):
            logger.info("✅ Contaminated history has been cleaned!")
            logger.info("📝 Backup files created for modified session files")
            logger.info("🔄 Consider restarting the framework to ensure clean agent states")
        elif self.dry_run:
            logger.info("ℹ️ This was a dry run - run without --dry-run to apply changes")
        else:
            logger.info("ℹ️ No contaminated history found - agents are clean!")

async def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Clean contaminated message history from ALL agents",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python clear_contaminated_history.py --dry-run
    python clear_contaminated_history.py
    python clear_contaminated_history.py --project "MyProject"
    python clear_contaminated_history.py --project "MyProject" --session "session_123"
        """
    )
    
    parser.add_argument(
        '--dry-run', 
        action='store_true',
        help='Show what would be cleaned without making changes'
    )
    parser.add_argument(
        '--project',
        type=str,
        help='Clean only the specified project'
    )
    parser.add_argument(
        '--session',
        type=str,
        help='Clean only the specified session (requires --project)'
    )
    
    args = parser.parse_args()
    
    # Validate arguments
    if args.session and not args.project:
        parser.error("--session requires --project to be specified")
    
    # Run cleanup
    cleanup = ContaminatedHistoryCleanup(dry_run=args.dry_run)
    await cleanup.run_cleanup(args.project, args.session)

if __name__ == "__main__":
    asyncio.run(main())
