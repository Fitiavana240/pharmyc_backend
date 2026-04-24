# utils/date_utils.py
# ============================================================
# Utilitaires de validation et conversion de dates
# Utilisés dans tous les routeurs pour garantir un format
# homogène et éviter les erreurs de parsing.
# ============================================================

from datetime import date, datetime
from typing import Optional, Union
from dateutil import parser
from pydantic import ValidationError

# Format de date standard utilisé dans l'API
DATE_FORMAT = "%Y-%m-%d"
DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"

def parse_date(value: Union[str, date, datetime, None]) -> Optional[date]:
    """
    Convertit une chaîne ou un objet datetime en date.
    Lève une ValueError si la chaîne n'est pas valide.
    """
    if value is None:
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        try:
            # Tente d'abord le format ISO %Y-%m-%d
            return datetime.strptime(value, DATE_FORMAT).date()
        except ValueError:
            try:
                # Sinon utilise dateutil (supporte "2025-01-15T10:30:00", "15/01/2025", etc.)
                dt = parser.parse(value)
                return dt.date()
            except Exception as e:
                raise ValueError(f"Format de date invalide : '{value}'. Utilisez YYYY-MM-DD.") from e
    raise ValueError(f"Type non supporté pour une date : {type(value)}")

def parse_datetime(value: Union[str, datetime, None]) -> Optional[datetime]:
    """Convertit une chaîne en datetime. Lève ValueError en cas d'échec."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.strptime(value, DATETIME_FORMAT)
        except ValueError:
            try:
                return parser.parse(value)
            except Exception as e:
                raise ValueError(f"Format datetime invalide : '{value}'. Utilisez YYYY-MM-DD HH:MM:SS.") from e
    raise ValueError(f"Type non supporté pour datetime : {type(value)}")

def format_date(d: Optional[date]) -> Optional[str]:
    """Convertit une date en chaîne ISO YYYY-MM-DD."""
    return d.strftime(DATE_FORMAT) if d else None

def format_datetime(dt: Optional[datetime]) -> Optional[str]:
    """Convertit un datetime en chaîne YYYY-MM-DD HH:MM:SS."""
    return dt.strftime(DATETIME_FORMAT) if dt else None

def validate_date_range(start: Optional[date], end: Optional[date]) -> None:
    """
    Vérifie que start <= end. Lève ValueError si ce n'est pas le cas.
    """
    if start and end and start > end:
        raise ValueError("La date de début doit être antérieure ou égale à la date de fin")

def is_expired(expiration_date: date, reference_date: Optional[date] = None) -> bool:
    """Retourne True si expiration_date est dans le passé par rapport à reference_date (aujourd'hui par défaut)."""
    ref = reference_date or date.today()
    return expiration_date < ref

def days_until(date_val: date, reference_date: Optional[date] = None) -> int:
    """Nombre de jours entre aujourd'hui et date_val (peut être négatif)."""
    ref = reference_date or date.today()
    return (date_val - ref).days

def is_valid_future_date(date_val: date, allow_today: bool = True) -> bool:
    """Vérifie si la date est dans le futur (ou aujourd'hui si allow_today=True)."""
    today = date.today()
    return date_val >= today if allow_today else date_val > today

def get_current_datetime_utc() -> datetime:
    """Retourne le datetime UTC actuel."""
    return datetime.utcnow()

def get_current_date() -> date:
    """Retourne la date UTC actuelle."""
    return datetime.utcnow().date()