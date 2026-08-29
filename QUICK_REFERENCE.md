# Deco Vision EC2 Deployment - Quick Reference

## Your EC2 Instance
- **IP Address**: 16.171.18.221
- **SSH Key**: your-key.pem
- **User**: ubuntu

## Quick Deploy Commands

### 1. SSH to EC2
```bash
ssh -i your-key.pem ubuntu@16.171.18.221
```

### 2. Clone Repository
```bash
cd /home/ubuntu
git clone https://github.com/Tarushi0106/Deco-vision.git
cd Deco-vision
chmod +x *.sh
```

### 3. Deploy Everything
```bash
bash deploy_complete.sh
```

### 4. Configure Backend
```bash
nano /home/ubuntu/Deco-vision/backend/.env
```

### 5. Restart Backend
```bash
sudo systemctl restart deco-vision-backend
```

### 6. Access Application
```
http://16.171.18.221
```

---

## Service Commands

### Backend
```bash
sudo systemctl start|stop|restart deco-vision-backend
sudo systemctl status deco-vision-backend
sudo journalctl -u deco-vision-backend -f
```

### Frontend (Nginx)
```bash
sudo systemctl start|stop|restart nginx
sudo systemctl status nginx
sudo tail -f /var/log/nginx/error.log
```

---

## Troubleshooting

### Check if services are running
```bash
sudo systemctl status deco-vision-backend
sudo systemctl status nginx
```

### View recent errors
```bash
sudo journalctl -u deco-vision-backend -n 50
sudo tail -n 50 /var/log/nginx/error.log
```

### Test connectivity
```bash
curl http://localhost/
curl http://localhost:8811/
```

---

## Deployment Scripts Created

1. **deploy_backend.sh** - Deploy backend only
2. **deploy_frontend.sh** - Deploy frontend only
3. **deploy_complete.sh** - Deploy both frontend and backend
4. **EC2_DEPLOYMENT_GUIDE.md** - Full deployment documentation
5. **.env.example** - Environment template

---

## Key Ports

- **80**: Frontend (Nginx)
- **8811**: Backend API (FastAPI)
- **22**: SSH access

---

## Important: After Deployment

1. Edit backend `.env` with your camera IP, username, and password
2. Restart backend: `sudo systemctl restart deco-vision-backend`
3. Access frontend at: http://16.171.18.221
4. Check logs if anything is wrong
