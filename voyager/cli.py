"""CLI entry point: ``vyg`` command."""

from __future__ import annotations

import typer
import uvicorn

app = typer.Typer(no_args_is_help=True)
bridge_app = typer.Typer(no_args_is_help=True)
app.add_typer(bridge_app, name="bridge")


@app.command("version")
def version() -> None:
    """Print Voyager version and build commit."""
    from voyager.build_info import BUILD_COMMIT, VERSION

    typer.echo(f"version: {VERSION}")
    typer.echo(f"build_commit: {BUILD_COMMIT}")


@bridge_app.command("serve")
def serve(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind host."),
    port: int = typer.Option(8787, "--port", help="Bind port."),
    log_level: str = typer.Option("info", "--log-level", help="Uvicorn log level."),
) -> None:
    """Start the bridge HTTP server via uvicorn."""
    uvicorn.run("voyager.server:app", host=host, port=port, log_level=log_level)


def main() -> None:
    """Entry point for the ``vyg`` console script."""
    app()


if __name__ == "__main__":
    main()
