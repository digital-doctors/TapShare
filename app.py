from flask import Flask, render_template, request, send_file, redirect, jsonify, url_for, session
import random
import io
import time
import qrcode
from werkzeug.middleware.proxy_fix import ProxyFix
import base64
import json
import os
from datetime import datetime, timedelta

app = Flask(__name__)
app.secret_key = 'your-secret-key-change-in-production'
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

# File paths
USERS_FILE = 'users.json'
INBOX_FILE = 'inbox.json'
FRIENDS_FILE = 'friends.json'

# In-memory storage for code-based sharing
FILES = {}
DOWNLOADED = set()
EXPIRATION_SECONDS = 600  # 10 minutes

# User management functions
def load_users():
    if os.path.exists(USERS_FILE):
        with open(USERS_FILE, 'r') as f:
            return json.load(f)
    return {}

def save_users(users):
    with open(USERS_FILE, 'w') as f:
        json.dump(users, f, indent=2)

def load_inbox():
    if os.path.exists(INBOX_FILE):
        with open(INBOX_FILE, 'r') as f:
            return json.load(f)
    return {}

def save_inbox(inbox):
    with open(INBOX_FILE, 'w') as f:
        json.dump(inbox, f, indent=2)

def load_friends():
    if os.path.exists(FRIENDS_FILE):
        with open(FRIENDS_FILE, 'r') as f:
            return json.load(f)
    return {}

def save_friends(friends):
    with open(FRIENDS_FILE, 'w') as f:
        json.dump(friends, f, indent=2)

def get_user_friends(username):
    friends = load_friends()
    return friends.get(username, {"friends": [], "requests_sent": [], "requests_received": []})

def cleanup_storage():
    now = time.time()
    # Clean up code-based files
    expired = [c for c, f in FILES.items() if now > f["expires"]]
    for c in expired:
        del FILES[c]
    
    # Clean up inbox files (2 days = 172800 seconds)
    inbox = load_inbox()
    for username in list(inbox.keys()):
        inbox[username] = [
            f for f in inbox[username] 
            if now < f["expires"]
        ]
        if not inbox[username]:
            del inbox[username]
    save_inbox(inbox)

def generate_qr_code(code):
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=2,
    )
    qr_url = url_for('download', code=code, _external=True)
    qr.add_data(qr_url)
    qr.make(fit=True)
    
    img = qr.make_image(fill_color="black", back_color="white")
    
    buffer = io.BytesIO()
    img.save(buffer, format='PNG')
    buffer.seek(0)
    qr_base64 = base64.b64encode(buffer.getvalue()).decode()
    
    return f"data:image/png;base64,{qr_base64}"

@app.route("/")
def index():
    cleanup_storage()
    return render_template("index.html", logged_in='username' in session, username=session.get('username'))

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        first_name = request.form.get("first_name", "").strip()
        last_name = request.form.get("last_name", "").strip()
        
        if not first_name or not last_name:
            return render_template("register.html", error="Please enter both first and last name")
        
        username = f"{first_name} {last_name}"
        users = load_users()
        
        if username in users:
            return render_template("register.html", error="This name is already registered. Please login instead.")
        
        users[username] = {
            "registered_date": datetime.now().isoformat()
        }
        save_users(users)
        
        # Initialize friends data
        friends = load_friends()
        friends[username] = {
            "friends": [],
            "requests_sent": [],
            "requests_received": []
        }
        save_friends(friends)
        
        session['username'] = username
        return redirect("/")
    
    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        first_name = request.form.get("first_name", "").strip()
        last_name = request.form.get("last_name", "").strip()
        
        if not first_name or not last_name:
            return render_template("login.html", error="Please enter both first and last name")
        
        username = f"{first_name} {last_name}"
        users = load_users()
        
        if username not in users:
            return render_template("login.html", error="Name not found. Please register first.")
        
        session['username'] = username
        return redirect("/")
    
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.pop('username', None)
    return redirect("/")

@app.route("/friends")
def friends():
    if 'username' not in session:
        return redirect("/login")
    
    username = session['username']
    user_friends = get_user_friends(username)
    
    return render_template("friends.html", 
                         username=username,
                         friends=user_friends['friends'],
                         requests_received=user_friends['requests_received'],
                         requests_sent=user_friends['requests_sent'])

@app.route("/friends/search", methods=["POST"])
def search_friends():
    if 'username' not in session:
        return redirect("/login")
    
    query = request.form.get("query", "").strip()
    if len(query) < 2:
        return redirect("/friends")
    
    users = load_users()
    current_user = session['username']
    user_friends = get_user_friends(current_user)
    
    matches = []
    for name in users.keys():
        if name != current_user and query.lower() in name.lower():
            status = "none"
            if name in user_friends['friends']:
                status = "friend"
            elif name in user_friends['requests_sent']:
                status = "sent"
            elif name in user_friends['requests_received']:
                status = "received"
            
            matches.append({"name": name, "status": status})
    
    return render_template("friends.html",
                         username=current_user,
                         friends=user_friends['friends'],
                         requests_received=user_friends['requests_received'],
                         requests_sent=user_friends['requests_sent'],
                         search_results=matches[:10])

@app.route("/friends/add/<username>", methods=["POST"])
def add_friend(username):
    if 'username' not in session:
        return jsonify({"success": False, "error": "Not logged in"})
    
    current_user = session['username']
    friends = load_friends()
    
    # Add to current user's sent requests
    if current_user not in friends:
        friends[current_user] = {"friends": [], "requests_sent": [], "requests_received": []}
    
    if username not in friends[current_user]['requests_sent'] and username not in friends[current_user]['friends']:
        friends[current_user]['requests_sent'].append(username)
    
    # Add to target user's received requests
    if username not in friends:
        friends[username] = {"friends": [], "requests_sent": [], "requests_received": []}
    
    if current_user not in friends[username]['requests_received'] and current_user not in friends[username]['friends']:
        friends[username]['requests_received'].append(current_user)
    
    save_friends(friends)
    return jsonify({"success": True})

@app.route("/friends/accept/<username>", methods=["POST"])
def accept_friend(username):
    if 'username' not in session:
        return redirect("/login")
    
    current_user = session['username']
    friends = load_friends()
    
    # Remove from requests
    if username in friends[current_user]['requests_received']:
        friends[current_user]['requests_received'].remove(username)
    if current_user in friends[username]['requests_sent']:
        friends[username]['requests_sent'].remove(current_user)
    
    # Add to friends
    if username not in friends[current_user]['friends']:
        friends[current_user]['friends'].append(username)
    if current_user not in friends[username]['friends']:
        friends[username]['friends'].append(current_user)
    
    save_friends(friends)
    return redirect("/friends")

@app.route("/friends/reject/<username>", methods=["POST"])
def reject_friend(username):
    if 'username' not in session:
        return redirect("/login")
    
    current_user = session['username']
    friends = load_friends()
    
    # Remove from requests
    if username in friends[current_user]['requests_received']:
        friends[current_user]['requests_received'].remove(username)
    if current_user in friends[username]['requests_sent']:
        friends[username]['requests_sent'].remove(current_user)
    
    save_friends(friends)
    return redirect("/friends")

@app.route("/send", methods=["GET", "POST"])
def send():
    cleanup_storage()
    
    # Get user's friends list if logged in
    friends_list = []
    if 'username' in session:
        user_friends = get_user_friends(session['username'])
        friends_list = user_friends['friends']
    
    if request.method == "POST":
        file = request.files.get("file")
        send_type = request.form.get("send_type")
        
        if not file:
            return "No file uploaded"
        
        if send_type == "user":
            # Send to specific user (must be friend)
            recipient = request.form.get("recipient")
            if not recipient:
                return render_template("send.html", error="Please select a recipient", logged_in='username' in session, friends=friends_list)
            
            # Check if recipient is a friend
            if recipient not in friends_list:
                return render_template("send.html", error="You can only send files to friends", logged_in='username' in session, friends=friends_list)
            
            inbox = load_inbox()
            if recipient not in inbox:
                inbox[recipient] = []
            
            file_id = str(random.randint(100000, 999999))
            inbox[recipient].append({
                "id": file_id,
                "filename": file.filename,
                "data": base64.b64encode(file.read()).decode(),
                "sender": session.get('username', 'Anonymous'),
                "expires": time.time() + (2 * 24 * 60 * 60),  # 2 days
                "timestamp": datetime.now().isoformat()
            })
            save_inbox(inbox)
            
            return render_template("send.html", success=True, sent_to_user=True, recipient=recipient, logged_in='username' in session, friends=friends_list)
        
        else:
            # Send via code/QR
            code = str(random.randint(100000, 999999))
            FILES[code] = {
                "filename": file.filename,
                "data": file.read(),
                "expires": time.time() + EXPIRATION_SECONDS
            }
            qr_code = generate_qr_code(code)
            return render_template("send.html", code=code, qr_code=qr_code, success=True, sent_to_user=False, logged_in='username' in session, friends=friends_list)
    
    return render_template("send.html", success=False, logged_in='username' in session, friends=friends_list)

@app.route("/receive", methods=["GET", "POST"])
def receive():
    cleanup_storage()
    code_from_url = request.args.get("code", "")
    
    if request.method == "POST":
        code = request.form["code"]
        if code in FILES:
            return redirect(f"/download/{code}")
        else:
            return render_template("receive.html", error=True, pre_filled_code=code_from_url)
    
    if code_from_url and code_from_url in FILES:
        return redirect(f"/download/{code_from_url}")
    
    error = code_from_url and code_from_url not in FILES
    return render_template("receive.html", error=error, pre_filled_code=code_from_url)

@app.route("/inbox")
def inbox():
    if 'username' not in session:
        return redirect("/login")
    
    cleanup_storage()
    inbox_data = load_inbox()
    user_files = inbox_data.get(session['username'], [])
    
    return render_template("inbox.html", files=user_files, username=session['username'])

@app.route("/inbox/download/<file_id>")
def inbox_download(file_id):
    if 'username' not in session:
        return redirect("/login")
    
    inbox_data = load_inbox()
    user_files = inbox_data.get(session['username'], [])
    
    file_to_download = None
    for f in user_files:
        if f['id'] == file_id:
            file_to_download = f
            break
    
    if not file_to_download:
        return "File not found or expired"
    
    # Remove file after download
    inbox_data[session['username']] = [f for f in user_files if f['id'] != file_id]
    if not inbox_data[session['username']]:
        del inbox_data[session['username']]
    save_inbox(inbox_data)
    
    file_data = base64.b64decode(file_to_download['data'])
    return send_file(
        io.BytesIO(file_data),
        as_attachment=True,
        download_name=file_to_download['filename']
    )

@app.route("/download/<code>")
def download(code):
    cleanup_storage()
    if code not in FILES:
        return "Link expired"
    f = FILES.pop(code)
    DOWNLOADED.add(code)
    return send_file(
        io.BytesIO(f["data"]),
        as_attachment=True,
        download_name=f["filename"]
    )

@app.route("/api/check-download/<code>")
def check_download(code):
    return jsonify({"downloaded": code in DOWNLOADED})

if __name__ == "__main__":
    app.run(debug=True)