from flask import Flask, render_template, request, jsonify, session, redirect, url_for
import sqlite3
import os
import uuid
import random
from datetime import datetime, timezone

from cachelib import SimpleCache
from pylti1p3.contrib.flask import FlaskOIDCLogin, FlaskMessageLaunch, FlaskCacheDataStorage
from pylti1p3.tool_config import ToolConfJsonFile
from pylti1p3.grade import Grade

app = Flask(__name__)
app.secret_key = "change-this-secret-key"

# In-memory cache for LTI launch data (use Redis in production)
_cache = SimpleCache()

LTI_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "lti_config.json")


def get_tool_conf():
    return ToolConfJsonFile(LTI_CONFIG_PATH)


def get_launch_data_storage():
    return FlaskCacheDataStorage(_cache)

DB_PATH = "data/results.db"


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    os.makedirs("data", exist_ok=True)
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS candidates (
        id TEXT PRIMARY KEY,
        created_at TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS answers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        candidate_id TEXT,
        question_name TEXT,
        parameters TEXT,
        response TEXT,
        correct INTEGER,
        score REAL,
        submitted_at TEXT
    )
    """)

    conn.commit()
    conn.close()


@app.before_request
def ensure_candidate():
    if request.endpoint in ("static", "lti_login", "lti_launch", "lti_jwks"):
        return

    if "candidate_id" not in session:
        candidate_id = str(uuid.uuid4())
        session["candidate_id"] = candidate_id

        conn = get_db()
        conn.execute(
            "INSERT INTO candidates (id, created_at) VALUES (?, ?)",
            (candidate_id, datetime.now(timezone.utc).isoformat())
        )
        conn.commit()
        conn.close()


@app.route("/")
def home():
    return redirect(url_for("equilibrium_question"))


# ── LTI 1.3 endpoints ────────────────────────────────────────────────────────

@app.route("/lti/login", methods=["GET", "POST"])
def lti_login():
    return FlaskOIDCLogin(
        request, get_tool_conf(),
        launch_data_storage=get_launch_data_storage()
    ).enable_check_cookies().redirect(
        url_for("lti_launch", _external=True)
    )


@app.route("/lti/launch", methods=["POST"])
def lti_launch():
    message_launch = FlaskMessageLaunch(
        request, get_tool_conf(),
        launch_data_storage=get_launch_data_storage()
    )
    launch_data = message_launch.get_launch_data()

    # Store LTI context in session for grade passback later
    session["lti_launch_id"] = message_launch.get_launch_id()
    session["lti_user_id"]   = launch_data.get("sub")

    return redirect(url_for("equilibrium_question"))


@app.route("/lti/.well-known/jwks.json")
def lti_jwks():
    return jsonify(get_tool_conf().get_jwks())


# ─────────────────────────────────────────────────────────────────────────────


@app.route("/equilibrium")
def equilibrium_question():
    # Randomize parameters once per page load
    a = random.randint(80, 100)
    b = round(random.uniform(1.0, 3.0), 2)
    c = random.randint(2, 8)
    d = round(random.uniform(0.5, 2.0), 2)

    return render_template(
        "equilibrium.html",
        a=a, b=b, c=c, d=d
    )


@app.route("/tax")
def tax_question():
    # Same supply/demand structure as equilibrium, plus unit tax t
    a = random.randint(80, 100)
    b = round(random.uniform(1.0, 3.0), 2)
    c = random.randint(2, 8)
    d = round(random.uniform(0.5, 2.0), 2)
    t = round(random.uniform(3.0, 8.0), 1)

    return render_template(
        "tax.html",
        a=a, b=b, c=c, d=d, t=t
    )


@app.route("/utility")
def utility_question():
    px = round(random.uniform(1.0, 3.0), 1)
    py = round(random.uniform(1.0, 3.0), 1)
    I  = random.randint(20, 40)
    return render_template("utility.html", px=px, py=py, I=I)


@app.route("/externality")
def externality_question():
    # Demand: P = a - bQ
    # PMC:   P = c + dQ
    # MED:   eQ
    a = random.randint(70, 100)
    b = round(random.uniform(0.8, 2.0), 2)
    c = random.randint(5, 20)
    d = round(random.uniform(0.5, 1.5), 2)
    e = round(random.uniform(0.5, 2.0), 2)

    return render_template(
        "externality.html",
        a=a, b=b, c=c, d=d, e=e
    )


@app.route("/save_answer", methods=["POST"])
def save_answer():
    payload = request.get_json(force=True)
    candidate_id = session["candidate_id"]

    question_name = payload.get("question_name")
    parameters = str(payload.get("parameters"))
    response = str(payload.get("response"))
    correct = int(payload.get("correct", 0))
    score = float(payload.get("score", 0.0))

    conn = get_db()
    conn.execute("""
        INSERT INTO answers
        (candidate_id, question_name, parameters, response, correct, score, submitted_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        candidate_id,
        question_name,
        parameters,
        response,
        correct,
        score,
        datetime.now(timezone.utc).isoformat()
    ))
    conn.commit()
    conn.close()

    return jsonify({"status": "ok"})


@app.route("/results")
def results():
    candidate_id = session["candidate_id"]
    conn = get_db()
    rows = conn.execute("""
        SELECT question_name, correct, score, submitted_at
        FROM answers
        WHERE candidate_id = ?
    """, (candidate_id,)).fetchall()
    conn.close()

    total_score = sum(row["score"] for row in rows)
    max_score   = 4  # one point per question

    # Send grade to Moodle if this was an LTI launch
    if "lti_launch_id" in session:
        try:
            message_launch = FlaskMessageLaunch.from_cache(
                session["lti_launch_id"], request, get_tool_conf(),
                launch_data_storage=get_launch_data_storage()
            )
            if message_launch.has_ags():
                grade = Grade()
                grade.set_score_given(total_score)
                grade.set_score_maximum(max_score)
                grade.set_timestamp(datetime.now(timezone.utc).isoformat() + "Z")
                grade.set_activity_progress("Completed")
                grade.set_grading_progress("FullyGraded")
                grade.set_user_id(session["lti_user_id"])
                message_launch.get_ags().put_grade(grade)
        except Exception as e:
            app.logger.warning("LTI grade passback failed: %s", e)

    return {
        "candidate_id": candidate_id,
        "answers": [dict(row) for row in rows],
        "total_score": total_score,
        "max_score": max_score
    }


if __name__ == "__main__":
    init_db()
    app.run(debug=True)