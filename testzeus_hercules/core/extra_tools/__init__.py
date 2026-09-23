import importlib
import pkgutil
import sys
from pathlib import Path

from testzeus_hercules.config import get_global_conf
from testzeus_hercules.utils.logger import logger

# Get the current directory path
package_path = Path(__file__).parent

if get_global_conf().get_load_extra_tools().lower().strip() != "false":
    # spec-r3 §5.1 (E1): optional subset allowlist (EXTRA_TOOLS_MODULES, csv of module names).
    # Empty/unset = full load = the r2 behaviour; a non-empty list imports only those modules.
    allow_raw = get_global_conf().get_extra_tools_modules() or ""
    allow = {name.strip() for name in allow_raw.split(",") if name.strip()}
    # Dynamically import all modules
    for _, module_name, _ in pkgutil.iter_modules([str(package_path)]):
        if allow and module_name not in allow:
            logger.info("[EXTRA_TOOLS] skipping module %s (not in EXTRA_TOOLS_MODULES allowlist)", module_name)
            continue
        # Construct the full module path
        full_module_name = f"testzeus_hercules.core.extra_tools.{module_name}"
        # Import the module
        module = importlib.import_module(full_module_name)
        # Add all objects from the module to the current namespace
        for attribute_name in dir(module):
            # Skip private attributes
            if not attribute_name.startswith("_"):
                globals()[attribute_name] = getattr(module, attribute_name)
