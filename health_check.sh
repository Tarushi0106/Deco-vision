#!/bin/bash

echo "=========================================="
echo "Deco Vision Deployment Health Check"
echo "=========================================="
echo ""

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Track failures
FAILED=0

# Check Backend Service
echo "1. Checking Backend Service..."
if sudo systemctl is-active --quiet deco-vision-backend; then
    echo -e "${GREEN}✓${NC} Backend service is running"
else
    echo -e "${RED}✗${NC} Backend service is NOT running"
    FAILED=$((FAILED+1))
fi

# Check Frontend Service (Nginx)
echo "2. Checking Frontend Service (Nginx)..."
if sudo systemctl is-active --quiet nginx; then
    echo -e "${GREEN}✓${NC} Nginx service is running"
else
    echo -e "${RED}✗${NC} Nginx service is NOT running"
    FAILED=$((FAILED+1))
fi

# Check Backend API Port
echo "3. Checking Backend API Port (8811)..."
if sudo netstat -tlnp 2>/dev/null | grep -q ':8811'; then
    echo -e "${GREEN}✓${NC} Backend is listening on port 8811"
else
    echo -e "${RED}✗${NC} Backend is NOT listening on port 8811"
    FAILED=$((FAILED+1))
fi

# Check Frontend Port
echo "4. Checking Frontend Port (80)..."
if sudo netstat -tlnp 2>/dev/null | grep -q ':80'; then
    echo -e "${GREEN}✓${NC} Frontend is listening on port 80"
else
    echo -e "${RED}✗${NC} Frontend is NOT listening on port 80"
    FAILED=$((FAILED+1))
fi

# Test Backend Connectivity
echo "5. Testing Backend API Connectivity..."
if curl -s http://localhost:8811/ > /dev/null 2>&1; then
    echo -e "${GREEN}✓${NC} Backend API is responding"
else
    echo -e "${RED}✗${NC} Backend API is NOT responding"
    FAILED=$((FAILED+1))
fi

# Test Frontend Connectivity
echo "6. Testing Frontend Connectivity..."
if curl -s http://localhost/ > /dev/null 2>&1; then
    echo -e "${GREEN}✓${NC} Frontend is responding"
else
    echo -e "${RED}✗${NC} Frontend is NOT responding"
    FAILED=$((FAILED+1))
fi

# Check Disk Space
echo "7. Checking Disk Space..."
DISK_USAGE=$(df / | awk 'NR==2 {print $5}' | sed 's/%//')
if [ "$DISK_USAGE" -lt 80 ]; then
    echo -e "${GREEN}✓${NC} Disk usage is healthy (${DISK_USAGE}%)"
else
    echo -e "${YELLOW}⚠${NC} Disk usage is high (${DISK_USAGE}%)"
fi

# Check Memory
echo "8. Checking Memory Usage..."
MEM_USAGE=$(free | grep Mem | awk '{printf("%.0f", $3/$2 * 100)}')
echo "   Memory usage: ${MEM_USAGE}%"

# Check Backend Environment
echo "9. Checking Backend .env Configuration..."
if [ -f "/home/ubuntu/Deco-vision/backend/.env" ]; then
    if grep -q "CAMERA_HOST" /home/ubuntu/Deco-vision/backend/.env; then
        echo -e "${GREEN}✓${NC} Backend .env file is configured"
    else
        echo -e "${YELLOW}⚠${NC} Backend .env file exists but may not be fully configured"
    fi
else
    echo -e "${YELLOW}⚠${NC} Backend .env file not found - camera connection may fail"
fi

# Summary
echo ""
echo "=========================================="
if [ $FAILED -eq 0 ]; then
    echo -e "${GREEN}All checks passed!${NC}"
else
    echo -e "${RED}${FAILED} checks failed. See details above.${NC}"
fi
echo "=========================================="
echo ""
echo "Access your application at: http://13.53.133.110"
echo ""
echo "Useful commands:"
echo "  View backend logs: sudo journalctl -u deco-vision-backend -f"
echo "  View nginx logs: sudo tail -f /var/log/nginx/error.log"
echo "  Restart backend: sudo systemctl restart deco-vision-backend"
echo "  Restart nginx: sudo systemctl restart nginx"
echo ""
