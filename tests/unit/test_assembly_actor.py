"""Unit tests for the Assembly actor-authorization gate (VOY-1818 Surface 7).

Covers every case enumerated in VOY-1818 §Surfaces row 7:
  (a) authorized via allow-list
  (b) authorized via association (OWNER / MEMBER / COLLABORATOR)
  (c) refused on association CONTRIBUTOR / NONE / FIRST_TIME_CONTRIBUTOR
  (d) refused on unrecognized association value (falls through to allow-list)
  (e) refused on missing comment.user
  (f) refused on missing sender
  (g) refused on empty-string login
  (h) refused on actor.type == "Bot"
  (i) refused on [bot]-suffix login even when login is on the allow-list (D7 precedence)
  (j) refused on malformed payload (non-dict comment, non-dict user)
  (k) env-var parsing — whitespace + comma-separated, case-insensitive (D10)
  (l) default deny on associations when BRIDGE_ASSEMBLY_AUTHORIZED_ASSOCIATIONS is unset (D6)
  (m) default trusted associations when BRIDGE_ASSEMBLY_AUTHORIZED_ASSOCIATIONS="" is set-but-empty (D6)
  (n) sender.login != comment.user.login divergence — WARNING logged, gate uses comment.user.login (D13)
  (o) full deny when both env vars are unset and no allow-list match (D4)

Env-var isolation: every test uses ``monkeypatch.setenv`` / ``monkeypatch.delenv``
per the CHG's §Testing requirement (D-isolation). No direct os.environ writes.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from voyager.bots.assembly import (
    AUTHORIZED_ACTORS_ENV,
    AUTHORIZED_ASSOCIATIONS_ENV,
    DEFAULT_AUTHORIZED_ASSOCIATIONS,
    REFUSAL_UNAUTHORIZED_ACTOR,
    ActorAuthorization,
    evaluate_actor_authorization,
)

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _payload(
    *,
    comment_login: str | None = "ryosaeba1985",
    comment_type: str | None = "User",
    association: str | None = "OWNER",
    sender_login: str | None = "ryosaeba1985",
    drop_comment: bool = False,
    drop_user: bool = False,
    drop_sender: bool = False,
    comment_override: Any = None,
    user_override: Any = None,
) -> dict[str, Any]:
    """Build a webhook payload skeleton with overridable actor fields."""
    user: Any = {"login": comment_login, "type": comment_type}
    if user_override is not None:
        user = user_override
    comment: Any = {
        "body": "/assembly",
        "author_association": association,
        "user": user,
    }
    if comment_override is not None:
        comment = comment_override
    payload: dict[str, Any] = {"action": "created"}
    if not drop_comment:
        payload["comment"] = comment
    if drop_user and isinstance(comment, dict):
        comment.pop("user", None)
    if not drop_sender:
        payload["sender"] = {"login": sender_login}
    return payload


@pytest.fixture(autouse=True)
def _isolate_actor_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default: both gate env vars unset for every test (CHG D4 default deny)."""
    monkeypatch.delenv(AUTHORIZED_ACTORS_ENV, raising=False)
    monkeypatch.delenv(AUTHORIZED_ASSOCIATIONS_ENV, raising=False)


# ---------------------------------------------------------------------------
# (a) Authorized via allow-list
# ---------------------------------------------------------------------------


class TestAuthorizedViaAllowList:
    def test_login_on_allow_list_is_authorized(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(AUTHORIZED_ACTORS_ENV, "ryosaeba1985 frankyxhl")
        result = evaluate_actor_authorization(
            _payload(comment_login="ryosaeba1985", association="NONE")
        )
        assert isinstance(result, ActorAuthorization)
        assert result.ok is True
        assert result.reason is None
        assert result.actor_login == "ryosaeba1985"
        assert result.matched_signal == "allow_list"

    def test_allow_list_overrides_untrusted_association(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Even with CONTRIBUTOR / NONE, the allow-list alone authorizes.
        monkeypatch.setenv(AUTHORIZED_ACTORS_ENV, "external-collab")
        monkeypatch.setenv(AUTHORIZED_ASSOCIATIONS_ENV, "OWNER MEMBER")
        result = evaluate_actor_authorization(
            _payload(comment_login="external-collab", association="NONE")
        )
        assert result.ok is True
        assert result.matched_signal == "allow_list"


# ---------------------------------------------------------------------------
# (b) Authorized via association
# ---------------------------------------------------------------------------


class TestAuthorizedViaAssociation:
    @pytest.mark.parametrize("assoc", ["OWNER", "MEMBER", "COLLABORATOR"])
    def test_trusted_associations_default_set_but_empty(
        self, monkeypatch: pytest.MonkeyPatch, assoc: str
    ) -> None:
        # D6: set-but-empty = use defaults.
        monkeypatch.setenv(AUTHORIZED_ASSOCIATIONS_ENV, "")
        result = evaluate_actor_authorization(_payload(association=assoc))
        assert result.ok is True
        assert result.matched_signal == "association"
        assert result.actor_association == assoc

    def test_default_associations_tuple_contents(self) -> None:
        # Anchor the documented default for D6.
        assert DEFAULT_AUTHORIZED_ASSOCIATIONS == ("OWNER", "MEMBER", "COLLABORATOR")

    def test_explicit_association_env_authorizes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(AUTHORIZED_ASSOCIATIONS_ENV, "OWNER")
        result = evaluate_actor_authorization(_payload(association="OWNER"))
        assert result.ok is True
        assert result.matched_signal == "association"


# ---------------------------------------------------------------------------
# (c) Refused on association CONTRIBUTOR / NONE / FIRST_TIME_CONTRIBUTOR
# ---------------------------------------------------------------------------


class TestRefusedOnUntrustedAssociations:
    @pytest.mark.parametrize(
        "assoc", ["CONTRIBUTOR", "NONE", "FIRST_TIME_CONTRIBUTOR", "FIRST_TIMER"]
    )
    def test_untrusted_associations_default_set(
        self, monkeypatch: pytest.MonkeyPatch, assoc: str
    ) -> None:
        # D6: set-but-empty = defaults — defaults reject these.
        monkeypatch.setenv(AUTHORIZED_ASSOCIATIONS_ENV, "")
        result = evaluate_actor_authorization(_payload(comment_login="drive-by", association=assoc))
        assert result.ok is False
        assert result.reason == REFUSAL_UNAUTHORIZED_ACTOR
        assert result.matched_signal is None


# ---------------------------------------------------------------------------
# (d) Refused on unrecognized association value
# ---------------------------------------------------------------------------


class TestUnrecognizedAssociation:
    def test_falls_through_to_allow_list_then_deny(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(AUTHORIZED_ASSOCIATIONS_ENV, "")
        result = evaluate_actor_authorization(_payload(comment_login="drive-by", association="FOO"))
        assert result.ok is False
        assert result.reason == REFUSAL_UNAUTHORIZED_ACTOR

    def test_unrecognized_assoc_with_login_on_allow_list_passes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(AUTHORIZED_ACTORS_ENV, "drive-by")
        result = evaluate_actor_authorization(_payload(comment_login="drive-by", association="FOO"))
        assert result.ok is True
        assert result.matched_signal == "allow_list"


# ---------------------------------------------------------------------------
# (e) Refused on missing comment.user
# ---------------------------------------------------------------------------


class TestMissingCommentUser:
    def test_missing_user_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(AUTHORIZED_ASSOCIATIONS_ENV, "")
        result = evaluate_actor_authorization(_payload(drop_user=True))
        assert result.ok is False
        assert result.reason == REFUSAL_UNAUTHORIZED_ACTOR
        assert result.actor_login is None

    def test_missing_comment_block(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(AUTHORIZED_ASSOCIATIONS_ENV, "")
        result = evaluate_actor_authorization(_payload(drop_comment=True))
        assert result.ok is False
        assert result.reason == REFUSAL_UNAUTHORIZED_ACTOR
        assert result.actor_login is None


# ---------------------------------------------------------------------------
# (f) Refused on missing sender
# ---------------------------------------------------------------------------


class TestMissingSender:
    def test_missing_sender_block_alone_does_not_grant(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # D8: gate authoritative source is comment.user.login. Missing
        # sender + valid comment.user + untrusted association is still a deny.
        monkeypatch.setenv(AUTHORIZED_ASSOCIATIONS_ENV, "")
        result = evaluate_actor_authorization(
            _payload(comment_login="drive-by", association="NONE", drop_sender=True)
        )
        assert result.ok is False
        assert result.reason == REFUSAL_UNAUTHORIZED_ACTOR

    def test_missing_sender_with_authorized_comment_user(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Sender missing but comment.user authorized via allow-list => pass.
        monkeypatch.setenv(AUTHORIZED_ACTORS_ENV, "ryosaeba1985")
        result = evaluate_actor_authorization(
            _payload(comment_login="ryosaeba1985", drop_sender=True)
        )
        assert result.ok is True
        assert result.actor_sender_login is None
        assert result.sender_divergent is False


# ---------------------------------------------------------------------------
# (g) Refused on empty-string login
# ---------------------------------------------------------------------------


class TestEmptyStringLogin:
    def test_empty_login_denied(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(AUTHORIZED_ASSOCIATIONS_ENV, "")
        result = evaluate_actor_authorization(_payload(comment_login=""))
        assert result.ok is False
        assert result.reason == REFUSAL_UNAUTHORIZED_ACTOR
        assert result.actor_login is None

    def test_empty_login_with_untrusted_assoc(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Empty login + non-trusted association is still a deny.
        monkeypatch.setenv(AUTHORIZED_ASSOCIATIONS_ENV, "")
        result = evaluate_actor_authorization(_payload(comment_login="", association="NONE"))
        assert result.ok is False
        assert result.reason == REFUSAL_UNAUTHORIZED_ACTOR


# ---------------------------------------------------------------------------
# (h) Refused on actor.type == "Bot"
# ---------------------------------------------------------------------------


class TestActorTypeBot:
    def test_bot_type_denied(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(AUTHORIZED_ASSOCIATIONS_ENV, "OWNER")
        result = evaluate_actor_authorization(
            _payload(comment_login="some-bot", comment_type="Bot", association="OWNER")
        )
        assert result.ok is False
        assert result.reason == REFUSAL_UNAUTHORIZED_ACTOR
        assert result.actor_type == "Bot"

    def test_bot_type_denied_even_on_allow_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # D7 precedence: bot check fires before allow-list.
        monkeypatch.setenv(AUTHORIZED_ACTORS_ENV, "some-bot")
        result = evaluate_actor_authorization(
            _payload(comment_login="some-bot", comment_type="Bot")
        )
        assert result.ok is False


# ---------------------------------------------------------------------------
# (i) Refused on [bot]-suffix login even when on allow-list (D7 precedence)
# ---------------------------------------------------------------------------


class TestBotSuffixPrecedence:
    def test_bracket_bot_suffix_denied(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(AUTHORIZED_ASSOCIATIONS_ENV, "")
        result = evaluate_actor_authorization(
            _payload(
                comment_login="iterwheel-stack[bot]",
                comment_type="User",
                association="OWNER",
            )
        )
        assert result.ok is False
        assert result.reason == REFUSAL_UNAUTHORIZED_ACTOR

    def test_bracket_bot_on_allow_list_still_denied(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # D7 explicit test: bot login on allow-list is STILL denied.
        monkeypatch.setenv(AUTHORIZED_ACTORS_ENV, "iterwheel-stack[bot] ryosaeba1985")
        result = evaluate_actor_authorization(
            _payload(
                comment_login="iterwheel-stack[bot]",
                comment_type=None,
                association="OWNER",
            )
        )
        assert result.ok is False
        assert result.reason == REFUSAL_UNAUTHORIZED_ACTOR

    def test_bracket_bot_suffix_case_insensitive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # D7: suffix check is case-insensitive.
        monkeypatch.setenv(AUTHORIZED_ASSOCIATIONS_ENV, "")
        result = evaluate_actor_authorization(
            _payload(
                comment_login="Some-Bot[BOT]",
                comment_type="User",
                association="OWNER",
            )
        )
        assert result.ok is False


# ---------------------------------------------------------------------------
# (j) Refused on malformed payload
# ---------------------------------------------------------------------------


class TestMalformedPayload:
    def test_non_dict_comment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(AUTHORIZED_ASSOCIATIONS_ENV, "")
        result = evaluate_actor_authorization(_payload(comment_override="oops"))
        assert result.ok is False
        assert result.reason == REFUSAL_UNAUTHORIZED_ACTOR
        assert result.actor_login is None

    def test_non_dict_user(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(AUTHORIZED_ASSOCIATIONS_ENV, "")
        result = evaluate_actor_authorization(_payload(user_override=["not", "a", "dict"]))
        assert result.ok is False
        assert result.reason == REFUSAL_UNAUTHORIZED_ACTOR

    def test_non_dict_user_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(AUTHORIZED_ASSOCIATIONS_ENV, "")
        result = evaluate_actor_authorization(_payload(user_override=42))
        assert result.ok is False
        assert result.reason == REFUSAL_UNAUTHORIZED_ACTOR


# ---------------------------------------------------------------------------
# (k) Env-var parsing — whitespace + comma-separated, case-insensitive
# ---------------------------------------------------------------------------


class TestEnvVarParsing:
    def test_whitespace_separated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(AUTHORIZED_ACTORS_ENV, "alice  bob    carol")
        result = evaluate_actor_authorization(_payload(comment_login="bob", association="NONE"))
        assert result.ok is True

    def test_comma_separated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(AUTHORIZED_ACTORS_ENV, "alice,bob,carol")
        result = evaluate_actor_authorization(_payload(comment_login="bob", association="NONE"))
        assert result.ok is True

    def test_comma_and_whitespace_mixed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(AUTHORIZED_ACTORS_ENV, "alice, bob , carol")
        result = evaluate_actor_authorization(_payload(comment_login="bob", association="NONE"))
        assert result.ok is True

    def test_login_compare_case_insensitive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # D10: operator typing Ryosaeba1985 must authorize webhook login ryosaeba1985.
        monkeypatch.setenv(AUTHORIZED_ACTORS_ENV, "Ryosaeba1985")
        result = evaluate_actor_authorization(
            _payload(comment_login="ryosaeba1985", association="NONE")
        )
        assert result.ok is True
        # Canonical lowercase exposed on the dataclass.
        assert result.actor_login == "ryosaeba1985"

    def test_login_compare_case_insensitive_reverse(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(AUTHORIZED_ACTORS_ENV, "ryosaeba1985")
        result = evaluate_actor_authorization(
            _payload(comment_login="RYOSAEBA1985", association="NONE")
        )
        assert result.ok is True
        assert result.actor_login == "ryosaeba1985"

    def test_association_compare_case_insensitive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(AUTHORIZED_ASSOCIATIONS_ENV, "owner, member")
        result = evaluate_actor_authorization(_payload(association="OWNER"))
        assert result.ok is True

    def test_association_value_case_insensitive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(AUTHORIZED_ASSOCIATIONS_ENV, "OWNER MEMBER COLLABORATOR")
        result = evaluate_actor_authorization(_payload(association="owner"))
        assert result.ok is True


# ---------------------------------------------------------------------------
# (l) Default deny on associations when BRIDGE_ASSEMBLY_AUTHORIZED_ASSOCIATIONS unset
# ---------------------------------------------------------------------------


class TestUnsetAssociationsEnv:
    def test_unset_associations_env_does_not_authorize(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # D6: unset = default deny on the association branch. Only allow-list
        # is consulted. OWNER alone is not enough.
        monkeypatch.delenv(AUTHORIZED_ASSOCIATIONS_ENV, raising=False)
        monkeypatch.delenv(AUTHORIZED_ACTORS_ENV, raising=False)
        result = evaluate_actor_authorization(_payload(association="OWNER"))
        assert result.ok is False
        assert result.reason == REFUSAL_UNAUTHORIZED_ACTOR
        assert result.matched_signal is None

    def test_unset_assoc_with_allow_list_still_authorizes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(AUTHORIZED_ASSOCIATIONS_ENV, raising=False)
        monkeypatch.setenv(AUTHORIZED_ACTORS_ENV, "ryosaeba1985")
        result = evaluate_actor_authorization(
            _payload(comment_login="ryosaeba1985", association="OWNER")
        )
        assert result.ok is True
        assert result.matched_signal == "allow_list"


# ---------------------------------------------------------------------------
# (m) Default trusted associations when set-but-empty
# ---------------------------------------------------------------------------


class TestSetButEmptyAssociationsEnv:
    @pytest.mark.parametrize("assoc", DEFAULT_AUTHORIZED_ASSOCIATIONS)
    def test_set_but_empty_activates_defaults(
        self, monkeypatch: pytest.MonkeyPatch, assoc: str
    ) -> None:
        # D6: set-but-empty = defaults. Every default association authorizes.
        monkeypatch.setenv(AUTHORIZED_ASSOCIATIONS_ENV, "")
        result = evaluate_actor_authorization(_payload(association=assoc))
        assert result.ok is True
        assert result.matched_signal == "association"

    def test_set_but_empty_still_rejects_contributor(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(AUTHORIZED_ASSOCIATIONS_ENV, "")
        result = evaluate_actor_authorization(
            _payload(comment_login="other", association="CONTRIBUTOR")
        )
        assert result.ok is False


# ---------------------------------------------------------------------------
# (n) Sender-vs-comment.user divergence: WARNING logged (D13)
# ---------------------------------------------------------------------------


class TestSenderDivergence:
    def test_divergence_logs_warning_and_uses_comment_user(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        monkeypatch.setenv(AUTHORIZED_ASSOCIATIONS_ENV, "")
        caplog.set_level(logging.WARNING, logger="voyager.bots.assembly.actor")
        result = evaluate_actor_authorization(
            _payload(
                comment_login="ryosaeba1985",
                sender_login="someone-else",
                association="OWNER",
            )
        )
        # D8: decision is based on comment.user.login — OWNER passes.
        assert result.ok is True
        # D13: divergence flagged and captured.
        assert result.sender_divergent is True
        assert result.actor_login == "ryosaeba1985"
        assert result.actor_sender_login == "someone-else"
        # WARNING log emitted under the actor logger.
        warnings = [
            r
            for r in caplog.records
            if r.levelname == "WARNING" and "assembly_actor_sender_divergence" in r.getMessage()
        ]
        assert warnings, "expected divergence WARNING log, got: " + repr(
            [(r.name, r.levelname, r.getMessage()) for r in caplog.records]
        )

    def test_no_divergence_when_logins_match(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        monkeypatch.setenv(AUTHORIZED_ASSOCIATIONS_ENV, "")
        caplog.set_level(logging.WARNING, logger="voyager.bots.assembly.actor")
        result = evaluate_actor_authorization(
            _payload(
                comment_login="ryosaeba1985",
                sender_login="ryosaeba1985",
                association="OWNER",
            )
        )
        assert result.sender_divergent is False
        warnings = [
            r for r in caplog.records if "assembly_actor_sender_divergence" in r.getMessage()
        ]
        assert not warnings


# ---------------------------------------------------------------------------
# (o) Full deny when both env vars unset and no allow-list match (D4)
# ---------------------------------------------------------------------------


class TestDefaultDenyBothUnset:
    def test_both_env_vars_unset_denies(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(AUTHORIZED_ACTORS_ENV, raising=False)
        monkeypatch.delenv(AUTHORIZED_ASSOCIATIONS_ENV, raising=False)
        result = evaluate_actor_authorization(_payload(association="OWNER"))
        assert result.ok is False
        assert result.reason == REFUSAL_UNAUTHORIZED_ACTOR
        assert result.matched_signal is None


# ---------------------------------------------------------------------------
# Dataclass schema anchor — guards Surface 1 contract
# ---------------------------------------------------------------------------


class TestActorAuthorizationSchema:
    def test_dataclass_fields_match_chg(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(AUTHORIZED_ACTORS_ENV, "ryosaeba1985")
        result = evaluate_actor_authorization(
            _payload(comment_login="ryosaeba1985", association="OWNER")
        )
        # Every field documented in §ActorAuthorization Schema must be readable.
        for field_name in (
            "ok",
            "reason",
            "actor_login",
            "actor_association",
            "actor_type",
            "actor_sender_login",
            "sender_divergent",
            "matched_signal",
        ):
            assert hasattr(result, field_name), f"missing field: {field_name}"

    def test_dataclass_is_frozen(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # frozen=True per §ActorAuthorization Schema.
        monkeypatch.setenv(AUTHORIZED_ACTORS_ENV, "ryosaeba1985")
        result = evaluate_actor_authorization(_payload(comment_login="ryosaeba1985"))
        from dataclasses import FrozenInstanceError

        with pytest.raises((FrozenInstanceError, AttributeError)):
            result.ok = False  # type: ignore[misc]
