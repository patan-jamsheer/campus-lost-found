from flask import Flask, render_template, request, redirect, url_for, flash, session, g, jsonify
from functools import wraps
import mysql.connector
import os
import threading
import re
import json
import random as _random
import string as _string
from datetime import datetime, timezone, timedelta
from werkzeug.utils import secure_filename
from flask_mail import Mail, Message
from groq import Groq as GroqClient
import cloudinary
import cloudinary.uploader

# IST timezone helper
IST = timezone(timedelta(hours=5, minutes=30))
def now_ist():
    return datetime.now(IST)
def today_ist():
    return now_ist().date()

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
app.config['MAIL_PORT']           = int(os.environ.get('MAIL_PORT') or 465)
app.config['MAIL_USE_TLS']        = False
app.config['MAIL_USE_SSL']         = True
app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD')  # ← PUT YOUR APP PASSWORD HERE
app.config['MAIL_DEFAULT_SENDER'] = os.environ.get('MAIL_USERNAME')

mail = Mail(app)

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
    try:
        os.makedirs(_folder, exist_ok=True)
    except Exception:
        pass

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

CATEGORIES = [
    'Electronics', 'Books & Stationery', 'Clothing & Accessories',
    'ID & Documents', 'Keys', 'Wallet & Money', 'Jewellery',
    'Bag & Luggage', 'Sports Equipment', 'Other'
]

# ── DB Connection Pool ──────────────────────────────────────
from mysql.connector import pooling as _pooling

db_pool = _pooling.MySQLConnectionPool(
    pool_name="campus_pool",
    pool_size=5,
    host=os.environ.get("MYSQL_HOST"),
    user=os.environ.get("MYSQL_USER"),
    password=os.environ.get("MYSQL_PASSWORD"),
    database=os.environ.get("MYSQL_DB"),
    port=int(os.environ.get("MYSQL_PORT", 3306)),
    ssl_disabled=False,
    autocommit=True
)

def get_db_connection():
    conn = db_pool.get_connection()
    cursor = conn.cursor()
    cursor.execute("SET time_zone = '+05:30'")
    cursor.close()
    return conn

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

# ── Login guard ─────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("home"))
        return f(*args, **kwargs)
    return decorated

# ── Admin guard ─────────────────────────────────────────────
def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        # Must be logged in first
        if "user_id" not in session:
            return redirect(url_for("home"))
        admin_id = kwargs.get('admin_id')
        if not admin_id:
            return redirect(url_for('home'))
        # Session user must match the admin_id in URL
        if session["user_id"] != admin_id:
            return "403 — Forbidden", 403
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
    email      = request.form["email"].strip().lower()
    mobile     = request.form["mobile"]
    password   = request.form["password"]

    # Gmail only check
    if not email.endswith("@gmail.com"):
        return render_template("signup.html",
            error="⚠ Only Gmail addresses are accepted (e.g. yourname@gmail.com).")

    # Check already registered
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT id FROM users WHERE email = %s", (email,))
    if cursor.fetchone():
        cursor.close(); conn.close()
        return render_template("signup.html",
            error="⚠ This email is already registered. Try logging in instead.")
    cursor.close(); conn.close()

    # Upload profile pic now (before OTP, so we have the URL ready)
    file = request.files.get("profile_pic")
    filename = "default.jpeg"
    if file and file.filename:
        filename = upload_to_cloudinary(file, "profiles") or "default.jpeg"

    # Generate 6-digit OTP
    otp = str(_random.randint(100000, 999999))

    # Store everything in session temporarily
    session["pending_registration"] = {
        "name": name, "department": department, "year": year,
        "section": section, "email": email, "mobile": mobile,
        "password": password, "profile_pic": filename, "otp": otp
    }

    # Send OTP email via Brevo
    send_notification_email(
        subject="🔐 Your Campus Lost & Found Verification Code",
        body=(
            f"Hi {name},\n\n"
            f"Your email verification code is:\n\n"
            f"  {otp}\n\n"
            f"Enter this code on the verification page to complete your registration.\n"
            f"This code expires when you close the page.\n\n"
            f"If you did not request this, ignore this email.\n\n"
            f"— Campus Lost & Found Team 🎓"
        ),
        recipient_list=[email]
    )

    return render_template("verify_otp.html", email=email)


@app.route("/verify_otp", methods=["GET", "POST"])
def verify_otp():
    # Handle direct GET visit (e.g. back button, direct URL)
    if request.method == "GET":
        pending = session.get("pending_registration")
        if not pending:
            return redirect(url_for("signup"))
        return render_template("verify_otp.html", email=pending["email"])

    entered_otp = request.form.get("otp", "").strip()
    pending     = session.get("pending_registration")

    if not pending:
        return render_template("signup.html",
            error="⚠ Session expired. Please sign up again.")

    if entered_otp != pending["otp"]:
        return render_template("verify_otp.html",
            email=pending["email"],
            error="⚠ Incorrect OTP. Please check your Gmail and try again.")

    # OTP correct — create the account
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO users (role, name, department, year, section, email, mobile, password, profile_pic)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """, ("Student", pending["name"], pending["department"], pending["year"],
          pending["section"], pending["email"], pending["mobile"],
          pending["password"], pending["profile_pic"]))
    cursor.close(); conn.close()

    session.pop("pending_registration", None)
    flash("✅ Email verified! Account created successfully. Please login.", "success")
    return redirect(url_for("home"))


    return redirect(url_for('home'))


@app.route("/resend_otp")
def resend_otp():
    pending = session.get("pending_registration")
    if not pending:
        return redirect(url_for("signup"))
    # Generate new OTP
    new_otp = str(_random.randint(100000, 999999))
    session["pending_registration"]["otp"] = new_otp
    session.modified = True
    send_notification_email(
        subject="🔐 New Verification Code — Campus Lost & Found",
        body=(
            f"Hi {pending['name']},\n\n"
            f"Your new verification code is:\n\n"
            f"  {new_otp}\n\n"
            f"— Campus Lost & Found Team 🎓"
        ),
        recipient_list=[pending["email"]]
    )
    return render_template("verify_otp.html", email=pending["email"],
        success="✅ New OTP sent to your Gmail!")



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
@login_required
def dashboard_user(user_id):
    # Prevent accessing another user's dashboard
    if session["user_id"] != user_id:
        return redirect(url_for("dashboard_user", user_id=session["user_id"]))
    user = get_user(user_id)
    if not user:
        return redirect(url_for("home"))
    if user["role"] == "Admin":
        return redirect(url_for("admin_dashboard", admin_id=user_id))
    return render_template("dashboard.html", user=user, current_user=g.current_user, active_page="dashboard")

# ════════════════════════════════════════════════════════════
# PROFILE
# ════════════════════════════════════════════════════════════
@app.route("/profile/<int:user_id>")
@login_required
def profile(user_id):
    user = get_user(user_id)
    if not user:
        return "User not found", 404
    # viewer = logged-in user (for back button going to correct dashboard)
    viewer_id = session.get("user_id", user_id)
    viewer = get_user(viewer_id) or user
    return render_template("profile.html", user=user, current_user=viewer, active_page="profile")

@app.route("/edit_profile/<int:user_id>")
@login_required
def edit_profile(user_id):
    if session["user_id"] != user_id:
        return "403 — You can only edit your own profile", 403
    user = get_user(user_id)
    if not user:
        return "User not found", 404
    return render_template("edit_profile.html", user=user, current_user=g.current_user, active_page="profile")

@app.route("/update_profile/<int:user_id>", methods=["POST"])
@login_required
def update_profile(user_id):
    if session["user_id"] != user_id:
        return "403 — You can only update your own profile", 403
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
@login_required
def report_lost(user_id):
    if session["user_id"] != user_id:
        return redirect(url_for("report_lost", user_id=session["user_id"]))
    user = get_user(user_id)
    if not user:
        return redirect(url_for("home"))
    return render_template("report_lost.html", user=user, current_user=g.current_user, active_page="report_lost")

@app.route("/report_lost", methods=["POST"])
@login_required
def submit_report_lost():
    user_id     = session["user_id"]   # ✅ NEVER trust user_id from form
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
    new_id = cursor.lastrowid  # ✅ grab ID before closing cursor
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

    flash(f"'{item_name}' reported! Checking for similar found items on campus... 🔍", "success")
    return redirect(url_for("lost_item_matches", item_id=new_id, user_id=user_id))

@app.route("/lost_item_matches/<int:item_id>/<int:user_id>")
def lost_item_matches(item_id, user_id):
    """Show AI-matched found items right after a lost item is submitted."""
    user = get_user(user_id)
    if not user:
        return redirect(url_for("home"))

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM lost_items WHERE id = %s", (item_id,))
    lost = cursor.fetchone()

    matches = []
    error_msg = None

    if lost:
        # Fetch all found items (no status filter)
        cursor.execute("""
            SELECT fi.id, fi.item_name, fi.description, fi.category,
                   fi.location_found, fi.date_found, fi.image,
                   fi.status, u.name AS finder_name
            FROM found_items fi JOIN users u ON fi.user_id = u.id
            ORDER BY fi.created_at DESC LIMIT 50
        """)
        found_items = cursor.fetchall()
        print(f"[Match Debug] Lost='{lost['item_name']}' cat='{lost['category']}' | Found items in DB: {len(found_items)}", flush=True)

        if found_items:
            # ── STEP 1: Direct DB match — same category (guaranteed, no AI needed) ──
            # Use strip() to handle any whitespace/encoding differences in DB values
            lost_cat = (lost['category'] or '').strip().lower()
            direct_matches = []
            for i in found_items:
                item_cat = (i['category'] or '').strip().lower()
                print(f"[Cat Debug] lost_cat='{lost_cat}' vs found_cat='{item_cat}' match={item_cat == lost_cat}", flush=True)
                if item_cat == lost_cat or lost_cat in item_cat or item_cat in lost_cat:
                    direct_matches.append({
                        "id": i["id"],
                        "item_name": i["item_name"],
                        "category": i["category"],
                        "description": i["description"][:100],
                        "location_found": i.get("location_found", ""),
                        "finder_name": i["finder_name"],
                        "image": i.get("image", ""),
                        "score": 80,
                        "reason": f"Same category: {i['category']}",
                        "status": i.get("status", ""),
                    })

            print(f"[Match Debug] Direct category matches: {len(direct_matches)}", flush=True)

            # ── STEP 2: Try Groq AI for smarter scoring on top of direct matches ──
            try:
                found_list = "\n".join([
                    f"ID:{i['id']} | {i['item_name']} | {i['category']} | {i['description'][:80]} | Found at: {i.get('location_found','?')}"
                    for i in found_items
                ])

                prompt = f"""You are a campus lost & found matching AI. Be VERY GENEROUS — if items could possibly be the same, include them.

Lost Item:
- Name: "{lost['item_name']}"
- Category: {lost['category']}
- Description: {lost['description'][:150]}

Found Items:
{found_list}

Rules:
- Same category = at least 70% score
- Similar name or description = higher score
- Score threshold: >= 20 (very generous)
- "bag", "backpack", "sling bag" all match "Bag & Luggage"
- "phone", "mobile" match "Electronics"

Return ONLY a JSON array:
[{{"id": 22, "score": 95, "reason": "Same category Bag & Luggage, similar item"}}]
If truly zero matches, return []"""

                client = get_groq_client()
                resp = client.chat.completions.create(
                    model="llama-3.3-70b-versatile",
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=300,
                    temperature=0.1
                )
                raw = resp.choices[0].message.content.strip()
                print(f"[Match Debug] Groq raw: {raw}", flush=True)

                json_match = re.search(r'\[.*?\]', raw, re.DOTALL)
                if json_match:
                    match_list = json.loads(json_match.group())
                    print(f"[Match Debug] Groq matches: {match_list}", flush=True)
                    found_map = {i["id"]: i for i in found_items}
                    ai_ids = set()
                    for m in match_list:
                        fid = m.get("id")
                        if fid and fid in found_map:
                            ai_ids.add(fid)
                            item = found_map[fid]
                            matches.append({
                                "id": fid,
                                "item_name": item["item_name"],
                                "category": item["category"],
                                "description": item["description"][:100],
                                "location_found": item.get("location_found", ""),
                                "finder_name": item["finder_name"],
                                "image": item.get("image", ""),
                                "score": m.get("score", 50),
                                "reason": m.get("reason", "Similar item"),
                                "status": item.get("status", ""),
                            })
                    # Add any direct matches Groq missed
                    for dm in direct_matches:
                        if dm["id"] not in ai_ids:
                            matches.append(dm)
                else:
                    # Groq returned no JSON — fall back to direct matches
                    matches = direct_matches

            except Exception as e:
                print(f"[Match Error] Groq failed: {e} — using direct matches", flush=True)
                matches = direct_matches  # Always show direct matches even if Groq fails

    cursor.close(); conn.close()

    # Sort by score descending
    matches.sort(key=lambda x: x["score"], reverse=True)

    return render_template("lost_item_matches.html",
        user=user, lost=lost, matches=matches,
        error_msg=error_msg, current_user=g.current_user, active_page="admin_dashboard")


@app.route("/lost_items/<int:user_id>")
@login_required
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
        filters={"search": search, "category": category, "status": status},
        active_page="lost_items"
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
        viewer=viewer, user=viewer, item=item, reporter=reporter, current_user=g.current_user, active_page="lost_items")

# ════════════════════════════════════════════════════════════
# FOUND ITEMS
# ════════════════════════════════════════════════════════════
@app.route("/report_found/<int:user_id>")
@login_required
def report_found(user_id):
    if session["user_id"] != user_id:
        return redirect(url_for("report_found", user_id=session["user_id"]))
    user = get_user(user_id)
    if not user:
        return redirect(url_for("home"))
    return render_template("report_found.html", user=user, current_user=g.current_user, active_page="report_found")

@app.route("/report_found", methods=["POST"])
@login_required
def submit_report_found():
    user_id        = session["user_id"]   # ✅ NEVER trust user_id from form
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

    # 🤖 AUTO-MATCH: scan all active lost items and notify matching owners
    threading.Thread(
        target=auto_notify_lost_item_owners,
        args=(item_name, description, category, location_found, date_found, int(user_id))
    ).start()

    return redirect(url_for("report_found", user_id=user_id))

@app.route("/found_items/<int:user_id>")
@login_required
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
        filters={"search": search, "category": category, "status": status},
        active_page="found_items"
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
        user=user, item=item, already_claimed=already_claimed, current_user=g.current_user, active_page="found_items")

# ════════════════════════════════════════════════════════════
# CLAIMS
# ════════════════════════════════════════════════════════════
@app.route("/claim_request/<int:item_id>/<int:user_id>", methods=["POST"])
@login_required
def submit_claim(item_id, user_id):
    if session["user_id"] != user_id:
        return "403 — Forbidden", 403
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

        # Get item and claimant details to notify the finder
        cursor.execute("""
            SELECT fi.item_name, fi.category, fi.location_found,
                   u.name AS finder_name, u.email AS finder_email,
                   c.name AS claimant_name, c.email AS claimant_email, c.mobile AS claimant_mobile
            FROM found_items fi
            JOIN users u ON fi.user_id = u.id
            JOIN users c ON c.id = %s
            WHERE fi.id = %s
        """, (user_id, item_id))
        info = cursor.fetchone()

        if info and info["finder_email"]:
            send_notification_email(
                subject=f"🔔 Someone is claiming your found item: {info['item_name']}",
                body=(
                    f"Hi {info['finder_name']},\n\n"
                    f"Someone has submitted a claim on the item you reported as found.\n\n"
                    f"📦 Item      : {info['item_name']}\n"
                    f"📁 Category  : {info['category']}\n"
                    f"📍 Found At  : {info['location_found']}\n\n"
                    f"👤 Claimant  : {info['claimant_name']}\n"
                    f"📧 Email     : {info['claimant_email']}\n"
                    f"📱 Mobile    : {info['claimant_mobile']}\n\n"
                    f"💬 Their message:\n\"{message}\"\n\n"
                    f"Please log in to review this claim and mark the item as handed over if verified.\n"
                    f"👉 https://campus-lost-found-app.onrender.com\n\n"
                    f"— Campus Lost & Found Team 🎓"
                ),
                recipient_list=[info["finder_email"]]
            )

        flash("✅ Claim request submitted! The finder has been notified by email.", "success")

    cursor.close(); conn.close()
    return redirect(url_for("found_item_detail", item_id=item_id, user_id=user_id))

@app.route("/my_claims/<int:user_id>")
@login_required
def my_claims(user_id):
    if session["user_id"] != user_id:
        return "403 — You can only view your own claims", 403
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

    return render_template("my_claims.html", user=user, claims=claims, current_user=g.current_user, active_page="claims")


@app.route("/incoming_claims/<int:user_id>")
@login_required
def incoming_claims(user_id):
    """Finder sees all claim requests on items THEY reported found."""
    if session["user_id"] != user_id:
        return "403 — You can only view your own incoming claims", 403
    user = get_user(user_id)
    if not user:
        return redirect(url_for("home"))

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT cr.*, fi.item_name, fi.category, fi.image, fi.location_found, fi.status AS item_status,
               u.name AS claimant_name, u.email AS claimant_email, u.mobile AS claimant_mobile,
               u.profile_pic AS claimant_pic
        FROM claim_requests cr
        JOIN found_items fi ON cr.found_item_id = fi.id
        JOIN users u ON cr.claimant_id = u.id
        WHERE fi.user_id = %s
        ORDER BY cr.created_at DESC
    """, (user_id,))
    claims = cursor.fetchall()
    cursor.close(); conn.close()

    return render_template("incoming_claims.html", user=user, claims=claims, current_user=g.current_user, active_page="incoming_claims")


@app.route("/handover/<int:claim_id>/<int:user_id>", methods=["POST"])
def handover_item(claim_id, user_id):
    """Finder marks the item as handed over — case closed."""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    # Get claim + item + claimant details
    cursor.execute("""
        SELECT cr.*, fi.item_name, fi.id AS found_item_id, fi.category,
               u.name AS claimant_name, u.email AS claimant_email
        FROM claim_requests cr
        JOIN found_items fi ON cr.found_item_id = fi.id
        JOIN users u ON cr.claimant_id = u.id
        WHERE cr.id = %s AND fi.user_id = %s
    """, (claim_id, user_id))
    claim = cursor.fetchone()

    if not claim:
        cursor.close(); conn.close()
        flash("⚠️ Claim not found or you are not authorized.", "error")
        return redirect(url_for("incoming_claims", user_id=user_id))

    # Mark claim as Approved and item as Closed
    cursor.execute("UPDATE claim_requests SET status = 'Approved' WHERE id = %s", (claim_id,))
    cursor.execute("UPDATE found_items SET status = 'Closed' WHERE id = %s", (claim["found_item_id"],))
    cursor.close(); conn.close()

    # Notify the claimant that their item is confirmed
    send_notification_email(
        subject=f"🎉 Your item has been handed over: {claim['item_name']}",
        body=(
            f"Hi {claim['claimant_name']},\n\n"
            f"Great news! The finder has confirmed that your item has been handed over to you.\n\n"
            f"📦 Item     : {claim['item_name']}\n"
            f"📁 Category : {claim['category']}\n\n"
            f"✅ Case Status: CLOSED — Item successfully returned!\n\n"
            f"Thank you for using Campus Lost & Found.\n"
            f"— Campus Lost & Found Team 🎓"
        ),
        recipient_list=[claim["claimant_email"]]
    )

    flash(f"✅ Item handed over to {claim['claimant_name']}! Case is now closed.", "success")
    return redirect(url_for("incoming_claims", user_id=user_id))



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

@app.route("/admin/delete_user/<int:user_id>/<int:admin_id>", methods=["POST"])
@admin_required
def admin_delete_user(user_id, admin_id):
    if user_id == admin_id:
        flash("⚠️ You cannot delete your own admin account.", "error")
        return redirect(url_for("admin_users", admin_id=admin_id))

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT name, role FROM users WHERE id = %s", (user_id,))
    user = cursor.fetchone()

    if not user:
        cursor.close(); conn.close()
        flash("⚠️ User not found.", "error")
        return redirect(url_for("admin_users", admin_id=admin_id))

    if user["role"] == "Admin":
        cursor.close(); conn.close()
        flash("⚠️ Cannot delete another admin account.", "error")
        return redirect(url_for("admin_users", admin_id=admin_id))

    cursor.execute("DELETE FROM claim_requests WHERE user_id = %s", (user_id,))
    cursor.execute("DELETE FROM lost_items WHERE user_id = %s", (user_id,))
    cursor.execute("DELETE FROM found_items WHERE user_id = %s", (user_id,))
    cursor.execute("DELETE FROM users WHERE id = %s", (user_id,))
    cursor.close(); conn.close()

    flash(f"✅ User '{user['name']}' and all their data has been deleted.", "success")
    return redirect(url_for("admin_users", admin_id=admin_id))


@app.route("/admin/users/<int:admin_id>")
@admin_required
def admin_users(admin_id):
    admin = get_user(admin_id)
    conn  = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM users ORDER BY created_at DESC")
    users = cursor.fetchall()
    cursor.close(); conn.close()
    return render_template("admin_users.html", admin=admin, users=users, current_user=g.current_user, active_page="admin_users")

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
@login_required
def settings(user_id):
    if session["user_id"] != user_id:
        return redirect(url_for("settings", user_id=session["user_id"]))
    user = get_user(user_id)
    if not user:
        return redirect(url_for('home'))
    return render_template("settings.html", user=user, current_user=g.current_user, active_page="settings")


@app.route("/api/stats/<int:user_id>")
def api_stats(user_id):
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


def auto_notify_lost_item_owners(found_name, found_desc, found_category, found_location, found_date, finder_user_id):
    """
    When a found item is submitted, use Groq to match it against all active lost items.
    Email the owners of matching lost items instantly.
    Runs in a background thread — never blocks the request.
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        # Get all active lost items with owner emails
        cursor.execute("""
            SELECT li.id, li.item_name, li.description, li.category, li.date_lost,
                   u.name AS owner_name, u.email AS owner_email
            FROM lost_items li JOIN users u ON li.user_id = u.id
            WHERE li.status = 'Searching'
            ORDER BY li.created_at DESC LIMIT 40
        """)
        lost_items = cursor.fetchall()
        cursor.close(); conn.close()

        if not lost_items:
            return

        # Build Groq prompt
        lost_list = "\n".join([
            f"ID:{i['id']} | {i['item_name']} | {i['category']} | {i['description'][:80]}"
            for i in lost_items
        ])


        prompt = f"""You are a lost & found matching AI.
A new found item was just reported:
Item: "{found_name}" | Category: {found_category} | Description: {found_desc[:120]} | Found at: {found_location}

Active Lost Items (people still searching):
{lost_list}

Return ONLY a JSON array of lost items that likely match the found item (score >= 40):
[{{"id": 3, "score": 88, "reason": "Same category, description matches closely"}}, ...]
If no matches, return []. Maximum 5 matches."""

        client = get_groq_client()
        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=250,
            temperature=0.2
        )
        raw = resp.choices[0].message.content.strip()
        json_match = re.search(r'\[.*?\]', raw, re.DOTALL)
        if not json_match:
            return

        matches = json.loads(json_match.group())
        if not matches:
            return

        # Map lost item IDs to their owners
        lost_map = {i["id"]: i for i in lost_items}

        for m in matches:
            lost_id = m.get("id")
            score   = m.get("score", 0)
            reason  = m.get("reason", "Similar item found")
            if not lost_id or lost_id not in lost_map:
                continue

            owner = lost_map[lost_id]
            send_notification_email(
                subject=f"🎉 Good news! A similar item to your '{owner['item_name']}' was just found!",
                body=(
                    f"Hi {owner['name']},\n\n"
                    f"Great news! Our AI found a possible match for your lost item.\n\n"
                    f"🔍 Your Lost Item   : {owner['item_name']} ({owner['category']})\n"
                    f"✅ Found Item       : {found_name} ({found_category})\n"
                    f"📍 Found At        : {found_location}\n"
                    f"📅 Date Found      : {found_date}\n"
                    f"🤖 AI Match Score  : {score}%\n"
                    f"💡 Reason          : {reason}\n\n"
                    f"Log in now to browse found items and submit a claim if it's yours!\n"
                    f"👉 https://campus-lost-found-app.onrender.com\n\n"
                    f"— Campus Lost & Found Team 🎓"
                ),
                recipient_list=[owner["owner_email"]]
            )
            print(f"✅ Auto-notified {owner['owner_email']} about match for lost item #{lost_id} (score {score}%)", flush=True)

    except Exception as e:
        print(f"❌ Auto-notify error: {e}", flush=True)


# ════════════════════════════════════════════════════════════
# 🤖 GROQ AI FEATURES
# ════════════════════════════════════════════════════════════

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")

def get_groq_client():
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY environment variable is not set.")
    return GroqClient(api_key=GROQ_API_KEY)

# ── 1. AI CHATBOT ──────────────────────────────────────────
def get_db_context_for_chat():
    """Pull live summary + recent items from DB to give Groq real data."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        # Stats
        cursor.execute("SELECT COUNT(*) AS n FROM lost_items WHERE status='Searching'")
        active_lost = cursor.fetchone()['n']
        cursor.execute("SELECT COUNT(*) AS n FROM found_items WHERE status='Available'")
        available_found = cursor.fetchone()['n']
        cursor.execute("SELECT COUNT(*) AS n FROM users WHERE role != 'Admin'")
        total_users = cursor.fetchone()['n']

        # Recent lost items (last 10)
        cursor.execute("""
            SELECT item_name, category, description, date_lost, status
            FROM lost_items ORDER BY created_at DESC LIMIT 10
        """)
        lost_items = cursor.fetchall()

        # Recent found items (last 10, available only)
        cursor.execute("""
            SELECT item_name, category, description, location_found, date_found
            FROM found_items WHERE status='Available' ORDER BY created_at DESC LIMIT 10
        """)
        found_items = cursor.fetchall()

        cursor.close(); conn.close()

        lost_lines = "\n".join([
            f"  - [{i['category']}] {i['item_name']}: {i['description'][:60]} (lost {i['date_lost']}, status: {i['status']}) | Browse: https://campus-lost-found-app.onrender.com"
            for i in lost_items
        ]) or "  None currently."

        found_lines = "\n".join([
            f"  - [{i['category']}] {i['item_name']}: {i['description'][:60]} (found at {i.get('location_found','?')} on {i['date_found']}) | Browse: https://campus-lost-found-app.onrender.com"
            for i in found_items
        ]) or "  None currently available."

        return (
            f"\n\n--- LIVE CAMPUS DATABASE (as of now) ---\n"
            f"📊 Stats: {active_lost} items being searched | {available_found} found items available | {total_users} registered students\n\n"
            f"🔍 Recent Lost Items (searching):\n{lost_lines}\n\n"
            f"✅ Recent Found Items (available to claim):\n{found_lines}\n"
            f"--- END OF DATABASE ---\n"
        )
    except Exception as e:
        print(f"DB context error (non-fatal): {e}", flush=True)
        return ""

@app.route("/api/chat", methods=["POST"])
def ai_chat():
    """Floating chatbot assistant with live DB knowledge."""
    try:
        data = request.get_json()
        user_msg = data.get("message", "").strip()
        history  = data.get("history", [])
        if not user_msg:
            return jsonify({"reply": "Please type something!"}), 400

        # Fetch live DB context
        db_context = get_db_context_for_chat()

        faq = (
            "--- CAMPUS LOST & FOUND FAQ ---\n\n"
            "Q: Who is the admin?\n"
            "A: The admin is the Lost & Found coordinator at MITS College who manages all items, approves or rejects claims, and oversees the system.\n\n"
            "Q: How do I contact the admin?\n"
            "A: Via the CampusBot chat in the app, or visit the college Lost & Found counter. Admin email: jamsheerkhan118@gmail.com\n\n"
            "Q: How do I report a lost item?\n"
            "A: Login → click Report Lost Item in sidebar → fill item name, category, description, date → submit. AI instantly scans for matching found items.\n\n"
            "Q: How do I report a found item?\n"
            "A: Login → click Report Found Item in sidebar → fill details and where you found it → submit.\n\n"
            "Q: How do I claim a found item?\n"
            "A: Browse Found Items → click the item → click Submit Claim → write why it is yours → admin reviews and approves or rejects.\n\n"
            "Q: How long does claim approval take?\n"
            "A: Typically within 24 hours on working days.\n\n"
            "Q: Will I get notified when my item is found?\n"
            "A: Yes! You get an automatic email when a found item matches your lost item report.\n\n"
            "Q: I forgot my password. What do I do?\n"
            "A: Click Forgot Password on the login page → enter your email → a temporary password is sent to your Gmail.\n\n"
            "Q: Only Gmail is accepted for registration?\n"
            "A: Yes, only @gmail.com addresses are accepted to ensure student authenticity.\n\n"
            "Q: What categories are available?\n"
            "A: Electronics, Books & Stationery, Clothing & Accessories, ID & Documents, Keys, Wallet & Money, Jewellery, Bag & Luggage, Sports Equipment, Other.\n\n"
            "Q: Can I delete my report?\n"
            "A: Yes. Go to the item detail page and click Delete. Only the owner or admin can delete.\n\n"
            "Q: What happens after my claim is approved?\n"
            "A: You get an email confirmation. Then coordinate with the finder or visit the Lost & Found counter to collect your item.\n\n"
            "--- END FAQ ---\n"
        )

        system_prompt = (
            "You are CampusBot, a friendly AI assistant for the Campus Lost & Found web app "
            "at MITS College. You have access to the live database AND a full FAQ.\n\n"
            "You can answer questions like:\n"
            "- 'Is there a blue bag found on campus?' → search the found items list\n"
            "- 'How many items are lost?' → use the stats\n"
            "- 'How do I contact the admin?' → use the FAQ\n"
            "- 'How do I claim an item?' → use the FAQ\n"
            "- 'I lost my laptop, has anyone found it?' → search found items for matches\n\n"
            "CRITICAL RULES:\n"
            "1. NEVER use HTML tags like <a href=...> in your responses. NEVER.\n"
            "2. For links, ONLY use markdown format: [Link Text](https://url) - nothing else.\n"
            "3. Do NOT include any URLs or links. Tell users to use the sidebar menu instead.\n"
            "4. Say things like: Browse Found Items in the sidebar, or click Report Lost Item in the menu.\n"
            "5. Be short, friendly and helpful. Use emojis occasionally.\n"
            "6. If asked something unrelated to campus/lost&found, politely redirect.\n"
            + faq
            + db_context
        )

        client = get_groq_client()
        messages = [
            {"role": "system", "content": system_prompt}
        ] + history[-6:] + [{"role": "user", "content": user_msg}]

        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages,
            max_tokens=350,
            temperature=0.5
        )
        reply = resp.choices[0].message.content.strip()
        import re as _re
        reply = _re.sub(r'<a[^>]+href=[^>]+>(.*?)</a>', r'\1', reply, flags=_re.IGNORECASE|_re.DOTALL)
        reply = _re.sub(r'<[^>]+>', '', reply)
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
    """Generate a good item description from keywords + optional image using Groq vision."""
    try:
        data = request.get_json()
        item_name    = data.get("item_name", "").strip()
        category     = data.get("category", "").strip()
        keywords     = data.get("keywords", "").strip()
        item_type    = data.get("type", "lost")
        image_base64 = data.get("image_base64", None)

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

        if image_base64:
            prompt_text = f"""You are helping write a campus lost & found report. Look ONLY at the image provided.
Describe EXACTLY what you see in the image - the actual item, its color, brand, size, condition, any markings.
DO NOT describe "{item_name}" - describe what is VISUALLY in the image.
Category hint: {category}
Extra details from user: {keywords}

Write ONLY 2-3 sentences describing the item in the image. No intro, no quotes."""
        else:
            prompt_text = f"""Write a clear, helpful {item_type} item report description for a campus lost & found app.
Item: {item_name}
Category: {category}
Keywords/details: {keywords}{db_context}

Write ONLY the description (2-3 sentences, no intro, no quotes). Be specific and descriptive."""

        client = get_groq_client()

        if image_base64:
            messages = [{
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}
                    },
                    {"type": "text", "text": prompt_text}
                ]
            }]
            resp = client.chat.completions.create(
                model="llama-3.2-11b-vision-preview",
                messages=messages,
                max_tokens=150,
                temperature=0.6
            )
        else:
            resp = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt_text}],
                max_tokens=120,
                temperature=0.6
            )

        desc = resp.choices[0].message.content.strip().strip('"''` ')
        return jsonify({"description": desc})
    except Exception as e:
        print(f"Groq desc error: {e}", flush=True)
        return jsonify({"description": "", "error": str(e)}), 500



@app.route("/api/debug_match/<int:lost_id>")
def debug_match(lost_id):
    """Temporary debug route — shows raw DB category values for matching."""
    from flask import jsonify
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT id, item_name, category, status FROM lost_items WHERE id = %s", (lost_id,))
    lost = cursor.fetchone()
    cursor.execute("SELECT id, item_name, category, status FROM found_items ORDER BY id DESC LIMIT 20")
    found = cursor.fetchall()
    cursor.close(); conn.close()

    result = {
        "lost_item": {
            "id": lost["id"] if lost else None,
            "item_name": lost["item_name"] if lost else None,
            "category": lost["category"] if lost else None,
            "category_repr": repr(lost["category"]) if lost else None,
        },
        "found_items": [
            {
                "id": f["id"],
                "item_name": f["item_name"],
                "category": f["category"],
                "category_repr": repr(f["category"]),
                "status": f["status"],
                "category_matches": (f["category"] or "").strip().lower() == (lost["category"] or "").strip().lower() if lost else False
            }
            for f in found
        ]
    }
    return jsonify(result)


# ════════════════════════════════════════════════════════════
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
