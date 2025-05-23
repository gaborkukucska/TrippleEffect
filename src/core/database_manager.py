# START OF FILE src/core/database_manager.py
import asyncio
import logging
import json
from contextlib import contextmanager, asynccontextmanager
from pathlib import Path
from typing import List, Optional, Dict, Any, Generator, AsyncGenerator
import datetime # Added for update timestamp

# SQLAlchemy imports
from sqlalchemy import create_engine, Column, Integer, String, Text, Float, DateTime, ForeignKey, JSON, Index, select, desc, func, update # Added update
from sqlalchemy.orm import sessionmaker, relationship, DeclarativeBase
from sqlalchemy.sql import func as sql_func # Alias sql functions
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession # Use Async Engine

# Import settings for DB path and BASE_DIR
from src.config.settings import settings, BASE_DIR

logger = logging.getLogger(__name__)

# Define the database file path (using SQLite for simplicity/compatibility)
DEFAULT_DB_FILENAME = "trippleeffect_memory.db"
DB_FILE_PATH = BASE_DIR / "data" / DEFAULT_DB_FILENAME
DB_URL = f"sqlite+aiosqlite:///{DB_FILE_PATH}" # Use aiosqlite for async

# Define the Base for declarative models
class Base(DeclarativeBase):
    pass

# --- SQLAlchemy Models ---

class Project(Base):
    __tablename__ = 'projects'
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, unique=True)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=sql_func.now())

    sessions = relationship("Session", back_populates="project", cascade="all, delete-orphan")

    __table_args__ = (Index('ix_project_name', 'name'),) # Index for faster name lookups

class Session(Base):
    __tablename__ = 'sessions'
    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey('projects.id'), nullable=False)
    name = Column(String, nullable=False)
    start_time = Column(DateTime(timezone=True), server_default=sql_func.now())
    end_time = Column(DateTime(timezone=True), nullable=True)

    project = relationship("Project", back_populates="sessions")
    # Define relationships to other tables linked by session_id
    agent_records = relationship("AgentRecord", back_populates="session", cascade="all, delete-orphan")
    interactions = relationship("Interaction", back_populates="session", cascade="all, delete-orphan")
    knowledge = relationship("LongTermKnowledge", back_populates="session", cascade="all, delete-orphan")

    __table_args__ = (Index('ix_session_project_id', 'project_id'), Index('ix_session_name_project', 'project_id', 'name'),) # Added combined index

class AgentRecord(Base):
    """ Represents an agent instance within a specific session. """
    __tablename__ = 'agent_records'
    id = Column(Integer, primary_key=True)
    agent_id = Column(String, nullable=False) # The runtime agent ID
    session_id = Column(Integer, ForeignKey('sessions.id'), nullable=False)
    persona = Column(String, nullable=False)
    model_config_json = Column(JSON, nullable=True) # Store provider, model, temp etc. as JSON
    created_at = Column(DateTime(timezone=True), server_default=sql_func.now())

    session = relationship("Session", back_populates="agent_records")

    __table_args__ = (Index('ix_agentrecord_session_agent', 'session_id', 'agent_id', unique=True),) # Agent ID should be unique per session

class Interaction(Base):
    """ Logs a message or tool interaction within a session. """
    __tablename__ = 'interactions'
    id = Column(Integer, primary_key=True)
    session_id = Column(Integer, ForeignKey('sessions.id'), nullable=False)
    agent_id = Column(String, nullable=False) # Agent involved (sender or target)
    role = Column(String, nullable=False) # E.g., 'user', 'assistant', 'tool', 'system_event', 'assistant_plan', 'system_feedback', 'system_error'
    content = Column(Text, nullable=True) # Main text content
    tool_calls_json = Column(JSON, nullable=True) # If role='assistant' and tools were called
    tool_results_json = Column(JSON, nullable=True) # If role='tool' or 'system_feedback', the results/data
    timestamp = Column(DateTime(timezone=True), server_default=sql_func.now())

    session = relationship("Session", back_populates="interactions")
    knowledge_source = relationship("LongTermKnowledge", back_populates="source_interaction", uselist=False) # One interaction might source one knowledge item

    __table_args__ = (
        Index('ix_interaction_session_id', 'session_id'),
        Index('ix_interaction_agent_id', 'agent_id'),
        Index('ix_interaction_timestamp', 'timestamp'),
    )

class LongTermKnowledge(Base):
    """ Stores distilled knowledge points derived from interactions. """
    __tablename__ = 'long_term_knowledge'
    id = Column(Integer, primary_key=True)
    session_id = Column(Integer, ForeignKey('sessions.id'), nullable=True) # Knowledge might span sessions? Or be linked to the session it was learned in
    keywords = Column(String, nullable=False) # Comma-separated or JSON list
    summary = Column(Text, nullable=False)
    source_interaction_id = Column(Integer, ForeignKey('interactions.id'), nullable=True) # Link back to where this was learned
    importance_score = Column(Float, nullable=True, default=0.5) # Simple relevance/importance
    created_at = Column(DateTime(timezone=True), server_default=sql_func.now())
    last_accessed = Column(DateTime(timezone=True), nullable=True) # For potential future use (e.g., LRU cache)

    session = relationship("Session", back_populates="knowledge")
    source_interaction = relationship("Interaction", back_populates="knowledge_source")

    __table_args__ = (
        Index('ix_knowledge_keywords', 'keywords'), # Index for keyword search
        Index('ix_knowledge_importance', 'importance_score'),
    )


# --- Database Manager Class ---

class DatabaseManager:
    """ Handles database connection, session management, and CRUD operations. """

    def __init__(self, db_url: str = DB_URL):
        self._engine = None # Initialize as None
        self._session_local = None # Initialize as None
        self.db_url = db_url
        # REMOVED: asyncio.create_task(self._initialize_db()) - Called from main.py lifespan

    async def _initialize_db(self):
        """ Asynchronously initializes the database engine and creates tables. """
        # Prevent re-initialization
        if self._engine is not None:
            logger.debug("Database already initialized. Skipping re-initialization.")
            return

        logger.info(f"Initializing asynchronous database connection to: {self.db_url}")
        try:
            # Ensure the data directory exists
            db_path = Path(DB_FILE_PATH)
            db_path.parent.mkdir(parents=True, exist_ok=True)
            logger.info(f"Ensured database directory exists: {db_path.parent}")

            self._engine = create_async_engine(self.db_url, echo=False) # Set echo=True for debugging SQL
            self._session_local = sessionmaker(
                bind=self._engine,
                class_=AsyncSession,
                expire_on_commit=False, # Important for async sessions
                autocommit=False,
                autoflush=False,
            )

            # Create tables if they don't exist
            async with self._engine.begin() as conn:
                # await conn.run_sync(Base.metadata.drop_all) # Uncomment to drop tables on startup for testing
                await conn.run_sync(Base.metadata.create_all)
            logger.info("Database tables created/verified successfully.")

        except Exception as e:
            logger.critical(f"CRITICAL: Database initialization failed: {e}", exc_info=True)
            self._engine = None
            self._session_local = None

    async def close(self):
        """ Closes the database engine connection pool. """
        if self._engine:
            logger.info("Closing database engine.")
            await self._engine.dispose()
            self._engine = None
            self._session_local = None

    @asynccontextmanager
    async def get_session(self) -> AsyncGenerator[AsyncSession, None]:
        """ Provides a database session within an async context manager. """
        if self._session_local is None:
             logger.error("Database session factory not initialized or initialization failed.")
             # Raise a clear error if trying to get a session before init is complete/successful
             raise RuntimeError("DatabaseManager._initialize_db() must be awaited successfully before getting a session.")

        session: AsyncSession = self._session_local()
        try:
            yield session
            await session.commit()
            logger.debug("DB Session committed.")
        except Exception as e:
            logger.error(f"Database session error: {e}", exc_info=True)
            await session.rollback()
            logger.warning("DB Session rolled back due to error.")
            raise # Re-raise the exception after rollback
        finally:
            await session.close()
            logger.debug("DB Session closed.")

    # --- Basic CRUD Methods ---

    async def add_project(self, name: str, description: Optional[str] = None) -> Optional[Project]:
        """ Adds a new project or returns the existing one. """
        async with self.get_session() as session:
            # Check if project exists
            stmt = select(Project).where(Project.name == name)
            result = await session.execute(stmt)
            existing = result.scalars().first()
            if existing:
                 logger.warning(f"Project '{name}' already exists with ID {existing.id}.")
                 return existing # Return existing project
            # Create new project
            new_project = Project(name=name, description=description)
            session.add(new_project)
            await session.flush() # Flush to get the ID
            await session.refresh(new_project)
            logger.info(f"Added new project '{name}' with ID {new_project.id}.")
            return new_project

    async def get_project_by_name(self, name: str) -> Optional[Project]:
        """ Gets a project by its name. """
        async with self.get_session() as session:
            stmt = select(Project).where(Project.name == name)
            result = await session.execute(stmt)
            project = result.scalars().first()
            if project: logger.debug(f"Found project '{name}' with ID {project.id}.")
            else: logger.debug(f"Project '{name}' not found.")
            return project

    # --- Session Management ---
    async def start_session(self, project_id: int, session_name: str) -> Optional[Session]:
        """ Creates a new session record for a project. """
        async with self.get_session() as session:
            new_session = Session(project_id=project_id, name=session_name)
            session.add(new_session)
            await session.flush()
            await session.refresh(new_session)
            logger.info(f"Started new session '{session_name}' (ID: {new_session.id}) for Project ID {project_id}.")
            return new_session

    async def end_session(self, session_id: int):
        """ Marks a session as ended by setting its end_time. """
        async with self.get_session() as session:
            stmt = select(Session).where(Session.id == session_id)
            result = await session.execute(stmt)
            db_session = result.scalars().first()
            if db_session:
                # Use the database's current timestamp function
                db_session.end_time = sql_func.now() # Or specific dialect function if needed
                await session.flush()
                logger.info(f"Marked session ID {session_id} as ended.")
            else:
                logger.warning(f"Could not find session ID {session_id} to mark as ended.")

    async def get_session_by_name(self, project_id: int, session_name: str) -> Optional[Session]:
        """ Gets a specific session by project ID and session name. """
        async with self.get_session() as session:
            stmt = select(Session).where(Session.project_id == project_id, Session.name == session_name)
            result = await session.execute(stmt)
            db_session = result.scalars().first()
            if db_session: logger.debug(f"Found session '{session_name}' (ID: {db_session.id}) for project ID {project_id}.")
            else: logger.debug(f"Session '{session_name}' not found for project ID {project_id}.")
            return db_session

    # --- *** NEW METHOD: Get Session ID by Name *** ---
    async def get_session_id_by_name(self, project_id: int, session_name: str) -> Optional[int]:
        """ Gets the ID of a specific session by project ID and session name. """
        async with self.get_session() as session:
            # Select only the ID column for efficiency
            stmt = select(Session.id).where(Session.project_id == project_id, Session.name == session_name)
            result = await session.execute(stmt)
            session_id = result.scalars().first() # Fetch the first result (should be unique)
            if session_id: logger.debug(f"Found session ID {session_id} for project ID {project_id}, session name '{session_name}'.")
            else: logger.debug(f"Session ID not found for project ID {project_id}, session name '{session_name}'.")
            return session_id
    # --- *** END NEW METHOD *** ---


    # --- Agent Records ---
    async def add_agent_record(self, session_id: int, agent_id: str, persona: str, model_config_dict: Optional[Dict] = None) -> Optional[AgentRecord]:
        """ Adds a record for an agent created during a session. """
        async with self.get_session() as session:
            # Check if record already exists for this agent in this session
            stmt_check = select(AgentRecord).where(AgentRecord.session_id == session_id, AgentRecord.agent_id == agent_id)
            result_check = await session.execute(stmt_check)
            existing_record = result_check.scalars().first()
            if existing_record:
                 logger.warning(f"Agent record for '{agent_id}' already exists in session {session_id}. Skipping duplicate add.")
                 return existing_record # Return existing record

            new_agent_record = AgentRecord(
                session_id=session_id,
                agent_id=agent_id,
                persona=persona,
                model_config_json=model_config_dict # SQLAlchemy handles JSON conversion
            )
            session.add(new_agent_record)
            await session.flush()
            await session.refresh(new_agent_record)
            logger.info(f"Added record for agent '{agent_id}' (Persona: '{persona}') in Session ID {session_id}.")
            return new_agent_record

    # --- Interaction Logging ---
    async def log_interaction(
        self,
        session_id: int,
        agent_id: str,
        role: str,
        content: Optional[str] = None,
        tool_calls: Optional[List[Dict]] = None,
        tool_results: Optional[List[Dict]] = None
        ) -> Optional[Interaction]:
        """ Logs a message or tool interaction to the database. """
        async with self.get_session() as session:
             interaction = Interaction(
                 session_id=session_id,
                 agent_id=agent_id,
                 role=role,
                 content=content,
                 tool_calls_json=tool_calls, # Can be None
                 tool_results_json=tool_results # Can be None
             )
             session.add(interaction)
             await session.flush()
             await session.refresh(interaction)
             logger.debug(f"Logged interaction ID {interaction.id} (Agent: {agent_id}, Role: {role}) for Session ID {session_id}.")
             return interaction

    # --- Knowledge Base Methods ---
    async def save_knowledge(
        self,
        keywords: str,
        summary: str,
        session_id: Optional[int] = None, # Link to session where learned
        interaction_id: Optional[int] = None, # Link to specific interaction
        importance: Optional[float] = 0.5
        ) -> Optional[LongTermKnowledge]:
        """ Saves a piece of distilled knowledge. """
        async with self.get_session() as session:
            knowledge = LongTermKnowledge(
                session_id=session_id,
                keywords=keywords.lower(), # Store lowercase for consistent search
                summary=summary,
                source_interaction_id=interaction_id,
                importance_score=importance
            )
            session.add(knowledge)
            await session.flush()
            await session.refresh(knowledge)
            logger.info(f"Saved knowledge ID {knowledge.id} (Keywords: '{keywords[:50]}...').")
            return knowledge

    async def search_knowledge(
        self,
        query_keywords: List[str],
        min_importance: Optional[float] = None,
        max_results: int = 5
        ) -> List[LongTermKnowledge]:
        """ Searches long-term knowledge based on keywords. """
        if not query_keywords: return []
        async with self.get_session() as session:
            stmt = select(LongTermKnowledge)
            # Simple keyword matching (can be improved with full-text search or vector search later)
            keyword_filters = [LongTermKnowledge.keywords.contains(kw.lower()) for kw in query_keywords]
            stmt = stmt.where(*keyword_filters) # Requires at least one keyword match

            if min_importance is not None:
                stmt = stmt.where(LongTermKnowledge.importance_score >= min_importance)

            # Order by importance (desc) then creation time (desc)
            stmt = stmt.order_by(desc(LongTermKnowledge.importance_score), desc(LongTermKnowledge.created_at))
            stmt = stmt.limit(max_results)

            result = await session.execute(stmt)
            knowledge_items = result.scalars().all()
            logger.info(f"Knowledge search for '{query_keywords}' found {len(knowledge_items)} items.")

            # Optional: Update last_accessed time for retrieved items
            now_time = datetime.datetime.now(datetime.timezone.utc) # Get current time once
            ids_to_update = [item.id for item in knowledge_items]
            if ids_to_update:
                update_stmt = (
                     update(LongTermKnowledge)
                     .where(LongTermKnowledge.id.in_(ids_to_update))
                     .values(last_accessed=now_time)
                 )
                await session.execute(update_stmt)
                # No need to refresh items here unless you need the updated timestamp immediately

            return list(knowledge_items) # Return as list


# --- Singleton Instance ---
# Instantiate the manager globally BUT DO NOT INITIALIZE ASYNC PARTS YET
db_manager = DatabaseManager()

# --- Cleanup Function ---
async def close_db_connection():
    """ Function to be called during application shutdown. """
    await db_manager.close()

# --- Import needed for update() ---
# Already imported at the top
