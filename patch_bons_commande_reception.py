# patch_bons_commande_reception.py — Pharmy-C v4.3
# ============================================================
# INSTRUCTION : Dans routers/bons_commande.py
# Remplacer le bloc "Propager le prix d'achat vers le produit"
# dans la fonction receptionner_commande() par ce code :
# ============================================================
#
# AVANT (v4.2) :
#   try:
#       pa_produit = float(getattr(produit, 'prix_achat', 0) or 0)
#       if pa_produit == 0 and prix_ht > 0:
#           setattr(produit, 'prix_achat', Decimal(str(prix_ht)))
#   except Exception:
#       pass
#
# APRÈS (v4.3) — remplacer par ce bloc complet :
# ─────────────────────────────────────────────────────────────

"""
        # ── Propagation prix vers PrixAchatFournisseur ──────────
        # v4.3 : chaque (produit × fournisseur) a son propre prix
        if prix_ht > 0 and fournisseur:
            try:
                from routers.prix_fournisseur import _mettre_a_jour_prix_fournisseur
                _mettre_a_jour_prix_fournisseur(
                    db,
                    id_produit      = produit.id,
                    id_fournisseur  = bc.id_fournisseur,
                    prix_ht         = prix_ht,
                    quantite        = qte_recue,
                    id_bc           = bc.id,
                )
                # produit.prix_achat est mis à jour dans _mettre_a_jour_prix_fournisseur()
            except Exception as e_pa:
                # Fallback : mise à jour directe si le service est indisponible
                try:
                    pa_produit = float(getattr(produit, 'prix_achat', 0) or 0)
                    if pa_produit == 0 and prix_ht > 0:
                        setattr(produit, 'prix_achat', Decimal(str(prix_ht)))
                except Exception:
                    pass
"""

# ═══════════════════════════════════════════════════════════
# AJOUTER AUSSI dans routers/entrees_stock.py
# dans la fonction ajouter_entree(), après db.add(entree) :
# ═══════════════════════════════════════════════════════════

"""
    # ── Prix d'achat et propagation vers PrixAchatFournisseur ──
    pa_u = getattr(payload, 'prix_achat_unitaire', None)
    if pa_u and float(pa_u) > 0:
        montant = float(pa_u) * payload.quantite
        try:
            setattr(entree, 'prix_achat_unitaire', Decimal(str(pa_u)))
            setattr(entree, 'montant_achat',       Decimal(str(round(montant, 2))))
        except Exception:
            pass

        # Propager vers PrixAchatFournisseur si fournisseur renseigné
        id_fournisseur_entree = getattr(payload, 'id_fournisseur', None)
        if id_fournisseur_entree:
            try:
                from routers.prix_fournisseur import _mettre_a_jour_prix_fournisseur
                _mettre_a_jour_prix_fournisseur(
                    db,
                    id_produit     = produit.id,
                    id_fournisseur = id_fournisseur_entree,
                    prix_ht        = float(pa_u),
                    quantite       = payload.quantite,
                )
            except Exception:
                pass
        else:
            # Pas de fournisseur spécifique → mettre à jour juste produit.prix_achat
            try:
                setattr(produit, 'prix_achat', Decimal(str(pa_u)))
            except Exception:
                pass
"""

# ═══════════════════════════════════════════════════════════
# MISE À JOUR DU SCHEMA EntreeStockCreate dans schemas.py
# ═══════════════════════════════════════════════════════════

"""
class EntreeStockCreate(BaseModel):
    id_produit:          int
    quantite:            int
    type_entree:         str = "achat"
    fournisseur:         Optional[str]  = None
    id_fournisseur:      Optional[int]  = None   # ← NOUVEAU : lien FK vers fournisseurs
    prix_achat_unitaire: Optional[float] = None  # ← NOUVEAU : prix par boîte HT
    # montant_achat calculé automatiquement = quantite × prix_achat_unitaire

    @field_validator("quantite")
    @classmethod
    def quantite_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("La quantité doit être positive")
        return v
"""

# ═══════════════════════════════════════════════════════════
# AJOUTER dans main.py → _migrer_colonnes() :
# ═══════════════════════════════════════════════════════════

MIGRATIONS_V43 = [
    # Table prix par fournisseur
    """CREATE TABLE IF NOT EXISTS prix_achat_fournisseur (
        id               SERIAL PRIMARY KEY,
        id_produit       INTEGER NOT NULL REFERENCES produits(id) ON DELETE CASCADE,
        id_fournisseur   INTEGER NOT NULL REFERENCES fournisseurs(id) ON DELETE CASCADE,
        prix_ht          NUMERIC(10,2) NOT NULL,
        prix_min_ht      NUMERIC(10,2),
        prix_max_ht      NUMERIC(10,2),
        nb_commandes     INTEGER NOT NULL DEFAULT 1,
        derniere_commande DATE,
        id_dernier_bc    INTEGER REFERENCES bons_commande(id) ON DELETE SET NULL,
        created_at       TIMESTAMP NOT NULL DEFAULT NOW(),
        updated_at       TIMESTAMP NOT NULL DEFAULT NOW(),
        CONSTRAINT uq_produit_fournisseur UNIQUE(id_produit, id_fournisseur)
    )""",

    # Index pour les lookups rapides
    "CREATE INDEX IF NOT EXISTS idx_paf_produit ON prix_achat_fournisseur(id_produit)",
    "CREATE INDEX IF NOT EXISTS idx_paf_fournisseur ON prix_achat_fournisseur(id_fournisseur)",

    # Table historique des prix
    """CREATE TABLE IF NOT EXISTS historique_prix_fournisseur (
        id              SERIAL PRIMARY KEY,
        id_produit      INTEGER NOT NULL REFERENCES produits(id) ON DELETE CASCADE,
        id_fournisseur  INTEGER NOT NULL REFERENCES fournisseurs(id) ON DELETE CASCADE,
        id_bon_commande INTEGER REFERENCES bons_commande(id) ON DELETE SET NULL,
        prix_ht         NUMERIC(10,2) NOT NULL,
        quantite        INTEGER NOT NULL DEFAULT 1,
        date_achat      DATE NOT NULL,
        created_at      TIMESTAMP NOT NULL DEFAULT NOW()
    )""",

    # Colonne id_fournisseur sur entrees_stock (lien FK vers fournisseurs)
    "ALTER TABLE entrees_stock ADD COLUMN IF NOT EXISTS id_fournisseur INTEGER REFERENCES fournisseurs(id) ON DELETE SET NULL",
]

# ═══════════════════════════════════════════════════════════
# AJOUTER dans main.py → section 3 "Import des routeurs" :
# ═══════════════════════════════════════════════════════════
"""
try:
    from routers import prix_fournisseur as prix_fournisseur_router
    app.include_router(
        prix_fournisseur_router.router,
        prefix="/prix-fournisseur",
        tags=["💰 Prix fournisseurs"],
    )
except ImportError as e:
    print(f"⚠️  Router prix_fournisseur non disponible : {e}")
"""