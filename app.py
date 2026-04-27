"""
College Event Management System
--------------------------------
Backend: Flask (Python)
Database: SQLite
Author: Your Name
"""

from flask import Flask, render_template, request, redirect, url_for, session, flash
import sqlite3
import os

app = Flask(__name__)
app.secret_key = "college_event_secret_key"  # Required for session/flash

# ─────────────────────────────────────────────
# DATABASE SETUP
# ─────────────────────────────────────────────

def get_db():
    """Connect to the SQLite database."""
    conn = sqlite3.connect("database.db")
    conn.row_factory = sqlite3.Row  # Allows dict-like access to rows
    return conn

def init_db():
    """Create tables if they don't exist."""
    conn = get_db()
    cursor = conn.cursor()

    # Events table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            title       TEXT NOT NULL,
            description TEXT NOT NULL,
            date        TEXT NOT NULL,
            venue       TEXT NOT NULL
        )
    """)

    # Registrations table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS registrations (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id    INTEGER NOT NULL,
            name        TEXT NOT NULL,
            email       TEXT NOT NULL,
            department  TEXT NOT NULL,
            FOREIGN KEY (event_id) REFERENCES events(id)
        )
    """)

    conn.commit()
    conn.close()

# ─────────────────────────────────────────────
# PUBLIC ROUTES
# ─────────────────────────────────────────────

@app.route("/")
def index():
    """Show all events to students."""
    conn = get_db()
    events = conn.execute("SELECT * FROM events ORDER BY date ASC").fetchall()
    conn.close()
    return render_template("index.html", events=events)


@app.route("/register/<int:event_id>")
def register(event_id):
    """Show the registration form for a specific event."""
    conn = get_db()
    event = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
    conn.close()

    if not event:
        flash("Event not found!", "danger")
        return redirect(url_for("index"))

    return render_template("register.html", event=event)


@app.route("/submit_registration", methods=["POST"])
def submit_registration():
    """Handle the registration form submission."""
    event_id   = request.form["event_id"]
    name       = request.form["name"].strip()
    email      = request.form["email"].strip()
    department = request.form["department"].strip()

    # Basic validation
    if not name or not email or not department:
        flash("All fields are required!", "danger")
        return redirect(url_for("register", event_id=event_id))

    conn = get_db()
    conn.execute(
        "INSERT INTO registrations (event_id, name, email, department) VALUES (?, ?, ?, ?)",
        (event_id, name, email, department)
    )
    conn.commit()
    conn.close()

    flash(f"🎉 Registration successful! Welcome, {name}!", "success")
    return redirect(url_for("index"))


# ─────────────────────────────────────────────
# ADMIN ROUTES
# ─────────────────────────────────────────────

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "admin123"

def admin_required(f):
    """Decorator to protect admin routes."""
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("admin_logged_in"):
            flash("Please login as admin first.", "warning")
            return redirect(url_for("admin_login"))
        return f(*args, **kwargs)
    return decorated


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    """Admin login page."""
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session["admin_logged_in"] = True
            flash("Welcome back, Admin! 👋", "success")
            return redirect(url_for("admin_dashboard"))
        else:
            flash("Invalid credentials. Try again.", "danger")

    return render_template("admin_login.html")


@app.route("/admin/logout")
def admin_logout():
    """Log the admin out."""
    session.pop("admin_logged_in", None)
    flash("Logged out successfully.", "info")
    return redirect(url_for("admin_login"))


@app.route("/admin/dashboard")
@admin_required
def admin_dashboard():
    """Admin dashboard — view all events."""
    conn = get_db()
    events = conn.execute("SELECT * FROM events ORDER BY date ASC").fetchall()

    # Get registration count per event
    counts = {}
    for event in events:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM registrations WHERE event_id = ?",
            (event["id"],)
        ).fetchone()
        counts[event["id"]] = row["cnt"]

    conn.close()
    return render_template("admin_dashboard.html", events=events, counts=counts)


@app.route("/admin/create", methods=["GET", "POST"])
@admin_required
def create_event():
    """Create a new event."""
    if request.method == "POST":
        title       = request.form["title"].strip()
        description = request.form["description"].strip()
        date        = request.form["date"]
        venue       = request.form["venue"].strip()

        if not title or not description or not date or not venue:
            flash("All fields are required!", "danger")
            return redirect(url_for("create_event"))

        conn = get_db()
        conn.execute(
            "INSERT INTO events (title, description, date, venue) VALUES (?, ?, ?, ?)",
            (title, description, date, venue)
        )
        conn.commit()
        conn.close()

        flash(f'Event "{title}" created successfully! ✅', "success")
        return redirect(url_for("admin_dashboard"))

    return render_template("create_event.html")


@app.route("/admin/delete/<int:event_id>")
@admin_required
def delete_event(event_id):
    """Delete an event and its registrations."""
    conn = get_db()
    conn.execute("DELETE FROM registrations WHERE event_id = ?", (event_id,))
    conn.execute("DELETE FROM events WHERE id = ?", (event_id,))
    conn.commit()
    conn.close()

    flash("Event deleted successfully.", "info")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/registrations/<int:event_id>")
@admin_required
def view_registrations(event_id):
    """View all registrations for a specific event."""
    conn = get_db()
    event = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
    registrations = conn.execute(
        "SELECT * FROM registrations WHERE event_id = ?", (event_id,)
    ).fetchall()
    conn.close()

    if not event:
        flash("Event not found!", "danger")
        return redirect(url_for("admin_dashboard"))

    return render_template("view_registrations.html", event=event, registrations=registrations)


# ─────────────────────────────────────────────
# RUN APP
# ─────────────────────────────────────────────

if __name__ == "__main__":
    init_db()          # Create tables on first run
    app.run(debug=True)  # debug=True shows errors in browser
