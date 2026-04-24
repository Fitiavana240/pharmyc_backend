# routers/fournisseurs.py
import random
import string
from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from database import get_db
from models.fournisseurs import Fournisseur, BonCommande
from schemas import FournisseurCreate, FournisseurUpdate, FournisseurRead
from routers.auth import get_current_user, owner_or_admin_required
from services.historique_service import enregistrer_action

router = APIRouter()


def generate_fournisseur_code(db: Session) -> str:
    while True:
        code = "FRN-" + "".join(random.choices(string.digits, k=6))
        if not db.query(Fournisseur).filter(Fournisseur.code == code).first():
            return code


@router.post("/", response_model=FournisseurRead, status_code=201)
def create_fournisseur(
    payload: FournisseurCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    if not current_user.id_pharmacie:
        raise HTTPException(400, "Aucune pharmacie associée")
    if current_user.role.name not in ["proprietaire", "gestionnaire_stock", "admin"]:
        raise HTTPException(403, "Accès refusé")

    existing = db.query(Fournisseur).filter(
        Fournisseur.id_pharmacie == current_user.id_pharmacie,
        Fournisseur.nom == payload.nom,
        Fournisseur.is_deleted == False,
    ).first()
    if existing:
        raise HTTPException(409, "Un fournisseur avec ce nom existe déjà")

    code = generate_fournisseur_code(db)
    fournisseur = Fournisseur(
        code=code,
        id_pharmacie=current_user.id_pharmacie,
        **payload.model_dump(),
    )
    db.add(fournisseur)
    db.commit()
    db.refresh(fournisseur)

    enregistrer_action(
        db=db, utilisateur=current_user,
        action="CREATE", entity_type="fournisseur",
        entity_id=fournisseur.id,
        new_value={"id": fournisseur.id, "code": fournisseur.code, "nom": fournisseur.nom},
    )
    return fournisseur


@router.get("/", response_model=List[FournisseurRead])
def list_fournisseurs(
    actif: Optional[bool] = Query(None),
    search: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    if not current_user.id_pharmacie:
        return []
    query = db.query(Fournisseur).filter(
        Fournisseur.id_pharmacie == current_user.id_pharmacie,
        Fournisseur.is_deleted == False,
    )
    if actif is not None:
        query = query.filter(Fournisseur.actif == actif)
    if search:
        query = query.filter(Fournisseur.nom.ilike(f"%{search}%"))
    return query.order_by(Fournisseur.nom).offset(skip).limit(limit).all()


@router.get("/{fournisseur_id}", response_model=FournisseurRead)
def get_fournisseur(
    fournisseur_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    f = db.query(Fournisseur).filter(
        Fournisseur.id == fournisseur_id,
        Fournisseur.id_pharmacie == current_user.id_pharmacie,
        Fournisseur.is_deleted == False,
    ).first()
    if not f:
        raise HTTPException(404, "Fournisseur introuvable")
    return f


@router.patch("/{fournisseur_id}", response_model=FournisseurRead)
def update_fournisseur(
    fournisseur_id: int,
    payload: FournisseurUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    f = db.query(Fournisseur).filter(
        Fournisseur.id == fournisseur_id,
        Fournisseur.id_pharmacie == current_user.id_pharmacie,
        Fournisseur.is_deleted == False,
    ).first()
    if not f:
        raise HTTPException(404, "Fournisseur introuvable")
    owner_or_admin_required(f.id_pharmacie, db, current_user)

    old = {col.name: getattr(f, col.name) for col in f.__table__.columns}
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(f, k, v)
    f.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(f)

    enregistrer_action(
        db=db, utilisateur=current_user,
        action="UPDATE", entity_type="fournisseur",
        entity_id=f.id, old_value=old,
        new_value={col.name: getattr(f, col.name) for col in f.__table__.columns},
    )
    return f


@router.delete("/{fournisseur_id}", status_code=204)
def delete_fournisseur(
    fournisseur_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    f = db.query(Fournisseur).filter(
        Fournisseur.id == fournisseur_id,
        Fournisseur.id_pharmacie == current_user.id_pharmacie,
        Fournisseur.is_deleted == False,
    ).first()
    if not f:
        raise HTTPException(404, "Fournisseur introuvable")
    owner_or_admin_required(f.id_pharmacie, db, current_user)

    # Bloquer si bons de commande actifs
    bc_actifs = db.query(BonCommande).filter(
        BonCommande.id_fournisseur == fournisseur_id,
        BonCommande.statut.in_(["brouillon", "envoye"]),
        BonCommande.is_deleted == False,
    ).count()
    if bc_actifs > 0:
        raise HTTPException(
            409, "Ce fournisseur a des bons de commande actifs. Annulez-les d'abord."
        )

    f.is_deleted = True
    f.deleted_at = datetime.utcnow()
    db.commit()
    enregistrer_action(
        db=db, utilisateur=current_user,
        action="DELETE", entity_type="fournisseur", entity_id=f.id,
        old_value={"is_deleted": False}, new_value={"is_deleted": True},
    )
    return