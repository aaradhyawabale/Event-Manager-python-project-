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
import uuid
import qrcode, io, base64
from PIL import Image
import cv2
import numpy as np
from datetime import datetime
from flask_mail import Mail, Message
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "college_event_secret_key")

# ─────────────────────────────────────────────
# FLASK-MAIL CONFIGURATION
# ─────────────────────────────────────────────
app.config['MAIL_SERVER'] = os.getenv("MAIL_SERVER", "smtp.gmail.com")
app.config['MAIL_PORT'] = int(os.getenv("MAIL_PORT", 587))
app.config['MAIL_USE_TLS'] = os.getenv("MAIL_USE_TLS", "True") == "True"
app.config['MAIL_USE_SSL'] = os.getenv("MAIL_USE_SSL", "False") == "True"
app.config['MAIL_USERNAME'] = os.getenv("MAIL_USERNAME", "pypr945@gmail.com")
# Gmail App Passwords usually work better without spaces
app.config['MAIL_PASSWORD'] = os.getenv("MAIL_PASSWORD", "ztvllyvcvoojlvie").replace(" ", "")
app.config['MAIL_DEFAULT_SENDER'] = app.config['MAIL_USERNAME']
app.config['MAIL_MAX_EMAILS'] = None
app.config['MAIL_ASCII_ATTACHMENTS'] = False

mail = Mail(app)

# ─────────────────────────────────────────────
# DATABASE SETUP
# ─────────────────────────────────────────────

def get_db():
    """Connect to the SQLite database."""
    conn = sqlite3.connect(os.path.join(os.path.dirname(__file__), "database.db"))
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
            qr_token    TEXT UNIQUE,
            checked_in  INTEGER DEFAULT 0,
            FOREIGN KEY (event_id) REFERENCES events(id)
        )
    """)

    conn.commit()
    conn.close()

def generate_qr_base64(token):
    """Generates a QR code as a base64 string from a given token."""
    qr = qrcode.make(token)
    buffer = io.BytesIO()
    qr.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("utf-8")

def generate_qr_file(token):
    """Generates a QR code and saves it to static/qrcodes/."""
    qr_dir = os.path.join(app.root_path, 'static', 'qrcodes')
    if not os.path.exists(qr_dir):
        os.makedirs(qr_dir)
    
    filename = f"{token}.png"
    filepath = os.path.join(qr_dir, filename)
    
    qr = qrcode.make(token)
    qr.save(filepath)
    
    return filename

def generate_qr_image_bytes(token):
    """Generates a QR code image as bytes."""
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(token)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()

def decode_qr_image(image_stream):
    """Decodes a QR code from an uploaded image file using OpenCV."""
    try:
        # Read the image stream into a format suitable for OpenCV
        file_bytes = np.asarray(bytearray(image_stream.read()), dtype=np.uint8)
        cv_img = cv2.imdecode(file_bytes, cv2.IMREAD_COLOR)
        
        if cv_img is not None:
            # OpenCV QR Code Detector
            detector = cv2.QRCodeDetector()
            data, bbox, straight_qrcode = detector.detectAndDecode(cv_img)
            
            if data:
                return data
                
        return None
    except Exception as e:
        print(f"Error decoding image: {e}")
        return None

def send_registration_email(to_email, name, event_title, qr_token):
    """Sends a registration confirmation email with QR token."""
    try:
        print(f"DEBUG: Preparing to send email to {to_email} for event '{event_title}'")
        
        msg = Message(
            subject=f"Event Registration Confirmation: {event_title}",
            recipients=[to_email]
        )
        
        # Render the HTML template
        msg.html = render_template(
            'email/registration_confirmation.html',
            name=name,
            event_title=event_title,
            qr_token=qr_token,
            current_year=datetime.now().year
        )
        
        # Generate QR code image bytes
        qr_image_bytes = generate_qr_image_bytes(qr_token)
        
        # Attach QR code image
        msg.attach(
            f"qr_code_{qr_token}.png",
            "image/png",
            qr_image_bytes
        )
        
        # Send the email
        mail.send(msg)
        print(f"DEBUG: Confirmation email successfully sent to {to_email}")
        return True
        
    except Exception as e:
        import traceback
        print(f"CRITICAL ERROR: Failed to send email to {to_email}")
        print(f"Error Type: {type(e).__name__}")
        print(f"Error Message: {str(e)}")
        print("Full Traceback:")
        print(traceback.format_exc())
        return False

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
    qr_token = str(uuid.uuid4())

    # Basic validation
    if not name or not email or not department:
        flash("All fields are required!", "danger")
        return redirect(url_for("register", event_id=event_id))

    conn = get_db()
    conn.execute(
        "INSERT INTO registrations (event_id, name, email, department, qr_token, checked_in) VALUES (?, ?, ?, ?, ?, ?)",
        (event_id, name, email, department, qr_token, 0)
    )
    conn.commit()
    conn.close()
    
    # Generate and save the QR file
    generate_qr_file(qr_token)

    # Fetch event details for email
    conn = get_db()
    event = conn.execute("SELECT title FROM events WHERE id = ?", (event_id,)).fetchone()
    event_title = event['title'] if event else "Unknown Event"
    conn.close()

    # Send confirmation email (non-blocking, with error handling)
    if send_registration_email(email, name, event_title, qr_token):
        flash(f"🎉 Registration successful! Welcome, {name}! A confirmation email has been sent to {email}.", "success")
    else:
        flash(f"🎉 Registration successful! Welcome, {name}! However, we could not send a confirmation email.", "warning")
        
    return redirect(url_for("confirmation", qr_token=qr_token))


@app.route("/confirmation/<qr_token>")
def confirmation(qr_token):
    """Show registration confirmation with QR code."""
    conn = get_db()
    registration = conn.execute(
        "SELECT r.*, e.title, e.date, e.venue FROM registrations r JOIN events e ON r.event_id = e.id WHERE r.qr_token = ?",
        (qr_token,)
    ).fetchone()
    conn.close()

    if not registration:
        flash("Registration not found!", "danger")
        return redirect(url_for("index"))

    qr_filename = f"{qr_token}.png"
    # Ensure the file exists (for old registrations)
    qr_dir = os.path.join(app.root_path, 'static', 'qrcodes')
    if not os.path.exists(os.path.join(qr_dir, qr_filename)):
        generate_qr_file(qr_token)

    return render_template("confirmation.html", registration=registration, qr_filename=qr_filename, qr_token=qr_token)


@app.route("/test_mail")
def test_mail():
    """Diagnostic route to test email configuration independently."""
    recipient = request.args.get("email", app.config['MAIL_USERNAME'])
    print(f"DEBUG: Running SMTP Diagnostic Test for {recipient}")
    
    msg = Message(
        subject="Event System - SMTP Diagnostic Test",
        recipients=[recipient],
        body=f"If you are reading this, your SMTP configuration is working correctly!\n\nDetails:\nServer: {app.config['MAIL_SERVER']}\nPort: {app.config['MAIL_PORT']}\nUser: {app.config['MAIL_USERNAME']}"
    )
    
    try:
        with app.app_context():
            mail.send(msg)
        print("DEBUG: SMTP Diagnostic Test successful")
        return {
            "status": "success",
            "message": f"Test email sent successfully to {recipient}",
            "config_used": {
                "server": app.config['MAIL_SERVER'],
                "port": app.config['MAIL_PORT'],
                "user": app.config['MAIL_USERNAME'],
                "tls": app.config['MAIL_USE_TLS'],
                "ssl": app.config['MAIL_USE_SSL']
            }
        }
    except Exception as e:
        import traceback
        error_info = traceback.format_exc()
        print(f"DEBUG: SMTP Diagnostic Test failed\n{error_info}")
        return {
            "status": "error",
            "error_type": type(e).__name__,
            "message": str(e),
            "traceback": error_info
        }, 500


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
        "SELECT * FROM registrations WHERE event_id = ? ORDER BY name ASC",
        (event_id,)
    ).fetchall()
    conn.close()

    if not event:
        flash("Event not found!", "danger")
        return redirect(url_for("admin_dashboard"))

    return render_template("view_registrations.html", event=event, registrations=registrations)


@app.route("/admin/checkin/<int:event_id>")
@admin_required
def checkin_scanner(event_id):
    """Render the QR scanner page for a specific event."""
    conn = get_db()
    event = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
    conn.close()

    if not event:
        flash("Event not found!", "danger")
        return redirect(url_for("admin_dashboard"))

    return render_template("checkin.html", event=event)


@app.route("/admin/verify", methods=["POST"])
@admin_required
def verify_qr():
    """Verify QR token and update check-in status."""
    event_id = request.form.get("event_id")
    qr_token = request.form.get("qr_token", "").strip()

    # Check if an image was uploaded
    if 'qr_image' in request.files and request.files['qr_image'].filename != '':
        qr_image = request.files['qr_image']
        decoded_token = decode_qr_image(qr_image.stream)
        if decoded_token:
            qr_token = decoded_token
        else:
            return "Invalid QR code image or no QR code detected. Please ensure the QR code is clear."

    if not qr_token:
        return "Please provide a QR token or upload a valid QR image."

    conn = get_db()
    registration = conn.execute(
        "SELECT * FROM registrations WHERE qr_token = ? AND event_id = ?",
        (qr_token, event_id)
    ).fetchone()

    if not registration:
        conn.close()
        return "Invalid QR code"

    if registration["checked_in"] == 1:
        conn.close()
        return "Already checked in"

    conn.execute(
        "UPDATE registrations SET checked_in = 1 WHERE id = ?",
        (registration["id"],)
    )
    conn.commit()
    conn.close()

    return f"Check-in successful ✅ for {registration['name']}"


# ─────────────────────────────────────────────
# APP INITIALIZATION
# ─────────────────────────────────────────────

if __name__ == "__main__":
    init_db()          # Create tables on first run
    app.run(debug=True, port=5002)  # debug=True shows errors in browser
