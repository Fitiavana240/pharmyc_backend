# routers/ventes.py — avec utcnow() et prix de gros
import random
import string
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_, and_
from sqlalchemy.orm import Session

from database import get_db
from models.models import Vente, DetailVente, Produit, Client, Paiement, Pharmacie, Utilisateur, Historique
from schemas import CommandeCreate, PaiementCreate
from routers.auth import get_current_user
from services.historique_service import enregistrer_action
from services.date_utils import utcnow  # ← import

router = APIRouter()

# ─── Helpers rôle ─────────────────────────────────────────

def get_role_name(user: Utilisateur) -> str:
    if user.role:
        return user.role.name
    return ""

def can_create_vente(user: Utilisateur) -> bool:
    return get_role_name(user) in ("vendeur", "proprietaire", "admin")

def can_valider_vente(user: Utilisateur) -> bool:
    return get_role_name(user) in ("vendeur", "proprietaire", "admin")

def can_payer_vente(user: Utilisateur) -> bool:
    return get_role_name(user) in ("caissier", "proprietaire", "admin", "vendeur")

def can_annuler_vente(user: Utilisateur) -> bool:
    return get_role_name(user) in ("proprietaire", "admin")

def can_voir_toutes_ventes(user: Utilisateur) -> bool:
    return get_role_name(user) in ("proprietaire", "admin", "caissier")

# ─── Prix helpers (avec gestion du prix de gros) ──────────────────────────

def _prix_boite(produit: Produit, type_prix: str) -> Decimal:
    if type_prix == "wholesale" and produit.prix_gros is not None:
        return Decimal(str(produit.prix_gros))
    return Decimal(str(produit.prix_vente))

def _prix_plaquette(produit: Produit, type_prix: str) -> Decimal:
    base = _prix_boite(produit, type_prix)
    qpb = Decimal(produit.quantite_par_boite or 1)
    return (base / qpb).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

def _prix_piece(produit: Produit, type_prix: str) -> Decimal:
    base = _prix_boite(produit, type_prix)
    qpb = Decimal(produit.quantite_par_boite or 1)
    ppp = Decimal(produit.pieces_par_plaquette or 1)
    return (base / qpb / ppp).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

def _calculer_item(item, produit: Produit) -> dict:
    type_prix = getattr(item, 'type_prix', 'retail')
    if type_prix == "wholesale" and produit.prix_gros is None:
        raise HTTPException(400, f"Le produit '{produit.nom}' n'a pas de prix de gros défini.")

    prix_boite = _prix_boite(produit, type_prix)
    prix_plaquette = _prix_plaquette(produit, type_prix)
    prix_piece = _prix_piece(produit, type_prix)

    nb_b = item.quantite_boite or 0
    nb_p = item.quantite_plaquette or 0
    nb_u = item.quantite_piece or 0

    total = (prix_boite * nb_b) + (prix_plaquette * nb_p) + (prix_piece * nb_u)

    qpb = Decimal(produit.quantite_par_boite or 1)
    ppp = Decimal(produit.pieces_par_plaquette or 1)
    pieces_total = (nb_b * qpb * ppp) + (nb_p * ppp) + nb_u

    if nb_u > 0:
        prix_affiche = prix_piece
    elif nb_p > 0:
        prix_affiche = prix_plaquette
    else:
        prix_affiche = prix_boite

    return {
        "pieces_total": pieces_total,
        "prix_unitaire": prix_affiche,
        "total_ligne": total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
    }

def _generate_code(db: Session) -> str:
    while True:
        code = "VNT-" + "".join(random.choices(string.digits, k=6))
        if not db.query(Vente).filter(Vente.code == code).first():
            return code

# ═══════════════════════════════════════════════════════════
# POST /ventes/commande — Créer une vente (brouillon)
# ═══════════════════════════════════════════════════════════
@router.post("/commande", status_code=201)
def creer_commande(
    payload: CommandeCreate,
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user),
):
    if not can_create_vente(current_user):
        raise HTTPException(403, "Seuls les vendeurs et propriétaires peuvent créer des ventes")
    if not current_user.id_pharmacie:
        raise HTTPException(400, "Aucune pharmacie associée")
    ph_id = current_user.id_pharmacie

    client = None
    if payload.id_client:
        client = db.query(Client).filter(
            Client.id == payload.id_client,
            Client.id_pharmacie == ph_id,
            Client.is_deleted == False,
        ).first()
        if not client:
            raise HTTPException(404, "Client introuvable")

    total = Decimal("0")
    details_data = []

    for item in payload.items:
        produit = db.query(Produit).filter(
            Produit.id == item.id_produit,
            Produit.id_pharmacie == ph_id,
            Produit.is_deleted == False,
        ).first()
        if not produit:
            raise HTTPException(404, f"Produit {item.id_produit} introuvable")

        calc = _calculer_item(item, produit)
        if calc["pieces_total"] <= 0:
            raise HTTPException(400, f"Quantité nulle pour '{produit.nom}'")

        ppp = produit.pieces_par_plaquette or 1
        qpb = produit.quantite_par_boite or 1
        stock_pieces = produit.stock_boite * qpb * ppp
        if stock_pieces < calc["pieces_total"]:
            raise HTTPException(
                400,
                f"Stock insuffisant pour '{produit.nom}' "
                f"(dispo: {stock_pieces} pcs, demandé: {calc['pieces_total']} pcs)",
            )

        total += calc["total_ligne"]
        details_data.append({
            "id_produit":    produit.id,
            "quantite":      calc["pieces_total"],
            "prix_unitaire": calc["prix_unitaire"],
            "total_ligne":   calc["total_ligne"],
        })

    code = _generate_code(db)
    vente = Vente(
        code=code,
        id_pharmacie=ph_id,
        id_client=payload.id_client,
        id_utilisateur=current_user.id,
        client_nom=payload.client_nom or (client.nom if client else None),
        date_vente=utcnow(),
        total=total,
        montant_paye=Decimal("0"),
        reste_a_payer=total,
        statut="brouillon",
    )
    db.add(vente)
    db.flush()

    for d in details_data:
        db.add(DetailVente(id_vente=vente.id, **d))

    db.commit()
    db.refresh(vente)

    enregistrer_action(
        db=db, utilisateur=current_user,
        action="CREATE", entity_type="vente", entity_id=vente.id,
        new_value={"id": vente.id, "code": vente.code, "total": float(vente.total)},
    )

    try:
        from services.notification_v2_service import notifier_vente_creee
        notifier_vente_creee(db, ph_id, vente.id, vente.code, current_user.nom)
    except ImportError:
        pass

    return {
        "message": "Commande créée (brouillon)",
        "vente": {
            "id": vente.id,
            "code": vente.code,
            "total": float(vente.total),
            "statut": vente.statut,
        },
    }

# ═══════════════════════════════════════════════════════════
# POST /ventes/{id}/valider
# ═══════════════════════════════════════════════════════════
@router.post("/{vente_id}/valider")
def valider_commande(
    vente_id: int,
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user),
):
    if not can_valider_vente(current_user):
        raise HTTPException(403, "Action non autorisée pour votre rôle")
    if not current_user.id_pharmacie:
        raise HTTPException(400, "Aucune pharmacie associée")

    vente = db.query(Vente).filter(
        Vente.id == vente_id,
        Vente.id_pharmacie == current_user.id_pharmacie,
        Vente.is_deleted == False,
    ).first()
    if not vente:
        raise HTTPException(404, "Vente introuvable ou non autorisée")
    if vente.statut != "brouillon":
        raise HTTPException(400, f"La vente est déjà '{vente.statut}'")

    details = db.query(DetailVente).filter(DetailVente.id_vente == vente.id).all()
    stock_updates = []

    for d in details:
        produit = db.query(Produit).filter(Produit.id == d.id_produit).first()
        if not produit:
            raise HTTPException(404, f"Produit {d.id_produit} introuvable")
        ppp = produit.pieces_par_plaquette or 1
        qpb = produit.quantite_par_boite or 1
        pieces_par_boite = qpb * ppp
        stock_pieces = produit.stock_boite * pieces_par_boite
        if stock_pieces < d.quantite:
            raise HTTPException(400, f"Stock insuffisant pour '{produit.nom}'")
        ancien = produit.stock_boite
        pieces_restantes = stock_pieces - d.quantite
        produit.stock_boite = pieces_restantes // pieces_par_boite
        produit.stock_total_piece = pieces_restantes
        stock_updates.append({
            "produit": produit.nom,
            "ancien": ancien,
            "nouveau": produit.stock_boite,
        })

    vente.statut     = "confirmee"
    vente.date_vente = utcnow()
    db.commit()

    enregistrer_action(
        db=db, utilisateur=current_user,
        action="UPDATE", entity_type="vente", entity_id=vente.id,
        old_value={"statut": "brouillon"},
        new_value={"statut": "confirmee", "stock_updates": stock_updates},
    )

    try:
        from services.notification_v2_service import (
            notifier_vente_validee,
            notifier_stock_apres_vente,
        )
        ph_id_notif = vente.id_pharmacie
        notifier_vente_validee(db, ph_id_notif, vente.id, vente.code)
        produits_vendus = [{"id_produit": d.id_produit} for d in details]
        notifier_stock_apres_vente(db, ph_id_notif, produits_vendus)
    except ImportError:
        pass

    return {"message": "Vente validée — stocks mis à jour", "stock_updates": stock_updates}

# ═══════════════════════════════════════════════════════════
# POST /ventes/{id}/payer
# ═══════════════════════════════════════════════════════════
@router.post("/{vente_id}/payer")
def payer_vente(
    vente_id: int,
    payload: PaiementCreate,
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user),
):
    if not can_payer_vente(current_user):
        raise HTTPException(403, "Seuls les caissiers et propriétaires peuvent encaisser")

    vente = db.query(Vente).filter(
        Vente.id == vente_id,
        Vente.id_pharmacie == current_user.id_pharmacie,
        Vente.is_deleted == False,
    ).first()
    if not vente:
        raise HTTPException(404, "Vente introuvable")
    if vente.statut == "payee":
        raise HTTPException(400, "Vente déjà payée")
    if vente.statut == "brouillon":
        raise HTTPException(400, "Validez la vente avant d'encaisser")
    if vente.statut == "annulee":
        raise HTTPException(400, "Vente annulée")
    if payload.montant > vente.reste_a_payer:
        raise HTTPException(
            400,
            f"Montant ({float(payload.montant):,.2f}) > reste ({float(vente.reste_a_payer):,.2f})",
        )

    paiement = Paiement(
        id_vente=vente.id,
        montant=payload.montant,
        moyen=payload.moyen,
        date_paiement=utcnow(),
    )
    db.add(paiement)

    vente.montant_paye  += payload.montant
    vente.reste_a_payer  = max(Decimal("0"), vente.total - vente.montant_paye)
    if vente.reste_a_payer == Decimal("0"):
        vente.statut = "payee"

    db.commit()
    db.refresh(vente)

    enregistrer_action(
        db=db, utilisateur=current_user,
        action="CREATE", entity_type="paiement", entity_id=paiement.id,
        new_value={"montant": float(paiement.montant), "moyen": paiement.moyen},
    )
    return {
        "message":     "Paiement enregistré",
        "statut":      vente.statut,
        "montant_paye": float(vente.montant_paye),
        "reste_a_payer": float(vente.reste_a_payer),
    }

# ═══════════════════════════════════════════════════════════
# POST /ventes/{id}/annuler
# ═══════════════════════════════════════════════════════════
@router.post("/{vente_id}/annuler")
def annuler_vente(
    vente_id: int,
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user),
):
    if not can_annuler_vente(current_user):
        raise HTTPException(403, "Seuls les propriétaires peuvent annuler des ventes")

    vente = db.query(Vente).filter(
        Vente.id == vente_id,
        Vente.id_pharmacie == current_user.id_pharmacie,
        Vente.is_deleted == False,
    ).first()
    if not vente:
        raise HTTPException(404, "Vente introuvable")
    if vente.statut not in ["confirmee", "payee"]:
        raise HTTPException(400, "Seules les ventes confirmées/payées peuvent être annulées")

    details = db.query(DetailVente).filter(DetailVente.id_vente == vente.id).all()
    stock_updates = []
    for d in details:
        produit = db.query(Produit).filter(Produit.id == d.id_produit).first()
        if produit:
            ppp = produit.pieces_par_plaquette or 1
            qpb = produit.quantite_par_boite or 1
            pieces_par_boite = qpb * ppp
            produit.stock_total_piece = (produit.stock_total_piece or 0) + d.quantite
            produit.stock_boite = produit.stock_total_piece // pieces_par_boite
            stock_updates.append({"produit": produit.nom, "pieces_restituees": d.quantite})

    old_statut   = vente.statut
    vente.statut = "annulee"
    db.commit()

    enregistrer_action(
        db=db, utilisateur=current_user,
        action="DELETE", entity_type="vente", entity_id=vente.id,
        old_value={"statut": old_statut},
        new_value={"statut": "annulee"},
    )

    try:
        from services.notification_v2_service import notifier_vente_annulee
        notifier_vente_annulee(db, vente.id_pharmacie, vente.id, vente.code)
    except ImportError:
        pass

    return {"message": "Vente annulée — stock restitué", "stock_updates": stock_updates}

# ═══════════════════════════════════════════════════════════
# GET /ventes/ — Liste filtrée selon le rôle
# ═══════════════════════════════════════════════════════════
@router.get("/")
def lister_ventes(
    statut:     Optional[str] = Query(None),
    date_debut: Optional[str] = Query(None),
    date_fin:   Optional[str] = Query(None),
    skip:  int = Query(0),
    limit: int = Query(100),
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user),
):
    if not current_user.id_pharmacie:
        return []

    role = get_role_name(current_user)

    query = db.query(Vente).filter(
        Vente.id_pharmacie == current_user.id_pharmacie,
        Vente.is_deleted == False,
    )

    if role == "vendeur":
        ids_hist = db.query(Historique.entity_id).filter(
            Historique.id_utilisateur == current_user.id,
            Historique.entity_type    == "vente",
            Historique.action         == "CREATE",
        ).subquery()
        query = query.filter(
            or_(
                Vente.id_utilisateur == current_user.id,
                and_(Vente.id_utilisateur == None, Vente.id.in_(ids_hist)),
            )
        )
    elif role == "caissier":
        if not statut:
            query = query.filter(Vente.statut.in_(["confirmee", "payee"]))

    if statut:
        query = query.filter(Vente.statut == statut)
    if date_debut:
        query = query.filter(Vente.date_vente >= date_debut)
    if date_fin:
        query = query.filter(Vente.date_vente <= date_fin)

    return query.order_by(Vente.date_vente.desc()).offset(skip).limit(limit).all()

# ═══════════════════════════════════════════════════════════
# GET /ventes/{id}
# ═══════════════════════════════════════════════════════════
@router.get("/{vente_id}")
def get_vente(
    vente_id: int,
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user),
):
    role = get_role_name(current_user)

    q = db.query(Vente).filter(
        Vente.id == vente_id,
        Vente.id_pharmacie == current_user.id_pharmacie,
        Vente.is_deleted == False,
    )
    if role == "vendeur":
        ids_hist = db.query(Historique.entity_id).filter(
            Historique.id_utilisateur == current_user.id,
            Historique.entity_type    == "vente",
            Historique.action         == "CREATE",
        ).subquery()
        q = q.filter(
            or_(
                Vente.id_utilisateur == current_user.id,
                and_(Vente.id_utilisateur == None, Vente.id.in_(ids_hist)),
            )
        )

    vente = q.first()
    if not vente:
        raise HTTPException(404, "Vente introuvable ou accès refusé")

    details   = db.query(DetailVente).filter(DetailVente.id_vente == vente_id).all()
    paiements = db.query(Paiement).filter(Paiement.id_vente == vente_id).all()
    pharmacie = db.query(Pharmacie).filter(Pharmacie.id == vente.id_pharmacie).first()

    details_enrichis = []
    for d in details:
        produit = db.query(Produit).filter(Produit.id == d.id_produit).first()
        details_enrichis.append({
            "id":            d.id,
            "id_produit":    d.id_produit,
            "produit_nom":   produit.nom if produit else None,
            "quantite":      d.quantite,
            "prix_unitaire": float(d.prix_unitaire),
            "total_ligne":   float(d.total_ligne),
            "prix_boite":    float(produit.prix_vente) if produit else None,
            "prix_plaquette": float(_prix_plaquette(produit, "retail")) if produit else None,
            "prix_piece":    float(_prix_piece(produit, "retail")) if produit else None,
        })

    droits = {
        "peut_valider": can_valider_vente(current_user),
        "peut_payer":   can_payer_vente(current_user),
        "peut_annuler": can_annuler_vente(current_user),
    }

    return {
        "vente":     vente,
        "details":   details_enrichis,
        "paiements": paiements,
        "pharmacie": pharmacie,
        "droits":    droits,
    }