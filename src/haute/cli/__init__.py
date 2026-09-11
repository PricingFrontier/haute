"""CLI entrypoint for Haute."""

import click

from haute._cpu_performance import configure_process_high_qos
from haute.cli._deploy import deploy
from haute.cli._impact import impact
from haute.cli._init_cmd import init
from haute.cli._lint import lint
from haute.cli._run import run
from haute.cli._serve import serve
from haute.cli._smoke import smoke
from haute.cli._status import status
from haute.cli._train import train


@click.group()
@click.version_option(package_name="haute")
def cli() -> None:
    """Haute - Open-source pricing engine for insurance teams on Databricks."""
    configure_process_high_qos()


cli.add_command(deploy)
cli.add_command(impact)
cli.add_command(init)
cli.add_command(lint)
cli.add_command(run)
cli.add_command(serve)
cli.add_command(smoke)
cli.add_command(status)
cli.add_command(train)
