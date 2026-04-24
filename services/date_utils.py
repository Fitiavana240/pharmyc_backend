# services/date_utils.py — Pharmy-C v4.3
# ===================================================
# Helpers pour créer des datetimes UTC avec timezone
# Compatible Python 3.9+
# ===================================================

from datetime import datetime, timezone


def utcnow() -> datetime:
    """
    Remplace datetime.utcnow() (déprécié Python 3.12+).
    Retourne un datetime UTC timezone-aware.
    → Sérialisé par Pydantic/FastAPI avec '+00:00'
    → JavaScript reconnaît l'UTC et convertit en heure locale ✅
    """
    return datetime.now(timezone.utc)


def to_iso_utc(dt: datetime | None) -> str | None:
    """
    Convertit un datetime en string ISO 8601 avec 'Z' (UTC).
    Utiliser dans les _format_xxx() des routers à la place de .isoformat()

    Avant : msg.created_at.isoformat()  → "2026-03-30T11:48:00"   ❌ JS croit heure locale
    Après : to_iso_utc(msg.created_at)  → "2026-03-30T11:48:00Z"  ✅ JS convertit en UTC+3
    """
    if dt is None:
        return None
    # Si pas de timezone, supposer UTC (cas des anciens enregistrements en BDD)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    # Convertir en UTC puis formater avec Z
    utc = dt.astimezone(timezone.utc)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def parse_utc(dt: datetime | None) -> datetime | None:
    """
    S'assure qu'un datetime est timezone-aware (UTC).
    Utile pour comparer des dates venant de la BDD.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)