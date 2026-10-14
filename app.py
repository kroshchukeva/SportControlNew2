
import sqlite3
from pathlib import Path
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from flask import (
    Flask, render_template, request,
    redirect, url_for, flash, abort
)

app = Flask(__name__)
app.secret_key = "change-this-secret-key-before-deployment"
app.jinja_env.globals["now"] = datetime.now

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "database.db"


# -------------------- DATABASE --------------------

def get_db():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_db():
    with get_db() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS schools (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                address TEXT DEFAULT '',
                notes TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS groups_table (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                school_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                monthly_price INTEGER NOT NULL DEFAULT 0,
                annual_price INTEGER NOT NULL DEFAULT 0,
                single_price INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (school_id)
                    REFERENCES schools(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS students (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id INTEGER NOT NULL,
                full_name TEXT NOT NULL,
                parent_phone TEXT DEFAULT '',
                notes TEXT DEFAULT '',
                FOREIGN KEY (group_id)
                    REFERENCES groups_table(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS trainings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id INTEGER NOT NULL,
                training_date TEXT NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT DEFAULT '',
                notes TEXT DEFAULT '',
                FOREIGN KEY (group_id)
                    REFERENCES groups_table(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER NOT NULL,
                payment_type TEXT NOT NULL,
                amount INTEGER NOT NULL,
                payment_date TEXT NOT NULL,
                covered_month TEXT DEFAULT '',
                notes TEXT DEFAULT '',
                FOREIGN KEY (student_id)
                    REFERENCES students(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS attendance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                training_id INTEGER NOT NULL,
                student_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'present',
                UNIQUE(training_id, student_id),
                FOREIGN KEY (training_id)
                    REFERENCES trainings(id) ON DELETE CASCADE,
                FOREIGN KEY (student_id)
                    REFERENCES students(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS annual_fees (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER NOT NULL UNIQUE,
                amount INTEGER NOT NULL DEFAULT 0,
                is_paid INTEGER NOT NULL DEFAULT 0,
                payment_date TEXT DEFAULT '',
                FOREIGN KEY (student_id)
                    REFERENCES students(id) ON DELETE CASCADE
            );
        """)



# -------------------- HELPERS --------------------

def parse_price(value, field_name):
    try:
        amount = Decimal((value or "0").strip().replace(",", "."))
        if not amount.is_finite() or amount < 0:
            raise ValueError
        # Store money as whole hryvnias in this first version.
        if amount != amount.to_integral_value():
            raise ValueError
        return int(amount)
    except (InvalidOperation, ValueError):
        raise ValueError(
            f"Поле «{field_name}» має містити цілу невід'ємну суму в гривнях."
        )


def get_school(school_id):
    with get_db() as db:
        school = db.execute(
            "SELECT * FROM schools WHERE id = ?", (school_id,)
        ).fetchone()
    if school is None:
        abort(404)
    return school


def get_group(group_id):
    with get_db() as db:
        group = db.execute("""
            SELECT g.*, s.name AS school_name
            FROM groups_table g
            JOIN schools s ON s.id = g.school_id
            WHERE g.id = ?
        """, (group_id,)).fetchone()
    if group is None:
        abort(404)
    return group


# -------------------- DASHBOARD --------------------

@app.route("/")
def dashboard():
    with get_db() as db:
        stats = {
            "schools": db.execute(
                "SELECT COUNT(*) FROM schools"
            ).fetchone()[0],
            "groups": db.execute(
                "SELECT COUNT(*) FROM groups_table"
            ).fetchone()[0],
            "students": db.execute(
                "SELECT COUNT(*) FROM students"
            ).fetchone()[0],
            "income": db.execute(
                "SELECT COALESCE(SUM(amount), 0) FROM payments "
                "WHERE substr(payment_date, 1, 7) = ?",
                (date.today().strftime("%Y-%m"),)
            ).fetchone()[0],
        }

        upcoming = db.execute("""
            SELECT t.*, g.name AS group_name, s.name AS school_name
            FROM trainings t
            JOIN groups_table g ON g.id = t.group_id
            JOIN schools s ON s.id = g.school_id
            WHERE t.training_date >= ?
            ORDER BY t.training_date, t.start_time
            LIMIT 8
        """, (date.today().isoformat(),)).fetchall()

        recent_payments = db.execute("""
            SELECT p.*, st.full_name
            FROM payments p
            JOIN students st ON st.id = p.student_id
            ORDER BY p.payment_date DESC, p.id DESC
            LIMIT 5
        """).fetchall()

    return render_template(
        "dashboard.html",
        stats=stats,
        upcoming=upcoming,
        recent_payments=recent_payments
    )


# -------------------- SCHOOLS --------------------

@app.route("/schools")
def schools():
    with get_db() as db:
        items = db.execute("""
            SELECT s.*,
                   COUNT(DISTINCT g.id) AS groups_count,
                   COUNT(DISTINCT st.id) AS students_count
            FROM schools s
            LEFT JOIN groups_table g ON g.school_id = s.id
            LEFT JOIN students st ON st.group_id = g.id
            GROUP BY s.id
            ORDER BY s.name
        """).fetchall()
    return render_template("schools.html", schools=items)

@app.route("/schools/<int:school_id>")
def school_detail(school_id):
    with get_db() as db:
        school = db.execute(
            "SELECT * FROM schools WHERE id = ?",
            (school_id,)
        ).fetchone()

        if school is None:
            abort(404)

        groups = db.execute("""
            SELECT
                g.*,
                COUNT(st.id) AS student_count
            FROM groups_table g
            LEFT JOIN students st ON st.group_id = g.id
            WHERE g.school_id = ?
            GROUP BY g.id
            ORDER BY g.name
        """, (school_id,)).fetchall()

    return render_template(
        "school_detail.html",
        school=school,
        groups=groups
    )



@app.route("/schools/add", methods=["GET", "POST"])
def school_add():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        address = request.form.get("address", "").strip()
        notes = request.form.get("notes", "").strip()

        if not name:
            flash("Введи назву школи.", "error")
            return render_template("school_form.html", school=None)

        with get_db() as db:
            db.execute(
                "INSERT INTO schools (name, address, notes) VALUES (?, ?, ?)",
                (name, address, notes)
            )
        flash("Школу додано.", "success")
        return redirect(url_for("schools"))

    return render_template("school_form.html", school=None)


@app.route("/schools/<int:school_id>/edit", methods=["GET", "POST"])
def school_edit(school_id):
    school = get_school(school_id)

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        address = request.form.get("address", "").strip()
        notes = request.form.get("notes", "").strip()

        if not name:
            flash("Введи назву школи.", "error")
            return render_template("school_form.html", school=school)

        with get_db() as db:
            db.execute("""
                UPDATE schools
                SET name = ?, address = ?, notes = ?
                WHERE id = ?
            """, (name, address, notes, school_id))

        flash("Дані школи оновлено.", "success")
        return redirect(url_for("schools"))

    return render_template("school_form.html", school=school)


@app.route("/schools/<int:school_id>/delete", methods=["POST"])
def school_delete(school_id):
    with get_db() as db:
        school = db.execute(
            "SELECT id, name FROM schools WHERE id = ?",
            (school_id,)
        ).fetchone()

        if school is None:
            flash("Школу не знайдено.", "error")
            return redirect(url_for("schools"))

        try:
            db.execute(
                "DELETE FROM schools WHERE id = ?",
                (school_id,)
            )
            flash(
                f"Школу «{school['name']}» видалено.",
                "success"
            )

        except Exception:
            flash(
                "Не вдалося видалити школу. "
                "Можливо, вона має пов'язані групи або записи.",
                "error"
            )

    return redirect(url_for("schools"))



# -------------------- GROUPS --------------------

@app.route("/groups")
def groups():
    with get_db() as db:
        items = db.execute("""
            SELECT g.*, s.name AS school_name,
                   COUNT(st.id) AS students_count
            FROM groups_table g
            JOIN schools s ON s.id = g.school_id
            LEFT JOIN students st ON st.group_id = g.id
            GROUP BY g.id
            ORDER BY s.name, g.name
        """).fetchall()

    return render_template("groups.html", groups=items)


@app.route("/groups/add", methods=["GET", "POST"])
def group_add():
    school_id = request.args.get("school_id", type=int)

    with get_db() as db:
        schools = db.execute("""
            SELECT id, name
            FROM schools
            ORDER BY name
        """).fetchall()

        if school_id is not None:
            exists = db.execute(
                "SELECT id FROM schools WHERE id = ?",
                (school_id,)
            ).fetchone()

            if exists is None:
                school_id = None

        if request.method == "POST":
            try:
                school_id = int(request.form.get("school_id", ""))
                name = request.form.get("name", "").strip()

                monthly_price = parse_price(
                    request.form.get("monthly_price", "0"),
                    "місячний абонемент"
                )
                annual_price = parse_price(
                    request.form.get("annual_price", "0"),
                    "річний внесок"
                )
                single_price = parse_price(
                    request.form.get("single_price", "0"),
                    "разове заняття"
                )

                school_exists = db.execute(
                    "SELECT id FROM schools WHERE id = ?",
                    (school_id,)
                ).fetchone()

                if school_exists is None:
                    raise ValueError("Обери школу зі списку.")

                if not name:
                    raise ValueError("Введи назву групи.")

            except (ValueError, TypeError) as error:
                flash(str(error) or "Перевір введені дані.", "error")

                return render_template(
                    "group_form.html",
                    schools=schools,
                    group=None,
                    selected_school_id=school_id
                )

            db.execute("""
                INSERT INTO groups_table (
                    school_id,
                    name,
                    monthly_price,
                    annual_price,
                    single_price
                )
                VALUES (?, ?, ?, ?, ?)
            """, (
                school_id,
                name,
                monthly_price,
                annual_price,
                single_price
            ))

            flash("Групу успішно додано.", "success")

            return redirect(
                url_for("school_detail", school_id=school_id)
            )

    return render_template(
        "group_form.html",
        schools=schools,
        group=None,
        selected_school_id=school_id
    )




@app.route("/groups/<int:group_id>/edit", methods=["GET", "POST"])
def group_edit(group_id):
    group = get_group(group_id)

    with get_db() as db:
        school_list = db.execute(
            "SELECT * FROM schools ORDER BY name"
        ).fetchall()

    if request.method == "POST":
        try:
            school_id = int(request.form.get("school_id", ""))
            name = request.form.get("name", "").strip()
            monthly = parse_price(
                request.form.get("monthly_price"), "місячний абонемент"
            )
            annual = parse_price(
                request.form.get("annual_price"), "річна оплата"
            )
            single = parse_price(
                request.form.get("single_price"), "разове заняття"
            )

            if not name:
                raise ValueError("Введи назву групи.")
            if not any(s["id"] == school_id for s in school_list):
                raise ValueError("Обери школу зі списку.")

        except (ValueError, TypeError) as error:
            flash(str(error) or "Перевір введені дані.", "error")
            return render_template(
                "group_form.html", group=group, schools=school_list
            )

        with get_db() as db:
            db.execute("""
                UPDATE groups_table
                SET school_id = ?, name = ?, monthly_price = ?,
                    annual_price = ?, single_price = ?
                WHERE id = ?
            """, (school_id, name, monthly, annual, single, group_id))

        flash("Групу оновлено.", "success")
        return redirect(url_for("groups"))

    return render_template(
        "group_form.html", group=group, schools=school_list
    )



@app.route("/groups/<int:group_id>/delete", methods=["POST"])
def group_delete(group_id):
    get_group(group_id)

    with get_db() as db:
        students_count = db.execute(
            "SELECT COUNT(*) FROM students WHERE group_id = ?",
            (group_id,)
        ).fetchone()[0]

        trainings_count = db.execute(
            "SELECT COUNT(*) FROM trainings WHERE group_id = ?",
            (group_id,)
        ).fetchone()[0]

        if students_count > 0 or trainings_count > 0:
            flash(
                "Не можна видалити групу, у якій є учні або тренування. "
                "Спочатку перенеси учнів та перевір історію тренувань.",
                "error"
            )
            return redirect(url_for("groups"))

        db.execute(
            "DELETE FROM groups_table WHERE id = ?",
            (group_id,)
        )

    flash("Групу видалено.", "success")
    return redirect(url_for("groups"))


# -------------------- STUDENTS --------------------





@app.route("/students/add", methods=["GET", "POST"])
def student_add():
    school_id = request.args.get("school_id", type=int)

    with get_db() as db:
        school = None

        if school_id is not None:
            school = db.execute(
                "SELECT * FROM schools WHERE id = ?",
                (school_id,)
            ).fetchone()

            if school is None:
                abort(404)

        query = """
            SELECT g.id, g.name, s.name AS school_name
            FROM groups_table g
            JOIN schools s ON s.id = g.school_id
        """
        params = []

        if school_id is not None:
            query += " WHERE s.id = ?"
            params.append(school_id)

        query += " ORDER BY s.name, g.name"
        group_list = db.execute(query, params).fetchall()

    if request.method == "POST":
        try:
            group_id = int(request.form.get("group_id", ""))
            full_name = request.form.get("full_name", "").strip()
            phone = request.form.get("parent_phone", "").strip()
            notes = request.form.get("notes", "").strip()

            valid_group = next(
                (g for g in group_list if g["id"] == group_id),
                None
            )

            if not full_name:
                raise ValueError("Введи ПІБ спортсмена.")

            if valid_group is None:
                raise ValueError(
                    "Обрана група не належить цій школі або не існує."
                )

        except (ValueError, TypeError) as error:
            flash(str(error) or "Перевір введені дані.", "error")
            return redirect(
                url_for("students", school_id=school_id)
            )

        with get_db() as db:
            db.execute("""
                INSERT INTO students
                    (group_id, full_name, parent_phone, notes)
                VALUES (?, ?, ?, ?)
            """, (group_id, full_name, phone, notes))

        flash("Спортсмена додано.", "success")
        return redirect(url_for("students", school_id=school_id))

    return render_template(
        "student_form.html",
        student=None,
        groups=group_list
    )



@app.route("/students/<int:student_id>/edit", methods=["GET", "POST"])
def student_edit(student_id):
    with get_db() as db:
        student = db.execute(
            "SELECT * FROM students WHERE id = ?", (student_id,)
        ).fetchone()
        group_list = db.execute("""
            SELECT g.id, g.name, s.name AS school_name
            FROM groups_table g
            JOIN schools s ON s.id = g.school_id
            ORDER BY s.name, g.name
        """).fetchall()

    if student is None:
        abort(404)

    if request.method == "POST":
        try:
            group_id = int(request.form.get("group_id", ""))
            full_name = request.form.get("full_name", "").strip()
            phone = request.form.get("parent_phone", "").strip()
            notes = request.form.get("notes", "").strip()

            if not full_name:
                raise ValueError("Введи ПІБ учня.")
            if not any(g["id"] == group_id for g in group_list):
                raise ValueError("Обери групу зі списку.")

        except (ValueError, TypeError) as error:
            flash(str(error) or "Перевір введені дані.", "error")
            return render_template(
                "student_form.html", student=student, groups=group_list
            )

        with get_db() as db:
            db.execute("""
                UPDATE students
                SET group_id = ?, full_name = ?, parent_phone = ?, notes = ?
                WHERE id = ?
            """, (group_id, full_name, phone, notes, student_id))

        flash("Дані учня оновлено.", "success")
        return redirect(url_for("students"))

    return render_template(
        "student_form.html", student=student, groups=group_list
    )


@app.route("/students/<int:student_id>/delete", methods=["POST"])
def student_delete(student_id):
    with get_db() as db:
        exists = db.execute(
            "SELECT id FROM students WHERE id = ?", (student_id,)
        ).fetchone()
        if exists is None:
            abort(404)
        db.execute("DELETE FROM students WHERE id = ?", (student_id,))

    flash("Учня та пов'язані записи видалено.", "success")
    return redirect(url_for("students"))


# -------------------- CALENDAR --------------------

@app.route("/calendar")
def calendar():
    today = date.today()
    school_id = request.args.get("school_id", type=int)

    try:
        year = int(request.args.get("year", today.year))
        month = int(request.args.get("month", today.month))

        if not 1 <= month <= 12 or not 1900 <= year <= 2100:
            raise ValueError
    except (ValueError, TypeError):
        year, month = today.year, today.month

    first_day = date(year, month, 1)

    next_month = (
        date(year + 1, 1, 1)
        if month == 12
        else date(year, month + 1, 1)
    )

    last_day = next_month - timedelta(days=1)

    view = request.args.get("view", "week")
    if view not in ("week", "month"):
        view = "week"

    if view == "month":
        start_date = first_day
        end_date = last_day
    else:
        selected_day = min(today.day, last_day.day)
        selected_date = date(year, month, selected_day)
        start_date = selected_date - timedelta(
            days=selected_date.weekday()
        )
        end_date = start_date + timedelta(days=6)

    with get_db() as db:
        school = None

        if school_id is not None:
            school = db.execute(
                "SELECT * FROM schools WHERE id = ?",
                (school_id,)
            ).fetchone()

            if school is None:
                abort(404)

        query = """
            SELECT
                t.*,
                g.name AS group_name,
                s.name AS school_name
            FROM trainings t
            JOIN groups_table g ON g.id = t.group_id
            JOIN schools s ON s.id = g.school_id
            WHERE t.training_date BETWEEN ? AND ?
        """
        params = [start_date.isoformat(), end_date.isoformat()]

        if school_id is not None:
            query += " AND s.id = ?"
            params.append(school_id)

        query += " ORDER BY t.training_date, t.start_time"
        trainings = db.execute(query, params).fetchall()

        group_query = """
            SELECT g.id, g.name, s.name AS school_name
            FROM groups_table g
            JOIN schools s ON s.id = g.school_id
        """
        group_params = []

        if school_id is not None:
            group_query += " WHERE s.id = ?"
            group_params.append(school_id)

        group_query += " ORDER BY s.name, g.name"
        groups = db.execute(group_query, group_params).fetchall()

    training_days = {}

    for training in trainings:
        training_days.setdefault(
            training["training_date"], []
        ).append(training)

    week_days = [
        start_date + timedelta(days=i)
        for i in range(7)
    ]

    return render_template(
        "calendar.html",
        trainings=trainings,
        training_days=training_days,
        week_days=week_days,
        groups=groups,
        view=view,
        year=year,
        month=month,
        first_day=first_day,
        last_day=last_day,
        today=today,
        prev_month=first_day - timedelta(days=1),
        next_month=next_month,
        school=school,
        school_id=school_id,
    )


@app.route("/trainings/add", methods=["POST"])
def training_add():
    school_id = request.form.get("school_id", type=int)

    try:
        group_id = int(request.form.get("group_id", ""))
        training_date = date.fromisoformat(
            request.form.get("training_date", "")
        )
        start_time = request.form.get("start_time", "").strip()
        end_time = request.form.get("end_time", "").strip()
        notes = request.form.get("notes", "").strip()

        if not start_time:
            raise ValueError("Вкажи час початку тренування.")

        if end_time and end_time <= start_time:
            raise ValueError(
                "Час завершення має бути пізніше початку."
            )

        with get_db() as db:
            query = """
                SELECT id, school_id
                FROM groups_table
                WHERE id = ?
            """
            group = db.execute(query, (group_id,)).fetchone()

            if group is None:
                raise ValueError("Обери наявну групу.")

            if (
                school_id is not None
                and group["school_id"] != school_id
            ):
                raise ValueError(
                    "Обрана група не належить цій школі."
                )

            db.execute("""
                INSERT INTO trainings
                    (group_id, training_date, start_time, end_time, notes)
                VALUES (?, ?, ?, ?, ?)
            """, (
                group_id,
                training_date.isoformat(),
                start_time,
                end_time,
                notes
            ))

    except (ValueError, TypeError) as error:
        flash(str(error) or "Перевір дані тренування.", "error")
    else:
        flash("Тренування додано.", "success")

    return redirect(url_for("calendar", school_id=school_id))




@app.route("/trainings/<int:training_id>/delete", methods=["POST"])
def training_delete(training_id):
    school_id = request.form.get("school_id", type=int)

    with get_db() as db:
        query = """
            SELECT t.id
            FROM trainings t
            JOIN groups_table g ON g.id = t.group_id
            WHERE t.id = ?
        """
        params = [training_id]

        if school_id is not None:
            query += " AND g.school_id = ?"
            params.append(school_id)

        training = db.execute(query, params).fetchone()

        if training is None:
            abort(404)

        db.execute(
            "DELETE FROM trainings WHERE id = ?",
            (training_id,)
        )

    flash("Тренування видалено.", "success")
    return redirect(url_for("calendar", school_id=school_id))



# -------------------- PAYMENTS --------------------

@app.route("/students/<int:student_id>/annual-fee", methods=["POST"])
def annual_fee_update(student_id):
    school_id = request.args.get("school_id", type=int)

    try:
        amount = parse_price(
            request.form.get("amount", "0"),
            "річний внесок"
        )
    except ValueError as error:
        flash(str(error), "error")
        return redirect(url_for("students", school_id=school_id))

    with get_db() as db:
        query = """
            SELECT st.id
            FROM students st
            JOIN groups_table g ON g.id = st.group_id
            WHERE st.id = ?
        """
        params = [student_id]

        if school_id is not None:
            query += " AND g.school_id = ?"
            params.append(school_id)

        student = db.execute(query, params).fetchone()

        if student is None:
            abort(404)

        db.execute("""
            INSERT INTO annual_fees
                (student_id, amount, is_paid, payment_date)
            VALUES (?, ?, 0, '')
            ON CONFLICT(student_id) DO UPDATE SET
                amount = excluded.amount
        """, (student_id, amount))

    flash("Суму річного внеску збережено.", "success")
    return redirect(url_for("students", school_id=school_id))



@app.route("/students/<int:student_id>/annual-fee/paid", methods=["POST"])
def annual_fee_mark_paid(student_id):
    school_id = request.args.get("school_id", type=int)

    with get_db() as db:
        query = """
            SELECT st.id
            FROM students st
            JOIN groups_table g ON g.id = st.group_id
            WHERE st.id = ?
        """
        params = [student_id]

        if school_id is not None:
            query += " AND g.school_id = ?"
            params.append(school_id)

        student = db.execute(query, params).fetchone()

        if student is None:
            abort(404)

        db.execute("""
            INSERT INTO annual_fees
                (student_id, amount, is_paid, payment_date)
            VALUES (?, 0, 1, ?)
            ON CONFLICT(student_id) DO UPDATE SET
                is_paid = 1,
                payment_date = excluded.payment_date
        """, (student_id, date.today().isoformat()))

    flash("Річний внесок позначено як оплачений.", "success")
    return redirect(url_for("students", school_id=school_id))


@app.route("/students")
def students():
    school_id = request.args.get("school_id", type=int)

    with get_db() as db:
        school = None

        if school_id is not None:
            school = db.execute(
                "SELECT * FROM schools WHERE id = ?",
                (school_id,)
            ).fetchone()

            if school is None:
                abort(404)

        query = """
            SELECT
                st.*,
                g.name AS group_name,
                s.name AS school_name,
                COALESCE(af.amount, 0) AS annual_fee_amount,
                COALESCE(af.is_paid, 0) AS annual_fee_paid,
                COALESCE(af.payment_date, '') AS annual_fee_date
            FROM students st
            JOIN groups_table g ON g.id = st.group_id
            JOIN schools s ON s.id = g.school_id
            LEFT JOIN annual_fees af ON af.student_id = st.id
        """

        params = []

        if school_id is not None:
            query += " WHERE s.id = ?"
            params.append(school_id)

        query += " ORDER BY st.full_name"

        items = db.execute(query, params).fetchall()

        group_query = """
            SELECT g.id, g.name, s.name AS school_name
            FROM groups_table g
            JOIN schools s ON s.id = g.school_id
        """

        group_params = []

        if school_id is not None:
            group_query += " WHERE s.id = ?"
            group_params.append(school_id)

        group_query += " ORDER BY s.name, g.name"

        group_list = db.execute(
            group_query, group_params
        ).fetchall()

    return render_template(
        "students.html",
        students=items,
        groups=group_list,
        school=school,
        school_id=school_id
    )


@app.route("/payments", methods=["GET", "POST"])
def payments():
    if request.method == "POST":
        try:
            student_id = int(request.form.get("student_id", ""))
            payment_type = request.form.get("payment_type", "")
            payment_date = date.fromisoformat(
                request.form.get("payment_date", "")
            )
            amount = parse_price(request.form.get("amount"), "сума оплати")
            covered_month = request.form.get("covered_month", "").strip()
            notes = request.form.get("notes", "").strip()

            if payment_type not in ("annual", "monthly", "single"):
                raise ValueError("Обери тип оплати.")
            if amount <= 0:
                raise ValueError("Сума оплати має бути більшою за нуль.")
            if payment_type == "monthly":
                try:
                    datetime.strptime(covered_month, "%Y-%m")
                except ValueError:
                    raise ValueError("Для абонемента обери місяць у форматі РРРР-ММ.")

            with get_db() as db:
                student = db.execute(
                    "SELECT id FROM students WHERE id = ?", (student_id,)
                ).fetchone()
                if student is None:
                    raise ValueError("Обери наявного учня.")

                db.execute("""
                    INSERT INTO payments
                    (student_id, payment_type, amount, payment_date,
                     covered_month, notes)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    student_id, payment_type, amount,
                    payment_date.isoformat(), covered_month, notes
                ))

        except (ValueError, TypeError) as error:
            flash(str(error) or "Перевір дані оплати.", "error")
        else:
            flash("Оплату збережено.", "success")

        return redirect(url_for("payments"))

    with get_db() as db:
        student_list = db.execute("""
            SELECT st.id, st.full_name, g.name AS group_name,
                   s.name AS school_name,
                   g.monthly_price, g.annual_price, g.single_price
            FROM students st
            JOIN groups_table g ON g.id = st.group_id
            JOIN schools s ON s.id = g.school_id
            ORDER BY st.full_name
        """).fetchall()

        payment_list = db.execute("""
            SELECT p.*, st.full_name, g.name AS group_name,
                   s.name AS school_name
            FROM payments p
            JOIN students st ON st.id = p.student_id
            JOIN groups_table g ON g.id = st.group_id
            JOIN schools s ON s.id = g.school_id
            ORDER BY p.payment_date DESC, p.id DESC
        """).fetchall()

        total = db.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM payments"
        ).fetchone()[0]

        month_total = db.execute("""
            SELECT COALESCE(SUM(amount), 0) FROM payments
            WHERE substr(payment_date, 1, 7) = ?
        """, (date.today().strftime("%Y-%m"),)).fetchone()[0]

    return render_template(
        "payments.html",
        students=student_list,
        payments=payment_list,
        total=total,
        month_total=month_total,
        today=date.today().isoformat(),
        current_month=date.today().strftime("%Y-%m"),
    )


# -------------------- START --------------------

init_db()

if __name__ == "__main__":
    app.run(debug=True)
