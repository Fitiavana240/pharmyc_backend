# routers/rapports.py — COMPLET avec accès par rôle
#
# RÈGLES :
#   proprietaire + admin → tout (CA, top produits, top vendeurs, clôture)
#   gestionnaire_stock   → stock valorisé + mouvements
#   vendeur/caissier     → dashboard limité (ses propres stats)
#
# FIX v4.2 :
#   dashboard() utilise une PLAGE DATETIME au lieu de func.date()
#   pour filtrer les ventes du jour → robuste aux problèmes de timezone
#   et au fait que date_vente était la date du brouillon (pas de la validation)
# ============================================================

from operator import or_
import os
from datetime import date, datetime, timedelta
from typing import Optional
from decimal import Decimal
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, and_
from sqlalchemy.orm import Session

from database import get_db
from models.models import (
    Vente, DetailVente, Produit, Pharmacie,
    Utilisateur, Paiement, EntreeStock, SortieStock,
)
from routers.auth import get_current_user

router = APIRouter()


def _get_role(user: Utilisateur) -> str:
    return user.role.name if user.role else ""


def _require_proprietaire(user: Utilisateur):
    if _get_role(user) not in ("proprietaire", "admin"):
        raise HTTPException(403, "Accès réservé au propriétaire")


def _require_stock_access(user: Utilisateur):
    if _get_role(user) not in ("proprietaire", "gestionnaire_stock", "admin"):
        raise HTTPException(403, "Accès réservé au gestionnaire de stock ou propriétaire")


# ═══════════════════════════════════════════════════════════
# GET /rapports/dashboard
# FIX v4.2 : plage datetime robuste au lieu de func.date()
# ═══════════════════════════════════════════════════════════
@router.get("/dashboard")
def dashboard(
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user),
):
    if not current_user.id_pharmacie:
        raise HTTPException(400, "Aucune pharmacie associée")

    ph_id = current_user.id_pharmacie
    role  = _get_role(current_user)

    # ── Calcul de la plage "aujourd'hui" en UTC ───────────────
    # IMPORTANT : date_vente est stocké via datetime.utcnow() → UTC pur
    # La plage doit donc aussi être en UTC, pas en heure locale.
    # On calcule minuit UTC d'aujourd'hui directement.
    from datetime import datetime as _dt
    now_utc      = _dt.utcnow()
    debut_jour   = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    fin_jour     = debut_jour + timedelta(days=1)
    debut_mois   = debut_jour.replace(day=1)

    # ── Filtre de base ──────────────────────────────────────
    base_filter = and_(
        Vente.id_pharmacie == ph_id,
        Vente.is_deleted   == False,
        Vente.statut.in_(["confirmee", "payee"]),
    )

    # ── Filtre selon le rôle ─────────────────────────────────
    # Vendeur : SES ventes uniquement
    #   Priorité 1 : id_utilisateur = current_user.id (ventes récentes)
    #   Fallback   : ventes créées via l'historique (ventes avant le fix)
    #                où id_utilisateur est NULL mais tracé dans historique
    if role == "vendeur":
        from models.models import Historique
        # IDs des ventes créées par ce vendeur selon l'historique
        ids_via_historique = db.query(Historique.entity_id).filter(
            Historique.id_utilisateur == current_user.id,
            Historique.entity_type    == "vente",
            Historique.action         == "CREATE",
            Historique.id_pharmacie   == ph_id,
        ).subquery()

        filtre_vendeur = and_(
            base_filter,
            or_(
                Vente.id_utilisateur == current_user.id,
                and_(
                    Vente.id_utilisateur == None,   # ventes anciennes sans id_utilisateur
                    Vente.id.in_(ids_via_historique),
                ),
            ),
        )
        filtre_jour = and_(filtre_vendeur, Vente.date_vente >= debut_jour, Vente.date_vente < fin_jour)
        filtre_mois = and_(filtre_vendeur, Vente.date_vente >= debut_mois)
    else:
        filtre_jour = and_(base_filter, Vente.date_vente >= debut_jour, Vente.date_vente < fin_jour)
        filtre_mois = and_(base_filter, Vente.date_vente >= debut_mois)

    # ── Requêtes ────────────────────────────────────────────
    ca_jour        = db.query(func.sum(Vente.total)).filter(filtre_jour).scalar() or 0
    nb_ventes_jour = db.query(func.count(Vente.id)).filter(filtre_jour).scalar() or 0
    ca_mois        = db.query(func.sum(Vente.total)).filter(filtre_mois).scalar() or 0
    nb_ventes_mois = db.query(func.count(Vente.id)).filter(filtre_mois).scalar() or 0

    ticket_moyen_jour = (float(ca_jour) / nb_ventes_jour) if nb_ventes_jour > 0 else 0

    result = {
        "ca_jour":           float(ca_jour),
        "ca_mois":           float(ca_mois),
        "nb_ventes_jour":    nb_ventes_jour,
        "nb_ventes_mois":    nb_ventes_mois,
        "ticket_moyen_jour": round(ticket_moyen_jour, 2),
    }

    # ── Stock (propriétaire + gestionnaire + admin) ──────────
    if role in ("proprietaire", "gestionnaire_stock", "admin"):
        today      = date.today()
        limite_exp = today + timedelta(days=30)
        result["stock_faible"] = db.query(func.count(Produit.id)).filter(
            Produit.id_pharmacie == ph_id,
            Produit.is_deleted   == False,
            Produit.stock_total_piece <= Produit.seuil_alerte,
            Produit.stock_total_piece >  0,
        ).scalar() or 0
        result["produits_rupture"] = db.query(func.count(Produit.id)).filter(
            Produit.id_pharmacie == ph_id,
            Produit.is_deleted   == False,
            Produit.stock_total_piece == 0,
        ).scalar() or 0
        result["produits_expiration_proche"] = db.query(func.count(Produit.id)).filter(
            Produit.id_pharmacie == ph_id,
            Produit.is_deleted   == False,
            Produit.date_expiration != None,
            Produit.date_expiration <= limite_exp,
        ).scalar() or 0

    # ── Commandes en attente (propriétaire + caissier + admin) ─
    if role in ("proprietaire", "caissier", "admin"):
        result["nb_ventes_confirmees"] = db.query(func.count(Vente.id)).filter(
            Vente.id_pharmacie == ph_id,
            Vente.is_deleted   == False,
            Vente.statut       == "confirmee",
        ).scalar() or 0

    return result


# ═══════════════════════════════════════════════════════════
# GET /rapports/chiffre-affaires — Propriétaire seulement
# ═══════════════════════════════════════════════════════════
@router.get("/chiffre-affaires")
def chiffre_affaires(
    date_debut:  str = Query(...),
    date_fin:    str = Query(...),
    grouper_par: str = Query("jour"),   # jour | semaine | mois
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user),
):
    _require_proprietaire(current_user)
    ph_id = current_user.id_pharmacie

    ventes = db.query(Vente).filter(
        Vente.id_pharmacie == ph_id,
        Vente.is_deleted   == False,
        Vente.statut.in_(["confirmee", "payee"]),
        Vente.date_vente >= date_debut,
        Vente.date_vente <= date_fin + " 23:59:59",
    ).all()

    groupes: dict = defaultdict(lambda: {"ca": Decimal("0"), "nb_ventes": 0})

    for v in ventes:
        d = v.date_vente.date() if hasattr(v.date_vente, "date") else v.date_vente
        if grouper_par == "mois":
            cle = d.strftime("%Y-%m")
        elif grouper_par == "semaine":
            cle = f"{d.isocalendar().year}-S{d.isocalendar().week:02d}"
        else:
            cle = str(d)
        groupes[cle]["ca"]       += v.total or Decimal("0")
        groupes[cle]["nb_ventes"] += 1

    series = sorted(
        [{"periode": k, "ca": float(v["ca"]), "nb_ventes": v["nb_ventes"]} for k, v in groupes.items()],
        key=lambda x: x["periode"],
    )

    ca_total = sum(s["ca"] for s in series)

    # Comparaison période précédente
    try:
        d1    = datetime.strptime(date_debut, "%Y-%m-%d").date()
        d2    = datetime.strptime(date_fin,   "%Y-%m-%d").date()
        duree = (d2 - d1).days + 1
        d1_prec = d1 - timedelta(days=duree)
        d2_prec = d2 - timedelta(days=duree)
        ca_prec = db.query(func.sum(Vente.total)).filter(
            Vente.id_pharmacie == ph_id,
            Vente.is_deleted   == False,
            Vente.statut.in_(["confirmee", "payee"]),
            Vente.date_vente >= str(d1_prec),
            Vente.date_vente <= str(d2_prec) + " 23:59:59",
        ).scalar() or 0
        evolution_pct = round(
            ((ca_total - float(ca_prec)) / float(ca_prec) * 100) if ca_prec > 0 else 0, 1
        )
    except Exception:
        ca_prec = 0
        evolution_pct = 0

    return {
        "ca_total":       ca_total,
        "ca_precedent":   float(ca_prec),
        "evolution_pct":  evolution_pct,
        "nb_ventes_total": sum(s["nb_ventes"] for s in series),
        "series":         series,
    }


# ═══════════════════════════════════════════════════════════
# GET /rapports/produits-top — Propriétaire seulement
# ═══════════════════════════════════════════════════════════
@router.get("/produits-top")
def top_produits(
    date_debut: str = Query(...),
    date_fin:   str = Query(...),
    top_n:      int = Query(10),
    critere:    str = Query("ca"),   # ca | quantite
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user),
):
    _require_proprietaire(current_user)
    ph_id = current_user.id_pharmacie

    rows = db.query(
        Produit.id,
        Produit.nom,
        Produit.categorie,
        func.sum(DetailVente.quantite).label("quantite_vendue"),
        func.sum(DetailVente.total_ligne).label("ca"),
        func.count(DetailVente.id).label("nb_ventes"),
    ).join(
        DetailVente, DetailVente.id_produit == Produit.id
    ).join(
        Vente, Vente.id == DetailVente.id_vente
    ).filter(
        Produit.id_pharmacie == ph_id,
        Produit.is_deleted   == False,
        Vente.is_deleted     == False,
        Vente.statut.in_(["confirmee", "payee"]),
        Vente.date_vente >= date_debut,
        Vente.date_vente <= date_fin + " 23:59:59",
    ).group_by(Produit.id, Produit.nom, Produit.categorie)

    if critere == "quantite":
        rows = rows.order_by(func.sum(DetailVente.quantite).desc())
    else:
        rows = rows.order_by(func.sum(DetailVente.total_ligne).desc())

    rows = rows.limit(top_n).all()

    return {
        "critere": critere,
        "periode": {"debut": date_debut, "fin": date_fin},
        "produits": [
            {
                "id":              r.id,
                "nom":             r.nom,
                "categorie":       r.categorie or "—",
                "quantite_vendue": int(r.quantite_vendue or 0),
                "ca":              float(r.ca or 0),
                "nb_ventes":       int(r.nb_ventes or 0),
            }
            for r in rows
        ],
    }


# ═══════════════════════════════════════════════════════════
# GET /rapports/top-vendeurs — Propriétaire seulement
# ═══════════════════════════════════════════════════════════
@router.get("/top-vendeurs")
def top_vendeurs(
    date_debut: str = Query(...),
    date_fin:   str = Query(...),
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user),
):
    _require_proprietaire(current_user)
    ph_id = current_user.id_pharmacie

    rows = db.query(
        Utilisateur.id,
        Utilisateur.nom,
        func.count(Vente.id).label("nb_ventes"),
        func.sum(Vente.total).label("ca"),
        func.sum(Vente.montant_paye).label("encaisse"),
        func.avg(Vente.total).label("ticket_moyen"),
    ).join(
        Vente, Vente.id_utilisateur == Utilisateur.id
    ).filter(
        Vente.id_pharmacie == ph_id,
        Vente.is_deleted   == False,
        Vente.statut.in_(["confirmee", "payee"]),
        Vente.date_vente >= date_debut,
        Vente.date_vente <= date_fin + " 23:59:59",
    ).group_by(Utilisateur.id, Utilisateur.nom
    ).order_by(func.sum(Vente.total).desc()).all()

    return {
        "periode": {"debut": date_debut, "fin": date_fin},
        "vendeurs": [
            {
                "id":           r.id,
                "nom":          r.nom,
                "nb_ventes":    int(r.nb_ventes or 0),
                "ca":           float(r.ca or 0),
                "encaisse":     float(r.encaisse or 0),
                "ticket_moyen": round(float(r.ticket_moyen or 0), 2),
            }
            for r in rows
        ],
    }


# ═══════════════════════════════════════════════════════════
# GET /rapports/clients-top — Propriétaire seulement
# ═══════════════════════════════════════════════════════════
@router.get("/clients-top")
def top_clients(
    date_debut: str = Query(...),
    date_fin:   str = Query(...),
    top_n: int = Query(10),
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user),
):
    _require_proprietaire(current_user)
    ph_id = current_user.id_pharmacie

    from models.models import Client
    rows = db.query(
        Client.id,
        Client.nom,
        Client.telephone,
        func.count(Vente.id).label("nb_ventes"),
        func.sum(Vente.total).label("ca_total"),
        func.sum(Vente.reste_a_payer).label("impaye_total"),
    ).join(
        Vente, Vente.id_client == Client.id
    ).filter(
        Vente.id_pharmacie == ph_id,
        Vente.is_deleted   == False,
        Client.is_deleted  == False,
        Vente.statut.in_(["confirmee", "payee"]),
        Vente.date_vente >= date_debut,
        Vente.date_vente <= date_fin + " 23:59:59",
    ).group_by(Client.id, Client.nom, Client.telephone
    ).order_by(func.sum(Vente.total).desc()
    ).limit(top_n).all()

    return {
        "clients": [
            {
                "id":           r.id,
                "nom":          r.nom,
                "telephone":    r.telephone,
                "nb_ventes":    int(r.nb_ventes or 0),
                "ca_total":     float(r.ca_total or 0),
                "impaye_total": float(r.impaye_total or 0),
            }
            for r in rows
        ]
    }


# ═══════════════════════════════════════════════════════════
# GET /rapports/stock-valorise — Propriétaire + gestionnaire
# ═══════════════════════════════════════════════════════════
@router.get("/stock-valorise")
def stock_valorise(
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user),
):
    _require_stock_access(current_user)
    ph_id  = current_user.id_pharmacie
    today  = date.today()
    limite = today + timedelta(days=30)

    produits = db.query(Produit).filter(
        Produit.id_pharmacie == ph_id,
        Produit.is_deleted   == False,
    ).all()

    valeur_totale = sum(
        (p.prix_vente or 0) * (p.stock_boite or 0) for p in produits
    )

    cats: dict = defaultdict(lambda: {"nb_produits": 0, "valeur": 0.0})
    for p in produits:
        c = p.categorie or "Non classé"
        cats[c]["nb_produits"] += 1
        cats[c]["valeur"] += float(p.prix_vente or 0) * (p.stock_boite or 0)

    stock_faible = [
        {"id": p.id, "nom": p.nom, "stock": p.stock_total_piece, "seuil": p.seuil_alerte}
        for p in produits
        if 0 < (p.stock_total_piece or 0) <= (p.seuil_alerte or 0)
    ]
    expirations = [
        {
            "id": p.id, "nom": p.nom,
            "date_expiration": str(p.date_expiration),
            "jours_restants": (p.date_expiration - today).days,
        }
        for p in produits
        if p.date_expiration and p.date_expiration <= limite and p.date_expiration >= today
    ]
    expirations.sort(key=lambda x: x["jours_restants"])

    return {
        "valeur_totale_stock":   float(valeur_totale),
        "nb_produits_total":     len(produits),
        "nb_stock_faible":       len(stock_faible),
        "nb_produits_epuises":   sum(1 for p in produits if (p.stock_total_piece or 0) == 0),
        "nb_expirations_proches": len(expirations),
        "par_categorie": sorted(
            [{"categorie": k, **v} for k, v in cats.items()],
            key=lambda x: x["valeur"], reverse=True,
        ),
        "stock_faible":        stock_faible,
        "expirations_proches": expirations,
    }


# ═══════════════════════════════════════════════════════════
# GET /rapports/cloture-journaliere — Propriétaire seulement
# FIX v4.2 : même filtre plage datetime que le dashboard
# ═══════════════════════════════════════════════════════════
@router.get("/cloture-journaliere")
def cloture_journaliere(
    date_cloture: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user),
):
    _require_proprietaire(current_user)
    ph_id = current_user.id_pharmacie

    if date_cloture:
        try:
            jour = datetime.strptime(date_cloture, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(400, "Format de date invalide (attendu: YYYY-MM-DD)")
    else:
        jour = date.today()

    # Plage UTC : date_vente est stocké en utcnow() → comparaison en UTC
    debut_jour = datetime(jour.year, jour.month, jour.day, 0, 0, 0)
    fin_jour   = debut_jour + timedelta(days=1)

    ventes_jour = db.query(Vente).filter(
        Vente.id_pharmacie == ph_id,
        Vente.is_deleted   == False,
        Vente.date_vente   >= debut_jour,
        Vente.date_vente   <  fin_jour,
    ).all()

    confirmees = [v for v in ventes_jour if v.statut in ("confirmee", "payee")]
    annulees   = [v for v in ventes_jour if v.statut == "annulee"]

    ca_brut        = sum(float(v.total or 0) for v in confirmees)
    total_encaisse = sum(float(v.montant_paye or 0) for v in confirmees)
    total_impaye   = sum(float(v.reste_a_payer or 0) for v in confirmees)
    nb_ventes      = len(confirmees)
    ticket_moyen   = (ca_brut / nb_ventes) if nb_ventes > 0 else 0

    # Par moyen de paiement
    paiements_jour = db.query(Paiement).join(
        Vente, Vente.id == Paiement.id_vente
    ).filter(
        Vente.id_pharmacie   == ph_id,
        Paiement.date_paiement >= debut_jour,
        Paiement.date_paiement <  fin_jour,
    ).all()

    par_moyen: dict = defaultdict(lambda: {"total": 0.0, "nb_transactions": 0})
    for p in paiements_jour:
        par_moyen[p.moyen]["total"]          += float(p.montant or 0)
        par_moyen[p.moyen]["nb_transactions"] += 1

    # Par vendeur
    par_vendeur: dict = defaultdict(lambda: {"ca": 0.0, "nb_ventes": 0})
    for v in confirmees:
        id_vend = getattr(v, "id_utilisateur", None)
        vendeur = db.query(Utilisateur).filter(Utilisateur.id == id_vend).first() if id_vend else None
        nom = vendeur.nom if vendeur else "Inconnu"
        par_vendeur[nom]["ca"]       += float(v.total or 0)
        par_vendeur[nom]["nb_ventes"] += 1

    return {
        "date": str(jour),
        "resume": {
            "ca_brut":        round(ca_brut, 2),
            "total_encaisse": round(total_encaisse, 2),
            "total_impaye":   round(total_impaye, 2),
            "nb_ventes":      nb_ventes,
            "nb_annulees":    len(annulees),
            "ticket_moyen":   round(ticket_moyen, 2),
        },
        "par_moyen_paiement": [
            {"moyen": k, "total": round(v["total"], 2), "nb_transactions": v["nb_transactions"]}
            for k, v in sorted(par_moyen.items(), key=lambda x: x[1]["total"], reverse=True)
        ],
        "par_vendeur": [
            {"vendeur": k, "ca": round(v["ca"], 2), "nb_ventes": v["nb_ventes"]}
            for k, v in sorted(par_vendeur.items(), key=lambda x: x[1]["ca"], reverse=True)
        ],
    }


# ═══════════════════════════════════════════════════════════
# GET /rapports/mouvements-stock — Propriétaire + gestionnaire
# ═══════════════════════════════════════════════════════════
@router.get("/mouvements-stock")
def mouvements_stock(
    date_debut: str = Query(...),
    date_fin:   str = Query(...),
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user),
):
    _require_stock_access(current_user)
    ph_id = current_user.id_pharmacie

    entrees = db.query(EntreeStock).join(
        Produit, Produit.id == EntreeStock.id_produit
    ).filter(
        Produit.id_pharmacie == ph_id,
        Produit.is_deleted   == False,
        EntreeStock.date_entree >= date_debut,
        EntreeStock.date_entree <= date_fin,
    ).all()

    sorties = db.query(SortieStock).join(
        Produit, Produit.id == SortieStock.id_produit
    ).filter(
        Produit.id_pharmacie == ph_id,
        Produit.is_deleted   == False,
        SortieStock.date_sortie >= date_debut,
        SortieStock.date_sortie <= date_fin,
    ).all()

    total_entrees = sum(e.quantite or 0 for e in entrees)
    total_sorties = sum(s.quantite or 0 for s in sorties)

    def _produit_nom(e):
        p = db.query(Produit).filter(Produit.id == e.id_produit).first()
        return p.nom if p else f"Produit #{e.id_produit}"

    return {
        "periode":              {"debut": date_debut, "fin": date_fin},
        "total_entrees_boites": total_entrees,
        "total_sorties_boites": total_sorties,
        "bilan_net":            total_entrees - total_sorties,
        "entrees": [
            {
                "id":          e.id,
                "produit":     _produit_nom(e),
                "quantite":    e.quantite,
                "type":        e.type_entree,
                "fournisseur": getattr(e, "fournisseur", None),
                "date":        str(e.date_entree),
            }
            for e in entrees
        ],
        "sorties": [
            {
                "id":       s.id,
                "produit":  _produit_nom(s),
                "quantite": s.quantite,
                "motif":    s.motif,
                "date":     str(s.date_sortie),
            }
            for s in sorties
        ],
    }