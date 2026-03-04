from flask import Flask, render_template, request, redirect, url_for, flash, session
from functools import wraps
import mysql.connector
import os
import threading
from werkzeug.utils import secure_filename
from flask_mail import Mail, Message
import cloudinary
import cloudinary.uploader

# Cloudinary config
cloudinary.config(
    cloud_name = os.environ.get("CLOUDINARY_CLOUD_NAME"),
    api_key = os.environ.get("CLOUDINARY_API_KEY"),
    api_secret = os.environ.get("CLOUDINARY_API_SECRET")
)

def upload_to_cloudinary(file, folder):
    try:
        result = cloudinary.uploader.upload(file, folder=folder)
        return result["secure_url"]
    except Exception as e:
        print(f"Cloudinary upload error: {e}", flush=True)
        return None

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "campus_secret_key_2024")

# ── Global Notification Toggle (Admin controlled) ──
NOTIFICATIONS_ENABLED = True

# ── Email Config ─────────────────────────────────────────────
# ⚠️  CHANGE THESE 2 LINES with your Gmail and App Password
app.config['MAIL_SERVER']         = os.environ.get('MAIL_SERVER', 'smtp-relay.brevo.com')
app.config['MAIL_PORT']           = int(os.environ.get('MAIL_PORT', 465))
app.config['MAIL_USE_TLS']        = False
app.config['MAIL_USE_SSL']         = True
app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD')  # ← PUT YOUR APP PASSWORD HERE
app.config['MAIL_DEFAULT_SENDER'] = os.environ.get('MAIL_USERNAME')

mail = Mail(app)

from flask import g

@app.before_request
def load_current_user():
    g.current_user = None
    if "user_id" in session:
        g.current_user = get_user(session["user_id"])


# ── Upload folders ──────────────────────────────────────────
UPLOAD_FOLDER            = "static/uploads"
LOST_ITEM_UPLOAD_FOLDER  = "static/uploads/lost_items"
FOUND_ITEM_UPLOAD_FOLDER = "static/uploads/found_items"

for _folder in [UPLOAD_FOLDER, LOST_ITEM_UPLOAD_FOLDER, FOUND_ITEM_UPLOAD_FOLDER]:
    os.makedirs(_folder, exist_ok=True)

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

CATEGORIES = [
    'Electronics', 'Books & Stationery', 'Clothing & Accessories',
    'ID & Documents', 'Keys', 'Wallet & Money', 'Jewellery',
    'Bag & Luggage', 'Sports Equipment', 'Other'
]

# ── DB ──────────────────────────────────────────────────────
def get_db_connection():
    return mysql.connector.connect(
        host=os.environ.get("MYSQL_HOST"),
        user=os.environ.get("MYSQL_USER"),
        password=os.environ.get("MYSQL_PASSWORD"),
        database=os.environ.get("MYSQL_DB"),
        autocommit=True
    )

def get_user(user_id):
    """Fetch a single user by id. Returns dict or None."""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM users WHERE id = %s", (user_id,))
    user = cursor.fetchone()
    cursor.close(); conn.close()
    return user

def get_all_user_emails():
    """Get all registered user emails (excluding Admin)."""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT email FROM users WHERE role != 'Admin'")
    emails = [row['email'] for row in cursor.fetchall()]
    cursor.close(); conn.close()
    return emails

def send_notification_email(subject, body, recipient_list):
    """Send email to all users via Brevo HTTP API in a background thread."""
    def send():
        try:
            import requests
            api_key = os.environ.get('BREVO_API_KEY')
            to = [{"email": email} for email in recipient_list]
            response = requests.post(
                "https://api.brevo.com/v3/smtp/email",
                headers={"api-key": api_key, "Content-Type": "application/json"},
                json={
                    "sender": {"email": "jamsheerkhan118@gmail.com", "name": "Campus Lost & Found"},
                    "to": to,
                    "subject": subject,
                    "textContent": body
                }
            )
            if response.status_code == 201:
                print(f"✅ Notification sent to {len(recipient_list)} users.", flush=True)
            else:
                print(f"❌ Email error: {response.text}", flush=True)
        except Exception as e:
            print(f"❌ Email error: {e}", flush=True)
    threading.Thread(target=send).start()

# ── Admin guard ─────────────────────────────────────────────
def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        admin_id = kwargs.get('admin_id')
        if not admin_id:
            return redirect(url_for('home'))
        u = get_user(admin_id)
        if not u or u['role'] != 'Admin':
            return "403 — Admins only", 403
        return f(*args, **kwargs)
    return decorated

# ════════════════════════════════════════════════════════════
# AUTH
# ════════════════════════════════════════════════════════════
@app.route("/")
def home():
    return render_template("login.html")

@app.route("/signup")
def signup():
    return render_template("signup.html")

@app.route("/register", methods=["POST"])
def register():
    name       = request.form["name"]
    department = request.form["department"]
    year       = request.form["year"]
    section    = request.form["section"]
    email      = request.form["email"]
    mobile     = request.form["mobile"]
    password   = request.form["password"]

    file = request.files.get("profile_pic")
    filename = "default.jpeg"
    if file and file.filename:
        filename = upload_to_cloudinary(file, "profiles") or "default.jpeg"

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT id FROM users WHERE email = %s", (email,))
    if cursor.fetchone():
        cursor.close(); conn.close()
        return render_template("signup.html",
            error="⚠ This email is already registered. Try logging in instead.")

    cursor.execute("""
        INSERT INTO users (role, name, department, year, section, email, mobile, password, profile_pic)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """, ("Student", name, department, year, section, email, mobile, password, filename))
    cursor.close(); conn.close()
    flash("Account created successfully! Please login.", "success")
    return redirect(url_for('home'))


@app.route("/login", methods=["POST"])
def login():
    email    = request.form["email"]
    password = request.form["password"]

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM users WHERE email = %s", (email,))
    user = cursor.fetchone()
    cursor.close(); conn.close()

    if not user:
        return render_template("login.html", error="not_found", submitted_email=email)
    if user["password"] != password:
        return render_template("login.html", error="wrong_password", submitted_email=email)

    # ✅ FIX: store session so refresh/back-button works
    session["user_id"]   = user["id"]
    session["user_role"] = user["role"]

    # Admin → admin panel, everyone else → dashboard
    if user["role"] == "Admin":
        return redirect(url_for("admin_dashboard", admin_id=user["id"]))
    return redirect(url_for("dashboard_user", user_id=user["id"]))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))

# ════════════════════════════════════════════════════════════
# DASHBOARD
# ════════════════════════════════════════════════════════════
@app.route("/dashboard")
def dashboard():
    if "user_id" not in session:
        return redirect(url_for("home"))
    return redirect(url_for("dashboard_user", user_id=session["user_id"]))

@app.route("/dashboard/<int:user_id>")
def dashboard_user(user_id):
    user = get_user(user_id)
    if not user:
        return redirect(url_for("home"))
    if user["role"] == "Admin":
        return redirect(url_for("admin_dashboard", admin_id=user_id))
    return render_template("dashboard.html", user=user, current_user=g.current_user)

# ════════════════════════════════════════════════════════════
# PROFILE
# ════════════════════════════════════════════════════════════
@app.route("/profile/<int:user_id>")
def profile(user_id):
    user = get_user(user_id)
    if not user:
        return "User not found", 404
    # viewer = logged-in user (for back button going to correct dashboard)
    viewer_id = session.get("user_id", user_id)
    viewer = get_user(viewer_id) or user
    return render_template("profile.html", user=user, current_user=viewer)

@app.route("/edit_profile/<int:user_id>")
def edit_profile(user_id):
    user = get_user(user_id)
    if not user:
        return "User not found", 404
    return render_template("edit_profile.html", user=user, current_user=g.current_user)

@app.route("/update_profile/<int:user_id>", methods=["POST"])
def update_profile(user_id):
    user = get_user(user_id)
    if not user:
        return "User not found", 404

    name        = request.form["name"]
    department  = request.form["department"]
    year        = request.form["year"]
    section     = request.form["section"]
    email       = request.form["email"]
    mobile      = request.form["mobile"]
    password    = request.form.get("password", "").strip()
    pic_changed = request.form.get("_pic_changed", "0")

    profile_pic = user["profile_pic"]
    if pic_changed == "1":
        file = request.files.get("profile_pic")
        if file and file.filename:
            profile_pic = upload_to_cloudinary(file, "profiles") or user["profile_pic"]

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("SELECT id FROM users WHERE email = %s AND id != %s", (email, user_id))
    if cursor.fetchone():
        cursor.close(); conn.close()
        flash("That email is already used by another account.", "error")
        return redirect(url_for("edit_profile", user_id=user_id))

    if not mobile.isdigit() or len(mobile) != 10:
        cursor.close(); conn.close()
        flash("Mobile number must be exactly 10 digits.", "error")
        return redirect(url_for("edit_profile", user_id=user_id))

    if password:
        cursor.execute("""
            UPDATE users SET name=%s, department=%s, year=%s, section=%s,
            email=%s, mobile=%s, password=%s, profile_pic=%s WHERE id=%s
        """, (name, department, year, section, email, mobile, password, profile_pic, user_id))
    else:
        cursor.execute("""
            UPDATE users SET name=%s, department=%s, year=%s, section=%s,
            email=%s, mobile=%s, profile_pic=%s WHERE id=%s
        """, (name, department, year, section, email, mobile, profile_pic, user_id))

    cursor.close(); conn.close()
    flash("Profile updated successfully! ✓", "success")
    return redirect(url_for("profile", user_id=user_id))

# ════════════════════════════════════════════════════════════
# LOST ITEMS
# ════════════════════════════════════════════════════════════
@app.route("/report_lost/<int:user_id>")
def report_lost(user_id):
    user = get_user(user_id)
    if not user:
        return redirect(url_for("home"))
    return render_template("report_lost.html", user=user, current_user=g.current_user)

@app.route("/report_lost", methods=["POST"])
def submit_report_lost():
    user_id     = request.form["user_id"]
    item_name   = request.form["item_name"].strip()
    description = request.form["description"].strip()
    category    = request.form["category"]
    date_lost   = request.form["date_lost"]
    status      = request.form.get("status", "Searching")

    if not all([item_name, description, category, date_lost]):
        flash("Please fill in all required fields.", "error")
        return redirect(url_for("report_lost", user_id=user_id))

    image_filename = None
    file = request.files.get("image")
    if file and file.filename:
        image_filename = upload_to_cloudinary(file, "lost_items")

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO lost_items (user_id, item_name, description, category, date_lost, image, status)
        VALUES (%s,%s,%s,%s,%s,%s,%s)
    """, (user_id, item_name, description, category, date_lost, image_filename, status))
    cursor.close(); conn.close()

    # 🔔 Send email notification to ALL users
    all_emails = get_all_user_emails()
    if all_emails and NOTIFICATIONS_ENABLED:
        send_notification_email(
            subject=f"🔍 Lost Item Alert: {item_name}",
            body=(
                f"Hi! Someone on Campus Lost & Found is looking for their lost item.\n\n"
                f"📦 Item     : {item_name}\n"
                f"📁 Category : {category}\n"
                f"📝 Details  : {description}\n"
                f"📅 Lost On  : {date_lost}\n\n"
                f"Have you seen it? Log in and help them out!\n"
                f"👉 https://campus-lost-found-app.onrender.com\n\n"
                f"— Campus Lost & Found Team"
            ),
            recipient_list=all_emails
        )

    flash(f"'{item_name}' reported successfully! We'll notify you if someone finds it.", "success")
    return redirect(url_for("report_lost", user_id=user_id))

@app.route("/lost_items/<int:user_id>")
def lost_items_list(user_id):
    user = get_user(user_id)
    if not user:
        return redirect(url_for("home"))

    search   = request.args.get("search", "").strip()
    category = request.args.get("category", "")
    status   = request.args.get("status", "")

    query  = """
        SELECT li.*, u.name AS reporter_name, u.email AS reporter_email, u.profile_pic
        FROM lost_items li JOIN users u ON li.user_id = u.id WHERE 1=1
    """
    params = []
    if search:   query += " AND li.item_name LIKE %s"; params.append(f"%{search}%")
    if category: query += " AND li.category = %s";     params.append(category)
    if status:   query += " AND li.status = %s";       params.append(status)
    query += " ORDER BY li.created_at DESC"

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(query, params)
    items = cursor.fetchall()

    cursor.execute("SELECT status, COUNT(*) as cnt FROM lost_items GROUP BY status")
    counts = {"searching": 0, "found": 0, "closed": 0}
    for r in cursor.fetchall():
        counts[r["status"].lower()] = r["cnt"]
    cursor.close(); conn.close()

    return render_template("lost_items_list.html",
        user=user, items=items, total=len(items), current_user=g.current_user,
        counts=counts, categories=CATEGORIES,
        filters={"search": search, "category": category, "status": status}
    )

@app.route("/lost_item/<int:item_id>/<int:viewer_id>")
def lost_item_detail(item_id, viewer_id):
    viewer = get_user(viewer_id)

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM lost_items WHERE id = %s", (item_id,))
    item = cursor.fetchone()

    if not item or not viewer:
        cursor.close(); conn.close()
        return redirect(url_for("home"))

    cursor.execute("SELECT * FROM users WHERE id = %s", (item["user_id"],))
    reporter = cursor.fetchone()
    cursor.close(); conn.close()

    return render_template("lost_item_detail.html",
        viewer=viewer, user=viewer, item=item, reporter=reporter, current_user=g.current_user)

# ════════════════════════════════════════════════════════════
# FOUND ITEMS
# ════════════════════════════════════════════════════════════
@app.route("/report_found/<int:user_id>")
def report_found(user_id):
    user = get_user(user_id)
    if not user:
        return redirect(url_for("home"))
    return render_template("report_found.html", user=user, current_user=g.current_user)

@app.route("/report_found", methods=["POST"])
def submit_report_found():
    user_id        = request.form["user_id"]
    item_name      = request.form["item_name"].strip()
    description    = request.form["description"].strip()
    category       = request.form["category"]
    location_found = request.form["location_found"].strip()
    date_found     = request.form["date_found"]

    if not all([item_name, description, category, location_found, date_found]):
        flash("Please fill in all required fields.", "error")
        return redirect(url_for("report_found", user_id=user_id))

    image_filename = None
    file = request.files.get("image")
    if file and file.filename:
        image_filename = upload_to_cloudinary(file, "found_items")

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO found_items (user_id, item_name, description, category, location_found, date_found, image)
        VALUES (%s,%s,%s,%s,%s,%s,%s)
    """, (user_id, item_name, description, category, location_found, date_found, image_filename))
    cursor.close(); conn.close()

    # 🔔 Send email notification to ALL users
    all_emails = get_all_user_emails()
    if all_emails and NOTIFICATIONS_ENABLED:
        send_notification_email(
            subject=f"✅ Found Item Alert: {item_name}",
            body=(
                f"Hi! Someone found an item on campus. Is it yours?\n\n"
                f"📦 Item          : {item_name}\n"
                f"📁 Category      : {category}\n"
                f"📝 Description   : {description}\n"
                f"📍 Found At      : {location_found}\n"
                f"📅 Date Found    : {date_found}\n\n"
                f"Think it's yours? Log in and submit a claim!\n"
                f"👉 https://campus-lost-found-app.onrender.com\n\n"
                f"— Campus Lost & Found Team"
            ),
            recipient_list=all_emails
        )

    flash(f"'{item_name}' reported as found successfully!", "success")
    return redirect(url_for("report_found", user_id=user_id))

@app.route("/found_items/<int:user_id>")
def found_items_list(user_id):
    user = get_user(user_id)
    if not user:
        return redirect(url_for("home"))

    search   = request.args.get("search", "").strip()
    category = request.args.get("category", "")
    status   = request.args.get("status", "")

    query = """
        SELECT fi.*, u.name AS reporter_name, u.email AS reporter_email, u.profile_pic
        FROM found_items fi JOIN users u ON fi.user_id = u.id WHERE 1=1
    """
    params = []
    if search:   query += " AND fi.item_name LIKE %s"; params.append(f"%{search}%")
    if category: query += " AND fi.category = %s";     params.append(category)
    if status:   query += " AND fi.status = %s";       params.append(status)
    query += " ORDER BY fi.created_at DESC"

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(query, params)
    items = cursor.fetchall()

    cursor.execute("SELECT COUNT(*) AS n FROM found_items WHERE status='Available'")
    available = cursor.fetchone()['n']
    cursor.execute("SELECT COUNT(*) AS n FROM found_items WHERE status='Claimed'")
    claimed = cursor.fetchone()['n']
    cursor.execute("SELECT COUNT(*) AS n FROM found_items WHERE status='Closed'")
    closed = cursor.fetchone()['n']
    cursor.close(); conn.close()

    return render_template("found_items_list.html",
        user=user, items=items, total=len(items), current_user=g.current_user,
        counts={"available": available, "claimed": claimed, "closed": closed},
        categories=CATEGORIES,
        filters={"search": search, "category": category, "status": status}
    )

@app.route("/found_item/<int:item_id>/<int:user_id>")
def found_item_detail(item_id, user_id):
    user = get_user(user_id)

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT fi.*, u.name AS reporter_name, u.email AS reporter_email,
               u.mobile AS reporter_mobile, u.profile_pic,
               u.department, u.year, u.section
        FROM found_items fi JOIN users u ON fi.user_id = u.id
        WHERE fi.id = %s
    """, (item_id,))
    item = cursor.fetchone()

    # ✅ FIX: check if this user already has a pending/approved claim
    already_claimed = False
    if user and item:
        cursor.execute("""
            SELECT id FROM claim_requests WHERE found_item_id = %s AND claimant_id = %s
        """, (item_id, user_id))
        already_claimed = cursor.fetchone() is not None

    cursor.close(); conn.close()

    if not item:
        return "Item not found", 404

    return render_template("found_item_detail.html",
        user=user, item=item, already_claimed=already_claimed, current_user=g.current_user)

# ════════════════════════════════════════════════════════════
# CLAIMS
# ════════════════════════════════════════════════════════════
@app.route("/claim_request/<int:item_id>/<int:user_id>", methods=["POST"])
def submit_claim(item_id, user_id):
    message = request.form.get("message", "").strip()

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT id FROM claim_requests WHERE found_item_id = %s AND claimant_id = %s
    """, (item_id, user_id))

    if cursor.fetchone():
        flash("You have already submitted a claim for this item.", "error")
    else:
        cursor.execute("""
            INSERT INTO claim_requests (found_item_id, claimant_id, message)
            VALUES (%s,%s,%s)
        """, (item_id, user_id, message))
        flash("✅ Claim request submitted! The finder will review it.", "success")

    cursor.close(); conn.close()
    return redirect(url_for("found_item_detail", item_id=item_id, user_id=user_id))

@app.route("/my_claims/<int:user_id>")
def my_claims(user_id):
    user = get_user(user_id)
    if not user:
        return redirect(url_for("home"))

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT cr.*, fi.item_name, fi.category, fi.image, fi.location_found,
               u.name AS finder_name, u.email AS finder_email
        FROM claim_requests cr
        JOIN found_items fi ON cr.found_item_id = fi.id
        JOIN users u ON fi.user_id = u.id
        WHERE cr.claimant_id = %s
        ORDER BY cr.created_at DESC
    """, (user_id,))
    claims = cursor.fetchall()
    cursor.close(); conn.close()

    return render_template("my_claims.html", user=user, claims=claims, current_user=g.current_user)

# ════════════════════════════════════════════════════════════
# ADMIN PANEL
# ════════════════════════════════════════════════════════════
@app.route("/admin/<int:admin_id>")
@admin_required
def admin_dashboard(admin_id):
    admin = get_user(admin_id)
    conn  = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("SELECT COUNT(*) AS n FROM users WHERE role != 'Admin'")
    total_users = cursor.fetchone()['n']
    cursor.execute("SELECT COUNT(*) AS n FROM lost_items")
    total_lost = cursor.fetchone()['n']
    cursor.execute("SELECT COUNT(*) AS n FROM found_items")
    total_found = cursor.fetchone()['n']
    cursor.execute("SELECT COUNT(*) AS n FROM claim_requests WHERE status = 'Pending'")
    pending_claims = cursor.fetchone()['n']

    cursor.execute("""
        SELECT cr.*, fi.item_name,
               u.name AS claimant_name, u.email AS claimant_email, u.mobile AS claimant_mobile
        FROM claim_requests cr
        JOIN found_items fi ON cr.found_item_id = fi.id
        JOIN users u ON cr.claimant_id = u.id
        ORDER BY cr.created_at DESC LIMIT 20
    """)
    claims = cursor.fetchall()

    cursor.execute("""
        SELECT li.*, u.name AS reporter_name FROM lost_items li
        JOIN users u ON li.user_id = u.id ORDER BY li.created_at DESC LIMIT 10
    """)
    lost_items = cursor.fetchall()

    cursor.execute("""
        SELECT fi.*, u.name AS reporter_name FROM found_items fi
        JOIN users u ON fi.user_id = u.id ORDER BY fi.created_at DESC LIMIT 10
    """)
    found_items = cursor.fetchall()
    cursor.close(); conn.close()

    return render_template("admin_dashboard.html",
        admin=admin,
        notifications_on=NOTIFICATIONS_ENABLED,
        stats={"users": total_users, "lost": total_lost,
               "found": total_found, "pending_claims": pending_claims},
        claims=claims, lost_items=lost_items, found_items=found_items
    , current_user=g.current_user)

@app.route("/admin/claim/<int:claim_id>/<action>/<int:admin_id>")
@admin_required
def admin_update_claim(claim_id, action, admin_id):
    if action not in ("approve", "reject"):
        return "Invalid action", 400

    new_status = "Approved" if action == "approve" else "Rejected"
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("UPDATE claim_requests SET status = %s WHERE id = %s", (new_status, claim_id))

    if action == "approve":
        # Mark the found item as Claimed and reject all other claims for same item
        cursor.execute("""
            UPDATE found_items fi JOIN claim_requests cr ON cr.found_item_id = fi.id
            SET fi.status = 'Claimed' WHERE cr.id = %s
        """, (claim_id,))
        cursor.execute("""
            UPDATE claim_requests cr
            JOIN claim_requests approved ON approved.id = %s
            SET cr.status = 'Rejected'
            WHERE cr.found_item_id = approved.found_item_id AND cr.id != %s AND cr.status = 'Pending'
        """, (claim_id, claim_id))

    cursor.close(); conn.close()
    flash(f"Claim {new_status} successfully.", "success")
    return redirect(url_for("admin_dashboard", admin_id=admin_id))

@app.route("/admin/lost_item/<int:item_id>/status/<int:admin_id>", methods=["POST"])
@admin_required
def admin_update_lost_status(item_id, admin_id):
    new_status = request.form.get("status")
    if new_status not in ("Searching", "Found", "Closed"):
        flash("Invalid status.", "error")
        return redirect(url_for("admin_dashboard", admin_id=admin_id))
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE lost_items SET status = %s WHERE id = %s", (new_status, item_id))
    cursor.close(); conn.close()
    flash(f"Lost item status updated to {new_status}.", "success")
    return redirect(url_for("admin_dashboard", admin_id=admin_id))

@app.route("/admin/found_item/<int:item_id>/status/<int:admin_id>", methods=["POST"])
@admin_required
def admin_update_found_status(item_id, admin_id):
    new_status = request.form.get("status")
    if new_status not in ("Available", "Claimed", "Closed"):
        flash("Invalid status.", "error")
        return redirect(url_for("admin_dashboard", admin_id=admin_id))
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE found_items SET status = %s WHERE id = %s", (new_status, item_id))
    cursor.close(); conn.close()
    flash(f"Found item status updated to {new_status}.", "success")
    return redirect(url_for("admin_dashboard", admin_id=admin_id))

@app.route("/admin/delete/<string:table>/<int:item_id>/<int:admin_id>")
@admin_required
def admin_delete_item(table, item_id, admin_id):
    if table not in ("lost_items", "found_items", "claim_requests"):
        return "Invalid table", 400
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(f"DELETE FROM {table} WHERE id = %s", (item_id,))
    cursor.close(); conn.close()
    flash("Deleted successfully.", "success")
    return redirect(url_for("admin_dashboard", admin_id=admin_id))

@app.route("/admin/users/<int:admin_id>")
@admin_required
def admin_users(admin_id):
    admin = get_user(admin_id)
    conn  = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM users ORDER BY created_at DESC")
    users = cursor.fetchall()
    cursor.close(); conn.close()
    return render_template("admin_users.html", admin=admin, users=users, current_user=g.current_user)

# ════════════════════════════════════════════════════════════
# OWNER / ADMIN DELETE — accessible from item detail pages
# ════════════════════════════════════════════════════════════

@app.route("/delete_lost/<int:item_id>/<int:user_id>", methods=["POST"])
def delete_lost_item(item_id, user_id):
    """Owner or Admin can delete a lost item."""
    user = get_user(user_id)
    if not user:
        return redirect(url_for("home"))

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT user_id FROM lost_items WHERE id = %s", (item_id,))
    item = cursor.fetchone()

    if not item:
        cursor.close(); conn.close()
        flash("Item not found.", "error")
        return redirect(url_for("lost_items_list", user_id=user_id))

    # Allow only owner or Admin
    if item["user_id"] != user_id and user["role"] != "Admin":
        cursor.close(); conn.close()
        flash("You don't have permission to delete this item.", "error")
        return redirect(url_for("lost_item_detail", item_id=item_id, viewer_id=user_id))

    cursor.execute("DELETE FROM lost_items WHERE id = %s", (item_id,))
    cursor.close(); conn.close()

    flash("Lost item deleted successfully.", "success")
    # Admin goes back to admin dashboard, owner goes to browse list
    if user["role"] == "Admin":
        return redirect(url_for("admin_dashboard", admin_id=user_id))
    return redirect(url_for("lost_items_list", user_id=user_id))


@app.route("/delete_found/<int:item_id>/<int:user_id>", methods=["POST"])
def delete_found_item(item_id, user_id):
    """Owner or Admin can delete a found item."""
    user = get_user(user_id)
    if not user:
        return redirect(url_for("home"))

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT user_id FROM found_items WHERE id = %s", (item_id,))
    item = cursor.fetchone()

    if not item:
        cursor.close(); conn.close()
        flash("Item not found.", "error")
        return redirect(url_for("found_items_list", user_id=user_id))

    if item["user_id"] != user_id and user["role"] != "Admin":
        cursor.close(); conn.close()
        flash("You don't have permission to delete this item.", "error")
        return redirect(url_for("found_item_detail", item_id=item_id, user_id=user_id))

    # Delete associated claim requests first (FK constraint)
    cursor.execute("DELETE FROM claim_requests WHERE found_item_id = %s", (item_id,))
    cursor.execute("DELETE FROM found_items WHERE id = %s", (item_id,))
    cursor.close(); conn.close()

    flash("Found item deleted successfully.", "success")
    if user["role"] == "Admin":
        return redirect(url_for("admin_dashboard", admin_id=user_id))
    return redirect(url_for("found_items_list", user_id=user_id))



import random as _random
import string as _string

@app.route("/forgot_password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "GET":
        return render_template("forgot_password.html")
    email = request.form.get("email", "").strip()
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM users WHERE email = %s", (email,))
    user = cursor.fetchone()
    if not user:
        cursor.close(); conn.close()
        return render_template("forgot_password.html", error="No account found with this email address.")
    temp_password = ''.join(_random.choices(_string.ascii_letters + _string.digits, k=8))
    cursor.execute("UPDATE users SET password = %s WHERE email = %s", (temp_password, email))
    cursor.close(); conn.close()
    send_notification_email(
        subject="\U0001f510 Password Reset \u2014 Campus Lost & Found",
        body=(
            f"Hi {user['name']},\n\n"
            f"Your temporary password is: {temp_password}\n\n"
            f"Please login and change it from Edit Profile.\n\n"
            f"\U0001f449 https://campus-lost-found-app.onrender.com\n\n"
            f"\u2014 Campus Lost & Found Team"
        ),
        recipient_list=[email]
    )
    return render_template("forgot_password.html", success=f"Temporary password sent to {email}. Check your inbox!")


@app.route("/settings/<int:user_id>")
def settings(user_id):
    user = get_user(user_id)
    if not user:
        return redirect(url_for('home'))
    return render_template("settings.html", user=user, current_user=g.current_user)


@app.route("/api/stats/<int:user_id>")
def api_stats(user_id):
    from flask import jsonify
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT COUNT(*) AS n FROM lost_items")
    lost = cursor.fetchone()['n']
    cursor.execute("SELECT COUNT(*) AS n FROM found_items")
    found = cursor.fetchone()['n']
    cursor.execute("SELECT COUNT(*) AS n FROM claim_requests WHERE claimant_id = %s", (user_id,))
    claims = cursor.fetchone()['n']
    cursor.close(); conn.close()
    return jsonify({"lost": lost, "found": found, "claims": claims})



@app.route("/admin/toggle_notifications/<int:admin_id>", methods=["POST"])
@admin_required
def toggle_notifications(admin_id):
    global NOTIFICATIONS_ENABLED
    NOTIFICATIONS_ENABLED = not NOTIFICATIONS_ENABLED
    status = "enabled ✅" if NOTIFICATIONS_ENABLED else "disabled 🔕"
    flash(f"Email notifications {status} for all users.", "success")
    return redirect(url_for("admin_dashboard", admin_id=admin_id))


# ════════════════════════════════════════════════════════════
# 🤖 GROQ AI FEATURES
# ════════════════════════════════════════════════════════════
from groq import Groq as GroqClient
from flask import jsonify

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")

def get_groq_client():
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY environment variable is not set.")
    return GroqClient(api_key=GROQ_API_KEY)

# ── 1. AI CHATBOT ──────────────────────────────────────────
@app.route("/api/chat", methods=["POST"])
def ai_chat():
    """Floating chatbot assistant for campus lost & found."""
    try:
        data = request.get_json()
        user_msg = data.get("message", "").strip()
        history  = data.get("history", [])   # [{role, content}, ...]
        if not user_msg:
            return jsonify({"reply": "Please type something!"}), 400

        client = get_groq_client()
        messages = [
            {"role": "system", "content": (
                "You are CampusBot, a friendly AI assistant for the Campus Lost & Found web app "
                "at MITS College. Help students with: reporting lost/found items, how to claim items, "
                "searching tips, and general campus lost & found questions. "
                "Be short, friendly and helpful. Use emojis occasionally. "
                "If asked something unrelated to campus/lost&found, politely redirect. "
                "App URL: https://campus-lost-found-app.onrender.com"
            )}
        ] + history[-6:] + [{"role": "user", "content": user_msg}]

        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages,
            max_tokens=300,
            temperature=0.7
        )
        reply = resp.choices[0].message.content.strip()
        return jsonify({"reply": reply})
    except Exception as e:
        print(f"Groq chat error: {e}", flush=True)
        return jsonify({"reply": "Sorry, AI is unavailable right now. Please try again later!"}), 500


# ── 2. SMART ITEM MATCHING ─────────────────────────────────
@app.route("/api/match/<int:lost_item_id>/<int:user_id>")
def ai_match_items(lost_item_id, user_id):
    """Given a lost item, find top matching found items using Groq AI."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        # Get the lost item
        cursor.execute("SELECT * FROM lost_items WHERE id = %s", (lost_item_id,))
        lost = cursor.fetchone()
        if not lost:
            cursor.close(); conn.close()
            return jsonify({"matches": [], "error": "Lost item not found"}), 404

        # Get available found items (max 30 for context window)
        cursor.execute("""
            SELECT fi.id, fi.item_name, fi.description, fi.category, fi.location_found,
                   fi.date_found, fi.image, u.name AS finder_name
            FROM found_items fi JOIN users u ON fi.user_id = u.id
            WHERE fi.status = 'Available'
            ORDER BY fi.created_at DESC LIMIT 30
        """)
        found_items = cursor.fetchall()
        cursor.close(); conn.close()

        if not found_items:
            return jsonify({"matches": [], "message": "No found items available yet."})

        # Build prompt for Groq
        found_list = "\n".join([
            f"ID:{item['id']} | {item['item_name']} | {item['category']} | {item['description'][:80]} | Found at: {item.get('location_found','?')}"
            for item in found_items
        ])

        prompt = f"""You are a lost & found matching AI. 
Lost Item: "{lost['item_name']}" | Category: {lost['category']} | Description: {lost['description'][:120]}

Found Items List:
{found_list}

Return ONLY a JSON array (no explanation) of the top 3 best matching found items, like:
[{{"id": 5, "score": 92, "reason": "Same category and description matches"}}, ...]
If no good matches, return []
Only include matches with score >= 40."""

        client = get_groq_client()
        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=200,
            temperature=0.2
        )
        raw = resp.choices[0].message.content.strip()

        # Parse JSON from response
        import re, json
        json_match = re.search(r'\[.*?\]', raw, re.DOTALL)
        if not json_match:
            return jsonify({"matches": []})

        match_list = json.loads(json_match.group())

        # Enrich with full item data
        found_map = {item["id"]: item for item in found_items}
        enriched = []
        for m in match_list:
            item_id = m.get("id")
            if item_id and item_id in found_map:
                item = found_map[item_id]
                enriched.append({
                    "id": item_id,
                    "item_name": item["item_name"],
                    "category": item["category"],
                    "description": item["description"][:100],
                    "location_found": item.get("location_found", ""),
                    "finder_name": item["finder_name"],
                    "image": item.get("image", ""),
                    "score": m.get("score", 50),
                    "reason": m.get("reason", "Similar item"),
                    "detail_url": f"/found_item/{item_id}/{user_id}"
                })

        return jsonify({"matches": enriched, "lost_item": lost["item_name"]})

    except Exception as e:
        print(f"Groq match error: {e}", flush=True)
        return jsonify({"matches": [], "error": str(e)}), 500


# ── 3. AI DESCRIPTION GENERATOR ───────────────────────────
@app.route("/api/generate_description", methods=["POST"])
def ai_generate_description():
    """Generate a good item description from basic keywords, enriched with DB context."""
    try:
        data = request.get_json()
        item_name = data.get("item_name", "").strip()
        category  = data.get("category", "").strip()
        keywords  = data.get("keywords", "").strip()
        item_type = data.get("type", "lost")  # "lost" or "found"

        if not item_name:
            return jsonify({"description": ""}), 400

        # Pull similar existing items from DB for context
        db_context = ""
        try:
            conn = get_db_connection()
            cursor = conn.cursor(dictionary=True)
            table = "lost_items" if item_type == "lost" else "found_items"
            if category:
                cursor.execute(
                    f"SELECT description FROM {table} WHERE category = %s ORDER BY id DESC LIMIT 5",
                    (category,)
                )
            else:
                cursor.execute(
                    f"SELECT description FROM {table} ORDER BY id DESC LIMIT 5"
                )
            rows = cursor.fetchall()
            cursor.close(); conn.close()
            if rows:
                samples = [r["description"] for r in rows if r.get("description")][:3]
                if samples:
                    db_context = "\n\nFor reference, here are similar existing descriptions on this campus:\n" + \
                                 "\n".join(f"- {s[:80]}" for s in samples)
        except Exception as db_err:
            print(f"DB context fetch error (non-fatal): {db_err}", flush=True)

        prompt = f"""Write a clear, helpful {item_type} item report description for a campus lost & found app.
Item: {item_name}
Category: {category}
Keywords/details: {keywords}{db_context}

Write ONLY the description (2-3 sentences, no intro, no quotes). Be specific and descriptive."""

        client = get_groq_client()
        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=120,
            temperature=0.6
        )
        desc = resp.choices[0].message.content.strip().strip('"\'\'` ')
        return jsonify({"description": desc})
    except Exception as e:
        print(f"Groq desc error: {e}", flush=True)
        return jsonify({"description": "", "error": str(e)}), 500



# ════════════════════════════════════════════════════════════
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
