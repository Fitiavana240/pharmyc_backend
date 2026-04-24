# routers/auth.py — Pharmy-C v4.1 CORRIGÉ
# ============================================================
# CORRECTIONS APPLIQUÉES :
#   - FIX #6  : endpoint POST /auth/refresh (refresh token JWT)
#   - FIX #8  : rate limiting sur /login (5 tentatives/min via slowapi)
#   - FIX #7  : pas de credentials par défaut faibles
#   - AMÉLIOR : login retourne aussi telephone et nom pour le frontend
# ============================================================
from email.header import Header
import os
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from sqlalchemy.orm import joinedload

from database import get_db
from models.models import Utilisateur, Pharmacie
from utils.security import verify_password, create_access_token, decode_access_token

# ── Rate limiting (slowapi) ──────────────────────────────────────────────────
# pip install slowapi
try:
    from slowapi import Limiter
    from slowapi.util import get_remote_address
    _limiter = Limiter(key_func=get_remote_address)
    _rate_limit_available = True
except ImportError:
    _limiter = None
    _rate_limit_available = False
    print("⚠️  slowapi non installé — rate limiting désactivé. Installez-le : pip install slowapi")

router        = APIRouter()
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")

ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("TOKEN_EXPIRE_MINUTES", "14400"))


# ────────────────────────────────────────────────────────────────────────────
# Dépendances d'authentification
# ────────────────────────────────────────────────────────────────────────────

def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
    request: Request = None,   # ← ajouté
) -> Utilisateur:
    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token invalide ou expiré",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Vérifier si le token est blacklisté
    from models.models import TokenBlacklist
    blacklisted = db.query(TokenBlacklist).filter(TokenBlacklist.token == token).first()
    if blacklisted:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token révoqué, veuillez vous reconnecter",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Token malformé")

    # Charger la relation 'role' pour accéder à user.role.name
    user = db.query(Utilisateur).options(joinedload(Utilisateur.role)).filter(
        Utilisateur.id == int(user_id),
        Utilisateur.is_deleted == False,
    ).first()

    if not user:
        raise HTTPException(404, detail="Utilisateur introuvable")
    if not user.est_actif or not user.confirmation_email:
        raise HTTPException(403, detail="Compte inactif ou email non confirmé")
    # Vérification de l'abonnement pour les non‑admin
    if user.id_pharmacie and (not user.role or user.role.name != "admin"):
        # Routes exclues (nécessaires même en cas d'abonnement expiré)
        if request:
            path = request.url.path
            if path.startswith("/abonnements/") or path in ("/utilisateurs/me", "/pharmacies/me"):
                return user

        from routers.abonnements import verifier_acces_pharmacie
        if not verifier_acces_pharmacie(user.id_pharmacie, db):
            raise HTTPException(
                403,
                "Abonnement expiré ou suspendu. Veuillez renouveler votre abonnement pour continuer.",
                headers={"X-Abonnement-Expire": "owner" if user.role and user.role.name == "proprietaire" else "employee"}
            )
    return user

def admin_required(current_user: Utilisateur = Depends(get_current_user)):
    
    if not current_user.role or current_user.role.name != "admin":
        raise HTTPException(status_code=403, detail="Accès réservé à l'administrateur")
    return current_user


def owner_or_admin_required(pharmacie_id: int, db: Session, current_user: Utilisateur):
    """Vérifie si l'utilisateur est admin OU propriétaire de la pharmacie."""
    if current_user.role and current_user.role.name == "admin":
        return True
    if current_user.id_pharmacie == pharmacie_id:
        ph = db.query(Pharmacie).filter(Pharmacie.id == pharmacie_id).first()
        if ph and ph.owner_user_id == current_user.id:
            return True
    raise HTTPException(status_code=403, detail="Action non autorisée")


# ────────────────────────────────────────────────────────────────────────────
# POST /auth/login
# FIX #8 : rate limited à 5 requêtes/minute par IP
# ────────────────────────────────────────────────────────────────────────────

def _login_handler(form_data: OAuth2PasswordRequestForm, db: Session):
    """Logique de login séparée pour pouvoir l'appeler avec ou sans rate limit."""
    user = db.query(Utilisateur).filter(
        Utilisateur.email == form_data.username.strip().lower(),
        Utilisateur.is_deleted == False,
    ).first()

    if not user or not verify_password(form_data.password, user.mot_de_passe):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email ou mot de passe incorrect",
        )

    if not user.est_actif or not user.confirmation_email:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Compte inactif ou email non confirmé",
        )

    token = create_access_token(
        data={"sub": str(user.id)},
        expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    )

    return {
        "access_token":  token,
        "token_type":    "bearer",
        "user_id":       user.id,
        "email":         user.email,
        "nom":           user.nom,
        "role":          user.role.name if user.role else None,
        "pharmacie_id":  user.id_pharmacie,
        "expires_in":    ACCESS_TOKEN_EXPIRE_MINUTES * 60,  # secondes
    }


if _rate_limit_available:
    @router.post("/login")
    @_limiter.limit("5/minute")
    def login(
        request: Request,
        form_data: OAuth2PasswordRequestForm = Depends(),
        db: Session = Depends(get_db),
    ):
        """
        Authentification — limité à 5 tentatives/minute par IP.
        Retourne un JWT valable TOKEN_EXPIRE_MINUTES minutes.
        """
        return _login_handler(form_data, db)
else:
    @router.post("/login")
    def login(
        request: Request,
        form_data: OAuth2PasswordRequestForm = Depends(),
        db: Session = Depends(get_db),
    ):
        user = db.query(Utilisateur).filter(
            Utilisateur.email == form_data.username.strip().lower(),
            Utilisateur.is_deleted == False,
        ).first()
        if not user:
            print(f"Login failed: email {form_data.username} not found")
            raise HTTPException(401, "Email ou mot de passe incorrect")
        if not verify_password(form_data.password, user.mot_de_passe):
            print(f"Login failed: wrong password for {user.email}")
            raise HTTPException(401, "Email ou mot de passe incorrect")
        if not user.est_actif:
            print(f"Login failed: account inactive for {user.email}")
            raise HTTPException(403, "Compte inactif")
        if not user.confirmation_email:
            print(f"Login failed: email not confirmed for {user.email}")
            raise HTTPException(403, "Email non confirmé")
        return _login_handler(form_data, db)


# ────────────────────────────────────────────────────────────────────────────
# POST /auth/refresh
# FIX #6 : refresh token — renouvelle un JWT encore valide
# ────────────────────────────────────────────────────────────────────────────

# ─── Endpoint logout ─────────────────────────────────────────
@router.post("/logout")
def logout(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
):
    """
    Invalide le token JWT côté serveur.
    Le token est ajouté à la blacklist jusqu'à son expiration.
    """
    # Décoder pour connaître la date d'expiration
    payload = decode_access_token(token)
    exp = payload.get("exp") if payload else None

    if exp:
        expired_at = datetime.utcfromtimestamp(exp)
    else:
        from datetime import timedelta
        expired_at = datetime.utcnow() + timedelta(hours=1)

    # Vérifier si déjà blacklisté (évite doublon)
    from models.models import TokenBlacklist
    existing = db.query(TokenBlacklist).filter(
        TokenBlacklist.token == token
    ).first()

    if not existing:
        db.add(TokenBlacklist(token=token, expired_at=expired_at))
        db.commit()

    return {"message": "Déconnexion réussie"}
 
@router.post("/refresh")
def refresh_token(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
):
    """
    Rafraîchit un access token.
    Le token actuel doit encore être valide (non expiré).
    Retourne un nouveau token avec une nouvelle durée de validité.
    """
    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token invalide ou expiré — veuillez vous reconnecter",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Token malformé")

    user = db.query(Utilisateur).filter(
        Utilisateur.id == int(user_id),
        Utilisateur.is_deleted == False,
    ).first()

    if not user or not user.est_actif:
        raise HTTPException(status_code=401, detail="Compte inactif ou supprimé")

    new_token = create_access_token(
        data={"sub": str(user.id)},
        expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    )

    return {
        "access_token": new_token,
        "token_type":   "bearer",
        "expires_in":   ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    }


# ────────────────────────────────────────────────────────────────────────────
# GET /auth/me — identité de l'utilisateur connecté (utilitaire)
# ────────────────────────────────────────────────────────────────────────────

@router.get("/me")
def auth_me(current_user: Utilisateur = Depends(get_current_user)):
    """Retourne l'identité de l'utilisateur associé au token."""
    return {
        "id":          current_user.id,
        "email":       current_user.email,
        "nom":         current_user.nom,
        "role":        current_user.role.name if current_user.role else None,
        "pharmacie_id": current_user.id_pharmacie,
    }