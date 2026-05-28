from flask import Flask, render_template, request, redirect, url_for, flash, session, send_from_directory
import os
import sqlite3
import boto3
import pymysql
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

# Configuration & Fallback Detection
DB_HOST = os.environ.get("DB_HOST", "")
USE_LOCAL = (DB_HOST == "__YOUR_HOST_NAME__" or os.environ.get("USE_LOCAL", "False").lower() in ("true", "1"))

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "")
app.permanent_session_lifetime = timedelta(minutes=15)  # Session timeout

# AWS S3 Config
S3_BUCKET = os.environ.get('S3_BUCKET', 'virtual-classroom-files-omkar-2026')
S3_REGION = os.environ.get('S3_REGION', 'ap-south-1')

# Setup local storage directory if running in local mode
LOCAL_UPLOAD_FOLDER = os.path.join(app.root_path, 'static', 'uploads')

s3 = None
if not USE_LOCAL:
    try:
        from botocore.client import Config
        s3 = boto3.client(
            's3', 
            region_name=S3_REGION, 
            endpoint_url=f"https://s3.{S3_REGION}.amazonaws.com",
            config=Config(signature_version='s3v4', s3={'addressing_style': 'virtual'})
        )
    except Exception as e:
        print(f"Warning: S3 client initialization failed ({e}). Switching to local mode.")
        USE_LOCAL = True

# Database Config
DB_USER = os.environ.get('DB_USER', '')
DB_PASSWORD = os.environ.get('DB_PASSWORD', '')
DB_NAME = os.environ.get('DB_NAME', 'virtual_classroom')

if USE_LOCAL:
    if not os.path.exists(LOCAL_UPLOAD_FOLDER):
        os.makedirs(LOCAL_UPLOAD_FOLDER)
    
    # Initialize local SQLite database schema
    def init_local_db():
        conn = sqlite3.connect('local_classroom.db')
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL
            )
        ''')
        conn.commit()
        conn.close()
    
    init_local_db()

def get_db_connection():
    if USE_LOCAL:
        conn = sqlite3.connect('local_classroom.db')
        conn.row_factory = sqlite3.Row
        return conn
    else:
        return pymysql.connect(
            host=DB_HOST,
            user=DB_USER,
            password=DB_PASSWORD,
            database=DB_NAME,
            cursorclass=pymysql.cursors.DictCursor
        )

# Query parameters placeholder logic
PLACEHOLDER = '?' if USE_LOCAL else '%s'

# Mock Courses Data
COURSES = [
    {
        'title': 'Web Development',
        'description': 'Learn HTML, CSS, JavaScript, and Flask to build responsive, modern web applications.',
        'badge': 'Beginner',
        'image': 'https://images.unsplash.com/photo-1547082299-de196ea013d6?auto=format&fit=crop&w=400&q=80',
        'link': 'web-development'
    },
    {
        'title': 'Data Science',
        'description': 'Master Python data analysis, visualization, and core machine learning algorithms.',
        'badge': 'Intermediate',
        'image': 'https://images.unsplash.com/photo-1551288049-bebda4e38f71?auto=format&fit=crop&w=400&q=80',
        'link': 'data-science'
    },
    {
        'title': 'Mobile Development',
        'description': 'Build high-performance cross-platform iOS and Android apps using modern frameworks.',
        'badge': 'Advanced',
        'image': 'https://images.unsplash.com/photo-1512941937669-90a1b58e7e9c?auto=format&fit=crop&w=400&q=80',
        'link': 'mobile-development'
    }
]

@app.route('/')
def home():
    return render_template('home.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('email')
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')

        if not username or not password:
            flash('All fields are required!', 'danger')
            return render_template('register.html')

        if password != confirm_password:
            flash('Passwords do not match!', 'danger')
            return render_template('register.html')

        hashed_password = generate_password_hash(password)

        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                f"INSERT INTO users (username, password) VALUES ({PLACEHOLDER}, {PLACEHOLDER})",
                (username, hashed_password)
            )
            conn.commit()
            flash('Registration successful! Please log in.', 'success')
            return redirect(url_for('login'))
        except (pymysql.MySQLError, sqlite3.IntegrityError) as e:
            # Handle duplicate email key
            is_dup = False
            if isinstance(e, sqlite3.IntegrityError):
                is_dup = True
            elif hasattr(e, 'args') and len(e.args) > 0 and e.args[0] == 1062:
                is_dup = True

            if is_dup:
                flash('Email address already registered!', 'danger')
            else:
                flash(f"Error: {str(e)}", 'danger')
        finally:
            cursor.close()
            conn.close()
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        if not username or not password:
            flash('All fields are required!', 'danger')
            return render_template('login.html')

        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(f"SELECT * FROM users WHERE username = {PLACEHOLDER}", (username,))
            user = cursor.fetchone()
            if user and check_password_hash(user['password'], password):
                session['username'] = username
                flash('Login successful!', 'success')
                return redirect(url_for('content'))
            else:
                flash('Invalid credentials!', 'danger')
        except Exception as e:
            flash(f"Database error: {str(e)}", 'danger')
        finally:
            cursor.close()
            conn.close()
    return render_template('login.html')

@app.route('/content', methods=['GET', 'POST'])
def content():
    if 'username' not in session:
        flash('Please log in to access content!', 'warning')
        return redirect(url_for('login'))

    if request.method == 'POST':
        if 'file' not in request.files:
            flash('No file part selected!', 'warning')
            return redirect(request.url)

        file = request.files['file']
        if file.filename == '':
            flash('No file selected for uploading!', 'warning')
            return redirect(request.url)

        # Basic validations
        ext = os.path.splitext(file.filename)[1].lower()
        if ext not in ['.pdf', '.jpg', '.jpeg', '.png']:
            flash("Invalid file type! Only PDF, JPG, and PNG are allowed.", 'danger')
        else:
            # Read size safely
            file.seek(0, os.SEEK_END)
            size = file.tell()
            file.seek(0)

            if size > 5 * 1024 * 1024:  # 5 MB size limit
                flash("File size exceeds 5MB limit!", 'danger')
            else:
                try:
                    if USE_LOCAL:
                        file.save(os.path.join(LOCAL_UPLOAD_FOLDER, file.filename))
                    else:
                        s3.upload_fileobj(file, S3_BUCKET, file.filename)
                    flash(f"File '{file.filename}' uploaded successfully!", 'success')
                except Exception as e:
                    flash(f"Error uploading file: {str(e)}", 'danger')

    files = []
    try:
        if USE_LOCAL:
            if os.path.exists(LOCAL_UPLOAD_FOLDER):
                for filename in os.listdir(LOCAL_UPLOAD_FOLDER):
                    filepath = os.path.join(LOCAL_UPLOAD_FOLDER, filename)
                    if os.path.isfile(filepath):
                        stat = os.stat(filepath)
                        files.append({
                            'Key': filename,
                            'Size': stat.st_size,
                            'LastModified': datetime.fromtimestamp(stat.st_mtime)
                        })
                # Sort by last modified descending
                files.sort(key=lambda x: x['LastModified'], reverse=True)
        else:
            contents = s3.list_objects_v2(Bucket=S3_BUCKET).get('Contents', [])
            for item in contents:
                files.append({
                    'Key': item['Key'],
                    'Size': item['Size'],
                    'LastModified': item['LastModified']
                })
    except Exception as e:
        flash(f"Error fetching files: {str(e)}", 'danger')

    return render_template('content.html', courses=COURSES, files=files)

@app.route('/enroll/<course_name>')
def enroll(course_name):
    if 'username' not in session:
        flash('Please log in to enroll in courses!', 'warning')
        return redirect(url_for('login'))
    
    matching = [c for c in COURSES if c['link'] == course_name]
    if matching:
        course_title = matching[0]['title']
        flash(f"Successfully enrolled in '{course_title}'!", 'success')
    else:
        flash('Course not found!', 'danger')
    return redirect(url_for('content'))

@app.route('/download/<filename>')
def download_file(filename):
    if 'username' not in session:
        flash('Please log in to download materials!', 'warning')
        return redirect(url_for('login'))

    if USE_LOCAL:
        try:
            return send_from_directory(LOCAL_UPLOAD_FOLDER, filename, as_attachment=True)
        except FileNotFoundError:
            flash("File not found on local storage!", 'danger')
            return redirect(url_for('content'))
    else:
        try:
            url = s3.generate_presigned_url(
                'get_object',
                Params={'Bucket': S3_BUCKET, 'Key': filename},
                ExpiresIn=3600
            )
            return redirect(url)
        except Exception as e:
            flash(f"Error generating download link: {str(e)}", 'danger')
            return redirect(url_for('content'))

@app.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out!', 'info')
    return redirect(url_for('home'))

if __name__ == '__main__':
    app.run(debug=True)
