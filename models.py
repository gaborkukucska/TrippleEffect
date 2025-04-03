from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, JSON, DateTime, Boolean
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()

class Agent(Base):
    """Database model for agent configurations"""
    __tablename__ = 'agents'
    
    id = Column(Integer, primary_key=True)
    name = Column(String(20), unique=True, nullable=False)
    config_json = Column(Text, nullable=False)
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class ActivityLog(Base):
    """Audit log for agent activities"""
    __tablename__ = 'activity_logs'
    
    id = Column(Integer, primary_key=True)
    agent_id = Column(Integer, nullable=False)
    event_type = Column(String(50), nullable=False)
    data = Column(JSON)
    timestamp = Column(DateTime, default=datetime.utcnow)
