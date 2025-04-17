"""
Configuration utilities for MCPM
"""

import json
import logging
import os
from typing import Any, Dict

logger = logging.getLogger(__name__)

# Default configuration paths
DEFAULT_CONFIG_DIR = os.path.expanduser("~/.config/mcpm")
DEFAULT_CONFIG_FILE = os.path.join(DEFAULT_CONFIG_DIR, "config.json")
# default router config
DEFAULT_HOST = "localhost"
DEFAULT_PORT = 6276  # 6276 represents MCPM on a T9 keypad (6=M, 2=C, 7=P, 6=M)
ROUTER_SERVER_NAME = "mcpm_router"
# default splitor pattern
TOOL_SPLITOR = "_t_"
RESOURCE_SPLITOR = ":"
RESOURCE_TEMPLATE_SPLITOR = ":"
PROMPT_SPLITOR = "_p_"


class ConfigManager:
    """Manages MCP basic configuration

    Note: This class only manages basic system configuration.
    Client-specific configurations are managed by ClientConfigManager.
    """

    def __init__(self, config_path: str = DEFAULT_CONFIG_FILE):
        self.config_path = config_path
        self.config_dir = os.path.dirname(config_path)
        self._config = None
        self._ensure_dirs()
        self._load_config()

    def _ensure_dirs(self) -> None:
        """Ensure all configuration directories exist"""
        os.makedirs(self.config_dir, exist_ok=True)

    def _load_config(self) -> None:
        """Load configuration from file or create default"""
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r") as f:
                    self._config = json.load(f)
            except json.JSONDecodeError:
                logger.error(f"Error parsing config file: {self.config_path}")
                self._config = self._default_config()
        else:
            self._config = self._default_config()
            self._save_config()

    def _default_config(self) -> Dict[str, Any]:
        """Create default configuration"""
        # Return empty config - don't set any defaults
        return {}

    def _save_config(self) -> None:
        """Save current configuration to file"""
        with open(self.config_path, "w") as f:
            json.dump(self._config, f, indent=2)

    def get_config(self) -> Dict[str, Any]:
        """Get the complete configuration"""
        return self._config

    def set_config(self, key: str, value: Any) -> bool:
        """Set a configuration value and persist to file

        Args:
            key: Configuration key to set
            value: Value to set for the key (must be JSON serializable)

        Returns:
            bool: Success or failure
        """
        try:
            if value is None and key in self._config:
                # Remove the key if value is None
                del self._config[key]
            else:
                # Set the key to the provided value
                self._config[key] = value

            # Save the updated configuration
            self._save_config()
            return True
        except Exception as e:
            logger.error(f"Error setting configuration {key}: {str(e)}")
            return False

    def get_router_config(self):
        """get router configuration from config file, if not exists, flush default config"""
        config = self.get_config()

        # check if router config exists
        if "router" not in config:
            # create default config and save
            router_config = {"host": DEFAULT_HOST, "port": DEFAULT_PORT}
            self.set_config("router", router_config)
            return router_config

        # get existing config
        router_config = config.get("router", {})

        # check if host and port exist, if not, set default values and update config
        # user may only set a customized port while leave host undefined
        updated = False
        if "host" not in router_config:
            router_config["host"] = DEFAULT_HOST
            updated = True
        if "port" not in router_config:
            router_config["port"] = DEFAULT_PORT
            updated = True

        # save config if updated
        if updated:
            self.set_config("router", router_config)

        return router_config

    def save_router_config(self, host, port):
        """save router configuration to config file"""
        router_config = self.get_config().get("router", {})

        # update config
        router_config["host"] = host
        router_config["port"] = port

        # save config
        return self.set_config("router", router_config)
