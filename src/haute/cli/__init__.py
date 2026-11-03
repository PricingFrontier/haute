"""CLI entrypoint for Haute."""

# Haute's Polars settings must be in the environment before Polars is first
# imported, so this import stays first.
import haute.cli._polars_startup  # noqa: F401

# isort: split

from pathlib import Path

import click

from haute._cpu_performance import configure_process_high_qos
from haute._project_context import ProjectContext, installed_project, project_scope
from haute.cli._app import app
from haute.cli._ci import ci
from haute.cli._deploy import deploy
from haute.cli._gpu_setup import gpu_setup
from haute.cli._impact import impact
from haute.cli._init_cmd import init
from haute.cli._lint import lint
from haute.cli._output import utf8_output
from haute.cli._run import run
from haute.cli._serve import serve
from haute.cli._smoke import smoke
from haute.cli._status import status
from haute.cli._train import train


@click.group()
@click.version_option(package_name="haute")
@click.pass_context
def cli(ctx: click.Context) -> None:
    """Haute - Open-source pricing engine for insurance teams"""
    utf8_output()
    configure_process_high_qos()
    # The CLI is an entry point: a command works in the shell's working directory, read
    # once here, unless its caller installed a project already. A command given a
    # pipeline file runs in that file's project instead.
    installed = installed_project()
    ctx.with_resource(
        project_scope(installed if installed is not None else ProjectContext.at(Path.cwd()))
    )


cli.add_command(app)
cli.add_command(ci)
cli.add_command(deploy)
cli.add_command(gpu_setup)
cli.add_command(impact)
cli.add_command(init)
cli.add_command(lint)
cli.add_command(run)
cli.add_command(serve)
cli.add_command(smoke)
cli.add_command(status)
cli.add_command(train)
