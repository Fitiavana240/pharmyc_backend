# routers/utilisateurs.py — Pharmy-C v4.1 CORRIGÉ
# ============================================================
# CORRECTIONS APPLIQUÉES :
#   - FIX #5 : GET /{user_id} ajouté (évite getEmploye côté client)
#   - FIX #4 : PATCH /me/password séparé (changement de mot de passe propre)
#   - AMÉLIOR : DELETE /{user_id} avec vérification pharmacie
# ============================================================
import secrets
import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session, joinedload

from database import get_db
from models.models import Utilisateur, Role, Pharmacie
from utils.security import get_password_hash, create_access_token, verify_password
from utils.email_utils import envoi_email_code
from schemas import UserCreate, EmailConfirm, ResendCode, UserRead, UserUpdate
from routers.auth import get_current_user, admin_required, owner_or_admin_required
from services.historique_service import enregistrer_action
from schemas import ResetPasswordRequest, ResetPasswordConfirm

router = APIRouter()


def generate_confirmation_code() -> str:
    return str(secrets.randbelow(10**6)).zfill(6)


# ────────────────────────────────────────────────────────────────────────────
# POST / — Inscription
# ────────────────────────────────────────────────────────────────────────────

@router.post("/", status_code=status.HTTP_201_CREATED, response_model=UserRead)
def create_user(user: UserCreate, db: Session = Depends(get_db)):
    existing = db.query(Utilisateur).filter(
        Utilisateur.email == user.email.strip().lower(),
        Utilisateur.is_deleted == False,
    ).first()

    if existing:
        if not existing.est_actif and not existing.confirmation_email:
            new_code = generate_confirmation_code()
            envoi_email_code(user.email, new_code)
            existing.code_confirmation = new_code
            db.commit()
            return existing
        raise HTTPException(status_code=400, detail="Un compte actif existe déjà avec cet email")

    role_proprio = db.query(Role).filter(Role.name == "proprietaire").first()
    if not role_proprio:
        raise HTTPException(status_code=500, detail="Rôle 'proprietaire' non trouvé")

    hashed = get_password_hash(user.mot_de_passe)
    code   = generate_confirmation_code()

    new_user = Utilisateur(
        uuid               = str(uuid.uuid4()),
        nom                = user.nom,
        email              = user.email.strip().lower(),
        mot_de_passe       = hashed,
        telephone          = getattr(user, "telephone", None),
        id_role            = role_proprio.id,
        code_confirmation  = code,
        est_actif          = False,
        confirmation_email = False,
    )
    try:
        envoi_email_code(user.email, code)
        db.add(new_user)
        db.commit()
        db.refresh(new_user)
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Erreur inscription : {e}")

    enregistrer_action(
        db=db, utilisateur=new_user, action="CREATE",
        entity_type="utilisateur", entity_id=new_user.id,
        new_value={"id": new_user.id, "nom": new_user.nom, "email": new_user.email},
    )
    return new_user


# ────────────────────────────────────────────────────────────────────────────
# POST /confirm — Confirmation email
# ────────────────────────────────────────────────────────────────────────────

@router.post("/confirm")
def confirm_email(data: EmailConfirm, db: Session = Depends(get_db)):
    user = db.query(Utilisateur).filter(
        Utilisateur.email == data.email,
        Utilisateur.is_deleted == False,
    ).first()
    if not user:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable")
    if not user.code_confirmation or user.code_confirmation != data.code:
        raise HTTPException(status_code=400, detail="Code incorrect")

    user.confirmation_email = True
    user.est_actif          = True
    user.code_confirmation  = None
    db.commit()

    enregistrer_action(
        db=db, utilisateur=user, action="UPDATE",
        entity_type="utilisateur", entity_id=user.id,
        new_value={"est_actif": True, "confirmation_email": True},
    )

    token = create_access_token(data={"sub": str(user.id)}, expires_delta=timedelta(minutes=30))
    return {"message": "Email confirmé avec succès", "access_token": token, "token_type": "bearer"}


# ────────────────────────────────────────────────────────────────────────────
# POST /resend-code — Renvoyer le code de confirmation
# ────────────────────────────────────────────────────────────────────────────

@router.post("/resend-code")
def resend_code(payload: ResendCode, db: Session = Depends(get_db)):
    user = db.query(Utilisateur).filter(
        Utilisateur.email == payload.email,
        Utilisateur.is_deleted == False,
    ).first()
    if not user:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable")
    if user.est_actif:
        raise HTTPException(status_code=400, detail="Le compte est déjà actif")

    new_code = generate_confirmation_code()
    envoi_email_code(payload.email, new_code)
    user.code_confirmation = new_code
    db.commit()
    return {"message": "Nouveau code envoyé"}


# ────────────────────────────────────────────────────────────────────────────
# GET /me — Profil utilisateur connecté
# ────────────────────────────────────────────────────────────────────────────

@router.get("/me", response_model=UserRead)
def get_me(current_user: Utilisateur = Depends(get_current_user)):
    return {
        "id":                 current_user.id,
        "uuid":               current_user.uuid,
        "nom":                current_user.nom,
        "email":              current_user.email,
        "telephone":          current_user.telephone,
        "id_role":            current_user.id_role,
        "role_name":          current_user.role.name if current_user.role else None,
        "id_pharmacie":       current_user.id_pharmacie,
        "est_actif":          current_user.est_actif,
        "confirmation_email": current_user.confirmation_email,
    }


# ────────────────────────────────────────────────────────────────────────────
# PATCH /me — Modifier son profil
# ────────────────────────────────────────────────────────────────────────────

@router.patch("/me")
def update_me(
    payload: UserUpdate,
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user),
):
    old_data = {"nom": current_user.nom, "email": current_user.email, "telephone": current_user.telephone}
    data = payload.model_dump(exclude_unset=True)
    # Empêcher la modification du mot de passe via ce endpoint
    data.pop("mot_de_passe", None)
    for k, v in data.items():
        setattr(current_user, k, v)
    db.commit()
    db.refresh(current_user)
    enregistrer_action(
        db=db, utilisateur=current_user, action="UPDATE",
        entity_type="utilisateur", entity_id=current_user.id,
        old_value=old_data, new_value=data,
    )
    return current_user


# ────────────────────────────────────────────────────────────────────────────
# PATCH /me/password — Changer son mot de passe
# FIX #4 : endpoint dédié — vérifie l'ancien mot de passe
# ────────────────────────────────────────────────────────────────────────────

@router.patch("/me/password")
def change_password(
    payload: dict,
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user),
):
    ancien_mdp  = payload.get("ancien_mdp")
    nouveau_mdp = payload.get("nouveau_mdp")

    if not ancien_mdp or not nouveau_mdp:
        raise HTTPException(status_code=422, detail="ancien_mdp et nouveau_mdp sont requis")
    if not verify_password(ancien_mdp, current_user.mot_de_passe):
        raise HTTPException(status_code=400, detail="Ancien mot de passe incorrect")
    if len(nouveau_mdp) < 8:
        raise HTTPException(status_code=422, detail="Le nouveau mot de passe doit contenir au moins 8 caractères")

    current_user.mot_de_passe = get_password_hash(nouveau_mdp)
    db.commit()
    enregistrer_action(
        db=db, utilisateur=current_user, action="UPDATE",
        entity_type="utilisateur", entity_id=current_user.id,
        new_value={"action": "change_password"},
    )
    return {"message": "Mot de passe modifié avec succès"}


# ────────────────────────────────────────────────────────────────────────────
# GET /{user_id} — Détail d'un utilisateur
# FIX #5 : endpoint dédié — remplace le filtre côté client dans getEmploye()
# ────────────────────────────────────────────────────────────────────────────

@router.get("/{user_id}", response_model=UserRead)
def get_utilisateur(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user),
):
    """
    Retourne le détail d'un utilisateur.
    - Admin : peut voir n'importe quel utilisateur.
    - Propriétaire : peut voir uniquement les utilisateurs de sa pharmacie.
    """
    user = db.query(Utilisateur).options(joinedload(Utilisateur.role)).filter(
        Utilisateur.id == user_id,
        Utilisateur.is_deleted == False,
    ).first()

    if not user:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable")

    # Contrôle d'accès
    if current_user.role and current_user.role.name != "admin":
        if user.id_pharmacie != current_user.id_pharmacie:
            raise HTTPException(status_code=403, detail="Accès non autorisé")

    return {
        "id":                 user.id,
        "uuid":               user.uuid,
        "nom":                user.nom,
        "email":              user.email,
        "telephone":          user.telephone,
        "id_role":            user.id_role,
        "role_name":          user.role.name if user.role else None,
        "id_pharmacie":       user.id_pharmacie,
        "est_actif":          user.est_actif,
        "confirmation_email": user.confirmation_email,
    }


# ────────────────────────────────────────────────────────────────────────────
# PATCH /{user_id} — Modifier un utilisateur (propriétaire/admin)
# ────────────────────────────────────────────────────────────────────────────

@router.patch("/{user_id}", response_model=UserRead)
def update_user(
    user_id: int,
    payload: UserUpdate,
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user),
):
    user = db.query(Utilisateur).filter(
        Utilisateur.id == user_id,
        Utilisateur.is_deleted == False,
    ).first()
    if not user:
        raise HTTPException(404, "Utilisateur introuvable")

    if current_user.role.name != "admin":
        if not current_user.id_pharmacie or current_user.id_pharmacie != user.id_pharmacie:
            raise HTTPException(403, "Action non autorisée")
        if user.role and user.role.name == "proprietaire":
            raise HTTPException(403, "Impossible de modifier un autre propriétaire")

    if payload.email and payload.email != user.email:
        existing = db.query(Utilisateur).filter(
            Utilisateur.email == payload.email,
            Utilisateur.is_deleted == False,
        ).first()
        if existing:
            raise HTTPException(409, "Email déjà utilisé")

    old_values = {"nom": user.nom, "email": user.email, "est_actif": user.est_actif, "id_role": user.id_role}
    data = payload.model_dump(exclude_unset=True)
    if "mot_de_passe" in data:
        data["mot_de_passe"] = get_password_hash(data["mot_de_passe"])
    for k, v in data.items():
        setattr(user, k, v)

    db.commit()
    db.refresh(user)
    enregistrer_action(
        db=db, utilisateur=current_user, action="UPDATE",
        entity_type="utilisateur", entity_id=user.id,
        old_value=old_values, new_value=data,
    )
    return user


# ────────────────────────────────────────────────────────────────────────────
# DELETE /{user_id} — Supprimer (soft delete)
# ────────────────────────────────────────────────────────────────────────────

@router.delete("/{user_id}", status_code=204)
def delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user),
):
    user = db.query(Utilisateur).filter(
        Utilisateur.id == user_id,
        Utilisateur.is_deleted == False,
    ).first()
    if not user:
        raise HTTPException(404, "Utilisateur introuvable")

    if current_user.role.name != "admin":
        if not current_user.id_pharmacie or current_user.id_pharmacie != user.id_pharmacie:
            raise HTTPException(403, "Action non autorisée")
        if user.role and user.role.name in ("admin", "proprietaire"):
            raise HTTPException(403, "Impossible de supprimer ce compte")

    from datetime import datetime
    user.is_deleted = True
    user.deleted_at = datetime.utcnow()
    db.commit()
    enregistrer_action(
        db=db, utilisateur=current_user, action="DELETE",
        entity_type="utilisateur", entity_id=user_id,
        old_value={"email": user.email, "nom": user.nom},
    )
    return


# ────────────────────────────────────────────────────────────────────────────
# POST /reset-password-request — Demande de reset
# ────────────────────────────────────────────────────────────────────────────

@router.post("/reset-password-request")
def reset_password_request(payload: ResetPasswordRequest, db: Session = Depends(get_db)):
    user = db.query(Utilisateur).filter(
        Utilisateur.email == payload.email,
        Utilisateur.is_deleted == False,
    ).first()

    if user:
        code = str(secrets.randbelow(10**6)).zfill(6)
        user.code_reinitialisation = code
        db.commit()
        try:
            envoi_email_code(payload.email, code, sujet="Réinitialisation de mot de passe")
        except Exception as e:
            print(f"⚠️ Erreur envoi email reset : {e}")

    # Toujours le même message (anti-énumération)
    return {"message": "Si cet email est enregistré, un code vous a été envoyé."}


# ────────────────────────────────────────────────────────────────────────────
# POST /reset-password-confirm — Validation du reset
# ────────────────────────────────────────────────────────────────────────────

@router.post("/reset-password-confirm")
def reset_password_confirm(payload: ResetPasswordConfirm, db: Session = Depends(get_db)):
    user = db.query(Utilisateur).filter(
        Utilisateur.email == payload.email,
        Utilisateur.is_deleted == False,
    ).first()

    if not user or not user.code_reinitialisation or user.code_reinitialisation != payload.code:
        raise HTTPException(400, "Code invalide ou expiré")

    user.mot_de_passe          = get_password_hash(payload.nouveau_mot_de_passe)
    user.code_reinitialisation = None
    db.commit()

    enregistrer_action(
        db=db, utilisateur=user, action="UPDATE",
        entity_type="utilisateur", entity_id=user.id,
        new_value={"action": "reset_password"},
    )
    return {"message": "Mot de passe réinitialisé avec succès. Vous pouvez vous connecter."}