# routers/pharmacies.py — Pharmy-C v5.1
import random
import string
import os
import time
from datetime import date, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from sqlalchemy.orm import Session, joinedload
from database import get_db
from models.models import Pharmacie, Utilisateur, Role
from utils.security import get_password_hash
from schemas import PharmacieCreate, PharmacieUpdate, PharmacieRead, EmployeCreate, UserRead
from routers.auth import get_current_user, owner_or_admin_required
from services.historique_service import enregistrer_action
from services.date_utils import utcnow, to_iso_utc
import uuid
from models.abonnements import Abonnement

router = APIRouter()

# ─── Helpers ──────────────────────────────────────────────

def generate_pharmacie_code(db: Session) -> str:
    while True:
        code = "PHAR-" + ''.join(random.choices(string.digits, k=6))
        if not db.query(Pharmacie).filter(Pharmacie.code == code).first():
            return code

DEVISES_INFO = [
    {"code": "MGA", "nom": "Ariary malgache",    "symbole": "Ar",   "pays": "🇲🇬 Madagascar"},
    {"code": "USD", "nom": "Dollar américain",   "symbole": "$",    "pays": "🇺🇸 États-Unis"},
    {"code": "EUR", "nom": "Euro",               "symbole": "€",    "pays": "🇪🇺 Europe"},
    {"code": "GBP", "nom": "Livre sterling",     "symbole": "£",    "pays": "🇬🇧 Royaume-Uni"},
    {"code": "CHF", "nom": "Franc suisse",       "symbole": "CHF",  "pays": "🇨🇭 Suisse"},
    {"code": "CAD", "nom": "Dollar canadien",    "symbole": "CA$",  "pays": "🇨🇦 Canada"},
    {"code": "ZAR", "nom": "Rand sud-africain",  "symbole": "R",    "pays": "🇿🇦 Afrique du Sud"},
    {"code": "KES", "nom": "Shilling kényan",    "symbole": "KSh",  "pays": "🇰🇪 Kenya"},
    {"code": "XOF", "nom": "Franc CFA (UEMOA)",  "symbole": "CFA",  "pays": "🌍 Afrique Ouest"},
    {"code": "XAF", "nom": "Franc CFA (CEMAC)",  "symbole": "FCFA", "pays": "🌍 Afrique Centrale"},
]

ALLOWED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp"}


async def _sauvegarder_logo(file: UploadFile, pharmacie_id: int) -> str:
    """Sauvegarde le logo et retourne le chemin relatif."""
    if file.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(400, "Type de fichier non supporté (png, jpeg, webp)")
    os.makedirs("uploads/logos", exist_ok=True)
    timestamp = int(time.time())
    ext       = os.path.splitext(file.filename)[1] if file.filename else ".png"
    filename  = f"pharmacie_{pharmacie_id}_{timestamp}{ext}"
    filepath  = f"uploads/logos/{filename}"
    content   = await file.read()
    with open(filepath, "wb") as f:
        f.write(content)
    return f"/{filepath}"


# ═══════════════════════════════════════════════════════════
# POST /pharmacies/ — Créer une pharmacie + abonnement essai
# ═══════════════════════════════════════════════════════════

@router.post("/", status_code=201)
def create_pharmacie(
    payload: PharmacieCreate,
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user),
):
    """
    Crée une pharmacie.
    - Admin : peut toujours créer
    - Utilisateur sans pharmacie : devient automatiquement propriétaire
    """
    is_admin              = current_user.role and current_user.role.name == "admin"
    is_nouveau_proprietaire = not current_user.id_pharmacie

    if not is_admin and not is_nouveau_proprietaire:
        raise HTTPException(403, "Vous avez déjà une pharmacie associée")

    if db.query(Pharmacie).filter(
        Pharmacie.email      == payload.email,
        Pharmacie.is_deleted == False,
    ).first():
        raise HTTPException(409, "Un compte avec cet email existe déjà")

    code = generate_pharmacie_code(db)
    pharmacie = Pharmacie(
        code          = code,
        nom           = payload.nom,
        email         = payload.email,
        mot_de_passe  = get_password_hash(payload.mot_de_passe),
        nif           = getattr(payload, 'nif',       None),
        stat          = getattr(payload, 'stat',      None),
        adresse       = getattr(payload, 'adresse',   None),
        telephone     = getattr(payload, 'telephone', None),
        devise        = getattr(payload, 'devise',    None) or "MGA",
        date_creation = date.today(),
        owner_user_id = current_user.id,
    )
    db.add(pharmacie)
    db.flush()  # obtenir pharmacie.id avant commit

    # Assigner rôle propriétaire si pas admin
    if not is_admin:
        role_proprio = db.query(Role).filter(Role.name == "proprietaire").first()
        if role_proprio:
            current_user.id_role      = role_proprio.id
            current_user.id_pharmacie = pharmacie.id

    # Abonnement d'essai 30 jours
    today    = date.today()
    date_fin = today + timedelta(days=30)
    abo = Abonnement(
        id_pharmacie       = pharmacie.id,
        statut             = "essai",
        date_debut         = today,
        date_fin           = date_fin,
        prix_mensuel       = 45000,
        proprietaire_nom   = current_user.nom,
        proprietaire_email = current_user.email,
        proprietaire_tel   = getattr(payload, 'telephone', None),
    )
    db.add(abo)
    db.commit()
    db.refresh(pharmacie)

    enregistrer_action(
        db=db, utilisateur=current_user,
        action="CREATE", entity_type="pharmacie",
        entity_id=pharmacie.id,
        new_value={
            "code":            pharmacie.code,
            "nom":             pharmacie.nom,
            "essai_jusqu_au":  str(date_fin),
            "created_at":      to_iso_utc(utcnow()),
        },
    )

    return {
        "pharmacie": {
            "id":            pharmacie.id,
            "code":          pharmacie.code,
            "nom":           pharmacie.nom,
            "email":         pharmacie.email,
            "devise":        pharmacie.devise,
            "date_creation": str(pharmacie.date_creation),
        },
        "abonnement": {
            "statut":     "essai",
            "date_debut": str(today),
            "date_fin":   str(date_fin),
            "message":    "1 mois d'essai gratuit activé. Abonnement à 45 000 Ar/mois après.",
        },
    }


# ═══════════════════════════════════════════════════════════
# GET /pharmacies/me — Ma pharmacie
# ═══════════════════════════════════════════════════════════

@router.get("/me")
def get_my_pharmacy(
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user),
):
    if not current_user.id_pharmacie:
        return {"pharmacie": None}

    ph = db.query(Pharmacie).filter(
        Pharmacie.id         == current_user.id_pharmacie,
        Pharmacie.is_deleted == False,
    ).first()
    if not ph:
        raise HTTPException(404, "Pharmacie introuvable")

    today    = date.today()
    abo      = db.query(Abonnement).filter(
        Abonnement.id_pharmacie == ph.id
    ).first()
    abo_info = None
    if abo:
        abo_info = {
            "statut":         abo.statut,
            "date_debut":     str(abo.date_debut),
            "date_fin":       str(abo.date_fin),
            "jours_restants": max(0, (abo.date_fin - today).days),
            "actif":          abo.statut != "suspendu" and abo.date_fin >= today,
            "prix_mensuel":   float(abo.prix_mensuel),
        }

    return {
        "id":            ph.id,
        "code":          ph.code,
        "nom":           ph.nom,
        "email":         ph.email,
        "nif":           ph.nif,
        "stat":          ph.stat,
        "telephone":     ph.telephone,
        "adresse":       ph.adresse,
        "devise":        ph.devise,
        "logo":          ph.logo,
        "date_creation": str(ph.date_creation) if ph.date_creation else None,
        "owner_user_id": ph.owner_user_id,
        "abonnement":    abo_info,
    }


# ═══════════════════════════════════════════════════════════
# GET /pharmacies/devises
# ═══════════════════════════════════════════════════════════

@router.get("/devises")
def list_devises():
    return DEVISES_INFO


# ═══════════════════════════════════════════════════════════
# GET /pharmacies/employes
# ═══════════════════════════════════════════════════════════

@router.get("/employes")
def list_employes(
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user),
):
    if not current_user.id_pharmacie:
        raise HTTPException(400, "Aucune pharmacie associée")

    owner_or_admin_required(current_user.id_pharmacie, db, current_user)

    roles_exclus = db.query(Role).filter(
        Role.name.in_(["admin", "proprietaire"])
    ).all()
    ids_exclus = [r.id for r in roles_exclus]

    employes = db.query(Utilisateur).filter(
        Utilisateur.id_pharmacie == current_user.id_pharmacie,
        Utilisateur.is_deleted   == False,
        Utilisateur.id_role.notin_(ids_exclus),
    ).options(joinedload(Utilisateur.role)).all()

    return [
        {
            "id":                 emp.id,
            "uuid":               emp.uuid,
            "nom":                emp.nom,
            "email":              emp.email,
            "telephone":          getattr(emp, 'telephone', None),
            "id_role":            emp.id_role,
            "role_name":          emp.role.name if emp.role else None,
            "id_pharmacie":       emp.id_pharmacie,
            "est_actif":          emp.est_actif,
            "confirmation_email": emp.confirmation_email,
        }
        for emp in employes
    ]


# ═══════════════════════════════════════════════════════════
# POST /pharmacies/employes — Ajouter un employé
# ═══════════════════════════════════════════════════════════

@router.post("/employes", status_code=201)
def ajouter_employe(
    payload: EmployeCreate,
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user),
):
    if not current_user.id_pharmacie:
        raise HTTPException(400, "Aucune pharmacie associée")

    pharmacie_id = current_user.id_pharmacie
    owner_or_admin_required(pharmacie_id, db, current_user)

    if payload.id_role is None:
        raise HTTPException(400, "Le rôle (id_role) est obligatoire")

    role = db.query(Role).filter(Role.id == payload.id_role).first()
    if not role:
        raise HTTPException(404, "Rôle introuvable")
    if role.name in ["admin", "proprietaire"]:
        raise HTTPException(403, f"Impossible d'assigner le rôle '{role.name}' à un employé")

    existing = db.query(Utilisateur).filter(
        Utilisateur.email      == payload.email,
        Utilisateur.is_deleted == False,
    ).first()

    if existing:
        if existing.id_pharmacie and existing.id_pharmacie != pharmacie_id:
            raise HTTPException(400, "Email déjà utilisé par une autre pharmacie")

        old_values = {
            "nom":       existing.nom,
            "id_role":   existing.id_role,
            "est_actif": existing.est_actif,
        }
        existing.nom                = payload.nom
        existing.mot_de_passe       = get_password_hash(payload.mot_de_passe)
        existing.id_role            = payload.id_role
        existing.id_pharmacie       = pharmacie_id
        existing.est_actif          = False
        existing.confirmation_email = True
        existing.code_confirmation  = None
        db.commit()
        db.refresh(existing)

        enregistrer_action(
            db=db, utilisateur=current_user,
            action="UPDATE", entity_type="utilisateur",
            entity_id=existing.id,
            old_value=old_values,
            new_value={
                "nom":       existing.nom,
                "id_role":   existing.id_role,
                "est_actif": existing.est_actif,
                "updated_at": to_iso_utc(utcnow()),
            },
        )
        return existing

    new_user = Utilisateur(
        uuid                = str(uuid.uuid4()),
        nom                 = payload.nom,
        email               = payload.email,
        mot_de_passe        = get_password_hash(payload.mot_de_passe),
        id_role             = payload.id_role,
        id_pharmacie        = pharmacie_id,
        est_actif           = False,
        confirmation_email  = True,
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    enregistrer_action(
        db=db, utilisateur=current_user,
        action="CREATE", entity_type="utilisateur",
        entity_id=new_user.id,
        new_value={
            "id":         new_user.id,
            "nom":        new_user.nom,
            "email":      new_user.email,
            "id_role":    new_user.id_role,
            "created_at": to_iso_utc(utcnow()),
        },
    )
    return new_user


# ═══════════════════════════════════════════════════════════
# GET /pharmacies/ — Liste
# ═══════════════════════════════════════════════════════════

@router.get("/")
def list_pharmacies(
    search: Optional[str] = Query(None),
    skip:   int = Query(0, ge=0),
    limit:  int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user),
):
    if current_user.role and current_user.role.name == "admin":
        q = db.query(Pharmacie).filter(Pharmacie.is_deleted == False)
        if search:
            q = q.filter(Pharmacie.nom.ilike(f"%{search}%"))
        pharmacies = q.order_by(Pharmacie.nom).offset(skip).limit(limit).all()
    else:
        if not current_user.id_pharmacie:
            return []
        pharmacies = db.query(Pharmacie).filter(
            Pharmacie.id         == current_user.id_pharmacie,
            Pharmacie.is_deleted == False,
        ).all()

    today  = date.today()
    result = []
    for ph in pharmacies:
        abo = db.query(Abonnement).filter(
            Abonnement.id_pharmacie == ph.id
        ).first()
        abo_info = None
        if abo:
            abo_info = {
                "statut":         abo.statut,
                "date_fin":       str(abo.date_fin),
                "jours_restants": max(0, (abo.date_fin - today).days),
                "actif":          abo.statut != "suspendu" and abo.date_fin >= today,
            }
        result.append({
            "id":            ph.id,
            "code":          ph.code,
            "nom":           ph.nom,
            "email":         ph.email,
            "telephone":     ph.telephone,
            "adresse":       ph.adresse,
            "devise":        ph.devise,
            "logo":          ph.logo,
            "date_creation": str(ph.date_creation) if ph.date_creation else None,
            "abonnement":    abo_info,
        })
    return result


# ═══════════════════════════════════════════════════════════
# GET /pharmacies/{pharmacie_id}
# ═══════════════════════════════════════════════════════════

@router.get("/{pharmacie_id}")
def get_pharmacie(
    pharmacie_id: int,
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user),
):
    ph = db.query(Pharmacie).filter(
        Pharmacie.id         == pharmacie_id,
        Pharmacie.is_deleted == False,
    ).first()
    if not ph:
        raise HTTPException(404, "Pharmacie introuvable")

    if current_user.role and current_user.role.name != "admin":
        if current_user.id_pharmacie != pharmacie_id:
            raise HTTPException(403, "Accès refusé")

    today    = date.today()
    abo      = db.query(Abonnement).filter(
        Abonnement.id_pharmacie == ph.id
    ).first()
    abo_info = None
    if abo:
        abo_info = {
            "statut":         abo.statut,
            "date_debut":     str(abo.date_debut),
            "date_fin":       str(abo.date_fin),
            "jours_restants": max(0, (abo.date_fin - today).days),
            "actif":          abo.statut != "suspendu" and abo.date_fin >= today,
            "prix_mensuel":   float(abo.prix_mensuel),
        }

    return {
        "id":            ph.id,
        "code":          ph.code,
        "nom":           ph.nom,
        "email":         ph.email,
        "nif":           ph.nif,
        "stat":          ph.stat,
        "telephone":     ph.telephone,
        "adresse":       ph.adresse,
        "devise":        ph.devise,
        "logo":          ph.logo,
        "date_creation": str(ph.date_creation) if ph.date_creation else None,
        "owner_user_id": ph.owner_user_id,
        "abonnement":    abo_info,
    }


# ═══════════════════════════════════════════════════════════
# PATCH /pharmacies/{pharmacie_id}
# ═══════════════════════════════════════════════════════════

@router.patch("/{pharmacie_id}")
def update_pharmacie(
    pharmacie_id: int,
    payload: PharmacieUpdate,
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user),
):
    ph = db.query(Pharmacie).filter(
        Pharmacie.id         == pharmacie_id,
        Pharmacie.is_deleted == False,
    ).first()
    if not ph:
        raise HTTPException(404, "Pharmacie introuvable")

    owner_or_admin_required(pharmacie_id, db, current_user)

    old_values = {col.name: getattr(ph, col.name) for col in ph.__table__.columns}
    data       = payload.model_dump(exclude_unset=True)

    if "mot_de_passe" in data and data["mot_de_passe"]:
        data["mot_de_passe"] = get_password_hash(data["mot_de_passe"])

    for k, v in data.items():
        setattr(ph, k, v)

    db.commit()
    db.refresh(ph)

    enregistrer_action(
        db=db, utilisateur=current_user,
        action="UPDATE", entity_type="pharmacie",
        entity_id=ph.id,
        old_value=old_values,
        new_value={**data, "updated_at": to_iso_utc(utcnow())},
    )
    return {"message": "Pharmacie mise à jour", "id": ph.id, "nom": ph.nom}


# ═══════════════════════════════════════════════════════════
# DELETE /pharmacies/{pharmacie_id}
# ═══════════════════════════════════════════════════════════

@router.delete("/{pharmacie_id}", status_code=204)
def delete_pharmacie(
    pharmacie_id: int,
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user),
):
    if not current_user.role or current_user.role.name != "admin":
        raise HTTPException(403, "Seul l'admin peut supprimer une pharmacie")

    ph = db.query(Pharmacie).filter(
        Pharmacie.id         == pharmacie_id,
        Pharmacie.is_deleted == False,
    ).first()
    if not ph:
        raise HTTPException(404, "Pharmacie introuvable")

    ph.is_deleted = True
    ph.deleted_at = utcnow()
    db.commit()

    enregistrer_action(
        db=db, utilisateur=current_user,
        action="DELETE", entity_type="pharmacie",
        entity_id=pharmacie_id,
        old_value={"nom": ph.nom, "email": ph.email},
        new_value={"deleted_at": to_iso_utc(utcnow())},
    )
    return


# ═══════════════════════════════════════════════════════════
# POST /pharmacies/{pharmacie_id}/logo — Upload logo existant
# ═══════════════════════════════════════════════════════════

@router.post("/{pharmacie_id}/logo")
async def upload_logo(
    pharmacie_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user),
):
    ph = db.query(Pharmacie).filter(
        Pharmacie.id         == pharmacie_id,
        Pharmacie.is_deleted == False,
    ).first()
    if not ph:
        raise HTTPException(404, "Pharmacie introuvable")

    owner_or_admin_required(pharmacie_id, db, current_user)

    old_logo  = ph.logo
    logo_path = await _sauvegarder_logo(file, pharmacie_id)
    ph.logo   = logo_path
    db.commit()

    enregistrer_action(
        db=db, utilisateur=current_user,
        action="UPDATE", entity_type="pharmacie",
        entity_id=ph.id,
        old_value={"logo": old_logo},
        new_value={"logo": ph.logo, "updated_at": to_iso_utc(utcnow())},
    )
    return {"logo_url": ph.logo}


# ═══════════════════════════════════════════════════════════
# POST /pharmacies/{pharmacie_id}/logo-creation — Logo à la création
# ═══════════════════════════════════════════════════════════

@router.post("/{pharmacie_id}/logo-creation")
async def upload_logo_creation(
    pharmacie_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user),
):
    """
    Upload du logo juste après la création de la pharmacie.
    Accessible au propriétaire nouvellement créé ou à l'admin.
    """
    ph = db.query(Pharmacie).filter(
        Pharmacie.id         == pharmacie_id,
        Pharmacie.is_deleted == False,
    ).first()
    if not ph:
        raise HTTPException(404, "Pharmacie introuvable")

    # Vérifier que c'est bien le propriétaire de cette pharmacie
    is_admin   = current_user.role and current_user.role.name == "admin"
    is_proprio = current_user.id_pharmacie == pharmacie_id

    if not is_admin and not is_proprio:
        raise HTTPException(403, "Accès refusé")

    logo_path = await _sauvegarder_logo(file, pharmacie_id)
    ph.logo   = logo_path
    db.commit()

    enregistrer_action(
        db=db, utilisateur=current_user,
        action="UPDATE", entity_type="pharmacie",
        entity_id=ph.id,
        new_value={"logo": ph.logo, "created_at": to_iso_utc(utcnow())},
    )
    return {"logo_url": ph.logo}