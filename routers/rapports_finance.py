# routers/rapports_finance.py — Pharmy-C v4.3


from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Optional
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, and_, extract
from sqlalchemy.orm import Session, joinedload

from database import get_db
from models.models import (
    Vente, DetailVente, Produit, EntreeStock, Utilisateur,
)
from routers.auth import get_current_user

router = APIRouter()

ROLES_FINANCE_COMPLET = ("proprietaire", "admin")
ROLES_FINANCE_STOCK   = ("proprietaire", "admin", "gestionnaire_stock")


# ─────────────────────────────────────────────────────────────
# Helpers internes
# ─────────────────────────────────────────────────────────────

def _get_role(user: Utilisateur) -> str:
    return user.role.name if user.role else ""


def _check_acces(user: Utilisateur, roles_autorises: tuple):
    if _get_role(user) not in roles_autorises:
        raise HTTPException(403, "Accès réservé au propriétaire / gestionnaire de stock")
    if not user.id_pharmacie:
        raise HTTPException(400, "Aucune pharmacie associée")


def _prix_achat_piece(produit: Produit) -> float:
    """Prix d'achat par pièce depuis produit.prix_achat (par boîte)."""
    try:
        pa  = float(getattr(produit, 'prix_achat', 0) or 0)
        qpb = produit.quantite_par_boite   or 1
        ppp = produit.pieces_par_plaquette or 1
        return pa / (qpb * ppp) if (qpb * ppp) > 0 else 0
    except Exception:
        return 0


def _cout_entree(e: EntreeStock) -> tuple[float, bool]:
    """
    Retourne (montant_achat, has_prix_reel).
    Priorité :
      1. montant_achat direct (champ calculé)
      2. prix_achat_unitaire * quantite
      3. Depuis LigneBonCommande liée (id_bon_commande_ligne)
      4. Depuis prix_achat du produit
    Retourne has_prix_reel=False si on utilise une estimation.
    """
    # 1. montant_achat direct
    montant = float(getattr(e, 'montant_achat', 0) or 0)
    if montant > 0:
        return montant, True

    # 2. prix_achat_unitaire
    pa_u = float(getattr(e, 'prix_achat_unitaire', 0) or 0)
    if pa_u > 0:
        return pa_u * (e.quantite or 0), True

    # 3. Depuis la LigneBonCommande liée
    try:
        from models.fournisseurs import LigneBonCommande
        id_ligne = getattr(e, 'id_bon_commande_ligne', None)
        if id_ligne:
            from sqlalchemy.orm import object_session
            db = object_session(e)
            if db:
                ligne = db.query(LigneBonCommande).filter(
                    LigneBonCommande.id == id_ligne
                ).first()
                if ligne and ligne.prix_unitaire_ht:
                    return float(ligne.prix_unitaire_ht) * (e.quantite or 0), True
    except Exception:
        pass

    return 0.0, False


def _cout_achats_periode(
    db: Session,
    ph_id: int,
    date_debut: date,
    date_fin: date,
) -> tuple[float, bool]:
    """
    Calcule le coût total des achats pour une pharmacie sur une période.
    Retourne (cout_total, has_prix_reel).

    Sources dans l'ordre de priorité :
      1. EntreeStock avec prix (montant_achat ou prix_achat_unitaire)
      2. LigneBonCommande des BCs réceptionnés sur la période
      3. Flag has_prix_reel = False si aucune source réelle trouvée
    """
    entrees = db.query(EntreeStock).filter(
        EntreeStock.id_produit.in_(
            db.query(Produit.id).filter(
                Produit.id_pharmacie == ph_id,
                Produit.is_deleted   == False,
            )
        ),
        EntreeStock.is_deleted == False,
        EntreeStock.date_entree >= date_debut,
        EntreeStock.date_entree <= date_fin,
    ).all()

    cout_total    = 0.0
    has_prix_reel = False

    for e in entrees:
        montant, has_prix = _cout_entree(e)
        if has_prix:
            cout_total    += montant
            has_prix_reel  = True

    # Fallback : LigneBonCommande des BCs reçus si EntreeStock sans prix
    if not has_prix_reel:
        try:
            from models.fournisseurs import LigneBonCommande, BonCommande
            rows = db.query(
                func.sum(LigneBonCommande.total_ligne_ht)
            ).join(
                BonCommande, BonCommande.id == LigneBonCommande.id_bon_commande
            ).filter(
                BonCommande.id_pharmacie == ph_id,
                BonCommande.is_deleted   == False,
                BonCommande.statut.in_(["recu", "partiellement_recu"]),
                func.date(BonCommande.date_livraison_reelle) >= date_debut,
                func.date(BonCommande.date_livraison_reelle) <= date_fin,
            ).scalar()
            if rows and float(rows) > 0:
                cout_total    = float(rows)
                has_prix_reel = True
        except Exception:
            pass

    return cout_total, has_prix_reel


# ═══════════════════════════════════════════════════════════
# GET /finance/dashboard — KPIs financiers du mois en cours
# ═══════════════════════════════════════════════════════════
@router.get("/dashboard")
def dashboard_finance(
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user),
):
    """
    KPIs du mois courant :
      - ca_mois, ca_mois_precedent, evolution_ca_pct
      - cout_achats_mois (entrées stock + BC réceptionnés)
      - benefice_brut, marge_brute_pct
      - stock_valorise (prix de revient réel, sans estimation silencieuse)
      - nb_entrees_mois, has_prix_achat
    """
    _check_acces(current_user, ROLES_FINANCE_COMPLET)
    ph_id = current_user.id_pharmacie
    today = date.today()

    debut_mois      = today.replace(day=1)
    debut_mois_prec = (debut_mois - timedelta(days=1)).replace(day=1)
    fin_mois_prec   = debut_mois - timedelta(days=1)

    # ── CA mois courant ──────────────────────────────────
    ca_mois = float(db.query(func.sum(Vente.total)).filter(
        Vente.id_pharmacie == ph_id,
        Vente.is_deleted   == False,
        Vente.statut.in_(["confirmee", "payee"]),
        func.date(Vente.date_vente) >= debut_mois,
    ).scalar() or 0)

    # ── CA mois précédent ────────────────────────────────
    ca_mois_prec = float(db.query(func.sum(Vente.total)).filter(
        Vente.id_pharmacie == ph_id,
        Vente.is_deleted   == False,
        Vente.statut.in_(["confirmee", "payee"]),
        func.date(Vente.date_vente) >= debut_mois_prec,
        func.date(Vente.date_vente) <= fin_mois_prec,
    ).scalar() or 0)

    evolution_ca = round(
        ((ca_mois - ca_mois_prec) / ca_mois_prec * 100) if ca_mois_prec > 0 else 0,
        1
    )

    # ── Coûts achats mois courant ────────────────────────
    cout_achats, has_prix_reel = _cout_achats_periode(db, ph_id, debut_mois, today)

    # ── Bénéfice brut ────────────────────────────────────
    benefice_brut = ca_mois - cout_achats
    marge_pct     = round((benefice_brut / ca_mois * 100) if ca_mois > 0 else 0, 1)

    # ── Stock valorisé au prix de revient réel ───────────
    # N'utilise PAS l'estimation 60% → flag explicite si prix manquant
    stock_valorise    = 0.0
    produits_sans_pa  = 0
    produits = db.query(Produit).filter(
        Produit.id_pharmacie == ph_id,
        Produit.is_deleted   == False,
    ).all()

    for p in produits:
        pa_piece = _prix_achat_piece(p)
        if pa_piece > 0:
            stock_valorise += pa_piece * (p.stock_total_piece or 0)
        else:
            produits_sans_pa += 1

    # ── Nb entrées mois ──────────────────────────────────
    nb_entrees = int(db.query(func.count(EntreeStock.id)).filter(
        EntreeStock.date_entree >= debut_mois,
        EntreeStock.id_produit.in_(
            db.query(Produit.id).filter(Produit.id_pharmacie == ph_id)
        ),
        EntreeStock.is_deleted == False,
    ).scalar() or 0)

    # ── BCs du mois ──────────────────────────────────────
    nb_bc_mois   = 0
    total_bc_mois = 0.0
    try:
        from models.fournisseurs import BonCommande
        nb_bc_mois = int(db.query(func.count(BonCommande.id)).filter(
            BonCommande.id_pharmacie == ph_id,
            BonCommande.is_deleted   == False,
            BonCommande.statut.in_(["recu", "partiellement_recu"]),
            func.date(BonCommande.date_livraison_reelle) >= debut_mois,
        ).scalar() or 0)
        total_bc_mois = float(db.query(func.sum(BonCommande.total_ttc)).filter(
            BonCommande.id_pharmacie == ph_id,
            BonCommande.is_deleted   == False,
            BonCommande.statut.in_(["recu", "partiellement_recu"]),
            func.date(BonCommande.date_livraison_reelle) >= debut_mois,
        ).scalar() or 0)
    except Exception:
        pass

    return {
        "mois":                  today.strftime("%B %Y"),
        "ca_mois":               round(ca_mois, 2),
        "ca_mois_precedent":     round(ca_mois_prec, 2),
        "evolution_ca_pct":      evolution_ca,
        "cout_achats_mois":      round(cout_achats, 2),
        "benefice_brut":         round(benefice_brut, 2),
        "marge_brute_pct":       marge_pct,
        "stock_valorise":        round(stock_valorise, 2),
        "nb_entrees_mois":       nb_entrees,
        "nb_bc_receptiones_mois": nb_bc_mois,
        "total_bc_mois":         round(total_bc_mois, 2),
        "has_prix_achat":        has_prix_reel,
        "nb_produits_sans_prix": produits_sans_pa,
    }


# ═══════════════════════════════════════════════════════════
# GET /finance/mensuel — Évolution sur N mois
# ═══════════════════════════════════════════════════════════
@router.get("/mensuel")
def rapport_mensuel(
    nb_mois: int = Query(12, ge=1, le=24),
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user),
):
    """
    Mois par mois (du plus ancien au plus récent) :
      ca, cout_achats, benefice, marge_pct, nb_ventes, has_prix_achat
    Les coûts incluent entrées stock ET bons de commande réceptionnés.
    """
    _check_acces(current_user, ROLES_FINANCE_COMPLET)
    ph_id = current_user.id_pharmacie
    today = date.today()

    resultats = []
    for i in range(nb_mois - 1, -1, -1):
        # Calcul du début du mois i mois en arrière
        d = today.replace(day=1)
        for _ in range(i):
            d = (d - timedelta(days=1)).replace(day=1)
        # Fin du mois = dernier jour
        fin = (d.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)

        ca = float(db.query(func.sum(Vente.total)).filter(
            Vente.id_pharmacie == ph_id,
            Vente.is_deleted   == False,
            Vente.statut.in_(["confirmee", "payee"]),
            func.date(Vente.date_vente) >= d,
            func.date(Vente.date_vente) <= fin,
        ).scalar() or 0)

        nb_ventes = int(db.query(func.count(Vente.id)).filter(
            Vente.id_pharmacie == ph_id,
            Vente.is_deleted   == False,
            Vente.statut.in_(["confirmee", "payee"]),
            func.date(Vente.date_vente) >= d,
            func.date(Vente.date_vente) <= fin,
        ).scalar() or 0)

        cout, has_prix = _cout_achats_periode(db, ph_id, d, fin)
        benefice = ca - cout
        marge    = round((benefice / ca * 100) if ca > 0 else 0, 1)

        resultats.append({
            "mois":          d.strftime("%b %Y"),
            "mois_court":    d.strftime("%b"),
            "annee":         d.year,
            "ca":            round(ca, 2),
            "cout_achats":   round(cout, 2),
            "benefice":      round(benefice, 2),
            "marge_pct":     marge,
            "nb_ventes":     nb_ventes,
            "has_prix_achat": has_prix,
        })

    return resultats


# ═══════════════════════════════════════════════════════════
# GET /finance/produits — Rentabilité par produit
# ═══════════════════════════════════════════════════════════
@router.get("/produits")
def rentabilite_produits(
    mois:  Optional[int] = Query(None, ge=1, le=12),
    annee: Optional[int] = Query(None, ge=2020),
    limit: int           = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user),
):
    """
    Par produit : quantite_vendue, ca, cout, benefice, marge_pct.
    prix_achat issu de produit.prix_achat (mis à jour via /finance/produits/{id}/prix-achat).
    Flag has_prix_achat = True si prix réel, False si non renseigné.
    Pas d'estimation silencieuse : benefice = None si prix manquant.
    """
    _check_acces(current_user, ROLES_FINANCE_COMPLET)
    ph_id = current_user.id_pharmacie

    q = db.query(
        DetailVente.id_produit,
        func.sum(DetailVente.quantite).label("qte_vendue"),
        func.sum(DetailVente.total_ligne).label("ca_produit"),
    ).join(
        Vente, Vente.id == DetailVente.id_vente
    ).filter(
        Vente.id_pharmacie == ph_id,
        Vente.is_deleted   == False,
        Vente.statut.in_(["confirmee", "payee"]),
    )

    if mois and annee:
        q = q.filter(
            extract('month', Vente.date_vente) == mois,
            extract('year',  Vente.date_vente) == annee,
        )
    elif annee:
        q = q.filter(extract('year', Vente.date_vente) == annee)

    rows = q.group_by(DetailVente.id_produit)\
            .order_by(func.sum(DetailVente.total_ligne).desc())\
            .limit(limit).all()

    resultats = []
    for row in rows:
        produit = db.query(Produit).filter(Produit.id == row.id_produit).first()
        if not produit:
            continue

        ca       = float(row.ca_produit or 0)
        qte      = int(row.qte_vendue or 0)
        pa_piece = _prix_achat_piece(produit)
        pa_boite = float(getattr(produit, 'prix_achat', 0) or 0)

        if pa_piece > 0:
            cout      = round(pa_piece * qte, 2)
            benefice  = round(ca - cout, 2)
            marge_pct = round((benefice / ca * 100) if ca > 0 else 0, 1)
            has_pa    = True
        else:
            # Pas d'estimation silencieuse
            cout      = None
            benefice  = None
            marge_pct = None
            has_pa    = False

        resultats.append({
            "id":                 produit.id,
            "nom":                produit.nom,
            "categorie":          produit.categorie,
            "quantite_vendue":    qte,
            "ca":                 round(ca, 2),
            "cout":               cout,
            "benefice":           benefice,
            "marge_pct":          marge_pct,
            "prix_vente_boite":   float(produit.prix_vente or 0),
            "prix_achat_boite":   pa_boite,
            "prix_revient_piece": round(pa_piece, 2) if pa_piece > 0 else None,
            "stock_actuel":       produit.stock_total_piece or 0,
            "has_prix_achat":     has_pa,
        })

    return resultats


# ═══════════════════════════════════════════════════════════
# GET /finance/achats — Historique entrées stock avec prix achat
# ═══════════════════════════════════════════════════════════
@router.get("/achats")
def historique_achats(
    date_debut: Optional[str] = Query(None),
    date_fin:   Optional[str] = Query(None),
    id_fournisseur: Optional[int] = Query(None, description="Filtrer par fournisseur"),
    skip:  int = Query(0,  ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user),
):
    """
    Historique des entrées de stock enrichi :
      - prix_achat_unitaire, montant_achat
      - bc_code + fournisseur_nom si entrée liée à un BC
      - has_prix : True si prix réel disponible
    Accessible : propriétaire, admin, gestionnaire_stock.
    """
    _check_acces(current_user, ROLES_FINANCE_STOCK)
    ph_id = current_user.id_pharmacie

    q = db.query(EntreeStock).filter(
        EntreeStock.id_produit.in_(
            db.query(Produit.id).filter(
                Produit.id_pharmacie == ph_id,
                Produit.is_deleted   == False,
            )
        ),
        EntreeStock.is_deleted == False,
    )

    if date_debut:
        q = q.filter(EntreeStock.date_entree >= date_debut)
    if date_fin:
        q = q.filter(EntreeStock.date_entree <= date_fin)

    entrees = q.order_by(EntreeStock.date_entree.desc()).offset(skip).limit(limit).all()

    # Précharger les données BC pour éviter N+1
    bc_map: dict[int, dict] = {}   # id_entree → {bc_code, fournisseur_nom}
    try:
        from models.fournisseurs import LigneBonCommande, BonCommande, Fournisseur
        for e in entrees:
            id_ligne = getattr(e, 'id_bon_commande_ligne', None)
            if id_ligne and id_ligne not in bc_map:
                ligne = db.query(LigneBonCommande).filter(
                    LigneBonCommande.id == id_ligne
                ).first()
                if ligne:
                    bc = db.query(BonCommande).filter(
                        BonCommande.id == ligne.id_bon_commande
                    ).first()
                    if bc:
                        fournisseur = db.query(Fournisseur).filter(
                            Fournisseur.id == bc.id_fournisseur
                        ).first()
                        bc_map[e.id] = {
                            "bc_id":          bc.id,
                            "bc_code":         bc.code,
                            "fournisseur_nom": fournisseur.nom if fournisseur else None,
                            "fournisseur_id":  bc.id_fournisseur,
                        }
    except Exception:
        pass

    # Filtre fournisseur si demandé (via lien BC)
    resultats = []
    for e in entrees:
        bc_info  = bc_map.get(e.id, {})
        montant, has_prix = _cout_entree(e)
        pa_u = float(getattr(e, 'prix_achat_unitaire', 0) or 0)

        # Filtrer par fournisseur si paramètre fourni
        if id_fournisseur and bc_info.get("fournisseur_id") != id_fournisseur:
            continue

        produit = db.query(Produit).filter(Produit.id == e.id_produit).first()

        resultats.append({
            "id":                  e.id,
            "date_entree":         e.date_entree.isoformat() if e.date_entree else None,
            "id_produit":          e.id_produit,
            "produit_nom":         produit.nom if produit else f"#{e.id_produit}",
            "quantite":            e.quantite,
            "type_entree":         e.type_entree,
            "fournisseur":         e.fournisseur or bc_info.get("fournisseur_nom"),
            "fournisseur_nom":     bc_info.get("fournisseur_nom"),
            "fournisseur_id":      bc_info.get("fournisseur_id"),
            "bc_id":               bc_info.get("bc_id"),
            "bc_code":             bc_info.get("bc_code"),
            "prix_achat_unitaire": pa_u,
            "montant_achat":       round(montant, 2),
            "has_prix":            has_prix,
        })

    total_achats = sum(r["montant_achat"] for r in resultats)

    return {
        "entrees":      resultats,
        "total_achats": round(total_achats, 2),
        "count":        len(resultats),
    }


# ═══════════════════════════════════════════════════════════
# GET /finance/synthese-achats — Vision complète par fournisseur / produit
# ═══════════════════════════════════════════════════════════
@router.get("/synthese-achats")
def synthese_achats(
    date_debut: Optional[str] = Query(None, description="YYYY-MM-DD"),
    date_fin:   Optional[str] = Query(None, description="YYYY-MM-DD"),
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user),
):
    """
    Synthèse financière des achats sur une période :

    1. par_fournisseur : total HT/TTC, nb bons, nb livraisons, nb produits
    2. par_produit     : total acheté (boîtes + montant), nb entrées
    3. par_mois        : évolution mensuelle des achats
    4. bons_en_attente : BCs envoyés non encore reçus (argent engagé)
    5. resume          : totaux globaux
    """
    _check_acces(current_user, ROLES_FINANCE_STOCK)
    ph_id = current_user.id_pharmacie

    # Dates par défaut : 3 derniers mois
    if not date_fin:
        date_fin   = date.today().isoformat()
    if not date_debut:
        date_debut = (date.today() - timedelta(days=90)).isoformat()

    try:
        from models.fournisseurs import BonCommande, LigneBonCommande, Fournisseur

        # ── 1. PAR FOURNISSEUR via BCs réceptionnés ──────────
        bc_receptiones = db.query(BonCommande).options(
            joinedload(BonCommande.lignes),
            joinedload(BonCommande.fournisseur),
        ).filter(
            BonCommande.id_pharmacie == ph_id,
            BonCommande.is_deleted   == False,
            BonCommande.statut.in_(["recu", "partiellement_recu"]),
        )
        if date_debut:
            bc_receptiones = bc_receptiones.filter(
                func.date(BonCommande.date_livraison_reelle) >= date_debut
            )
        if date_fin:
            bc_receptiones = bc_receptiones.filter(
                func.date(BonCommande.date_livraison_reelle) <= date_fin
            )
        bcs = bc_receptiones.all()

        par_fournisseur: dict = defaultdict(lambda: {
            "fournisseur_id":   None,
            "fournisseur_nom":  "",
            "nb_bons":          0,
            "total_ht":         0.0,
            "total_ttc":        0.0,
            "nb_produits":      set(),
            "nb_lignes":        0,
        })

        par_produit_bc: dict = defaultdict(lambda: {
            "produit_id":   None,
            "produit_nom":  "",
            "quantite":     0,
            "montant_ht":   0.0,
            "nb_entrees":   0,
        })

        par_mois_bc: dict = defaultdict(lambda: {"total_ht": 0.0, "nb_bons": 0})

        for bc in bcs:
            f = bc.fournisseur
            f_key = bc.id_fournisseur
            par_fournisseur[f_key]["fournisseur_id"]  = bc.id_fournisseur
            par_fournisseur[f_key]["fournisseur_nom"] = f.nom if f else f"#{bc.id_fournisseur}"
            par_fournisseur[f_key]["nb_bons"]  += 1
            par_fournisseur[f_key]["total_ht"]  += float(bc.total_ht or 0)
            par_fournisseur[f_key]["total_ttc"] += float(bc.total_ttc or 0)
            par_fournisseur[f_key]["nb_lignes"] += len(bc.lignes or [])

            mois_key = bc.date_livraison_reelle.strftime("%Y-%m") if bc.date_livraison_reelle else "?"
            par_mois_bc[mois_key]["total_ht"] += float(bc.total_ht or 0)
            par_mois_bc[mois_key]["nb_bons"]  += 1

            for ligne in (bc.lignes or []):
                par_fournisseur[f_key]["nb_produits"].add(ligne.id_produit)
                produit = db.query(Produit).filter(Produit.id == ligne.id_produit).first()
                par_produit_bc[ligne.id_produit]["produit_id"]  = ligne.id_produit
                par_produit_bc[ligne.id_produit]["produit_nom"] = produit.nom if produit else f"#{ligne.id_produit}"
                par_produit_bc[ligne.id_produit]["quantite"]  += ligne.quantite_commandee or 0
                par_produit_bc[ligne.id_produit]["montant_ht"] += float(ligne.total_ligne_ht or 0)
                par_produit_bc[ligne.id_produit]["nb_entrees"] += 1

        # ── 2. PAR PRODUIT via EntreeStock (toutes sources) ──
        entrees_periode = db.query(EntreeStock).filter(
            EntreeStock.id_produit.in_(
                db.query(Produit.id).filter(
                    Produit.id_pharmacie == ph_id,
                    Produit.is_deleted   == False,
                )
            ),
            EntreeStock.is_deleted == False,
            EntreeStock.date_entree >= date_debut,
            EntreeStock.date_entree <= date_fin,
        ).all()

        par_produit_entree: dict = defaultdict(lambda: {
            "produit_id":  None,
            "produit_nom": "",
            "quantite_boites": 0,
            "montant":     0.0,
            "has_prix":    False,
        })

        for e in entrees_periode:
            produit = db.query(Produit).filter(Produit.id == e.id_produit).first()
            montant, has_prix = _cout_entree(e)
            p_key = e.id_produit
            par_produit_entree[p_key]["produit_id"]      = e.id_produit
            par_produit_entree[p_key]["produit_nom"]     = produit.nom if produit else f"#{e.id_produit}"
            par_produit_entree[p_key]["quantite_boites"] += e.quantite or 0
            par_produit_entree[p_key]["montant"]         += montant
            if has_prix:
                par_produit_entree[p_key]["has_prix"] = True

        # ── 3. BCs EN ATTENTE (argent engagé non encore livré) ──
        bcs_attente = db.query(BonCommande).options(
            joinedload(BonCommande.fournisseur)
        ).filter(
            BonCommande.id_pharmacie == ph_id,
            BonCommande.is_deleted   == False,
            BonCommande.statut.in_(["envoye", "brouillon"]),
        ).order_by(BonCommande.date_commande.desc()).all()

        total_engage = sum(float(bc.total_ttc or 0) for bc in bcs_attente if bc.statut == "envoye")

        # ── Sérialisation ──
        fournisseurs_list = sorted(
            [
                {
                    "fournisseur_id":  v["fournisseur_id"],
                    "fournisseur_nom": v["fournisseur_nom"],
                    "nb_bons":         v["nb_bons"],
                    "total_ht":        round(v["total_ht"], 2),
                    "total_ttc":       round(v["total_ttc"], 2),
                    "nb_produits":     len(v["nb_produits"]),
                    "nb_lignes":       v["nb_lignes"],
                }
                for v in par_fournisseur.values()
            ],
            key=lambda x: x["total_ttc"], reverse=True,
        )

        produits_bc_list = sorted(
            [
                {
                    "produit_id":  v["produit_id"],
                    "produit_nom": v["produit_nom"],
                    "quantite_commandee": v["quantite"],
                    "montant_ht":  round(v["montant_ht"], 2),
                    "nb_entrees":  v["nb_entrees"],
                }
                for v in par_produit_bc.values()
            ],
            key=lambda x: x["montant_ht"], reverse=True,
        )

        produits_entree_list = sorted(
            [
                {
                    "produit_id":      v["produit_id"],
                    "produit_nom":     v["produit_nom"],
                    "quantite_boites": v["quantite_boites"],
                    "montant":         round(v["montant"], 2),
                    "has_prix":        v["has_prix"],
                }
                for v in par_produit_entree.values()
            ],
            key=lambda x: x["montant"], reverse=True,
        )

        par_mois_list = sorted(
            [
                {
                    "mois":     k,
                    "total_ht": round(v["total_ht"], 2),
                    "nb_bons":  v["nb_bons"],
                }
                for k, v in par_mois_bc.items()
            ],
            key=lambda x: x["mois"],
        )

        bons_attente_list = [
            {
                "id":                    bc.id,
                "code":                  bc.code,
                "statut":                bc.statut,
                "fournisseur_nom":       bc.fournisseur.nom if bc.fournisseur else None,
                "date_commande":         bc.date_commande.date().isoformat() if bc.date_commande else None,
                "date_livraison_prevue": bc.date_livraison_prevue.isoformat() if bc.date_livraison_prevue else None,
                "total_ttc":             float(bc.total_ttc or 0),
            }
            for bc in bcs_attente
        ]

        return {
            "periode":          {"debut": date_debut, "fin": date_fin},
            "par_fournisseur":  fournisseurs_list,
            "par_produit_bc":   produits_bc_list,
            "par_produit_entree": produits_entree_list,
            "par_mois":         par_mois_list,
            "bons_en_attente":  bons_attente_list,
            "resume": {
                "total_achats_bc":       round(sum(f["total_ht"] for f in fournisseurs_list), 2),
                "nb_bons_receptiones":   sum(f["nb_bons"] for f in fournisseurs_list),
                "nb_fournisseurs_actifs": len(fournisseurs_list),
                "total_engage":          round(total_engage, 2),
                "nb_bcs_en_attente":     len([b for b in bons_attente_list if b["statut"] == "envoye"]),
                "total_entrees_periode": len(entrees_periode),
            },
        }

    except ImportError as e:
        raise HTTPException(500, f"Module fournisseurs non disponible : {e}")


# ═══════════════════════════════════════════════════════════
# GET /finance/bons-commande — KPIs financiers des BCs
# ═══════════════════════════════════════════════════════════
@router.get("/bons-commande")
def finance_bons_commande(
    date_debut: Optional[str] = Query(None),
    date_fin:   Optional[str] = Query(None),
    statut:     Optional[str] = Query(None, description="brouillon|envoye|recu|partiellement_recu|annule"),
    skip:  int = Query(0,  ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user),
):
    """
    Liste des bons de commande avec détail financier :
      - total_ht, total_ttc, taux_tva
      - montant_recu (basé sur quantite_recue * prix)
      - ecart (commandé vs reçu)
      - statut de livraison et paiement
    """
    _check_acces(current_user, ROLES_FINANCE_STOCK)
    ph_id = current_user.id_pharmacie

    try:
        from models.fournisseurs import BonCommande, LigneBonCommande, Fournisseur

        q = db.query(BonCommande).options(
            joinedload(BonCommande.lignes),
            joinedload(BonCommande.fournisseur),
        ).filter(
            BonCommande.id_pharmacie == ph_id,
            BonCommande.is_deleted   == False,
        )

        if statut:
            q = q.filter(BonCommande.statut == statut)
        if date_debut:
            q = q.filter(func.date(BonCommande.date_commande) >= date_debut)
        if date_fin:
            q = q.filter(func.date(BonCommande.date_commande) <= date_fin)

        bcs = q.order_by(BonCommande.date_commande.desc()).offset(skip).limit(limit).all()

        resultats = []
        for bc in bcs:
            total_ht  = float(bc.total_ht  or 0)
            total_ttc = float(bc.total_ttc or 0)

            # Calcul du montant effectivement reçu
            montant_recu = sum(
                float(l.quantite_recue or 0) * float(l.prix_unitaire_ht or 0)
                for l in (bc.lignes or [])
            )
            taux_tva = float(bc.taux_tva or 0)
            montant_recu_ttc = montant_recu * (1 + taux_tva / 100)

            # Écart commandé vs reçu
            ecart_ht = total_ht - montant_recu

            # Taux de livraison
            total_commande = sum(l.quantite_commandee or 0 for l in (bc.lignes or []))
            total_recu     = sum(l.quantite_recue or 0     for l in (bc.lignes or []))
            taux_livraison = round((total_recu / total_commande * 100) if total_commande > 0 else 0, 1)

            resultats.append({
                "id":                    bc.id,
                "code":                  bc.code,
                "statut":                bc.statut,
                "fournisseur_id":        bc.id_fournisseur,
                "fournisseur_nom":       bc.fournisseur.nom if bc.fournisseur else None,
                "date_commande":         bc.date_commande.date().isoformat() if bc.date_commande else None,
                "date_livraison_prevue": bc.date_livraison_prevue.isoformat() if bc.date_livraison_prevue else None,
                "date_livraison_reelle": bc.date_livraison_reelle.isoformat() if bc.date_livraison_reelle else None,
                "taux_tva":              taux_tva,
                "total_ht":              round(total_ht, 2),
                "total_ttc":             round(total_ttc, 2),
                "montant_recu_ht":       round(montant_recu, 2),
                "montant_recu_ttc":      round(montant_recu_ttc, 2),
                "ecart_ht":              round(ecart_ht, 2),
                "taux_livraison_pct":    taux_livraison,
                "nb_lignes":             len(bc.lignes or []),
                "notes":                 bc.notes,
            })

        # KPIs agrégés
        total_commande_global = sum(r["total_ttc"]        for r in resultats)
        total_recu_global     = sum(r["montant_recu_ttc"] for r in resultats)
        total_ecart_global    = sum(r["ecart_ht"]         for r in resultats)

        return {
            "bons":         resultats,
            "count":        len(resultats),
            "kpis": {
                "total_commande":    round(total_commande_global, 2),
                "total_recu":        round(total_recu_global, 2),
                "total_ecart_ht":    round(total_ecart_global, 2),
                "nb_bons_complets":  sum(1 for r in resultats if r["statut"] == "recu"),
                "nb_bons_partiels":  sum(1 for r in resultats if r["statut"] == "partiellement_recu"),
                "nb_bons_attente":   sum(1 for r in resultats if r["statut"] == "envoye"),
                "nb_bons_brouillon": sum(1 for r in resultats if r["statut"] == "brouillon"),
            },
        }

    except ImportError as e:
        raise HTTPException(500, f"Module fournisseurs non disponible : {e}")


# ═══════════════════════════════════════════════════════════
# PATCH /finance/produits/{id}/prix-achat — Définir prix de revient
# ═══════════════════════════════════════════════════════════
@router.patch("/produits/{produit_id}/prix-achat")
def set_prix_achat_produit(
    produit_id: int,
    prix_achat: float = Query(..., gt=0, description="Prix d'achat par boîte"),
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user),
):
    """
    Définit le prix d'achat de référence d'un produit (par boîte).
    Recalcule et retourne : prix_revient_piece, marge_estimee_pct.
    """
    _check_acces(current_user, ROLES_FINANCE_STOCK)

    produit = db.query(Produit).filter(
        Produit.id           == produit_id,
        Produit.id_pharmacie == current_user.id_pharmacie,
        Produit.is_deleted   == False,
    ).first()
    if not produit:
        raise HTTPException(404, "Produit introuvable")

    try:
        setattr(produit, 'prix_achat', Decimal(str(prix_achat)))
        db.commit()
    except Exception as e:
        raise HTTPException(500, f"Erreur mise à jour : {e}")

    pa_piece   = _prix_achat_piece(produit)
    qpb        = produit.quantite_par_boite   or 1
    ppp        = produit.pieces_par_plaquette or 1
    pv_piece   = float(produit.prix_vente or 0) / (qpb * ppp)
    marge_pct  = round(
        ((pv_piece - pa_piece) / pv_piece * 100) if pv_piece > 0 and pa_piece > 0 else 0,
        1
    )

    return {
        "message":            "Prix d'achat mis à jour",
        "produit_id":         produit.id,
        "produit_nom":        produit.nom,
        "prix_achat_boite":   prix_achat,
        "prix_revient_piece": round(pa_piece, 2),
        "prix_vente_piece":   round(pv_piece, 2),
        "marge_estimee_pct":  marge_pct,
    }


# ═══════════════════════════════════════════════════════════
# PATCH /finance/entrees/{id}/prix-achat — Corriger prix a posteriori
# ═══════════════════════════════════════════════════════════
@router.patch("/entrees/{entree_id}/prix-achat")
def set_prix_achat_entree(
    entree_id:         int,
    prix_achat_unitaire: float = Query(..., gt=0, description="Prix d'achat unitaire (par boîte)"),
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user),
):
    """
    Corrige ou renseigne le prix d'achat d'une entrée de stock existante.
    Met à jour prix_achat_unitaire et recalcule montant_achat.
    Permet de corriger une entrée créée sans prix (ex: entrée manuelle).
    """
    _check_acces(current_user, ROLES_FINANCE_STOCK)
    ph_id = current_user.id_pharmacie

    entree = db.query(EntreeStock).filter(
        EntreeStock.id == entree_id,
        EntreeStock.is_deleted == False,
        EntreeStock.id_produit.in_(
            db.query(Produit.id).filter(
                Produit.id_pharmacie == ph_id,
                Produit.is_deleted   == False,
            )
        ),
    ).first()
    if not entree:
        raise HTTPException(404, "Entrée de stock introuvable")

    montant_achat = prix_achat_unitaire * (entree.quantite or 0)

    try:
        setattr(entree, 'prix_achat_unitaire', Decimal(str(prix_achat_unitaire)))
        setattr(entree, 'montant_achat',       Decimal(str(round(montant_achat, 2))))
        db.commit()
    except Exception as e:
        raise HTTPException(500, f"Erreur mise à jour : {e}")

    produit = db.query(Produit).filter(Produit.id == entree.id_produit).first()

    return {
        "message":              "Prix d'achat de l'entrée mis à jour",
        "entree_id":            entree.id,
        "produit_nom":          produit.nom if produit else None,
        "quantite":             entree.quantite,
        "prix_achat_unitaire":  prix_achat_unitaire,
        "montant_achat":        round(montant_achat, 2),
        "date_entree":          entree.date_entree.isoformat() if entree.date_entree else None,
    }