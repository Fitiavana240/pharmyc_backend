# models/prix_fournisseur.py — Pharmy-C v4.3
# ============================================================
# Table de référence des prix d'achat par fournisseur
#
# Logique :
#   - Chaque (produit × fournisseur) a son propre prix HT
#   - Mis à jour automatiquement à chaque réception de BC
#   - Consultable au moment de créer un BC ou une entrée stock
#   - Historique optionnel pour suivre l'évolution des prix
# ============================================================

from datetime import datetime, date
from sqlalchemy import (
    Column, Integer, Numeric, Date, DateTime,
    ForeignKey, UniqueConstraint, Boolean,
)
from sqlalchemy.orm import relationship
from database import Base


class PrixAchatFournisseur(Base):
    """
    Prix d'achat unitaire (par boîte) d'un produit chez un fournisseur donné.

    Une seule ligne par (produit, fournisseur) — mise à jour à chaque réception.
    Contient aussi des méta-données pour l'aide à la décision :
      - prix_ht         : prix courant par boîte HT
      - prix_min_ht     : prix le plus bas jamais vu chez ce fournisseur
      - prix_max_ht     : prix le plus haut jamais vu
      - nb_commandes    : nombre de fois commandé chez ce fournisseur
      - derniere_commande : date du dernier achat effectif (réception)
    """
    __tablename__ = "prix_achat_fournisseur"
    __table_args__ = (
        UniqueConstraint("id_produit", "id_fournisseur", name="uq_produit_fournisseur"),
    )

    id              = Column(Integer, primary_key=True, index=True)
    id_produit      = Column(Integer, ForeignKey("produits.id",    ondelete="CASCADE"), nullable=False, index=True)
    id_fournisseur  = Column(Integer, ForeignKey("fournisseurs.id", ondelete="CASCADE"), nullable=False, index=True)

    # Prix courant (dernier prix connu)
    prix_ht         = Column(Numeric(10, 2), nullable=False)

    # Statistiques historiques
    prix_min_ht     = Column(Numeric(10, 2), nullable=True)   # plus bas prix observé
    prix_max_ht     = Column(Numeric(10, 2), nullable=True)   # plus haut prix observé
    nb_commandes    = Column(Integer, default=1, nullable=False)

    # Traçabilité
    derniere_commande = Column(Date, nullable=True)
    id_dernier_bc     = Column(Integer, ForeignKey("bons_commande.id", ondelete="SET NULL"), nullable=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relations
    produit     = relationship("Produit",     backref="prix_fournisseurs", foreign_keys=[id_produit])
    fournisseur = relationship("Fournisseur", backref="prix_produits",    foreign_keys=[id_fournisseur])


class HistoriquePrixFournisseur(Base):
    """
    Historique optionnel des prix : une ligne par BC réceptionné.
    Permet de voir l'évolution du prix d'un produit chez un fournisseur.
    """
    __tablename__ = "historique_prix_fournisseur"

    id             = Column(Integer, primary_key=True, index=True)
    id_produit     = Column(Integer, ForeignKey("produits.id",    ondelete="CASCADE"), nullable=False, index=True)
    id_fournisseur = Column(Integer, ForeignKey("fournisseurs.id", ondelete="CASCADE"), nullable=False, index=True)
    id_bon_commande= Column(Integer, ForeignKey("bons_commande.id", ondelete="SET NULL"), nullable=True)

    prix_ht        = Column(Numeric(10, 2), nullable=False)
    quantite       = Column(Integer, nullable=False, default=1)
    date_achat     = Column(Date, nullable=False, default=date.today)

    created_at     = Column(DateTime, default=datetime.utcnow, nullable=False)