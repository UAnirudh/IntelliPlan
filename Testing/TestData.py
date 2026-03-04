# Test data for the assignment scheduling system


Assignment_1 = {
    "title": "Homework 1",
    "course": "Pre-Calc",
    "due_date": "2026-03-04",
    "estimated_time": 30,
    "priority": "Low"
    }
Assignment_2 = {
    "title": "Homework 2",
    "course": "History",
    "due_date": "2026-03-05",
    "estimated_time": 60,
    "priority": "Medium"
    }
Assignment_3 = {
    "title": "Homework 3",
    "course": "English",
    "due_date": "2026-03-07",
    "estimated_time": 15,
    "priority": "Low"
    }
Assignment_4 = {
    "title": "Homework 4",
    "course": "Biology",
    "due_date": "2026-03-03",
    "estimated_time": 5,
    "priority": "High"
    }
assignments = [Assignment_1, Assignment_2, Assignment_3, Assignment_4]
def sort_by_due_date(assignments):
    return sorted(assignments, key=lambda x: x['due_date'])
def sort_by_priority(assignments):
    priority_order = {"High": 1, "Medium": 2, "Low": 3}
    return sorted(assignments, key=lambda x: priority_order[x['priority']])
def generate_schedule(assignments):
    sorted_assignments = sort_by_due_date(assignments)
    schedule = []
    priority_order = {"High": 1, "Medium": 2, "Low": 3}
    return sorted(assignments, key=lambda x: (priority_order[x['priority']], x['due_date']))
    

# print(sort_by_due_date(assignments))
# print(sort_by_priority(assignments))
#print(generate_schedule(assignments))
for assignment in generate_schedule(assignments):
    print(f"{assignment['title']} - {assignment['course']} - Due: {assignment['due_date']} - Priority: {assignment['priority']}")
# Generate Schedule prioritizes by priority first, then by due date. So Homework 4 (High) comes before Homework 2 (Medium) and Homework 1 and 3 (Low). Among the Low priority assignments, Homework 1 comes before Homework 3 because it has an earlier due date.