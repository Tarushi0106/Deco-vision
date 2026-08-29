#!/bin/bash
set -e

echo "=========================================="
echo "Deco Vision Complete Deployment Script"
echo "EC2 Instance: 13.53.133.110"
echo "=========================================="

# Run backend deployment
echo "Starting backend deployment..."
bash /home/ubuntu/Deco-vision/deploy_backend.sh

sleep 5

# Run frontend deployment
echo "Starting frontend deployment..."
bash /home/ubuntu/Deco-vision/deploy_frontend.sh

echo ""
echo "=========================================="
echo "DEPLOYMENT COMPLETE!"
echo "=========================================="
echo ""
echo "Frontend URL: http://13.53.133.110"
echo "Backend API: http://13.53.133.110/api"
echo "WebSocket: ws://13.53.133.110/ws"
echo ""
echo "Next steps:"
echo "1. Update the .env file in /home/ubuntu/Deco-vision/backend with your camera credentials"
echo "2. Restart the backend service:"
echo "   sudo systemctl restart deco-vision-backend"
echo "3. Access the application at http://13.53.133.110"
echo ""
echo "Useful commands:"
echo "  View backend logs: sudo journalctl -u deco-vision-backend -f"
echo "  View nginx logs: sudo tail -f /var/log/nginx/error.log"
echo "  Restart backend: sudo systemctl restart deco-vision-backend"
echo "  Restart frontend: sudo systemctl restart nginx"
echo ""
