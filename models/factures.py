# models/factures.py
from database import Base
from sqlalchemy import (
    Column, Integer, String, Text, Boolean, Date, DateTime,
    ForeignKey, Numeric
)
from sqlalchemy.orm import relationship
from datetime import datetime


class Facture(Base):
    __tablename__ = "factures"

    id               = Column(Integer, primary_key=True, index=True)
    code             = Column(String(20), unique=True, nullable=False, index=True)  # FAC-XXXXXX
    id_pharmacie     = Column(Integer, ForeignKey("pharmacies.id", ondelete="CASCADE"), nullable=False)
    id_vente         = Column(Integer, ForeignKey("ventes.id", ondelete="RESTRICT"), nullable=True)
    id_client        = Column(Integer, ForeignKey("clients.id", ondelete="SET NULL"), nullable=True)
    id_retour        = Column(Integer, ForeignKey("retours_produit.id", ondelete="SET NULL"), nullable=True)

    # type_facture : vente | avoir | proforma
    type_facture     = Column(String(20), default="vente")
    # numéro séquentiel par pharmacie, remis à 1 chaque année
    numero_facture   = Column(Integer, nullable=False)

    date_facture     = Column(DateTime, default=datetime.utcnow)
    date_echeance    = Column(Date, nullable=True)

    montant_ht       = Column(Numeric(14, 2), default=0)
    taux_tva         = Column(Numeric(5, 2), default=0)
    montant_tva      = Column(Numeric(14, 2), default=0)
    montant_ttc      = Column(Numeric(14, 2), default=0)
    montant_remise   = Column(Numeric(14, 2), default=0)

    # statut : emise | payee | annulee
    statut           = Column(String(20), default="emise")

    pdf_url          = Column(Text, nullable=True)
    notes            = Column(Text, nullable=True)

    is_deleted       = Column(Boolean, default=False)
    deleted_at       = Column(DateTime, nullable=True)
    created_at       = Column(DateTime, default=datetime.utcnow)

    pharmacie = relationship("Pharmacie")
    vente     = relationship("Vente")
    client    = relationship("Client")
    retour    = relationship("RetourProduit")