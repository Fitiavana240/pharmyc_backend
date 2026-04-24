# routers/prix_fournisseur.py — Pharmy-C v4.3
# ============================================================
# CRUD des prix d'achat par fournisseur
#
# ENDPOINTS :
#   GET  /prix-fournisseur/produit/{id}
#        → liste tous les fournisseurs qui vendent ce produit
#          avec leur prix, classés du moins cher au plus cher
#
#   GET  /prix-fournisseur/lookup?id_produit=X&id_fournisseur=Y
#        → prix d'achat d'un produit chez un fournisseur précis
#          (retourne aussi le prix recommandé si pas de prix connu)
#
#   PUT  /prix-fournisseur/  (upsert)
#        → créer ou mettre à jour manuellement un prix
#
#   GET  /prix-fournisseur/fournisseur/{id}
#        → tous les produits achetés chez ce fournisseur avec leurs prix
#
#   POST /prix-fournisseur/sync-depuis-bc/{bc_id}
#        → resynchronise les prix depuis un BC réceptionné
#          (utile si la propagation automatique a échoué)
#
# PROPAGATION AUTOMATIQUE :
#   → bons_commande.py/receptionner_commande() appelle
#     _mettre_a_jour_prix_fournisseur() à chaque réception
# ============================================================

from datetime import date, datetime
from decimal import Decimal
from typing import Optional, List

from models.fournisseurs import Fournisseur
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel

from database import get_db
from models.models import Produit, Utilisateur
from routers.auth import get_current_user

router = APIRouter()

ROLES_AUTORISÉS = ("proprietaire", "admin", "gestionnaire_stock")


def _get_role(user: Utilisateur) -> str:
    return user.role.name if user.role else ""


def _check_acces(user: Utilisateur):
    if _get_role(user) not in ROLES_AUTORISÉS:
        raise HTTPException(403, "Accès réservé au propriétaire ou gestionnaire de stock")
    if not user.id_pharmacie:
        raise HTTPException(400, "Aucune pharmacie associée")


# ── Schéma Pydantic ───────────────────────────────────────
class PrixFournisseurUpsert(BaseModel):
    id_produit:    int
    id_fournisseur: int
    prix_ht:       float


# ─────────────────────────────────────────────────────────
# Service interne : mise à jour prix (appelé depuis bons_commande.py)
# ─────────────────────────────────────────────────────────
def _mettre_a_jour_prix_fournisseur(
    db: Session,
    id_produit:    int,
    id_fournisseur: int,
    prix_ht:       float,
    quantite:      int   = 1,
    id_bc:         int   = None,
):
    """
    Crée ou met à jour le prix d'achat d'un produit chez un fournisseur.
    Appelé automatiquement par bons_commande.py à chaque réception.

    Logique :
      - Si première fois → crée la ligne avec prix_min = prix_max = prix_ht
      - Si déjà existant → met à jour prix_ht, recalcule min/max, incrémente nb_commandes
      - Enregistre dans l'historique
      - Propage vers produit.prix_achat (dernier prix tous fournisseurs confondus)
    """
    try:
        from models.prix_fournisseur import PrixAchatFournisseur, HistoriquePrixFournisseur

        pa_dec = Decimal(str(prix_ht))

        # Upsert
        existant = db.query(PrixAchatFournisseur).filter(
            PrixAchatFournisseur.id_produit    == id_produit,
            PrixAchatFournisseur.id_fournisseur == id_fournisseur,
        ).first()

        if existant:
            existant.prix_ht         = pa_dec
            existant.nb_commandes   += 1
            existant.derniere_commande = date.today()
            existant.updated_at      = datetime.utcnow()
            if id_bc:
                existant.id_dernier_bc = id_bc
            # Mettre à jour min/max
            if existant.prix_min_ht is None or pa_dec < existant.prix_min_ht:
                existant.prix_min_ht = pa_dec
            if existant.prix_max_ht is None or pa_dec > existant.prix_max_ht:
                existant.prix_max_ht = pa_dec
        else:
            ligne = PrixAchatFournisseur(
                id_produit      = id_produit,
                id_fournisseur  = id_fournisseur,
                prix_ht         = pa_dec,
                prix_min_ht     = pa_dec,
                prix_max_ht     = pa_dec,
                nb_commandes    = 1,
                derniere_commande = date.today(),
                id_dernier_bc   = id_bc,
            )
            db.add(ligne)

        # Historique
        db.add(HistoriquePrixFournisseur(
            id_produit      = id_produit,
            id_fournisseur  = id_fournisseur,
            id_bon_commande = id_bc,
            prix_ht         = pa_dec,
            quantite        = quantite,
            date_achat      = date.today(),
        ))

        # Propager vers produit.prix_achat (prix de référence global)
        produit = db.query(Produit).filter(Produit.id == id_produit).first()
        if produit:
            setattr(produit, 'prix_achat', pa_dec)

        db.flush()

    except Exception as e:
        # Non bloquant — log uniquement
        print(f"⚠️  _mettre_a_jour_prix_fournisseur: {e}")


def _get_meilleur_prix(db: Session, id_produit: int, id_pharmacie: int) -> dict | None:
    """
    Retourne le fournisseur le moins cher pour un produit donné.
    Utilisé pour le pré-remplissage dans les BC.
    """
    try:
        from models.prix_fournisseur import PrixAchatFournisseur
        from models.fournisseurs import Fournisseur

        # Fournisseurs de cette pharmacie seulement
        fournisseurs_ph = db.query(Fournisseur.id).filter(
            Fournisseur.id_pharmacie == id_pharmacie,
            Fournisseur.is_deleted   == False,
            Fournisseur.actif        == True,
        ).subquery()

        ligne = db.query(PrixAchatFournisseur).filter(
            PrixAchatFournisseur.id_produit    == id_produit,
            PrixAchatFournisseur.id_fournisseur.in_(fournisseurs_ph),
        ).order_by(PrixAchatFournisseur.prix_ht.asc()).first()

        if not ligne:
            return None

        fournisseur = db.query(Fournisseur).filter(
            Fournisseur.id == ligne.id_fournisseur
        ).first()

        return {
            "id_fournisseur":   ligne.id_fournisseur,
            "fournisseur_nom":  fournisseur.nom if fournisseur else None,
            "prix_ht":          float(ligne.prix_ht),
            "derniere_commande": ligne.derniere_commande.isoformat() if ligne.derniere_commande else None,
            "nb_commandes":     ligne.nb_commandes,
        }
    except Exception:
        return None




@router.delete("/{produit_id}/{fournisseur_id}")
def delete_prix_fournisseur(
    produit_id: int,
    fournisseur_id: int,
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user),
):
    _check_acces(current_user)
    # Vérifier que le produit et fournisseur appartiennent à la pharmacie
    produit = db.query(Produit).filter(
        Produit.id == produit_id,
        Produit.id_pharmacie == current_user.id_pharmacie,
    ).first()
    if not produit:
        raise HTTPException(404, "Produit introuvable")
    fournisseur = db.query(Fournisseur).filter(
        Fournisseur.id == fournisseur_id,
        Fournisseur.id_pharmacie == current_user.id_pharmacie,
    ).first()
    if not fournisseur:
        raise HTTPException(404, "Fournisseur introuvable")

    from models.prix_fournisseur import PrixAchatFournisseur
    ligne = db.query(PrixAchatFournisseur).filter(
        PrixAchatFournisseur.id_produit == produit_id,
        PrixAchatFournisseur.id_fournisseur == fournisseur_id,
    ).first()
    if not ligne:
        raise HTTPException(404, "Relation produit-fournisseur introuvable")

    db.delete(ligne)
    db.commit()
    return {"message": "Prix supprimé"}


# ═══════════════════════════════════════════════════════════
# GET /prix-fournisseur/lookup
# ═══════════════════════════════════════════════════════════
@router.get("/lookup")
def lookup_prix(
    id_produit:     int = Query(..., description="ID du produit"),
    id_fournisseur: int = Query(..., description="ID du fournisseur"),
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user),
):
    """
    Retourne le prix d'achat d'un produit chez un fournisseur spécifique.
    Utilisé pour pré-remplir le prix dans un BC ou une entrée stock.

    Logique de fallback :
      1. Prix exact du fournisseur (table prix_achat_fournisseur)
      2. Dernier prix connu chez n'importe quel fournisseur (produit.prix_achat)
      3. None si aucun prix connu
    """
    _check_acces(current_user)

    try:
        from models.prix_fournisseur import PrixAchatFournisseur

        ligne = db.query(PrixAchatFournisseur).filter(
            PrixAchatFournisseur.id_produit    == id_produit,
            PrixAchatFournisseur.id_fournisseur == id_fournisseur,
        ).first()

        produit = db.query(Produit).filter(
            Produit.id           == id_produit,
            Produit.id_pharmacie == current_user.id_pharmacie,
            Produit.is_deleted   == False,
        ).first()
        if not produit:
            raise HTTPException(404, "Produit introuvable")

        if ligne:
            return {
                "id_produit":       id_produit,
                "id_fournisseur":   id_fournisseur,
                "prix_ht":          float(ligne.prix_ht),
                "prix_min_ht":      float(ligne.prix_min_ht) if ligne.prix_min_ht else None,
                "prix_max_ht":      float(ligne.prix_max_ht) if ligne.prix_max_ht else None,
                "nb_commandes":     ligne.nb_commandes,
                "derniere_commande": ligne.derniere_commande.isoformat() if ligne.derniere_commande else None,
                "source":           "fournisseur_exact",
                "produit_nom":      produit.nom,
                "prix_vente":       float(produit.prix_vente),
            }

        # Fallback : prix global du produit
        prix_global = float(getattr(produit, 'prix_achat', 0) or 0)
        return {
            "id_produit":       id_produit,
            "id_fournisseur":   id_fournisseur,
            "prix_ht":          prix_global if prix_global > 0 else None,
            "source":           "prix_global" if prix_global > 0 else "aucun",
            "produit_nom":      produit.nom,
            "prix_vente":       float(produit.prix_vente),
        }

    except ImportError:
        # Table pas encore créée → fallback produit.prix_achat
        produit = db.query(Produit).filter(
            Produit.id == id_produit,
            Produit.id_pharmacie == current_user.id_pharmacie,
        ).first()
        if not produit:
            raise HTTPException(404, "Produit introuvable")
        prix = float(getattr(produit, 'prix_achat', 0) or 0)
        return {
            "id_produit":     id_produit,
            "id_fournisseur": id_fournisseur,
            "prix_ht":        prix if prix > 0 else None,
            "source":         "prix_global",
            "produit_nom":    produit.nom,
            "prix_vente":     float(produit.prix_vente),
        }


# ═══════════════════════════════════════════════════════════
# GET /prix-fournisseur/produit/{id}
# ═══════════════════════════════════════════════════════════
@router.get("/produit/{produit_id}")
def get_prix_par_produit(
    produit_id: int,
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user),
):
    """
    Liste tous les fournisseurs qui ont vendu ce produit,
    avec leurs prix respectifs, du moins cher au plus cher.
    """
    _check_acces(current_user)

    produit = db.query(Produit).filter(
        Produit.id           == produit_id,
        Produit.id_pharmacie == current_user.id_pharmacie,
        Produit.is_deleted   == False,
    ).first()
    if not produit:
        raise HTTPException(404, "Produit introuvable")

    try:
        from models.prix_fournisseur import PrixAchatFournisseur
        from models.fournisseurs import Fournisseur

        lignes = db.query(PrixAchatFournisseur).filter(
            PrixAchatFournisseur.id_produit == produit_id,
        ).order_by(PrixAchatFournisseur.prix_ht.asc()).all()

        resultats = []
        for l in lignes:
            fournisseur = db.query(Fournisseur).filter(
                Fournisseur.id           == l.id_fournisseur,
                Fournisseur.id_pharmacie == current_user.id_pharmacie,
            ).first()
            if not fournisseur:
                continue  # Fournisseur d'une autre pharmacie → ignorer

            pv = float(produit.prix_vente)
            pa = float(l.prix_ht)
            qpb = produit.quantite_par_boite   or 1
            ppp = produit.pieces_par_plaquette or 1
            pv_piece = pv / (qpb * ppp)
            pa_piece = pa / (qpb * ppp)
            marge_pct = round(((pv_piece - pa_piece) / pv_piece * 100) if pv_piece > 0 else 0, 1)

            resultats.append({
                "id_fournisseur":    l.id_fournisseur,
                "fournisseur_nom":   fournisseur.nom,
                "fournisseur_actif": fournisseur.actif,
                "prix_ht":           pa,
                "prix_min_ht":       float(l.prix_min_ht) if l.prix_min_ht else pa,
                "prix_max_ht":       float(l.prix_max_ht) if l.prix_max_ht else pa,
                "marge_pct":         marge_pct,
                "nb_commandes":      l.nb_commandes,
                "derniere_commande": l.derniere_commande.isoformat() if l.derniere_commande else None,
                "est_meilleur_prix": False,  # rempli ci-dessous
            })

        # Marquer le meilleur prix
        if resultats:
            resultats[0]["est_meilleur_prix"] = True  # déjà trié par asc

        return {
            "produit_id":   produit.id,
            "produit_nom":  produit.nom,
            "prix_vente":   float(produit.prix_vente),
            "prix_achat_global": float(getattr(produit, 'prix_achat', 0) or 0),
            "nb_fournisseurs": len(resultats),
            "fournisseurs": resultats,
        }

    except ImportError:
        return {
            "produit_id":   produit.id,
            "produit_nom":  produit.nom,
            "prix_vente":   float(produit.prix_vente),
            "prix_achat_global": float(getattr(produit, 'prix_achat', 0) or 0),
            "nb_fournisseurs": 0,
            "fournisseurs": [],
        }


# ═══════════════════════════════════════════════════════════
# GET /prix-fournisseur/fournisseur/{id}
# ═══════════════════════════════════════════════════════════
# ... (autres imports et code existant)

# ═══════════════════════════════════════════════════════════
# GET /prix-fournisseur/fournisseur/{id}
# ═══════════════════════════════════════════════════════════
@router.get("/fournisseur/{fournisseur_id}")
def get_prix_par_fournisseur(
    fournisseur_id: int,
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user),
):
    """
    Tous les produits achetés chez ce fournisseur avec leurs prix.
    Utile pour le catalogue fournisseur.
    """
    _check_acces(current_user)

    try:
        from models.fournisseurs import Fournisseur
        fournisseur = db.query(Fournisseur).filter(
            Fournisseur.id == fournisseur_id,
            Fournisseur.id_pharmacie == current_user.id_pharmacie,
            Fournisseur.is_deleted == False,
        ).first()
        # ⚠️ MODIFICATION : au lieu de 404, on retourne une structure vide
        if not fournisseur:
            return {
                "fournisseur_id":  fournisseur_id,
                "fournisseur_nom": "Inconnu",
                "nb_produits":     0,
                "produits":        [],
            }
    except ImportError:
        raise HTTPException(500, "Module fournisseurs non disponible")

    try:
        from models.prix_fournisseur import PrixAchatFournisseur

        lignes = db.query(PrixAchatFournisseur).filter(
            PrixAchatFournisseur.id_fournisseur == fournisseur_id,
        ).order_by(PrixAchatFournisseur.prix_ht.asc()).all()

        resultats = []
        for l in lignes:
            produit = db.query(Produit).filter(
                Produit.id == l.id_produit,
                Produit.id_pharmacie == current_user.id_pharmacie,
                Produit.is_deleted == False,
            ).first()
            if not produit:
                continue

            pv = float(produit.prix_vente)
            pa = float(l.prix_ht)
            qpb = produit.quantite_par_boite   or 1
            ppp = produit.pieces_par_plaquette or 1
            pv_piece = pv / (qpb * ppp)
            pa_piece = pa / (qpb * ppp)
            marge_pct = round(((pv_piece - pa_piece) / pv_piece * 100) if pv_piece > 0 else 0, 1)

            resultats.append({
                "id_produit":        produit.id,
                "produit_nom":       produit.nom,
                "categorie":         produit.categorie,
                "prix_ht":           pa,
                "prix_vente":        pv,
                "marge_pct":         marge_pct,
                "stock_actuel":      produit.stock_boite or 0,
                "seuil_alerte":      produit.seuil_alerte or 0,
                "stock_faible":      (produit.stock_total_piece or 0) <= (produit.seuil_alerte or 0),
                "derniere_commande": l.derniere_commande.isoformat() if l.derniere_commande else None,
                "nb_commandes":      l.nb_commandes,
                "prix_min_ht":       float(l.prix_min_ht) if l.prix_min_ht else pa,
                "prix_max_ht":       float(l.prix_max_ht) if l.prix_max_ht else pa,
            })

        return {
            "fournisseur_id":  fournisseur.id,
            "fournisseur_nom": fournisseur.nom,
            "nb_produits":     len(resultats),
            "produits":        resultats,
        }

    except ImportError:
        return {
            "fournisseur_id":  fournisseur_id,
            "fournisseur_nom": fournisseur.nom if 'fournisseur' in locals() else "Inconnu",
            "nb_produits":     0,
            "produits":        [],
        }
# ═══════════════════════════════════════════════════════════
# PUT /prix-fournisseur/ — Upsert manuel
# ═══════════════════════════════════════════════════════════
@router.put("/")
def upsert_prix_fournisseur(
    payload: PrixFournisseurUpsert,
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user),
):
    """
    Crée ou met à jour manuellement le prix d'achat d'un produit
    chez un fournisseur. Ne crée pas d'historique (saisie manuelle).
    """
    _check_acces(current_user)

    # Vérifier produit + fournisseur appartiennent à la pharmacie
    produit = db.query(Produit).filter(
        Produit.id           == payload.id_produit,
        Produit.id_pharmacie == current_user.id_pharmacie,
        Produit.is_deleted   == False,
    ).first()
    if not produit:
        raise HTTPException(404, "Produit introuvable")

    try:
        from models.fournisseurs import Fournisseur
        fournisseur = db.query(Fournisseur).filter(
            Fournisseur.id           == payload.id_fournisseur,
            Fournisseur.id_pharmacie == current_user.id_pharmacie,
            Fournisseur.is_deleted   == False,
        ).first()
        if not fournisseur:
            raise HTTPException(404, "Fournisseur introuvable")
    except ImportError:
        raise HTTPException(500, "Module fournisseurs non disponible")

    # Upsert
    _mettre_a_jour_prix_fournisseur(
        db,
        id_produit     = payload.id_produit,
        id_fournisseur = payload.id_fournisseur,
        prix_ht        = payload.prix_ht,
    )

    try:
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(500, f"Erreur sauvegarde : {e}")

    # Calcul marge
    pv = float(produit.prix_vente)
    pa = payload.prix_ht
    qpb = produit.quantite_par_boite   or 1
    ppp = produit.pieces_par_plaquette or 1
    pv_piece = pv / (qpb * ppp)
    pa_piece = pa / (qpb * ppp)
    marge_pct = round(((pv_piece - pa_piece) / pv_piece * 100) if pv_piece > 0 else 0, 1)

    return {
        "message":           "Prix d'achat mis à jour",
        "id_produit":        produit.id,
        "produit_nom":       produit.nom,
        "id_fournisseur":    fournisseur.id,
        "fournisseur_nom":   fournisseur.nom,
        "prix_ht":           pa,
        "prix_vente":        pv,
        "marge_estimee_pct": marge_pct,
        "prix_revient_piece": round(pa_piece, 2),
    }


# ═══════════════════════════════════════════════════════════
# POST /prix-fournisseur/sync-depuis-bc/{bc_id}
# ═══════════════════════════════════════════════════════════
@router.post("/sync-depuis-bc/{bc_id}")
def sync_depuis_bc(
    bc_id: int,
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user),
):
    """
    Resynchronise les prix depuis un BC réceptionné.
    Utile si la propagation automatique a échoué.
    """
    _check_acces(current_user)

    try:
        from models.fournisseurs import BonCommande, LigneBonCommande

        bc = db.query(BonCommande).filter(
            BonCommande.id           == bc_id,
            BonCommande.id_pharmacie == current_user.id_pharmacie,
            BonCommande.is_deleted   == False,
        ).first()
        if not bc:
            raise HTTPException(404, "Bon de commande introuvable")
        if bc.statut not in ("recu", "partiellement_recu"):
            raise HTTPException(400, "Le BC doit être réceptionné pour synchroniser les prix")

        syncs = []
        for ligne in bc.lignes:
            if ligne.prix_unitaire_ht and float(ligne.prix_unitaire_ht) > 0:
                _mettre_a_jour_prix_fournisseur(
                    db,
                    id_produit     = ligne.id_produit,
                    id_fournisseur = bc.id_fournisseur,
                    prix_ht        = float(ligne.prix_unitaire_ht),
                    quantite       = ligne.quantite_recue or ligne.quantite_commandee,
                    id_bc          = bc.id,
                )
                syncs.append({
                    "id_produit":  ligne.id_produit,
                    "prix_ht":     float(ligne.prix_unitaire_ht),
                })

        db.commit()
        return {
            "message":     f"{len(syncs)} prix synchronisés depuis {bc.code}",
            "syncs":       syncs,
        }

    except ImportError as e:
        raise HTTPException(500, f"Module non disponible : {e}")