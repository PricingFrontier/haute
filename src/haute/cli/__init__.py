"""CLI entrypoint for Haute."""

# Haute's Polars settings must be in the environment before Polars is first
# imported, so this import stays first.
import haute.cli._polars_startup  # noqa: F401

# isort: split

import errno
from pathlib import Path

import click

from haute._cpu_performance import configure_process_high_qos
from haute._project_context import ProjectContext, installed_project, project_scope
from haute.cli._app import app
from haute.cli._ci import ci
from haute.cli._deploy import deploy
from haute.cli._gpu_setup import gpu_setup
from haute.cli._helpers import answer_command_failure
from haute.cli._impact import impact
from haute.cli._init_cmd import init
from haute.cli._lint import lint
from haute.cli._output import utf8_output
from haute.cli._run import run
from haute.cli._serve import serve
from haute.cli._smoke import smoke
from haute.cli._status import status
from haute.cli._train import train


class HauteGroup(click.Group):
    """The ``haute`` command group: the command line's one edge for failures.

    Whatever a command raises that Click does not answer itself (its usage errors, an
    abort, an exit) ends the command here in one line and exit 1, through
    :func:`haute.cli._helpers.answer_command_failure`; no command ends in a traceback.
    """

    def invoke(self, ctx: click.Context) -> object:
        try:
            return super().invoke(ctx)
        except (click.ClickException, click.exceptions.Abort, click.exceptions.Exit):
            raise
        except OSError as exc:
            if exc.errno == errno.EPIPE:
                # A reader that stopped reading (``| head``): Click ends that quietly.
                raise
            answer_command_failure(exc, command=ctx.invoked_subcommand or "haute")
        except Exception as exc:
            answer_command_failure(exc, command=ctx.invoked_subcommand or "haute")


@click.group(cls=HauteGroup)
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
