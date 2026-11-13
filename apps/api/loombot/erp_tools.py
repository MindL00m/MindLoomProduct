"""NetSuite and SAP in Ask.

Ask gets the same four tools automations have (``shuttle/skill_automation.py``), with one
difference. An automation pauses for approval before a change; Ask answers in one turn, so
a change is not made at all: the tool drafts it, the reply carries a card, and the record
is created or changed only when the person presses Approve on that card
(``POST /integrations/erp/apply``).
"""

from __future__ import annotations

import copy
from typing import Any

from fastapi import HTTPException

from core.models import ProposedErpChange

NETSUITE_TOOLS = ("netsuite_query", "netsuite_record")
SAP_TOOLS = ("sap_read", "sap_write")
NAMES = frozenset(NETSUITE_TOOLS + SAP_TOOLS)
_DRAFT_NOTE = (
    " In Ask this does NOT change anything: it drafts the change and the user sees a card to "
    "approve. Say a draft is ready for review; never say the record was created or changed."
)

RULES = """\
NetSuite and SAP rules (only when these tools are offered):
- For questions about customers, vendors, items, orders, invoices, stock or balances in the
  company's ERP, read live with netsuite_query (SuiteQL) or sap_read (OData). Select only
  the fields needed and keep pages small. Quote figures exactly as returned.
- If a query fails on a field or record name, read one record or the service's list first
  to learn the names, then try again. Do not guess twice.
- To create or change a record, call netsuite_record (create or update) or sap_write once
  with the exact values. That drafts the change for the user's approval; it does not make
  it. Before drafting a create, look for a record that already exists.
- Never invent record IDs, customer names or amounts. Ask when a value is missing.
"""


def _spec(name: str) -> dict[str, Any]:
    from shuttle import skill_automation

    spec = copy.deepcopy(next(t for t in skill_automation.ENGINE_TOOLS if t["function"]["name"] == name))
    if name in {"netsuite_record", "sap_write"}:
        description = spec["function"]["description"].replace("pause for approval", "are drafted for approval")
        spec["function"]["description"] = description.replace("Always pauses for approval. ", "") + _DRAFT_NOTE
    return spec


async def connected(org_id: str, user_id: str) -> tuple[bool, bool]:
    """(NetSuite connected, SAP connected) for this person. A lookup that fails means no."""

    from weaver import netsuite, sap

    async def check(probe: Any) -> bool:
        try:
            return bool(await probe(org_id, user_id))
        except Exception:  # noqa: BLE001 - Ask must still answer when the lookup fails
            return False

    return await check(netsuite.is_connected), await check(sap.is_connected)


async def ask_specs(org_id: str, user_id: str) -> list[dict[str, Any]]:
    """The tools to offer this person in Ask: only for the systems they connected."""

    has_netsuite, has_sap = await connected(org_id, user_id)
    names = (NETSUITE_TOOLS if has_netsuite else ()) + (SAP_TOOLS if has_sap else ())
    return [_spec(name) for name in names]


def _draft(name: str, arguments: dict[str, Any]) -> ProposedErpChange | dict[str, str]:
    action = str(arguments.get("action") or "").strip().lower()
    fields = arguments.get("fields")
    if action not in {"create", "update"}:
        return {"error": "action must be create or update."}
    if not isinstance(fields, dict) or not fields:
        return {"error": "fields must be an object of field names and values."}
    if name == "netsuite_record":
        target, record_id = str(arguments.get("record_type") or "").strip(), str(arguments.get("id") or "").strip()
        if not target or (action == "update" and not record_id):
            return {"error": "Give record_type, and id for an update."}
        return ProposedErpChange(system="netsuite", action=action, target=target,
                                 record_id=record_id or None, fields=fields)
    target = str(arguments.get("path") or "").strip()
    if not target:
        return {"error": "Give the path of the record set or record."}
    return ProposedErpChange(system="sap", action=action, target=target, fields=fields)


async def run_for_ask(name: str, arguments: dict[str, Any], *, org_id: str, user_id: str,
                      changes: list[ProposedErpChange]) -> dict[str, Any]:
    """Run a read now; turn a write into a draft the person approves on a card."""

    from weaver import netsuite, sap

    try:
        if name == "netsuite_query":
            return await netsuite.query(
                org_id, user_id, arguments.get("query"), limit=arguments.get("limit"), offset=arguments.get("offset"),
                params=arguments.get("params") if isinstance(arguments.get("params"), list) else None)
        if name == "sap_read":
            return await sap.read(org_id, user_id, arguments.get("path"), arguments.get("query"))
        action = str(arguments.get("action") or "").strip().lower()
        if name == "netsuite_record" and action == "fields":
            return await netsuite.describe_record(org_id, user_id, arguments.get("record_type"))
        if name == "netsuite_record" and action == "get":
            if not arguments.get("id"):
                return {"error": "get needs the record's id."}
            return await netsuite.get_record(org_id, user_id, arguments.get("record_type"), arguments.get("id"))
    except HTTPException as exc:
        return {"error": str(exc.detail)}
    if changes:
        # One card per reply: a second draft would be announced but never shown.
        return {"error": "One change at a time. A draft is already waiting for the user's approval in this reply."}
    try:
        draft = _draft(name, arguments)
    except ValueError:
        return {"error": "That record type, path or id is not valid."}
    if isinstance(draft, dict):
        return draft
    changes.append(draft)
    return {"drafted": True, "note": "Nothing was changed. The user sees a card and approves or cancels it."}


async def apply_change(org_id: str, user_id: str, change: ProposedErpChange) -> dict[str, Any]:
    """Make an approved change, as the person who approved it, and record it (worked or not)."""

    from weaver import erp_audit, netsuite, sap

    entry = dict(system=change.system, action=change.action, target=change.target, record_id=change.record_id,
                 fields=change.fields, source="ask")
    try:
        if change.system == "netsuite":
            if change.action == "create":
                result = await netsuite.create_record(org_id, user_id, change.target, change.fields)
            elif not change.record_id:
                raise HTTPException(status_code=422, detail="An update needs the record's id.")
            else:
                result = await netsuite.update_record(org_id, user_id, change.target, change.record_id, change.fields)
        else:
            write = sap.create if change.action == "create" else sap.update
            result = await write(org_id, user_id, change.target, change.fields)
    except HTTPException as exc:
        await erp_audit.record(org_id, user_id, ok=False, detail=exc.detail, **entry)
        raise
    await erp_audit.record(org_id, user_id, ok=True, **{**entry, "record_id": change.record_id or result.get("id")})
    return result
