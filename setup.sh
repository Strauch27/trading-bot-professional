#!/bin/bash

# Trading Bot Professional - Automated Setup Script
# This script automates the installation of all dependencies and configuration

set -e  # Exit on error

echo "=========================================="
echo "Trading Bot Professional - Setup"
echo "=========================================="
echo ""

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Function to print colored output
print_success() {
    echo -e "${GREEN}✓${NC} $1"
}

print_error() {
    echo -e "${RED}✗${NC} $1"
}

print_info() {
    echo -e "${YELLOW}ℹ${NC} $1"
}

# Step 1: Check for Python 3.12+
echo "Step 1: Checking Python version..."
PYTHON_CMD=""

# Try different Python commands
for cmd in python3.14 python3.13 python3.12 python3 /opt/homebrew/bin/python3; do
    if command -v $cmd &> /dev/null; then
        VERSION=$($cmd --version 2>&1 | awk '{print $2}')
        MAJOR=$(echo $VERSION | cut -d. -f1)
        MINOR=$(echo $VERSION | cut -d. -f2)

        if [ "$MAJOR" -eq 3 ] && [ "$MINOR" -ge 12 ]; then
            PYTHON_CMD=$cmd
            print_success "Found Python $VERSION at $cmd"
            break
        fi
    fi
done

if [ -z "$PYTHON_CMD" ]; then
    print_error "Python 3.12 or higher is required!"
    print_info "Please install Python 3.12+ via Homebrew:"
    echo "  brew install python@3.14"
    exit 1
fi

# Step 2: Remove old virtual environment if it exists
if [ -d "venv" ]; then
    print_info "Removing old virtual environment..."
    rm -rf venv
fi

# Step 3: Create virtual environment
echo ""
echo "Step 2: Creating virtual environment..."
$PYTHON_CMD -m venv venv
print_success "Virtual environment created"

# Step 4: Upgrade pip
echo ""
echo "Step 3: Upgrading pip..."
./venv/bin/python3 -m pip install --upgrade pip --quiet
print_success "pip upgraded to latest version"

# Step 5: Install dependencies
echo ""
echo "Step 4: Installing dependencies..."
print_info "This may take a few minutes..."

# Install packages one by one to handle errors better
PACKAGES=(
    "ccxt>=4.0.0"
    "pydantic>=2.0.0"
    "rich>=13.0.0"
    "colorlog>=6.0.0"
    "python-json-logger>=4.0.0"
    "Flask>=3.0.0"
    "Flask-SocketIO>=5.0.0"
    "python-socketio>=5.0.0"
    "pandas>=2.0.0"
    "numpy>=1.24.0"
    "ta>=0.11.0"
    "python-dotenv>=1.0.0"
    "PyYAML>=6.0.0"
    "psutil>=5.0.0"
    "requests>=2.31.0"
    "aiohttp>=3.9.0"
)

./venv/bin/python3 -m pip install "${PACKAGES[@]}" --quiet

print_success "All core dependencies installed"

# Try to install pandas-ta (may fail on Python 3.14+)
echo ""
print_info "Attempting to install pandas-ta (optional)..."
if ./venv/bin/python3 -m pip install "pandas-ta>=0.4.67b0" --quiet 2>/dev/null; then
    print_success "pandas-ta installed successfully"
else
    print_info "pandas-ta could not be installed (requires specific Python version)"
    print_info "The bot will still work without it"
fi

# Step 6: Create VS Code settings
echo ""
echo "Step 5: Configuring VS Code..."
mkdir -p .vscode

cat > .vscode/settings.json << 'EOF'
{
  "python.defaultInterpreterPath": "${workspaceFolder}/venv/bin/python3"
}
EOF

print_success "VS Code settings created"

# Step 7: Verify installation
echo ""
echo "Step 6: Verifying installation..."
if ./venv/bin/python3 -c "import ccxt, requests, pandas, numpy" 2>/dev/null; then
    print_success "All critical imports verified"
else
    print_error "Import verification failed"
    exit 1
fi

# Step 8: Create activation helper script
cat > activate.sh << 'EOF'
#!/bin/bash
# Quick activation script for the virtual environment
source venv/bin/activate
echo "Virtual environment activated!"
echo "Python: $(which python3)"
echo "To deactivate, run: deactivate"
EOF
chmod +x activate.sh

# Final summary
echo ""
echo "=========================================="
echo "Setup Complete!"
echo "=========================================="
echo ""
print_success "Virtual environment: ./venv"
print_success "Python version: $($PYTHON_CMD --version)"
print_success "VS Code configured: .vscode/settings.json"
echo ""
echo "To run the bot:"
echo "  ./venv/bin/python3 main.py"
echo ""
echo "Or activate the virtual environment:"
echo "  source activate.sh"
echo "  python3 main.py"
echo ""
echo "To check installed packages:"
echo "  ./venv/bin/python3 -m pip list"
echo ""
