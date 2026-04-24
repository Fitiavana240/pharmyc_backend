# models/retours.py
from database import Base
from sqlalchemy import (
    Column, Integer, String, Text, Boolean, DateTime,
    ForeignKey, Numeric
)
from sqlalchemy.orm import relationship
from datetime import datetime


class RetourProduit(Base):
    __tablename__ = "retours_produit"

    id                  = Column(Integer, primary_key=True, index=True)
    code                = Column(String(20), unique=True, nullable=False, index=True)  # RET-XXXXXX
    id_pharmacie        = Column(Integer, ForeignKey("pharmacies.id", ondelete="CASCADE"), nullable=False)
    id_vente            = Column(Integer, ForeignKey("ventes.id", ondelete="RESTRICT"), nullable=True)
    id_client           = Column(Integer, ForeignKey("clients.id", ondelete="SET NULL"), nullable=True)
    id_utilisateur      = Column(Integer, ForeignKey("utilisateurs.id", ondelete="SET NULL"), nullable=True)

    motif               = Column(String(200), nullable=False)
    # type_retour : produit_defectueux | erreur_dispensation | client_insatisfait | perime | autre
    type_retour         = Column(String(50), default="client_insatisfait")

    # statut : en_attente | approuve | rejete | rembourse
    statut              = Column(String(30), default="en_attente")

    montant_total       = Column(Numeric(12, 2), default=0)
    montant_rembourse   = Column(Numeric(12, 2), default=0)
    # moyen_remboursement : espèces | virement | avoir
    moyen_remboursement = Column(String(50), nullable=True)

    restock_effectue    = Column(Boolean, default=False)
    notes               = Column(Text, nullable=True)

    date_retour         = Column(DateTime, default=datetime.utcnow)
    date_traitement     = Column(DateTime, nullable=True)

    is_deleted          = Column(Boolean, default=False)
    deleted_at          = Column(DateTime, nullable=True)

    pharmacie   = relationship("Pharmacie")
    vente       = relationship("Vente")
    client      = relationship("Client")
    utilisateur = relationship("Utilisateur")
    lignes      = relationship("LigneRetour", back_populates="retour",
                               cascade="all, delete-orphan")


class LigneRetour(Base):
    __tablename__ = "lignes_retour"

    id           = Column(Integer, primary_key=True, index=True)
    id_retour    = Column(Integer, ForeignKey("retours_produit.id", ondelete="CASCADE"), nullable=False)
    id_produit   = Column(Integer, ForeignKey("produits.id", ondelete="RESTRICT"), nullable=False)
    quantite     = Column(Integer, nullable=False)
    prix_unitaire = Column(Numeric(10, 2), nullable=False)
    total_ligne  = Column(Numeric(12, 2), nullable=False)
    # etat_produit : bon | defectueux | perime
    etat_produit = Column(String(50), default="bon")

    retour   = relationship("RetourProduit", back_populates="lignes")
    produit  = relationship("Produit")