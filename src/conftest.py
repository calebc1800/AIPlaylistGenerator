"""
pytest configuration helpers for the aiplaylist project.

This module ensures Django is configured even when Pytest runs outside the
standard manage.py entry point.
"""

import logging
import os
import sys
from pathlib import Path

import django
from django.conf import settings
from django.core.exceptions import AppRegistryNotReady, ImproperlyConfigured

LOGGER = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parent

# Add project root to Python path
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

KNOWN_SETTING_MODULES = (
    'aiplaylist.settings',
    'settings',
    'config.settings',
    'core.settings',
    'aiplaylist.settings.base',
    'aiplaylist.settings.development',
    'aiplaylist.settings.local',
)

def setup_django():
    """Setup Django configuration with fallback options for aiplaylist project."""

    # Possible settings modules to try for aiplaylist project
    configured = os.environ.get('DJANGO_SETTINGS_MODULE')
    settings_modules = []
    if configured:
        settings_modules.append(configured)
    settings_modules.extend(KNOWN_SETTING_MODULES)

    for settings_module in settings_modules:
        if not settings_module:
            continue
        try:
            os.environ['DJANGO_SETTINGS_MODULE'] = settings_module
            django.setup()
            LOGGER.info("Django configured with %s", settings_module)
            return True
        except ImportError as exc:
            LOGGER.debug("Unable to import %s: %s", settings_module, exc, exc_info=exc)
        except (ImproperlyConfigured, AppRegistryNotReady, RuntimeError) as exc:
            LOGGER.warning("Failed to setup Django with %s: %s", settings_module, exc, exc_info=exc)

    return False

# Setup Django when conftest is loaded
if not settings.configured:
    if not setup_django():
        LOGGER.error("Could not configure Django for the aiplaylist project. Tests may fail.")
        LOGGER.info(
            "Ensure the 'aiplaylist' module is importable and has proper __init__.py files."
        )

def pytest_configure(config):  # pylint: disable=unused-argument
    """Called after command line options have been parsed."""
    if not settings.configured:
        setup_django()


def pytest_collection_modifyitems(config, items):  # pylint: disable=unused-argument
    """Modify collected test items."""
    # Add Django DB marker to tests that need it
    for item in items:
        if "django" in str(item.fspath).lower() or "test_" in item.name:
            item.add_marker("django_db")
