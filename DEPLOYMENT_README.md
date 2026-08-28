# Deco Vision AWS EC2 Deployment Guide

## 🎯 Quick Start (Choose Your Platform)

### For Linux/Mac Users:
```bash
# Make script executable
chmod +x deploy.sh

# Run deployment
./deploy.sh
```

### For Windows PowerShell Users:
```powershell
# Run deployment
.\deploy.ps1
```

---

## 📋 Instance Information

- **Public IP**: 13.53.133.110
- **SSH User**: ubuntu
- **PEM Key**: deco-vision-dewin.pem
- **OS**: Ubuntu 24.04 LTS
- **Region**: Your AWS region

---

## 🔗 Access Your Application

Once deployed:

- **Frontend**: http://13.53.133.110
- **Backend API**: http://13.53.133.110/api
- **WebSocket**: ws://13.53.133.110/ws
- **Backend Direct** (if needed): http://13.53.133.110:8811

---

## 📂 Deployment Files Included

### One-Command Scripts (Choose One)
- **deploy.sh** - Bash script for Linux/Mac (recommended)
- **deploy.ps1** - PowerShell script for Windows

### Individual Deployment Scripts (for EC2 only)
- **deploy_backend.sh** - Backend setup only
- **deploy_frontend.sh** - Frontend setup only
- **deploy_complete.sh** - Both backend and frontend
- **health_check.sh** - Post-deployment verification

### Configuration
- **backend/.env.example** - Environment template

### Documentation
- **EC2_DEPLOYMENT_GUIDE.md** - Full step-by-step guide
- **QUICK_REFERENCE.md** - Quick command reference
- **README.md** - This file

---

## 🚀 Deployment Steps

### Step 1: Prerequisites
- Ensure PEM key (deco-vision-dewin.pem) is in your project root
- For Windows: Install Git for Windows (includes ssh and scp)
- For Linux/Mac: Should already have ssh/scp

### Step 2: Run One-Command Deployment

**Linux/Mac:**
```bash
chmod +x deploy.sh
./deploy.sh
```

**Windows PowerShell:**
```powershell
.\deploy.ps1
```

This will:
1. ✓ Upload all deployment scripts to EC2
2. ✓ Clone your Deco Vision repository
3. ✓ Install backend (Python, FastAPI, dependencies)
4. ✓ Install frontend (Node.js, React, Vite)
5. ✓ Configure Nginx as reverse proxy
6. ✓ Setup systemd services for auto-restart
7. ✓ Start all services

**Deployment time: ~10-15 minutes**

### Step 3: Configure Backend

After deployment, SSH into your instance:

```bash
ssh -i deco-vision-dewin.pem ubuntu@13.53.133.110
```

Edit the environment file with your camera credentials:

```bash
nano /home/ubuntu/Deco-vision/backend/.env
```

Update these values:
```env
CAMERA_HOST=your-camera-ip-or-hostname
CAMERA_USER=your-camera-username
CAMERA_PASSWORD=your-camera-password
CAMERA_RTSP_PORT=554
```

### Step 4: Restart Backend

After updating .env:

```bash
sudo systemctl restart deco-vision-backend
```

### Step 5: Verify Deployment

Run the health check:

```bash
bash /home/ubuntu/Deco-vision/health_check.sh
```

Check logs for any errors:

```bash
sudo journalctl -u deco-vision-backend -f
```

### Step 6: Access Application

Open your browser and go to:
```
http://13.53.133.110
```

---

## 🔧 Management Commands

### Backend (FastAPI)
```bash
# Status
sudo systemctl status deco-vision-backend

# Start
sudo systemctl start deco-vision-backend

# Stop
sudo systemctl stop deco-vision-backend

# Restart
sudo systemctl restart deco-vision-backend

# View logs (live)
sudo journalctl -u deco-vision-backend -f

# View logs (last 100 lines)
sudo journalctl -u deco-vision-backend -n 100

# View logs (since last boot)
journalctl -u deco-vision-backend -b
```

### Frontend (Nginx)
```bash
# Status
sudo systemctl status nginx

# Start
sudo systemctl start nginx

# Stop
sudo systemctl stop nginx

# Restart
sudo systemctl restart nginx

# Test configuration
sudo nginx -t

# View error logs
sudo tail -f /var/log/nginx/error.log

# View access logs
sudo tail -f /var/log/nginx/access.log
```

---

## 🔄 Updates & Maintenance

### Update Application Code

```bash
cd /home/ubuntu/Deco-vision

# Update from GitHub
git pull

# For backend changes
cd backend
source venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart deco-vision-backend

# For frontend changes
cd ../frontend
npm ci
npm run build
sudo systemctl restart nginx
```

### System Updates

```bash
# Update packages
sudo apt update && sudo apt upgrade -y

# Check disk space
df -h

# Check memory usage
free -h

# Monitor processes
top
```

---

## 🆘 Troubleshooting

### Frontend Not Loading
```bash
# Check Nginx status
sudo systemctl status nginx

# Test Nginx configuration
sudo nginx -t

# View Nginx errors
sudo tail -f /var/log/nginx/error.log

# Restart Nginx
sudo systemctl restart nginx
```

### Backend Not Responding
```bash
# Check backend status
sudo systemctl status deco-vision-backend

# Test if backend is listening
curl http://localhost:8811/

# View backend logs
sudo journalctl -u deco-vision-backend -f

# Restart backend
sudo systemctl restart deco-vision-backend
```

### API Requests Failing
```bash
# Check if both services are running
sudo systemctl status deco-vision-backend nginx

# Check Nginx proxy configuration
sudo cat /etc/nginx/sites-available/deco-vision

# Test backend connectivity from frontend server
curl http://127.0.0.1:8811/api/cameras
```

### Camera Connection Issues
```bash
# Edit .env file with correct credentials
nano /home/ubuntu/Deco-vision/backend/.env

# Verify file has correct values
grep CAMERA /home/ubuntu/Deco-vision/backend/.env

# Restart backend to apply changes
sudo systemctl restart deco-vision-backend

# Watch logs for connection attempts
sudo journalctl -u deco-vision-backend -f
```

### Disk Space Issues
```bash
# Check usage
du -sh /home/ubuntu/Deco-vision/*

# Clean up old logs
sudo journalctl --vacuum=time=7d

# Check what's taking space
du -sh /*
```

---

## 🔐 Security Recommendations

1. **SSH Access**
   - Keep PEM key secure
   - Only share with authorized users
   - Consider restricting SSH in security group

2. **Firewall**
   - Restrict backend API port (8811) if not needed
   - Only allow port 80/443 for public access

3. **SSL/HTTPS Setup**
   ```bash
   sudo apt install -y certbot python3-certbot-nginx
   sudo certbot --nginx -d your-domain.com
   ```

4. **Monitoring**
   - Set up CloudWatch alarms for EC2 instance
   - Monitor disk space and memory usage
   - Enable log shipping to CloudWatch

5. **Backups**
   - Backup enrollment photos regularly
   - Keep database backups
   - Document configuration changes

---

## 📊 Monitor System Health

```bash
# Real-time monitoring
top

# Interactive monitoring (better top)
htop

# Disk usage
df -h

# Memory usage
free -h

# Network connections
netstat -tlnp

# Process check
ps aux | grep -E 'uvicorn|nginx'
```

---

## 📝 Logs Location

- **Backend (FastAPI)**: `journalctl -u deco-vision-backend`
- **Frontend (Nginx)**: `/var/log/nginx/access.log` and `error.log`
- **System**: `/var/log/syslog`

---

## 🎓 Common Tasks

### Add SSL Certificate
```bash
sudo certbot --nginx -d your-domain.com
```

### Enable Auto-renewal
```bash
sudo systemctl enable certbot.timer
```

### Change Backend Port
Edit `/home/ubuntu/Deco-vision/backend/.env`:
```env
SERVER_PORT=9000
```
Then restart: `sudo systemctl restart deco-vision-backend`

### Disable a Service
```bash
sudo systemctl disable deco-vision-backend
```

### View Real-time Service Logs
```bash
# Backend
sudo journalctl -u deco-vision-backend -f

# Nginx
sudo tail -f /var/log/nginx/error.log
```

---

## 📞 Support

For issues with:
- **Application code**: Check GitHub repository
- **Deployment scripts**: Review logs with `journalctl`
- **AWS configuration**: Check AWS console and security groups
- **Camera connectivity**: Verify network and credentials in `.env`

---

## ✅ Deployment Checklist

After deployment, verify:

- [ ] Backend service running: `sudo systemctl status deco-vision-backend`
- [ ] Frontend service running: `sudo systemctl status nginx`
- [ ] Frontend loads: `curl http://localhost/`
- [ ] Backend API responds: `curl http://localhost:8811/`
- [ ] .env configured with camera credentials
- [ ] Backend restarted after .env update
- [ ] Health check passes: `bash health_check.sh`
- [ ] Application accessible at http://13.53.133.110

---

**Last Updated**: 2026-08-28  
**EC2 Instance**: 13.53.133.110  
**PEM Key**: deco-vision-dewin.pem
