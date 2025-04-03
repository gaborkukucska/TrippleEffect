#!/data/data/com.termux/files/usr/bin/python3
import os
import logging
from flask import Flask, render_template
from flask_socketio import SocketIO
from sqlalchemy import create_engine
from sqlalchemy.orm import scoped_session, sessionmaker
from agents.agent_manager import AgentManager
from config.global_settings import BASE_DIR

# Initialize application
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'tripple_effect_default_key')
socketio = SocketIO(app, async_mode='threading')

# Configure database
engine = create_engine(f'sqlite:///{BASE_DIR}/config/agents.db')
db_session = scoped_session(sessionmaker(autocommit=False, autoflush=False, bind=engine))

# Initialize agent system
agent_manager = AgentManager(db_session)
venv_manager = VenvManager()  # Will be defined in sandbox/venv_manager.py

@app.route('/')
def index():
    """Render main interface"""
    return render_template('index.html')

@app.route('/configure')
def configuration():
    """Agent configuration interface"""
    agents = db_session.query(Agent).all()
    return render_template('configure.html', agents=agents)

@socketio.on('connect')
def handle_connect():
    """WebSocket connection handler"""
    logging.info('Client connected')
    socketio.emit('status_update', {'msg': 'System initialized'})

@socketio.on('user_request')
def handle_user_request(data):
    """Process user input from multiple sources"""
    try:
        # Validate input
        if not data.get('prompt'):
            raise ValueError("Empty request")
        
        # Dispatch to agents
        results = agent_manager.dispatch_request(
            prompt=data['prompt'],
            files=data.get('files', []),
            user_context=data.get('context', {})
        )
        
        # Broadcast results
        socketio.emit('agent_response', {
            'timestamp': datetime.now().isoformat(),
            'results': results
        })
        
    except Exception as e:
        logging.error(f"Request failed: {str(e)}")
        socketio.emit('error', {'message': str(e)})

if __name__ == '__main__':
    # Initialize subsystems
    agent_manager.load_agents()
    venv_manager.verify_base_environment()
    
    # Start application
    socketio.run(app, 
                 host='127.0.0.1',
                 port=5000,
                 use_reloader=False,
                 debug=False)
