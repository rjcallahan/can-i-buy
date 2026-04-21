# staff.py
import csv
import io
import os
import sqlite3
from database import get_db, DB_PATH

# ── Valid roles ──────────────────────────────────────────────
VALID_ROLES = [
    "procurement_manager",
    "department_director",
    "city_manager",
    "acm",
    "hr",
    "it_director",
    "city_engineer",
    "city_attorney",
    "special_program",
    "city_council",
    "requester",
]

# ── Role display names ───────────────────────────────────────
ROLE_LABELS = {
    "procurement_manager": "Procurement Manager",
    "department_director": "Department Director",
    "city_manager":        "City Manager",
    "acm":                 "Asst. / Deputy City Manager",
    "hr":                  "Human Resources",
    "it_director":         "IT Director",
    "city_engineer":       "City Engineer",
    "city_attorney":       "City Attorney",
    "special_program":     "Special Program Compliance",
    "city_council":        "City Council",
    "requester":           "Requester",
}

# ── Stage → role mapping ─────────────────────────────────────
# Used to find the right staff member for each workflow stage
STAGE_ROLE_MAP = {
    "procurement_review": "procurement_manager",
    "hr_union_review":    "hr",
    "it_review":          "it_director",
    "engineering_review": "city_engineer",
    "attorney_review":    "city_attorney",
    "spc_review":         "special_program",
    "director_approval":  "department_director",
    "acm_approval":       "acm",
    "manager_approval":   "city_manager",
    "council_approval":   "city_council",
}


def import_from_csv(csv_content: str) -> dict:
    """
    Import staff from CSV content string.
    Expected columns (case-insensitive, flexible order):
      name, title, department, email, phone, role

    Role must be one of VALID_ROLES. If blank or unrecognized,
    defaults to 'requester'.

    Returns dict with counts of inserted, updated, skipped, errors.
    """
    results = {
        "inserted": 0,
        "updated":  0,
        "skipped":  0,
        "errors":   [],
    }

    try:
        reader = csv.DictReader(io.StringIO(csv_content))
    except Exception as e:
        results["errors"].append(f"Could not parse CSV: {e}")
        return results

    # Normalize headers — strip whitespace, lowercase
    if not reader.fieldnames:
        results["errors"].append("CSV has no headers.")
        return results

    conn = get_db()

    for i, row in enumerate(reader, start=2):  # start=2 (row 1 is header)
        # Normalize keys
        clean = {k.strip().lower(): (v.strip() if v else "")
                 for k, v in row.items()}

        name = clean.get("name", "").strip()
        if not name:
            results["errors"].append(f"Row {i}: missing name — skipped")
            results["skipped"] += 1
            continue

        title      = clean.get("title", "")
        department = clean.get("department", "")
        email      = clean.get("email", "").lower()
        phone      = clean.get("phone", "")
        role       = clean.get("role", "requester").lower().strip()

        # Validate role
        if role not in VALID_ROLES:
            results["errors"].append(
                f"Row {i}: '{role}' is not a valid role for '{name}' "
                f"— defaulting to 'requester'"
            )
            role = "requester"

        # Upsert — match on email if provided, otherwise name+department
        try:
            if email:
                existing = conn.execute(
                    "SELECT id FROM staff WHERE email=?",
                    (email,)).fetchone()
            else:
                existing = conn.execute(
                    "SELECT id FROM staff WHERE name=? AND department=?",
                    (name, department)).fetchone()

            if existing:
                conn.execute("""
                    UPDATE staff
                    SET name=?, title=?, department=?,
                        email=?, phone=?, role=?, active=1
                    WHERE id=?
                """, (name, title, department, email,
                      phone, role, existing["id"]))
                results["updated"] += 1
            else:
                conn.execute("""
                    INSERT INTO staff
                        (name, title, department, email, phone, role)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (name, title, department, email, phone, role))
                results["inserted"] += 1

        except Exception as e:
            results["errors"].append(f"Row {i} ('{name}'): {e}")
            results["skipped"] += 1

    conn.commit()
    conn.close()
    return results

def auto_register_requester(name: str, email: str,
                             department: str,
                             phone: str | None = None) -> bool:
    """
    Add a requester to the staff table if they don't already exist.
    Does nothing if email already registered.
    """
    if not email or "@" not in email:
        return False
    conn = get_db()
    existing = conn.execute(
        "SELECT id FROM staff WHERE LOWER(email)=?",
        (email.lower().strip(),)).fetchone()
    if existing:
        conn.close()
        return False
    conn.execute("""
        INSERT INTO staff (name, email, department, phone, role, active)
        VALUES (?, ?, ?, ?, 'requester', 1)
    """, (name, email.lower().strip(), department, phone))
    conn.commit()
    conn.close()
    return True 

def get_staff_for_stage(stage_code: str,
                        department: str = None) -> list:
    """
    Return staff members appropriate for a given workflow stage.
    For director_approval, filter by department if provided.
    """
    role = STAGE_ROLE_MAP.get(stage_code)
    if not role:
        return []

    conn = get_db()

    if stage_code == "director_approval" and department:
        # Try to find a director for the specific department first
        rows = conn.execute("""
            SELECT * FROM staff
            WHERE role='department_director'
              AND department=?
              AND active=1
            ORDER BY name
        """, (department,)).fetchall()

        # Fall back to any director if department-specific not found
        if not rows:
            rows = conn.execute("""
                SELECT * FROM staff
                WHERE role='department_director'
                  AND active=1
                ORDER BY name
            """).fetchall()
    else:
        rows = conn.execute("""
            SELECT * FROM staff
            WHERE role=? AND active=1
            ORDER BY name
        """, (role,)).fetchall()

    conn.close()
    return [dict(r) for r in rows]


def get_all_staff() -> list:
    """Return all active staff ordered by role then name."""
    conn = get_db()
    rows = conn.execute("""
        SELECT s.*,
               CASE s.role
                 WHEN 'procurement_manager' THEN 1
                 WHEN 'city_manager'        THEN 2
                 WHEN 'acm'                 THEN 3
                 WHEN 'department_director' THEN 4
                 WHEN 'hr'                  THEN 5
                 WHEN 'it_director'         THEN 6
                 WHEN 'city_engineer'       THEN 7
                 WHEN 'city_attorney'       THEN 8
                 WHEN 'special_program'     THEN 9
                 WHEN 'city_council'        THEN 10
                 ELSE 11
               END AS role_order
        FROM staff s
        WHERE s.active = 1
        ORDER BY role_order, name
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def deactivate_staff(staff_id: int) -> bool:
    """Soft-delete a staff member."""
    conn = get_db()
    conn.execute("UPDATE staff SET active=0 WHERE id=?", (staff_id,))
    conn.commit()
    conn.close()
    return True


def get_csv_template() -> str:
    """Return a CSV template string for download."""
    lines = [
        "name,title,department,email,phone,role",
        "Jane Smith,Procurement and Contracting Director,Procurement,"
        "jsmith@palmspringsca.gov,(760) 323-8100,procurement_manager",
        "John Doe,Director of Human Resources,Human Resources,"
        "jdoe@palmspringsca.gov,(760) 323-8101,hr",
        "Mary Johnson,IT Director,Information Technology,"
        "mjohnson@palmspringsca.gov,(760) 323-8102,it_director",
        "Bob Williams,City Engineer,Development Services — Engineering Services,"
        "bwilliams@palmspringsca.gov,(760) 323-8103,city_engineer",
        "Alice Brown,City Attorney,City Manager,"
        "abrown@palmspringsca.gov,(760) 323-8104,city_attorney",
        "Carlos Rivera,Director of Finance & Treasurer,Finance & Treasury,"
        "crivera@palmspringsca.gov,(760) 323-8105,department_director",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    # Test with sample data
    sample_csv = get_csv_template()
    print("Testing CSV import with sample data...\n")
    print(sample_csv)
    print()

    from database import init_db
    init_db()

    result = import_from_csv(sample_csv)
    print(f"Import results: {result}")

    print("\nAll staff:")
    for s in get_all_staff():
        print(f"  {s['name']:25} {s['role']:22} {s['department']}")

    print("\nStaff for procurement_review stage:")
    for s in get_staff_for_stage("procurement_review"):
        print(f"  {s['name']} — {s['email']}")