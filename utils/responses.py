# utils/responses.py — Pharmy-C v4.2


from typing import Any, List, Optional
from fastapi.responses import JSONResponse


def success(
    message: str = "Opération réussie",
    data: Any = None,
    code: int = 200,
    details: Optional[List[str]] = None,
) -> dict:
    """
    Réponse de succès standard.
    Retourne un dict (FastAPI le sérialise en JSON automatiquement).
    """
    return {
        "status":  "success",
        "code":    code,
        "message": message,
        "data":    data,
        "details": details,
    }


def warning(
    message: str,
    data: Any = None,
    code: int = 200,
    details: Optional[List[str]] = None,
) -> dict:
    """
    Réponse d'avertissement : opération réussie mais avec une mise en garde.
    Ex: vente créée mais stock faible, abonnement bientôt expiré, etc.
    Le code HTTP reste 200 car l'opération a réussi.
    """
    return {
        "status":  "warning",
        "code":    code,
        "message": message,
        "data":    data,
        "details": details,
    }


def created(
    message: str = "Créé avec succès",
    data: Any = None,
    details: Optional[List[str]] = None,
) -> JSONResponse:
    """
    Réponse 201 Created.
    Retourne JSONResponse pour forcer le status_code 201.
    """
    return JSONResponse(
        status_code=201,
        content={
            "status":  "success",
            "code":    201,
            "message": message,
            "data":    data,
            "details": details,
        },
    )


def no_content() -> JSONResponse:
    """Réponse 204 No Content (suppression réussie)."""
    return JSONResponse(status_code=204, content=None)


# ─── Shortcuts pour les erreurs les plus communes ────────────
# NOTE : en général, préférer raise HTTPException() directement
# dans les routers. Ces helpers existent pour les cas où on
# veut retourner (et non lever) une erreur.

def not_found(message: str = "Ressource introuvable") -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={"status": "warning", "code": 404, "message": message, "data": None, "details": None},
    )


def forbidden(message: str = "Accès refusé") -> JSONResponse:
    return JSONResponse(
        status_code=403,
        content={"status": "warning", "code": 403, "message": message, "data": None, "details": None},
    )


def conflict(message: str = "Conflit — ressource déjà existante") -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content={"status": "warning", "code": 409, "message": message, "data": None, "details": None},
    )


def bad_request(
    message: str = "Requête invalide",
    details: Optional[List[str]] = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={"status": "warning", "code": 400, "message": message, "data": None, "details": details},
    )


def server_error(message: str = "Erreur interne du serveur") -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content={"status": "error", "code": 500, "message": message, "data": None, "details": None},
    )