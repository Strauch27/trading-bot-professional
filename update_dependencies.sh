#!/bin/bash

# Trading Bot Professional - Update Dependencies Script
# Updates all installed packages to their latest versions

set -e

echo "=========================================="
echo "Updating Dependencies"
echo "=========================================="
echo ""

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

print_success() {
    echo -e "${GREEN}✓${NC} $1"
}

print_info() {
    echo -e "${YELLOW}ℹ${NC} $1"
}

# Check if venv exists
if [ ! -d "venv" ]; then
    echo "Error: Virtual environment not found!"
    echo "Please run ./setup.sh first"
    exit 1
fi

# Upgrade pip
print_info "Upgrading pip..."
./venv/bin/python3 -m pip install --upgrade pip

# Update all packages
print_info "Updating all packages..."
./venv/bin/python3 -m pip install --upgrade -r requirements.txt 2>/dev/null || {
    print_info "Updating packages individually..."
    ./venv/bin/python3 -m pip list --outdated --format=freeze | cut -d = -f 1 | xargs -n1 ./venv/bin/python3 -m pip install -U
}

print_success "All packages updated!"
echo ""
echo "To see installed versions:"
echo "  ./venv/bin/python3 -m pip list"
