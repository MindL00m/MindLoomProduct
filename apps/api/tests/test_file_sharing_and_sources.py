"""Sharing files, email attachments on drafts, Drive and email-attachment browsing, web contacts."""

from __future__ import annotations

import email
import email.policy
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

from core.models import WebResult
from loombot import ask_agent, web_search
from loombot import files as files_service
from shuttle import notification_delivery
from weaver import file_sources


# --- Web search: who to contact -----------------------------------------------------

RESULTS = [
    WebResult(title="Acme API help", url="https://acme.com/support",
              snippet="Questions about the API? Email api-support@acme.com or call +1 (555) 010-2000."),
    WebResult(title="Acme press", url="https://acme.com/press", snippet="Press: press@acme.com"),
]


def test_contacts_keep_only_details_found_in_the_results() -> None:
    real = web_search._verified_contact(
        {"name": "Acme API Support", "email": "API-support@acme.com", "phone": "+1 555 010 2000",
         "url": "https://acme.com/support", "source": 1}, RESULTS)
    assert (real.email, real.phone, real.url, real.source) == (
        "api-support@acme.com", "+1 555 010 2000", "https://acme.com/support", 1)

    invented = web_search._verified_contact(
        {"name": "Jane Doe", "email": "jane@acme.com", "phone": "+1 555 999 9999", "source": 2}, RESULTS)
    # The made-up email and phone are dropped; the cited page is kept as the way to reach them.
    assert (invented.email, invented.phone, invented.url) == ("", "", "https://acme.com/press")
    assert web_search._verified_contact({"name": "Nobody", "email": "x@y.com"}, RESULTS) is None


def test_contacts_section_lists_web_and_internal_people() -> None:
    contacts = [web_search.WebContact(name="Acme API Support", email="api-support@acme.com",
                                      url="https://acme.com/support", source=1)]
    internal = [SimpleNamespace(name="Priya Shah", email="priya@us.com", reason="Owns the Acme integration")]
    section = web_search.contacts_section(contacts, internal)
    assert section.startswith("**Who to contact**")
    assert "**Acme API Support**: api-support@acme.com" in section and "[WEB: 1]" in section
    assert "Inside your company: **Priya Shah** (priya@us.com), Owns the Acme integration" in section
    assert "No contact details" in web_search.contacts_section([], [])


async def test_search_with_contacts_merges_a_contact_search(monkeypatch) -> None:
    queries: list[str] = []

    async def fake_search(query, **kwargs):
        queries.append(query)
        if "contact" in query:
            return {"query": query, "results": [RESULTS[1], RESULTS[0]]}
        return {"query": query, "results": [RESULTS[0]]}

    monkeypatch.setattr(web_search, "search_web", fake_search)
    found = await web_search.search_with_contacts("acme api rate limits")
    assert queries == ["acme api rate limits", "acme api rate limits contact email support"]
    assert [r.url for r in found["results"]] == ["https://acme.com/support", "https://acme.com/press"]


# --- Email drafts with attachments ----------------------------------------------------


def test_emails_carry_attachments_and_several_recipients() -> None:
    message = notification_delivery.build_email(
        sender="me@us.com", recipient="a@x.com, b@y.com", subject="Report", body="Attached.",
        attachments=[("Q3 report.pdf", b"%PDF-1.4 data", "application/pdf")],
    )
    parsed = email.message_from_bytes(message.as_bytes(), policy=email.policy.default)
    assert parsed["To"] == "a@x.com, b@y.com"
    files = [(part.get_filename(), part.get_content_type(), part.get_content()) for part in parsed.iter_attachments()]
    assert files == [("Q3 report.pdf", "application/pdf", b"%PDF-1.4 data")]


async def test_gmail_uses_the_upload_endpoint_for_attachments(monkeypatch) -> None:
    requests: list[httpx.Request] = []
    real_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"id": "sent-1"})

    async def token(org_id, user_id):
        return "tok", "me@us.com"

    monkeypatch.setattr(notification_delivery, "_workspace_access_token", token)
    monkeypatch.setattr(
        notification_delivery.httpx, "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw),
    )
    sent = await notification_delivery.send_user_gmail(
        org_id="o", user_id="u", recipient="a@x.com", subject="s", body="b",
        attachments=[("a.csv", b"x,y\n", "text/csv")],
    )
    assert sent == "sent-1"
    assert requests[0].url.path == "/upload/gmail/v1/users/me/messages/send"
    assert requests[0].url.params["uploadType"] == "media"
    assert requests[0].headers["Content-Type"] == "message/rfc822"
    assert b'filename="a.csv"' in requests[0].content


async def test_attachments_are_capped(monkeypatch) -> None:
    async def big(org_id, user_id, file_id):
        return b"x" * (files_service.MAX_EMAIL_ATTACHMENT_BYTES + 1), {"name": "big.pdf", "mime_type": "application/pdf"}

    monkeypatch.setattr(files_service, "get_file_bytes", big)
    with pytest.raises(HTTPException) as raised:
        await files_service.files_as_attachments("o", "u", ["f1"])
    assert raised.value.status_code == 413


async def test_ask_attaches_named_files_to_the_email_draft(monkeypatch) -> None:
    async def find(org_id, user_id, name):
        if name.lower().startswith("q3"):
            return {"file_id": "f-q3", "name": "Q3 report.docx", "kind": "docx", "size_bytes": 1200}
        return None

    monkeypatch.setattr(files_service, "find_file_by_name", find)
    result, _expert, _pr, _ws, draft = await ask_agent._run_tool(
        name="propose_email",
        arguments={"recipient_email": "priya@us.com", "subject": "Q3 report", "body": "Here it is.",
                   "attachment_names": ["Q3 report", "budget.xlsx"]},
        org_id="o", user_id="u", people_cache={},
    )
    assert [(a.file_id, a.name) for a in draft.attachments] == [("f-q3", "Q3 report.docx")]
    assert result["attachments"] == ["Q3 report.docx"]
    assert result["missing_attachments"] == ["budget.xlsx"]


# --- Sharing ------------------------------------------------------------------------


class _Session:
    def __init__(self, members):
        self.members = members

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def execute(self, statement, params=None):
        sql = str(statement)
        if "lower(email) = ANY" in sql:
            rows = [m for m in self.members if m["email"] in params["emails"]]
            return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: rows))
        return SimpleNamespace(scalar_one_or_none=lambda: "Alex Kim")


def _share_fakes(monkeypatch, *, google=True):
    calls: dict = {"mindloom": [], "email": []}
    members = [{"user_id": "u-priya", "name": "Priya", "email": "priya@us.com"},
               {"user_id": "u-me", "name": "Alex Kim", "email": "me@us.com"}]

    async def get_file(org_id, user_id, file_id):
        return {"file_id": file_id, "name": "Plan.docx"}

    async def share_in(org_id, user_id, file_id, ids, *, note=None):
        calls["mindloom"].append((ids, note))
        return {"shared": [{"user_id": i, "name": "Priya", "email": "priya@us.com", "file_name": "Plan.docx"} for i in ids]}

    async def attachments(org_id, user_id, ids):
        return [("Plan.docx", b"PK", "application/x")]

    async def send(**kwargs):
        calls["email"].append(kwargs)
        return "m1"

    async def connected(*args):
        return google

    monkeypatch.setattr(files_service, "get_session_factory", lambda: (lambda: _Session(members)))
    monkeypatch.setattr(files_service, "get_file", get_file)
    monkeypatch.setattr(files_service, "share_in_mindloom", share_in)
    monkeypatch.setattr(files_service, "files_as_attachments", attachments)
    monkeypatch.setattr(notification_delivery, "send_user_gmail", send)
    monkeypatch.setattr("weaver.integrations.has_google_workspace_connection", connected)
    return calls


async def test_share_in_mindloom_copies_to_members_and_emails_everyone(monkeypatch) -> None:
    calls = _share_fakes(monkeypatch)
    result = await files_service.share_file(
        "o", "u-me", "f1", ["Priya@us.com", "vendor@other.com"],
        send_email=True, in_mindloom=True, message="Please review by Friday.",
    )
    assert calls["mindloom"] == [(["u-priya"], "Please review by Friday.")]
    assert result["not_in_mindloom"] == ["vendor@other.com"]
    sent = calls["email"][0]
    assert sent["recipient"] == "Priya@us.com, vendor@other.com"
    assert sent["subject"] == "Alex Kim shared Plan.docx with you"
    assert "Please review by Friday." in sent["body"] and sent["attachments"][0][0] == "Plan.docx"


async def test_share_in_mindloom_only_needs_no_gmail(monkeypatch) -> None:
    calls = _share_fakes(monkeypatch, google=False)
    result = await files_service.share_file(
        "o", "u-me", "f1", ["priya@us.com"], send_email=False, in_mindloom=True,
    )
    assert calls["email"] == [] and [s["user_id"] for s in result["shared_in_mindloom"]] == ["u-priya"]
    with pytest.raises(HTTPException) as raised:
        await files_service.share_file("o", "u-me", "f1", ["priya@us.com"], send_email=True)
    assert "Connect Google Workspace" in raised.value.detail
    with pytest.raises(HTTPException):
        await files_service.share_file("o", "u-me", "f1", ["priya@us.com"], send_email=False)


async def test_share_rejects_sharing_with_only_yourself(monkeypatch) -> None:
    with pytest.raises(HTTPException):
        await files_service.share_in_mindloom("o", "u-me", "f1", ["u-me"])


def test_file_metadata_reports_who_shared_it() -> None:
    row = {
        "file_id": "f", "name": "Plan.docx", "kind": "docx", "mime_type": None, "size_bytes": 1,
        "row_count": 0, "column_count": 0, "last_edited_by": "user", "can_undo": False,
        "created_at": None, "updated_at": None, "based_on_file_id": None, "monitor_enabled": False,
        "monitor_instructions": None, "monitor_started_at": None, "shared_by_user_id": "u-priya",
        "shared_by_name": "Priya", "shared_note": "FYI", "shared_at": "2026-10-24", "share_seen_at": None,
    }
    meta = files_service._meta(row)
    assert (meta["shared_by_name"], meta["share_seen"]) == ("Priya", False)
    assert files_service._meta({**row, "shared_at": None, "shared_by_name": None})["share_seen"] is True


# --- Google Drive and email attachments in Files -----------------------------------------


def test_drive_items_map_google_formats_and_shortcuts() -> None:
    doc = file_sources._drive_item({"id": "1", "name": "Plan", "mimeType": "application/vnd.google-apps.document"})
    assert (doc["kind"], doc["type_label"], doc["can_import"]) == ("docx", "Google Doc", True)
    shortcut = file_sources._drive_item({
        "id": "s", "name": "Budget", "mimeType": "application/vnd.google-apps.shortcut",
        "shortcutDetails": {"targetId": "t", "targetMimeType": "application/vnd.google-apps.spreadsheet"},
    })
    assert (shortcut["id"], shortcut["kind"]) == ("t", "xlsx")
    folder = file_sources._drive_item({"id": "f", "name": "Q3", "mimeType": file_sources.FOLDER})
    assert (folder["kind"], folder["can_preview"]) == ("folder", False)


def _drive(monkeypatch, handler) -> list[httpx.Request]:
    requests: list[httpx.Request] = []
    real_client = httpx.AsyncClient

    def recording(request):
        requests.append(request)
        return handler(request)

    async def token(org_id, user_id):
        return "drive-token"

    monkeypatch.setattr(file_sources, "_drive_token", token)
    monkeypatch.setattr(
        file_sources.httpx, "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(recording), **kw),
    )
    return requests


async def test_drive_listing_builds_safe_queries(monkeypatch) -> None:
    requests = _drive(monkeypatch, lambda r: httpx.Response(200, json={"files": [], "nextPageToken": "n"}))
    await file_sources.list_drive_files("o", "u")
    await file_sources.list_drive_files("o", "u", folder_id="root")
    page = await file_sources.list_drive_files("o", "u", search="O'Brien plan")
    queries = [r.url.params["q"] for r in requests]
    assert queries[0] == f"trashed = false and mimeType != '{file_sources.FOLDER}'"
    assert queries[1] == "trashed = false and 'root' in parents"
    assert queries[2] == "trashed = false and name contains 'O\\'Brien plan'"
    assert requests[0].headers["Authorization"] == "Bearer drive-token"
    assert page["next_page_token"] == "n"


async def test_google_docs_are_exported_for_preview(monkeypatch) -> None:
    from loombot.docx_builder import markdown_to_docx

    docx_bytes = markdown_to_docx("# Plan\n\nShip in **May**.")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/export"):
            return httpx.Response(200, content=docx_bytes)
        return httpx.Response(200, json={"id": "d1", "name": "Plan", "mimeType": "application/vnd.google-apps.document"})

    requests = _drive(monkeypatch, handler)
    preview = await file_sources.drive_preview("o", "u", "d1")
    assert requests[1].url.params["mimeType"].endswith("wordprocessingml.document")
    assert preview["kind"] == "docx" and preview["item"]["download_name"] == "Plan.docx"
    assert "Ship in **May**." in preview["markdown"]


def test_csv_previews_render_as_a_sheet() -> None:
    preview = file_sources.preview_bytes("csv", b"item,price\nflour,4.50\n", "prices.csv")
    assert preview["kind"] == "xlsx" and preview["sheets"][0]["rows"][1] == ["flour", "4.50"]


def test_attachment_source_ids_split_with_colons_in_file_names() -> None:
    parts = file_sources.split_attachment_source_id("me@us.com:18c2:1:Q3: final.pdf")
    assert parts == {"account": "me@us.com", "message_id": "18c2", "index": 1, "filename": "Q3: final.pdf"}


async def test_email_attachments_list_only_the_users_mailboxes(monkeypatch) -> None:
    seen: dict = {}

    async def accounts(org_id, user_id):
        return {"me@us.com": "gmail"}

    async def rows(org_id, mailbox_accounts, **kwargs):
        seen["accounts"] = mailbox_accounts
        return [
            {"external_id": "me@us.com:m1:0:INV-2041.pdf", "document_id": "d1", "created_at": None},
            {"external_id": "me@us.com:m2:0:notes.txt", "document_id": "d2", "created_at": None},
        ]

    async def documents(org_id, ids):
        return {
            "d1": {"title": "INV-2041.pdf", "author": "Acme Billing", "source_location": "Gmail INBOX: October invoice",
                   "sent_at": "2026-10-21T00:00:00+00:00"},
            "d2": {"title": "notes.txt", "author": "Priya", "source_location": "Gmail INBOX: Notes", "sent_at": None},
        }

    monkeypatch.setattr(file_sources, "_mailbox_accounts", accounts)
    monkeypatch.setattr(file_sources, "_attachment_rows", rows)
    monkeypatch.setattr(file_sources, "_documents", documents)
    listed = await file_sources.list_email_attachments("o", "u", search="invoice")
    assert seen["accounts"] == ["me@us.com"]
    assert [(a["filename"], a["subject"], a["from"], a["file_kind"]) for a in listed["attachments"]] == [
        ("INV-2041.pdf", "October invoice", "Acme Billing", "pdf"),
    ]


async def test_unknown_attachment_ids_are_not_found(monkeypatch) -> None:
    async def accounts(org_id, user_id):
        return {"me@us.com": "gmail"}

    async def no_rows(org_id, mailbox_accounts, **kwargs):
        return []

    monkeypatch.setattr(file_sources, "_mailbox_accounts", accounts)
    monkeypatch.setattr(file_sources, "_attachment_rows", no_rows)
    with pytest.raises(HTTPException) as raised:
        await file_sources.email_attachment_detail("o", "u", "someone-elses-doc")
    assert raised.value.status_code == 404
