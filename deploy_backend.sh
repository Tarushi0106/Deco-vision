#!/bin/bash
set -e

echo "=========================================="
echo "Deco Vision Backend Deployment Script"
echo "=========================================="

# Update system
echo "Updating system packages..."
sudo apt update && sudo apt upgrade -y

# Install dependencies
echo "Installing system dependencies..."
sudo apt install -y python3-pip python3-venv python3-dev git curl
sudo apt install -y libsm6 libxext6 libxrender-dev
sudo apt install -y ffmpeg

# Clone/Update repository
echo "Setting up project directory..."
cd /home/ubuntu
if [ ! -d "Deco-vision" ]; then
    git clone https://github.com/Tarushi0106/Deco-vision.git
else
    cd Deco-vision
    git pull
    cd ..
fi

# Setup backend
echo "Setting up backend..."
cd Deco-vision/backend

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install Python dependencies
echo "Installing Python dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

# Create .env file if not exists
if [ ! -f ".env" ]; then
    echo "Creating .env file..."
    cat > .env << 'EOF'
# Backend Configuration
SERVER_HOST=0.0.0.0
SERVER_PORT=8811

# Camera Configuration (update these with your values)
CAMERA_HOST=your-camera-ip
CAMERA_RTSP_PORT=554
CAMERA_USER=admin
CAMERA_PASSWORD=your-password
CAMERA_STREAM_PATH=/h264/ch1/sub/av_stream
CAMERA_ADMIN_PORT=443
RTSP_TRANSPORT=tcp
EOF
    echo "Created .env file. Please edit with your camera credentials."
fi

# Create systemd service
echo "Setting up systemd service..."
sudo tee /etc/systemd/system/deco-vision-backend.service > /dev/null <<EOF
[Unit]
Description=Deco Vision Backend API
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/Deco-vision/backend
Environment="PATH=/home/ubuntu/Deco-vision/backend/venv/bin"
ExecStart=/home/ubuntu/Deco-vision/backend/venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8811
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

# Enable and start service
echo "Enabling backend service..."
sudo systemctl daemon-reload
sudo systemctl enable deco-vision-backend
sudo systemctl start deco-vision-backend

# Verify service
echo ""
echo "=========================================="
echo "Backend Setup Complete!"
echo "=========================================="
sudo systemctl status deco-vision-backend

echo ""
echo "Backend is running at: http://0.0.0.0:8811"
echo ""
