"""
College Event Management System
--------------------------------
Backend: Flask (Python) + SQLite
"""

from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, send_from_directory
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
from werkzeug.utils import secure_filename
import logging

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "college_event_secret_key")

app.config['MAIL_SERVER']         = os.getenv("MAIL_SERVER", "smtp.gmail.com")
app.config['MAIL_PORT']           = int(os.getenv("MAIL_PORT", 587))
app.config['MAIL_USE_TLS']        = os.getenv("MAIL_USE_TLS", "True") == "True"
app.config['MAIL_USE_SSL']        = os.getenv("MAIL_USE_SSL", "False") == "True"
app.config['MAIL_USERNAME']       = os.getenv("MAIL_USERNAME", "pypr945@gmail.com")
app.config['MAIL_PASSWORD']       = os.getenv("MAIL_PASSWORD", "ztvllyvcvoojlvie").replace(" ", "")
app.config['MAIL_DEFAULT_SENDER'] = app.config['MAIL_USERNAME']
app.config['MAIL_MAX_EMAILS']     = None
app.config['MAIL_ASCII_ATTACHMENTS'] = False

UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'static', 'payment_screenshots')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

mail = Mail(app)

# ─────────────────────────────────────────────
# DATABASE
# ─────────────────────────────────────────────
DB_PATH = os.path.join(os.path.dirname(__file__), "database.db")

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()

    c.execute("""CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL, description TEXT NOT NULL,
        date TEXT NOT NULL, venue TEXT NOT NULL,
        payment_required INTEGER DEFAULT 0,
        upi_id TEXT, amount REAL DEFAULT 0
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS registrations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id INTEGER NOT NULL, name TEXT NOT NULL,
        email TEXT NOT NULL, department TEXT NOT NULL,
        qr_token TEXT UNIQUE, checked_in INTEGER DEFAULT 0,
        payment_status TEXT DEFAULT 'not_required',
        FOREIGN KEY (event_id) REFERENCES events(id)
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        registration_id INTEGER NOT NULL,
        screenshot_filename TEXT,
        uploaded_at TEXT,
        ocr_raw_text TEXT,
        confidence_score REAL DEFAULT 0,
        amount_detected TEXT,
        upi_id_detected TEXT,
        txn_id_detected TEXT,
        success_keyword_found INTEGER DEFAULT 0,
        status TEXT DEFAULT 'pending',
        admin_note TEXT,
        verified_at TEXT,
        FOREIGN KEY (registration_id) REFERENCES registrations(id)
    )""")

    # Schema migrations — safe to run multiple times
    for col, defn in [
        ("payment_required", "INTEGER DEFAULT 0"),
        ("upi_id", "TEXT"),
        ("amount", "REAL DEFAULT 0"),
    ]:
        try:
            c.execute(f"ALTER TABLE events ADD COLUMN {col} {defn}")
        except sqlite3.OperationalError:
            pass

    try:
        c.execute("ALTER TABLE registrations ADD COLUMN payment_status TEXT DEFAULT 'not_required'")
    except sqlite3.OperationalError:
        pass

    conn.commit()
    conn.close()

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# ─────────────────────────────────────────────
# QR / EMAIL HELPERS
# ─────────────────────────────────────────────
def generate_qr_file(token):
    qr_dir = os.path.join(app.root_path, 'static', 'qrcodes')
    os.makedirs(qr_dir, exist_ok=True)
    filepath = os.path.join(qr_dir, f"{token}.png")
    qrcode.make(token).save(filepath)
    return f"{token}.png"

def generate_qr_base64(data):
    qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_L, box_size=8, border=4)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")

def generate_qr_image_bytes(token):
    qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_L, box_size=10, border=4)
    qr.add_data(token)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()

def decode_qr_image(image_stream):
    try:
        file_bytes = np.asarray(bytearray(image_stream.read()), dtype=np.uint8)
        cv_img = cv2.imdecode(file_bytes, cv2.IMREAD_COLOR)
        if cv_img is not None:
            detector = cv2.QRCodeDetector()
            data, _, _ = detector.detectAndDecode(cv_img)
            if data:
                return data
    except Exception as e:
        print(f"QR decode error: {e}")
    return None

def send_registration_email(to_email, name, event_title, qr_token):
    try:
        msg = Message(subject=f"Event Registration: {event_title}", recipients=[to_email])
        msg.html = render_template('email/registration_confirmation.html',
                                   name=name, event_title=event_title,
                                   qr_token=qr_token, current_year=datetime.now().year)
        msg.attach(f"qr_{qr_token}.png", "image/png", generate_qr_image_bytes(qr_token))
        mail.send(msg)
        print(f"DEBUG: Email sent to {to_email}")
        return True
    except Exception as e:
        import traceback
        print(f"ERROR: Email failed: {e}\n{traceback.format_exc()}")
        return False

# ─────────────────────────────────────────────
# PUBLIC ROUTES
# ─────────────────────────────────────────────
@app.route("/")
def index():
    conn = get_db()
    events = conn.execute("SELECT * FROM events ORDER BY date ASC").fetchall()
    conn.close()
    return render_template("index.html", events=events)

@app.route("/register/<int:event_id>")
def register(event_id):
    conn = get_db()
    event = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
    conn.close()
    if not event:
        flash("Event not found!", "danger")
        return redirect(url_for("index"))
    upi_qr_b64 = None
    upi_link = None
    if event["payment_required"] and event["upi_id"]:
        from payment_verifier import generate_upi_link
        upi_link = generate_upi_link(event["upi_id"], event["amount"], event["title"])
        upi_qr_b64 = generate_qr_base64(upi_link)
    return render_template("register.html", event=event, upi_qr_b64=upi_qr_b64, upi_link=upi_link)

@app.route("/submit_registration", methods=["POST"])
def submit_registration():
    event_id   = request.form["event_id"]
    name       = request.form["name"].strip()
    email      = request.form["email"].strip()
    department = request.form["department"].strip()
    qr_token   = str(uuid.uuid4())

    if not name or not email or not department:
        flash("All fields are required!", "danger")
        return redirect(url_for("register", event_id=event_id))

    conn = get_db()
    event = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()

    if not event:
        flash("Event not found!", "danger")
        conn.close()
        return redirect(url_for("index"))

    payment_required = event["payment_required"]
    payment_status = "pending" if payment_required else "not_required"

    # Prevent duplicate registrations by same email for same event
    existing = conn.execute(
        "SELECT id FROM registrations WHERE event_id = ? AND email = ?",
        (event_id, email)
    ).fetchone()
    if existing:
        flash("You have already registered for this event with this email.", "warning")
        conn.close()
        return redirect(url_for("register", event_id=event_id))

    conn.execute(
        "INSERT INTO registrations (event_id, name, email, department, qr_token, checked_in, payment_status) VALUES (?,?,?,?,?,?,?)",
        (event_id, name, email, department, qr_token, 0, payment_status)
    )
    conn.commit()

    reg = conn.execute("SELECT id FROM registrations WHERE qr_token = ?", (qr_token,)).fetchone()
    reg_id = reg["id"]
    conn.close()

    generate_qr_file(qr_token)

    if payment_required:
        return redirect(url_for("upload_payment", reg_id=reg_id))

    event_title = event["title"]
    if send_registration_email(email, name, event_title, qr_token):
        flash(f"🎉 Registration successful! Email sent to {email}.", "success")
    else:
        flash(f"🎉 Registration successful! (Email could not be sent.)", "warning")
    return redirect(url_for("confirmation", qr_token=qr_token))

@app.route("/upload_payment/<int:reg_id>", methods=["GET", "POST"])
def upload_payment(reg_id):
    conn = get_db()
    reg = conn.execute(
        "SELECT r.*, e.title, e.upi_id, e.amount FROM registrations r JOIN events e ON r.event_id = e.id WHERE r.id = ?",
        (reg_id,)
    ).fetchone()

    if not reg:
        conn.close()
        flash("Registration not found.", "danger")
        return redirect(url_for("index"))

    if request.method == "POST":
        if 'screenshot' not in request.files:
            flash("No file selected.", "danger")
            return redirect(request.url)

        file = request.files['screenshot']
        if file.filename == '':
            flash("No file selected.", "danger")
            return redirect(request.url)

        if not allowed_file(file.filename):
            flash("Invalid file type. Please upload PNG, JPG, or JPEG.", "danger")
            return redirect(request.url)

        filename = f"{uuid.uuid4()}_{secure_filename(file.filename)}"
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)

        # Run OCR verification
        from payment_verifier import verify_payment_screenshot
        result = verify_payment_screenshot(
            filepath=filepath,
            expected_amount=reg["amount"],
            expected_upi_id=reg["upi_id"]
        )

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            """INSERT INTO payments (registration_id, screenshot_filename, uploaded_at,
               ocr_raw_text, confidence_score, amount_detected, upi_id_detected,
               txn_id_detected, success_keyword_found, status)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (reg_id, filename, now,
             result["ocr_text"][:2000],
             result["confidence"],
             result["amount_detected"],
             result["upi_id_detected"],
             result["txn_id_detected"],
             1 if result["success_keyword_found"] else 0,
             result["status"])
        )
        conn.execute(
            "UPDATE registrations SET payment_status = ? WHERE id = ?",
            (result["status"], reg_id)
        )
        conn.commit()
        conn.close()

        if result["status"] == "verified":
            send_registration_email(reg["email"], reg["name"], reg["title"], reg["qr_token"])
            flash("✅ Payment verified! Registration confirmed.", "success")
        elif result["status"] == "manual_review":
            flash("⏳ Screenshot received. Our team will verify and confirm shortly.", "info")
        else:
            flash("❌ Screenshot could not be verified. Please upload a clear payment screenshot.", "warning")

        return redirect(url_for("payment_pending", qr_token=reg["qr_token"]))

    conn.close()
    return render_template("upload_payment.html", reg=reg)

@app.route("/payment_pending/<qr_token>")
def payment_pending(qr_token):
    conn = get_db()
    reg = conn.execute(
        "SELECT r.*, e.title, e.date, e.venue, e.amount FROM registrations r JOIN events e ON r.event_id = e.id WHERE r.qr_token = ?",
        (qr_token,)
    ).fetchone()
    if not reg:
        conn.close()
        flash("Registration not found.", "danger")
        return redirect(url_for("index"))

    payment = conn.execute(
        "SELECT * FROM payments WHERE registration_id = ? ORDER BY id DESC LIMIT 1",
        (reg["id"],)
    ).fetchone()
    conn.close()

    qr_filename = f"{qr_token}.png"
    qr_dir = os.path.join(app.root_path, 'static', 'qrcodes')
    if not os.path.exists(os.path.join(qr_dir, qr_filename)):
        generate_qr_file(qr_token)

    return render_template("payment_pending.html", reg=reg, payment=payment, qr_filename=qr_filename)

@app.route("/confirmation/<qr_token>")
def confirmation(qr_token):
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
    qr_dir = os.path.join(app.root_path, 'static', 'qrcodes')
    if not os.path.exists(os.path.join(qr_dir, qr_filename)):
        generate_qr_file(qr_token)
    return render_template("confirmation.html", registration=registration, qr_filename=qr_filename, qr_token=qr_token)

@app.route("/test_mail")
def test_mail():
    recipient = request.args.get("email", app.config['MAIL_USERNAME'])
    msg = Message(subject="SMTP Test", recipients=[recipient], body="SMTP is working!")
    try:
        mail.send(msg)
        return {"status": "success", "message": f"Test email sent to {recipient}"}
    except Exception as e:
        return {"status": "error", "message": str(e)}, 500

# ─────────────────────────────────────────────
# ADMIN
# ─────────────────────────────────────────────
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "admin123"

def admin_required(f):
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
    if request.method == "POST":
        if request.form["username"] == ADMIN_USERNAME and request.form["password"] == ADMIN_PASSWORD:
            session["admin_logged_in"] = True
            flash("Welcome back, Admin! 👋", "success")
            return redirect(url_for("admin_dashboard"))
        flash("Invalid credentials.", "danger")
    return render_template("admin_login.html")

@app.route("/admin/logout")
def admin_logout():
    session.pop("admin_logged_in", None)
    flash("Logged out.", "info")
    return redirect(url_for("admin_login"))

@app.route("/admin/dashboard")
@admin_required
def admin_dashboard():
    conn = get_db()
    events = conn.execute("SELECT * FROM events ORDER BY date ASC").fetchall()
    counts = {}
    pending_payments = 0
    for event in events:
        row = conn.execute("SELECT COUNT(*) as cnt FROM registrations WHERE event_id = ?", (event["id"],)).fetchone()
        counts[event["id"]] = row["cnt"]
    pending_payments = conn.execute("SELECT COUNT(*) as cnt FROM payments WHERE status IN ('pending','manual_review')").fetchone()["cnt"]
    conn.close()
    return render_template("admin_dashboard.html", events=events, counts=counts, pending_payments=pending_payments)

@app.route("/admin/create", methods=["GET", "POST"])
@admin_required
def create_event():
    if request.method == "POST":
        title       = request.form["title"].strip()
        description = request.form["description"].strip()
        date        = request.form["date"]
        venue       = request.form["venue"].strip()
        pay_req     = 1 if request.form.get("payment_required") == "on" else 0
        upi_id      = request.form.get("upi_id", "").strip()
        amount_str  = request.form.get("amount", "0").strip()
        try:
            amount = float(amount_str) if amount_str else 0.0
        except ValueError:
            amount = 0.0

        if not title or not description or not date or not venue:
            flash("All fields are required!", "danger")
            return redirect(url_for("create_event"))
        if pay_req and not upi_id:
            flash("UPI ID is required when payment is enabled.", "danger")
            return redirect(url_for("create_event"))

        conn = get_db()
        conn.execute(
            "INSERT INTO events (title, description, date, venue, payment_required, upi_id, amount) VALUES (?,?,?,?,?,?,?)",
            (title, description, date, venue, pay_req, upi_id if pay_req else None, amount if pay_req else 0)
        )
        conn.commit()
        conn.close()
        flash(f'Event "{title}" created! ✅', "success")
        return redirect(url_for("admin_dashboard"))
    return render_template("create_event.html")

@app.route("/admin/delete/<int:event_id>")
@admin_required
def delete_event(event_id):
    conn = get_db()
    conn.execute("DELETE FROM registrations WHERE event_id = ?", (event_id,))
    conn.execute("DELETE FROM events WHERE id = ?", (event_id,))
    conn.commit()
    conn.close()
    flash("Event deleted.", "info")
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/registrations/<int:event_id>")
@admin_required
def view_registrations(event_id):
    conn = get_db()
    event = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
    registrations = conn.execute(
        "SELECT r.*, p.status as pay_status, p.confidence_score FROM registrations r LEFT JOIN payments p ON p.registration_id = r.id AND p.id = (SELECT MAX(id) FROM payments WHERE registration_id = r.id) WHERE r.event_id = ? ORDER BY r.name ASC",
        (event_id,)
    ).fetchall()
    conn.close()
    if not event:
        flash("Event not found!", "danger")
        return redirect(url_for("admin_dashboard"))
    return render_template("view_registrations.html", event=event, registrations=registrations)

@app.route("/admin/payments")
@admin_required
def admin_payments():
    conn = get_db()
    filter_status = request.args.get("status", "all")
    if filter_status == "all":
        payments = conn.execute(
            """SELECT p.*, r.name, r.email, r.department, r.qr_token, e.title as event_title, e.amount as expected_amount
               FROM payments p JOIN registrations r ON p.registration_id = r.id
               JOIN events e ON r.event_id = e.id ORDER BY p.uploaded_at DESC"""
        ).fetchall()
    else:
        payments = conn.execute(
            """SELECT p.*, r.name, r.email, r.department, r.qr_token, e.title as event_title, e.amount as expected_amount
               FROM payments p JOIN registrations r ON p.registration_id = r.id
               JOIN events e ON r.event_id = e.id WHERE p.status = ? ORDER BY p.uploaded_at DESC""",
            (filter_status,)
        ).fetchall()
    stats = conn.execute(
        "SELECT status, COUNT(*) as cnt FROM payments GROUP BY status"
    ).fetchall()
    conn.close()
    stats_dict = {row["status"]: row["cnt"] for row in stats}
    return render_template("admin_payments.html", payments=payments, stats=stats_dict, filter_status=filter_status)

@app.route("/admin/payment/approve/<int:payment_id>", methods=["POST"])
@admin_required
def approve_payment(payment_id):
    note = request.form.get("note", "").strip()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db()
    payment = conn.execute("SELECT * FROM payments WHERE id = ?", (payment_id,)).fetchone()
    if payment:
        conn.execute(
            "UPDATE payments SET status = 'verified', admin_note = ?, verified_at = ? WHERE id = ?",
            (note, now, payment_id)
        )
        conn.execute(
            "UPDATE registrations SET payment_status = 'verified' WHERE id = ?",
            (payment["registration_id"],)
        )
        reg = conn.execute(
            "SELECT r.*, e.title FROM registrations r JOIN events e ON r.event_id = e.id WHERE r.id = ?",
            (payment["registration_id"],)
        ).fetchone()
        conn.commit()
        conn.close()
        if reg:
            send_registration_email(reg["email"], reg["name"], reg["title"], reg["qr_token"])
        flash("✅ Payment approved and confirmation email sent.", "success")
    else:
        conn.close()
        flash("Payment record not found.", "danger")
    return redirect(url_for("admin_payments"))

@app.route("/admin/payment/reject/<int:payment_id>", methods=["POST"])
@admin_required
def reject_payment(payment_id):
    note = request.form.get("note", "").strip()
    conn = get_db()
    payment = conn.execute("SELECT * FROM payments WHERE id = ?", (payment_id,)).fetchone()
    if payment:
        conn.execute(
            "UPDATE payments SET status = 'failed', admin_note = ? WHERE id = ?",
            (note, payment_id)
        )
        conn.execute(
            "UPDATE registrations SET payment_status = 'failed' WHERE id = ?",
            (payment["registration_id"],)
        )
        conn.commit()
    conn.close()
    flash("Payment rejected.", "warning")
    return redirect(url_for("admin_payments"))

@app.route("/admin/payment/screenshot/<filename>")
@admin_required
def view_screenshot(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route("/admin/checkin/<int:event_id>")
@admin_required
def checkin_scanner(event_id):
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
    event_id = request.form.get("event_id")
    qr_token = request.form.get("qr_token", "").strip()
    if 'qr_image' in request.files and request.files['qr_image'].filename != '':
        decoded = decode_qr_image(request.files['qr_image'].stream)
        if decoded:
            qr_token = decoded
        else:
            return "Invalid QR code image."
    if not qr_token:
        return "Please provide a QR token."
    conn = get_db()
    reg = conn.execute(
        "SELECT * FROM registrations WHERE qr_token = ? AND event_id = ?",
        (qr_token, event_id)
    ).fetchone()
    if not reg:
        conn.close()
        return "Invalid QR code"
    if reg["payment_status"] == "pending" or reg["payment_status"] == "manual_review":
        conn.close()
        return "⚠️ Payment not yet verified for this registration."
    if reg["checked_in"] == 1:
        conn.close()
        return "Already checked in"
    conn.execute("UPDATE registrations SET checked_in = 1 WHERE id = ?", (reg["id"],))
    conn.commit()
    conn.close()
    return f"Check-in successful ✅ for {reg['name']}"

# ─────────────────────────────────────────────
# STARTUP
# ─────────────────────────────────────────────
if __name__ == "__main__":
    print(f"DEBUG: DB path → {DB_PATH}")
    init_db()
    print("DEBUG: Database initialized.")
    app.run(debug=True, port=5002)
