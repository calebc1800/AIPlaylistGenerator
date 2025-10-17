"""
conftest.py - pytest configuration for aiplaylist Django project

This file helps pytest-django find and configure the Django project
even when there are import path issues.
"""

import os
import sys
import django
from django.conf import settings
from pathlib import Path

# Add project root to Python path
project_root = Path(__file__).parent.absolute()
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# Try to configure Django settings
def setup_django():
    """Setup Django configuration with fallback options for aiplaylist project."""

    # Possible settings modules to try for aiplaylist project
    settings_modules = [
        os.environ.get('DJANGO_SETTINGS_MODULE'),  # From environment
        'aiplaylist.settings',                     # Main project settings
        'settings',                                # Direct import
        'config.settings',                         # Config layout
        'core.settings',                          # Core layout
        'aiplaylist.settings.base',               # Split settings - base
        'aiplaylist.settings.development',        # Split settings - dev
        'aiplaylist.settings.local',              # Split settings - local
    ]

    # Remove None values
    settings_modules = [s for s in settings_modules if s]

    for settings_module in settings_modules:
        try:
            os.environ['DJANGO_SETTINGS_MODULE'] = settings_module
            django.setup()
            print(f"✅ Django configured with: {settings_module}")
            return True
        except ImportError as e:
            print(f"❌ Failed to import {settings_module}: {e}")
            continue
        except Exception as e:
            print(f"❌ Failed to setup Django with {settings_module}: {e}")
            continue

    return False

# Setup Django when conftest is loaded
if not settings.configured:
    if not setup_django():
        print("⚠️  Warning: Could not configure Django for aiplaylist project. Tests may fail.")
        print("💡 Make sure 'aiplaylist' module is importable and has proper __init__.py files")

def pytest_configure(config):
    """Called after command line options have been parsed."""
    # Additional Django configuration if needed
    pass

def pytest_collection_modifyitems(config, items):
    """Modify collected test items."""
    # Add Django DB marker to tests that need it
    for item in items:
        if "django" in str(item.fspath).lower() or "test_" in item.name:
            item.add_marker("django_db")
