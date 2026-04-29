import json
import logging
import sqlite3
from functools import wraps
from pathlib import Path

logger = logging.getLogger("ftw_tools")

# ── Database ───────────────────────────────────────────────────────────────────

DB_PATH = Path(__file__).parent.parent / "data" / "health_records.db"


def _get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _query(person_id: str, record_type: str) -> list[dict]:
    with _get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM health_records "
            "WHERE person_id = ? AND record_type = ? "
            "ORDER BY record_date",
            (person_id, record_type),
        ).fetchall()
    return [dict(r) for r in rows]


# ── Consent types ──────────────────────────────────────────────────────────────

CONSENT_TYPES = [
    "gp_appointment",
    "investigation",
    "diagnosis",
    "medication",
    "sick_leave",
]

CONSENT_LABELS = {
    "gp_appointment": "GP appointment history",
    "investigation":  "Medical investigations and test results",
    "diagnosis":      "Diagnoses and medical conditions",
    "medication":     "Current and past medications",
    "sick_leave":     "Sick leave history",
}


# Implement a registry that keeps track of the consent status for each type of data
class ConsentRegistry:
    def __init__(self, consent_types):
        """Initialize the consent registry with the specified consent types."""
        self.consent_status = {data_type: False for data_type in consent_types}

    def grant(self, data_type):
        """Grant consent status for a specific data type."""
        if data_type not in self.consent_status:
            raise ValueError(f"Data type '{data_type}' is not recognized.")
        self.consent_status[data_type] = True
        logger.info("Consent granted: %s", data_type)

    def revoke(self, data_type):
        """Revoke consent status for a specific data type."""
        if data_type not in self.consent_status:
            raise ValueError(f"Data type '{data_type}' is not recognized.")
        self.consent_status[data_type] = False
        logger.info("Consent revoked: %s", data_type)

    def has_consent(self, data_type):
        """Check if consent has been given for a specific data type."""
        try:
            return self.consent_status[data_type]
        except KeyError:
            raise ValueError(f"Data type '{data_type}' is not recognized.")

    @property
    def consent_types(self):
        """Return a list of all consent types."""
        return list(self.consent_status.keys())

    @property
    def all_consents(self):
        """Return a dictionary of all consent statuses."""
        return self.consent_status


# Create decorators for checking consent status before executing a function
def requires_consent(data_type):
    """Decorator to check if consent has been given for a specific data type before executing a function."""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # When called as a class method, args[0] is self and carries its own registry
            registry = getattr(args[0], "consent_registry", None) if args else None
            if registry is None or not registry.has_consent(data_type):
                label = CONSENT_LABELS.get(data_type, data_type)
                logger.warning(
                    "Tool %s blocked — consent not granted for '%s'",
                    func.__name__, data_type,
                )
                return {
                    "error": "consent_not_granted",
                    "data_type": data_type,
                    "message": (
                        f"Access to '{label}' has not been consented to. "
                        "You must ask the user for consent before retrying."
                    ),
                }
            logger.info("Tool %s called: args=%s kwargs=%s", func.__name__, args[1:], kwargs)
            return func(*args, **kwargs)
        return wrapper
    return decorator


# ------ Tools ------

# Maps each data type to (output_key, {output_field: db_column})
# The four DB columns are always: record_date, description, outcome_or_status, additional_notes
_RECORD_CONFIG: dict[str, tuple[str, dict[str, str]]] = {
    "gp_appointment": ("gp_appointments", {
        "date":    "record_date",
        "reason":  "description",
        "outcome": "outcome_or_status",
        "notes":   "additional_notes",
    }),
    "investigation": ("investigations", {
        "date":   "record_date",
        "type":   "description",
        "result": "outcome_or_status",
        "notes":  "additional_notes",
    }),
    "diagnosis": ("diagnoses", {
        "date_diagnosed": "record_date",
        "condition":      "description",
        "status":         "outcome_or_status",
        "notes":          "additional_notes",
    }),
    "medication": ("medications", {
        "start_date":      "record_date",
        "medication":      "description",
        "status_and_dose": "outcome_or_status",
        "indication":      "additional_notes",
    }),
    "sick_leave": ("sick_leave", {
        "start_date": "record_date",
        "reason":     "description",
        "duration":   "outcome_or_status",
        "notes":      "additional_notes",
    }),
}


class Tools:
    """Tools available to the agent for fetching health data.
    Each method requiring sensitive data is gated by the consent registry.
    """

    def __init__(self, consent_registry: ConsentRegistry):
        self.consent_registry = consent_registry

    # ── No-consent tools ───────────────────────────────────────────────────────

    def list_people(self) -> dict:
        """List everyone in the database. No consent required."""
        with _get_connection() as conn:
            rows = conn.execute(
                "SELECT DISTINCT person_id, person_name, job_title, department "
                "FROM health_records ORDER BY person_id"
            ).fetchall()
        return {"people": [dict(r) for r in rows], "count": len(rows)}

    def get_person_info(self, person_id: str) -> dict:
        """Return basic demographic and employment information. No consent required."""
        with _get_connection() as conn:
            row = conn.execute(
                "SELECT DISTINCT person_id, person_name, date_of_birth, job_title, department "
                "FROM health_records WHERE person_id = ? LIMIT 1",
                (person_id,),
            ).fetchone()
        if not row:
            return {"error": "not_found", "message": f"No records found for person_id '{person_id}'"}
        return dict(row)

    # ── Private helper ─────────────────────────────────────────────────────────

    def _fetch_and_map(self, data_type: str, person_id: str) -> dict:
        """Query the DB and map rows to the typed output schema. No consent check here —
        that is the decorator's responsibility on each public method."""
        output_key, field_map = _RECORD_CONFIG[data_type]
        records = _query(person_id, data_type)
        logger.info("Retrieved %d %s records for person_id=%s", len(records), data_type, person_id)
        return {
            "person_id": person_id,
            output_key:  [{out_f: r[db_f] for out_f, db_f in field_map.items()} for r in records],
            "count":     len(records),
        }

    # ── Public tool methods ────────────────────────────────────────────────────

    @requires_consent("gp_appointment")
    def get_gp_appointments(self, person_id: str) -> dict:
        """Return GP appointment history for a person."""
        return self._fetch_and_map("gp_appointment", person_id)

    @requires_consent("investigation")
    def get_investigations(self, person_id: str) -> dict:
        """Return medical investigations and test results for a person."""
        return self._fetch_and_map("investigation", person_id)

    @requires_consent("diagnosis")
    def get_diagnoses(self, person_id: str) -> dict:
        """Return diagnoses and medical conditions for a person."""
        return self._fetch_and_map("diagnosis", person_id)

    @requires_consent("medication")
    def get_medications(self, person_id: str) -> dict:
        """Return current and past medications for a person."""
        return self._fetch_and_map("medication", person_id)

    @requires_consent("sick_leave")
    def get_sick_leave(self, person_id: str) -> dict:
        """Return sick leave history for a person."""
        return self._fetch_and_map("sick_leave", person_id)
