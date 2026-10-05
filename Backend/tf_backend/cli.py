import typer

app = typer.Typer(no_args_is_help=True, help="Trend Finder command line")


@app.callback()
def main() -> None:
    """Trend Finder command line."""


@app.command()
def version() -> None:
    """Print the app version."""
    from tf_backend import __version__

    typer.echo(__version__)
