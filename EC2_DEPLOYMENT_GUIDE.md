# Deco Vision EC2 Deployment Guide

## Instance Information
- **Public IP**: 16.171.18.221
- **OS**: Ubuntu 24.04 LTS
- **Frontend URL**: http://16.171.18.221
- **Backend API**: http://16.171.18.221/api
- **Backend Direct**: http://16.171.18.221:8811

## Prerequisites
1. EC2 instance created and running
2. Security groups configured to allow:
   - Port 22 (SSH) from your IP
   - Port 80 (HTTP) from 0.0.0.0/0
   - Port 443 (HTTPS) from 0.0.0.0/0
   - Port 8811 (Backend API) - optional if using nginx proxy
3. SSH key pair downloaded

## Deployment Steps

### 1. Connect to EC2 Instance
```bash
ssh -i your-key.pem ubuntu@16.171.18.221
```

### 2. Prepare Deployment Files
```bash
cd /home/ubuntu
git clone https://github.com/Tarushi0106/Deco-vision.git
cd Deco-vision
chmod +x deploy_backend.sh deploy_frontend.sh deploy_complete.sh
```

### 3. Option A: Deploy Everything at Once
```bash
bash deploy_complete.sh
```

### 3. Option B: Deploy Backend Only
```bash
bash deploy_backend.sh
```

### 3. Option C: Deploy Frontend Only
```bash
bash deploy_frontend.sh
```

### 4. Configure Backend Environment
Edit the backend configuration:
```bash
nano /home/ubuntu/Deco-vision/backend/.env
```

Update these values with your camera credentials:
```
CAMERA_HOST=your-camera-ip
CAMERA_USER=admin
CAMERA_PASSWORD=your-password
```

### 5. Restart Backend Service (after .env changes)
```bash
sudo systemctl restart deco-vision-backend
```

### 6. Verify Deployment

**Check Backend Status**:
```bash
sudo systemctl status deco-vision-backend
```

**Test Backend API**:
```bash
curl http://localhost:8811/api/cameras
```

**Check Frontend**:
```bash
curl http://localhost/
```

**View Backend Logs**:
```bash
sudo journalctl -u deco-vision-backend -f
```

**View Nginx Logs**:
```bash
sudo tail -f /var/log/nginx/error.log
sudo tail -f /var/log/nginx/access.log
```

## Management Commands

### Backend Service Management
```bash
# Start backend
sudo systemctl start deco-vision-backend

# Stop backend
sudo systemctl stop deco-vision-backend

# Restart backend
sudo systemctl restart deco-vision-backend

# View backend status
sudo systemctl status deco-vision-backend

# View logs (last 100 lines)
sudo journalctl -u deco-vision-backend -n 100

# Stream logs (follow)
sudo journalctl -u deco-vision-backend -f
```

### Frontend Service Management
```bash
# Restart Nginx
sudo systemctl restart nginx

# Stop Nginx
sudo systemctl stop nginx

# View Nginx status
sudo systemctl status nginx

# Check Nginx configuration
sudo nginx -t
```

### Updates
```bash
# Update backend code
cd /home/ubuntu/Deco-vision/backend
git pull
source venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart deco-vision-backend

# Update frontend code
cd /home/ubuntu/Deco-vision/frontend
git pull
npm ci
npm run build
sudo systemctl restart nginx
```

## Troubleshooting

### Backend not responding
```bash
# Check if service is running
sudo systemctl status deco-vision-backend

# Check logs for errors
sudo journalctl -u deco-vision-backend -n 50

# Restart service
sudo systemctl restart deco-vision-backend
```

### Frontend not loading
```bash
# Check Nginx configuration
sudo nginx -t

# Check if Nginx is running
sudo systemctl status nginx

# View Nginx error log
sudo tail -f /var/log/nginx/error.log

# Restart Nginx
sudo systemctl restart nginx
```

### API requests failing
```bash
# Test if backend is accessible
curl http://localhost:8811/

# Check proxy configuration
sudo cat /etc/nginx/sites-available/deco-vision

# Check if ports are listening
sudo netstat -tlnp | grep -E ':80|:8811'
```

### Camera connection issues
```bash
# Edit .env file
nano /home/ubuntu/Deco-vision/backend/.env

# Verify camera credentials
# Then restart the backend
sudo systemctl restart deco-vision-backend

# Check logs
sudo journalctl -u deco-vision-backend -f
```

## Accessing the Application

1. **Open browser and navigate to**:
   ```
   http://16.171.18.221
   ```

2. **Backend API documentation** (if configured):
   ```
   http://16.171.18.221:8811/docs
   ```

3. **Direct backend access**:
   ```
   http://16.171.18.221:8811/api/cameras
   ```

## Additional Configuration

### SSL/HTTPS Setup
```bash
# Install Certbot
sudo apt install -y certbot python3-certbot-nginx

# Get certificate
sudo certbot --nginx -d your-domain.com

# Auto-renewal is configured automatically
```

### System Monitoring
```bash
# Check system resources
free -h
df -h

# Monitor processes
top
# or
htop
```

### Backing Up Data
```bash
# Backup enrollment photos
tar -czf enrollment_photos_backup.tar.gz /home/ubuntu/Deco-vision/backend/data/enrollment_photos/

# Backup database (if using local database)
# Adjust command based on your database type
```

## Important Notes

1. Always update camera credentials in `.env` before running production
2. Regularly check logs for errors: `sudo journalctl -u deco-vision-backend -f`
3. Keep EC2 instance updated: `sudo apt update && sudo apt upgrade -y`
4. Consider setting up CloudWatch monitoring
5. Backup enrollment photos regularly
6. Use SSL/HTTPS for production deployments

## Support & Debugging

For detailed logs during development:
```bash
# View all system logs
journalctl -xe

# View only errors
journalctl -p err

# View logs since last boot
journalctl -b
```

## Next Steps

1. Connect to EC2 via SSH
2. Clone and run deployment scripts
3. Configure camera credentials
4. Access application at http://16.171.18.221
5. Set up monitoring and backups
