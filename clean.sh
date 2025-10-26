#!/bin/bash

# Trading Bot Professional - Cleanup Script
# Removes virtual environment and cached files

echo "=========================================="
echo "Cleanup Script"
echo "=========================================="
echo ""

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

print_success() {
    echo -e "${GREEN}✓${NC} $1"
}

print_info() {
    echo -e "${YELLOW}ℹ${NC} $1"
}

read -p "This will remove the virtual environment and cache. Continue? (y/N) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Cancelled."
    exit 1
fi

# Remove virtual environment
if [ -d "venv" ]; then
    print_info "Removing virtual environment..."
    rm -rf venv
    print_success "Virtual environment removed"
fi

# Remove Python cache
if [ -d "__pycache__" ]; then
    print_info "Removing Python cache..."
    find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
    find . -type f -name "*.pyc" -delete 2>/dev/null || true
    print_success "Python cache removed"
fi

# Remove activation script
if [ -f "activate.sh" ]; then
    rm -f activate.sh
    print_success "Activation script removed"
fi

echo ""
print_success "Cleanup complete!"
echo ""
echo "To reinstall, run:"
echo "  ./setup.sh"
