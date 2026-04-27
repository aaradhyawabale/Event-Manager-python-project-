# 🎓 College Event Management System

A beginner-friendly web app built with **Flask + SQLite** for managing college events.

---

## 📁 Project Structure

```
event-management-system/
│── app.py              ← Main Flask app (all routes + DB logic)
│── database.db         ← Auto-created on first run
│── /templates          ← HTML templates (Jinja2)
│     ├── base.html
│     ├── index.html
│     ├── register.html
│     ├── admin_login.html
│     ├── admin_dashboard.html
│     ├── create_event.html
│     └── view_registrations.html
└── /static
      └── style.css     ← Custom CSS
```

---

## ⚙️ Setup & Run

### Step 1 — Install Flask
```bash
pip install flask
```

### Step 2 — Run the app
```bash
cd event-management-system
python app.py
```

### Step 3 — Open in browser
```
http://127.0.0.1:5000
```

---

## 🌐 URLs to Test

| URL | Description |
|-----|-------------|
| `http://localhost:5000/` | Student view — all events |
| `http://localhost:5000/register/1` | Register for event #1 |
| `http://localhost:5000/admin/login` | Admin login |
| `http://localhost:5000/admin/dashboard` | Manage events |
| `http://localhost:5000/admin/create` | Create a new event |
| `http://localhost:5000/admin/registrations/1` | View registrations for event #1 |

---

## 🔐 Admin Credentials
- **Username:** `admin`
- **Password:** `admin123`

---

## ⚠️ Common Errors & Fixes

| Error | Fix |
|-------|-----|
| `ModuleNotFoundError: No module named 'flask'` | Run `pip install flask` |
| `TemplateNotFound` | Make sure you're running `python app.py` from inside the project folder |
| `database.db not found` | It's auto-created on first run — just start the app |
| Port already in use | Change port: `app.run(debug=True, port=5001)` |

---

## 🧠 Viva Prep — Key Concepts

- **Flask** → Python web framework; handles routes (URLs) and renders HTML
- **SQLite** → Lightweight file-based database; no server needed
- **Jinja2** → Template engine; lets you use Python logic inside HTML
- **Sessions** → Used to remember if admin is logged in
- **Flash messages** → One-time messages (success/error) shown after actions
- **Foreign Key** → `registrations.event_id` links to `events.id`
