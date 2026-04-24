# models/fournisseurs.py
from database import Base
from sqlalchemy import (
    Column, Integer, String, Text, Boolean, Date, DateTime,
    ForeignKey, Numeric
)
from sqlalchemy.orm import relationship
from datetime import datetime


class Fournisseur(Base):
    __tablename__ = "fournisseurs"

    id                  = Column(Integer, primary_key=True, index=True)
    code                = Column(String(20), unique=True, nullable=False, index=True)  # FRN-XXXXXX
    id_pharmacie        = Column(Integer, ForeignKey("pharmacies.id", ondelete="CASCADE"), nullable=False)
    nom                 = Column(String(200), nullable=False)
    email               = Column(String(200), nullable=True)
    telephone           = Column(String(50), nullable=True)
    adresse             = Column(Text, nullable=True)
    contact_nom         = Column(String(200), nullable=True)
    nif                 = Column(String(50), nullable=True)
    conditions_paiement = Column(String(100), nullable=True)   # ex: "30 jours"
    delai_livraison     = Column(Integer, nullable=True)        # en jours
    actif               = Column(Boolean, default=True)
    notes               = Column(Text, nullable=True)
    is_deleted          = Column(Boolean, default=False)
    deleted_at          = Column(DateTime, nullable=True)
    created_at          = Column(DateTime, default=datetime.utcnow)
    updated_at          = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    pharmacie     = relationship("Pharmacie")
    bons_commande = relationship("BonCommande", back_populates="fournisseur",
                                 cascade="all, delete-orphan")


class BonCommande(Base):
    __tablename__ = "bons_commande"

    id                    = Column(Integer, primary_key=True, index=True)
    code                  = Column(String(20), unique=True, nullable=False, index=True)  # BC-XXXXXX
    id_pharmacie          = Column(Integer, ForeignKey("pharmacies.id", ondelete="CASCADE"), nullable=False)
    id_fournisseur        = Column(Integer, ForeignKey("fournisseurs.id", ondelete="RESTRICT"), nullable=False)
    id_utilisateur        = Column(Integer, ForeignKey("utilisateurs.id", ondelete="SET NULL"), nullable=True)
    date_commande         = Column(DateTime, default=datetime.utcnow)
    date_livraison_prevue = Column(Date, nullable=True)
    date_livraison_reelle = Column(Date, nullable=True)
    statut                = Column(String(30), default="brouillon")
    # brouillon | envoye | partiellement_recu | recu | annule
    total_ht              = Column(Numeric(14, 2), default=0)
    total_ttc             = Column(Numeric(14, 2), default=0)
    taux_tva              = Column(Numeric(5, 2), default=0)
    notes                 = Column(Text, nullable=True)
    is_deleted            = Column(Boolean, default=False)
    deleted_at            = Column(DateTime, nullable=True)
    created_at            = Column(DateTime, default=datetime.utcnow)

    pharmacie    = relationship("Pharmacie")
    fournisseur  = relationship("Fournisseur", back_populates="bons_commande")
    utilisateur  = relationship("Utilisateur")
    lignes       = relationship("LigneBonCommande", back_populates="bon_commande",
                                cascade="all, delete-orphan")


class LigneBonCommande(Base):
    __tablename__ = "lignes_bon_commande"

    id                  = Column(Integer, primary_key=True, index=True)
    id_bon_commande     = Column(Integer, ForeignKey("bons_commande.id", ondelete="CASCADE"), nullable=False)
    id_produit          = Column(Integer, ForeignKey("produits.id", ondelete="RESTRICT"), nullable=False)
    quantite_commandee  = Column(Integer, nullable=False)
    quantite_recue      = Column(Integer, default=0)
    prix_unitaire_ht    = Column(Numeric(10, 2), nullable=False)
    total_ligne_ht      = Column(Numeric(12, 2), nullable=False)

    bon_commande = relationship("BonCommande", back_populates="lignes")
    produit      = relationship("Produit")