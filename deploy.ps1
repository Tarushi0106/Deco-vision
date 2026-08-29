# Deco Vision One-Command Deployment Script (Windows PowerShell)
# Usage: .\deploy.ps1

$EC2_IP = "13.53.133.110"
$EC2_USER = "ubuntu"
$PEM_KEY = "deco-vision-dewin.pem"

Write-Host "=========================================="
Write-Host "Deco Vision AWS EC2 Deployment"
Write-Host "=========================================="
Write-Host ""
Write-Host "📍 Instance: $EC2_IP"
Write-Host "🔑 Using PEM key: $PEM_KEY"
Write-Host ""

# Check if PEM key exists
if (-not (Test-Path $PEM_KEY)) {
    Write-Host "❌ Error: PEM key not found: $PEM_KEY" -ForegroundColor Red
    Write-Host "Please ensure $PEM_KEY is in the current directory"
    exit 1
}

Write-Host "✓ PEM key found"
Write-Host ""

# Check if SSH is available
try {
    $null = ssh -V
}
catch {
    Write-Host "❌ Error: OpenSSH is not installed or not in PATH"
    Write-Host "Please install OpenSSH or add it to your PATH"
    exit 1
}

# Step 1: Upload scripts to EC2
Write-Host "📤 Uploading deployment scripts to EC2..."
$files = @("deploy_backend.sh", "deploy_frontend.sh", "deploy_complete.sh", "health_check.sh")

foreach ($file in $files) {
    if (Test-Path $file) {
        scp -i $PEM_KEY -o StrictHostKeyChecking=no $file "${EC2_USER}@${EC2_IP}:/home/${EC2_USER}/Deco-vision/"
    }
    else {
        Write-Host "⚠️  Warning: $file not found"
    }
}

Write-Host "✓ Scripts uploaded successfully"
Write-Host ""

# Step 2: Clone repository on EC2
Write-Host "🔄 Cloning Deco Vision repository on EC2..."
ssh -i $PEM_KEY -o StrictHostKeyChecking=no ${EC2_USER}@${EC2_IP} @"
    cd /home/ubuntu
    if [ ! -d "Deco-vision" ]; then
        git clone https://github.com/Tarushi0106/Deco-vision.git
    else
        cd Deco-vision && git pull && cd ..
    fi
"@

Write-Host "✓ Repository ready"
Write-Host ""

# Step 3: Make scripts executable
Write-Host "🔧 Making deployment scripts executable..."
ssh -i $PEM_KEY -o StrictHostKeyChecking=no ${EC2_USER}@${EC2_IP} "chmod +x /home/ubuntu/Deco-vision/*.sh"

Write-Host "✓ Scripts are executable"
Write-Host ""

# Step 4: Run deployment
Write-Host "🚀 Starting deployment..."
Write-Host "This may take 10-15 minutes. Grab some coffee! ☕"
Write-Host ""

ssh -i $PEM_KEY -o StrictHostKeyChecking=no ${EC2_USER}@${EC2_IP} @"
    cd /home/ubuntu/Deco-vision
    bash deploy_complete.sh
"@

Write-Host ""
Write-Host "==========================================" -ForegroundColor Green
Write-Host "✅ DEPLOYMENT COMPLETED!" -ForegroundColor Green
Write-Host "==========================================" -ForegroundColor Green
Write-Host ""
Write-Host "🌐 Your application is ready!"
Write-Host ""
Write-Host "📍 Frontend URL: http://13.53.133.110" -ForegroundColor Cyan
Write-Host "🔌 Backend API: http://13.53.133.110/api" -ForegroundColor Cyan
Write-Host "🔗 WebSocket: ws://13.53.133.110/ws" -ForegroundColor Cyan
Write-Host ""
Write-Host "⚙️  Next Steps:"
Write-Host "1. SSH into your instance:"
Write-Host "   ssh -i $PEM_KEY ubuntu@13.53.133.110"
Write-Host ""
Write-Host "2. Configure your camera credentials:"
Write-Host "   nano /home/ubuntu/Deco-vision/backend/.env"
Write-Host ""
Write-Host "3. Restart backend with new configuration:"
Write-Host "   sudo systemctl restart deco-vision-backend"
Write-Host ""
Write-Host "4. Run health check:"
Write-Host "   bash /home/ubuntu/Deco-vision/health_check.sh"
Write-Host ""
Write-Host "=========================================="
