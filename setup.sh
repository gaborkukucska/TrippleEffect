#!/data/data/com.termux/files/usr/bin/bash
# TrippleEffect Setup Script

# Ensure clean start
pkg clean
pkg update -y
pkg upgrade -y

# Install core dependencies
pkg install -y python nodejs proot clang libxml2 libxslt openssl

# Setup Python environment
python -m ensurepip --upgrade
pip install --upgrade wheel setuptools

# Create project structure
mkdir -p ~/TrippleEffect/{agents,config/agent_configs,sandbox,web/{static,templates},docs}

# Install Python requirements
cat << EOF > requirements.txt
flask==3.0.2
websockets>=12.0
sqlalchemy==2.0.25
psutil>=5.9.7
python-dotenv==1.0.0
bandit==1.7.5
openai==1.12.0
anthropic==0.19.1
python-magic==0.4.27
EOF

pip install -r requirements.txt

# Set permissions
chmod +x main.py
termux-setup-storage

# Initialize database
if [ ! -f ~/TrippleEffect/config/agents.db ]; then
    sqlite3 ~/TrippleEffect/config/agents.db "VACUUM;"
fi

echo -e "\n\033[1;32mSetup complete!\033[0m"
echo "Run: python main.py to start the system"
echo "Access UI at: http://localhost:5000"
