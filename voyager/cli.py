"""CLI entry point: ``vyg`` command."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

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


@countdown_app.command("review-thread-diagnostic")
def review_thread_diagnostic(
    repo: str = typer.Option(..., "--repo", help="GitHub repository (owner/name)."),
    pr: int = typer.Option(..., "--pr", min=1, help="Pull request number."),
    thread_ids: list[str] = typer.Option(
        ...,
        "--thread-id",
        "-t",
        help="PullRequestReviewThread node ID. Repeat for multiple threads.",
    ),
    app_slug: str = typer.Option(
        "iterwheel-countdown",
        "--app",
        help="GitHub App slug to use for the diagnostic.",
    ),
    config: Path | None = typer.Option(
        None,
        "--config",
        help="Voyager config path. Defaults to VOYAGER_CONFIG_PATH/search order.",
    ),
    resolve: bool = typer.Option(
        False,
        "--resolve",
        help="Run a controlled resolveReviewThread canary after capability checks.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """Query Countdown review-thread resolver capability, optionally resolving canary threads."""
    import asyncio

    from voyager.core.config import load_config
    from voyager.core.countdown_diagnostic import (
        ReviewThreadCapabilityReport,
        ReviewThreadResolveCanaryReport,
        query_review_thread_capabilities,
        run_review_thread_resolve_canary,
    )
    from voyager.core.github_app import GitHubAppClient

    cfg = load_config(config)
    if app_slug not in cfg.apps:
        typer.echo(f"ERROR: app {app_slug!r} is not configured", err=True)
        raise typer.Exit(code=1)

    async def _run() -> ReviewThreadCapabilityReport | ReviewThreadResolveCanaryReport:
        client = GitHubAppClient(cfg.apps)
        try:
            if resolve:
                return await run_review_thread_resolve_canary(
                    client,
                    app_slug=app_slug,
                    repository=repo,
                    pr=pr,
                    thread_ids=thread_ids,
                )
            return await query_review_thread_capabilities(
                client,
                app_slug=app_slug,
                repository=repo,
                pr=pr,
                thread_ids=thread_ids,
            )
        finally:
            await client.aclose()

    result = asyncio.run(_run())
    public_result: dict[str, Any] = result.to_public_dict()
    if json_output:
        typer.echo(json.dumps(public_result, indent=2, sort_keys=True))
        return

    if resolve:
        typer.echo("Countdown review-thread resolve canary")
        before = public_result["before"]
        after = public_result["after"]
        typer.echo(f"actor: {before['actor_login']}")
        typer.echo(f"repo: {before['repo']}#{before['pr']}")
        typer.echo("before:")
        _echo_thread_capabilities(before["threads"])
        typer.echo("operations:")
        for operation in public_result["operations"]:
            reason = operation["reason"] or "resolved"
            typer.echo(
                f"- {operation['thread_id']}: applied={operation['applied']} reason={reason} "
                f"resolvedBy={operation['resolvedBy']}"
            )
        typer.echo("after:")
        _echo_thread_capabilities(after["threads"])
        return

    typer.echo("Countdown review-thread capability diagnostic")
    typer.echo(f"actor: {public_result['actor_login']}")
    typer.echo(f"repo: {public_result['repo']}#{public_result['pr']}")
    _echo_thread_capabilities(public_result["threads"])


def _echo_thread_capabilities(threads: list[dict[str, Any]]) -> None:
    for thread in threads:
        typer.echo(
            f"- {thread['thread_id']}: repo={thread['repo']} pr={thread['pr']} "
            f"isResolved={thread['isResolved']} isOutdated={thread['isOutdated']} "
            f"viewerCanResolve={thread['viewerCanResolve']} "
            f"viewerCanReply={thread['viewerCanReply']} error={thread['error']}"
        )


@countdown_app.command("user-device-code")
def user_device_code(
    client_id: str = typer.Option(..., "--client-id", help="GitHub App client ID."),
    store_refresh_token_command: str = typer.Option(
        ...,
        "--store-refresh-token-command",
        help=(
            "Command that receives the first refresh token on stdin. "
            "The command is split with shlex and is not run through a shell."
        ),
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """Start a GitHub App user-to-server device flow without printing token material."""
    import asyncio
    import time

    from voyager.core.github_app_user_auth import exchange_device_code, request_device_code

    async def _run() -> dict[str, Any]:
        response = await request_device_code(client_id)
        public_result = response.to_public_dict()
        if json_output:
            typer.echo(json.dumps(public_result, indent=2, sort_keys=True))
        else:
            typer.echo("Countdown GitHub App user authorization")
            typer.echo(f"verification_uri: {public_result['verification_uri']}")
            typer.echo(f"user_code: {public_result['user_code']}")
            typer.echo(f"expires_in: {public_result['expires_in']}")
            typer.echo(f"poll_interval: {public_result['interval']}")
            typer.echo("device_code: [redacted]")

        deadline = time.monotonic() + response.expires_in
        interval = response.interval
        while True:
            try:
                token_response = await exchange_device_code(client_id, response.device_code)
                break
            except RuntimeError as exc:
                message = str(exc)
                if "slow_down" in message:
                    interval += 5
                elif "authorization_pending" not in message:
                    raise
                if time.monotonic() + interval >= deadline:
                    raise RuntimeError("GitHub device authorization expired") from exc
                await asyncio.sleep(interval)

        _store_refresh_token(store_refresh_token_command, token_response.refresh_token)
        result = token_response.to_public_dict()
        result["refresh_token_stored"] = bool(token_response.refresh_token)
        return result

    public_result = asyncio.run(_run())
    if json_output:
        return

    typer.echo(f"token_type: {public_result['token_type']}")
    typer.echo(f"expires_in: {public_result['expires_in']}")
    typer.echo(f"refresh_token_present: {public_result['refresh_token_present']}")
    typer.echo(f"refresh_token_expires_in: {public_result['refresh_token_expires_in']}")
    typer.echo(f"refresh_token_stored: {public_result['refresh_token_stored']}")


@countdown_app.command("user-refresh-check")
def user_refresh_check(
    client_id: str = typer.Option(..., "--client-id", help="GitHub App client ID."),
    refresh_token_env: str = typer.Option(
        "VOYAGER_COUNTDOWN_REFRESH_TOKEN",
        "--refresh-token-env",
        help="Environment variable containing the current refresh token.",
    ),
    check_viewer: bool = typer.Option(
        False,
        "--check-viewer",
        help="Query GraphQL viewer.login with the refreshed access token.",
    ),
    store_refresh_token_command: str | None = typer.Option(
        None,
        "--store-refresh-token-command",
        help=(
            "Optional command that receives the replacement refresh token on stdin. "
            "The command is split with shlex and is not run through a shell."
        ),
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """Refresh a GitHub App user token and report only non-secret metadata."""
    import asyncio

    from voyager.core.github_app_user_auth import query_viewer_login, refresh_user_access_token

    refresh_token = os.environ.get(refresh_token_env)
    if not refresh_token:
        typer.echo(f"ERROR: {refresh_token_env} is not set", err=True)
        raise typer.Exit(code=1)
    if not store_refresh_token_command:
        typer.echo("ERROR: --store-refresh-token-command is required", err=True)
        raise typer.Exit(code=1)

    async def _run() -> dict[str, Any]:
        response = await refresh_user_access_token(client_id, refresh_token)
        _store_refresh_token(store_refresh_token_command, response.refresh_token)
        stored_replacement = bool(response.refresh_token)
        redacted = "[" + "redacted" + "]"
        result = response.to_public_dict()
        result["replacement_refresh_token_must_be_stored"] = bool(response.refresh_token)
        result["replacement_refresh_token_stored"] = stored_replacement
        result["access_token"] = redacted
        result["refresh_token"] = redacted if response.refresh_token else None
        if check_viewer:
            viewer_login = await query_viewer_login(response.access_token)
            result["viewer_login_present"] = bool(viewer_login)
            result["viewer_login"] = redacted
        return result

    public_result = asyncio.run(_run())
    if json_output:
        typer.echo(json.dumps(public_result, indent=2, sort_keys=True))
        return

    typer.echo("Countdown GitHub App user refresh check")
    typer.echo(f"token_type: {public_result['token_type']}")
    typer.echo(f"expires_in: {public_result['expires_in']}")
    typer.echo(f"refresh_token_present: {public_result['refresh_token_present']}")
    typer.echo(f"refresh_token_expires_in: {public_result['refresh_token_expires_in']}")
    typer.echo(
        "replacement_refresh_token_must_be_stored: "
        f"{public_result['replacement_refresh_token_must_be_stored']}"
    )
    typer.echo(
        f"replacement_refresh_token_stored: {public_result['replacement_refresh_token_stored']}"
    )
    if check_viewer:
        typer.echo(f"viewer_login_present: {public_result['viewer_login_present']}")
        typer.echo("viewer_login: [redacted]")
    typer.echo("access_token: [redacted]")
    typer.echo("refresh_token: [redacted]")


def _store_refresh_token(command: str, refresh_token: str | None) -> None:
    if not refresh_token:
        raise RuntimeError("GitHub response did not include a replacement refresh token")

    import shlex
    import subprocess  # nosec B404

    # Operator-provided secret-store command: shlex-split argv, no shell.
    subprocess.run(  # nosec B603
        shlex.split(command),
        input=refresh_token,
        text=True,
        check=True,
    )


def main() -> None:
    """Entry point for the ``vyg`` console script."""
    app()


if __name__ == "__main__":
    main()
