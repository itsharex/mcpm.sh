"""
Router command for managing the MCPRouter daemon process
"""

import logging
import os
import secrets
import signal
import subprocess
import sys
import uuid

import click
import psutil
from rich.console import Console
from rich.prompt import Confirm

from mcpm.clients.client_registry import ClientRegistry
from mcpm.router.share import Tunnel
from mcpm.utils.config import ROUTER_SERVER_NAME, ConfigManager
from mcpm.utils.platform import get_log_directory, get_pid_directory

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)
console = Console()

APP_SUPPORT_DIR = get_pid_directory("mcpm")
APP_SUPPORT_DIR.mkdir(parents=True, exist_ok=True)
PID_FILE = APP_SUPPORT_DIR / "router.pid"
SHARE_CONFIG = APP_SUPPORT_DIR / "share.json"

LOG_DIR = get_log_directory("mcpm")
LOG_DIR.mkdir(parents=True, exist_ok=True)


def is_process_running(pid):
    """check if the process is running"""
    try:
        return psutil.pid_exists(pid)
    except Exception:
        return False


def read_pid_file():
    """read the pid file and return the process id, if the file does not exist or the process is not running, return None"""
    if not PID_FILE.exists():
        return None

    try:
        pid = int(PID_FILE.read_text().strip())
        if is_process_running(pid):
            return pid
        else:
            # if the process is not running, delete the pid file
            remove_pid_file()
            return None
    except (ValueError, IOError) as e:
        logger.error(f"Error reading PID file: {e}")
        return None


def write_pid_file(pid):
    """write the process id to the pid file"""
    try:
        PID_FILE.write_text(str(pid))
        logger.debug(f"PID {pid} written to {PID_FILE}")
    except IOError as e:
        logger.error(f"Error writing PID file: {e}")
        sys.exit(1)


def remove_pid_file():
    """remove the pid file"""
    try:
        PID_FILE.unlink(missing_ok=True)
    except IOError as e:
        logger.error(f"Error removing PID file: {e}")


@click.group(name="router")
@click.help_option("-h", "--help")
def router():
    """Manage MCP router service."""
    pass


@router.command(name="on")
@click.help_option("-h", "--help")
def start_router():
    """Start MCPRouter as a daemon process.

    Example:

    \b
        mcpm router on
    """
    # check if there is a router already running
    existing_pid = read_pid_file()
    if existing_pid:
        console.print(f"[bold red]Error:[/] MCPRouter is already running (PID: {existing_pid})")
        console.print("Use 'mcpm router off' to stop the running instance.")
        return

    # get router config
    config = ConfigManager().get_router_config()
    host = config["host"]
    port = config["port"]

    # prepare uvicorn command
    uvicorn_cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "mcpm.router.app:app",
        "--host",
        host,
        "--port",
        str(port),
        "--timeout-graceful-shutdown",
        "5",
    ]

    # start process
    try:
        # create log file
        log_file = LOG_DIR / "router_access.log"

        # open log file, prepare to redirect stdout and stderr
        with open(log_file, "a") as log:
            # use subprocess.Popen to start uvicorn
            process = subprocess.Popen(
                uvicorn_cmd,
                stdout=log,
                stderr=log,
                env=os.environ.copy(),
                start_new_session=True,  # create new session, so the process won't be affected by terminal closing
            )

        # record PID
        pid = process.pid
        write_pid_file(pid)

        console.print(f"[bold green]MCPRouter started[/] at http://{host}:{port} (PID: {pid})")
        console.print(f"Log file: {log_file}")
        console.print("Use 'mcpm router off' to stop the router.")

    except Exception as e:
        console.print(f"[bold red]Error:[/] Failed to start MCPRouter: {e}")


@router.command(name="set")
@click.option("-H", "--host", type=str, help="Host to bind the SSE server to")
@click.option("-p", "--port", type=int, help="Port to bind the SSE server to")
@click.option("-a", "--address", type=str, help="Remote address to share the router")
@click.help_option("-h", "--help")
def set_router_config(host, port, address):
    """Set MCPRouter global configuration.

    Example:
        mcpm router set -H localhost -p 8888
        mcpm router set --host 127.0.0.1 --port 9000
    """
    if not host and not port and not address:
        console.print(
            "[yellow]No changes were made. Please specify at least one option (--host, --port, or --address)[/]"
        )
        return

    # get current config, make sure all field are filled by default value if not exists
    config_manager = ConfigManager()
    current_config = config_manager.get_router_config()

    # if user does not specify a host, use current config
    host = host or current_config["host"]
    port = port or current_config["port"]
    share_address = address or current_config["share_address"]

    # save config
    if config_manager.save_router_config(host, port, share_address):
        console.print(
            f"[bold green]Router configuration updated:[/] host={host}, port={port}, share_address={share_address}"
        )
        console.print("The new configuration will be used next time you start the router.")

        # if router is running, prompt user to restart
        pid = read_pid_file()
        if pid:
            console.print("[yellow]Note: Router is currently running. Restart it to apply new settings:[/]")
            console.print("    mcpm router off")
            console.print("    mcpm router on")
    else:
        console.print("[bold red]Error:[/] Failed to save router configuration.")
        return

    if Confirm.ask("Do you want to update router for all clients now?"):
        active_profile = ClientRegistry.get_active_profile()
        if not active_profile:
            console.print("[yellow]No active profile found, skipped.[/]")
            return
        installed_clients = ClientRegistry.detect_installed_clients()
        for client, installed in installed_clients.items():
            if not installed:
                continue
            client_manager = ClientRegistry.get_client_manager(client)
            if client_manager is None:
                console.print(f"[yellow]Client '{client}' not found.[/] Skipping...")
                continue
            if client_manager.get_server(ROUTER_SERVER_NAME):
                console.print(f"[cyan]Updating profile router for {client}...[/]")
                client_manager.deactivate_profile()
                client_manager.activate_profile(active_profile, config_manager.get_router_config())
                console.print(f"[green]Profile router updated for {client}[/]")
        console.print("[bold green]Success: Profile router updated for all clients[/]")
        if pid:
            console.print("[bold yellow]Restart MCPRouter to apply new settings.[/]\n")


@router.command(name="off")
@click.help_option("-h", "--help")
def stop_router():
    """Stop the running MCPRouter daemon process.

    Example:

    \b
        mcpm router off
    """
    # check if there is a router already running
    pid = read_pid_file()
    if not pid:
        console.print("[yellow]MCPRouter is not running.[/]")
        return

    # send termination signal
    try:
        config_manager = ConfigManager()
        share_config = config_manager.read_share_config()
        if share_config.get("pid"):
            console.print("[green]Disabling share link...[/]")
            os.kill(share_config["pid"], signal.SIGTERM)
            config_manager.save_share_config(share_url=None, share_pid=None, api_key=None)
            console.print("[bold green]Share link disabled[/]")

        os.kill(pid, signal.SIGTERM)
        console.print(f"[bold green]MCPRouter stopped (PID: {pid})[/]")

        # delete PID file
        remove_pid_file()
    except OSError as e:
        console.print(f"[bold red]Error:[/] Failed to stop MCPRouter: {e}")

        # if process does not exist, clean up PID file
        if e.errno == 3:  # "No such process"
            console.print("[yellow]Process does not exist, cleaning up PID file...[/]")
            remove_pid_file()


@router.command(name="status")
@click.help_option("-h", "--help")
def router_status():
    """Check the status of the MCPRouter daemon process.

    Example:

    \b
        mcpm router status
    """
    # get router config
    config = ConfigManager().get_router_config()
    host = config["host"]
    port = config["port"]

    # check process status
    pid = read_pid_file()
    if pid:
        console.print(f"[bold green]MCPRouter is running[/] at http://{host}:{port} (PID: {pid})")
        share_config = ConfigManager().read_share_config()
        if share_config.get("pid"):
            console.print(f"[bold green]MCPRouter is sharing[/] at {share_config['url']} (PID: {share_config['pid']})")
    else:
        console.print("[yellow]MCPRouter is not running.[/]")


@router.command()
@click.help_option("-h", "--help")
@click.option("-a", "--address", type=str, required=False, help="Remote address to bind the tunnel to")
@click.option("-p", "--profile", type=str, required=False, help="Profile to share")
def share(address, profile):
    """Create a share link for the MCPRouter daemon process.

    Example:

    \b
        mcpm router share --address example.com:8877
    """

    # check if there is a router already running
    pid = read_pid_file()
    config_manager = ConfigManager()
    if not pid:
        console.print("[yellow]MCPRouter is not running.[/]")
        return

    if not profile:
        active_profile = ClientRegistry.get_active_profile()
        if not active_profile:
            console.print("[yellow]No active profile found. You need to specify a profile to share.[/]")

        console.print(f"[cyan]Sharing with active profile {active_profile}...[/]")
        profile = active_profile
    else:
        console.print(f"[cyan]Sharing with profile {profile}...[/]")

    # check if share link is already active
    share_config = config_manager.read_share_config()
    if share_config.get("pid"):
        console.print(f"[yellow]Share link is already active at {share_config['url']}.[/]")
        return

    # get share address
    if not address:
        console.print("[cyan]Using share address from config...[/]")
        config = config_manager.get_router_config()
        address = config["share_address"]

    # create share link
    remote_host, remote_port = address.split(":")

    # start tunnel
    # TODO: tls certificate if necessary
    tunnel = Tunnel(remote_host, remote_port, config["host"], config["port"], secrets.token_urlsafe(32), None)
    share_url = tunnel.start_tunnel()
    share_pid = tunnel.proc.pid if tunnel.proc else None
    # generate random api key
    api_key = str(uuid.uuid4())
    console.print(f"[bold green]Generated secret for share link: {api_key}[/]")
    # TODO: https is not supported yet
    share_url = share_url.replace("https://", "http://") + "/sse"
    # save share pid and link to config
    config_manager.save_share_config(share_url, share_pid, api_key)
    profile = profile or "<your_profile>"

    # print share link
    console.print(f"[bold green]Router is sharing at {share_url}[/]")
    console.print(f"[green]Your profile can be accessed with the url {share_url}?s={api_key}&profile={profile}[/]\n")
    console.print(
        "[bold yellow]Be careful about the share link, it will be exposed to the public. Make sure to share to trusted users only.[/]"
    )


@router.command("unshare")
@click.help_option("-h", "--help")
def stop_share():
    """Stop the share link for the MCPRouter daemon process."""
    # check if there is a share link already running
    config_manager = ConfigManager()
    share_config = config_manager.read_share_config()
    if not share_config["url"]:
        console.print("[yellow]No share link is active.[/]")
        return

    pid = share_config["pid"]
    if not pid:
        console.print("[yellow]No share link is active.[/]")
        return

    # send termination signal
    try:
        console.print(f"[bold yellow]Stopping share link at {share_config['url']} (PID: {pid})...[/]")
        os.kill(pid, signal.SIGTERM)
        console.print(f"[bold green]Share process stopped (PID: {pid})[/]")

        # delete share config
        config_manager.save_share_config(share_url=None, share_pid=None, api_key=None)
    except OSError as e:
        console.print(f"[bold red]Error:[/] Failed to stop share link: {e}")

        # if process does not exist, clean up share config
        if e.errno == 3:  # "No such process"
            console.print("[yellow]Share process does not exist, cleaning up share config...[/]")
            config_manager.save_share_config(share_url=None, share_pid=None, api_key=None)
    console.print("[bold green]Share link disabled[/]")
