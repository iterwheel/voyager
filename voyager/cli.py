"""CLI entry point: ``vyg`` command."""

from __future__ import annotations

import json

import typer
import uvicorn

app = typer.Typer(no_args_is_help=True)
bridge_app = typer.Typer(no_args_is_help=True)
countdown_app = typer.Typer(no_args_is_help=True)
app.add_typer(bridge_app, name="bridge")
app.add_typer(countdown_app, name="countdown")


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


@bridge_app.command("check-drift")
def check_drift(
    bridge_url: str = typer.Argument(..., help="Bridge URL (e.g. http://localhost:8787)."),
    repo: str = typer.Option("iterwheel/voyager", "--repo", help="GitHub repository (owner/name)."),
    git_token: str = typer.Option(
        "", "--git-token", envvar="GITHUB_TOKEN", help="GitHub API token."
    ),
    create_issue: bool = typer.Option(
        False, "--create-issue", help="Create a drift alert GitHub issue."
    ),
) -> None:
    """Check for deployed-version drift between the latest release tag
    and the /healthz endpoint of a running bridge."""
    import asyncio

    from voyager.core.drift_check import (
        check_drift,
        create_drift_alert_issue,
    )

    async def _run() -> None:
        if not git_token:
            typer.echo("ERROR: --git-token or GITHUB_TOKEN env var is required")
            raise typer.Exit(code=1)

        result = await check_drift(
            repo=repo,
            bridge_url=bridge_url,
            github_token=git_token,
        )

        typer.echo(f"latest_tag:       {result['latest_tag']}")
        typer.echo(f"deployed_version: {result['deployed_version']}")
        typer.echo(f"drifted:          {result['drifted']}")
        typer.echo(f"summary:          {result['summary']}")

        if not result["ok"] and result["drifted"] and create_issue:
            created = await create_drift_alert_issue(
                repo=repo,
                github_token=git_token,
                deployed_version=result["deployed_version"],
                latest_tag=result["latest_tag"],
            )
            if created:
                typer.echo(f"Created alert issue #{created.get('number')}")
            else:
                typer.echo("Alert issue already exists or could not be created")

    asyncio.run(_run())


@countdown_app.command("resolve-conversation")
def resolve_conversation(
    repo: str = typer.Option(..., "--repo", help="GitHub repository (owner/name); allowlisted."),
    pr: int = typer.Option(0, "--pr", help="Pull request number (resolve all its threads)."),
    thread_id: str = typer.Option("", "--thread-id", help="Single review thread node ID."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Report without issuing mutations."),
    as_json: bool = typer.Option(False, "--json", help="Emit redacted JSON summary."),
) -> None:
    """Resolve PR review conversations as the fixed machine account.

    Identity is fixed to iterwheel-countdown-user (token via gh, never printed).
    The only GraphQL mutation issued is resolveReviewThread.
    """
    from voyager.core.resolve_conversation import (
        RESOLVE_ALLOWED_REPOS,
        ResolveConversationError,
        make_github_gql,
        read_machine_token,
        resolve_conversations,
    )

    # Gate ALL usage errors (allowlist + target selection) BEFORE reading the
    # machine token: a bad invocation must never touch the credential store, which
    # would otherwise mask a usage error as an auth failure on gh-less hosts.
    if repo not in RESOLVE_ALLOWED_REPOS:
        typer.echo(f"ERROR: repo {repo!r} is not in the resolve allowlist")
        raise typer.Exit(code=1)
    pr_val = pr or None
    thread_val = thread_id or None
    if (pr_val is None) == (thread_val is None):
        typer.echo("ERROR: provide exactly one of --pr or --thread-id")
        raise typer.Exit(code=1)

    try:
        token = read_machine_token()
        gql = make_github_gql(token)
        summary = resolve_conversations(
            repo=repo,
            pr=pr_val,
            thread_id=thread_val,
            dry_run=dry_run,
            gql=gql,
        )
    except ResolveConversationError as exc:
        typer.echo(f"ERROR: {exc}")
        raise typer.Exit(code=1) from exc

    public = summary.to_public_dict()
    if as_json:
        typer.echo(json.dumps(public))
    else:
        typer.echo(f"repo:     {public['repo']}")
        typer.echo(f"resolved: {public['resolved']}")
        typer.echo(f"skipped:  {public['skipped']}")
        typer.echo(f"dry_run:  {public['dry_run']}")


@countdown_app.command("resolve-loop")
def resolve_loop(
    repos: str = typer.Option(..., "--repos", help="Path to an OWNER/REPO-per-line file."),
    max_resolves: int = typer.Option(20, "--max-resolves", help="Cap on resolves per run."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Enumerate + judge; issue no mutation."),
    as_json: bool = typer.Option(False, "--json", help="Emit the redacted JSON summary."),
) -> None:
    """Resolve review conversations across allowlisted repos as the machine account.

    Enumerates open PRs, deterministically prefilters resolvable threads, applies a
    fail-closed LLM should-resolve gate, and resolves only approved threads — under a
    single-instance lock and a max-resolves cap. Identity is fixed to
    iterwheel-countdown-user (token via gh, never printed).
    """
    from pathlib import Path

    from voyager.core.countdown_gate import build_gate_from_env
    from voyager.core.countdown_loop import (
        AlreadyRunningError,
        load_repo_list,
        make_read_gql,
        run_resolve_loop,
        single_instance_lock,
    )
    from voyager.core.resolve_conversation import (
        ResolveConversationError,
        make_github_gql,
        read_machine_token,
    )

    try:
        requested = load_repo_list(Path(repos))
        token = read_machine_token()
        gate = build_gate_from_env()
        try:
            read_gql = make_read_gql(token)
            resolve_gql = make_github_gql(token)
            with single_instance_lock():
                summary = run_resolve_loop(
                    requested_repos=requested,
                    gate=gate,
                    read_gql=read_gql,
                    resolve_gql=resolve_gql,
                    max_resolves=max_resolves,
                    dry_run=dry_run,
                )
        finally:
            gate.close()
    except AlreadyRunningError as exc:
        typer.echo(f"ERROR: {exc}")
        raise typer.Exit(code=1) from exc
    except (ResolveConversationError, OSError, ValueError, RuntimeError) as exc:
        typer.echo(f"ERROR: {exc}")
        raise typer.Exit(code=1) from exc

    public = summary.to_public_dict()
    if as_json:
        typer.echo(json.dumps(public))
    else:
        typer.echo(f"repos_scanned: {len(public['repos_scanned'])}")
        typer.echo(f"repos_skipped: {public['repos_skipped']}")
        typer.echo(f"prs_scanned:   {public['prs_scanned']}")
        typer.echo(f"resolved:      {public['resolved']}")
        typer.echo(f"would_resolve: {public['would_resolve']}")
        typer.echo(f"capped:        {public['capped']}")
        typer.echo(f"dry_run:       {public['dry_run']}")
        typer.echo(f"errors:        {len(public['errors'])}")
    if summary.systemic_failure:
        # stderr, so --json stdout stays pure JSON exactly when it matters most.
        typer.echo("ERROR: systemic failure — no repo/PR could be enumerated", err=True)
        raise typer.Exit(code=1)


def main() -> None:
    """Entry point for the ``vyg`` console script."""
    app()


if __name__ == "__main__":
    main()
