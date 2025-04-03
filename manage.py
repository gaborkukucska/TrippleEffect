#!/usr/bin/env python
import click
from alembic.config import Config
from alembic import command
from models import Base
from config.global_settings import Settings

@click.group()
def cli():
    """TrippleEffect Management Interface"""
    pass

@cli.command()
@click.option('--name', prompt='Agent name')
@click.option('--provider', type=click.Choice(['openai', 'anthropic', 'openrouter', 'ollama']))
def create_agent(name, provider):
    """Initialize new agent configuration"""
    from agents.agent_manager import AgentManager
    from db import Session
    
    config_template = {
        "name": name,
        "api_config": {"provider": provider},
        "model_params": Settings().dict(include={'DEFAULT_TEMP', 'DEFAULT_TOP_P'}),
        "system_messages": [f"You are {name}"],
        "sandbox": {"venv_path": str(Settings.SANDBOX_BASE / name)}
    }
    
    session = Session()
    try:
        AgentManager(session).create_agent(config_template)
        click.echo(f"Agent {name} created successfully")
    except Exception as e:
        click.echo(f"Error: {str(e)}")
    finally:
        session.close()

@cli.command()
def clean_sandbox():
    """Reset all sandbox environments"""
    from shutil import rmtree
    from config.global_settings import Settings
    
    sandbox_dir = Settings.SANDBOX_BASE
    if sandbox_dir.exists():
        rmtree(sandbox_dir)
        sandbox_dir.mkdir()
        click.echo("Sandbox environments reset")
    else:
        click.echo("No sandbox directory found")

@cli.command()
def migrate_db():
    """Run database migrations"""
    alembic_cfg = Config("alembic.ini")
    command.upgrade(alembic_cfg, "head")

if __name__ == "__main__":
    cli()
