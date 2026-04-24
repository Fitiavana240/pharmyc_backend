# ============================================================
# routers/produits.py — COMPLET AVEC UPLOAD IMAGE
# + migration automatique pour image_url
# ============================================================
#
# NOUVEAUTÉS :
#   - POST /produits/{id}/image   → upload image (multipart)
#   - DELETE /produits/{id}/image → supprimer l'image
#   - GET  /produits/             → retourne image_url dans la réponse
#   - Seuls proprietaire et admin peuvent créer/modifier/supprimer
#
# MIGRATION (à ajouter dans main.py dans _migrer_colonnes) :
#   "ALTER TABLE produits ADD COLUMN IF NOT EXISTS image_url VARCHAR(500)"
# ============================================================

import os
import random
import string
import shutil
from datetime import datetime, date
from decimal import Decimal
from typing import Optional
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from sqlalchemy.orm import Session

from database import get_db
from models.models import Produit
from schemas import ProduitCreate, ProduitUpdate, ProduitRead
from routers.auth import get_current_user, owner_or_admin_required
from services.historique_service import enregistrer_action

router = APIRouter()

# Dossier images produits
IMAGES_DIR = Path("uploads/produits")
IMAGES_DIR.mkdir(parents=True, exist_ok=True)

# Extensions autorisées
ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp"}
MAX_SIZE_MB = 5


def generate_product_code(db: Session) -> str:
    while True:
        code = "PRD-" + ''.join(random.choices(string.digits + string.ascii_uppercase, k=6))
        if not db.query(Produit).filter(Produit.code == code).first():
            return code


def _enrichir(produit: Produit) -> dict:
    """Ajoute prix_plaquette, prix_piece et image_url dans la réponse."""
    from decimal import Decimal, ROUND_HALF_UP
    qpb = Decimal(produit.quantite_par_boite or 1)
    ppp = Decimal(produit.pieces_par_plaquette or 1)
    pb  = Decimal(str(produit.prix_vente))
    pp  = (pb / qpb).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    pu  = (pb / qpb / ppp).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    data = {col.name: getattr(produit, col.name) for col in produit.__table__.columns}
    data["prix_plaquette"] = pp
    data["prix_piece"]     = pu
    # image_url : s'assurer qu'il est présent même si la colonne n'existe pas encore
    data["image_url"] = getattr(produit, "image_url", None)
    return data


# ══════════════════════════════════════════════════════════════
# POST /produits/ — Créer un produit
# ══════════════════════════════════════════════════════════════
@router.post("/", status_code=201)
def create_produit(
    payload: ProduitCreate,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    if not current_user.id_pharmacie:
        raise HTTPException(400, "Aucune pharmacie associée")

    role = current_user.role.name if current_user.role else ""
    if role not in ("proprietaire", "gestionnaire_stock", "admin"):
        raise HTTPException(403, "Seuls le propriétaire et le gestionnaire peuvent créer des produits")

    # Unicité du nom
    existing = db.query(Produit).filter(
        Produit.id_pharmacie == current_user.id_pharmacie,
        Produit.nom == payload.nom,
        Produit.is_deleted == False,
    ).first()
    if existing:
        raise HTTPException(409, f"Un produit '{payload.nom}' existe déjà")

    code = generate_product_code(db)
    data = payload.model_dump()
    produit = Produit(
        code=code,
        id_pharmacie=current_user.id_pharmacie,
        **data,
    )
    produit.stock_total_piece = (
        (produit.stock_boite or 0)
        * (produit.quantite_par_boite or 1)
        * (produit.pieces_par_plaquette or 1)
    )
    db.add(produit)
    db.commit()
    db.refresh(produit)

    enregistrer_action(
        db=db, utilisateur=current_user,
        action="CREATE", entity_type="produit", entity_id=produit.id,
        new_value={"id": produit.id, "code": produit.code, "nom": produit.nom},
    )
    return _enrichir(produit)


# ══════════════════════════════════════════════════════════════
# GET /produits/ — Liste
# ══════════════════════════════════════════════════════════════
@router.get("/")
def list_produits(
    search: Optional[str] = Query(None),
    categorie: Optional[str] = Query(None),
    skip: int = Query(0),
    limit: int = Query(200),
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    if not current_user.id_pharmacie:
        return []

    query = db.query(Produit).filter(
        Produit.id_pharmacie == current_user.id_pharmacie,
        Produit.is_deleted == False,
    )
    if search:
        query = query.filter(
            Produit.nom.ilike(f"%{search}%") |
            Produit.code.ilike(f"%{search}%") |
            Produit.categorie.ilike(f"%{search}%")
        )
    if categorie:
        query = query.filter(Produit.categorie == categorie)

    produits = query.order_by(Produit.nom).offset(skip).limit(limit).all()
    return [_enrichir(p) for p in produits]


# ══════════════════════════════════════════════════════════════
# GET /produits/{id}
# ══════════════════════════════════════════════════════════════
@router.get("/{produit_id}")
def get_produit(
    produit_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    produit = db.query(Produit).filter(
        Produit.id == produit_id,
        Produit.id_pharmacie == current_user.id_pharmacie,
        Produit.is_deleted == False,
    ).first()
    if not produit:
        raise HTTPException(404, "Produit introuvable")
    return _enrichir(produit)


# ══════════════════════════════════════════════════════════════
# PATCH /produits/{id} — Modifier (proprietaire/admin)
# ══════════════════════════════════════════════════════════════
@router.patch("/{produit_id}")
def update_produit(
    produit_id: int,
    payload: ProduitUpdate,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    produit = db.query(Produit).filter(
        Produit.id == produit_id,
        Produit.id_pharmacie == current_user.id_pharmacie,
        Produit.is_deleted == False,
    ).first()
    if not produit:
        raise HTTPException(404, "Produit introuvable")

    owner_or_admin_required(produit.id_pharmacie, db, current_user)

    # Unicité du nom si modifié
    data = payload.model_dump(exclude_unset=True)
    if "nom" in data and data["nom"] != produit.nom:
        existing = db.query(Produit).filter(
            Produit.id_pharmacie == current_user.id_pharmacie,
            Produit.nom == data["nom"],
            Produit.id != produit_id,
            Produit.is_deleted == False,
        ).first()
        if existing:
            raise HTTPException(409, f"Un autre produit porte déjà le nom '{data['nom']}'")

    old = {col.name: getattr(produit, col.name) for col in produit.__table__.columns}
    for k, v in data.items():
        setattr(produit, k, v)

    # Recalcul stock
    if any(k in data for k in ("stock_boite", "quantite_par_boite", "pieces_par_plaquette")):
        produit.stock_total_piece = (
            (produit.stock_boite or 0)
            * (produit.quantite_par_boite or 1)
            * (produit.pieces_par_plaquette or 1)
        )

    db.commit()
    db.refresh(produit)

    enregistrer_action(
        db=db, utilisateur=current_user,
        action="UPDATE", entity_type="produit", entity_id=produit.id,
        old_value=old,
        new_value={col.name: getattr(produit, col.name) for col in produit.__table__.columns},
    )
    return _enrichir(produit)


# ══════════════════════════════════════════════════════════════
# DELETE /produits/{id} — Supprimer (proprietaire/admin)
# ══════════════════════════════════════════════════════════════
@router.delete("/{produit_id}", status_code=204)
def delete_produit(
    produit_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    produit = db.query(Produit).filter(
        Produit.id == produit_id,
        Produit.id_pharmacie == current_user.id_pharmacie,
        Produit.is_deleted == False,
    ).first()
    if not produit:
        raise HTTPException(404, "Produit introuvable")
    owner_or_admin_required(produit.id_pharmacie, db, current_user)

    # Supprimer l'image si elle existe
    img = getattr(produit, "image_url", None)
    if img:
        img_path = Path(img.lstrip("/"))
        if img_path.exists():
            img_path.unlink()

    produit.is_deleted = True
    produit.deleted_at = datetime.utcnow()
    db.commit()

    enregistrer_action(
        db=db, utilisateur=current_user,
        action="DELETE", entity_type="produit", entity_id=produit.id,
        new_value={"is_deleted": True},
    )
    return


# ══════════════════════════════════════════════════════════════
# POST /produits/{id}/image — Upload image (propriétaire/admin)
# ══════════════════════════════════════════════════════════════
@router.post("/{produit_id}/image")
async def upload_image(
    produit_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """
    Upload une image pour un produit.
    - Format accepté : JPG, PNG, WEBP
    - Taille max : 5 Mo
    - Propriétaire / admin uniquement
    """
    produit = db.query(Produit).filter(
        Produit.id == produit_id,
        Produit.id_pharmacie == current_user.id_pharmacie,
        Produit.is_deleted == False,
    ).first()
    if not produit:
        raise HTTPException(404, "Produit introuvable")
    owner_or_admin_required(produit.id_pharmacie, db, current_user)

    # Vérifier l'extension
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(400, f"Format non autorisé. Acceptés : {', '.join(ALLOWED_EXT)}")

    # Vérifier la taille
    content = await file.read()
    if len(content) > MAX_SIZE_MB * 1024 * 1024:
        raise HTTPException(400, f"Image trop volumineuse (max {MAX_SIZE_MB} Mo)")

    # Supprimer l'ancienne image si elle existe
    old_url = getattr(produit, "image_url", None)
    if old_url:
        old_path = Path(old_url.lstrip("/"))
        if old_path.exists():
            old_path.unlink()

    # Sauvegarder la nouvelle image
    filename = f"produit_{produit_id}_{int(datetime.utcnow().timestamp())}{ext}"
    file_path = IMAGES_DIR / filename
    with open(file_path, "wb") as f:
        f.write(content)

    # Mettre à jour l'URL en base
    image_url = f"/uploads/produits/{filename}"
    try:
        setattr(produit, "image_url", image_url)
        db.commit()
    except Exception:
        # Si la colonne n'existe pas encore → migration pas encore faite
        raise HTTPException(
            500,
            "La colonne image_url n'existe pas encore. "
            "Redémarrez le backend pour appliquer la migration automatique."
        )

    enregistrer_action(
        db=db, utilisateur=current_user,
        action="UPDATE", entity_type="produit", entity_id=produit.id,
        new_value={"image_url": image_url},
    )
    return {"message": "Image uploadée", "image_url": image_url}


# ══════════════════════════════════════════════════════════════
# DELETE /produits/{id}/image — Supprimer l'image
# ══════════════════════════════════════════════════════════════
@router.delete("/{produit_id}/image")
def delete_image(
    produit_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    produit = db.query(Produit).filter(
        Produit.id == produit_id,
        Produit.id_pharmacie == current_user.id_pharmacie,
        Produit.is_deleted == False,
    ).first()
    if not produit:
        raise HTTPException(404, "Produit introuvable")
    owner_or_admin_required(produit.id_pharmacie, db, current_user)

    img = getattr(produit, "image_url", None)
    if not img:
        raise HTTPException(404, "Ce produit n'a pas d'image")

    img_path = Path(img.lstrip("/"))
    if img_path.exists():
        img_path.unlink()

    try:
        setattr(produit, "image_url", None)
        db.commit()
    except Exception:
        pass

    return {"message": "Image supprimée"}


# ══════════════════════════════════════════════════════════════
# GET /produits/low-stock
# ══════════════════════════════════════════════════════════════
@router.get("/low-stock")
def produits_low_stock(
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    if not current_user.id_pharmacie:
        return []
    produits = db.query(Produit).filter(
        Produit.id_pharmacie == current_user.id_pharmacie,
        Produit.is_deleted == False,
        Produit.stock_total_piece <= Produit.seuil_alerte,
        Produit.stock_total_piece > 0,
    ).all()
    return [_enrichir(p) for p in produits]


# ══════════════════════════════════════════════════════════════
# GET /produits/expiring
# ══════════════════════════════════════════════════════════════
@router.get("/expiring")
def produits_expiring(
    days: int = Query(30),
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    from datetime import timedelta
    if not current_user.id_pharmacie:
        return []
    limite = date.today() + timedelta(days=days)
    produits = db.query(Produit).filter(
        Produit.id_pharmacie == current_user.id_pharmacie,
        Produit.is_deleted == False,
        Produit.date_expiration != None,
        Produit.date_expiration <= limite,
    ).order_by(Produit.date_expiration).all()
    return [_enrichir(p) for p in produits]


# ══════════════════════════════════════════════════════════════
# GET /produits/categories
# Liste des catégories disponibles
# ══════════════════════════════════════════════════════════════
@router.get("/categories")
def list_categories(
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    if not current_user.id_pharmacie:
        return []
    from sqlalchemy import distinct
    rows = db.query(distinct(Produit.categorie)).filter(
        Produit.id_pharmacie == current_user.id_pharmacie,
        Produit.is_deleted == False,
        Produit.categorie != None,
    ).all()
    return sorted([r[0] for r in rows if r[0]])