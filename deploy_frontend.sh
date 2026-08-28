#!/bin/bash
set -e

echo "=========================================="
echo "Deco Vision Frontend Deployment Script"
echo "=========================================="

# Install Node.js
echo "Installing Node.js..."
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs

# Navigate to frontend
echo "Building frontend..."
cd /home/ubuntu/Deco-vision/frontend

# Install dependencies and build
npm ci
npm run build

# Install and configure Nginx
echo "Setting up Nginx..."
sudo apt install -y nginx

# Create Nginx configuration
echo "Configuring Nginx..."
sudo tee /etc/nginx/sites-available/deco-vision > /dev/null <<'EOFNGINX'
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name _;

    root /home/ubuntu/Deco-vision/frontend/dist;
    index index.html index.htm;

    # Serve frontend with SPA routing
    location / {
        try_files $uri $uri/ /index.html;
        add_header Cache-Control "no-cache, must-revalidate";
    }

    # Proxy API requests to backend
    location /api/ {
        proxy_pass http://127.0.0.1:8811;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_buffering off;
        proxy_request_buffering off;
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
    }

    # WebSocket support
    location /ws {
        proxy_pass http://127.0.0.1:8811;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_read_timeout 86400;
        proxy_send_timeout 86400;
    }

    # Cache static assets
    location ~* \.(js|css|png|jpg|jpeg|gif|ico|svg|woff|woff2|ttf|eot)$ {
        expires 1y;
        add_header Cache-Control "public, immutable";
    }
}
EOFNGINX

# Enable the site
echo "Enabling Nginx configuration..."
sudo ln -sf /etc/nginx/sites-available/deco-vision /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default

# Test Nginx configuration
sudo nginx -t

# Start/restart Nginx
sudo systemctl enable nginx
sudo systemctl restart nginx

echo ""
echo "=========================================="
echo "Frontend Setup Complete!"
echo "=========================================="
echo ""
echo "Frontend is running at: http://13.53.133.110"
echo ""
