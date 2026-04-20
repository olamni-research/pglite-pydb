# 🚀 pglite-pydb Development Commands
# ================================
#
# Vite-style convenience commands for development

# Define Python command using uv
PYTHON_CMD := uv run python

.PHONY: help dev test examples lint quick clean install

# Default target
help:
	@echo "🚀 pglite-pydb Development Commands"
	@echo "================================"
	@echo ""
	@echo "Core Commands:"
	@echo "  make dev         Run full development workflow (like CI)"
	@echo "  make test        Run tests only"
	@echo "  make examples    Run examples only"
	@echo "  make lint        Run linting only"
	@echo "  make quick       Quick checks for development"
	@echo ""
	@echo "Utility Commands:"
	@echo "  make install     Install in development mode"
	@echo "  make clean       Clean build artifacts"
	@echo "  make fmt         Auto-fix formatting"
	@echo ""
	@echo "Example Usage:"
	@echo "  make dev         # Full workflow (linting + tests + examples)"
	@echo "  make quick       # Quick checks during development"
	@echo "  make test        # Just run the test suite"

# Full development workflow (mirrors CI exactly)
dev: | install lint examples test

# Run tests only
test:
	@echo "🧪 Running test suite..."
	uv run pytest tests/

# Run examples only
examples:
	@echo "📚 Running examples..."
	uv run pytest examples/

# Run linting only
lint:
	@echo "🎨 Running linting checks..."
	uv run pre-commit run --all-files

# Quick checks for development
quick: | install lint
	@echo "⚡ Running quick development checks..."
	$(PYTHON_CMD) -c "import pglite_pydb"
	$(PYTHON_CMD) -c "from pglite_pydb import PGliteManager, PGliteConfig"
	$(PYTHON_CMD) -c "print('✅ All imports working')"

# Install in development mode
install:
	@echo "📦 Installing in development mode..."
	uv sync

# Auto-fix formatting
fmt:
	@echo "🎨 Auto-fixing formatting..."
	uv run ruff format
	@echo "✅ Formatting complete!"

# Clean build artifacts
clean:
	@echo "🧹 Cleaning build artifacts..."
	rm -rf build/
	rm -rf dist/
	rm -rf *.egg-info/
	rm -rf .pytest_cache/
	rm -rf .mypy_cache/
	rm -rf .ruff_cache/
	find src/ tests/ examples/ -type d -name __pycache__ -exec rm -rf {} +
	find src/ tests/ examples/ -type f -name "*.pyc" -exec rm -rf {} +
	@echo "✅ Cleanup complete!"

# Show project status
status:
	@echo "📊 Project Status"
	@echo "================"
	@echo "Python version: $(shell $(PYTHON_CMD) --version)"
	@echo "Quick test:"
	@$(PYTHON_CMD) -c "import pglite_pydb; print(f'pglite-pydb {pglite_pydb.__version__} ready!')" 2>/dev/null || echo "pglite-pydb not installed in dev mode"
