# routers/entrees_stock.py
from datetime import date, datetime
from decimal import Decimal
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session, joinedload
from database import get_db
from models.models import EntreeStock, Produit
from schemas import EntreeStockCreate, EntreeStockRead
from routers.auth import get_current_user, owner_or_admin_required
from services.historique_service import enregistrer_action
 
router = APIRouter()
 
 
# ──────────────────────────────────────────────────────────────
# POST /entrees_stock — Ajouter une entrée de stock
# ──────────────────────────────────────────────────────────────
@router.post("/", status_code=status.HTTP_201_CREATED)
def ajouter_entree(
    payload: EntreeStockCreate,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """
    Enregistre une entrée de stock pour un produit.
    Met à jour stock_boite et stock_total_piece.
    Enregistre prix_achat_unitaire, montant_achat et id_fournisseur
    directement dans l'EntreeStock pour le module Finance.
    """
    if not current_user.id_pharmacie:
        raise HTTPException(400, "Aucune pharmacie associée")
 
    if current_user.role.name not in ["proprietaire", "gestionnaire_stock", "admin"]:
        raise HTTPException(403, "Action non autorisée")
 
    produit = db.query(Produit).filter(
        Produit.id           == payload.id_produit,
        Produit.id_pharmacie == current_user.id_pharmacie,
        Produit.is_deleted   == False,
    ).first()
    if not produit:
        raise HTTPException(404, "Produit introuvable")
 
    # ── Calcul du prix et du montant ──────────────────────────
    pa_u = payload.prix_achat_unitaire  # Optional[Decimal]
    if pa_u and float(pa_u) > 0:
        prix_achat_unitaire_val = Decimal(str(pa_u))
        montant_achat_val       = Decimal(str(round(float(pa_u) * payload.quantite, 2)))
    else:
        prix_achat_unitaire_val = None
        montant_achat_val       = None
 
    # ── Résolution id_fournisseur ─────────────────────────────
    id_fournisseur_val = getattr(payload, 'id_fournisseur', None)
 
    # ── Création de l'EntreeStock avec TOUS les champs ────────
    # Les champs financiers sont passés directement au constructeur
    # → SQLAlchemy les persiste correctement en base
    entree = EntreeStock(
        id_produit            = produit.id,
        quantite              = payload.quantite,
        type_entree           = payload.type_entree,
        fournisseur           = payload.fournisseur,      # nom texte
        id_fournisseur        = id_fournisseur_val,       # ✅ FK vers fournisseurs
        date_entree           = getattr(payload, 'date_entree', None) or date.today(),
        prix_achat_unitaire   = prix_achat_unitaire_val,  # ✅ Finance
        montant_achat         = montant_achat_val,        # ✅ Finance
        id_bon_commande_ligne = None,                     # non applicable pour entrée manuelle
    )
    db.add(entree)
 
    # ── Mise à jour du stock ──────────────────────────────────
    pieces_par_boite   = (produit.quantite_par_boite or 1) * (produit.pieces_par_plaquette or 1)
    pieces_ajoutees    = payload.quantite * pieces_par_boite
    ancien_stock_boite = produit.stock_boite
    ancien_stock_piece = produit.stock_total_piece
 
    produit.stock_total_piece += pieces_ajoutees
    produit.stock_boite        = produit.stock_total_piece // pieces_par_boite
 
    # ── Propagation du prix d'achat ───────────────────────────
    if prix_achat_unitaire_val:
        if id_fournisseur_val:
            # Propagation vers PrixAchatFournisseur (table par fournisseur)
            try:
                from routers.prix_fournisseur import _mettre_a_jour_prix_fournisseur
                _mettre_a_jour_prix_fournisseur(
                    db,
                    id_produit     = produit.id,
                    id_fournisseur = id_fournisseur_val,
                    prix_ht        = float(prix_achat_unitaire_val),
                    quantite       = payload.quantite,
                )
            except Exception:
                # Fallback : mettre à jour produit.prix_achat directement
                try:
                    if not produit.prix_achat:
                        produit.prix_achat = prix_achat_unitaire_val
                except Exception:
                    pass
        else:
            # Pas de fournisseur → mettre à jour produit.prix_achat
            try:
                if not produit.prix_achat:
                    produit.prix_achat = prix_achat_unitaire_val
            except Exception:
                pass
 
    db.commit()
    db.refresh(entree)
 
    # ── Historique ────────────────────────────────────────────
    enregistrer_action(
        db=db, utilisateur=current_user,
        action="CREATE", entity_type="entree_stock", entity_id=entree.id,
        new_value={
            "id_produit":          produit.id,
            "quantite_boites":     payload.quantite,
            "type_entree":         payload.type_entree,
            "fournisseur":         payload.fournisseur,
            "id_fournisseur":      id_fournisseur_val,
            "prix_achat_unitaire": float(prix_achat_unitaire_val) if prix_achat_unitaire_val else None,
            "montant_achat":       float(montant_achat_val) if montant_achat_val else None,
            "date_entree":         str(entree.date_entree),
        },
    )
    enregistrer_action(
        db=db, utilisateur=current_user,
        action="UPDATE", entity_type="produit", entity_id=produit.id,
        old_value={"stock_boite": ancien_stock_boite, "stock_total_piece": ancien_stock_piece},
        new_value={"stock_boite": produit.stock_boite, "stock_total_piece": produit.stock_total_piece},
    )
 
    return {
        "id":                   entree.id,
        "id_produit":           entree.id_produit,
        "quantite":             entree.quantite,
        "type_entree":          entree.type_entree,
        "fournisseur":          entree.fournisseur,
        "id_fournisseur":       entree.id_fournisseur,
        "date_entree":          entree.date_entree,
        "prix_achat_unitaire":  float(entree.prix_achat_unitaire) if entree.prix_achat_unitaire else None,
        "montant_achat":        float(entree.montant_achat)        if entree.montant_achat        else None,
        "id_bon_commande_ligne": entree.id_bon_commande_ligne,
        "produit_nom":          produit.nom,
    }
 
 
# ──────────────────────────────────────────────────────────────
# GET /entrees_stock — Liste avec filtres
# ──────────────────────────────────────────────────────────────
@router.get("/")
def list_entrees(
    skip:           int            = Query(0,    ge=0),
    limit:          int            = Query(100,  ge=1, le=1000),
    produit_id:     Optional[int]  = Query(None),
    id_fournisseur: Optional[int]  = Query(None),
    date_debut:     Optional[date] = Query(None),
    date_fin:       Optional[date] = Query(None),
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    if not current_user.id_pharmacie:
        return []
 
    query = db.query(EntreeStock).join(Produit).filter(
        Produit.id_pharmacie == current_user.id_pharmacie,
        EntreeStock.is_deleted == False,
    )
 
    if produit_id:
        query = query.filter(EntreeStock.id_produit == produit_id)
 
    # ✅ Filtre sur id_fournisseur (colonne FK dans EntreeStock)
    if id_fournisseur:
        query = query.filter(EntreeStock.id_fournisseur == id_fournisseur)
 
    if date_debut:
        query = query.filter(EntreeStock.date_entree >= date_debut)
    if date_fin:
        query = query.filter(EntreeStock.date_entree <= date_fin)
 
    entrees = query.order_by(EntreeStock.date_entree.desc()).offset(skip).limit(limit).all()
 
    result = []
    for e in entrees:
        result.append({
            "id":                    e.id,
            "id_produit":            e.id_produit,
            "quantite":              e.quantite,
            "type_entree":           e.type_entree,
            "fournisseur":           e.fournisseur,
            "id_fournisseur":        e.id_fournisseur,
            "date_entree":           e.date_entree,
            "prix_achat_unitaire":   float(e.prix_achat_unitaire) if e.prix_achat_unitaire else None,
            "montant_achat":         float(e.montant_achat)       if e.montant_achat       else None,
            "id_bon_commande_ligne": e.id_bon_commande_ligne,
            "produit_nom":           e.produit.nom if e.produit else None,
        })
    return result
 
 
# ──────────────────────────────────────────────────────────────
# GET /entrees_stock/{entree_id} — Détail
# ──────────────────────────────────────────────────────────────
@router.get("/{entree_id}")
def get_entree(
    entree_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    if not current_user.id_pharmacie:
        raise HTTPException(400, "Aucune pharmacie associée")
 
    entree = db.query(EntreeStock).join(Produit).filter(
        EntreeStock.id       == entree_id,
        Produit.id_pharmacie == current_user.id_pharmacie,
        EntreeStock.is_deleted == False,
    ).first()
    if not entree:
        raise HTTPException(404, "Entrée introuvable")
 
    return {
        "id":                    entree.id,
        "id_produit":            entree.id_produit,
        "quantite":              entree.quantite,
        "type_entree":           entree.type_entree,
        "fournisseur":           entree.fournisseur,
        "id_fournisseur":        entree.id_fournisseur,
        "date_entree":           entree.date_entree,
        "prix_achat_unitaire":   float(entree.prix_achat_unitaire) if entree.prix_achat_unitaire else None,
        "montant_achat":         float(entree.montant_achat)       if entree.montant_achat       else None,
        "id_bon_commande_ligne": entree.id_bon_commande_ligne,
        "produit_nom":           entree.produit.nom if entree.produit else None,
    }
 
 
# ──────────────────────────────────────────────────────────────
# DELETE /entrees_stock/{entree_id} — Suppression logique
# ──────────────────────────────────────────────────────────────
@router.delete("/{entree_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_entree(
    entree_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    if not current_user.id_pharmacie:
        raise HTTPException(400, "Aucune pharmacie associée")
 
    entree = db.query(EntreeStock).join(Produit).filter(
        EntreeStock.id       == entree_id,
        Produit.id_pharmacie == current_user.id_pharmacie,
        EntreeStock.is_deleted == False,
    ).first()
    if not entree:
        raise HTTPException(404, "Entrée introuvable")
 
    owner_or_admin_required(entree.produit.id_pharmacie, db, current_user)
 
    anciennes_valeurs = {"is_deleted": entree.is_deleted, "deleted_at": entree.deleted_at}
    entree.is_deleted = True
    entree.deleted_at = datetime.utcnow()
    db.commit()
 
    enregistrer_action(
        db=db, utilisateur=current_user,
        action="DELETE", entity_type="entree_stock", entity_id=entree.id,
        old_value=anciennes_valeurs,
        new_value={"is_deleted": True, "deleted_at": str(entree.deleted_at)},
    )
    return
 
 