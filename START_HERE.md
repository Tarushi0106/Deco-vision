# ✅ Deco Vision AWS EC2 Deployment - Ready to Deploy

## 🎉 Your Deployment Package is Complete!

**Instance IP**: 13.53.133.110  
**PEM Key**: deco-vision-dewin.pem  
**SSH User**: ubuntu

---

## 📦 What You Have

### One-Command Deployment Scripts
Choose based on your operating system:

#### Linux/Mac:
```bash
chmod +x deploy.sh
./deploy.sh
```

#### Windows PowerShell:
```powershell
.\deploy.ps1
```

Both scripts will:
✓ Connect to your EC2 instance  
✓ Upload deployment automation scripts  
✓ Clone Deco Vision repository  
✓ Install Python + FastAPI backend  
✓ Install Node.js + React frontend  
✓ Setup Nginx as reverse proxy  
✓ Configure systemd services  
✓ Start all services automatically  

**Total time**: ~10-15 minutes

---

## 🚀 Quick Start

### Step 1: Run the Deployment Script

**For Linux/Mac:**
```bash
chmod +x deploy.sh
./deploy.sh
```

**For Windows PowerShell:**
```powershell
.\deploy.ps1
```

### Step 2: Wait for Completion
The script will show progress and completion status.

### Step 3: Configure Camera
```bash
ssh -i deco-vision-dewin.pem ubuntu@13.53.133.110
nano /home/ubuntu/Deco-vision/backend/.env
# Edit with your camera details
```

### Step 4: Restart Backend
```bash
sudo systemctl restart deco-vision-backend
```

### Step 5: Access Application
Open browser and go to: **http://13.53.133.110**

---

## 📍 After Deployment

- **Frontend**: http://13.53.133.110
- **Backend API**: http://13.53.133.110/api
- **WebSocket**: ws://13.53.133.110/ws

---

## 📚 Documentation Files

All these are in your project folder:

1. **DEPLOYMENT_README.md** - Complete deployment guide
2. **EC2_DEPLOYMENT_GUIDE.md** - Step-by-step instructions
3. **QUICK_REFERENCE.md** - Common commands cheat sheet
4. **deploy_backend.sh** - Backend deployment script
5. **deploy_frontend.sh** - Frontend deployment script
6. **deploy_complete.sh** - Both backend + frontend
7. **health_check.sh** - Post-deployment verification

---

## 🔧 Key Management Commands

### Check Services Status
```bash
sudo systemctl status deco-vision-backend  # Backend
sudo systemctl status nginx                 # Frontend
```

### View Logs
```bash
sudo journalctl -u deco-vision-backend -f  # Backend (live)
sudo tail -f /var/log/nginx/error.log      # Frontend (live)
```

### Restart Services
```bash
sudo systemctl restart deco-vision-backend  # Restart backend
sudo systemctl restart nginx                 # Restart frontend
```

### Health Check
```bash
bash /home/ubuntu/Deco-vision/health_check.sh
```

---

## ✨ What's Configured

✅ Backend:
- Python 3.11+ virtual environment
- FastAPI with Uvicorn
- Systemd service (auto-restart on crash)
- Port 8811 listening
- All dependencies installed

✅ Frontend:
- Node.js 20
- React 19 + React Router
- Vite build system
- Nginx reverse proxy
- API proxy to backend
- WebSocket support

✅ Infrastructure:
- Nginx reverse proxy
- SSL-ready (for HTTPS setup)
- Systemd auto-restart
- Logging configured
- Port forwarding setup

---

## 🆘 Troubleshooting

**Frontend not loading?**
```bash
sudo systemctl restart nginx
sudo tail -f /var/log/nginx/error.log
```

**Backend not responding?**
```bash
sudo systemctl restart deco-vision-backend
sudo journalctl -u deco-vision-backend -f
```

**Need to update camera credentials?**
```bash
nano /home/ubuntu/Deco-vision/backend/.env
sudo systemctl restart deco-vision-backend
```

---

## 📋 Deployment Checklist

- [ ] Have PEM key: deco-vision-dewin.pem (✓ Already in folder)
- [ ] Know your EC2 IP: 13.53.133.110 (✓ Already configured)
- [ ] Run deployment script (deploy.sh or deploy.ps1)
- [ ] Wait for completion (~10-15 min)
- [ ] SSH into instance
- [ ] Edit .env with camera credentials
- [ ] Restart backend
- [ ] Visit http://13.53.133.110
- [ ] Run health check

---

## 🎯 Next Action

**For Linux/Mac Users:**
```bash
chmod +x deploy.sh && ./deploy.sh
```

**For Windows PowerShell Users:**
```powershell
.\deploy.ps1
```

---

**Ready to deploy? The scripts handle everything!** 🚀

For detailed documentation, see: **DEPLOYMENT_README.md**
