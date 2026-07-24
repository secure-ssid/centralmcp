# ArubaOS 8 → Central migration contract matrix

**Status: gating document for centralmcp 0.5.0.** No parser, schema, or adapter
change may claim broader migration coverage than what this matrix records as
`exact` or a bounded `conditional`. Rows marked `manual` or `unsupported`
remain out of scope for automatic writes until a follow-up revision of this
file records new verified evidence.

This matrix is the authoritative reference for the centralmcp 0.5.0
implementation plan's first milestone ("Build the authoritative migration
contract matrix"). It does not
change `pipeline/aos8_schema.py`, `pipeline/aos8_parsers.py`,
`pipeline/aos8_migration.py`, or `pipeline/aos8_target_adapters.py` — it
records what those files do today, what is provably supported by local
OpenAPI/spec evidence, and exactly what must change before coverage can
broaden.

## 1. How to read this matrix

### 1.1 Classification legend

| Classification | Meaning |
|---|---|
| `exact` | A verified, tested, source-to-target field mapping with a confirmed method/path/schema and no silent loss. |
| `conditional` | A mapping that is schema-expressible and has partial/official evidence, but requires a specific precondition (context, live confirmation, narrower value set) before it can be trusted as lossless. |
| `manual` | No safe automatic object-level write exists; the operator must recreate the object by hand on the target, using the retained source data as a reference. |
| `unsupported` | No target API/tool exists for this AOS8 concept in the audited surface, or the existing code path explicitly rejects the candidate (`AdapterError`). |

### 1.2 Context glossary (New Central)

- **SHARED vs LOCAL** — `object-type=SHARED` objects are library profiles (roles, WLAN SSIDs, VLANs, policies, server groups, auth servers, AAA/dot1x/macauth profiles) that exist independently of any device and must be bound to a scope + device-function through a separate config-assignment. `object-type=LOCAL` objects (network profiles: BGP, OSPF, VRF, VSX, VRRP-global, telemetry, app-bandwidth-contract, config-checkpoint) require `scope-id` and `device-function` directly on the create/update call and need no separate assignment step. This is documented verbatim in `mcp_servers/config.py:2115-2134` and matches the official "Working with Library Profiles" pattern.
- **scope-id / device-function** — Required identifiers for both LOCAL writes and SHARED config-assignments. `device-function` is a closed enum (`MOBILITY_GW`, `BRANCH_GW`, `VPNC`, `CAMPUS_AP`, `MICROBRANCH_AP`, `ACCESS_SWITCH`, `ALL`, `SERVICE_PERSONA`, `BRIDGE`, `IOT`, `HYBRID_NAC`, `CORE_SWITCH`, `AGG_SWITCH`, `AOSS_ACCESS_SWITCH`, `AOSS_CORE_SWITCH`, `AOSS_AGG_SWITCH`, `EC_VPNC`, `EC_BRANCH_GW`) per `ArubaConfigAssignment_ConfigAssignmentsSchema` in `ingestion/sources/openapi_specs/config-assignment.json`.
- **persona** — The adapter's internal name for `device-function` (`pipeline/aos8_target_adapters.py:39-51` `TargetContext.persona`); `BaseCentralTargetAdapter.__init__` (`:265-297`) rejects any context missing a resolved scope/persona.
- **gateway / cluster context** — Tunneled (overlay) WLANs require `cluster_name` and `cluster_scope_id` in addition to `scope_id` (`pipeline/aos8_target_adapters.py:881-895`). No AOS8 candidate object currently carries a target gateway serial; this remains a gap noted per-family below.

### 1.3 Context glossary (Classic Central)

- **group / scope reference** — Classic Central's `full_wlan` object REST is scoped by a URL path segment (`{target}` — a group or device serial), not a JSON body field. The adapter derives this from `context.scope_name` (`pipeline/aos8_target_adapters.py:1039`, `:1076`), which must be a Classic *group* name, never a New Central `scope_id`.
- **AOS8 AP groups are not Central groups** — An AOS8 `ap_groups` profile is a set of virtual-AP/WLAN bindings inside one controller config, while a Classic Central "group" and a New Central Device Group/site/scope are both device-container objects. There is no automatic 1:1 creation; an operator must select the target Device Group/scope explicitly.

## 2. Authoritative sources and provenance policy

1. **New Central**: `/network-config/v1alpha1` generated operations/specs under `ingestion/sources/openapi_specs/*.json` are authoritative. When a curated `/v1` (or `/v1alpha1`) tool in `mcp_servers/*.py` diverges from the generated spec's method/path/schema, the generated spec wins and the divergence is called out below as a verification blocker (see §2.1).
2. **Classic Central**: the only verified object REST in this repository is `POST/GET/PUT/DELETE /configuration/full_wlan/{target}/{wlan}` (`pipeline/aos8_target_adapters.py:1039-1085`), citing `developer.arubanetworks.com/central/reference/apifull_wlancreate_wlan` and `apifull_wlanget_wlan_list`, plus the community `central-python-workflows` `Classic-Central/wlan_config/configurations/open_network.yaml` sample as secondary/reference-only evidence — never a primary contract.
3. **Official HPE developer URLs cited throughout**: `developer.arubanetworks.com/new-central/docs/getting-started-with-rest-apis`, `developer.arubanetworks.com/new-central/docs/introduction-to-configuration-apis`, `developer.arubanetworks.com/new-central-config/reference/config-checkpoint`, `developer.arubanetworks.com/central/reference/apifull_wlancreate_wlan` (see `docs/release-indexes.md:118-125` for the indexed source list).
4. **Community/example repositories** (`central-python-workflows`, any GitHub sample) may be cited as *secondary* corroboration of an official Aruba-maintained example only. They never define a contract by themselves and never justify treating Classic and New Central payloads as interchangeable.

### 2.1 Known curated-tool vs. generated-spec divergence (verification blocker)

`mcp_servers/config.py:1743-1761` (`create_config_assignment`/`delete_config_assignment`) issues `POST`/`DELETE` against the **path-parameterized** endpoint `/network-config/v1alpha1/config-assignments/{scope_id}/{device_function}/{profile_type}/{profile_instance}`. The generated spec (`ingestion/sources/openapi_specs/config-assignment.json`) only declares `delete` on that path; `post`/`get` are declared on the **collection** path `/network-config/v1alpha1/config-assignments` with a `config-assignment` array request body (`ArubaConfigAssignment_ConfigAssignmentsSchema`). `pipeline/aos8_target_adapters.py:689-703` (`_map_role`'s `assignment` operation) uses the spec-correct collection-body form. **This divergence must be resolved against a live read before any role/config-assignment write is authorized** — treat the curated tool's create path as unverified until confirmed.

## 3. Audited source-model findings — actionable prerequisites

These were confirmed defects/gaps in the parser and redaction logic. Items 1, 4, 5,
and 6 are **RESOLVED** by the `aos8-source-enrichment` todo (parser/migration-level
fixes only, verified by new regression tests in `tests/unit/test_aos8_parsers.py`
and `tests/unit/test_aos8_migration.py`). Item 7 is **RESOLVED** by the
`aos8-source-review-fixes` todo (a follow-up code-review pass covering the
migration and orchestrator layers, verified by new regression tests in
`tests/unit/test_aos8_migration.py` and `tests/unit/test_aos8_migration_orchestrator.py`).
Item 2 is **partially resolved**: a
bounded, fail-closed source-side security-intent signal now exists, but no
adapter/target mapping has been implemented, so every §6.2 classification below is
unchanged pending that follow-up work. Item 3 is unchanged by design (see below).
Item 8 is **RESOLVED** by the `aos8-companion-repo-fixes` todo (a read-only audit
of a third-party same-owner migration tool that surfaced one classifier bug fixed
here, plus corroborating secondary evidence for design decisions already made in
items 6/7 — no code was copied; the referenced repository is unlicensed).

1. **RESOLVED — `mac_server_group` alias miss.** `parse_aaa_profiles`
   (`pipeline/aos8_parsers.py`) now aliases the literal key `mac_server_group` in
   addition to `mba_server_group`/`mac-server-group`. Regression:
   `test_parse_aaa_profiles_recognizes_literal_mac_server_group_alias`
   (`tests/unit/test_aos8_parsers.py`).
2. **PARTIALLY RESOLVED — WLAN security normalization.** `parse_wlans`
   (`pipeline/aos8_parsers.py`) now also extracts three evidenced AOS8 `ssid_prof`
   signals (`mcp_servers/openapi_gen/manifests/aos8.json`
   `aos8_post_object_ssid_prof` request-body properties): the `wpa3_transition`
   boolean flag and *presence-only* booleans for `wpa_passphrase`/`wpa_hexkey`
   (`AOS8Wlan.wpa3_transition`/`.passphrase_present`/`.psk_hexkey_present` — never
   the secret value itself). `build_migration_plan`
   (`pipeline/aos8_migration.py` `_wlan_security_intent`) combines these with the
   raw `opmode` string and a cross-reference against the WLAN's attached
   `aaa_profile` (dot1x/MAC-auth chain) to emit a bounded
   `payload["security"]` structure on every WLAN candidate with
   `mode ∈ {open, wpa2_personal, wpa3_sae, wpa3_transition_personal,
   enhanced_open, enterprise_dot1x, mac_auth_only, mac_auth_psk, unknown}` plus
   an explicit `ambiguous` flag. Any combination not covered by this evidence
   (e.g. WEP, an unresolved `aaa_profile` reference, or a bare WPA2/WPA3
   opmode string with no PSK/passphrase signal) is reported as `unknown` with
   an explicit warning — never guessed or defaulted. **No AOS8-side source
   opmode enum has official documented evidence in this repository**, so this
   remains keyword/field-presence-based classification, not a verified
   1:1 enum mapping; the original raw `opmode` is always preserved unchanged
   alongside the normalized `mode`. Regression tests:
   `test_wlan_security_intent_classifies_*` and
   `test_wlan_security_intent_reports_unknown_*`
   (`tests/unit/test_aos8_migration.py`). **No adapter/target mapping exists
   yet** — this is still the gap blocking every secured-WLAN row below beyond
   `OPEN`; §6.2 classifications are unchanged.
3. **Auth profiles/servers/server-groups/routes/VRRP retain undecoded detail in `settings`/raw.** `AOS8AuthProfile.settings`, `AOS8ServerGroup.settings`, `AOS8AuthServer.settings`, `AOS8Route.settings`, `AOS8VRRP.settings` (`pipeline/aos8_schema.py`) hold every field not explicitly named in the dataclass. These are propagated as `unsupported_fields` on every candidate (`pipeline/aos8_migration.py` `_append_for_both` calls throughout `build_migration_plan`), each with a mandatory unmapped-field warning (`_unsupported_warnings`). Any adapter that tries to apply these candidates today (`NewCentralAdapter._reject_unmapped`) will raise `AdapterError` unless the field is in a narrow `allowed` allow-list per object type — this is intentional fail-closed behavior, not a bug, and is unchanged by this revision.
4. **RESOLVED — server-group dependency resolution is now type-aware.**
   `build_migration_plan` (`pipeline/aos8_migration.py`) now keys
   `server_ids_by_name` by `dict[str, dict[server_type, identifier]]` instead of
   `dict[str, list[identifier]]`. A server-group's per-entry auth-server
   reference resolves to a dependency only when exactly one server type
   matches that name; if a RADIUS/LDAP/TACACS name collision exists in the
   export (AOS8 stores them in separate `radius_servers`/`ldap_servers`/
   `tacacs_servers` sections, so this is possible), the candidate emits a
   `unsupported_fields["auth_server_type_collisions"]` entry and an explicit
   fail-closed warning instead of silently selecting one candidate — the
   dependency is left unresolved. Deterministic ID/order guarantees are
   preserved (`build_migration_plan` sorts every dependency list and the
   overall plan is still fully reproducible across runs). Regression tests:
   `test_server_group_dependency_resolution_is_type_aware_across_radius_and_ldap`,
   `test_server_group_dependency_collision_fails_closed_with_warning`,
   `test_server_group_dependencies_remain_deterministic_across_runs`
   (`tests/unit/test_aos8_migration.py`).
5. **RESOLVED — AP-group VAP and role-policy dependencies now warn explicitly.**
   The AP-group loop and the role loop in `build_migration_plan`
   (`pipeline/aos8_migration.py`) each now emit a specific, per-reference
   warning the moment a virtual-AP→WLAN or role→policy reference cannot be
   resolved against the export (`ap_group:<name>: virtual AP '<vap>' does not
   match any parsed WLAN profile...`; `role:<name>: referenced policy
   '<acl>' was not present in the export...`), in addition to the existing
   generic end-of-plan dependency-not-present check. No target object is
   invented in either case — the dependency remains unresolved and the
   candidate stays unapplied. Regression tests:
   `test_ap_group_warns_explicitly_on_unresolved_vap_to_wlan_dependency`,
   `test_role_warns_explicitly_on_missing_policy_dependency`
   (`tests/unit/test_aos8_migration.py`).
6. **RESOLVED — `ldap_admindn` is no longer over-redacted.**
   `pipeline/aos8_migration.py` `_SENSITIVE_EXACT_KEYS` no longer lists
   `ldap_admindn`/`ldap_admin_dn`; `_redact_sensitive_values` now leaves the
   LDAP bind/admin distinguished name (e.g. `cn=admin,dc=example,dc=com`)
   visible in the candidate payload/`unsupported_fields`, while the
   accompanying bind **password** (`ldap_adminpasswd`/`ldap_adminpwd`, still
   listed) remains redacted, transient, and flagged
   `requires_secret_input`. The same AOS8 `ssid_prof` PSK/WEP key-material
   fields (`wpa_hexkey`, `wepkey1`-`wepkey4`) were added to the secret list as
   a related defensive fix so WLAN key material is never persisted either.
   Regression tests: `test_ldap_admin_dn_stays_visible_while_bind_password_is_redacted`,
   `test_build_migration_plan_never_serializes_auth_secrets`,
   `test_sensitive_key_detection_covers_credentials_without_false_positives`
   (`tests/unit/test_aos8_migration.py`).
7. **RESOLVED — flattened path-like keys evaded the item-6 secret list, and
   WPA2-personal classification was over-eager.** A follow-up code-review
   pass found that `_wlan_payload`'s `unsupported_fields` flattening (e.g.
   `f"ssid_profile.{key}"` for every unmapped `ssid_prof`/`virtual_ap` field)
   produced path-like keys such as `ssid_profile.wpa_hexkey` /
   `ssid_profile.wepkey1`-`wepkey4` that no longer matched
   `_SENSITIVE_EXACT_KEYS`/the prefix+suffix checks once normalized, because
   normalization collapses the non-secret path prefix and the secret leaf
   token into one indistinguishable underscore-joined string — an
   unredacted-secret-leak regression on top of item 6's fix.
   `pipeline/aos8_migration.py` `_is_sensitive_key` now also evaluates the
   final `.`/`/`-separated path component alone against the same rules, so a
   non-secret prefix can no longer dilute a secret leaf out of the check.
   Separately, `_wlan_security_intent`'s final `wpa2_personal` branch
   classified *any* passphrase/PSK-hexkey presence or a bare `"psk"` token in
   `opmode` as verified WPA2-personal, which misclassified legacy WPA1/TKIP
   opmodes (e.g. `wpa-psk-tkip`, `wpa-tkip`) or any other unrecognized
   personal mode as WPA2; it now additionally requires the opmode to
   explicitly contain `wpa2`, falling through to `mode="unknown"` with a
   warning otherwise (see §6.2's WPA2 Personal row). Finally, the
   orchestrator's `_safe_candidate`/`_sanitize` redaction in
   `pipeline/aos8_migration_orchestrator.py` was masking the non-secret
   `passphrase_present`/`psk_hexkey_present` presence booleans to the literal
   string `"******"` in previews and persisted migration-run state; a narrow,
   type-checked allowlist (`_is_presence_metadata` — exact key name *and* an
   actual `bool` value) now preserves them as real booleans while still
   redacting any actual secret value. Regression tests:
   `test_sensitive_key_detection_evaluates_flattened_path_like_keys_by_leaf`,
   `test_wlan_secret_material_never_appears_in_plan_json`,
   `test_wlan_security_intent_does_not_classify_legacy_wpa_tkip_psk_as_wpa2`,
   `test_wlan_security_intent_does_not_classify_wpa_tkip_with_passphrase_present_as_wpa2`,
   `test_wlan_security_intent_does_not_classify_unrecognized_psk_opmode_as_wpa2`
   (`tests/unit/test_aos8_migration.py`);
   `test_safe_candidate_redaction_preserves_presence_booleans_and_redacts_secrets`,
   `test_safe_candidate_redaction_only_bypasses_boolean_presence_values`
   (`tests/unit/test_aos8_migration_orchestrator.py`).

8. **RESOLVED — role-only AAA profiles could block WLAN security
   classification; plus corroborating secondary evidence for two prior
   design decisions.** A read-only audit of
   [`secure-ssid/aos8-migration-tool`](https://github.com/secure-ssid/aos8-migration-tool)
   pinned at commit
   [`7bfa884`](https://github.com/secure-ssid/aos8-migration-tool/tree/7bfa884d8e8f1c7e97a7bfa42f15596aa42fcf79)
   — a third-party, same-owner AOS8 migration tool with **no LICENSE file**,
   so it is cited here as secondary provenance/corroboration only; no code
   from it was copied or adapted, and it is never an authoritative API
   contract — surfaced the following:
   - **Classifier bug (fixed here).** `_wlan_security_intent`
     (`pipeline/aos8_migration.py`) previously treated *any* resolved
     `aaa_profile` reference as blocking further classification, even when
     that profile configured neither a `dot1x_auth_profile` nor a
     `mac_auth_profile` (i.e. it was **role-only** — used only for
     post-auth role assignment, not authentication). This produced a false
     `mode="unknown"` for an otherwise verifiable WPA2-PSK WLAN. The
     companion repo's own parser regression tests fixture pairs a
     `wlan virtual-ap "guest-vap"` (opmode `wpa2-psk-aes` +
     `wpa-passphrase`) with `aaa profile "guest-aaa"` that sets only
     `initial-role "guest-logon"` — a real-world instance of the same
     role-only-AAA-on-a-personal-WLAN shape:
     https://github.com/secure-ssid/aos8-migration-tool/blob/7bfa884d8e8f1c7e97a7bfa42f15596aa42fcf79/tests/test_aos8_parser.py#L15-L34
     (secondary, same-owner prior art — not an authoritative API contract).
     `_wlan_security_intent` now only promotes to `enterprise_dot1x`/
     `mac_auth_only`/`mac_auth_psk` when the resolved `aaa_profile` carries
     an explicit `dot1x_auth_profile` or `mac_auth_profile` reference; a
     role-only profile falls through to the existing verified
     opmode/passphrase classification instead of stopping early. An
     aaa_profile reference that fails to resolve in the export at all (or
     one that ambiguously configures both a dot1x and a MAC-auth profile)
     still correctly stays `unknown` — only the role-only case changed.
     Regression tests:
     `test_wlan_security_intent_role_only_aaa_profile_falls_through_to_opmode_classification`,
     `test_wlan_security_intent_stays_unknown_when_aaa_profile_has_both_dot1x_and_mac_auth`
     (`tests/unit/test_aos8_migration.py`).
     **Re-review follow-up (fixed here).** The role-only fall-through above
     was itself too permissive: a resolved `aaa_profile` that sets no
     `dot1x_auth_profile`/`mac_auth_profile` but *does* configure a
     `dot1x_server_group`, `mac_server_group`, or `accounting_server_group`
     (`pipeline/aos8_schema.py` `AOS8AAAProfile`) still carries external
     server-group authentication intent that cannot be safely verified from
     opmode/passphrase alone — e.g. an `opensystem` WLAN with only a
     `dot1x_server_group` configured on its `aaa_profile` was previously
     falling through and being classified as unambiguous `open`.
     `_wlan_security_intent` now only falls through to opmode/passphrase
     classification when the resolved `aaa_profile` configures **none** of
     `dot1x_auth_profile`, `mac_auth_profile`, `dot1x_server_group`,
     `mac_server_group`, or `accounting_server_group`; otherwise it stays
     `mode="unknown"`, `ambiguous=True`, with an explicit fail-closed
     warning naming the configured server-group field(s). The originally
     verified role-only (`initial-role`-only) + WPA2-PSK fallback is
     unchanged. Regression tests:
     `test_wlan_security_intent_stays_unknown_for_dot1x_server_group_without_auth_profile`,
     `test_wlan_security_intent_stays_unknown_for_mac_server_group_without_auth_profile`,
     `test_wlan_security_intent_stays_unknown_for_accounting_server_group_without_auth_profile`
     (`tests/unit/test_aos8_migration.py`).
   - **Defensive boolean/flag normalization (hardened here).** The same
     repo's client documents AOS8 fields arriving in loosely-typed shapes —
     flag dicts, and values "double-wrapped as `{key: {key: val}}`"
     (secondary, same-owner prior art, not an authoritative API contract):
     https://github.com/secure-ssid/aos8-migration-tool/blob/7bfa884d8e8f1c7e97a7bfa42f15596aa42fcf79/docs/API-NOTES.md#L57-L64.
     `_wlan_security_signals`'s `wpa3_transition` extraction
     (`pipeline/aos8_parsers.py`) previously did a naive `bool(raw_value)`,
     which would silently misclassify an ambiguous shape (notably: an
     *empty* dict, which is falsy in Python, was reported as a confident
     `False` rather than "unverifiable"). A new `_normalize_optional_bool`
     helper accepts only actual booleans, integer `0`/`1`, a narrow set of
     explicit true/false-ish strings, and recursively unwraps single-key
     wrapper dicts (bounded to 4 levels); any other shape (multi-key dict,
     empty dict, list, or unrecognized string) now returns `None` rather
     than guessing. Regression tests: `test_normalize_optional_bool_*`,
     `test_parse_wlans_wpa3_transition_accepts_documented_flag_variants`
     (`tests/unit/test_aos8_parsers.py`).
   - **Source alias coverage (audited and extended here).** The same
     client tries multiple build-dependent object/field names for the same
     AOS8 concept — `ssid_prof`/`ssid-profile`/`ssid-prof` for the
     SSID-profile reference, and the legacy `wlan_virtual_ap` object name
     alongside the canonical `virtual_ap` (secondary, same-owner prior art,
     not an authoritative API contract):
     https://github.com/secure-ssid/aos8-migration-tool/blob/7bfa884d8e8f1c7e97a7bfa42f15596aa42fcf79/lib/aos8_client.py#L315-L399.
     `parse_wlans` (`pipeline/aos8_parsers.py`) already recognized
     `ssid-profile`/`ssid_prof`; the missing all-hyphen `ssid-prof` variant
     was added (and to `_wlan_payload`'s consumed-field set in
     `pipeline/aos8_migration.py`, so it is not double-reported as an
     unmapped field). `aos8_list_virtual_aps`
     (`mcp_servers/aos8.py`) now falls back to the legacy
     `wlan_virtual_ap` object name when the canonical `virtual_ap` object
     read fails, mirroring the same tolerant-of-either-name behavior — a
     failure on both names is still reported exactly as a single failed
     lookup always has been (no new silent-success path). The `ap_group`
     virtual-AP binding field itself already accepted both `virtual-ap` and
     `virtual_ap`; no changes were needed there. Regression tests:
     `test_parse_wlans_recognizes_ssid_prof_hyphenated_alias`
     (`tests/unit/test_aos8_parsers.py`);
     `test_aos8_list_virtual_aps_falls_back_to_legacy_wlan_virtual_ap_object`,
     `test_aos8_export_wlans_still_warns_when_both_virtual_ap_object_names_fail`
     (`tests/unit/test_aos8_export_and_migration_tool.py`).
     **Re-review follow-up (fixed here).** The parser-level alias fix above
     was incomplete: `_VIRTUAL_AP_FIELDS` (`mcp_servers/aos8.py`), the
     bounded field allow-list used by `_compact_primary_list` to compact
     live `aos8_list_virtual_aps`/`aos8_export_wlans` reads, still only
     listed `ssid-profile`/`ssid_prof` and omitted the all-hyphen
     `ssid-prof` alias. A live virtual-AP record shaped like
     `{profile-name, ssid-prof, vlan}` had its `ssid-prof` field stripped by
     bounded compaction before `parse_wlans` ever saw it, so the WLAN could
     never be joined to its SSID profile even though the parser itself
     supported the alias. `ssid-prof` was added to `_VIRTUAL_AP_FIELDS`.
     Regression tests:
     `test_aos8_list_virtual_aps_retains_ssid_prof_hyphenated_alias`,
     `test_aos8_export_wlans_links_ssid_prof_alias_vap_to_one_wlan`
     (`tests/unit/test_aos8_export_and_migration_tool.py`).
   - **Secondary corroboration for item 7's Classic read-back stance
     (no code change — informational only).** The same repo's Classic
     Central client documents that its own group-create call "is a known
     flaw [that] lets the v3 create return success **without applying**",
     requiring an explicit `GET /configuration/v1/groups/properties`
     read-back to confirm the setting actually took (secondary, same-owner
     prior art, not an authoritative API contract):
     https://github.com/secure-ssid/aos8-migration-tool/blob/7bfa884d8e8f1c7e97a7bfa42f15596aa42fcf79/docs/API-NOTES.md#L112-L118.
     This corroborates (but does not itself establish) the general
     Classic-Central caution already reflected in this matrix's §2/§5
     read-back-before-trust posture; centralmcp's own Classic adapter has
     no group-create call to change, so no code changed for this item.
   - **Secondary corroboration for item 6/7's stricter no-PSK-reuse rule
     (no code change — informational only).** The same repo's own New
     Central client includes a `secret_looks_unusable()` guard and a
     Classic-side hashed-PSK-to-placeholder substitution specifically
     because AOS8-exported PSK/secret values are sometimes unusable as-is
     (empty, longer than a real WPA passphrase can be, or a hex-encoded
     hash) (secondary, same-owner prior art, not an authoritative API
     contract):
     https://github.com/secure-ssid/aos8-migration-tool/blob/7bfa884d8e8f1c7e97a7bfa42f15596aa42fcf79/lib/central_client.py#L40-L51,
     https://github.com/secure-ssid/aos8-migration-tool/blob/7bfa884d8e8f1c7e97a7bfa42f15596aa42fcf79/tests/test_clients_http.py#L336-L354.
     This corroborates centralmcp's own, stricter rule (item 6/7 above):
     rather than substituting a placeholder, centralmcp never persists or
     reuses a source PSK/hexkey value at all — only a presence boolean
     ever leaves the parser/migration layer, and the operator must always
     re-enter the real credential on the target.

## 4. New Central audited conclusions (encoded contract)

- **Authoritative base**: `/network-config/v1alpha1` (see server blocks in every spec cited in §5 — `wlan.json`, `auth-server.json`, `auth-server-group.json`, `aaa-profile.json`, `aaa-dot1xauth.json`, `aaa-macauth.json`, `role.json`, `role-acl.json`, `static-route.json`, `l3-route.json`, `vrrp.json`, `vrrp-interface.json`, `policy.json`, `config-assignment.json`, `cda-auth-profile.json`, `cda-authz-policy.json` all declare `"servers": [{"url": "/network-config/v1alpha1"}]`).
- **SHARED/LOCAL + config-assignment verification is mandatory** — see §1.2/§1.3 and the divergence in §2.1. LOCAL objects require `scope-id`+`device-function` on the write call itself; SHARED objects require a follow-up config-assignment (`mcp_servers/config.py:2100-2134`).
- **Secured WLAN schema** (`ingestion/sources/openapi_specs/wlan.json`, `ArubaWlanSecurity_WlanSecurityConfig.opmode`) supports the closed enum `OPEN, WPA2_PERSONAL, WPA2_ENTERPRISE, ENHANCED_OPEN, WPA3_SAE, WPA3_ENTERPRISE_CCM_128, WPA3_ENTERPRISE_GCM_256, WPA3_ENTERPRISE_CNSA, WPA_ENTERPRISE, WPA_PERSONAL, WPA2_MPSK_AES, WPA2_MPSK_LOCAL, DPP, WPA2_PSK_AES_DPP, WPA2_AES_DPP, WPA3_SAE_DPP, WPA3_AES_CCM_128_DPP, WPA3_AES_GCM_256_DPP, BOTH_WPA_WPA2_PSK, BOTH_WPA_WPA2_DOT1X, STATIC_WEP, DYNAMIC_WEP, WPA3_MPSK_SAE`. There is **no** `WPA2_PSK` value — any code or documentation using `WPA2_PSK` (rather than `WPA2_PERSONAL`) is stale/incorrect and must be corrected wherever it appears before implementation. WPA3 personal transition is represented as the boolean `wpa3-transition-mode-enable` inside `ArubaWlanSecurity_WirelessSecurityAdvancedConfig` (`ingestion/sources/openapi_specs/wlan.json:5578-5584` and `:5714-5720`), **not** as a distinct `opmode` value — this needs live validation against a real WPA3-Personal-transition SSID before being classified `exact`.
- **Auth server** (`ingestion/sources/openapi_specs/auth-server.json`, `ArubaAuthServer_AuthServersauthServerSchema.type`) supports the enum `RADIUS, LDAP, TACACS, WINDOWS, RFC3576, XMLAPI, RADSEC, LOCAL`, with `x-supportedDeviceType` restricted per platform (AP: RADIUS/LDAP/TACACS/XMLAPI; CX: RADIUS/TACACS; PVOS: RADIUS/TACACS; GW: RADIUS/TACACS/WINDOWS/LDAP). RadSec (`RADSEC`) representation needs care — it was not exercised anywhere in `pipeline/aos8_target_adapters.py` (only the RADIUS path is implemented, `:728-780`) and needs its own schema-field audit before use. Server groups (`auth-server-group.json`) have an **ordered** `servers` array (`server-name` + `position`, `ingestion/sources/openapi_specs/auth-server-group.json`), which the current `AOS8ServerGroup.auth_servers`/`auth_server_entries` fields (`pipeline/aos8_schema.py`) do not yet guarantee order-preserving mapping for.
- **Device AAA/dot1x/macauth profiles are Gateway/Switch concepts** — `ArubaAaaProfile_AaaProfileSchemaGet`, `ArubaAaaDot1xauth_Dot1xauthSchemaGet`, `ArubaAaaMacauth_MacauthSchemaGet` are all `x-supportedDeviceType: [Gateway, Switch CX, Switch PVOS]` only (no `Access Point`). AP WLAN authentication is configured directly on the WLAN/SSID resource (`ArubaWlanSecurity_AuthServerConfig` embedded in `wlan.json`), not through a device AAA profile. **Central NAC auth profiles** (`ingestion/sources/openapi_specs/cda-auth-profile.json`, `x-tag-group: "Central NAC"`) are a distinct, explicit alternative surface for MAC/MPSK/wired/EAP authentication policy and must never be produced as an automatic LDAP/TACACS conversion of an AOS8 `mac_auth_profile`/`dot1x_auth_profile`.
- **Role ACL is CX-only** — `ArubaRoleAcl_RoleAclsSchemaGet.x-supportedDeviceType == ["Switch CX"]` (`ingestion/sources/openapi_specs/role-acl.json`). Gateway security policies (`policy.json`, used by the existing `delete_gw_policy` tool at `/network-config/v1alpha1/policies/{name}`, `mcp_servers/config.py:2110-2121`) are a **distinct** object family from CX role-ACLs. `NewCentralAdapter._map_role` (`pipeline/aos8_target_adapters.py:647-726`) currently only accepts a normalized ACL value of `allowall`/`sys_allow_all` and raises `AdapterError` for anything else (`:661-665`) — custom AOS8 ACLs/policies must never be silently reduced to allow-all; they remain `manual` until a verified custom-policy write path exists.
- **AOS8 AP groups require operator-selected Device Group/scope and profile assignments** — there is no automatic one-to-one creation of a New Central Device Group from an AOS8 `ap_groups` profile. `NewCentralAdapter` has no `_map_ap_group` method at all today, so any `ap_group` candidate falls through `_map_candidate`'s `getattr(..., None)` branch (`:574-580`) to `unsupported`.
- **Gateway IPv4 static-route destination contract is not verified.** `ArubaStaticRoute_Ipv4RouteCfg` (`ingestion/sources/openapi_specs/static-route.json`) keys routes by a composite `prefix-vrf-nexthop-id` string and exposes a `forwarding-type` enum (`NEXTHOP, INTERFACE, NULLROUTE, REJECT, VLAN, TUNNEL, IPSECMAP, CLUSTER`) that is `x-supportedDeviceType: ["Switch CX"]`-only for that specific field, while `next-hop` itself spans AP/Gateway/Switch. No live read has confirmed the exact destination/prefix write shape for a Gateway. IPv6 (`ArubaStaticRoute_Ipv6RouteCfg`) is schema-expressible but conditional for the same reason. **`l3-route.json` (`/l3-route`) is Switch-CX-only** (`ArubaL3Route_L3RouteSchemaGet.x-supportedDeviceType == ["Switch CX"]`) and is a separate, non-Gateway payload family — never substitute it for `static-route.json` on a Gateway/AP persona.
- **VRRP/VRRPv6/tracking remain conditional/unsupported** until VLAN-interface attachment and tracking normalization are proven. `vrrp.json` (`/vrrp-global`, used by the existing `build_*` LOCAL network-profile helper, `mcp_servers/config.py` `_NETWORK_PROFILE_TYPES["vrrp"] = "vrrp-global"`) and `vrrp-interface.json` (`/vrrp`, Gateway-only per `ArubaVrrpInterface_VrrpprofileSchema`) are two **different** resources; the interface-level profile keys `virtual-router` entries by a composite `router-id-address-family` string, not directly by VLAN ID, and tracking is a nested `ArubaVrrpInterface_VrrpTrackingConfiguration` block that has not been mapped from `AOS8VRRP.tracking` (`pipeline/aos8_schema.py`).

## 5. Classic Central audited conclusions (encoded contract)

- **Verified object REST**: `POST/GET/PUT/DELETE /configuration/full_wlan/{target}/{wlan}` with body `{"wlan": {...}, "access_rule": {...}}` (`pipeline/aos8_target_adapters.py:1039-1085`), citing `developer.arubanetworks.com/central/reference/apifull_wlancreate_wlan` / `apifull_wlanget_wlan_list`. There is a **separate, documented v2 WLAN contract** for the documented WPA2-Personal case that has not been reconciled with the `full_wlan` shape used here — treat v1 `full_wlan` and any v2 WLAN endpoint as non-interchangeable until both are read live against the same target group.
- **Official samples support WPA3 Personal and Enterprise**, but transition mode, MAC-auth, and the enterprise dependency lifecycle (auth server → server group → AAA profile/role → WLAN, in Classic's object model) are ambiguous in the audited sources and must remain `manual`/`unsupported` without live goldens confirming the exact request/response shape and dependency order.
- **No standalone object REST exists in the audited Classic API surface** for AAA profiles, server groups, roles, gateway/role policies, static/VRRP routes, LDAP, or TACACS. `ClassicCentralAdapter._map_candidate` (`pipeline/aos8_target_adapters.py:938-956`) explicitly rejects every `object_type != "wlan"` with `compatibility_errors=["Classic Central '<type>' target operation is not verified in this repository; candidate remains unapplied"]`. AP CLI (`aos8_write`) and MobilityController templates are **whole-config/manual fallbacks only** — never treat a CLI/template blob as a safe, idempotent, per-object automatic write.
- **Central groups/device moves are not AOS8 AP groups.** The Classic `full_wlan` `{target}` path segment must be an explicit Classic group name resolved by the operator/context, never derived automatically from an AOS8 `ap_groups` profile name.

## 6. Contract matrix by family

Apply order (`pipeline/aos8_migration.py:33-46`, `APPLY_ORDER`) is shared between Classic and New Central candidate lists and drives every dependency-aware topological sort (`_topological_candidates`, `pipeline/aos8_target_adapters.py:205-264`): `vlan`/`auth_server`=10, `dot1x_auth_profile`/`mac_auth_profile`/`server_group`/`policy`=20, `role`=30, `aaa_profile`=40, `wlan`=50, `ap_group`=60, `route`=70, `vrrp`=80, `controller`=90.

### 6.1 VLANs (foundation dependency for every other family)

| | Classic Central | New Central |
|---|---|---|
| **Source fields** | `AOS8Vlan.vlan_id`, `.description`, remaining raw fields (`pipeline/aos8_schema.py`) | same |
| **Candidate payload** | `{"vlan_id", "description"}` (`pipeline/aos8_migration.py:544-565`) | same |
| **Target method/path** | Not implemented in `ClassicCentralAdapter` (falls to generic "not verified" rejection, `:938-948`) | `create_vlan` tool → `POST/PUT /network-config/v1/layer2-vlan/{vlan_id}` plus scope-map (`pipeline/aos8_target_adapters.py:609-645`) |
| **Context** | Classic group (unresolved) | `scope_id`, `persona` |
| **Preflight/read-back** | none | `GET /network-config/v1/layer2-vlan/{vlan_id}` (`:637-645`) |
| **Update** | n/a | same `create_vlan` operation reused as update (`:635`) |
| **Secrets** | none | none |
| **Classification** | `unsupported` | `exact` (implemented, tested per `tests/unit/test_aos8_target_adapters.py:150-190`) |

### 6.2 Secured WLANs

`parse_wlans`/`build_migration_plan` now derive a bounded, fail-closed source
`payload["security"]` intent summary for every WLAN candidate (§3 item 2 —
partially resolved): the original raw `opmode` string is always preserved,
plus a normalized `mode` in
`{open, wpa2_personal, wpa3_sae, wpa3_transition_personal, enhanced_open,
enterprise_dot1x, mac_auth_only, mac_auth_psk, unknown}` derived only from
evidenced AOS8 `ssid_prof` fields (`opmode`, `wpa3_transition`, and
presence-only `wpa_passphrase`/`wpa_hexkey` booleans) plus a cross-reference
against the attached `aaa_profile`'s dot1x/MAC-auth chain. Any unverifiable
combination reports `mode="unknown"` with an explicit warning rather than a
guess. **No adapter/target mapping exists yet for any mode this enriches** —
every classification below is unchanged; this is still the single gap
blocking every secured-WLAN row beyond `OPEN` from becoming schema-mapped.

| Mode | AOS8 source signal (raw, unverified enum) | Classic target | New Central target | Classification |
|---|---|---|---|---|
| **OPEN** | `opmode` ∈ `{open, opensystem}` (checked case-insensitively, `pipeline/aos8_target_adapters.py:850`, `:983`); source `payload["security"]["mode"] == "open"` | `full_wlan` body with `"opmode": "opensystem"`, `wpa_passphrase: ""` (`:1006-1032`) | `build_underlay_ssid`/`build_overlay_ssid` with `opmode: "OPEN"` (`:866-903`) | **exact** (only mode implemented/tested end to end) |
| **WPA2 Personal** | source `mode == "wpa2_personal"` **only** when `opmode` explicitly contains `wpa2` *and* also contains a `psk`-style token or `wpa_passphrase`/`wpa_hexkey` is present (presence only, never the value); legacy/unrecognized personal modes that carry a passphrase/PSK signal without an explicit `wpa2` opmode token (e.g. WPA1/TKIP `wpa-psk-tkip`, `wpa-tkip`) are reported as `unknown` with a warning rather than guessed | no verified payload; would require `wpa_passphrase` + PSK fields in `full_wlan.wlan` | target schema supports `WPA2_PERSONAL` (`wlan.json` enum) but no adapter code populates passphrase/PMF | `manual` (Classic), `conditional` (New Central — schema exists, no implementation or live validation) |
| **WPA3-SAE** | source `mode == "wpa3_sae"` when `opmode` contains `wpa3` and a PSK-style signal (text/passphrase/hex-key presence) | no verified payload | target schema supports `WPA3_SAE`; official samples exist per plan notes but unreconciled with local adapter code | `manual` (Classic), `conditional` (New Central) |
| **WPA2/WPA3 transition (mixed personal)** | source `mode == "wpa3_transition_personal"` when the evidenced `wpa3_transition` ssid_prof flag is true (`pipeline/aos8_parsers.py` `_wlan_security_signals`); AOS8 concept still maps loosely to `BOTH_WPA_WPA2_PSK` or the target-side `wpa3-transition-mode-enable` flag, not a single 1:1 field | ambiguous per §5 (transition mode unreconciled in official samples) | `wpa3-transition-mode-enable` boolean exists (`wlan.json:5578-5584`, `:5714-5720`) but is unvalidated against a live SSID | `manual`/`unsupported` until live validation |
| **Enhanced Open (OWE)** | source `mode == "enhanced_open"` when `opmode` contains both `enhanced` and `open` (keyword match, no in-repo enum evidence for the exact AOS8 CLI token) | no verified payload | target schema supports `ENHANCED_OPEN` | `manual` (Classic), `conditional` (New Central) |
| **MAC-auth only** | `AOS8Wlan.aaa_profile` reference exists but is explicitly rejected — `NewCentralAdapter._map_wlan` raises `AdapterError` whenever `aaa_profile` is non-empty (`:844-847`); Classic raises the identical error (`:979-982`). Source `mode == "mac_auth_only"` when the resolved `aaa_profile` has a `mac_auth_profile` and no PSK/passphrase signal | `unsupported` (both adapters explicitly reject any AAA-profile-attached WLAN) | `unsupported` | **unsupported** (deliberate fail-closed, both targets) |
| **MAC-auth + PSK** | same AAA-profile-attach gap as above; source `mode == "mac_auth_psk"` when the resolved `aaa_profile` has a `mac_auth_profile` and a PSK/passphrase signal is also present | `unsupported` | `unsupported` | **unsupported** |
| **Enterprise (802.1X)** | same AAA-profile-attach gap, plus dot1x server-group chain (§6.3–§6.7) is unmapped end to end; source `mode == "enterprise_dot1x"` when the resolved `aaa_profile` has a `dot1x_auth_profile` | `unsupported`; Classic dependency lifecycle is explicitly called ambiguous (§5) | `unsupported`; target schema supports `WPA2_ENTERPRISE`/`WPA3_ENTERPRISE_*` but the profile-attach rejection blocks it | **unsupported** |

**Dependencies (all modes)**: `vlan:{vlan_id}` and, once unblocked, `aaa_profile:{name}` (`_dependencies` call in `build_migration_plan`, `pipeline/aos8_migration.py:723-726`). **Apply order**: 50. **Secrets**: WPA2/WPA3 Personal passphrases and any MPSK/WEP key material are never persisted in the candidate payload — `payload["security"]` carries only presence booleans (`passphrase_present`/`psk_hexkey_present`), and the raw `wpa_passphrase`/`wpa_hexkey`/`wepkey1`-`wepkey4` values are redacted wherever the full ssid_prof is otherwise retained in `unsupported_fields`. No apply-time secret-input flow exists yet because no secured mode has an adapter mapper.

### 6.3 AAA profiles

| | Classic Central | New Central |
|---|---|---|
| **Source fields/aliases** | `AOS8AAAProfile`: `profile-name`/`name`; `default_user_role`/`default-user-role`; `dot1x_auth_profile`/`dot1x-auth-profile`; `dot1x_default_role`/`dot1x-default-role`; `dot1x_server_group`/`dot1x-server-group`; `mac_auth_profile`/`mac-auth-profile`; `mac_default_role`/`mac-default-role`; `mac_server_group` aliases are `mac_server_group`/`mba_server_group`/`mac-server-group` (§3 item 1 — RESOLVED); `accounting_server_group` from `rad_acct_sg`/`radius-accounting-server-group` (`pipeline/aos8_parsers.py`) | same source |
| **Candidate payload** | `{"name","default_user_role","dot1x_auth_profile","dot1x_default_role","dot1x_server_group","mac_auth_profile","mac_default_role","mac_server_group","accounting_server_group"}` (`_aaa_payload`, `pipeline/aos8_migration.py:433-445`) | same |
| **Target method/path** | not implemented (falls to generic rejection) | `create_aaa_profile` → `POST /network-config/v1alpha1/aaa-profile/{name}` (`pipeline/aos8_target_adapters.py:819`) |
| **Schema/context** | n/a | `x-supportedDeviceType: [Gateway, Switch CX, Switch PVOS]` (`aaa-profile.json`) — **not** an AP concept; `device-function`/`persona` must resolve to a gateway or switch persona |
| **Payload fields actually mapped** | n/a | only `auth_role` (← `default_user_role`) and `acct_server_group` (← `accounting_server_group`); any of `dot1x_auth_profile`, `dot1x_default_role`, `dot1x_server_group`, `mac_auth_profile`, `mac_default_role`, `mac_server_group` being non-empty raises `AdapterError` (`:786-802`) |
| **Dependencies/apply order** | `role:{default_user_role, dot1x_default_role, mac_default_role}`, `dot1x_auth_profile:{...}`, `mac_auth_profile:{...}`, `server_group:{dot1x_server_group, mac_server_group, accounting_server_group}` (`pipeline/aos8_migration.py:711-720`); apply order 40 | same |
| **Preflight/read-back** | none | `get_aaa_profile` (`:825-831`) |
| **Secrets** | none directly | none directly (server-group/auth-server secrets flow through dependencies) |
| **Classification** | `unsupported` | **conditional** — only the simple (no dot1x/mac/server-group refs) subset is `exact` and tested (`tests/unit/test_aos8_target_adapters.py:307-323`); any profile carrying real 802.1X/MAC/server-group references is `unsupported` today |

### 6.4 Device 802.1X profiles

| | Classic Central | New Central |
|---|---|---|
| **Source fields** | `AOS8AuthProfile(auth_type="dot1x")`: `profile-name`/`name`, all else in `.settings` (`parse_auth_profiles`, `pipeline/aos8_parsers.py:399-419`) | same |
| **Candidate payload** | `{"name","auth_type":"dot1x"}` plus `unsupported_fields=profile.settings` (`pipeline/aos8_migration.py:562-577`) | same |
| **Target method/path** | none verified | `/network-config/v1alpha1/dot1xauth/{name}` (`ingestion/sources/openapi_specs/aaa-dot1xauth.json`) — no adapter mapper exists yet (`NewCentralAdapter` has no `_map_dot1x_auth_profile`) |
| **Schema context** | n/a | `x-supportedDeviceType: [Gateway, Switch CX, Switch PVOS]` — Gateway/switch concept, **not** used for AP WLAN 802.1X (that lives on the WLAN resource itself, `ArubaWlanSecurity_AuthServerConfig`) |
| **Dependencies/apply order** | referenced by `aaa_profile.dot1x_auth_profile`; apply order 20 | same |
| **Classification** | `unsupported` | **unsupported** (candidate IR exists; no adapter mapper) |

### 6.5 Device MAC-auth profiles

| | Classic Central | New Central |
|---|---|---|
| **Source fields** | `AOS8AuthProfile(auth_type="mac")`, same shape as 802.1X (`pipeline/aos8_parsers.py:399-419`) | same |
| **Target method/path** | none verified | `/network-config/v1alpha1/macauth/{name}` (`aaa-macauth.json`) — no adapter mapper exists |
| **Schema context** | n/a | `x-supportedDeviceType: [Gateway, Switch CX, Switch PVOS]` — same Gateway/switch-only caveat as §6.4 |
| **Classification** | `unsupported` | **unsupported** |

### 6.6 Central NAC auth-profile alternative

| | Classic Central | New Central |
|---|---|---|
| **Purpose** | n/a | Distinct auth-policy surface (`ingestion/sources/openapi_specs/cda-auth-profile.json`, `x-tag-group: "Central NAC"`; companion `cda-authz-policy.json`) covering MAB, MPSK, wired-profile, and EAP/custom-certificate authentication policy — `/network-config/v1alpha1/auth-profiles/{auth-profile-id}` and `/network-config/v1alpha1/authz-policies/{policy-id}` |
| **Relationship to AOS8 source** | n/a | **Never** an automatic conversion target for an AOS8 `dot1x_auth_profile`/`mac_auth_profile`/LDAP/TACACS server. Central NAC is an explicit, operator-selected alternative architecture, only to be offered as an option in tooling/UX copy — never auto-selected by the migration pipeline. |
| **Classification** | n/a | **manual** (by design — requires an explicit operator decision, not a mapping) |

### 6.7 Auth servers (RADIUS / RadSec / LDAP / TACACS)

| | Classic Central | New Central |
|---|---|---|
| **Source fields** | `AOS8AuthServer`: RADIUS from `radius_servers` (`rad_server_name`/`name`, `rad_host`/`host`); LDAP from `ldap_servers` (`ldap_server_name`/`name`, `ldap_host`/`host`); TACACS from `tacacs_servers` (`tacacs_server_name`/`name`, `tacacs_host`/`host`); all else in `.settings` (`parse_auth_servers`, `pipeline/aos8_parsers.py:497-527`) | same |
| **Candidate payload** | `{"name","server_type","host"}` plus `unsupported_fields=server.settings` (`pipeline/aos8_migration.py:517-536`) | same |
| **Target method/path** | none verified | `create_auth_server` → `/network-config/v1alpha1/auth-servers/{name}` (`pipeline/aos8_target_adapters.py:728-780`) |
| **Schema/enum** | n/a | `ArubaAuthServer_AuthServersauthServerSchema.type` ∈ `{RADIUS, LDAP, TACACS, WINDOWS, RFC3576, XMLAPI, RADSEC, LOCAL}` (`auth-server.json`); per-platform support varies (AP: RADIUS/LDAP/TACACS/XMLAPI; CX/PVOS: RADIUS/TACACS; GW: RADIUS/TACACS/WINDOWS/LDAP) |
| **Payload fields mapped** | n/a | Adapter **only** accepts `server_type == "radius"`; anything else raises `AdapterError` (`:730-733`). Allowed unsupported-field passthrough: `rad_authport`, `rad_acctport`, `rad_key`/`radius_key`/`radius_secret`/`shared_secret` (`:735-746`) |
| **Secrets** | n/a | `shared_secret` is resolved via `_secret_value(context, key, "shared_secret")` (`:754`) — an ephemeral, apply-time-only caller-supplied secret, never persisted; marked `sensitive_argument_fields=("shared_secret",)` for preview masking (`:775`) |
| **Preflight/read-back** | none | `get_auth_server` (`:768-776`) |
| **Dependencies/apply order** | referenced by `server_group.auth_servers`; apply order 10 | same |
| **Classification (RADIUS)** | `unsupported` | **exact** (tested, `tests/unit/test_aos8_target_adapters.py:280-306`) |
| **Classification (RadSec)** | `unsupported` | **conditional** — schema value `RADSEC` exists but no field-level audit or adapter code exists; must be scoped separately (§4) |
| **Classification (LDAP)** | `unsupported` | **unsupported** — explicitly rejected by the adapter (`:730-733`); the `ldap_admindn` over-redaction defect (§3 item 6) is now resolved, so this row is blocked solely by the adapter's explicit RADIUS-only rejection, not by secret handling |
| **Classification (TACACS)** | `unsupported` | **unsupported** — explicitly rejected by the adapter |

### 6.8 Server groups

| | Classic Central | New Central |
|---|---|---|
| **Source fields** | `AOS8ServerGroup`: `sg_name`/`profile-name`/`name`; `auth_server`/`auth-server` (list, each resolved via `_server_reference` to a bare name); `fail_thru`/`fail-through`; `load_balance`/`load-balance`; `derivation_rules_vlan_role`; all else in `.settings` (`parse_server_groups`, `pipeline/aos8_parsers.py`). Type-collision risk (§3 item 4) is now resolved at the migration-planning layer — see next row. | same |
| **Candidate payload** | `{"name","auth_servers","auth_server_entries","fail_through","load_balance","derivation_rules"}` (`pipeline/aos8_migration.py:591-599`) | same |
| **Target method/path** | none verified | `/network-config/v1alpha1/server-groups/{name}` (`auth-server-group.json`) — no adapter mapper exists yet |
| **Schema** | n/a | `ArubaAuthServerGroup_ServerGroupsserverGroupSchema.servers` is an **ordered** array (`server-name` + `position` + `match-rules`), `type` restricted to CX (`RADIUS`/`TACACS` mandatory there); `load-balance`/`load-balance-algo` are AP/Gateway concepts |
| **Dependencies/apply order** | resolved by `server_ids_by_name` keyed by name **and** server type (§3 item 4 — RESOLVED); a same-name collision across RADIUS/LDAP/TACACS is left unresolved with an explicit `auth_server_type_collisions` warning rather than guessed; apply order 20 | same |
| **Classification** | `unsupported` | **unsupported** (candidate IR + type-aware dependency graph exist; no adapter mapper yet) |

### 6.9 Roles

| | Classic Central | New Central |
|---|---|---|
| **Source fields** | `AOS8Role`: `rolename`/`role`/`name`/`profile-name`; `vlan`; `acl`/`access-list`; `captive-portal-profile` (known-lossy, `UNSUPPORTED_FIELDS["role"]["captive_portal_profile"]`, `pipeline/aos8_schema.py:255-262`) | same |
| **Candidate payload** | `{"name","vlan","acl"}` (Classic key is `"acl"`) | `{"name","vlan","policies"}` (New Central key is `"policies"`) — `_role_payload(new_central=bool)` branches the key name (`pipeline/aos8_migration.py:360-389`) |
| **Target method/path** | none verified (falls to generic rejection) | `create_role`/`update_role` → `POST/PUT /network-config/v1/roles/{name}` (`pipeline/aos8_target_adapters.py:706-715`), plus a required config-assignment (`:689-703`, see §2.1 divergence) |
| **Payload fields mapped** | n/a | only a normalized ACL value of `allowall`/`sys_allow_all` is accepted; anything else raises `AdapterError` — **custom AOS8 ACLs are never reduced to allow-all silently; they are blocked** (`:661-665`) |
| **Dependencies/apply order** | `vlan:{vlan}`, `policy:{acl}` (via `_policy_dependencies`, `:460-469`); apply order 30 | same |
| **Preflight/read-back** | none | `list_roles(full_list=True)` matched by identifier (`:718-725`) |
| **Classification** | `unsupported` | **conditional** — allow-all roles only are `exact`/tested (`tests/unit/test_aos8_target_adapters.py:150-192` pattern); any role with a real ACL is `manual`/`unsupported` pending §6.10 |

### 6.10 Gateway security policies vs. role ACLs (explicit distinction)

| Concept | Source | Classic target | New Central target | Notes |
|---|---|---|---|---|
| **AOS8 session ACL / "policy"** | `AOS8Policy`/`AOS8PolicyRule` — IPv4 rules from `acl_sess__v4policy` (or legacy `rule`/`rules`), IPv6 from `acl_sess__v6policy`; per-rule aliases `source/src/source-address/...`, `destination/dst/...`, `service/svc/protocol/application/app`, `action/permit/deny`, `log/logging` (`parse_policies`/`_parse_policy_rules`, `pipeline/aos8_parsers.py:266-338`) | none verified | none verified — **no adapter mapper exists**; would need to choose between CX role-ACL (`role-acl.json`, CX-only) and Gateway security policy (`policy.json`) depending on target persona | `unsupported` on both targets |
| **CX role ACL** | n/a (target-only concept) | n/a | `ArubaRoleAcl_RoleAclsSchemaGet.x-supportedDeviceType == ["Switch CX"]` — only valid when the target persona is a CX switch | Never apply a role-ACL write against a Gateway/AP persona |
| **Gateway security policy** | n/a (target-only concept) | n/a | `/network-config/v1alpha1/policies/{name}` (`policy.json`); an existing `delete_gw_policy` curated tool exists (`mcp_servers/config.py:2110-2121`) with **no corresponding create/update path audited yet** | Distinct object type from role ACLs; do not conflate |

**Classification**: `unsupported` for automatic policy/ACL object writes on both targets. `manual` recreation is the only currently safe path; any custom rule set must never be summarized as "allow-all" (per §4).

### 6.11 AP-group / device-group / profile assignment

| | Classic Central | New Central |
|---|---|---|
| **Source fields** | `AOS8ApGroup.profile_name`, `.virtual_ap_profiles` (list of VAP names, `parse_ap_groups`, `pipeline/aos8_parsers.py:12-33`) | same |
| **Candidate payload** | `{"name","wlan_profiles"}` (sorted VAP names mapped through `vap_to_wlan`, `pipeline/aos8_migration.py:770-792`) | same |
| **Target method/path** | none verified (falls to generic rejection) | no `_map_ap_group` method on `NewCentralAdapter`; falls to `unsupported` via `getattr(self, "_map_ap_group", None)` returning `None` (`:574-580`) |
| **Dependencies/apply order** | `wlan:{...}` per VAP (`:774-778`); apply order 60 | same |
| **Required operator input** | n/a | **Explicit** Device Group/scope selection and profile (WLAN/role/VLAN) assignment — there is no automatic 1:1 creation of a Device Group from an AOS8 AP group. VAP-to-WLAN dependency resolution must be validated against the actual export; an unresolved VAP now produces an explicit per-reference warning (§3 item 5 — RESOLVED) in addition to the generic dependency-not-present check, and never invents a target WLAN |
| **Classification** | `unsupported` | **unsupported** |

### 6.12 IPv4/IPv6 static routes

| | Classic Central | New Central |
|---|---|---|
| **Source fields** | `AOS8Route`: `address_family`; `destip`/`destination`; `destmask`/`netmask`; `nexthop`/`next-hop`; `nexthop1`/`secondary-next-hop`; `vlanid`/`vlan`; `cost`; `cost1`; `zero`; all else in `.settings` (`parse_routes`, `pipeline/aos8_parsers.py:531-568`) | same |
| **Candidate payload** | `{"address_family","destination","netmask","next_hop","secondary_next_hop","vlan_id","cost","secondary_cost","zero"}` (`pipeline/aos8_migration.py:824-838`) | same |
| **Target method/path (IPv4)** | none verified | `/network-config/v1alpha1/static-route/{name}` (`static-route.json`) — no adapter mapper; not yet implemented |
| **Schema notes** | n/a | `ArubaStaticRoute_Ipv4RouteCfg` keys by composite `prefix-vrf-nexthop-id`; `forwarding-type` enum (`NEXTHOP, INTERFACE, NULLROUTE, REJECT, VLAN, TUNNEL, IPSECMAP, CLUSTER`) is CX-only for that field; the exact Gateway/AP destination-write contract is **not verified live** |
| **IPv6** | n/a | `ArubaStaticRoute_Ipv6RouteCfg` — schema-expressible, same unverified-destination caveat, classified conditional pending live evidence |
| **Switch routes** | n/a | `l3-route.json` (`/l3-route`) is a **separate**, Switch-CX-only payload family (`ArubaL3Route_L3RouteSchemaGet.x-supportedDeviceType == ["Switch CX"]`) — never substitute for `static-route.json` on a Gateway/AP persona |
| **Dependencies/apply order** | `vlan:{vlan_id}` (`:846`); apply order 70 | same |
| **Classification** | `unsupported` | **conditional** for IPv4 (schema exists, destination contract unverified); **conditional** for IPv6 (same, plus narrower device support); `unsupported` for automatic Switch-CX `l3-route` selection without explicit persona confirmation |

### 6.13 VRRP / VRRPv6 / tracking

| | Classic Central | New Central |
|---|---|---|
| **Source fields** | `AOS8VRRP`: `address_family`; `id`(vrid); `{prefix}_ip`/`_vlan`/`_priority`/`_preempt`/`_shut`/`_adv_interval`/`_holdtime`/`_desc`/`_auth`; `tracking` dict assembled from `{prefix}_track_*` keys; all else in `.settings` (`parse_vrrp`, `pipeline/aos8_parsers.py:614-660`) where `prefix` is `vrrp` (IPv4) or `vrrp6` (IPv6) | same |
| **Candidate payload** | `{"address_family","vrid","virtual_ip","vlan_id","priority","preempt","shutdown","advertisement_interval","hold_time","description","authentication","tracking"}` (`pipeline/aos8_migration.py:856-869`) | same |
| **Target method/path** | none verified | Two **distinct** resources: `vrrp.json` → `/network-config/v1alpha1/vrrp-global` (LOCAL network profile, already wired as `_NETWORK_PROFILE_TYPES["vrrp"]` in `mcp_servers/config.py`); `vrrp-interface.json` → `/network-config/v1alpha1/vrrp` (Gateway-only, `ArubaVrrpInterface_VrrpprofileSchema`) |
| **Schema notes** | n/a | The interface-level profile keys `virtual-router` entries by composite `router-id-address-family`, not directly by VLAN — **VLAN-interface attachment is a separate, unproven step**. Tracking is a nested `ArubaVrrpInterface_VrrpTrackingConfiguration` block that has no mapping from `AOS8VRRP.tracking` today |
| **Dependencies/apply order** | `vlan:{vlan_id}` (`:876`); apply order 80 | same |
| **Classification** | `unsupported` | **unsupported** until VLAN-interface attachment and tracking normalization are proven live; do not treat `vrrp-global` and `vrrp-interface` as interchangeable |

### 6.14 Controllers / Mobility Conductors

| | Classic Central | New Central |
|---|---|---|
| **Source fields** | `AOS8Controller.name/ip_address/model/version`, remaining raw fields (`parse_controllers`, `pipeline/aos8_parsers.py:171-191`) | same |
| **Candidate payload** | `{"name","ip_address","model","version"}`, Classic-only candidate (no New Central candidate is emitted for controllers, `pipeline/aos8_migration.py:876-899`) | not emitted |
| **Target method/path** | none — explicit warning appended: "AOS8 controllers/Mobility Conductors are not migrated as New Central objects; onboard replacement gateways/APs individually" (`:894-897`) | n/a |
| **Classification** | **unsupported** (explicit, by design) | **unsupported** (no candidate emitted) |

## 7. Implementation order (post-matrix)

1. ~~Fix the §3 prerequisites (`mac_server_group` alias, `ldap_admindn` redaction split, server-group name+type dependency keying) in `pipeline/aos8_parsers.py`/`pipeline/aos8_migration.py`~~ — **done** by the `aos8-source-enrichment` todo (§3 items 1, 4, 5, 6); item 2 (WLAN security normalization) is partially done at the source layer only — no adapter/target mapping exists yet.
2. Enrich WLAN security parsing only far enough to represent the AOS8-side signal needed for `OPEN`, `WPA2_PERSONAL`-equivalent, `WPA3_SAE`-equivalent, `ENHANCED_OPEN`-equivalent, and enterprise dot1x/mac-chain references — without inventing an AOS8 enum this repository has not observed in a real export.
3. Implement New Central adapter mappers for `dot1x_auth_profile`, `mac_auth_profile`, `server_group` (ordered `servers`, type-aware), `policy`→Gateway-policy-or-CX-role-ACL (persona-branched), `route` (static-route, Gateway/AP), `vrrp`/`vrrp-interface` (with explicit VLAN-interface attachment), `ap_group`, in that dependency order.
4. Implement Classic adapter mappers only from official Classic docs/collections or validated live read shapes — never by reusing a New Central payload shape.
5. Resolve the §2.1 curated-tool-vs-spec divergence for config-assignments before any role/SHARED-object write path is trusted.
6. Re-run this matrix's classification for every row that moved from `unsupported`/`conditional` to `exact`, with the exact live evidence cited.

## 8. Verification and live-lab gate checklist

All of the following remain **read-only discovery and dry-run only**. No real write against any target is authorized by this document; a real write requires a separate, explicit confirmation naming the exact target and payload, per the 0.5 plan's write-gate contract (`pipeline/aos8_target_adapters.py` `WriteGateError`, `execute(..., dry_run: bool, confirmation: bool)`).

- [ ] Enable/configure the optional AOS8 backend and run `aos8_login`/session diagnostics.
- [ ] Export a bounded source configuration (`aos8_export_all`) and retain only sanitized evidence (never raw secrets).
- [ ] Resolve Classic group and New Central scope/persona/gateway/cluster context with **read-only** calls before constructing any `TargetContext`.
- [ ] Run `aos8_migration_plan` and confirm every candidate's `classification` in this matrix matches the adapter's actual `CandidateAction.status` (`ready`/`blocked`/`unsupported`) for a real export.
- [ ] For each `exact` row (currently: New Central `vlan`, `auth_server` RADIUS-only, `aaa_profile` simple subset, `role` allow-all subset, `wlan` open bridged/tunneled; Classic `wlan` open bridged only), run `dry_run=True` previews against a live lab scope/group and confirm the preflight read, payload, and read-back match.
- [ ] Confirm the §2.1 config-assignment divergence against a live read before trusting any role/SHARED-object assignment path.
- [ ] Confirm WPA3 transition (`wpa3-transition-mode-enable`) and RadSec (`auth-server type=RADSEC`) field-level behavior against a live WLAN/auth-server read before reclassifying either as `exact`.
- [ ] Confirm the Gateway IPv4/IPv6 static-route destination write contract (`prefix-vrf-nexthop-id`, `forwarding-type`) against a live Gateway read before reclassifying routes as `exact`.
- [ ] Confirm VRRP VLAN-interface attachment and tracking field mapping against a live Gateway VRRP read before reclassifying VRRP as anything but `unsupported`.
- [ ] Record every supported, lossy, blocked, and unverifiable finding back into this matrix (update classifications, never silently widen scope in adapter code without a matching matrix update).

## 9. Related documentation

- [`docs/product-workflows.md`](product-workflows.md) — AOS8 migration tool roadmap (`aos8_migration_plan`, `aos8_preview_migration_run`/`aos8_create_migration_run`/`aos8_apply_migration_run`, `aos8_verify_migration_run`) that this matrix gates.
- [`docs/capability-gap-matrix.md`](capability-gap-matrix.md) — ranked practical gap #1 ("Broader verified migration mappings and live evaluation") tracks the same scope at a summary level; this file is the detailed contract behind that ranked gap.
- `pipeline/aos8_schema.py`, `pipeline/aos8_parsers.py`, `pipeline/aos8_migration.py`, `pipeline/aos8_target_adapters.py` — the implementation this matrix constrains.
- `tests/unit/test_aos8_parsers.py`, `tests/unit/test_aos8_migration.py`, `tests/unit/test_aos8_target_adapters.py`, `tests/unit/test_aos8_migration_orchestrator.py`, `tests/unit/test_aos8_export_and_migration_tool.py` — current regression coverage; every row moved to `exact` in a future revision must gain a corresponding test here.
