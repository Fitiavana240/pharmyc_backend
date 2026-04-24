# utils/exceptions.py
from fastapi import HTTPException, status
from typing import Optional

class AppException(HTTPException):
    """Exception de base pour l'application."""
    def __init__(self, status_code: int, detail: str, headers: Optional[dict] = None):
        super().__init__(status_code=status_code, detail=detail, headers=headers)

class NotFoundError(AppException):
    def __init__(self, detail: str = "Ressource non trouvée"):
        super().__init__(status_code=status.HTTP_404_NOT_FOUND, detail=detail)

class BadRequestError(AppException):
    def __init__(self, detail: str = "Requête invalide"):
        super().__init__(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)

class UnauthorizedError(AppException):
    def __init__(self, detail: str = "Non authentifié"):
        super().__init__(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)

class ForbiddenError(AppException):
    def __init__(self, detail: str = "Accès interdit"):
        super().__init__(status_code=status.HTTP_403_FORBIDDEN, detail=detail)

class ConflictError(AppException):
    def __init__(self, detail: str = "Conflit"):
        super().__init__(status_code=status.HTTP_409_CONFLICT, detail=detail)

class ValidationError(AppException):
    def __init__(self, detail: str = "Erreur de validation", errors: list = None):
        super().__init__(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detail)
        self.errors = errors