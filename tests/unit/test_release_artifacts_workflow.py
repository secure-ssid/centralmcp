"""Offline structural assertions for .github/workflows/release-artifacts.yml.

Parses the workflow with PyYAML (never executes it) and asserts the
properties this workstream's task requires:

- Triggered on ``v*`` tag pushes and manual ``workflow_dispatch`` (no other
  trigger, and in particular no automatic push-to-main trigger).
- Least-privilege permissions: workflow-level defaults to read-only;
  ``id-token``/``attestations`` write scopes are granted only on the one
  job that needs them for ``actions/attest-build-provenance``.
- Every ``uses:`` step is pinned to a stable major-version tag (``@vN``),
  matching this repo's existing ``ci.yml`` pinning style -- never a
  floating branch, ``@main``, or a bare commit SHA without a version
  comment.
- The provenance-attestation step is gated to ``push`` events only (never
  runs on a manual ``workflow_dispatch`` build, which has no tag subject).
- The workflow builds, restores/smoke-tests, and uploads the bundle, but
  never publishes/creates a GitHub Release itself.

Note: PyYAML resolves the bare scalar key ``on`` as the boolean ``True``
under YAML 1.1 core-schema rules (a well-known PyYAML/GitHub-Actions-YAML
quirk) -- so this file reads the trigger block via ``doc[True]``, not
``doc["on"]``.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "release-artifacts.yml"
CI_WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "ci.yml"

_PINNED_VERSION_TAG = re.compile(r"@v\d+(\.\d+){0,2}$")


def _load_workflow() -> dict:
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


def _iter_uses_steps(doc: dict):
    for job in doc["jobs"].values():
        for step in job.get("steps", ()):
            if "uses" in step:
                yield job, step


class TestTriggers:
    def test_workflow_file_exists(self):
        assert WORKFLOW_PATH.is_file()

    def test_triggers_on_version_tags_and_manual_dispatch(self):
        doc = _load_workflow()
        triggers = doc.get("on", doc.get(True))
        assert triggers is not None, "could not locate the trigger ('on:') block"
        assert "workflow_dispatch" in triggers
        assert "push" in triggers
        tags = triggers["push"].get("tags", [])
        assert "v*" in tags

    def test_no_schedule_trigger(self):
        # Scheduled source-freshness behavior lives in ci.yml and must stay
        # there -- this workflow is tag/dispatch-only.
        doc = _load_workflow()
        triggers = doc.get("on", doc.get(True))
        assert "schedule" not in triggers

    def test_no_plain_push_to_branch_trigger(self):
        doc = _load_workflow()
        triggers = doc.get("on", doc.get(True))
        push_trigger = triggers.get("push", {})
        assert "branches" not in push_trigger


class TestPermissions:
    def test_workflow_level_permissions_are_read_only(self):
        doc = _load_workflow()
        assert doc["permissions"] == {"contents": "read"}

    def test_job_level_permissions_are_minimal_and_scoped(self):
        doc = _load_workflow()
        for job in doc["jobs"].values():
            perms = job.get("permissions", {})
            allowed = {"contents", "id-token", "attestations"}
            assert set(perms) <= allowed, f"unexpected permission scopes: {perms}"
            for scope, level in perms.items():
                if scope == "contents":
                    assert level == "read"
                else:
                    assert level == "write"


class TestActionPinning:
    def test_every_uses_step_is_pinned_to_a_stable_major_version(self):
        doc = _load_workflow()
        for _job, step in _iter_uses_steps(doc):
            uses = step["uses"]
            assert _PINNED_VERSION_TAG.search(uses), (
                f"step {step.get('name', uses)!r} is not pinned to a stable "
                f"version tag: {uses!r}"
            )

    def test_no_floating_or_branch_refs(self):
        doc = _load_workflow()
        for _job, step in _iter_uses_steps(doc):
            uses = step["uses"]
            assert not uses.endswith("@main")
            assert not uses.endswith("@master")
            assert not uses.endswith("@latest")

    def test_node_runtime_actions_use_node24_major(self):
        expected = {
            "actions/checkout": "v7",
            "actions/upload-artifact": "v7",
        }
        for workflow_path in (WORKFLOW_PATH, CI_WORKFLOW_PATH):
            doc = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
            for _job, step in _iter_uses_steps(doc):
                action, _, version = step["uses"].partition("@")
                if action in expected:
                    assert version == expected[action], (
                        f"{workflow_path.name} uses {step['uses']}; expected "
                        f"{action}@{expected[action]} for the Node 24 runtime"
                    )

    def test_attest_build_provenance_action_present(self):
        doc = _load_workflow()
        actions_used = {step["uses"].split("@")[0] for _job, step in _iter_uses_steps(doc)}
        assert "actions/attest-build-provenance" in actions_used
        assert "actions/upload-artifact" in actions_used
        assert "actions/checkout" in actions_used


class TestWorkflowBehavior:
    def test_attestation_step_gated_to_push_events_only(self):
        doc = _load_workflow()
        attest_steps = [
            step
            for _job, step in _iter_uses_steps(doc)
            if step["uses"].startswith("actions/attest-build-provenance")
        ]
        assert attest_steps, "expected an actions/attest-build-provenance step"
        for step in attest_steps:
            condition = step.get("if", "")
            assert "push" in condition

    def test_builds_bundle_via_repo_script(self):
        doc = _load_workflow()
        run_steps = [
            step.get("run", "")
            for job in doc["jobs"].values()
            for step in job.get("steps", ())
            if "run" in step
        ]
        assert any("scripts/build_release_bundle.py" in run for run in run_steps)

    def test_restores_and_smoke_tests_bundle(self):
        doc = _load_workflow()
        run_steps = [
            step.get("run", "")
            for job in doc["jobs"].values()
            for step in job.get("steps", ())
            if "run" in step
        ]
        assert any("scripts/restore_release_bundle.py" in run for run in run_steps)

    def test_never_publishes_or_creates_a_release(self):
        doc = _load_workflow()
        for job in doc["jobs"].values():
            for step in job.get("steps", ()):
                uses = step.get("uses", "")
                run = step.get("run", "")
                assert "create-release" not in uses.lower()
                assert "softprops/action-gh-release" not in uses
                assert "gh release create" not in run
                assert "gh release upload" not in run

    def test_does_not_make_any_curl_or_network_call_to_vendor_hosts(self):
        # A light guard against accidental live-vendor calls creeping into
        # this workflow -- it should only ever invoke local repo scripts.
        doc = _load_workflow()
        for job in doc["jobs"].values():
            for step in job.get("steps", ()):
                run = step.get("run", "")
                assert "curl " not in run
                assert "wget " not in run
