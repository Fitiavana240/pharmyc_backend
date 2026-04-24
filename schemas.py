# ============================================================
# schemas.py — Pharmy-C API v4.3
# Tous les schémas Pydantic — anciens + nouveaux modules
# ============================================================

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, field_validator, model_validator


# ============================================================
# 2. RÔLES
# ============================================================

class RoleBase(BaseModel):
    name: str
    description: Optional[str] = None


class RoleCreate(RoleBase):
    pass


class RoleUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None


class RoleRead(RoleBase):
    id: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)


# ============================================================
# 3. MENUS
# ============================================================

class MenuBase(BaseModel):
    name: str
    path: Optional[str] = None
    icon: Optional[str] = None
    parent_id: Optional[int] = None
    order: Optional[int] = 0
    is_active: Optional[bool] = True


class MenuCreate(MenuBase):
    pass


class MenuUpdate(BaseModel):
    name: Optional[str] = None
    path: Optional[str] = None
    icon: Optional[str] = None
    parent_id: Optional[int] = None
    order: Optional[int] = None
    is_active: Optional[bool] = None


class MenuRead(MenuBase):
    id: int
    children: List[MenuRead] = []
    model_config = ConfigDict(from_attributes=True)


# ============================================================
# 4. UTILISATEURS
# ============================================================

class UserCreate(BaseModel):
    nom: str
    email: EmailStr
    mot_de_passe: str
    id_role: Optional[int] = None
    telephone: Optional[str] = None


class EmployeCreate(UserCreate):
    """Ajout d'un employé par le propriétaire — id_role obligatoire."""
    id_role: int


class UserRead(BaseModel):
    id: int
    uuid: str
    nom: str
    email: EmailStr
    id_role: Optional[int]
    role_name: Optional[str] = None
    id_pharmacie: Optional[int]
    telephone: Optional[str] = None
    est_actif: bool
    confirmation_email: bool
    model_config = ConfigDict(from_attributes=True)


class UserUpdate(BaseModel):
    nom: Optional[str] = None
    email: Optional[EmailStr] = None
    id_role: Optional[int] = None
    est_actif: Optional[bool] = None
    telephone: Optional[str] = None
    mot_de_passe: Optional[str] = None


class EmployeCreate(UserCreate):
    """Ajout d'un employé par le propriétaire – id_role est obligatoire."""
    id_role: int
    telephone: Optional[str] = None


class EmailConfirm(BaseModel):
    email: EmailStr
    code: str


class ResendCode(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    """Demande de réinitialisation de mot de passe par email."""
    email: EmailStr


class ResetPasswordConfirm(BaseModel):
    """Confirmation avec le code reçu par email."""
    email: EmailStr
    code: str
    nouveau_mot_de_passe: str

    @field_validator("nouveau_mot_de_passe")
    @classmethod
    def mdp_assez_long(cls, v: str) -> str:
        if len(v) < 6:
            raise ValueError("Le mot de passe doit contenir au moins 6 caractères")
        return v


# ============================================================
# 5. PHARMACIES
# ============================================================

DEVISES_AUTORISEES = ["MGA", "USD", "EUR", "GBP", "CHF", "CAD", "ZAR", "KES", "XOF", "XAF"]
 
class PharmacieBase(BaseModel):
    nom:       str
    email:     EmailStr
    nif:       Optional[str] = None
    stat:      Optional[str] = None
    adresse:   Optional[str] = None
    telephone: Optional[str] = None
    devise:    Optional[str] = "MGA"
 
    @field_validator("devise")
    @classmethod
    def valider_devise(cls, v):
        if v and v not in ["MGA","USD","EUR","GBP","CHF","CAD","ZAR","KES","XOF","XAF"]:
            raise ValueError(f"Devise non supportée. Choisir parmi : MGA, USD, EUR, GBP, CHF...")
        return v
 
class PharmacieCreate(PharmacieBase):
    mot_de_passe: str
 
class PharmacieUpdate(BaseModel):
    nom:         Optional[str]      = None
    email:       Optional[EmailStr] = None
    mot_de_passe: Optional[str]     = None
    logo:        Optional[str]      = None
    nif:         Optional[str]      = None
    stat:        Optional[str]      = None
    adresse:     Optional[str]      = None
    telephone:   Optional[str]      = None
    devise:      Optional[str]      = None
 
    @field_validator("devise")
    @classmethod
    def valider_devise(cls, v):
        if v and v not in ["MGA","USD","EUR","GBP","CHF","CAD","ZAR","KES","XOF","XAF"]:
            raise ValueError("Devise non supportée")
        return v
 
class PharmacieRead(PharmacieBase):
    id:            int
    code:          str
    logo:          Optional[str]
    devise:        str = "MGA"
    date_creation: date
    owner_user_id: Optional[int]
    is_deleted:    bool
    model_config = ConfigDict(from_attributes=True)


# ============================================================
# 6. CLIENTS
# ============================================================

class ClientBase(BaseModel):
    nom: str
    email: Optional[EmailStr] = None
    telephone: Optional[str] = None
    adresse: Optional[str] = None


class ClientCreate(ClientBase):
    id_pharmacie: Optional[int] = None


class ClientUpdate(BaseModel):
    nom: Optional[str] = None
    email: Optional[EmailStr] = None
    telephone: Optional[str] = None
    adresse: Optional[str] = None


class ClientRead(ClientBase):
    id: int
    code: str
    date_creation: date
    id_pharmacie: Optional[int] = None
    is_deleted: bool
    model_config = ConfigDict(from_attributes=True)


# ============================================================
# 7. PRODUITS
# ============================================================

class ProduitBase(BaseModel):
    nom: str
    description: Optional[str] = None
    prix_vente: Decimal
    prix_gros: Optional[Decimal] = None
    stock_boite: Optional[int] = 0
    quantite_par_boite: Optional[int] = 1
    pieces_par_plaquette: Optional[int] = 1
    date_expiration: Optional[date] = None
    seuil_alerte: Optional[int] = 0
    categorie: Optional[str] = None

    @field_validator("prix_vente")
    @classmethod
    def prix_positif(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("Le prix de vente doit être positif")
        return v


class ProduitCreate(ProduitBase):
    pass


class ProduitUpdate(BaseModel):
    nom: Optional[str] = None
    description: Optional[str] = None
    prix_vente: Optional[Decimal] = None
    prix_gros: Optional[Decimal] = None
    stock_boite: Optional[int] = None
    quantite_par_boite: Optional[int] = None
    pieces_par_plaquette: Optional[int] = None
    date_expiration: Optional[date] = None
    seuil_alerte: Optional[int] = None
    categorie: Optional[str] = None


class ProduitRead(ProduitBase):
    id: int
    code: str
    id_pharmacie: int
    stock_total_piece: Optional[int] = 0
    is_deleted: bool
    model_config = ConfigDict(from_attributes=True)


# ============================================================
# 8. ENTRÉES DE STOCK
# ============================================================

class EntreeStockCreate(BaseModel):
    id_produit:          int
    quantite:            int
    type_entree:         str = "achat"
    fournisseur:         Optional[str]  = None
    id_fournisseur:      Optional[int]  = None
    prix_achat_unitaire: Optional[float] = None
 
    @field_validator("quantite")
    @classmethod
    def quantite_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("La quantité doit être positive")
        return v


class EntreeStockRead(BaseModel):
    id: int
    id_produit: int
    quantite: int
    type_entree: str
    fournisseur: Optional[str] = None
    date_entree: date
    produit_nom: Optional[str] = None
    id_fournisseur: Optional[int] = None
    prix_achat_unitaire: Optional[float] = None
    montant_achat: Optional[float] = None
    model_config = ConfigDict(from_attributes=True)


# ============================================================
# 9. SORTIES DE STOCK
# ============================================================

class SortieStockCreate(BaseModel):
    id_produit: int
    quantite: int
    motif: str = "vente"

    @field_validator("quantite")
    @classmethod
    def quantite_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("La quantité doit être positive")
        return v


class SortieStockRead(BaseModel):
    id: int
    id_produit: int
    quantite: int
    motif: str
    date_sortie: date
    produit_nom: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)


# ============================================================
# 10. VENTES & PAIEMENTS
# ============================================================

class ItemCommande(BaseModel):
    """Article d'une vente avec gestion multi-unités (boîte / plaquette / pièce)."""
    id_produit: int
    quantite_boite: int = 0
    quantite_plaquette: int = 0
    quantite_piece: int = 0
    prix_unitaire: Optional[Decimal] = None
    type_prix: Optional[str] = "retail"   # 'retail' ou 'wholesale'

    @field_validator("quantite_boite", "quantite_plaquette", "quantite_piece")
    @classmethod
    def quantites_non_negatives(cls, v: int) -> int:
        if v < 0:
            raise ValueError("Les quantités ne peuvent pas être négatives")
        return v

    @model_validator(mode="after")
    def total_positif(self) -> 'ItemCommande':
        total = self.quantite_boite + self.quantite_plaquette + self.quantite_piece
        if total <= 0:
            raise ValueError(
                f"La quantité totale pour le produit {self.id_produit} doit être > 0"
            )
        return self


class CommandeCreate(BaseModel):
    id_client: Optional[int] = None
    client_nom: Optional[str] = None
    items: List[ItemCommande]

    @field_validator("items")
    @classmethod
    def items_non_vides(cls, v: list) -> list:
        if not v:
            raise ValueError("La commande doit contenir au moins un article")
        return v


class PaiementCreate(BaseModel):
    montant: Decimal
    moyen: str = "espèces"

    @field_validator("montant")
    @classmethod
    def montant_valide(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("Le montant doit être positif")
        return v


class PaiementRead(BaseModel):
    id: int
    id_vente: int
    montant: Decimal
    moyen: str
    date_paiement: datetime
    model_config = ConfigDict(from_attributes=True)


class DetailVenteRead(BaseModel):
    id: int
    id_produit: int
    quantite: int
    prix_unitaire: Decimal
    total_ligne: Decimal
    produit_nom: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)


class VenteRead(BaseModel):
    id: int
    code: str
    id_pharmacie: int
    id_client: Optional[int] = None
    client_nom: Optional[str] = None
    date_vente: datetime
    total: Decimal
    montant_paye: Decimal
    reste_a_payer: Decimal
    moyen_paiement: Optional[str] = None
    statut: str
    is_deleted: bool
    model_config = ConfigDict(from_attributes=True)


class VenteDetaillee(VenteRead):
    """Vente avec ses détails lignes et paiements associés."""
    details: List[DetailVenteRead] = []
    paiements: List[PaiementRead] = []


# ============================================================
# 11. NOTIFICATIONS
# ============================================================

class NotificationBase(BaseModel):
    type_notif: str
    message: str
    lu: bool = False
    date_notif: datetime


class NotificationRead(NotificationBase):
    id: int
    id_pharmacie: int
    id_produit: Optional[int] = None
    model_config = ConfigDict(from_attributes=True)


class NotificationUpdate(BaseModel):
    lu: Optional[bool] = None


# ============================================================
# 12. HISTORIQUE
# ============================================================

class HistoriqueRead(BaseModel):
    id: int
    id_utilisateur: Optional[int] = None
    id_pharmacie: int
    action: str
    entity_type: str
    entity_id: Optional[int] = None
    old_value: Optional[str] = None
    new_value: Optional[str] = None
    date_action: datetime
    model_config = ConfigDict(from_attributes=True)


# ============================================================
# 13. FOURNISSEURS
# ============================================================

class FournisseurBase(BaseModel):
    nom: str
    email: Optional[EmailStr] = None
    telephone: Optional[str] = None
    adresse: Optional[str] = None
    contact_nom: Optional[str] = None
    nif: Optional[str] = None
    conditions_paiement: Optional[str] = None
    delai_livraison: Optional[int] = None   # en jours
    notes: Optional[str] = None


class FournisseurCreate(FournisseurBase):
    pass


class FournisseurUpdate(BaseModel):
    nom: Optional[str] = None
    email: Optional[EmailStr] = None
    telephone: Optional[str] = None
    adresse: Optional[str] = None
    contact_nom: Optional[str] = None
    nif: Optional[str] = None
    conditions_paiement: Optional[str] = None
    delai_livraison: Optional[int] = None
    actif: Optional[bool] = None
    notes: Optional[str] = None


class FournisseurRead(FournisseurBase):
    id: int
    code: str
    id_pharmacie: int
    actif: bool
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)


# ============================================================
# 14. BONS DE COMMANDE
# ============================================================

class LigneBCCreate(BaseModel):
    id_produit: int
    quantite_commandee: int
    prix_unitaire_ht: Decimal

    @field_validator("quantite_commandee")
    @classmethod
    def quantite_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("La quantité commandée doit être positive")
        return v

    @field_validator("prix_unitaire_ht")
    @classmethod
    def prix_positif(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("Le prix unitaire HT doit être positif")
        return v


class BonCommandeUpdate(BaseModel):
    """
    Mise à jour d'un bon de commande en brouillon.
    Tous les champs sont optionnels.
    Si 'lignes' est fourni, les anciennes lignes sont remplacées entièrement.
    """
    id_fournisseur:       Optional[int]            = None
    date_livraison_prevue: Optional[date]           = None
    taux_tva:             Optional[Decimal]         = None
    notes:                Optional[str]             = None
    lignes:               Optional[List[LigneBCCreate]] = None
 

class BonCommandeCreate(BaseModel):
    id_fournisseur: int
    date_livraison_prevue: Optional[date] = None
    taux_tva: Optional[Decimal] = Decimal("0")
    notes: Optional[str] = None
    lignes: List[LigneBCCreate]

    @field_validator("lignes")
    @classmethod
    def lignes_non_vides(cls, v: list) -> list:
        if not v:
            raise ValueError("Le bon de commande doit contenir au moins une ligne")
        return v


class ReceptionBCCreate(BaseModel):
    """Payload pour réceptionner une livraison (totale ou partielle)."""
    lignes: List[dict]    # [{"id_ligne": int, "quantite_recue": int}, ...]
    date_livraison_reelle: Optional[date] = None
    notes: Optional[str] = None


class LigneBCRead(BaseModel):
    id: int
    id_produit: int
    quantite_commandee: int
    quantite_recue: int
    prix_unitaire_ht: Decimal
    total_ligne_ht: Decimal
    produit_nom: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)


class BonCommandeRead(BaseModel):
    id: int
    code: str
    id_pharmacie: int
    id_fournisseur: int
    fournisseur_nom: Optional[str] = None
    date_commande: datetime
    date_livraison_prevue: Optional[date] = None
    date_livraison_reelle: Optional[date] = None
    statut: str
    total_ht: Decimal
    total_ttc: Decimal
    taux_tva: Decimal
    notes: Optional[str] = None
    lignes: List[LigneBCRead] = []
    model_config = ConfigDict(from_attributes=True)


# ============================================================
# 15. ORDONNANCES
# ============================================================

class LigneOrdonnanceCreate(BaseModel):
    id_produit: Optional[int] = None       # optionnel : médicament peut ne pas être en stock
    medicament_nom: str                    # nom écrit par le médecin
    dosage: Optional[str] = None           # ex: "500mg"
    posologie: Optional[str] = None        # ex: "1 comprimé 3x/jour"
    quantite_prescrite: int = 1

    @field_validator("quantite_prescrite")
    @classmethod
    def quantite_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("La quantité prescrite doit être positive")
        return v


class LigneOrdonnanceRead(BaseModel):
    id: int
    id_produit: Optional[int] = None
    medicament_nom: str
    dosage: Optional[str] = None
    posologie: Optional[str] = None
    quantite_prescrite: int
    quantite_dispensee: int
    dispensee: bool
    model_config = ConfigDict(from_attributes=True)


class OrdonnanceCreate(BaseModel):
    id_client: Optional[int] = None
    medecin_nom: Optional[str] = None
    medecin_telephone: Optional[str] = None
    medecin_adresse: Optional[str] = None
    specialite: Optional[str] = None
    patient_nom: Optional[str] = None
    patient_age: Optional[int] = None
    date_prescription: date
    date_expiration: Optional[date] = None
    notes: Optional[str] = None
    lignes: List[LigneOrdonnanceCreate]

    @field_validator("lignes")
    @classmethod
    def lignes_non_vides(cls, v: list) -> list:
        if not v:
            raise ValueError("L'ordonnance doit contenir au moins un médicament")
        return v

    @model_validator(mode="after")
    def expiration_apres_prescription(self) -> 'OrdonnanceCreate':
        if self.date_expiration and self.date_expiration < self.date_prescription:
            raise ValueError("La date d'expiration doit être après la date de prescription")
        return self


class OrdonnanceUpdate(BaseModel):
    statut: Optional[str] = None
    notes: Optional[str] = None
    id_vente: Optional[int] = None
    medecin_nom: Optional[str] = None
    date_expiration: Optional[date] = None


class DispensationCreate(BaseModel):
    """Payload pour dispenser les médicaments d'une ordonnance."""
    lignes: List[dict]        # [{"id_ligne": int, "quantite_dispensee": int}, ...]
    id_vente: Optional[int] = None


class OrdonnanceRead(BaseModel):
    id: int
    code: str
    id_pharmacie: int
    id_client: Optional[int] = None
    id_vente: Optional[int] = None
    medecin_nom: Optional[str] = None
    medecin_telephone: Optional[str] = None
    specialite: Optional[str] = None
    patient_nom: Optional[str] = None
    patient_age: Optional[int] = None
    statut: str
    date_prescription: date
    date_expiration: Optional[date] = None
    date_dispensation: Optional[datetime] = None
    image_url: Optional[str] = None
    notes: Optional[str] = None
    lignes: List[LigneOrdonnanceRead] = []
    created_at: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)


# ============================================================
# 16. RETOURS PRODUIT
# ============================================================

class LigneRetourCreate(BaseModel):
    id_produit: int
    quantite: int
    prix_unitaire: Decimal
    etat_produit: str = "bon"   # bon | defectueux | perime

    @field_validator("quantite")
    @classmethod
    def quantite_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("La quantité doit être positive")
        return v

    @field_validator("prix_unitaire")
    @classmethod
    def prix_positif(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("Le prix unitaire doit être positif")
        return v

    @field_validator("etat_produit")
    @classmethod
    def etat_valide(cls, v: str) -> str:
        valides = {"bon", "defectueux", "perime"}
        if v not in valides:
            raise ValueError(f"État produit invalide. Valeurs acceptées : {valides}")
        return v


class LigneRetourRead(BaseModel):
    id: int
    id_produit: int
    quantite: int
    prix_unitaire: Decimal
    total_ligne: Decimal
    etat_produit: str
    produit_nom: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)


class RetourCreate(BaseModel):
    id_vente: Optional[int] = None
    id_client: Optional[int] = None
    motif: str
    type_retour: str = "client_insatisfait"
    # produit_defectueux | erreur_dispensation | client_insatisfait | perime | autre
    notes: Optional[str] = None
    lignes: List[LigneRetourCreate]

    @field_validator("type_retour")
    @classmethod
    def type_valide(cls, v: str) -> str:
        valides = {
            "produit_defectueux", "erreur_dispensation",
            "client_insatisfait", "perime", "autre"
        }
        if v not in valides:
            raise ValueError(f"Type de retour invalide. Valeurs acceptées : {valides}")
        return v

    @field_validator("lignes")
    @classmethod
    def lignes_non_vides(cls, v: list) -> list:
        if not v:
            raise ValueError("Le retour doit contenir au moins un produit")
        return v


class RetourTraitement(BaseModel):
    """Payload pour approuver, rejeter ou rembourser un retour."""
    statut: str             # approuve | rejete | rembourse
    montant_rembourse: Optional[Decimal] = None
    moyen_remboursement: Optional[str] = None   # espèces | virement | avoir
    restock_effectue: Optional[bool] = False
    notes: Optional[str] = None

    @field_validator("statut")
    @classmethod
    def statut_valide(cls, v: str) -> str:
        valides = {"approuve", "rejete", "rembourse"}
        if v not in valides:
            raise ValueError(f"Statut invalide. Valeurs acceptées : {valides}")
        return v


class RetourRead(BaseModel):
    id: int
    code: str
    id_pharmacie: int
    id_vente: Optional[int] = None
    id_client: Optional[int] = None
    id_utilisateur: Optional[int] = None
    motif: str
    type_retour: str
    statut: str
    montant_total: Decimal
    montant_rembourse: Decimal
    moyen_remboursement: Optional[str] = None
    restock_effectue: bool
    notes: Optional[str] = None
    date_retour: datetime
    date_traitement: Optional[datetime] = None
    lignes: List[LigneRetourRead] = []
    model_config = ConfigDict(from_attributes=True)


# ============================================================
# 17. FACTURATION
# ============================================================

class FactureCreate(BaseModel):
    id_vente: Optional[int] = None
    id_client: Optional[int] = None
    type_facture: str = "vente"     # vente | avoir | proforma
    taux_tva: Decimal = Decimal("0")
    montant_remise: Decimal = Decimal("0")
    date_echeance: Optional[date] = None
    notes: Optional[str] = None

    @field_validator("type_facture")
    @classmethod
    def type_valide(cls, v: str) -> str:
        valides = {"vente", "avoir", "proforma"}
        if v not in valides:
            raise ValueError(f"Type de facture invalide. Valeurs acceptées : {valides}")
        return v

    @field_validator("taux_tva", "montant_remise")
    @classmethod
    def non_negatif(cls, v: Decimal) -> Decimal:
        if v < 0:
            raise ValueError("La valeur ne peut pas être négative")
        return v


class FactureUpdate(BaseModel):
    statut: Optional[str] = None
    notes: Optional[str] = None
    date_echeance: Optional[date] = None


class FactureRead(BaseModel):
    id: int
    code: str
    id_pharmacie: int
    id_vente: Optional[int] = None
    id_client: Optional[int] = None
    id_retour: Optional[int] = None
    type_facture: str
    numero_facture: int
    date_facture: datetime
    date_echeance: Optional[date] = None
    montant_ht: Decimal
    taux_tva: Decimal
    montant_tva: Decimal
    montant_ttc: Decimal
    montant_remise: Decimal
    statut: str
    pdf_url: Optional[str] = None
    notes: Optional[str] = None
    is_deleted: bool
    created_at: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)


# ============================================================
# 18. RAPPORTS & DASHBOARD
# ============================================================

class DashboardStats(BaseModel):
    """Réponse du endpoint GET /rapports/dashboard ou GET /dashboard/stats."""
    ca_jour: float
    ca_mois: float
    nb_ventes_jour: int
    nb_ventes_mois: int
    stock_faible: int
    produits_expiration_proche: int
    produits_rupture: int
    retours_en_attente: int
    factures_impayees: int
    ticket_moyen_jour: float


class SerieCA(BaseModel):
    """Une ligne de la série temporelle du chiffre d'affaires."""
    periode: Optional[str] = None
    ca: float
    nb_ventes: int
    encaisse: float
    impaye: float


class RapportCA(BaseModel):
    """Réponse du endpoint GET /rapports/chiffre-affaires."""
    periode: dict
    ca_total: float
    ca_precedent: float
    evolution_pct: float
    series: List[SerieCA]


class ProduitTopItem(BaseModel):
    id: int
    nom: str
    categorie: Optional[str] = None
    quantite_vendue: int
    ca: float
    nb_ventes: int


class RapportProduitsTop(BaseModel):
    periode: dict
    critere: str
    produits: List[ProduitTopItem]


class StockFaibleItem(BaseModel):
    id: int
    nom: str
    stock: int
    seuil: int


class ExpirationItem(BaseModel):
    id: int
    nom: str
    date_expiration: str
    stock: int
    jours_restants: int


class CategorieStockItem(BaseModel):
    categorie: str
    nb_produits: int
    valeur: float


class RapportStockValorise(BaseModel):
    valeur_totale_stock: float
    nb_produits_total: int
    nb_stock_faible: int
    nb_expirations_proches: int
    nb_produits_epuises: int
    stock_faible: List[StockFaibleItem]
    expirations_proches: List[ExpirationItem]
    produits_epuises: List[dict]
    par_categorie: List[CategorieStockItem]


class MoyenPaiementItem(BaseModel):
    moyen: str
    total: float
    nb_transactions: int


class VendeurItem(BaseModel):
    vendeur: str
    nb_ventes: int
    ca: float


class RapportCloture(BaseModel):
    """Réponse du endpoint GET /rapports/cloture-journaliere."""
    date: str
    resume: dict
    par_moyen_paiement: List[MoyenPaiementItem]
    par_vendeur: List[VendeurItem]


class ClientTopItem(BaseModel):
    id: int
    nom: str
    telephone: Optional[str] = None
    nb_ventes: int
    ca_total: float
    impaye_total: float


class RapportClientsTop(BaseModel):
    periode: dict
    clients: List[ClientTopItem]