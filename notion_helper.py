from notion_client import Client
from datetime import datetime, timedelta
import base64
import os
import urllib.parse

import requests as http_requests


# ── OAuth ────────────────────────────────────────────────────────────
NOTION_OAUTH_AUTHORIZE_URL = "https://api.notion.com/v1/oauth/authorize"
NOTION_OAUTH_TOKEN_URL = "https://api.notion.com/v1/oauth/token"
NOTION_API_VERSION = "2022-06-28"


def _notion_redirect_uri(redirect_uri=None):
    return (
        redirect_uri
        or os.getenv("NOTION_REDIRECT_URI")
        or "https://intelliplan.tech/oauth/notion/callback"
    )


def get_notion_auth_url(state, redirect_uri=None):
    """Build the Notion OAuth authorization URL (mirrors GCal's pattern).

    Notion public integrations don't support PKCE — only the `state`
    parameter is used to defeat CSRF. The session stores `state` until
    the callback arrives.
    """
    client_id = os.getenv("NOTION_CLIENT_ID")
    if not client_id:
        raise RuntimeError("NOTION_CLIENT_ID not configured.")
    params = {
        "client_id": client_id,
        "redirect_uri": _notion_redirect_uri(redirect_uri),
        "response_type": "code",
        "owner": "user",
        "state": state,
    }
    return f"{NOTION_OAUTH_AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"


def exchange_notion_code(code, redirect_uri=None):
    """Exchange an OAuth `code` for a long-lived workspace access token.

    Notion access tokens don't expire unless the user revokes them, so
    no refresh flow is needed (unlike Google). Returns the JSON payload
    from Notion which already contains workspace metadata.
    """
    client_id = os.getenv("NOTION_CLIENT_ID")
    client_secret = os.getenv("NOTION_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise RuntimeError("Notion OAuth credentials missing.")
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    resp = http_requests.post(
        NOTION_OAUTH_TOKEN_URL,
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/json",
            "Notion-Version": NOTION_API_VERSION,
        },
        json={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": _notion_redirect_uri(redirect_uri),
        },
        timeout=20,
    )
    data = resp.json()
    if resp.status_code >= 400 or "error" in data:
        raise Exception(f"Notion token exchange failed: {data}")
    # Normalise the fields we care about; keep the full payload too.
    return {
        "access_token": data.get("access_token"),
        "workspace_id": data.get("workspace_id"),
        "workspace_name": data.get("workspace_name"),
        "workspace_icon": data.get("workspace_icon"),
        "bot_id": data.get("bot_id"),
        "owner": data.get("owner"),
        "duplicated_template_id": data.get("duplicated_template_id"),
        "raw": data,
    }


def get_notion_client(token):
    return Client(auth=token)

def test_notion_token(token):
    """Return True if the token authenticates, else False. Used by the
    legacy connect endpoint that only needs a boolean."""
    ok, _ = test_notion_token_detail(token)
    return ok


def test_notion_token_detail(token):
    """Return (ok, info_or_error). On success info is the bot user dict
    so callers can populate workspace metadata for manual-token connections
    without an extra round-trip."""
    if not token:
        return False, "No token provided"
    try:
        client = get_notion_client(token)
        me = client.users.me()
        return True, me
    except Exception as e:
        # Notion's APIResponseError carries .code/.body — surface what we can.
        msg = getattr(e, "body", None) or str(e) or "Notion rejected the token"
        return False, msg

def get_notion_databases(token):
    """List databases the integration can read.

    Notion only returns objects that have been explicitly shared with the
    integration (under Notion's permission model the integration is a
    bot user). If this list is empty the user almost always needs to
    open a database in Notion and click "Connections -> IntelliPlan".
    """
    client = get_notion_client(token)
    results = client.search(filter={"property": "object", "value": "database"}).get("results", [])
    databases = []
    for db in results:
        title = "Untitled"
        title_arr = db.get("title", [])
        if title_arr:
            title = title_arr[0].get("plain_text", "Untitled")
        parent = db.get("parent", {}) or {}
        databases.append({
            "id": db["id"],
            "name": title,
            "url": db.get("url", ""),
            "parent_type": parent.get("type", ""),
            "last_edited": db.get("last_edited_time", ""),
        })
    return databases


def get_shared_pages(token):
    """Pages the integration can see — used as parent candidates when we
    auto-create an IntelliPlan tasks database."""
    client = get_notion_client(token)
    results = client.search(filter={"property": "object", "value": "page"}).get("results", [])
    pages = []
    for p in results:
        title = ""
        props = p.get("properties", {})
        for v in props.values():
            if v.get("type") == "title":
                arr = v.get("title", [])
                if arr:
                    title = arr[0].get("plain_text", "")
                    break
        if not title:
            # workspace-level pages put their title in object["title"]
            arr = p.get("title", []) or []
            if arr:
                title = arr[0].get("plain_text", "")
        pages.append({
            "id": p["id"],
            "name": title or "Untitled",
            "url": p.get("url", ""),
        })
    return pages


def create_intelliplan_database(token, parent_page_id, name="IntelliPlan Tasks"):
    """Create a new tasks database under a page the integration owns,
    pre-populated with the schema the rest of the code expects.

    Returns the new database's id. Lets the user go from "I just clicked
    Connect" to "I have a working sync target" in one step instead of
    having to build the schema by hand in Notion.
    """
    client = get_notion_client(token)
    db = client.databases.create(
        parent={"type": "page_id", "page_id": parent_page_id},
        title=[{"type": "text", "text": {"content": name}}],
        properties={
            "Name":     {"title": {}},
            "Due":      {"date": {}},
            "Priority": {"select": {"options": [
                {"name": "High",   "color": "red"},
                {"name": "Medium", "color": "yellow"},
                {"name": "Low",    "color": "green"},
            ]}},
            "Done":     {"checkbox": {}},
            "Course":   {"rich_text": {}},
        },
    )
    return db["id"]

def get_notion_tasks(token, database_id):
    """Pull tasks from a Notion database (all pages, following pagination)."""
    client = get_notion_client(token)
    results = []
    cursor = None
    # Notion returns at most 100 rows per page — follow the cursor so large
    # databases pull completely instead of silently stopping at 100.
    for _ in range(50):  # hard cap (5000 rows) to avoid a runaway loop
        resp = client.databases.query(
            database_id=database_id,
            **({"start_cursor": cursor} if cursor else {}),
        )
        results.extend(resp.get("results", []))
        if not resp.get("has_more"):
            break
        cursor = resp.get("next_cursor")
        if not cursor:
            break

    tasks = []
    for page in results:
        props = page.get("properties", {})
        
        # Extract title
        title = ""
        for key in ["Name", "Task", "Title", "title"]:
            if key in props and props[key].get("type") == "title":
                rich = props[key].get("title", [])
                if rich:
                    title = rich[0].get("plain_text", "")
                break
        
        if not title:
            continue
        
        # Extract due date
        due_date = None
        for key in ["Due", "Due Date", "Date", "Deadline"]:
            if key in props and props[key].get("type") == "date":
                date_obj = props[key].get("date")
                if date_obj:
                    due_date = date_obj.get("start", "")[:10]
                break
        
        # Extract status/priority
        priority = "Medium"
        for key in ["Priority", "Status", "Urgency"]:
            if key in props:
                if props[key].get("type") == "select":
                    val = (props[key].get("select") or {}).get("name", "")
                    if val.lower() in ["high", "urgent", "critical"]:
                        priority = "High"
                    elif val.lower() in ["low", "someday"]:
                        priority = "Low"
                break
        
        # Check completion
        done = False
        for key in ["Done", "Complete", "Completed", "Checkbox"]:
            if key in props and props[key].get("type") == "checkbox":
                done = props[key].get("checkbox", False)
                break
        
        if done:
            continue
        
        tasks.append({
            "id": page["id"],
            "notion_page_id": page["id"],
            "title": title,
            "due_date": due_date or "",
            "priority": priority,
            "source": "notion",
            "estimated_time": 60,
            "course": "Notion",
            "difficulty": "Medium",
            "color": {"High": "#ef4444", "Medium": "#f59e0b", "Low": "#22c55e"}.get(priority, "#f59e0b")
        })
    
    return tasks

def create_notion_task(token, database_id, title, due_date=None, priority="Medium"):
    """Create a new task in Notion."""
    client = get_notion_client(token)
    
    props = {
        "Name": {"title": [{"text": {"content": title}}]},
    }
    
    if due_date:
        props["Due"] = {"date": {"start": due_date}}
    
    props["Priority"] = {"select": {"name": priority}}
    
    page = client.pages.create(
        parent={"database_id": database_id},
        properties=props
    )
    return page["id"]

def update_notion_task(token, page_id, updates):
    """Update a Notion task."""
    client = get_notion_client(token)
    props = {}
    
    if "title" in updates:
        props["Name"] = {"title": [{"text": {"content": updates["title"]}}]}
    if "due_date" in updates and updates["due_date"]:
        props["Due"] = {"date": {"start": updates["due_date"]}}
    if "priority" in updates:
        props["Priority"] = {"select": {"name": updates["priority"]}}
    if "done" in updates:
        props["Done"] = {"checkbox": updates["done"]}
    
    client.pages.update(page_id=page_id, properties=props)

def complete_notion_task(token, page_id):
    """Mark a Notion task as done."""
    update_notion_task(token, page_id, {"done": True})


def get_upcoming_notion_tasks(token, database_id, days=7):
    """Notion analog of Google Calendar's `get_upcoming_events`.

    Returns the next `days` of tasks that have a due date and are not
    already marked done. Same dict shape as get_notion_tasks for easy
    merging into the unified dashboard payload.
    """
    if not token or not database_id:
        return []
    today = datetime.utcnow().date()
    horizon = today + timedelta(days=days)
    tasks = get_notion_tasks(token, database_id)
    upcoming = []
    for t in tasks:
        due = t.get("due_date") or ""
        if not due:
            continue
        try:
            d = datetime.strptime(due[:10], "%Y-%m-%d").date()
        except Exception:
            continue
        if today <= d <= horizon:
            upcoming.append(t)
    upcoming.sort(key=lambda x: x.get("due_date") or "")
    return upcoming


def add_schedule_to_notion(token, database_id, schedule_data):
    """Push a generated study schedule back into Notion as task rows.

    Mirrors `add_schedule_to_calendar`. Skips rows that already exist
    (same Name + Due) so re-running an export is idempotent. Returns
    (created_ids, skipped_count).
    """
    if not token or not database_id:
        return [], 0
    client = get_notion_client(token)

    # Pre-fetch existing pages so we don't duplicate when the user
    # re-exports the same plan.
    try:
        existing_rows = client.databases.query(database_id=database_id).get("results", [])
    except Exception:
        existing_rows = []
    existing_keys = set()
    for page in existing_rows:
        props = page.get("properties", {})
        title = ""
        for key in ["Name", "Task", "Title", "title"]:
            if key in props and props[key].get("type") == "title":
                rich = props[key].get("title", [])
                if rich:
                    title = rich[0].get("plain_text", "")
                break
        due_date = ""
        for key in ["Due", "Due Date", "Date", "Deadline"]:
            if key in props and props[key].get("type") == "date":
                date_obj = props[key].get("date") or {}
                due_date = (date_obj.get("start") or "")[:10]
                break
        if title:
            existing_keys.add((title.lower(), due_date))

    created_ids = []
    skipped = 0
    for day in schedule_data.get("schedule", []):
        date_str = day.get("date", "")
        for block in day.get("blocks", []):
            if block.get("is_break"):
                continue
            assignment = block.get("assignment") or "Study"
            course = block.get("course") or ""
            time_slot = block.get("time_slot", "")
            duration = block.get("duration_minutes", 30)
            title = f"📚 {assignment}"
            key = (title.lower(), date_str)
            if key in existing_keys:
                skipped += 1
                continue

            props = {"Name": {"title": [{"text": {"content": title}}]}}
            if date_str:
                props["Due"] = {"date": {"start": date_str}}
            try:
                page = client.pages.create(
                    parent={"database_id": database_id},
                    properties=props,
                    children=[
                        {
                            "object": "block",
                            "type": "paragraph",
                            "paragraph": {
                                "rich_text": [{
                                    "type": "text",
                                    "text": {
                                        "content": (
                                            f"Course: {course}\n"
                                            f"Time: {time_slot}\n"
                                            f"Duration: {duration} min\n"
                                            f"\nCreated by IntelliPlan"
                                        )
                                    }
                                }]
                            }
                        }
                    ],
                )
                created_ids.append(page["id"])
                existing_keys.add(key)
            except Exception as e:
                print(f"[Notion export] failed to create page: {e}")
    return created_ids, skipped