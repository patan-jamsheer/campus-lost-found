from flask import Flask, render_template, request, redirect, url_for, flash, session
from functools import wraps
import mysql.connector
import os
import threading
from werkzeug.utils import secure_filename
from flask_mail import Mail, Message

app = Flask(__name__)
app.secret_key = "campus_secret_key_2024"

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
        host=os.environ.get("MYSQL_HOST", "sql12.freesqldatabase.com"),
        user=os.environ.get("MYSQL_USER", "sql12818306"),
        password=os.environ.get("MYSQL_PASSWORD", "HgCSNGey8Q"),
        database=os.environ.get("MYSQL_DB", "sql12818306"),
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
    """Send email to all users in a background thread (so page doesn't slow down)."""
    def send():
        with app.app_context():
            try:
                msg = Message(subject=subject, recipients=recipient_list, body=body)
                mail.send(msg)
                print(f"✅ Notification sent to {len(recipient_list)} users.", flush=True)
            except Exception as e:
                import traceback
                print(f"❌ Email error: {e}", flush=True)
                print(traceback.format_exc(), flush=True)
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
        filename = secure_filename(file.filename)
        file.save(os.path.join(UPLOAD_FOLDER, filename))

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
    return render_template("signup.html", success="Account created! Redirecting to login...")


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
    return render_template("dashboard.html", user=user)

# ════════════════════════════════════════════════════════════
# PROFILE
# ════════════════════════════════════════════════════════════
@app.route("/profile/<int:user_id>")
def profile(user_id):
    user = get_user(user_id)
    if not user:
        return "User not found", 404
    return render_template("profile.html", user=user)

@app.route("/edit_profile/<int:user_id>")
def edit_profile(user_id):
    user = get_user(user_id)
    if not user:
        return "User not found", 404
    return render_template("edit_profile.html", user=user)

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
            filename = secure_filename(file.filename)
            file.save(os.path.join(UPLOAD_FOLDER, filename))
            profile_pic = filename

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
    return render_template("report_lost.html", user=user)

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
        image_filename = secure_filename(file.filename)
        file.save(os.path.join(LOST_ITEM_UPLOAD_FOLDER, image_filename))

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO lost_items (user_id, item_name, description, category, date_lost, image, status)
        VALUES (%s,%s,%s,%s,%s,%s,%s)
    """, (user_id, item_name, description, category, date_lost, image_filename, status))
    cursor.close(); conn.close()

    # 🔔 Send email notification to ALL users
    all_emails = get_all_user_emails()
    if all_emails:
        send_notification_email(
            subject=f"🔍 Lost Item Alert: {item_name}",
            body=(
                f"Hi! Someone on Campus Lost & Found is looking for their lost item.\n\n"
                f"📦 Item     : {item_name}\n"
                f"📁 Category : {category}\n"
                f"📝 Details  : {description}\n"
                f"📅 Lost On  : {date_lost}\n\n"
                f"Have you seen it? Log in and help them out!\n"
                f"👉 https://campus-lost-found-app.onrender.com/lost_items/{user_id}\n\n"
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
        user=user, items=items, total=len(items),
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
        viewer=viewer, user=viewer, item=item, reporter=reporter)

# ════════════════════════════════════════════════════════════
# FOUND ITEMS
# ════════════════════════════════════════════════════════════
@app.route("/report_found/<int:user_id>")
def report_found(user_id):
    user = get_user(user_id)
    if not user:
        return redirect(url_for("home"))
    return render_template("report_found.html", user=user)

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
        image_filename = secure_filename(file.filename)
        file.save(os.path.join(FOUND_ITEM_UPLOAD_FOLDER, image_filename))

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO found_items (user_id, item_name, description, category, location_found, date_found, image)
        VALUES (%s,%s,%s,%s,%s,%s,%s)
    """, (user_id, item_name, description, category, location_found, date_found, image_filename))
    cursor.close(); conn.close()

    # 🔔 Send email notification to ALL users
    all_emails = get_all_user_emails()
    if all_emails:
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
                f"👉 https://campus-lost-found-app.onrender.com/found_items/{user_id}\n\n"
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
        user=user, items=items, total=len(items),
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
        user=user, item=item, already_claimed=already_claimed)

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

    return render_template("my_claims.html", user=user, claims=claims)

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
        stats={"users": total_users, "lost": total_lost,
               "found": total_found, "pending_claims": pending_claims},
        claims=claims, lost_items=lost_items, found_items=found_items
    )

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
    return render_template("admin_users.html", admin=admin, users=users)

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


# ════════════════════════════════════════════════════════════
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)