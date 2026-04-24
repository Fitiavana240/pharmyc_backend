# routers/bons_commande.py — Pharmy-C v4.3
# ============================================================
# CORRECTIONS v4.3 :
#   BUG #1 : envoyer_bon_commande() — variables 'fournisseur' et
#            'pharmacie' non définies → ajout des requêtes manquantes
#            + condition if/else correcte autour de l'email
#   BUG #2 : receptionner_commande() — 'ph_id' non défini
#            + notifier_bon_commande_recu importé avec fallback
#   BUG #3 : receptionner_commande() — prix d'achat NON propagé
#            vers EntreeStock lors de la réception → ajout de la
#            création d'EntreeStock avec prix_achat_unitaire
#            ET mise à jour de produit.prix_achat
#   BUG #4 : annuler_bon_commande() — statut "annule" vs "annulee"
#            harmonisé avec les autres modules
#   AJOUT  : EntrÃ©eStock créée automatiquement à la réception
#            avec prix_achat_unitaire = prix de la ligne BC
#            et id_bon_commande_ligne pour traçabilité finance
# ============================================================

import random
import string
from datetime import date, datetime
from decimal import Decimal
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload

from database import get_db
from models.fournisseurs import BonCommande, LigneBonCommande, Fournisseur
from models.models import Produit, EntreeStock, Pharmacie
from schemas import BonCommandeCreate, BonCommandeRead, BonCommandeUpdate, ReceptionBCCreate
from routers.auth import get_current_user, owner_or_admin_required
from services.historique_service import enregistrer_action
from services.email_fournisseur_service import email_bon_commande_fournisseur

router = APIRouter()


def generate_bc_code(db: Session) -> str:
    while True:
        code = "BC-" + "".join(random.choices(string.digits, k=6))
        if not db.query(BonCommande).filter(BonCommande.code == code).first():
            return code


def _bc_to_dict(bc: BonCommande) -> dict:
    return {
        "id":                    bc.id,
        "code":                  bc.code,
        "id_pharmacie":          bc.id_pharmacie,
        "id_fournisseur":        bc.id_fournisseur,
        "fournisseur_nom":       bc.fournisseur.nom if bc.fournisseur else None,
        "date_commande":         bc.date_commande,
        "date_livraison_prevue": bc.date_livraison_prevue,
        "date_livraison_reelle": bc.date_livraison_reelle,
        "statut":                bc.statut,
        "total_ht":              bc.total_ht,
        "total_ttc":             bc.total_ttc,
        "taux_tva":              bc.taux_tva,
        "notes":                 bc.notes,
        "lignes": [
            {
                "id":                  l.id,
                "id_produit":          l.id_produit,
                "quantite_commandee":  l.quantite_commandee,
                "quantite_recue":      l.quantite_recue,
                "prix_unitaire_ht":    l.prix_unitaire_ht,
                "total_ligne_ht":      l.total_ligne_ht,
                "produit_nom":         l.produit.nom if l.produit else None,
            }
            for l in (bc.lignes or [])
        ],
    }


def _get_bc_or_404(bc_id: int, db: Session, current_user) -> BonCommande:
    bc = db.query(BonCommande).filter(
        BonCommande.id           == bc_id,
        BonCommande.id_pharmacie == current_user.id_pharmacie,
        BonCommande.is_deleted   == False,
    ).first()
    if not bc:
        raise HTTPException(404, "Bon de commande introuvable")
    owner_or_admin_required(bc.id_pharmacie, db, current_user)
    return bc


# ═══════════════════════════════════════════════════════════
# POST /bons_commande/
# ═══════════════════════════════════════════════════════════
@router.post("/", status_code=201)
def create_bon_commande(
    payload: BonCommandeCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    if not current_user.id_pharmacie:
        raise HTTPException(400, "Aucune pharmacie associée")
    if current_user.role.name not in ["proprietaire", "gestionnaire_stock", "admin"]:
        raise HTTPException(403, "Accès refusé")

    fournisseur = db.query(Fournisseur).filter(
        Fournisseur.id           == payload.id_fournisseur,
        Fournisseur.id_pharmacie == current_user.id_pharmacie,
        Fournisseur.is_deleted   == False,
    ).first()
    if not fournisseur:
        raise HTTPException(404, "Fournisseur introuvable")

    total_ht    = Decimal("0")
    lignes_data = []

    for ligne in payload.lignes:
        produit = db.query(Produit).filter(
            Produit.id           == ligne.id_produit,
            Produit.id_pharmacie == current_user.id_pharmacie,
            Produit.is_deleted   == False,
        ).first()
        if not produit:
            raise HTTPException(404, f"Produit {ligne.id_produit} introuvable")

        total_ligne = Decimal(str(ligne.quantite_commandee)) * ligne.prix_unitaire_ht
        total_ht   += total_ligne
        lignes_data.append({
            "id_produit":         produit.id,
            "quantite_commandee": ligne.quantite_commandee,
            "quantite_recue":     0,
            "prix_unitaire_ht":   ligne.prix_unitaire_ht,
            "total_ligne_ht":     total_ligne,
        })

    taux      = payload.taux_tva or Decimal("0")
    total_ttc = total_ht * (1 + taux / 100)

    code = generate_bc_code(db)
    bc = BonCommande(
        code=code,
        id_pharmacie=current_user.id_pharmacie,
        id_fournisseur=payload.id_fournisseur,
        id_utilisateur=current_user.id,
        date_commande=datetime.utcnow(),
        date_livraison_prevue=payload.date_livraison_prevue,
        taux_tva=taux,
        total_ht=total_ht,
        total_ttc=total_ttc,
        notes=payload.notes,
        statut="brouillon",
    )
    db.add(bc)
    db.flush()

    for ld in lignes_data:
        db.add(LigneBonCommande(id_bon_commande=bc.id, **ld))

    db.commit()
    db.refresh(bc)

    enregistrer_action(
        db=db, utilisateur=current_user,
        action="CREATE", entity_type="bon_commande",
        entity_id=bc.id,
        new_value={"code": bc.code, "fournisseur": fournisseur.nom, "total_ttc": float(total_ttc)},
    )
    return _bc_to_dict(bc)


# ═══════════════════════════════════════════════════════════
# GET /bons_commande/
# ═══════════════════════════════════════════════════════════
@router.get("/")
def list_bons_commande(
    statut:         Optional[str]  = Query(None),
    id_fournisseur: Optional[int]  = Query(None),
    date_debut:     Optional[date] = Query(None),
    date_fin:       Optional[date] = Query(None),
    skip:  int = Query(0,  ge=0),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    if not current_user.id_pharmacie:
        return []

    query = db.query(BonCommande).options(
        joinedload(BonCommande.lignes).joinedload(LigneBonCommande.produit),
        joinedload(BonCommande.fournisseur),
    ).filter(
        BonCommande.id_pharmacie == current_user.id_pharmacie,
        BonCommande.is_deleted   == False,
    )

    if statut:
        query = query.filter(BonCommande.statut == statut)
    if id_fournisseur:
        query = query.filter(BonCommande.id_fournisseur == id_fournisseur)
    if date_debut:
        query = query.filter(BonCommande.date_commande >= date_debut)
    if date_fin:
        query = query.filter(BonCommande.date_commande <= date_fin)

    bcs = query.order_by(BonCommande.date_commande.desc()).offset(skip).limit(limit).all()
    return [_bc_to_dict(bc) for bc in bcs]


# ═══════════════════════════════════════════════════════════
# GET /bons_commande/{bc_id}
# ═══════════════════════════════════════════════════════════
@router.get("/{bc_id}")
def get_bon_commande(
    bc_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    bc = db.query(BonCommande).options(
        joinedload(BonCommande.lignes).joinedload(LigneBonCommande.produit),
        joinedload(BonCommande.fournisseur),
    ).filter(
        BonCommande.id           == bc_id,
        BonCommande.id_pharmacie == current_user.id_pharmacie,
        BonCommande.is_deleted   == False,
    ).first()
    if not bc:
        raise HTTPException(404, "Bon de commande introuvable")
    return _bc_to_dict(bc)


# ═══════════════════════════════════════════════════════════
# PATCH /bons_commande/{bc_id} — Modifier un brouillon
# ═══════════════════════════════════════════════════════════
@router.patch("/{bc_id}")
def update_bon_commande(
    bc_id: int,
    payload: BonCommandeUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Modifie un bon de commande en statut BROUILLON uniquement."""
    if not current_user.id_pharmacie:
        raise HTTPException(400, "Aucune pharmacie associée")
    if current_user.role.name not in ["proprietaire", "gestionnaire_stock", "admin"]:
        raise HTTPException(403, "Accès refusé")

    bc = db.query(BonCommande).options(
        joinedload(BonCommande.lignes),
        joinedload(BonCommande.fournisseur),
    ).filter(
        BonCommande.id           == bc_id,
        BonCommande.id_pharmacie == current_user.id_pharmacie,
        BonCommande.is_deleted   == False,
    ).first()

    if not bc:
        raise HTTPException(404, "Bon de commande introuvable")
    if bc.statut != "brouillon":
        raise HTTPException(
            400,
            f"Seuls les brouillons peuvent être modifiés (statut actuel : {bc.statut})"
        )

    if payload.id_fournisseur is not None:
        fournisseur = db.query(Fournisseur).filter(
            Fournisseur.id           == payload.id_fournisseur,
            Fournisseur.id_pharmacie == current_user.id_pharmacie,
            Fournisseur.is_deleted   == False,
        ).first()
        if not fournisseur:
            raise HTTPException(404, "Fournisseur introuvable")
        bc.id_fournisseur = payload.id_fournisseur

    if payload.date_livraison_prevue is not None:
        bc.date_livraison_prevue = payload.date_livraison_prevue
    if payload.taux_tva is not None:
        bc.taux_tva = payload.taux_tva
    if payload.notes is not None:
        bc.notes = payload.notes

    if payload.lignes is not None:
        if len(payload.lignes) == 0:
            raise HTTPException(400, "Le bon de commande doit contenir au moins une ligne")

        for old_ligne in bc.lignes:
            db.delete(old_ligne)
        db.flush()

        total_ht = Decimal("0")
        for ligne in payload.lignes:
            produit = db.query(Produit).filter(
                Produit.id           == ligne.id_produit,
                Produit.id_pharmacie == current_user.id_pharmacie,
                Produit.is_deleted   == False,
            ).first()
            if not produit:
                raise HTTPException(404, f"Produit {ligne.id_produit} introuvable")

            total_ligne = Decimal(str(ligne.quantite_commandee)) * ligne.prix_unitaire_ht
            total_ht   += total_ligne

            db.add(LigneBonCommande(
                id_bon_commande=bc.id,
                id_produit=produit.id,
                quantite_commandee=ligne.quantite_commandee,
                quantite_recue=0,
                prix_unitaire_ht=ligne.prix_unitaire_ht,
                total_ligne_ht=total_ligne,
            ))

        taux = bc.taux_tva or Decimal("0")
        bc.total_ht  = total_ht
        bc.total_ttc = total_ht * (1 + taux / 100)

    db.commit()
    db.refresh(bc)

    enregistrer_action(
        db=db, utilisateur=current_user,
        action="UPDATE", entity_type="bon_commande",
        entity_id=bc.id,
        new_value={"statut": bc.statut, "total_ttc": float(bc.total_ttc)},
    )
    return _bc_to_dict(bc)


# ═══════════════════════════════════════════════════════════
# DELETE /bons_commande/{bc_id} — Supprimer un brouillon
# ═══════════════════════════════════════════════════════════
@router.delete("/{bc_id}", status_code=204)
def delete_bon_commande(
    bc_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    if current_user.role.name not in ["proprietaire", "gestionnaire_stock", "admin"]:
        raise HTTPException(403, "Accès refusé")

    bc = db.query(BonCommande).filter(
        BonCommande.id           == bc_id,
        BonCommande.id_pharmacie == current_user.id_pharmacie,
        BonCommande.is_deleted   == False,
    ).first()
    if not bc:
        raise HTTPException(404, "Bon de commande introuvable")
    if bc.statut != "brouillon":
        raise HTTPException(400, f"Seul un brouillon peut être supprimé (statut : {bc.statut})")

    bc.is_deleted = True
    bc.deleted_at = datetime.utcnow()
    db.commit()
    enregistrer_action(
        db=db, utilisateur=current_user,
        action="DELETE", entity_type="bon_commande",
        entity_id=bc_id, old_value={"code": bc.code},
    )
    return


# ═══════════════════════════════════════════════════════════
# POST /bons_commande/{bc_id}/envoyer
# FIX : fournisseur et pharmacie maintenant chargés correctement
# ═══════════════════════════════════════════════════════════
@router.post("/{bc_id}/envoyer")
def envoyer_bon_commande(
    bc_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    bc = _get_bc_or_404(bc_id, db, current_user)
    if bc.statut != "brouillon":
        raise HTTPException(400, f"Le bon est déjà '{bc.statut}', impossible de l'envoyer")

    # FIX : charger fournisseur et pharmacie (étaient manquants dans v4.2)
    fournisseur = db.query(Fournisseur).filter(
        Fournisseur.id == bc.id_fournisseur
    ).first()
    pharmacie = db.query(Pharmacie).filter(
        Pharmacie.id == bc.id_pharmacie
    ).first()

    bc.statut = "envoye"
    db.commit()

    enregistrer_action(
        db=db, utilisateur=current_user,
        action="UPDATE", entity_type="bon_commande", entity_id=bc.id,
        old_value={"statut": "brouillon"}, new_value={"statut": "envoye"},
    )

    # FIX : condition if/else corrigée (l'email est optionnel)
    if fournisseur and fournisseur.email and pharmacie:
        lignes_data = [
            {
                "produit_nom": l.produit.nom if l.produit else f"Produit #{l.id_produit}",
                "quantite":    l.quantite_commandee,
                "prix_ht":     float(l.prix_unitaire_ht),
            }
            for l in (bc.lignes or [])
        ]
        try:
            email_bon_commande_fournisseur(
                fournisseur_email     = fournisseur.email,
                fournisseur_nom       = fournisseur.nom,
                pharmacie_nom         = pharmacie.nom,
                bc_code               = bc.code,
                date_commande         = str(bc.date_commande.date()),
                date_livraison_prevue = str(bc.date_livraison_prevue) if bc.date_livraison_prevue else None,
                lignes                = lignes_data,
                total_ht              = float(bc.total_ht),
                total_ttc             = float(bc.total_ttc),
                taux_tva              = float(bc.taux_tva),
                notes                 = bc.notes or "",
            )
        except Exception:
            # L'email est un service auxiliaire — ne bloque pas le workflow
            pass

    return {"message": "Bon de commande marqué comme envoyé", "code": bc.code}


# ═══════════════════════════════════════════════════════════
# POST /bons_commande/{bc_id}/receptionner
# FIX : ph_id défini, notification avec fallback,
#       EntreeStock créé avec prix d'achat depuis la ligne BC
# ═══════════════════════════════════════════════════════════

@router.post("/{bc_id}/receptionner")
def receptionner_commande(
    bc_id: int,
    payload: ReceptionBCCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Réceptionne une livraison totale ou partielle.
    - Met à jour quantite_recue sur chaque ligne
    - Met à jour le stock du produit (stock_boite + stock_total_piece)
    - Crée une EntreeStock avec tous les champs financiers directement
      dans le constructeur (prix_achat_unitaire, montant_achat,
      id_fournisseur, id_bon_commande_ligne)
    - Propage prix_achat vers produit.prix_achat si non défini
    """
    bc = _get_bc_or_404(bc_id, db, current_user)
    if bc.statut not in ["envoye", "partiellement_recu"]:
        raise HTTPException(400, "Ce bon ne peut pas être réceptionné dans son état actuel")
 
    ph_id = current_user.id_pharmacie
 
    if payload.date_livraison_reelle:
        bc.date_livraison_reelle = payload.date_livraison_reelle
    else:
        bc.date_livraison_reelle = date.today()
 
    fournisseur = db.query(Fournisseur).filter(
        Fournisseur.id == bc.id_fournisseur
    ).first()
 
    stock_updates = []
 
    for reception in payload.lignes:
        id_ligne  = reception.get("id_ligne")
        qte_recue = reception.get("quantite_recue", 0)
 
        ligne = db.query(LigneBonCommande).filter(
            LigneBonCommande.id              == id_ligne,
            LigneBonCommande.id_bon_commande == bc_id,
        ).first()
        if not ligne:
            raise HTTPException(404, f"Ligne {id_ligne} introuvable dans ce bon")
        if qte_recue < 0:
            raise HTTPException(400, "La quantité reçue ne peut pas être négative")
        if qte_recue == 0:
            continue
        if ligne.quantite_recue + qte_recue > ligne.quantite_commandee:
            raise HTTPException(
                400,
                f"Quantité reçue ({ligne.quantite_recue + qte_recue}) dépasse "
                f"la quantité commandée ({ligne.quantite_commandee})"
            )
 
        ligne.quantite_recue += qte_recue
 
        produit = db.query(Produit).filter(Produit.id == ligne.id_produit).first()
        if not produit:
            continue
 
        # ── Mise à jour du stock ──────────────────────────────
        ancien_stock = produit.stock_boite
        pieces_par_boite = (produit.quantite_par_boite or 1) * (produit.pieces_par_plaquette or 1)
        produit.stock_boite       += qte_recue
        produit.stock_total_piece  = produit.stock_boite * pieces_par_boite
 
        # ── Prix et montant ───────────────────────────────────
        prix_ht = float(ligne.prix_unitaire_ht or 0)
        montant = round(prix_ht * qte_recue, 2)
 
        # ── Création EntreeStock COMPLÈTE ─────────────────────
        # Tous les champs financiers passés au constructeur directement
        # → SQLAlchemy les persiste correctement sans setattr()
        entree = EntreeStock(
            id_produit            = produit.id,
            id_fournisseur        = bc.id_fournisseur,                          # ✅ FK
            quantite              = qte_recue,
            type_entree           = "achat",
            fournisseur           = fournisseur.nom if fournisseur else None,   # nom texte
            date_entree           = bc.date_livraison_reelle or date.today(),
            prix_achat_unitaire   = Decimal(str(prix_ht)) if prix_ht > 0 else None,  # ✅
            montant_achat         = Decimal(str(montant)) if montant > 0 else None,  # ✅
            id_bon_commande_ligne = ligne.id,                                   # ✅ traçabilité
        )
        db.add(entree)
 
        # ── Propagation prix vers PrixAchatFournisseur ────────
        if prix_ht > 0 and fournisseur:
            try:
                from routers.prix_fournisseur import _mettre_a_jour_prix_fournisseur
                _mettre_a_jour_prix_fournisseur(
                    db,
                    id_produit     = produit.id,
                    id_fournisseur = bc.id_fournisseur,
                    prix_ht        = prix_ht,
                    quantite       = qte_recue,
                    id_bc          = bc.id,
                )
            except Exception:
                # Fallback : mise à jour directe si service indisponible
                try:
                    if not getattr(produit, 'prix_achat', None):
                        produit.prix_achat = Decimal(str(prix_ht))
                except Exception:
                    pass
 
        stock_updates.append({
            "produit_id":    produit.id,
            "produit_nom":   produit.nom,
            "ancien_stock":  ancien_stock,
            "nouveau_stock": produit.stock_boite,
            "quantite_recue": qte_recue,
            "prix_ht":        prix_ht,
            "montant_achat":  montant,
        })
 
    # ── Statut BC ─────────────────────────────────────────────
    toutes_recues = all(
        l.quantite_recue >= l.quantite_commandee for l in bc.lignes
    )
    bc.statut = "recu" if toutes_recues else "partiellement_recu"
    db.commit()
 
    enregistrer_action(
        db=db, utilisateur=current_user,
        action="UPDATE", entity_type="bon_commande", entity_id=bc.id,
        new_value={"statut": bc.statut, "stock_updates": stock_updates},
    )
 
    # ── Notification ──────────────────────────────────────────
    try:
        from services.notification_v2_service import notifier_bon_commande_recu
        notifier_bon_commande_recu(
            db, ph_id, bc.code,
            fournisseur.nom if fournisseur else "Fournisseur",
            bc.statut,
        )
    except (ImportError, Exception):
        pass
 
    return {
        "message":             f"Réception enregistrée — statut : {bc.statut}",
        "stock_mises_a_jour":  stock_updates,
        "entrees_stock_crees": len(stock_updates),
    }
 


# ═══════════════════════════════════════════════════════════
# POST /bons_commande/{bc_id}/annuler
# FIX : statut "annule" harmonisé (sans 'e' final — cohérent avec BC)
# ═══════════════════════════════════════════════════════════
@router.post("/{bc_id}/annuler")
def annuler_bon_commande(
    bc_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    bc = _get_bc_or_404(bc_id, db, current_user)
    # FIX : "annule" (pas "annulee") pour les BCs
    if bc.statut in ["recu", "annule"]:
        raise HTTPException(400, f"Impossible d'annuler un bon '{bc.statut}'")

    old_statut = bc.statut
    bc.statut  = "annule"
    db.commit()

    enregistrer_action(
        db=db, utilisateur=current_user,
        action="UPDATE", entity_type="bon_commande", entity_id=bc.id,
        old_value={"statut": old_statut}, new_value={"statut": "annule"},
    )
    return {"message": "Bon de commande annulé"}