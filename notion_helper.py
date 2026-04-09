from notion_client import Client
from datetime import datetime
import os

def get_notion_client(token):
    return Client(auth=token)

def test_notion_token(token):
    try:
        client = get_notion_client(token)
        client.users.me()
        return True
    except:
        return False

def get_notion_databases(token):
    """List available databases the user can access."""
    client = get_notion_client(token)
    results = client.search(filter={"property": "object", "value": "database"}).get("results", [])
    databases = []
    for db in results:
        title = ""
        title_arr = db.get("title", [])
        if title_arr:
            title = title_arr[0].get("plain_text", "Untitled")
        databases.append({"id": db["id"], "name": title})
    return databases

def get_notion_tasks(token, database_id):
    """Pull tasks from a Notion database."""
    client = get_notion_client(token)
    results = client.databases.query(database_id=database_id).get("results", [])
    
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