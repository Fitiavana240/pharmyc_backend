# models/ordonnances.py
from database import Base
from sqlalchemy import (
    Column, Integer, String, Text, Boolean, Date, DateTime,
    ForeignKey
)
from sqlalchemy.orm import relationship
from datetime import datetime


class Ordonnance(Base):
    __tablename__ = "ordonnances"

    id                = Column(Integer, primary_key=True, index=True)
    code              = Column(String(20), unique=True, nullable=False, index=True)  # ORD-XXXXXX
    id_pharmacie      = Column(Integer, ForeignKey("pharmacies.id", ondelete="CASCADE"), nullable=False)
    id_client         = Column(Integer, ForeignKey("clients.id", ondelete="SET NULL"), nullable=True)
    id_vente          = Column(Integer, ForeignKey("ventes.id", ondelete="SET NULL"), nullable=True)

    # Infos prescripteur
    medecin_nom       = Column(String(200), nullable=True)
    medecin_telephone = Column(String(50), nullable=True)
    medecin_adresse   = Column(Text, nullable=True)
    specialite        = Column(String(100), nullable=True)

    # Infos patient (peut différer du client enregistré)
    patient_nom       = Column(String(200), nullable=True)
    patient_age       = Column(Integer, nullable=True)

    # Statut
    # en_attente | dispensee | partiellement_dispensee | expiree | annulee
    statut            = Column(String(30), default="en_attente")

    date_prescription = Column(Date, nullable=False)
    date_expiration   = Column(Date, nullable=True)
    date_dispensation = Column(DateTime, nullable=True)

    # Photo / scan de l'ordonnance papier
    image_url         = Column(Text, nullable=True)
    notes             = Column(Text, nullable=True)

    is_deleted        = Column(Boolean, default=False)
    deleted_at        = Column(DateTime, nullable=True)
    created_at        = Column(DateTime, default=datetime.utcnow)

    pharmacie = relationship("Pharmacie")
    client    = relationship("Client")
    vente     = relationship("Vente")
    lignes    = relationship("LigneOrdonnance", back_populates="ordonnance",
                             cascade="all, delete-orphan")


class LigneOrdonnance(Base):
    __tablename__ = "lignes_ordonnance"

    id                  = Column(Integer, primary_key=True, index=True)
    id_ordonnance       = Column(Integer, ForeignKey("ordonnances.id", ondelete="CASCADE"), nullable=False)
    id_produit          = Column(Integer, ForeignKey("produits.id", ondelete="RESTRICT"), nullable=True)
    # nullable=True car le médicament peut ne pas être référencé dans le stock

    medicament_nom      = Column(String(200), nullable=False)  # nom écrit par le médecin
    dosage              = Column(String(100), nullable=True)    # ex: "500mg"
    posologie           = Column(String(200), nullable=True)    # ex: "1 cp 3x/jour pendant 7j"
    quantite_prescrite  = Column(Integer, default=1)
    quantite_dispensee  = Column(Integer, default=0)
    dispensee           = Column(Boolean, default=False)

    ordonnance = relationship("Ordonnance", back_populates="lignes")
    produit    = relationship("Produit")