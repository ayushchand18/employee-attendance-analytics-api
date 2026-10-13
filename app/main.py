"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""
import os
from datetime import datetime, date, time, timedelta, timezone
from typing import Literal, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, StrictInt
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError
from decimal import Decimal, ROUND_HALF_UP
from pymongo import ReturnDocument
load_dotenv()

client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"))
db = client[os.getenv("MONGO_DB", "attendance_db")]

IST = timezone(timedelta(hours=5, minutes=30))

app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")

@app.on_event("startup")
def create_indexes():
    db.employees.create_index(
        [("emp_code", 1)],
        unique=True,
        name="uq_employee_emp_code"
    )

    db.attendance_logs.create_index(
        [("emp_code", 1), ("date", 1)],
        unique=True,
        name="uq_attendance_emp_date"
    )

    db.attendance_logs.create_index(
        [("date", -1)],
        name="idx_attendance_date_desc"
    )

    db.employees.create_index(
        [("department", 1), ("joined_on", 1)],
        name="idx_employees_department_joined"
    )

    db.attendance_logs.create_index(
        [("date", 1), ("late_minutes", 1)],
        name="idx_attendance_date_late"
    )


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def compute_late_minutes(
    punch_in: datetime,
    shift_start: str,
    attendance_date: str,
) -> int:
    local_punch_in = punch_in.astimezone(IST)

    hour, minute = map(int, shift_start.split(":"))
    shift_day = date.fromisoformat(attendance_date)

    shift_start_dt = datetime.combine(
        shift_day,
        time(hour, minute),
        tzinfo=IST,
    )

    elapsed_seconds = int(
        (local_punch_in - shift_start_dt).total_seconds()
    )

    if elapsed_seconds <= 10 * 60:
        return 0

    return elapsed_seconds // 60


def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    seconds = Decimal(str((punch_out - punch_in).total_seconds()))
    hours = seconds / Decimal("3600")

    return float(
        hours.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    )


def compute_overtime(
    punch_out: datetime,
    shift_start: str,
    shift_end: str,
    attendance_date: str,
) -> int:
    shift_day = date.fromisoformat(attendance_date)

    start_hour, start_minute = map(int, shift_start.split(":"))
    end_hour, end_minute = map(int, shift_end.split(":"))

    start_minutes = start_hour * 60 + start_minute
    end_minutes = end_hour * 60 + end_minute

    shift_end_day = shift_day
    if end_minutes <= start_minutes:
        shift_end_day += timedelta(days=1)

    shift_end_dt = datetime.combine(
        shift_end_day,
        time(end_hour, end_minute),
        tzinfo=IST,
    )

    local_punch_out = punch_out.astimezone(IST)

    overtime_seconds = int(
        (local_punch_out - shift_end_dt).total_seconds()
    )

    if overtime_seconds < 30 * 60:
        return 0

    return overtime_seconds // 60


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #

import re
from datetime import date
from pydantic import BaseModel, ConfigDict, Field, field_validator


class EmployeeIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    emp_code: str = Field(pattern=r"^EMP\d{4,6}$")
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(
        min_length=1,
        max_length=120,
        pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
    )
    department: str = Field(min_length=1, max_length=50)
    shift_start: str = Field(
        default="09:30",
        pattern=r"^([01]\d|2[0-3]):[0-5]\d$"
    )
    shift_end: str = Field(
        default="18:30",
        pattern=r"^([01]\d|2[0-3]):[0-5]\d$"
    )
    joined_on: str

    @field_validator("joined_on")
    @classmethod
    def validate_joined_on(cls, value):
        try:
            date.fromisoformat(value)
        except ValueError:
            raise ValueError("joined_on must be a valid YYYY-MM-DD date")

        if len(value) != 10:
            raise ValueError("joined_on must use YYYY-MM-DD format")

        return value

    @field_validator("shift_end")
    @classmethod
    def validate_shift_end(cls, value, info):
        if info.data.get("shift_start") == value:
            raise ValueError("shift_start and shift_end must differ")
        return value



class PunchInIn(BaseModel):
    emp_code: str = Field(min_length=1)
    punched_at: Optional[
        StrictInt
    ] = Field(
        default=None,
        ge=100_000_000_000,
        le=4_102_444_800_000,
    )
    status: Literal["PRESENT", "WFH", "ON_DUTY"] = "PRESENT"

class PunchOutIn(BaseModel):
    emp_code: str = Field(min_length=1)
    punched_at: Optional[StrictInt] = Field(
        default=None,
        ge=100_000_000_000,
        le=4_102_444_800_000,
    )


class RegularizeRequest(BaseModel):
    status: Optional[
        Literal["PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"]
    ] = None

    punch_in: Optional[StrictInt] = Field(
        default=None,
        ge=100_000_000_000,
        le=4_102_444_800_000,
    )

    punch_out: Optional[StrictInt] = Field(
        default=None,
        ge=100_000_000_000,
        le=4_102_444_800_000,
    )

    reason: str = Field(min_length=5, max_length=200)
    regularized_by: str = Field(min_length=1, max_length=50)


@app.get("/health")
def health():
    try:
        db.command("ping")
        return {"status": "ok"}
    except Exception:
        raise HTTPException(
            status_code=503,
            detail="Database unavailable"
        )


@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    doc = body.model_dump()
    doc["created_at"] = datetime.now(timezone.utc)

    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(
            status_code=409,
            detail="emp_code already exists"
        )

    doc.pop("_id", None)
    return doc


from fastapi import Query

@app.get("/employees")
def list_employees(
    department: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    query = {}
    if department is not None:
        query["department"] = department

    skip = (page - 1) * page_size
    total = db.employees.count_documents(query)

    items = list(
        db.employees.find(query, {"_id": 0})
        .sort("emp_code", 1)
        .skip(skip)
        .limit(page_size)
    )

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }

@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one(
        {"emp_code": body.emp_code},
        {"_id": 0},
    )

    if emp is None:
        raise HTTPException(
            status_code=404,
            detail="Employee not found",
        )

    if body.punched_at is None:
        ts = datetime.now(timezone.utc).replace(microsecond=0)
    else:
        ts = datetime.fromtimestamp(
            body.punched_at / 1000,
            tz=timezone.utc,
        ).replace(microsecond=0)

    local_ts = ts.astimezone(IST)

    shift_start = emp["shift_start"]
    shift_end = emp["shift_end"]

    start_hour, start_minute = map(int, shift_start.split(":"))
    end_hour, end_minute = map(int, shift_end.split(":"))

    start_minutes = start_hour * 60 + start_minute
    end_minutes = end_hour * 60 + end_minute
    local_minutes = local_ts.hour * 60 + local_ts.minute

    is_overnight = end_minutes <= start_minutes

    if is_overnight and local_minutes < end_minutes:
        attendance_day = local_ts.date() - timedelta(days=1)
    else:
        attendance_day = local_ts.date()

    attendance_date = attendance_day.isoformat()

    late_minutes = compute_late_minutes(
        ts,
        shift_start,
        attendance_date,
    )

    doc = {
        "emp_code": body.emp_code,
        "date": attendance_date,
        "status": body.status,
        "punch_in": ts,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": late_minutes,
        "overtime_minutes": 0,
        "history": [],
    }

    try:
        db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(
            status_code=409,
            detail="Attendance record already exists for this employee and date",
        )

    return {
        "emp_code": doc["emp_code"],
        "date": doc["date"],
        "status": doc["status"],
        "punch_in": int(ts.timestamp() * 1000),
        "punch_out": None,
        "work_hours": None,
        "late_minutes": doc["late_minutes"],
        "overtime_minutes": doc["overtime_minutes"],
        "history": doc["history"],
    }
@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    record = db.attendance_logs.find_one(
        {
            "emp_code": body.emp_code,
            "punch_out": None,
        }
    )

    if record is None:
        existing = db.attendance_logs.find_one(
            {"emp_code": body.emp_code}
        )

        if existing is None:
            raise HTTPException(
                status_code=404,
                detail="Attendance record not found",
            )

        raise HTTPException(
            status_code=409,
            detail="Employee has already punched out",
        )

    if body.punched_at is None:
        ts = datetime.now(timezone.utc).replace(microsecond=0)
    else:
        ts = datetime.fromtimestamp(
            body.punched_at / 1000,
            tz=timezone.utc,
        ).replace(microsecond=0)

    punch_in_ts = record["punch_in"]

    if punch_in_ts.tzinfo is None:
        punch_in_ts = punch_in_ts.replace(tzinfo=timezone.utc)

    if ts <= punch_in_ts:
        raise HTTPException(
            status_code=422,
            detail="Punch-out must be after punch-in",
        )

    emp = db.employees.find_one({"emp_code": body.emp_code})


    overtime_minutes = compute_overtime(
        ts,
        emp["shift_start"],
        emp["shift_end"],
        record["date"],
    )

    work_hours = compute_work_hours(punch_in_ts, ts)
    half_day = work_hours < 4.5

    update = db.attendance_logs.update_one(
        {
            "_id": record["_id"],
            "punch_out": None,
        },
        {
            "$set": {
                "punch_out": ts,
                "work_hours": work_hours,
                "overtime_minutes": overtime_minutes,
                "half_day": half_day,
            }
        },
    )

    if update.modified_count == 0:
        raise HTTPException(
            status_code=409,
            detail="Attendance record was already updated",
        )

    return {
        "emp_code": record["emp_code"],
        "date": record["date"],
        "status": record["status"],
        "punch_in": int(punch_in_ts.timestamp() * 1000),
        "punch_out": int(ts.timestamp() * 1000),
        "work_hours": work_hours,
        "late_minutes": record["late_minutes"],
        "overtime_minutes": overtime_minutes,
        "half_day": half_day,
        "history": record.get("history", []),
    }


def datetime_from_ms(value):
    if value is None:
        return None

    return datetime.fromtimestamp(
        value / 1000,
        tz=timezone.utc,
    ).replace(microsecond=0)



def serialize_attendance(record):
    return {
        "emp_code": record["emp_code"],
        "date": record["date"],
        "status": record["status"],
        "punch_in": epoch_ms(record.get("punch_in")),
        "punch_out": epoch_ms(record.get("punch_out")),
        "work_hours": record.get("work_hours"),
        "late_minutes": record.get("late_minutes", 0),
        "overtime_minutes": record.get("overtime_minutes", 0),
        "half_day": record.get("half_day", False),
        "history": record.get("history", []),
    }




def epoch_ms(value):
    if value is None:
        return None

    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)

    return int(value.timestamp() * 1000)


@app.get("/attendance")

@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    q = {}

    if emp_code:
        q["emp_code"] = emp_code

    if date_from or date_to:
        date_filter = {}

        if date_from:
            try:
                parsed_from = date.fromisoformat(date_from)
            except ValueError:
                raise HTTPException(
                    status_code=422,
                    detail="date_from must use YYYY-MM-DD format",
                )

            if parsed_from.isoformat() != date_from:
                raise HTTPException(
                    status_code=422,
                    detail="date_from must use YYYY-MM-DD format",
                )

            date_filter["$gte"] = date_from

        if date_to:
            try:
                parsed_to = date.fromisoformat(date_to)
            except ValueError:
                raise HTTPException(
                    status_code=422,
                    detail="date_to must use YYYY-MM-DD format",
                )

            if parsed_to.isoformat() != date_to:
                raise HTTPException(
                    status_code=422,
                    detail="date_to must use YYYY-MM-DD format",
                )

            date_filter["$lte"] = date_to

        if date_from and date_to and date_from > date_to:
            raise HTTPException(
                status_code=422,
                detail="date_from must be on or before date_to",
            )

        q["date"] = date_filter

    if status:
        allowed_statuses = {
            "PRESENT",
            "WFH",
            "ON_DUTY",
            "ABSENT",
            "LEAVE",
        }

        if status not in allowed_statuses:
            raise HTTPException(
                status_code=422,
                detail="Invalid attendance status",
            )

        q["status"] = status

    total = db.attendance_logs.count_documents(q)

    cursor = (
        db.attendance_logs.find(q, {"_id": 0})
        .sort([("date", -1), ("emp_code", 1)])
        .skip((page - 1) * page_size)
        .limit(page_size)
    )

    items = list(cursor)

    for item in items:
        item["punch_in"] = epoch_ms(item.get("punch_in"))
        item["punch_out"] = epoch_ms(item.get("punch_out"))
        item["half_day"] = item.get("half_day", False)
        item["late_minutes"] = item.get("late_minutes", 0)
        item["overtime_minutes"] = item.get("overtime_minutes", 0)
        item["history"] = item.get("history", [])

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@app.patch("/attendance/{emp_code}/{date}")
def regularize_attendance(
    emp_code: str,
    date: str,
    body: RegularizeRequest,
):

    try:
        attendance_day = datetime.strptime(date, "%Y-%m-%d").date()
        if attendance_day.isoformat() != date:
            raise ValueError
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="date must be in YYYY-MM-DD format",
        )

    employee = db.employees.find_one({"emp_code": emp_code})
    if employee is None:
        raise HTTPException(status_code=404, detail="Employee not found")

    record = db.attendance_logs.find_one({
        "emp_code": emp_code,
        "date": date,
    })
    if record is None:
        raise HTTPException(status_code=404, detail="Attendance record not found")

    old_history = record.get("history", [])
    old_status = record["status"]

    old_in = record.get("punch_in")
    old_out = record.get("punch_out")

    # MongoDB returns naive datetimes by default.
    # Interpret stored timestamps as UTC.
    if old_in is not None and old_in.tzinfo is None:
        old_in = old_in.replace(tzinfo=timezone.utc)

    if old_out is not None and old_out.tzinfo is None:
        old_out = old_out.replace(tzinfo=timezone.utc)

    new_status = body.status if body.status is not None else old_status
    new_in = (
        datetime_from_ms(body.punch_in)
        if body.punch_in is not None
        else old_in
    )
    new_out = (
        datetime_from_ms(body.punch_out)
        if body.punch_out is not None
        else old_out
    )

    # ABSENT and LEAVE cannot be supplied with punch times.
    if new_status in {"ABSENT", "LEAVE"}:
        if body.punch_in is not None or body.punch_out is not None:
            raise HTTPException(
                status_code=422,
                detail="ABSENT and LEAVE cannot include punch times",
            )
        new_in = None
        new_out = None
    elif new_in is None:
        raise HTTPException(
            status_code=422,
            detail="Presence statuses require punch_in",
        )

    # A corrected punch-in must still belong to this attendance date.
    if new_in is not None:
        local_in = new_in.astimezone(IST)
        shift_start = employee["shift_start"]
        shift_end = employee["shift_end"]

        is_overnight = shift_end <= shift_start
        expected_day = local_in.date()

        if (
            is_overnight
            and local_in.strftime("%H:%M") < shift_end
        ):
            expected_day -= timedelta(days=1)

        if expected_day != attendance_day:
            raise HTTPException(
                status_code=422,
                detail="punch_in must belong to the record's attendance date",
            )

    if new_out is not None:
        if new_in is None:
            raise HTTPException(
                status_code=422,
                detail="punch_out requires punch_in",
            )

        duration = (new_out - new_in).total_seconds()
        if duration <= 0 or duration > 24 * 60 * 60:
            raise HTTPException(
                status_code=422,
                detail="punch_out must be after punch_in and within 24 hours",
            )

    # Recalculate the derived fields from the final times.
    if new_in is not None:
        late_minutes = compute_late_minutes(
            new_in,
            employee["shift_start"],
            date,
        )
    else:
        late_minutes = 0

    if new_out is not None:
        work_hours = compute_work_hours(new_in, new_out)
        overtime_minutes = compute_overtime(
            new_out,
            employee["shift_start"],
            employee["shift_end"],
            date,
        )
        half_day = work_hours < 4.50
    else:
        work_hours = None
        overtime_minutes = 0
        half_day = False

    old_derived = {
        "work_hours": record.get("work_hours"),
        "late_minutes": record.get("late_minutes", 0),
        "overtime_minutes": record.get("overtime_minutes", 0),
        "half_day": record.get("half_day", False),
    }

    new_values = {
        "status": new_status,
        "punch_in": new_in,
        "punch_out": new_out,
        "work_hours": work_hours,
        "late_minutes": late_minutes,
        "overtime_minutes": overtime_minutes,
        "half_day": half_day,
    }

    old_values = {
        "status": old_status,
        "punch_in": old_in,
        "punch_out": old_out,
        **old_derived,
    }

    changes = {}
    for field, new_value in new_values.items():
        old_value = old_values[field]

        if field in {"punch_in", "punch_out"}:
            old_value = epoch_ms(old_value)
            new_value = epoch_ms(new_value)

        if old_value != new_value:
            changes[field] = {
                "from": old_value,
                "to": new_value,
            }

    if not changes:
        raise HTTPException(
            status_code=422,
            detail="The request does not change the record",
        )

    history_entry = {
        "at": epoch_ms(datetime.now(timezone.utc).replace(microsecond=0)),
        "by": body.regularized_by,
        "reason": body.reason,
        "changes": changes,
    }

    update_filter = {
        "_id": record["_id"],
        "status": old_status,
        "punch_in": old_in,
        "punch_out": old_out,
    }

    # Compare the old history as part of the update to prevent lost edits.
    if "history" in record:
        update_filter["history"] = record["history"]
    else:
        update_filter["history"] = {"$exists": False}

    updated = db.attendance_logs.find_one_and_update(
        update_filter,
        {
            "$set": new_values,
            "$push": {"history": history_entry},
        },
        return_document=ReturnDocument.AFTER,
    )

    if updated is None:
        raise HTTPException(
            status_code=409,
            detail="Attendance record changed concurrently; retry the request",
        )

    return serialize_attendance(updated)


from calendar import monthrange
from decimal import Decimal, ROUND_HALF_UP
from fastapi import Query


def round_half_up(value, places=2):
    quantum = Decimal("1").scaleb(-places)
    return float(
        Decimal(str(value)).quantize(
            quantum,
            rounding=ROUND_HALF_UP,
        )
    )


@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(
    emp_code: str,
    month: str = Query(
        ...,
        pattern=r"^\d{4}-(0[1-9]|1[0-2])$",
    ),
):
    employee = db.employees.find_one(
        {"emp_code": emp_code},
        {"_id": 0, "emp_code": 1, "joined_on": 1},
    )

    if employee is None:
        raise HTTPException(
            status_code=404,
            detail="Employee not found",
        )

    year, month_number = map(int, month.split("-"))
    first_day = f"{month}-01"
    last_day = f"{month}-{monthrange(year, month_number)[1]:02d}"

    joined_on = date.fromisoformat(employee["joined_on"])
    period_start = max(
        joined_on,
        date.fromisoformat(first_day),
    )
    period_end = date.fromisoformat(last_day)

    working_days = 0
    if period_start <= period_end:
        current_day = period_start
        while current_day <= period_end:
            if current_day.weekday() < 5:
                working_days += 1
            current_day += timedelta(days=1)

    pipeline = [
        {
            "$match": {
                "emp_code": emp_code,
                "date": {
                    "$gte": first_day,
                    "$lte": last_day,
                },
            }
        },
        {
            "$group": {
                "_id": None,
                "present_days": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                    {
                                        "$in": [
                                            {"$dayOfWeek": {
                                                "$dateFromString": {
                                                    "dateString": "$date"
                                                }
                                            }},
                                            [2, 3, 4, 5, 6],
                                        ]
                                    },
                                ]
                            },
                            {
                                "$cond": [
                                    {"$eq": ["$half_day", True]},
                                    0.5,
                                    1,
                                ]
                            },
                            0,
                        ]
                    }
                },
                "leave_days": {
                    "$sum": {
                        "$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]
                    }
                },
                "late_count": {
                    "$sum": {
                        "$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]
                    }
                },
                "total_late_minutes": {"$sum": "$late_minutes"},
                "total_overtime_minutes": {"$sum": "$overtime_minutes"},
            }
        },
    ]

    results = list(db.attendance_logs.aggregate(pipeline))
    metrics = results[0] if results else {}

    present_days = round_half_up(metrics.get("present_days", 0))
    attendance_pct = (
        round_half_up((present_days / working_days) * 100)
        if working_days > 0
        else None
    )

    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": present_days,
        "leave_days": metrics.get("leave_days", 0),
        "late_count": metrics.get("late_count", 0),
        "total_late_minutes": metrics.get("total_late_minutes", 0),
        "total_overtime_minutes": metrics.get("total_overtime_minutes", 0),
        "attendance_pct": attendance_pct,
    }


@app.get("/analytics/departments/summary")
def department_summary(
    month: str = Query(
        ...,
        pattern=r"^\d{4}-(0[1-9]|1[0-2])$",
    ),
    department: Optional[str] = None,
):
    year, month_number = map(int, month.split("-"))
    first_day = f"{month}-01"
    last_day = f"{month}-{monthrange(year, month_number)[1]:02d}"

    employee_match = {"joined_on": {"$lte": last_day}}
    if department is not None:
        employee_match["department"] = department

    pipeline = [
        {"$match": employee_match},
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {"employee_code": "$emp_code"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$emp_code", "$$employee_code"]},
                                    {"$gte": ["$date", first_day]},
                                    {"$lte": ["$date", last_day]},
                                ]
                            }
                        }
                    }
                ],
                "as": "attendance",
            }
        },
        {
            "$unwind": {
                "path": "$attendance",
                "preserveNullAndEmptyArrays": True,
            }
        },
        {
            "$addFields": {
                "_has_attendance": {
                    "$ne": [
                        {"$ifNull": ["$attendance.date", None]},
                        None,
                    ]
                },
                "_is_present": {
                    "$in": [
                        "$attendance.status",
                        ["PRESENT", "WFH", "ON_DUTY"],
                    ]
                },
                "_is_weekday": {
                    "$cond": [
                        {
                            "$ne": [
                                {"$ifNull": ["$attendance.date", None]},
                                None,
                            ]
                        },
                        {
                            "$in": [
                                {
                                    "$dayOfWeek": {
                                        "$dateFromString": {
                                            "dateString": "$attendance.date"
                                        }
                                    }
                                },
                                [2, 3, 4, 5, 6],
                            ]
                        },
                        False,
                    ]
                },
                "_has_work_hours": {
                    "$ne": [
                        {"$ifNull": ["$attendance.work_hours", None]},
                        None,
                    ]
                },
            }
        },
        {
            "$group": {
                "_id": {
                    "emp_code": "$emp_code",
                    "department": "$department",
                },
                "headcount": {"$first": 1},
                "present_days": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    "$_has_attendance",
                                    "$_is_present",
                                    "$_is_weekday",
                                ]
                            },
                            {
                                "$cond": [
                                    {"$eq": ["$attendance.half_day", True]},
                                    0.5,
                                    1,
                                ]
                            },
                            0,
                        ]
                    }
                },
                "late_count": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    "$_has_attendance",
                                    {"$gt": ["$attendance.late_minutes", 0]},
                                ]
                            },
                            1,
                            0,
                        ]
                    }
                },
                "total_late_minutes": {
                    "$sum": {
                        "$cond": [
                            "$_has_attendance",
                            {"$ifNull": ["$attendance.late_minutes", 0]},
                            0,
                        ]
                    }
                },
                "leave_count": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    "$_has_attendance",
                                    {"$eq": ["$attendance.status", "LEAVE"]},
                                ]
                            },
                            1,
                            0,
                        ]
                    }
                },
                "on_duty_count": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    "$_has_attendance",
                                    {"$eq": ["$attendance.status", "ON_DUTY"]},
                                ]
                            },
                            1,
                            0,
                        ]
                    }
                },
                "work_hours_sum": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    "$_has_attendance",
                                    "$_is_present",
                                    "$_has_work_hours",
                                ]
                            },
                            "$attendance.work_hours",
                            0,
                        ]
                    }
                },
                "work_hours_count": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    "$_has_attendance",
                                    "$_is_present",
                                    "$_has_work_hours",
                                ]
                            },
                            1,
                            0,
                        ]
                    }
                },
            }
        },
        {
            "$group": {
                "_id": "$_id.department",
                "headcount": {"$sum": "$headcount"},
                "present_days": {"$sum": "$present_days"},
                "late_count": {"$sum": "$late_count"},
                "total_late_minutes": {"$sum": "$total_late_minutes"},
                "leave_count": {"$sum": "$leave_count"},
                "on_duty_count": {"$sum": "$on_duty_count"},
                "work_hours_sum": {"$sum": "$work_hours_sum"},
                "work_hours_count": {"$sum": "$work_hours_count"},
            }
        },
        {"$sort": {"_id": 1}},
    ]

    results = list(db.employees.aggregate(pipeline))

    items = []
    for row in results:
        count = row["work_hours_count"]
        average = (
            round_half_up(row["work_hours_sum"] / count)
            if count
            else None
        )

        items.append({
            "department": row["_id"],
            "headcount": row["headcount"],
            "present_days": round_half_up(row["present_days"]),
            "avg_work_hours": average,
            "late_count": row["late_count"],
            "total_late_minutes": row["total_late_minutes"],
            "leave_count": row["leave_count"],
            "on_duty_count": row["on_duty_count"],
        })

    return {"month": month, "items": items}


@app.get("/analytics/leaderboard/late")
def late_leaderboard(
    month: str = Query(
        ...,
        pattern=r"^\d{4}-(0[1-9]|1[0-2])$",
    ),
    limit: int = Query(default=10, ge=1, le=50),
    department: Optional[str] = None,
):
    year, month_number = map(int, month.split("-"))
    first_day = f"{month}-01"
    last_day = f"{month}-{monthrange(year, month_number)[1]:02d}"

    pipeline = [
        {
            "$match": {
                "date": {"$gte": first_day, "$lte": last_day},
                "late_minutes": {"$gt": 0},
            }
        },
        {
            "$group": {
                "_id": "$emp_code",
                "total_late_minutes": {"$sum": "$late_minutes"},
                "late_count": {"$sum": 1},
            }
        },
        {
            "$lookup": {
                "from": "employees",
                "localField": "_id",
                "foreignField": "emp_code",
                "as": "employee",
            }
        },
        {"$unwind": "$employee"},
    ]

    if department is not None:
        pipeline.append({
            "$match": {"employee.department": department}
        })

    pipeline.extend([
        {
            "$setWindowFields": {
                "sortBy": {"total_late_minutes": -1},
                "output": {
                    "rank": {"$rank": {}}
                },
            }
        },
        {"$match": {"rank": {"$lte": limit}}},
        {
            "$sort": {
                "total_late_minutes": -1,
                "_id": 1,
            }
        },
        {
            "$project": {
                "_id": 0,
                "rank": 1,
                "emp_code": "$_id",
                "name": "$employee.name",
                "department": "$employee.department",
                "total_late_minutes": 1,
                "late_count": 1,
            }
        },
    ])

    items = list(db.attendance_logs.aggregate(pipeline))

    return {"month": month, "items": items}




@app.get("/analytics/departments/{department}/trend")
def department_trend(
    department: str,
    from_date: str = Query(..., alias="from"),
    to_date: str = Query(..., alias="to"),
):
    try:
        start_day = date.fromisoformat(from_date)
        end_day = date.fromisoformat(to_date)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="from and to must be YYYY-MM-DD dates",
        )

    if start_day.isoformat() != from_date or end_day.isoformat() != to_date:
        raise HTTPException(
            status_code=422,
            detail="from and to must use YYYY-MM-DD format",
        )

    if end_day < start_day:
        raise HTTPException(
            status_code=422,
            detail="'to' must be on or after 'from'",
        )

    if (end_day - start_day).days + 1 > 92:
        raise HTTPException(
            status_code=422,
            detail="Date range cannot exceed 92 days",
        )

    if not db.employees.find_one(
        {"department": department},
        {"_id": 1},
    ):
        raise HTTPException(
            status_code=404,
            detail="Department not found",
        )

    start_datetime = datetime.combine(
        start_day, time.min, tzinfo=timezone.utc
    )
    end_exclusive = datetime.combine(
        end_day + timedelta(days=1), time.min, tzinfo=timezone.utc
    )

    pipeline = [
        {"$documents": [{"day": start_datetime}]},
        {
            "$densify": {
                "field": "day",
                "range": {
                    "step": 1,
                    "unit": "day",
                    "bounds": [start_datetime, end_exclusive],
                },
            }
        },
        {
            "$set": {
                "date": {
                    "$dateToString": {
                        "format": "%Y-%m-%d",
                        "date": "$day",
                        "timezone": "UTC",
                    }
                },
                "is_working_day": {
                    "$in": [{"$dayOfWeek": "$day"}, [2, 3, 4, 5, 6]]
                },
            }
        },
        {
            "$lookup": {
                "from": "employees",
                "let": {"day_string": "$date"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$department", department]},
                                    {"$lte": ["$joined_on", "$$day_string"]},
                                ]
                            }
                        }
                    },
                    {"$project": {"_id": 0, "emp_code": 1}},
                ],
                "as": "eligible_employees",
            }
        },
        {
            "$set": {
                "headcount": {"$size": "$eligible_employees"},
                "_employee_codes": "$eligible_employees.emp_code",
            }
        },
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {
                    "day_string": "$date",
                    "employee_codes": "$_employee_codes",
                },
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$date", "$$day_string"]},
                                    {
                                        "$in": [
                                            "$emp_code",
                                            "$$employee_codes",
                                        ]
                                    },
                                ]
                            }
                        }
                    },
                    {
                        "$project": {
                            "_id": 0,
                            "status": 1,
                            "half_day": 1,
                            "late_minutes": 1,
                        }
                    },
                ],
                "as": "logs",
            }
        },
        {
            "$set": {
                "present_count": {
                    "$reduce": {
                        "input": "$logs",
                        "initialValue": 0,
                        "in": {
                            "$add": [
                                "$$value",
                                {
                                    "$cond": [
                                        {
                                            "$in": [
                                                "$$this.status",
                                                ["PRESENT", "WFH", "ON_DUTY"],
                                            ]
                                        },
                                        {
                                            "$cond": [
                                                {"$eq": ["$$this.half_day", True]},
                                                0.5,
                                                1,
                                            ]
                                        },
                                        0,
                                    ]
                                },
                            ]
                        },
                    }
                },
                "late_count": {
                    "$size": {
                        "$filter": {
                            "input": "$logs",
                            "as": "log",
                            "cond": {
                                "$gt": [
                                    {"$ifNull": ["$$log.late_minutes", 0]},
                                    0,
                                ]
                            },
                        }
                    }
                },
            }
        },
        {
            "$set": {
                "attendance_rate": {
                    "$cond": [
                        {
                            "$and": [
                                "$is_working_day",
                                {"$gt": ["$headcount", 0]},
                            ]
                        },
                        {"$divide": ["$present_count", "$headcount"]},
                        None,
                    ]
                }
            }
        },
        {
            "$setWindowFields": {
                "sortBy": {"day": 1},
                "output": {
                    "moving_avg_7d": {
                        "$avg": "$attendance_rate",
                        "window": {"documents": [-6, 0]},
                    }
                },
            }
        },
        {
            "$project": {
                "_id": 0,
                "date": 1,
                "is_working_day": 1,
                "headcount": 1,
                "present_count": {"$round": ["$present_count", 0]},
                "late_count": 1,
                "attendance_rate": {"$round": ["$attendance_rate", 4]},
                "moving_avg_7d": {
                    "$cond": [
                        {"$eq": [{"$type": "$moving_avg_7d"}, "missing"]},
                        None,
                        {"$round": ["$moving_avg_7d", 4]},
                    ]
                },
            }
        },
        {"$sort": {"date": 1}},
    ]

    result = db.command(
        "aggregate",
        1,
        pipeline=pipeline,
        cursor={},
    )

    items = result["cursor"]["firstBatch"]

    return {
        "department": department,
        "items": items,
    }


@app.get("/admin/explain/{endpoint}")
def explain_endpoint(endpoint: str):
    explain_queries = {
        "attendance_list": (
            db.attendance_logs,
            {"date": {"$gte": "2026-10-01", "$lte": "2026-10-31"}},
            [("date", -1)],
        ),
        "employee_monthly": (
            db.attendance_logs,
            {"emp_code": "EMP1003", "date": {"$gte": "2026-10-01", "$lte": "2026-10-31"}},
            [("emp_code", 1), ("date", 1)],
        ),
        "department_summary": (
            db.employees,
            {"joined_on": {"$lte": "2026-10-31"}},
            [("department", 1), ("joined_on", 1)],
        ),
        "late_leaderboard": (
            db.attendance_logs,
            {"date": {"$gte": "2026-10-01", "$lte": "2026-10-31"}, "late_minutes": {"$gt": 0}},
            [("date", 1), ("late_minutes", 1)],
        ),
        "department_trend": (
            db.employees,
            {"department": "Engineering", "joined_on": {"$lte": "2026-10-31"}},
            [("department", 1), ("joined_on", 1)],
        ),
    }

    if endpoint not in explain_queries:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Unsupported endpoint name",
                "supported": list(explain_queries.keys()),
            },
        )

    collection, query, sort_spec = explain_queries[endpoint]

    plan = collection.find(query).sort(sort_spec).explain()
    return {
        "endpoint": endpoint,
        "executionStats": plan.get("executionStats", {}),
        "queryPlanner": plan.get("queryPlanner", {}),
    }


