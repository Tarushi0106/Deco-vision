#!/bin/bash

# Deco Vision One-Command Deployment Script
# Usage: ./deploy.sh

set -e

# Configuration
EC2_IP="13.53.133.110"
EC2_USER="ubuntu"
PEM_KEY="deco-vision-dewin.pem"

echo "=========================================="
echo "Deco Vision AWS EC2 Deployment"
echo "=========================================="
echo ""
echo "📍 Instance: $EC2_IP"
echo "🔑 Using PEM key: $PEM_KEY"
echo ""

# Check if PEM key exists
if [ ! -f "$PEM_KEY" ]; then
    echo "❌ Error: PEM key not found: $PEM_KEY"
    echo "Please ensure $PEM_KEY is in the current directory"
    exit 1
fi

# Set PEM key permissions
chmod 400 "$PEM_KEY"

# Step 1: Copy deployment scripts to EC2
echo "📤 Uploading deployment scripts to EC2..."
scp -i "$PEM_KEY" -o StrictHostKeyChecking=no \
    deploy_backend.sh deploy_frontend.sh deploy_complete.sh health_check.sh \
    ${EC2_USER}@${EC2_IP}:/home/${EC2_USER}/Deco-vision/

echo "✓ Scripts uploaded successfully"
echo ""

# Step 2: Clone repository on EC2 if needed
echo "🔄 Cloning Deco Vision repository on EC2..."
ssh -i "$PEM_KEY" -o StrictHostKeyChecking=no ${EC2_USER}@${EC2_IP} << 'SSH_COMMANDS'
    cd /home/ubuntu
    if [ ! -d "Deco-vision" ]; then
        git clone https://github.com/Tarushi0106/Deco-vision.git
    else
        cd Deco-vision && git pull && cd ..
    fi
SSH_COMMANDS

echo "✓ Repository ready"
echo ""

# Step 3: Make scripts executable
echo "🔧 Making deployment scripts executable..."
ssh -i "$PEM_KEY" -o StrictHostKeyChecking=no ${EC2_USER}@${EC2_IP} << 'SSH_COMMANDS'
    chmod +x /home/ubuntu/Deco-vision/*.sh
SSH_COMMANDS

echo "✓ Scripts are executable"
echo ""

# Step 4: Run deployment
echo "🚀 Starting deployment..."
echo "This may take 10-15 minutes. Grab some coffee! ☕"
echo ""

ssh -i "$PEM_KEY" -o StrictHostKeyChecking=no ${EC2_USER}@${EC2_IP} << 'SSH_COMMANDS'
    cd /home/ubuntu/Deco-vision
    bash deploy_complete.sh
SSH_COMMANDS

echo ""
echo "=========================================="
echo "✅ DEPLOYMENT COMPLETED!"
echo "=========================================="
echo ""
echo "🌐 Your application is ready!"
echo ""
echo "📍 Frontend URL: http://13.53.133.110"
echo "🔌 Backend API: http://13.53.133.110/api"
echo "🔗 WebSocket: ws://13.53.133.110/ws"
echo ""
echo "⚙️  Next Steps:"
echo "1. SSH into your instance:"
echo "   ssh -i $PEM_KEY ubuntu@13.53.133.110"
echo ""
echo "2. Configure your camera credentials:"
echo "   nano /home/ubuntu/Deco-vision/backend/.env"
echo ""
echo "3. Restart backend with new configuration:"
echo "   sudo systemctl restart deco-vision-backend"
echo ""
echo "4. Run health check:"
echo "   bash /home/ubuntu/Deco-vision/health_check.sh"
echo ""
echo "5. View logs if needed:"
echo "   sudo journalctl -u deco-vision-backend -f"
echo ""
echo "=========================================="
